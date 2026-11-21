"""Fase 18 — Caddy + WireGuard + nftables REAIS num laboratório de rede isolado (namespaces, sem root).

O que é real: o binário oficial do Caddy com deploy/caddy/Caddyfile.exemplo (só marcadores substituídos), o
WireGuard do KERNEL com chaves de teste geradas na hora a partir de deploy/wireguard/*.exemplo, as regras de
deploy/nftables/rocha-reis.nft.exemplo carregadas no kernel, e webhook/painel/worker da versão instalada.
O que NÃO é real: a internet (uma ponte br0 dentro do namespace), DNS e certificado público (CA interna do
Caddy só para este teste: `local_certs`), o servidor definitivo. Tudo vive em namespaces de usuário/rede/
montagem criados com `unshare`: nada muda na rede, no firewall ou nos serviços do computador; ao terminar o
processo, as interfaces, regras e rotas desaparecem.

Topologia:  servidor  br0 203.0.113.1/24  wg0 10.8.0.1/24   (Caddy :443 público e :443 na VPN)
            ana       eth0 203.0.113.10   wg0 10.8.0.10   (aparelho autorizado)
            bia       eth0 203.0.113.11   wg0 10.8.0.11   (aparelho autorizado, depois revogado)
            intruso   eth0 203.0.113.12   wg0 10.8.0.12   (chave desconhecida; "internet")

Uso (no host):  python tests_infra/fase18_rede.py --destino DIR_NOVO --caddy /caminho/caddy
"""

from __future__ import annotations

import argparse
import base64
from contextlib import closing, contextmanager
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import time
from typing import Iterator

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests_infra"))

from rocha_reis_agent.ops import deploy  # noqa: E402

SERVER_PUBLIC, VPN_SERVER = "203.0.113.1", "10.8.0.1"
DEVICES = {"ana": ("203.0.113.10", "10.8.0.10"), "bia": ("203.0.113.11", "10.8.0.11"),
           "intruso": ("203.0.113.12", "10.8.0.12")}
ORIGIN, PANEL, WEBHOOK = "https://painel.interno", "painel.interno", "webhook.teste"
ORIG_NS = -1


# ------------------------------------------------------------------ utilidades
def sh(*cmd: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), check=check, text=True, capture_output=True, timeout=60)


@contextmanager
def ns(name: str | None) -> Iterator[None]:
    """Executa o bloco DENTRO do namespace de rede do aparelho (sockets criados aqui pertencem a ele)."""
    if name is None:
        yield
        return
    fd = os.open(f"/run/netns/{name}", os.O_RDONLY)
    os.setns(fd, os.CLONE_NEWNET)
    try:
        yield
    finally:
        os.setns(ORIG_NS, os.CLONE_NEWNET)
        os.close(fd)


def tcp(name: str | None, ip: str, port: int, timeout: float = 2.0) -> str:
    with ns(name):
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                return "aberta"
        except ConnectionRefusedError:
            return "recusada"
        except (socket.timeout, TimeoutError):
            return "sem_resposta"
        except OSError as error:
            return f"erro:{error.errno}"


class Pinned(http.client.HTTPSConnection):
    """HTTPS para um IP fixo com SNI/Host do nome (equivale a `curl --resolve`)."""

    def __init__(self, ip: str, host: str, context: ssl.SSLContext, timeout: float) -> None:
        super().__init__(host, 443, context=context, timeout=timeout)
        self.ip = ip

    def connect(self) -> None:
        raw = socket.create_connection((self.ip, 443), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


class Client:
    def __init__(self, netns: str | None, ip: str, host: str, context: ssl.SSLContext) -> None:
        self.netns, self.ip, self.host, self.context = netns, ip, host, context
        self.cookies: dict[str, str] = {}
        self.set_cookie_raw: list[str] = []
        self.csrf = ""

    def request(self, method: str, path: str, body: bytes | None = None, headers: dict[str, str] | None = None,
                timeout: float = 5.0) -> tuple[object, dict[str, str], bytes]:
        with ns(self.netns):
            connection = Pinned(self.ip, self.host, self.context, timeout)
            try:
                head = dict(headers or {})
                if self.cookies:
                    head["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
                connection.request(method, path, body=body, headers=head)
                response = connection.getresponse()
                data = response.read()
                for value in response.headers.get_all("Set-Cookie") or []:
                    self.set_cookie_raw.append(value)
                    name, _, rest = value.partition("=")
                    cookie_value = rest.split(";", 1)[0]
                    if "max-age=0" in value.lower() or cookie_value in ('""', ""):
                        self.cookies.pop(name, None)
                    else:
                        self.cookies[name] = cookie_value
                return response.status, {k.lower(): v for k, v in response.headers.items()}, data
            except (socket.timeout, TimeoutError):
                return "sem_resposta", {}, b""
            except ssl.SSLError as error:
                return f"tls:{error.reason}", {}, b""
            except (ConnectionError, OSError) as error:
                return f"erro:{type(error).__name__}", {}, b""
            finally:
                connection.close()

    def api(self, method: str, path: str, payload: dict | None = None, headers: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
        head = {"Origin": ORIGIN, "X-CSRF-Token": self.csrf, **(headers or {})}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            head["Content-Type"] = "application/json"
        status, hdrs, data = self.request(method, "/api/panel" + path, body, head)
        parsed = None
        try:
            parsed = json.loads(data) if data else None
        except ValueError:
            pass
        if isinstance(parsed, dict) and parsed.get("csrf_token"):
            self.csrf = parsed["csrf_token"]
        return status, parsed


class Report:
    def __init__(self) -> None:
        self.steps: list[dict[str, object]] = []

    def step(self, name: str, ok: bool, **details: object) -> bool:
        self.steps.append({"etapa": name, "ok": bool(ok), **details})
        print(("OK   " if ok else "FALHA ") + name, json.dumps(details, ensure_ascii=False, default=str)[:500], flush=True)
        return ok


_last_step = [0]


def mfa_code(secret: bytes) -> str:
    """Código TOTP de um intervalo ainda não usado (o painel recusa reutilização do mesmo código)."""
    from rocha_reis_agent.panel import mfa as mfa_module
    while int(time.time() // 30) <= _last_step[0]:
        time.sleep(0.5)
    _last_step[0] = int(time.time() // 30)
    return mfa_module.code_for(secret)


# ------------------------------------------------------------------ configurações a partir de deploy/
def render_caddy(dest: Path, *, bind_public: bool) -> Path:
    text = (REPO / "deploy/caddy/Caddyfile.exemplo").read_text(encoding="utf-8")
    text = (text.replace("<webhook.dominio-do-salao>", WEBHOOK).replace("<painel.interno>", PANEL)
            .replace("<IP-DA-VPN>", VPN_SERVER).replace("/var/log/caddy/rocha-reis.log", str(dest / "caddy.log")))
    text = text.replace("{\n\t# API administrativa", "{\n\t# Fase 18 (TESTE): CA interna para todos os sites (sem ACME), "
                        "sem instalar a CA no sistema\n\tlocal_certs\n\tskip_install_trust\n\tstorage file_system "
                        f"{dest / 'dados'}\n\t# API administrativa", 1)
    if bind_public:
        text = text.replace(f"{WEBHOOK} {{\n", f"{WEBHOOK} {{\n\tbind {SERVER_PUBLIC}\n", 1)
    leftover = [line for line in text.splitlines() if re.search(r"<[A-Za-z]", line) and not line.lstrip().startswith("#")]
    assert not leftover, leftover
    path = dest / ("Caddyfile.bind-publico" if bind_public else "Caddyfile")
    path.write_text(text, encoding="utf-8")
    return path


def render_wireguard(dest: Path) -> dict[str, object]:
    import wgnl
    server_tpl = (REPO / "deploy/wireguard/wg0.conf.exemplo").read_text(encoding="utf-8")
    device_tpl = (REPO / "deploy/wireguard/aparelho.conf.exemplo").read_text(encoding="utf-8")
    server_key = wgnl.genkey()
    head, peer_tpl = server_tpl.split("[Peer]", 1)
    keys = {name: (wgnl.genkey(), wgnl.genpsk()) for name in DEVICES}
    blocks = {}
    for name in ("ana", "bia"):  # só os autorizados entram no servidor
        priv, psk = keys[name]
        blocks[name] = "[Peer]" + (peer_tpl.replace("<Nome do funcionário> — <aparelho> — criado em <AAAA-MM-DD>",
                                                    f"{name} (TESTE) — aparelho fictício — criado em 2026-11-21")
                                   .replace("<CHAVE-PUBLICA-DO-APARELHO>", wgnl.pubkey(priv))
                                   .replace("<CHAVE-PRE-COMPARTILHADA-DO-APARELHO>", psk)
                                   .replace("10.8.0.10/32", f"{DEVICES[name][1]}/32"))
    head = head.replace("<CHAVE-PRIVADA-DO-SERVIDOR>", server_key)
    devices = {}
    for name, (priv, psk) in keys.items():
        devices[name] = (device_tpl.replace("<CHAVE-PRIVADA-DO-APARELHO>", priv)
                         .replace("<CHAVE-PUBLICA-DO-SERVIDOR>", wgnl.pubkey(server_key))
                         .replace("<CHAVE-PRE-COMPARTILHADA-DO-APARELHO>", psk)
                         .replace("<IP-OU-NOME-PUBLICO-DO-SERVIDOR>", SERVER_PUBLIC)
                         .replace("10.8.0.10/32", f"{DEVICES[name][1]}/32"))
    folder = dest / "wireguard"
    folder.mkdir(mode=0o700)
    for name, text in {"wg0.conf": head + "".join(blocks.values()), **{f"{n}.conf": t for n, t in devices.items()}}.items():
        fd = os.open(folder / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
    return {"head": head, "blocks": blocks, "folder": folder, "server_pub": wgnl.pubkey(server_key),
            "pubs": {n: wgnl.pubkey(k[0]) for n, k in keys.items()}}


def wg_up(netns: str | None, config_text: str) -> None:
    import wgnl
    prefix = ["ip", "-n", netns] if netns else ["ip"]
    sh(*prefix, "link", "add", "wg0", "type", "wireguard")
    config = wgnl.parse(config_text)
    with ns(netns):
        link = wgnl.Netlink()
        try:
            link.set_device("wg0", config)
        finally:
            link.close()
    for address in config.addresses:
        sh(*prefix, "address", "add", address, "dev", "wg0")
    sh(*prefix, "link", "set", "wg0", "up")
    if netns:  # wg-quick adiciona rotas para os AllowedIPs do aparelho
        for peer in config.peers:
            for allowed in peer.allowed_ips:
                sh(*prefix, "route", "add", allowed, "dev", "wg0")


def wg_sync(config_text: str, netns: str | None = None) -> list[str]:
    """`wg syncconf wg0 <(wg-quick strip wg0)` — aplica sem derrubar a interface nem as sessões dos demais."""
    import wgnl
    with ns(netns):
        link = wgnl.Netlink()
    try:
        return link.sync_device("wg0", wgnl.parse(config_text))
    finally:
        link.close()


def wait_for(condition, timeout: float, interval: float = 0.5) -> float | None:  # type: ignore[no-untyped-def]
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if condition():
            return round(time.monotonic() - started, 1)
        time.sleep(interval)
    return None


def wg_state() -> dict[str, object]:
    import wgnl
    link = wgnl.Netlink()
    try:
        return link.get_device("wg0")
    finally:
        link.close()


# ------------------------------------------------------------------ fase interna (dentro dos namespaces)
def inner(dest: Path, caddy: str, python: str) -> int:
    global ORIG_NS
    from rocha_reis_agent.channels.meta import sign
    from rocha_reis_agent.ops.opsdrill import _envelope
    r = Report()
    root = dest / "raiz"
    processes: list[subprocess.Popen[bytes]] = []
    caddy_proc: subprocess.Popen[bytes] | None = None
    try:
        # 0. namespaces (montagem privada: /run/netns só existe aqui dentro)
        sh("mount", "-t", "tmpfs", "-o", "mode=0755", "tmpfs", "/run")
        os.makedirs("/run/netns")
        ORIG_NS = os.open("/proc/self/ns/net", os.O_RDONLY)
        sh("ip", "link", "set", "lo", "up")
        sh("ip", "link", "add", "br0", "type", "bridge")
        sh("ip", "address", "add", f"{SERVER_PUBLIC}/24", "dev", "br0")
        sh("ip", "link", "set", "br0", "up")
        for name, (public, _) in DEVICES.items():
            sh("ip", "netns", "add", name)
            sh("ip", "link", "add", f"v-{name}", "type", "veth", "peer", "name", "eth0", "netns", name)
            sh("ip", "link", "set", f"v-{name}", "master", "br0", "up")
            sh("ip", "-n", name, "address", "add", f"{public}/24", "dev", "eth0")
            sh("ip", "-n", name, "link", "set", "eth0", "up")
            sh("ip", "-n", name, "link", "set", "lo", "up")
        forward = Path("/proc/sys/net/ipv4/ip_forward").read_text().strip()
        r.step("laboratorio_de_rede", True, ip_forward_servidor=forward,
               aviso="namespaces de usuário/rede/montagem; nada muda na rede do computador")

        # 1. nftables real (referência com marcadores preenchidos)
        rules = (REPO / "deploy/nftables/rocha-reis.nft.exemplo").read_text(encoding="utf-8")
        rules = rules.replace("<if-publica>", "br0").replace("<IP-ADMINISTRACAO>", "203.0.113.250")
        (dest / "rocha-reis.nft").write_text(rules, encoding="utf-8")
        check = sh("nft", "-c", "-f", str(dest / "rocha-reis.nft"), check=False)
        applied = sh("nft", "-f", str(dest / "rocha-reis.nft"), check=False) if check.returncode == 0 else check
        r.step("nftables_carregado_no_kernel", check.returncode == 0 and applied.returncode == 0,
               erro=(check.stderr or applied.stderr).strip()[-400:])

        # 2. aplicação (versão instalada) só em 127.0.0.1 do servidor
        current = deploy.layout(root)["current"].resolve()
        env = {"PATH": os.environ["PATH"], "HOME": str(dest), "PYTHONDONTWRITEBYTECODE": "1",
               "ROCHA_REIS_PROFILE": str(deploy.layout(root)["config"])}
        logs = dest / "processos"
        logs.mkdir()
        for name, cmd in {
            "webhook": [python, "-m", "uvicorn", "rocha_reis_agent.main:create_app", "--factory", "--host", "127.0.0.1",
                        "--port", "8080", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"],
            "painel": [python, "-m", "uvicorn", "rocha_reis_agent.panel.app:create_panel_app", "--factory", "--host",
                       "127.0.0.1", "--port", "8000", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips",
                       "127.0.0.1"],
            "worker": [python, "-m", "rocha_reis_agent.worker"]}.items():
            with open(logs / f"{name}.log", "wb") as out:
                processes.append(subprocess.Popen(cmd, cwd=current, env=env, stdout=out, stderr=subprocess.STDOUT))
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not (
                deploy.http_status("http://127.0.0.1:8080/ready") == 200
                and deploy.http_status("http://127.0.0.1:8000/ready", host=PANEL) == 200):
            time.sleep(0.3)
        r.step("aplicacao_pronta", deploy.http_status("http://127.0.0.1:8000/ready", host=PANEL) == 200)

        # 3. Caddy: validação da referência e ORDEM de subida (o painel escuta no IP da VPN)
        caddy_env = {"PATH": os.environ["PATH"], "HOME": str(dest), "XDG_DATA_HOME": str(dest / "xdg-data"),
                     "XDG_CONFIG_HOME": str(dest / "xdg-config")}
        caddyfile = render_caddy(dest, bind_public=False)
        valid = subprocess.run([caddy, "validate", "--adapter", "caddyfile", "--config", str(caddyfile)],
                               capture_output=True, text=True, env=caddy_env, timeout=60)
        r.step("caddy_validate", valid.returncode == 0, saida=(valid.stderr or valid.stdout).strip()[-500:])

        def start_caddy(path: Path) -> tuple[subprocess.Popen[bytes], bool, str]:
            out = open(dest / "caddy-processo.log", "ab")
            proc = subprocess.Popen([caddy, "run", "--adapter", "caddyfile", "--config", str(path)], env=caddy_env,
                                    stdout=out, stderr=subprocess.STDOUT)
            ok = False
            for _ in range(60):
                if proc.poll() is not None:
                    break
                if tcp(None, SERVER_PUBLIC, 443, 0.5) == "aberta" and tcp(None, "127.0.0.1", 2019, 0.5) == "aberta":
                    ok = True
                    break
                time.sleep(0.25)
            tail = (dest / "caddy-processo.log").read_text(errors="replace")[-700:]
            return proc, ok, tail

        proc, ok, tail = start_caddy(caddyfile)  # wg0 ainda NÃO existe
        if proc.poll() is None:
            proc.terminate()
            proc.wait(10)
        public_up_without_vpn = ok
        # Alternativa que desacopla: ip_nonlocal_bind=1 deixa o Caddy escutar 10.8.0.1 antes de a VPN existir.
        nonlocal_path = Path("/proc/sys/net/ipv4/ip_nonlocal_bind")
        try:
            nonlocal_path.write_text("1\n")
        except OSError as error:
            ok_nonlocal = f"sysctl indisponível no namespace: {error.strerror}"
        else:
            proc, ok_nonlocal, _ = start_caddy(caddyfile)
            if proc.poll() is None:
                proc.terminate()
                proc.wait(10)
            nonlocal_path.write_text("0\n")  # o restante do teste usa o padrão do sistema (0)
        r.step("caddy_antes_da_vpn", not public_up_without_vpn and ok_nonlocal, subiu_sem_vpn=public_up_without_vpn,
               subiu_com_ip_nonlocal_bind=ok_nonlocal,
               observacao="sem o IP da VPN o Caddy INTEIRO não sobe (inclusive o webhook público)",
               trecho=[x[-220:] for x in tail.splitlines() if "bind" in x or "assign" in x][-1:])

        # 4. WireGuard real: servidor + aparelhos a partir das referências
        wg = render_wireguard(dest)
        folder: Path = wg["folder"]  # type: ignore[assignment]
        wg_up(None, (folder / "wg0.conf").read_text())
        for name in DEVICES:
            wg_up(name, (folder / f"{name}.conf").read_text())
        r.step("wireguard_configurado", True, servidor=wg_state().get("listen_port"),
               aparelhos=list(DEVICES), chaves="geradas na hora, só de teste, 0600 em " + folder.name)

        proc, ok, tail = start_caddy(caddyfile)
        variant = "referencia"
        if not ok:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(10)
            r.step("caddy_referencia_sobe_com_vpn", False, trecho=tail.strip().splitlines()[-4:])
            proc, ok, tail = start_caddy(render_caddy(dest, bind_public=True))
            variant = "bind_publico"
        caddy_proc = proc
        r.step("caddy_em_execucao", ok, variante=variant, trecho=tail.strip().splitlines()[-2:] if not ok else [])
        context = ssl.create_default_context(cafile=str(dest / "dados/pki/authorities/local/root.crt"))

        # 5. aparelhos autorizados: handshake e painel pela VPN
        ana = Client("ana", VPN_SERVER, PANEL, context)
        bia = Client("bia", VPN_SERVER, PANEL, context)
        status_ana = ana.request("GET", "/panel/")[0]
        status_bia = bia.request("GET", "/panel/")[0]
        state = wg_state()
        handshakes = {n: state["peers"].get(wg["pubs"][n], {}).get("ultimo_handshake", 0) > 0  # type: ignore[union-attr]
                      for n in ("ana", "bia")}
        with ns("ana"):
            plain = socket.create_connection((VPN_SERVER, 443), 4)
            tls = context.wrap_socket(plain, server_hostname=PANEL)
            tls_info = {"versao": tls.version(), "cifra": tls.cipher()[0],
                        "emissor": dict(x[0] for x in tls.getpeercert()["issuer"]).get("commonName")}
            tls.close()
        r.step("vpn_handshake_e_painel", status_ana == 200 and status_bia == 200 and all(handshakes.values()),
               painel_ana=status_ana, painel_bia=status_bia, handshake=handshakes, tls=tls_info)

        # 6. login com troca de senha e 2º fator ATRAVÉS de VPN + Caddy (cookie Secure)
        password = (deploy.layout(root)["config"] / "credenciais-iniciais.txt").read_text().splitlines()[1].split("\t")[2]
        s1, b1 = ana.api("POST", "/auth/login", {"login": "admin.preprod", "password": password})
        new_password = "Rede-Fase18-Senha-Definitiva!"
        s2, _ = ana.api("POST", "/auth/password", {"current_password": password, "new_password": new_password})
        s3, setup = ana.api("POST", "/auth/mfa/setup", {"password": new_password})
        secret_text = (setup or {}).get("secret", "").replace(" ", "")
        secret = base64.b32decode(secret_text + "=" * (-len(secret_text) % 8)) if secret_text else b""
        s4, _ = ana.api("POST", "/auth/mfa/activate", {"code": mfa_code(secret)})
        ana.api("POST", "/auth/logout", {})
        s5, b5 = ana.api("POST", "/auth/login", {"login": "admin.preprod", "password": new_password})
        s6, _ = ana.api("POST", "/auth/mfa", {"code": mfa_code(secret)})
        s7, listing = ana.api("GET", "/conversations?filter=waiting")
        secure = all("secure" in c.lower() and "httponly" in c.lower() for c in ana.set_cookie_raw if "=;" not in c)
        r.step("login_2fa_pela_vpn", (s1, s2, s3, s4, s5, s6, s7) == (200, 200, 200, 200, 200, 200, 200)
               and (b5 or {}).get("mfa_required") is True and secure,
               codigos=[s1, s2, s3, s4, s5, s6, s7], cookies_secure_httponly=secure)

        # 7. webhook público pelo Caddy (intruso = internet) e atualização ao vivo (SSE) no painel pela VPN
        profile = json.loads((deploy.layout(root)["config"] / "perfil.json").read_text())["settings"]
        public = Client("intruso", SERVER_PUBLIC, WEBHOOK, context)
        challenge = "desafio-fase18"
        verify = public.request("GET", f"/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token="
                                       f"{profile['whatsapp_verify_token']}&hub.challenge={challenge}")
        with ns("ana"):
            sse = Pinned(VPN_SERVER, PANEL, context, 15)
            sse.request("GET", "/api/panel/events", headers={
                "Cookie": "; ".join(f"{k}={v}" for k, v in ana.cookies.items()), "Accept": "text/event-stream"})
            sse_response = sse.getresponse()
        raw = _envelope("wamid.f18.rede.1", "Quero falar com uma pessoa", "5511900009100")
        sent_at = time.monotonic()
        posted = public.request("POST", "/webhooks/whatsapp", raw, {
            "Content-Type": "application/json", "X-Hub-Signature-256": sign(profile["whatsapp_app_secret"].encode(), raw)})
        got_event, latency = False, None
        buffer = b""
        while time.monotonic() - sent_at < 12:
            try:
                chunk = sse_response.read1(4096)
            except (socket.timeout, TimeoutError):
                break
            if not chunk:
                break
            buffer += chunk
            if b"event: changes" in buffer:
                got_event, latency = True, round(time.monotonic() - sent_at, 2)
                break
        sse.close()
        r.step("webhook_publico_e_sse_pela_vpn", verify[0] == 200 and verify[2] == challenge.encode()
               and posted[0] == 200 and sse_response.status == 200 and got_event,
               verificacao=verify[0], post=posted[0], sse=sse_response.status, evento_ao_vivo=got_event,
               latencia_s=latency, buffer_proxy=sse_response.headers.get("X-Accel-Buffering"))

        # 8. superfície pública: só o webhook; tudo o mais 404, sem painel por nome, caminho ou cabeçalho
        paths = ["/panel/", "/panel", "/api/panel/auth/session", "/health", "/ready", "/docs", "/openapi.json",
                 "/webhooks/whatsapp/../../panel/", "/%2e%2e/panel/", "//panel/", "/WEBHOOKS/whatsapp",
                 "/webhooks/whatsapp%2f..%2f..%2fpanel%2f", "/webhooks/whatsapp/extra", "/webhooks//whatsapp/../../api/panel/auth/session"]
        results = {p: public.request("GET", p)[0] for p in paths}
        methods = {m: public.request(m, "/webhooks/whatsapp", b"{}")[0] for m in ("PUT", "DELETE", "PATCH")}
        fronting = public.request("GET", "/panel/", headers={"Host": PANEL})
        fronting_api = public.request("GET", "/api/panel/auth/session", headers={"Host": PANEL})
        sni_panel = Client("intruso", SERVER_PUBLIC, PANEL, context).request("GET", "/panel/")
        panel_page = ana.request("GET", "/panel/")[2]

        def leaked(answer) -> bool:  # type: ignore[no-untyped-def]
            return answer[0] == 200 and (len(answer[2]) > 0 or answer[2] == panel_page)

        r.step("superficie_publica_isolada", all(v == 404 for v in results.values())
               and all(v in (404, 405) for v in methods.values())
               and not any(leaked(x) for x in (fronting, fronting_api, sni_panel)), caminhos=results, metodos=methods,
               host_do_painel_no_publico={"status": fronting[0], "bytes": len(fronting[2])},
               host_do_painel_api={"status": fronting_api[0], "bytes": len(fronting_api[2])},
               sni_do_painel_no_publico={"status": sni_panel[0], "bytes": len(sni_panel[2])},
               bytes_da_pagina_real_do_painel=len(panel_page))

        # 9. X-Forwarded-For forjado não engana o limitador de login (IP real = 10.8.0.11 da VPN)
        codes = []
        for i in range(7):
            status, _ = bia.api("POST", "/auth/login", {"login": f"inexistente.{i}", "password": "errada-errada"},
                                headers={"X-Forwarded-For": f"198.51.100.{i + 1}", "X-Real-IP": f"198.51.100.{i + 1}"})
            codes.append(status)
        database = Path(json.loads((deploy.layout(root)["config"] / "perfil.json").read_text())["database"])
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as c:
            keys = {row[0] for row in c.execute("SELECT key_hash FROM panel_login_attempts")}
        real = hashlib.sha256(f"ip:{DEVICES['bia'][1]}".encode()).hexdigest() in keys
        forged = [i for i in range(7) if hashlib.sha256(f"ip:198.51.100.{i + 1}".encode()).hexdigest() in keys]
        r.step("xff_forjado_ignorado", real and not forged and 429 in codes, codigos=codes,
               ip_real_registrado=real, ips_forjados_registrados=forged)

        # 10. limites do Caddy
        big_panel = ana.request("POST", "/api/panel/auth/login", b"{" + b" " * (70 * 1024) + b"}",
                                {"Content-Type": "application/json", "Origin": ORIGIN})[0]
        big_hook = public.request("POST", "/webhooks/whatsapp", b"{" + b" " * (300 * 1024) + b"}",
                                  {"Content-Type": "application/json"})[0]
        big_header = ana.request("GET", "/panel/", headers={"X-Grande": "a" * (20 * 1024)})[0]
        with ns("ana"):
            slow = context.wrap_socket(socket.create_connection((VPN_SERVER, 443), 5), server_hostname=PANEL)
        slow.sendall(f"GET /panel/ HTTP/1.1\r\nHost: {PANEL}\r\n".encode())
        slow.settimeout(20)
        t0 = time.monotonic()
        try:
            answer = slow.recv(200)
        except (socket.timeout, TimeoutError, ssl.SSLError, OSError):
            answer = b"<sem resposta>"
        slow_seconds = round(time.monotonic() - t0, 1)
        slow.close()
        r.step("limites_e_timeouts_do_caddy", big_panel == 413 and big_hook == 413 and big_header == 431
               and slow_seconds <= 15, corpo_70kb_painel=big_panel, corpo_300kb_webhook=big_hook,
               cabecalho_20kb=big_header, cabecalho_lento_fechado_em_s=slow_seconds,
               resposta_ao_lento=answer[:40].decode(errors="replace"))

        # 11. firewall (sem VPN): portas internas e SSH fechadas; 443 aberta
        ports = {p: tcp("intruso", SERVER_PUBLIC, p) for p in (443, 8000, 8080, 2019, 22)}
        vpn_ports = {p: tcp("ana", VPN_SERVER, p) for p in (443, 8000, 8080, 2019, 22)}
        r.step("firewall_portas", ports[443] == "aberta" and all(ports[p] == "sem_resposta" for p in (8000, 8080, 2019, 22))
               and vpn_ports[443] == "aberta" and all(vpn_ports[p] == "sem_resposta" for p in (8000, 8080, 2019, 22)),
               publico=ports, pela_vpn=vpn_ports)

        # 12. chave desconhecida não estabelece túnel
        unknown = tcp("intruso", VPN_SERVER, 443, 3)
        state = wg_state()
        r.step("chave_desconhecida_recusada", unknown == "sem_resposta" and wg["pubs"]["intruso"] not in state["peers"],  # type: ignore[operator]
               resultado=unknown)

        # 13. aparelho autorizado não consegue se passar por outro (IP de bia dentro do túnel de ana)
        #     (o aparelho é controlado pelo funcionário: aqui ele MEXE na própria configuração de propósito)
        sh("ip", "-n", "ana", "address", "del", f"{DEVICES['ana'][1]}/32", "dev", "wg0")
        sh("ip", "-n", "ana", "address", "add", f"{DEVICES['bia'][1]}/32", "dev", "wg0")
        sh("ip", "-n", "ana", "route", "replace", f"{VPN_SERVER}/32", "dev", "wg0", "src", DEVICES["bia"][1])
        spoof = tcp("ana", VPN_SERVER, 443, 3)
        sh("ip", "-n", "ana", "address", "del", f"{DEVICES['bia'][1]}/32", "dev", "wg0")
        sh("ip", "-n", "ana", "address", "add", f"{DEVICES['ana'][1]}/32", "dev", "wg0")
        sh("ip", "-n", "ana", "route", "replace", f"{VPN_SERVER}/32", "dev", "wg0")
        own = (folder / "ana.conf").read_text()
        wg_sync(own.replace("AllowedIPs = 10.8.0.1/32", "AllowedIPs = 10.8.0.0/24"), "ana")  # alarga a própria rota
        sh("ip", "-n", "ana", "route", "replace", f"{DEVICES['bia'][1]}/32", "dev", "wg0")
        lateral = tcp("ana", DEVICES["bia"][1], 443, 3)
        wg_sync(own, "ana")
        sh("ip", "-n", "ana", "route", "del", f"{DEVICES['bia'][1]}/32", "dev", "wg0")
        r.step("isolamento_entre_aparelhos", spoof == "sem_resposta" and lateral == "sem_resposta"
               and ana.request("GET", "/panel/")[0] == 200, ip_de_outro_aparelho=spoof,
               ana_para_bia_com_rota_alargada=lateral,
               observacao="servidor descarta origem fora do AllowedIPs do peer e não encaminha entre aparelhos")

        # 14. fronteira da VPN: o IP 10.8.0.1 alcançado SEM túnel, pela interface pública (mesma rede local
        #     do provedor). O firewall precisa recusar; a regra de 443 público não pode valer para o IP da VPN.
        sh("ip", "-n", "intruso", "route", "replace", f"{VPN_SERVER}/32", "via", SERVER_PUBLIC, "dev", "eth0")
        bypass_tcp = tcp("intruso", VPN_SERVER, 443, 3)
        bypass = Client("intruso", VPN_SERVER, PANEL, context).request("GET", "/panel/", timeout=4)[0]
        sh("ip", "-n", "intruso", "route", "replace", f"{VPN_SERVER}/32", "dev", "wg0")
        r.step("painel_inacessivel_sem_tunel", bypass_tcp != "aberta" and bypass != 200,
               tcp=bypass_tcp, painel=bypass)

        # 15. revogação (syncconf sem o bloco da bia): a conexão já aberta cai e novas não entram
        with ns("bia"):
            kept = Pinned(VPN_SERVER, PANEL, context, 4)
            kept.connect()
        revoked_text = wg["head"] + wg["blocks"]["ana"]  # type: ignore[operator]
        (folder / "wg0.conf").write_text(revoked_text)
        removed = wg_sync(revoked_text)
        try:
            kept.request("GET", "/panel/")
            old = kept.getresponse().status
        except (socket.timeout, TimeoutError, OSError, http.client.HTTPException) as error:
            old = type(error).__name__
        kept.close()
        new_bia = tcp("bia", VPN_SERVER, 443, 3)
        still_ana = ana.request("GET", "/panel/")[0]
        r.step("revogacao_imediata", old != 200 and new_bia == "sem_resposta" and still_ana == 200
               and removed == [wg["pubs"]["bia"]], conexao_aberta_apos_revogar=old,  # type: ignore[index]
               nova_conexao_bia=new_bia, ana_continua=still_ana, peers_removidos=len(removed))

        # 16. recuperação: interface do servidor recriada a partir do wg0.conf guardado (servidor reinstalado)
        sh("ip", "link", "del", "wg0")
        down = tcp("ana", VPN_SERVER, 443, 2)
        wg_up(None, (folder / "wg0.conf").read_text())
        # O aparelho ainda usa a sessão antiga: refaz o handshake sozinho (sem ação do funcionário) em até ~15-25 s.
        reconnect = wait_for(lambda: ana.request("GET", "/panel/", timeout=3)[0] == 200, 60, 1.0)
        relog = Client("ana", VPN_SERVER, PANEL, context)
        s8, b8 = relog.api("POST", "/auth/login", {"login": "admin.preprod", "password": new_password})
        s9, _ = relog.api("POST", "/auth/mfa", {"code": mfa_code(secret)})
        r.step("recuperacao_da_vpn_com_2fa", down == "sem_resposta" and reconnect is not None and s8 == 200
               and (b8 or {}).get("mfa_required") is True and s9 == 200,
               sem_interface=down, reconexao_automatica_s=reconnect, login=s8, segundo_fator=s9)

        # 17. logs do Caddy sem segredos, cookies, assinatura, token ou IP completo
        caddy_log = (dest / "caddy.log").read_text(errors="replace") if (dest / "caddy.log").exists() else ""
        needles = {"token_verificacao": profile["whatsapp_verify_token"], "desafio": challenge,
                   "assinatura": sign(profile["whatsapp_app_secret"].encode(), raw).split("=", 1)[1],
                   **{f"cookie:{k}": v for k, v in ana.cookies.items()}, "csrf": ana.csrf,
                   "ip_completo_ana": DEVICES["ana"][1], "ip_completo_intruso": DEVICES["intruso"][0]}
        hits = {k: caddy_log.count(v) for k, v in needles.items() if v}
        mode = oct((dest / "caddy.log").stat().st_mode & 0o777) if (dest / "caddy.log").exists() else None
        r.step("logs_do_caddy", bool(caddy_log) and not any(hits.values()), ocorrencias=hits,
               linhas=len(caddy_log.splitlines()), permissao=mode)

        # 18. limite de novas conexões por IP no 443 público (nftables), por último: bloqueia o intruso por 1 min
        outcomes = [tcp("intruso", SERVER_PUBLIC, 443, 1.0) for _ in range(75)]
        first_drop = next((i + 1 for i, o in enumerate(outcomes) if o != "aberta"), None)
        r.step("limite_de_conexoes_webhook", first_drop is not None and 15 <= first_drop <= 25,
               aceitas=outcomes.count("aberta"), primeira_recusada=first_drop,
               observacao="60 novas conexões/min por IP com rajada de 20; a Meta entrega por poucos IPs (6B)")
    except Exception as error:  # noqa: BLE001
        r.steps.append({"etapa": "interrompido", "ok": False, "erro": f"{type(error).__name__}: {error}"[:800]})
    finally:
        for proc in ([caddy_proc] if caddy_proc else []) + processes:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    proc.kill()
    report = {"tipo": "TESTE COM SERVIÇOS REAIS EM LABORATÓRIO DE REDE ISOLADO (namespaces) — não é servidor real",
              "resultado": "OK" if all(s["ok"] for s in r.steps) else "FALHOU", "etapas": r.steps}
    out = dest / "relatorio-rede.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.chmod(out, 0o600)
    return 0 if report["resultado"] == "OK" else 1


# ------------------------------------------------------------------ fase externa (usuário comum)
def outer(dest: Path, caddy: str, python: str) -> int:
    root = dest / "raiz"
    package = deploy.build_package(dest / "pacotes")
    installed = deploy.install_release(package, root, python=python, create_venv=False)
    deploy.activate(root, str(installed["id"]))
    deploy.prepare_instance(root, panel_origin=ORIGIN)
    validation = deploy.validate_installation(root)
    if not validation["ok"]:
        raise SystemExit(f"Instalação inválida: {validation['problemas']}")
    caddy_version = subprocess.run([caddy, "version"], capture_output=True, text=True).stdout.strip()
    print(json.dumps({"versao": installed["versao"], "caddy": caddy_version}), flush=True)
    done = subprocess.run(["unshare", "--user", "--map-root-user", "--net", "--mount", "--fork", python, "-B",
                           str(Path(__file__).resolve()), "--interno", "--destino", str(dest), "--caddy", caddy,
                           "--python", python])
    return done.returncode


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--destino", required=True)
    parser.add_argument("--caddy", required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--interno", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--manter", action="store_true")
    args = parser.parse_args()
    destination = Path(args.destino).expanduser().absolute()
    if args.interno:
        raise SystemExit(inner(destination, args.caddy, args.python))
    if destination.exists() and any(destination.iterdir()):
        raise SystemExit("O destino precisa ser um diretório novo ou vazio.")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    code = outer(destination, args.caddy, args.python)
    if not args.manter:
        for directory, _, _ in os.walk(destination):
            os.chmod(directory, 0o700)
        shutil.rmtree(destination / "raiz", ignore_errors=True)
        shutil.rmtree(destination / "wireguard", ignore_errors=True)
    raise SystemExit(code)
