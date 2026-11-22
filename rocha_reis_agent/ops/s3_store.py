"""Destino EXTERNO de backups por API compatível com S3 (Fase 18B-0) — DESATIVADO nesta fase.

Implementa o contrato `ExternalStore` de `ops/offsite.py` (put/get/list/delete) para provedores com API
S3 (candidatos: Backblaze B2 e Cloudflare R2), com assinatura AWS Signature V4 feita com a biblioteca
padrão (hmac/hashlib) — sem dependência nova. NÃO cifra nada: só aceita arquivos `.rrbackup`, que já
saem cifrados (AES-256-GCM) de `ops/backup.py`; a chave nunca sai do servidor.

Trava: com `EXTERNAL_BACKUP_ACTIVATION_AUTHORIZED` falsa (padrão), o adaptador SÓ aceita endpoint em
127.0.0.1 (servidor S3 falso dos testes) e recusa qualquer outro ANTES de abrir conexão.

Regras:
- nunca sobrescreve: confere antes (HEAD) e envia com `If-None-Match: *`; mesmo nome + mesmo SHA-256 =
  repetição idempotente; mesmo nome + conteúdo diferente = recusa;
- integridade: `x-amz-content-sha256` (o provedor confere o corpo assinado) + `Content-MD5` + SHA-256 nos
  metadados, conferido de novo ao baixar;
- tentativas LIMITADAS só para falhas transitórias (rede, tempo esgotado, 5xx, 429); credencial recusada
  ou permissão insuficiente falham na hora;
- exclusão pelo cliente DESLIGADA por padrão: a retenção fica com o provedor (Object Lock na B2, regras de
  bloqueio do bucket na R2, regras de ciclo de vida);
- mensagens de erro e estatísticas sem credencial, assinatura, URL assinada nem conteúdo.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
import stat
import time
from typing import Callable
from urllib.parse import quote, urlsplit
import xml.etree.ElementTree as ET

from rocha_reis_agent import config as _config
from rocha_reis_agent.ops.offsite import ExternalError

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}\.rrbackup$")
PREFIX = re.compile(r"^(?:[a-z0-9][a-z0-9._-]{0,60}/){0,4}$")
BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
REGION = re.compile(r"^[a-z0-9-]{2,32}$")
EMPTY_SHA = hashlib.sha256(b"").hexdigest()
MAX_LIST_BYTES = 5 * 1024 * 1024
TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}


@dataclass(frozen=True, repr=False)
class Credentials:
    access_key_id: str
    secret_access_key: str

    def __repr__(self) -> str:  # nunca expõe a credencial em log/traceback
        return "Credentials(***)"


def load_credentials(path: Path) -> Credentials:
    """Arquivo JSON 0600 do próprio usuário, FORA do repositório e dos backups."""
    path = Path(path)
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        raise ExternalError("Credencial do armazenamento externo ausente.") from None
    if not stat.S_ISREG(status.st_mode) or status.st_mode & 0o077 or status.st_uid != os.geteuid():
        raise ExternalError("Credencial do armazenamento externo deve ser arquivo 0600 do próprio usuário.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        key_id, secret = str(data["access_key_id"]), str(data["secret_access_key"])
    except (OSError, ValueError, KeyError, TypeError):
        raise ExternalError("Credencial do armazenamento externo ilegível.") from None
    if not (8 <= len(key_id) <= 128 and 16 <= len(secret) <= 256):
        raise ExternalError("Credencial do armazenamento externo inválida.")
    return Credentials(key_id, secret)


@dataclass(frozen=True)
class S3Config:
    endpoint: str
    region: str
    bucket: str
    prefix: str = ""
    provider: str = "s3"                    # "b2" | "r2" | "s3" (só muda as regras de retenção)
    lock_mode: str | None = None            # "COMPLIANCE" | "GOVERNANCE" (Object Lock por objeto; B2)
    retention_days: int | None = None
    allow_client_delete: bool = False
    timeout_seconds: float = 30.0
    attempts: int = 3
    max_object_bytes: int = 2 * 1024 ** 3

    @classmethod
    def from_profile(cls, data: dict[str, object]) -> "S3Config":
        try:
            return cls(endpoint=str(data["endpoint"]), region=str(data["regiao"]), bucket=str(data["bucket"]),
                       prefix=str(data.get("prefixo", "")), provider=str(data.get("provedor", "s3")),
                       lock_mode=(str(data["modo_bloqueio"]) if data.get("modo_bloqueio") else None),
                       retention_days=(int(data["retencao_dias"]) if data.get("retencao_dias") else None),  # type: ignore[call-overload]
                       allow_client_delete=bool(data.get("permitir_exclusao", False)),
                       timeout_seconds=float(data.get("timeout_segundos", 30)),  # type: ignore[arg-type]
                       attempts=int(data.get("tentativas", 3)))  # type: ignore[call-overload]
        except (KeyError, TypeError, ValueError):
            raise ExternalError("Configuração do armazenamento externo incompleta ou inválida.") from None


def _loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_config(cfg: S3Config, *, authorized: bool | None = None) -> tuple[str, str, int]:
    """Valida ANTES de qualquer conexão. Devolve (esquema, host, porta)."""
    authorized = _config.EXTERNAL_BACKUP_ACTIVATION_AUTHORIZED if authorized is None else authorized
    parts = urlsplit(cfg.endpoint)
    host = parts.hostname or ""
    if parts.username or parts.password or parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ExternalError("Endpoint do armazenamento externo deve ser só esquema://host[:porta].")
    if parts.scheme not in ("https", "http") or not host:
        raise ExternalError("Endpoint do armazenamento externo inválido.")
    local = _loopback(host)
    if not local and not authorized:
        raise ExternalError("Envio REAL ao armazenamento externo bloqueado pela trava "
                            "EXTERNAL_BACKUP_ACTIVATION_AUTHORIZED (só 127.0.0.1 nos testes).")
    if parts.scheme == "http" and not local:
        raise ExternalError("Armazenamento externo exige HTTPS.")
    if not BUCKET.match(cfg.bucket) or ".." in cfg.bucket or not REGION.match(cfg.region):
        raise ExternalError("Bucket ou região do armazenamento externo inválidos.")
    if not PREFIX.match(cfg.prefix):
        raise ExternalError("Prefixo do armazenamento externo inválido (ex.: rocha-reis/backups/).")
    if cfg.provider not in ("b2", "r2", "s3"):
        raise ExternalError("Provedor do armazenamento externo desconhecido.")
    if cfg.lock_mode not in (None, "COMPLIANCE", "GOVERNANCE"):
        raise ExternalError("Modo de bloqueio deve ser COMPLIANCE ou GOVERNANCE.")
    if (cfg.lock_mode is None) != (cfg.retention_days is None):
        raise ExternalError("Modo de bloqueio e dias de retenção vão juntos.")
    if cfg.retention_days is not None and not 1 <= cfg.retention_days <= 3000:
        raise ExternalError("Retenção por objeto deve ficar entre 1 e 3000 dias.")
    if cfg.provider == "r2" and cfg.lock_mode:
        # A API S3 da R2 não implementa cabeçalhos x-amz-object-lock-* (documentação oficial, 11/2026):
        # a imutabilidade na R2 é feita por regras de bloqueio DO BUCKET, configuradas na conta.
        raise ExternalError("Cloudflare R2 não aceita Object Lock por objeto; use regras de bloqueio do bucket.")
    if not 1 <= cfg.attempts <= 5 or not 1 <= cfg.timeout_seconds <= 300:
        raise ExternalError("Tentativas (1-5) ou tempo limite (1-300 s) fora do permitido.")
    return parts.scheme, host, parts.port or (443 if parts.scheme == "https" else 80)


# ------------------------------------------------------------------ assinatura AWS Signature V4
def _hmac(key: bytes, text: str) -> bytes:
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).digest()


def _uri(path: str) -> str:
    return quote(path, safe="/-_.~")


def sign_v4(method: str, path: str, query: dict[str, str], headers: dict[str, str], payload_sha256: str,
            credentials: Credentials, region: str, moment: datetime, service: str = "s3") -> str:
    """Valor do cabeçalho Authorization (todos os cabeçalhos informados são assinados)."""
    amz_date = moment.strftime("%Y%m%dT%H%M%SZ")
    day = amz_date[:8]
    canonical_query = "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted(query.items()))
    items = sorted((k.lower().strip(), " ".join(str(v).strip().split())) for k, v in headers.items())
    canonical_headers = "".join(f"{k}:{v}\n" for k, v in items)
    signed = ";".join(k for k, _ in items)
    request = "\n".join([method, _uri(path), canonical_query, canonical_headers, signed, payload_sha256])
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(request.encode()).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(("AWS4" + credentials.secret_access_key).encode(), day), region), service),
                "aws4_request")
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return f"AWS4-HMAC-SHA256 Credential={credentials.access_key_id}/{scope}, SignedHeaders={signed}, Signature={signature}"


# ------------------------------------------------------------------ cliente
@dataclass
class Stats:
    requisicoes: int = 0
    novas_tentativas: int = 0
    bytes_enviados: int = 0
    bytes_recebidos: int = 0
    por_operacao: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {"requisicoes": self.requisicoes, "novas_tentativas": self.novas_tentativas,
                "bytes_enviados": self.bytes_enviados, "bytes_recebidos": self.bytes_recebidos,
                "por_operacao": dict(self.por_operacao)}


class _Transient(Exception):
    pass


class S3ExternalStore:
    """Implementa `ExternalStore` (ops/offsite.py). `simulated` é falso: o estado do envio registra que é
    um destino de verdade — que, com a trava fechada, só pode ser o servidor falso em 127.0.0.1."""

    simulated = False

    def __init__(self, cfg: S3Config, credentials: Credentials, *, authorized: bool | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 sleep: Callable[[float], None] = time.sleep, tls: ssl.SSLContext | None = None) -> None:
        self.scheme, self.host, self.port = validate_config(cfg, authorized=authorized)
        self.cfg, self._credentials, self._clock, self._sleep = cfg, credentials, clock, sleep
        self._tls = tls or ssl.create_default_context()
        self.stats = Stats()
        self.client_deletes = cfg.allow_client_delete  # lido por offsite.sync (retenção pelo cliente?)

    # -- baixo nível
    def _key(self, name: str) -> str:
        if not NAME.match(name):
            raise ExternalError("Nome de objeto inválido.")
        return self.cfg.prefix + name

    def _request(self, op: str, method: str, key: str | None, *, query: dict[str, str] | None = None,
                 body: bytes = b"", extra: dict[str, str] | None = None,
                 max_response: int | None = None) -> tuple[int, dict[str, str], bytes]:
        path = f"/{self.cfg.bucket}" + (f"/{key}" if key is not None else "")
        query = query or {}
        last = "falha de rede"
        for attempt in range(self.cfg.attempts):
            if attempt:
                self.stats.novas_tentativas += 1
                self._sleep(min(8.0, 0.5 * 2 ** (attempt - 1)))
            payload_sha = hashlib.sha256(body).hexdigest() if body else EMPTY_SHA
            moment = self._clock()
            host_header = self.host if self.port in (80, 443) else f"{self.host}:{self.port}"
            headers = {"host": host_header, "x-amz-date": moment.strftime("%Y%m%dT%H%M%SZ"),
                       "x-amz-content-sha256": payload_sha, **(extra or {})}
            if body or method == "PUT":
                headers["content-length"] = str(len(body))
            headers["authorization"] = sign_v4(method, path, query, headers, payload_sha, self._credentials,
                                               self.cfg.region, moment)
            target = _uri(path) + ("?" + "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}"
                                                  for k, v in sorted(query.items())) if query else "")
            self.stats.requisicoes += 1
            self.stats.por_operacao[op] = self.stats.por_operacao.get(op, 0) + 1
            connection = (http.client.HTTPSConnection(self.host, self.port, timeout=self.cfg.timeout_seconds,
                                                      context=self._tls) if self.scheme == "https"
                          else http.client.HTTPConnection(self.host, self.port, timeout=self.cfg.timeout_seconds))
            try:
                connection.request(method, target, body=body or None, headers=headers)
                response = connection.getresponse()
                limit = max_response if max_response is not None else self.cfg.max_object_bytes
                data = response.read(limit + 1) if method != "HEAD" else b""
                if len(data) > limit:
                    raise ExternalError("Resposta do armazenamento externo maior que o permitido.")
                self.stats.bytes_enviados += len(body)
                self.stats.bytes_recebidos += len(data)
                status = response.status
                response_headers = {k.lower(): v for k, v in response.getheaders()}
                if status in TRANSIENT_STATUS:
                    last = f"HTTP {status}"
                    continue
                return status, response_headers, data
            except (socket.timeout, TimeoutError):
                last = "tempo esgotado"
            except (ConnectionError, http.client.HTTPException, ssl.SSLError, OSError) as error:
                last = "certificado TLS recusado" if isinstance(error, ssl.SSLCertVerificationError) else "falha de rede"
                if isinstance(error, ssl.SSLCertVerificationError):
                    break
            finally:
                connection.close()
        raise ExternalError(f"Armazenamento externo indisponível ({last}) após {self.cfg.attempts} tentativa(s).")

    @staticmethod
    def _error(status: int, body: bytes, action: str) -> ExternalError:
        code = ""
        match = re.search(rb"<Code>([A-Za-z]{1,64})</Code>", body[:4096])
        if match:
            code = match.group(1).decode()
        if code in ("SignatureDoesNotMatch", "InvalidAccessKeyId", "ExpiredToken", "InvalidToken") or status == 401:
            return ExternalError(f"Credencial recusada pelo armazenamento externo ({action}).")
        if status == 403:
            return ExternalError(f"Permissão insuficiente no armazenamento externo ({action}; {code or 'HTTP 403'}).")
        if status == 409 or code in ("ObjectLockedByBucketPolicy", "AccessDeniedRetention"):
            return ExternalError(f"Objeto protegido por retenção no armazenamento externo ({action}).")
        return ExternalError(f"Armazenamento externo recusou ({action}; HTTP {status}{'; ' + code if code else ''}).")

    def head(self, name: str) -> dict[str, object] | None:
        status, headers, body = self._request("head", "HEAD", self._key(name))
        if status == 404:
            return None
        if status != 200:
            raise self._error(status, body, "conferir")
        return {"tamanho": int(headers.get("content-length", "-1")), "sha256": headers.get("x-amz-meta-rr-sha256"),
                "retencao_ate": headers.get("x-amz-object-lock-retain-until-date"),
                "modo_bloqueio": headers.get("x-amz-object-lock-mode")}

    # -- contrato ExternalStore
    def put(self, name: str, data: bytes) -> None:
        key = self._key(name)
        if not data.startswith(b"RRBACKUP"):
            raise ExternalError("Só backups cifrados do projeto (.rrbackup) podem sair do servidor.")
        if len(data) > self.cfg.max_object_bytes:
            raise ExternalError("Backup maior que o limite configurado para o armazenamento externo.")
        digest = hashlib.sha256(data).hexdigest()
        existing = self.head(name)
        if existing is not None:
            if existing.get("sha256") == digest and existing.get("tamanho") == len(data):
                return  # repetição idempotente: já está lá, idêntico
            raise ExternalError("Já existe backup com o mesmo nome e conteúdo diferente no destino; nada foi sobrescrito.")
        headers = {"content-type": "application/octet-stream", "if-none-match": "*",
                   "content-md5": base64.b64encode(hashlib.md5(data).digest()).decode(),  # noqa: S324 (integridade, não segurança)
                   "x-amz-meta-rr-sha256": digest, "x-amz-meta-rr-formato": "rocha-reis-backup"}
        if self.cfg.lock_mode:
            until = self._clock() + timedelta(days=int(self.cfg.retention_days or 0))
            headers["x-amz-object-lock-mode"] = self.cfg.lock_mode
            headers["x-amz-object-lock-retain-until-date"] = until.strftime("%Y-%m-%dT%H:%M:%SZ")
        status, _, body = self._request("put", "PUT", key, body=data, extra=headers, max_response=64 * 1024)
        if status in (200, 201):
            return
        if status == 412:  # outro envio chegou antes (corrida): só aceita se for idêntico
            again = self.head(name)
            if again is not None and again.get("sha256") == digest:
                return
            raise ExternalError("Já existe backup com o mesmo nome e conteúdo diferente no destino; nada foi sobrescrito.")
        raise self._error(status, body, "enviar")

    def get(self, name: str) -> bytes:
        status, headers, body = self._request("get", "GET", self._key(name))
        if status == 404:
            raise ExternalError("Backup não encontrado no destino externo.")
        if status != 200:
            raise self._error(status, body, "baixar")
        expected = headers.get("x-amz-meta-rr-sha256")
        if headers.get("content-length") and int(headers["content-length"]) != len(body):
            raise ExternalError("Download incompleto do destino externo.")
        if expected and hashlib.sha256(body).hexdigest() != expected:
            raise ExternalError("Backup baixado não confere com o SHA-256 registrado no envio (corrompido).")
        return body

    def list(self) -> list[str]:
        names: list[str] = []
        token: str | None = None
        for _ in range(1000):  # 1000 páginas × 1000 objetos: muito além da retenção prevista
            query = {"list-type": "2", "prefix": self.cfg.prefix, "max-keys": "1000"}
            if token:
                query["continuation-token"] = token
            status, _, body = self._request("list", "GET", None, query=query, max_response=MAX_LIST_BYTES)
            if status != 200:
                raise self._error(status, body, "listar")
            if b"<!DOCTYPE" in body[:2048] or b"<!ENTITY" in body:
                raise ExternalError("Resposta de listagem recusada (XML com DTD).")
            try:
                root = ET.fromstring(body)
            except ET.ParseError:
                raise ExternalError("Resposta de listagem ilegível.") from None
            ns = root.tag[: root.tag.index("}") + 1] if root.tag.startswith("{") else ""
            for item in root.findall(f"{ns}Contents"):
                key = item.findtext(f"{ns}Key") or ""
                name = key[len(self.cfg.prefix):] if key.startswith(self.cfg.prefix) else ""
                if NAME.match(name):
                    names.append(name)
            if (root.findtext(f"{ns}IsTruncated") or "").lower() != "true":
                return sorted(names)
            token = root.findtext(f"{ns}NextContinuationToken")
            if not token:
                raise ExternalError("Listagem truncada sem token de continuação.")
        raise ExternalError("Listagem do destino externo longa demais.")

    def delete(self, name: str) -> None:
        if not self.cfg.allow_client_delete:
            raise ExternalError("Exclusão pelo servidor desativada: a retenção é feita pelo armazenamento externo.")
        status, _, body = self._request("delete", "DELETE", self._key(name), max_response=64 * 1024)
        if status not in (200, 204):
            raise self._error(status, body, "apagar")


def from_profile(profile_dir: Path, external: dict[str, object], *, authorized: bool | None = None) -> S3ExternalStore:
    """Monta o adaptador a partir da seção `backup.externo` do perfil; credencial em arquivo separado."""
    cfg = S3Config.from_profile(external)
    validate_config(cfg, authorized=authorized)  # trava e formato ANTES de ler a credencial
    credential = Path(str(external.get("credencial", "chaves/armazenamento-externo.json")))
    return S3ExternalStore(cfg, load_credentials(credential if credential.is_absolute() else profile_dir / credential),
                           authorized=authorized)
