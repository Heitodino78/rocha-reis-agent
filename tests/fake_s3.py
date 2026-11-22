"""Servidor S3 FALSO (somente 127.0.0.1) para testar o adaptador de backup externo sem nenhum serviço real.

Implementa o mínimo usado pelo adaptador — PUT/GET/HEAD/DELETE de objeto e ListObjectsV2 com paginação —
com as regras que importam: verificação INDEPENDENTE da assinatura AWS SigV4 (canonicalização escrita de
novo aqui, não importada do produto), conferência de x-amz-content-sha256 e Content-MD5, `If-None-Match`,
permissões por chave (read/write/list/delete), versões, Object Lock por objeto (estilo B2: COMPLIANCE não
pode ser apagado nem encurtado) ou regra de bloqueio do BUCKET (estilo R2: sem cabeçalhos de Object Lock,
409 ObjectLockedByBucketPolicy). Falhas injetáveis: 500, atraso (timeout), conexão cortada no meio do
envio, corpo corrompido no download, travar antes de gravar (processo morto durante a transferência).
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
from urllib.parse import parse_qsl, quote, unquote, urlsplit
from xml.sax.saxutils import escape


class FakeS3:
    def __init__(self, *, bucket: str = "rr-backups", region: str = "us-west-004",
                 keys: dict[str, tuple[str, set[str]]] | None = None, style: str = "b2",
                 bucket_lock_days: int | None = None) -> None:
        self.bucket, self.region, self.style = bucket, region, style
        self.keys = keys or {"AKTESTEROCHAREIS01": ("segredo-de-teste-0123456789abcdef", {"read", "write", "list"})}
        self.bucket_lock_days = bucket_lock_days
        self.objects: dict[str, list[dict[str, object]]] = {}
        self.faults: list[tuple[str, str]] = []          # (operação|any, tipo[:arg]) consumidos em ordem
        self.log: list[tuple[str, str, int]] = []        # (método, caminho, status) — sem corpo nem cabeçalhos
        self.lock = threading.Lock()
        self.now = lambda: datetime.now(timezone.utc)
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: object) -> None:
                return None

            def _send(self, status: int, body: bytes = b"", headers: dict[str, str] | None = None) -> None:
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                if "Content-Length" not in (headers or {}):
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)
                server.log.append((self.command, urlsplit(self.path).path, status))

            def _error(self, status: int, code: str) -> None:
                self._send(status, f"<?xml version='1.0'?><Error><Code>{code}</Code></Error>".encode(),
                           {"Content-Type": "application/xml"})

            def do_PUT(self) -> None: server._handle(self, "put")
            def do_GET(self) -> None: server._handle(self, "get")
            def do_HEAD(self) -> None: server._handle(self, "head")
            def do_DELETE(self) -> None: server._handle(self, "delete")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.endpoint = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> "FakeS3":
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def current(self, key: str) -> dict[str, object] | None:
        versions = self.objects.get(key) or []
        return versions[-1] if versions else None

    # -- assinatura (implementação independente)
    def _verify(self, h, body: bytes) -> str | None:  # type: ignore[no-untyped-def]
        auth = h.headers.get("Authorization", "")
        try:
            algo, rest = auth.split(" ", 1)
            parts = dict(item.strip().split("=", 1) for item in rest.split(","))
            key_id, day, region, service, term = parts["Credential"].split("/")
            signed = parts["SignedHeaders"].split(";")
        except (ValueError, KeyError):
            return "AccessDenied"
        if algo != "AWS4-HMAC-SHA256" or key_id not in self.keys:
            return "InvalidAccessKeyId"
        amz = h.headers.get("x-amz-date", "")
        moment = datetime.strptime(amz, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        if abs((moment - self.now()).total_seconds()) > 900 or amz[:8] != day or region != self.region:
            return "RequestTimeTooSkewed"
        payload = h.headers.get("x-amz-content-sha256", "")
        if payload != hashlib.sha256(body).hexdigest():
            return "XAmzContentSHA256Mismatch"
        url = urlsplit(h.path)
        query = sorted(parse_qsl(url.query, keep_blank_values=True))
        cq = "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in query)
        lines = []
        for name in signed:
            value = h.headers.get(name)
            if value is None:
                return "AccessDenied"
            lines.append(f"{name}:{' '.join(value.strip().split())}\n")
        canonical = f"{h.command}\n{quote(unquote(url.path), safe='/-_.~')}\n{cq}\n{''.join(lines)}\n{';'.join(signed)}\n{payload}"
        scope = f"{day}/{region}/{service}/{term}"
        string = f"AWS4-HMAC-SHA256\n{amz}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
        k = ("AWS4" + self.keys[key_id][0]).encode()
        for part in (day, region, service, term):
            k = hmac.new(k, part.encode(), hashlib.sha256).digest()
        expected = hmac.new(k, string.encode(), hashlib.sha256).hexdigest()
        return None if hmac.compare_digest(expected, parts["Signature"]) else "SignatureDoesNotMatch"

    def _fault(self, op: str) -> str | None:
        with self.lock:
            for i, (target, kind) in enumerate(self.faults):
                if target in (op, "any"):
                    self.faults.pop(i)
                    return kind
        return None

    def _handle(self, h, op: str) -> None:  # type: ignore[no-untyped-def]
        fault = self._fault(op)
        length = int(h.headers.get("Content-Length") or 0)
        if fault == "drop":  # lê só metade do envio e derruba a conexão (envio interrompido)
            h.rfile.read(length // 2)
            h.close_connection = True
            h.connection.shutdown(2)
            return
        body = h.rfile.read(length) if length else b""
        if fault and fault.startswith("sleep:"):
            time.sleep(float(fault.split(":")[1]))
        if fault == "hang":  # recebeu tudo mas "morre" antes de gravar: nada fica salvo
            time.sleep(30)
            return
        if fault == "500":
            return h._error(500, "InternalError")
        problem = self._verify(h, body)
        if problem:
            return h._error(403, problem)
        key_id = h.headers["Authorization"].split("Credential=")[1].split("/")[0]
        perms = self.keys[key_id][1]
        url = urlsplit(h.path)
        path = unquote(url.path).lstrip("/")
        bucket, _, key = path.partition("/")
        if bucket != self.bucket:
            return h._error(404, "NoSuchBucket")
        with self.lock:
            if op == "get" and not key:
                return self._list(h, perms, dict(parse_qsl(url.query)))
            obj = self.current(key)
            now = self.now()
            if op == "head" or op == "get":
                if "read" not in perms:
                    return h._error(403, "AccessDenied")
                if obj is None:
                    return h._error(404, "NoSuchKey") if op == "get" else h._send(404)
                data = bytes(obj["data"])  # type: ignore[arg-type]
                headers = {"Content-Length": str(len(data)), **obj["meta"]}  # type: ignore[dict-item]
                if obj.get("until"):
                    headers.update({"x-amz-object-lock-mode": str(obj["mode"]),
                                    "x-amz-object-lock-retain-until-date": str(obj["until"])})
                if op == "head":
                    return h._send(200, b"", headers)
                if fault == "corrupt":
                    data = data[:-1] + bytes([data[-1] ^ 0xFF])
                return h._send(200, data, headers)
            if op == "put":
                if "write" not in perms:
                    return h._error(403, "AccessDenied")
                md5 = h.headers.get("Content-MD5")
                if md5 and base64.b64decode(md5) != hashlib.md5(body).digest():  # noqa: S324
                    return h._error(400, "BadDigest")
                mode = h.headers.get("x-amz-object-lock-mode")
                if mode and self.style == "r2":
                    return h._error(501, "NotImplemented")
                if obj is not None and self._bucket_locked(obj, now):
                    return h._error(409, "ObjectLockedByBucketPolicy")
                if h.headers.get("If-None-Match") == "*" and obj is not None:
                    return h._error(412, "PreconditionFailed")
                meta = {k: v for k, v in h.headers.items() if k.lower().startswith("x-amz-meta-")}
                self.objects.setdefault(key, []).append({
                    "data": body, "meta": {k.lower(): v for k, v in meta.items()}, "mode": mode,
                    "until": h.headers.get("x-amz-object-lock-retain-until-date"), "at": now})
                return h._send(200, b"", {"ETag": '"' + hashlib.md5(body).hexdigest() + '"'})  # noqa: S324
            if op == "delete":
                if "delete" not in perms:
                    return h._error(403, "AccessDenied")
                if obj is None:
                    return h._send(204)
                if obj.get("until") and datetime.strptime(str(obj["until"]), "%Y-%m-%dT%H:%M:%SZ").replace(
                        tzinfo=timezone.utc) > now:
                    return h._error(403, "AccessDenied")  # COMPLIANCE: nem o dono apaga antes do prazo
                if self._bucket_locked(obj, now):
                    return h._error(409, "ObjectLockedByBucketPolicy")
                self.objects.pop(key)
                return h._send(204)
        return h._error(400, "InvalidRequest")

    def _bucket_locked(self, obj: dict[str, object], now: datetime) -> bool:
        return bool(self.bucket_lock_days) and now < obj["at"] + timedelta(days=self.bucket_lock_days)  # type: ignore[operator]

    def _list(self, h, perms: set[str], query: dict[str, str]) -> None:  # type: ignore[no-untyped-def]
        if "list" not in perms:
            return h._error(403, "AccessDenied")
        prefix, size = query.get("prefix", ""), int(query.get("max-keys", "1000"))
        size = min(size, getattr(self, "page_size", 1000))
        keys = sorted(k for k in self.objects if k.startswith(prefix))
        start = int(query.get("continuation-token", "0"))
        page = keys[start:start + size]
        truncated = start + size < len(keys)
        xml = ["<?xml version='1.0' encoding='UTF-8'?>",
               "<ListBucketResult xmlns='http://s3.amazonaws.com/doc/2006-03-01/'>",
               f"<Name>{self.bucket}</Name><Prefix>{escape(prefix)}</Prefix><IsTruncated>{str(truncated).lower()}</IsTruncated>"]
        xml += [f"<Contents><Key>{escape(k)}</Key><Size>{len(self.current(k)['data'])}</Size></Contents>" for k in page]  # type: ignore[index, arg-type]
        if truncated:
            xml.append(f"<NextContinuationToken>{start + size}</NextContinuationToken>")
        xml.append("</ListBucketResult>")
        return h._send(200, "".join(xml).encode(), {"Content-Type": "application/xml"})
