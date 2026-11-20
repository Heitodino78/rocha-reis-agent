"""Fase 13 — HTTPS local real (certificado de desenvolvimento), confiança em proxies, configurações
de referência, backup automático, teste de restauração, ensaio de desastre, rotação de chave e
endurecimento da pré-produção."""

from contextlib import closing
import json
import logging
import os
from pathlib import Path
import re
import socket
import sqlite3
import ssl
import threading
import time

import httpx2
import pytest
import uvicorn

from panel_support import ORIGIN, PASSWORD, add_user, create_panel_app, panel_settings
from rocha_reis_agent.ops import backup as bk
from rocha_reis_agent.ops import backup_job, environments
from rocha_reis_agent.ops.backup import BackupError
from rocha_reis_agent.ops.demo import build_demo, profile_settings
from rocha_reis_agent.ops.launcher import server_config
from rocha_reis_agent.ops.tls import create_dev_certificate
from rocha_reis_agent.persistence import storage_security

ROOT = Path(__file__).resolve().parents[1]
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
_REAL = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Substitui a trava geral SÓ neste módulo: permite 127.0.0.1 (servidor local de teste) e
    continua falhando qualquer outro destino."""
    def check(host: object) -> None:
        if str(host) not in LOOPBACK:
            pytest.fail("Rede proibida nos testes locais (somente 127.0.0.1).")

    monkeypatch.setattr(socket.socket, "connect", lambda self, addr: (check(addr[0]), _REAL[0](self, addr))[1])
    monkeypatch.setattr(socket.socket, "connect_ex", lambda self, addr: (check(addr[0]), _REAL[1](self, addr))[1])
    monkeypatch.setattr(socket, "create_connection",
                        lambda addr, *a, **k: (check(addr[0]), _REAL[2](addr, *a, **k))[1])
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k: (check(host), _REAL[3](host, *a, **k))[1])


def test_loopback_guard_still_blocks_external_hosts():
    with pytest.raises(pytest.fail.Exception):
        socket.getaddrinfo("example.com", 443)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Running:
    """uvicorn real numa thread (como o iniciador), para TLS e cabeçalhos encaminhados."""

    def __init__(self, config: uvicorn.Config) -> None:
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> "Running":
        self.thread.start()
        deadline = time.monotonic() + 15
        while not self.server.started:
            assert time.monotonic() < deadline, "servidor não iniciou"
            time.sleep(0.05)
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


# ------------------------------------------------------------ HTTPS (itens 23, 25, 28)
def test_real_tls_secure_cookie_hsts_and_sse_over_https(tmp_path):
    port = free_port()
    cert, key = create_dev_certificate(tmp_path.parent / f"{tmp_path.name}-tls")
    assert (key.stat().st_mode & 0o777) == 0o600
    origin = f"https://127.0.0.1:{port}"
    settings = panel_settings(tmp_path, panel_origin=origin, panel_cookie_secure=True, app_env="local")
    app = create_panel_app(settings)
    add_user(app, "ana.teste")
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False,
                            ssl_certfile=str(cert), ssl_keyfile=str(key), proxy_headers=False)
    with Running(config):
        # Sem confiar no certificado de desenvolvimento, a conexão é recusada (não é certificado público).
        with pytest.raises(httpx2.ConnectError):
            httpx2.get(origin + "/health")
        context = ssl.create_default_context(cafile=str(cert))
        with httpx2.Client(base_url=origin, verify=context) as client:
            login = client.post("/api/panel/auth/login", json={"login": "ana.teste", "password": PASSWORD},
                                headers={"Origin": origin})
            assert login.status_code == 200
            cookie = login.headers["set-cookie"]
            assert cookie.startswith("__Host-rr_panel=") and "Secure" in cookie and "HttpOnly" in cookie
            assert "samesite=strict" in cookie.lower() and "Path=/" in cookie and "Domain" not in cookie
            assert login.headers["strict-transport-security"] == "max-age=31536000"
            with client.stream("GET", "/api/panel/events") as stream:
                first = next(stream.iter_lines())
                assert first.startswith("retry:")


def test_forwarded_headers_are_ignored_unless_proxy_is_trusted(tmp_path):
    def login_storm(trusted: str) -> list[int]:
        port = free_port()
        base = tmp_path / f"p{port}"
        base.mkdir()
        app = create_panel_app(panel_settings(base, panel_origin=f"http://127.0.0.1:{port}", app_env="local",
                                              panel_trusted_proxies=trusted))
        statuses = []
        with Running(server_config(app, app.state.settings, port)):
            with httpx2.Client(base_url=f"http://127.0.0.1:{port}") as client:
                for n in range(7):  # logins e IPs "diferentes" a cada tentativa
                    statuses.append(client.post("/api/panel/auth/login", json={"login": f"x{n}.teste", "password": "errada-1234567"},
                                                headers={"Origin": f"http://127.0.0.1:{port}",
                                                         "X-Forwarded-For": f"203.0.113.{n}"}).status_code)
        return statuses
    # Sem proxy confiável o IP real (127.0.0.1) é usado: o limitador por IP pega o ataque.
    assert login_storm("")[-1] == 429
    # Só quando o proxy é declarado confiável o cabeçalho é aceito (configuração explícita).
    assert 429 not in login_storm("127.0.0.1")


def test_reference_deploy_configs_are_safe():
    caddy = (ROOT / "deploy" / "caddy" / "Caddyfile.exemplo").read_text(encoding="utf-8")
    public = caddy.split("# 2) PAINEL")[0]
    assert "path /webhooks/whatsapp" in public and 'respond "Not Found" 404' in public
    assert "/panel" not in public and "/api/panel" not in public and "8000" not in public  # painel fora do público
    panel = caddy.split("# 2) PAINEL")[1]
    assert "bind <IP-DA-VPN>" in panel and "flush_interval -1" in panel
    assert "Strict-Transport-Security" in caddy and "0.0.0.0/0" not in caddy
    for unit in (ROOT / "deploy" / "systemd").glob("*.service"):
        text = unit.read_text(encoding="utf-8")
        exec_line = next(line for line in text.splitlines() if line.startswith("ExecStart="))
        assert not re.search(r"(?i)(secret|token|key|senha|password)=", exec_line), unit.name
        assert "UMask=0077" in text and "NoNewPrivileges=true" in text
    panel_unit = (ROOT / "deploy" / "systemd" / "rocha-reis-panel.service").read_text(encoding="utf-8")
    assert "--host 127.0.0.1" in panel_unit and "--forwarded-allow-ips 127.0.0.1" in panel_unit
    timer = (ROOT / "deploy" / "systemd" / "rocha-reis-backup.timer").read_text(encoding="utf-8")
    assert "Persistent=true" in timer and "OnCalendar=" in timer


# ------------------------------------------------------------ backup automático (itens 29–40)
@pytest.fixture
def profile(tmp_path):
    path = tmp_path / "demo"
    build_demo(path)
    return path


def test_scheduled_backup_creates_verifies_copies_and_reports(profile):
    status = backup_job.run_backup_job(profile)
    assert status["falhas_consecutivas"] == 0 and status["destino_secundario"]["situacao"] == "ok"
    assert status["copias_secundario"] == 1 and status["copias_primario"] == 2  # + o da criação da demo
    copy = profile / "backups-secundario" / status["ultimo_backup"]
    assert copy.read_bytes() == (profile / "backups" / status["ultimo_backup"]).read_bytes()
    assert copy.stat().st_mode & 0o777 == 0o600
    assert not list((profile / "backups-secundario").glob("*.partial"))
    saved = json.loads((profile / backup_job.STATUS_FILE).read_text())
    assert (profile / backup_job.STATUS_FILE).stat().st_mode & 0o777 == 0o600
    text = json.dumps(saved)
    for secret in ((profile / "chaves" / "backup.key").read_text().strip(), str(profile)):
        assert secret not in text  # nem chave nem caminhos no estado
    again = backup_job.run_backup_job(profile)  # execução repetida: nova cópia distinta, sem conflito
    assert again["copias_primario"] == 3 and again["copias_secundario"] == 2
    health = backup_job.backup_health(profile)
    assert health["saudavel"] is True and health["idade_horas"] < 1


def test_secondary_unavailable_or_write_failure_is_reported_and_primary_kept(profile):
    os.rename(profile / "backups-secundario", profile / "fora-do-ar")
    with pytest.raises(BackupError, match="cópia secundária falhou"):
        backup_job.run_backup_job(profile)
    status = backup_job.load_status(profile)
    assert status["falhas_consecutivas"] == 1 and status["destino_secundario"]["situacao"] == "falhou"
    assert "indisponível" in status["destino_secundario"]["erro"]
    assert len([b for b in bk.list_backups(profile / "backups") if b["ok"]]) == 2  # primário preservado
    os.rename(profile / "fora-do-ar", profile / "backups-secundario")

    def boom(stage):
        if stage == "during_copy":
            raise OSError("disco cheio simulado")
    with pytest.raises(BackupError):
        backup_job.run_backup_job(profile, fault=boom)
    assert not list((profile / "backups-secundario").glob("*.partial"))  # cópia parcial removida
    assert backup_job.load_status(profile)["falhas_consecutivas"] == 2
    (profile / "backups-secundario" / "sobra.rrbackup.partial").write_bytes(b"x")  # interrupção antiga
    status = backup_job.run_backup_job(profile)  # recupera após interrupção
    assert status["falhas_consecutivas"] == 0 and backup_job.backup_health(profile)["saudavel"]


def test_missing_wrong_or_loose_key_and_insecure_dirs_fail_before_copying(profile):
    key = profile / "chaves" / "backup.key"
    before = sorted(p.name for p in (profile / "backups").iterdir())
    os.chmod(key, 0o644)
    with pytest.raises(BackupError, match="privado"):
        backup_job.run_backup_job(profile)
    os.chmod(key, 0o600)
    os.rename(key, key.with_suffix(".sumida"))
    with pytest.raises(BackupError, match="não encontrado"):
        backup_job.run_backup_job(profile)
    os.rename(key.with_suffix(".sumida"), key)
    os.chmod(profile / "backups", 0o755)
    with pytest.raises(BackupError, match="0700"):
        backup_job.run_backup_job(profile)
    os.chmod(profile / "backups", 0o700)
    assert {p.name for p in (profile / "backups").iterdir()} - {".backup.lock"} == set(before) - {".backup.lock"}
    assert backup_job.load_status(profile)["falhas_consecutivas"] == 3
    # Chave trocada: os backups antigos não abrem (teste de restauração falha e é registrado).
    backup_job.run_backup_job(profile)
    os.unlink(key)
    bk.generate_key(key)
    with pytest.raises(BackupError, match="Chave incorreta"):
        backup_job.restore_test(profile)
    assert backup_job.load_status(profile)["ultimo_teste_restauracao"]["resultado"] == "falhou"


def test_concurrent_runs_are_refused(profile):
    import fcntl
    descriptor = os.open(profile / "backups" / ".backup.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    try:
        with pytest.raises(BackupError, match="em andamento"):
            backup_job.run_backup_job(profile)
    finally:
        os.close(descriptor)


def test_retention_keeps_last_valid_and_ignores_corrupt_files(profile):
    data = json.loads((profile / "perfil.json").read_text())
    data["backup"] = {"manter": 2, "dias": 1}
    (profile / "perfil.json").write_text(json.dumps(data))
    os.chmod(profile / "perfil.json", 0o600)
    (profile / "backups" / "corrompido.rrbackup").write_bytes(b"lixo")
    for _ in range(4):
        status = backup_job.run_backup_job(profile)
    # Tudo é recente (< 1 dia): a política por dias mantém; nada válido some além do permitido.
    valid = [b for b in bk.list_backups(profile / "backups") if b["ok"]]
    assert len(valid) >= 2 and (profile / "backups" / "corrompido.rrbackup").exists()
    plan = bk.retention_plan(profile / "backups", keep_last=1)
    assert len(plan["manter"]) == 1  # mesmo agressiva, a política nunca remove o último válido
    assert status["falhas_consecutivas"] == 0


def test_restore_test_is_isolated_and_leaves_no_plaintext_copy(profile):
    backup_job.run_backup_job(profile)
    result = backup_job.restore_test(profile)
    assert result["resultado"] == "ok" and result["contagens"]["panel_users"] == 3
    assert not list(profile.glob(".teste-restauracao-*"))
    assert backup_job.backup_health(profile)["ultimo_teste_restauracao"]["resultado"] == "ok"


# ------------------------------------------------------------ ensaio de desastre (item 9)
def test_disaster_drill_rebuilds_from_secondary_copy_without_touching_source(profile, tmp_path):
    from rocha_reis_agent.ops.disaster import drill
    backup_job.run_backup_job(profile)
    snapshot = {p.relative_to(profile): p.stat().st_mtime_ns for p in profile.rglob("*") if p.is_file()}
    report = drill(profile, tmp_path / "ensaio")
    assert report["resultado"] == "recuperado" and report["integridade"] == "ok" and report["contagens_conferem"]
    assert report["fila_saida"].get("UNKNOWN") == 1 and report["fila_saida"].get("FAILED") == 1
    assert report["funcionarios_ativos"] == 3 and report["segredos_2_etapas_legiveis"] == 0
    assert any("banco íntegro" in check for check in report["verificacoes"])
    after = {p.relative_to(profile): p.stat().st_mtime_ns for p in profile.rglob("*") if p.is_file()}
    assert after == snapshot  # origem intocada
    with pytest.raises(BackupError, match="vazio"):
        drill(profile, tmp_path / "ensaio")
    with pytest.raises(BackupError, match="dentro do perfil"):
        drill(profile, profile / "sub")


def test_drill_without_secondary_backup_reports_impossible_recovery(profile, tmp_path):
    from rocha_reis_agent.ops.disaster import drill
    with pytest.raises(BackupError, match="recuperação impossível"):
        drill(profile, tmp_path / "ensaio2")


# ------------------------------------------------------------ chaves do segundo fator
def test_mfa_key_rotation_command_keeps_old_key_aside(profile):
    from rocha_reis_agent.ops.__main__ import rotate_profile_mfa_key
    from rocha_reis_agent.panel import mfa
    settings = profile_settings(profile)
    app = create_panel_app(settings)
    store, cipher = app.state.panel_store, app.state.mfa_cipher
    admin_id = store.user_by_login("admin.demo")[0].id
    secret = mfa.new_secret()
    store.mfa_begin(admin_id, cipher.encrypt(admin_id, secret), cipher.key_id)
    store.mfa_activate(admin_id, 1, [])
    result = rotate_profile_mfa_key(profile)
    assert result["segredos_recifrados"] == 1
    rotated = profile_settings(profile)
    new_cipher = mfa.MfaCipher.from_file(rotated.panel_mfa_key_file)
    row = store.mfa_row(admin_id)
    assert new_cipher.decrypt(admin_id, row["secret_blob"], row["key_id"]) == secret
    assert (profile / "chaves" / result["chave_antiga_guardada"]).stat().st_mode & 0o777 == 0o600


# ------------------------------------------------------------ pré-produção endurecida (itens 41–43)
def encrypted(monkeypatch, value=True):
    result = storage_security.VolumeProtection(value, True, "dm-crypt LUKS2" if value else "sem dm-crypt")
    monkeypatch.setattr(storage_security, "volume_protection", lambda path, **_: result)
    monkeypatch.setattr(environments, "volume_protection", lambda path, **_: result)


def test_preproduction_profile_has_https_and_mandatory_mfa(tmp_path, monkeypatch):
    encrypted(monkeypatch)
    profile = tmp_path / "preprod"
    environments.build_preproduction(profile)
    settings = profile_settings(profile, port=8443)
    assert settings.panel_origin == "https://127.0.0.1:8443" and settings.panel_cookie_secure
    assert settings.panel_mfa_required and settings.panel_mfa_key_file.parent.name == "chaves"
    report = environments.diagnose(profile)
    security = report["seguranca"]
    assert security["segundo_fator"] == {"obrigatorio": True, "chave_configurada": True, "chave_privada": True}
    assert security["https"]["origem"] == "https" and security["https"]["dias_restantes"] >= 29
    assert (profile / "tls" / "dev.key").stat().st_mode & 0o777 == 0o600
    assert {r["requisito"]: r["situacao"] for r in report["dados_reais"]["requisitos"]}[
        "Segundo fator obrigatório no painel (implementado na Fase 13)"] == "ok"
    assert report["dados_reais"]["situacao"] == "BLOQUEADOS"


@pytest.mark.parametrize("changes", [{"panel_mfa_required": False}, {"panel_cookie_secure": False},
                                     {"panel_origin": "http://127.0.0.1:8000"}, {"panel_session_idle_minutes": 60}])
def test_preproduction_panel_refuses_weak_settings(tmp_path, monkeypatch, changes):
    encrypted(monkeypatch)
    profile = tmp_path / "preprod"
    environments.build_preproduction(profile)
    data = json.loads((profile / "perfil.json").read_text())
    values = {**data["settings"], "storage_path": profile / "dados" / data["database"],
              "environment_profile": "preproduction", "panel_origin": "https://127.0.0.1:8443",
              "panel_mfa_key_file": profile / "chaves" / "mfa.key", **changes}
    from rocha_reis_agent.config import Settings
    with pytest.raises(ValueError, match="segundo fator obrigatório, HTTPS|Cookie sem Secure"):
        Settings(_env_file=None, **values)


def test_backup_job_refuses_unverified_volume_in_preproduction(tmp_path, monkeypatch):
    encrypted(monkeypatch)
    profile = tmp_path / "preprod"
    environments.build_preproduction(profile)
    assert backup_job.run_backup_job(profile)["falhas_consecutivas"] == 0
    encrypted(monkeypatch, False)
    with pytest.raises(BackupError, match="cifragem verificada"):
        backup_job.run_backup_job(profile)


def test_ops_logs_carry_no_secrets(profile, caplog):
    caplog.set_level(logging.DEBUG)
    backup_job.run_backup_job(profile)
    backup_job.restore_test(profile)
    for key in (profile / "chaves").glob("*.key"):
        assert key.read_text().strip() not in caplog.text
    passwords = [line.split("\t")[2] for line in (profile / "credenciais-demo.txt").read_text().splitlines()[1:]]
    assert all(p not in caplog.text for p in passwords)
