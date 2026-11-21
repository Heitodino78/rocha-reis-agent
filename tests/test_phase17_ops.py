"""Fase 17 — empacotamento, instalação, validação, alertas, cópia externa (simulada), worker contínuo,
prontidão, rotação de log e configurações de referência do servidor."""

from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import re
import socket
import stat
import tarfile
from threading import Event, Thread
import time

import pytest
from fastapi.testclient import TestClient

from rocha_reis_agent import config as config_module
from rocha_reis_agent.ops import alerts, deploy, offsite
from rocha_reis_agent.ops.backup import BackupError, KeyMaterial, create_backup, generate_key
from rocha_reis_agent.ops.demo import build_demo, profile_settings
from server_support import encrypted_tmp, installed_root

ROOT = Path(__file__).resolve().parents[1]
_REAL_CONNECT = socket.socket.connect


# ------------------------------------------------------------------ pacote
def test_package_is_reproducible_runtime_only_and_verified(tmp_path):
    first = deploy.build_package(tmp_path / "a")
    second = deploy.build_package(tmp_path / "b")
    assert first.read_bytes() == second.read_bytes()  # reprodutível: mesmo conteúdo → mesmos bytes
    manifest = deploy.verify_package(first, tmp_path / "x")
    names = set(manifest["arquivos"])
    assert "rocha_reis_agent/worker.py" in names and "requirements.lock" in names
    assert not any(n.startswith(("tests/", "tests_e2e/", "docs/")) for n in names)  # só o de execução
    assert not any(re.search(r"\.(sqlite3|rrbackup|key|crt|pem|log)$|credenciais|perfil\.json", n) for n in names)
    from rocha_reis_agent import __version__
    from rocha_reis_agent.persistence.sqlite import SCHEMA_VERSION
    assert manifest["versao"] == __version__ and manifest["esquema_banco"] == SCHEMA_VERSION


def _rewrite(package: Path, target: Path, mutate) -> Path:  # type: ignore[no-untyped-def]
    with tarfile.open(package, "r:gz") as source, tarfile.open(target, "w:gz") as out:
        for member in source.getmembers():
            data = source.extractfile(member).read() if member.isfile() else b""  # type: ignore[union-attr]
            for info, content in mutate(member, data):
                out.addfile(info, io.BytesIO(content) if content is not None else None)
    return target


def test_tampered_or_unsafe_packages_are_refused(tmp_path):
    package = deploy.build_package(tmp_path / "p")

    def changed(member, data):  # type: ignore[no-untyped-def]
        if member.name == "rocha_reis_agent/worker.py":
            data = data + b"\n# alterado\n"
            member.size = len(data)
        yield member, data

    def extra(member, data):  # type: ignore[no-untyped-def]
        yield member, data
        if member.name == "RELEASE.json":
            info = tarfile.TarInfo("rocha_reis_agent/intruso.py")
            info.size = 3
            yield info, b"x=1"

    def traversal(member, data):  # type: ignore[no-untyped-def]
        yield member, data
        if member.name == "RELEASE.json":
            info = tarfile.TarInfo("../../etc/cron.d/x")
            info.size = 1
            yield info, b"x"

    def link(member, data):  # type: ignore[no-untyped-def]
        yield member, data
        if member.name == "RELEASE.json":
            info = tarfile.TarInfo("rocha_reis_agent/link")
            info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
            yield info, None

    for name, mutate, message in (("alterado", changed, "hash diferente"), ("extra", extra, "a mais ou a menos"),
                                  ("travessia", traversal, "insegura"), ("link", link, "insegura")):
        bad = _rewrite(package, tmp_path / f"{name}.tar.gz", mutate)
        with pytest.raises(deploy.DeployError, match=message):
            deploy.verify_package(bad, tmp_path / f"x-{name}")
    assert not (tmp_path / "etc").exists()
    (tmp_path / "lixo.tar.gz").write_bytes(b"isto nao e um pacote")
    with pytest.raises(deploy.DeployError, match="ilegível"):
        deploy.verify_package(tmp_path / "lixo.tar.gz", tmp_path / "x-lixo")


def test_install_layout_is_private_read_only_and_atomic(tmp_path):
    package = deploy.build_package(tmp_path / "p")
    root = tmp_path / "raiz"
    first = deploy.install_release(package, root, create_venv=False)
    again = deploy.install_release(package, root, create_venv=False)
    assert again["reutilizada"] is True and again["id"] == first["id"]
    paths = deploy.layout(root)
    for name in deploy.PRIVATE:
        assert stat.S_IMODE(paths[name].stat().st_mode) == 0o700, name
    code = paths["releases"] / first["id"] / "rocha_reis_agent" / "worker.py"
    assert not code.stat().st_mode & 0o222  # código somente leitura
    assert deploy.activate(root, first["id"]) is None and paths["current"].is_symlink()
    assert (paths["current"] / "RELEASE.json").is_file()
    os.chmod(paths["dados"], 0o755)
    assert any("dados" in p for p in deploy.check_layout(root))
    with pytest.raises(deploy.DeployError, match="não instalada"):
        deploy.activate(root, "0.0.0-inexistente")


def test_installation_validation_refuses_insecure_configuration():
    with encrypted_tmp() as base:
        root = installed_root(base)
        assert deploy.validate_installation(root)["ok"] is True
        profile = deploy.layout(root)["config"] / "perfil.json"
        original = profile.read_text(encoding="utf-8")
        for change, expected in (
            (lambda d: d["settings"].update(panel_mfa_required=False), "perfil.json inválido"),
            (lambda d: d["settings"].update(ai_provider="gemini"), "perfil.json inválido"),
            (lambda d: d["settings"].update(whatsapp_transport="graph"), "perfil.json inválido"),
            (lambda d: d["settings"].update(data_classification="real"), "perfil.json inválido"),
            (lambda d: d.update(mfa_key=str(deploy.layout(root)["dados"] / "mfa.key")), "perfil.json inválido"),
        ):
            data = json.loads(original)
            change(data)
            profile.write_text(json.dumps(data), encoding="utf-8")
            result = deploy.validate_installation(root)
            assert result["ok"] is False and any(expected in p for p in result["problemas"]), result
        profile.write_text(original, encoding="utf-8")
        os.chmod(profile, 0o644)
        assert any("perfil.json: permissão" in p for p in deploy.validate_installation(root)["problemas"])
        os.chmod(profile, 0o600)
        assert set(deploy.validate_installation(root)["travas"].values()) == {False}
        with pytest.raises(deploy.DeployError, match="já existe"):
            deploy.prepare_instance(root)


def test_instance_requires_verified_encryption_and_https(tmp_path):
    with pytest.raises(deploy.DeployError, match="HTTPS"):
        deploy.prepare_instance(tmp_path / "r", panel_origin="http://painel.interno")
    with pytest.raises(deploy.DeployError, match="cifragem"):
        deploy.prepare_instance(tmp_path / "r")  # /tmp é memória: recusado
    assert not (deploy.layout(tmp_path / "r")["config"] / "perfil.json").exists()


def test_services_load_configuration_from_profile_variable(monkeypatch, tmp_path):
    build_demo(tmp_path / "demo")
    monkeypatch.setenv("ROCHA_REIS_PROFILE", str(tmp_path / "demo"))
    settings = config_module.load_settings()
    assert settings.storage_path == profile_settings(tmp_path / "demo").storage_path
    monkeypatch.setenv("ROCHA_REIS_PROFILE", str(tmp_path / "nao-existe"))
    with pytest.raises(config_module.ConfigurationError) as error:
        config_module.load_settings()
    assert "nao-existe" not in str(error.value)


# ------------------------------------------------------------------ prontidão
def test_readiness_checks_database_schema_and_wal(tmp_path):
    from rocha_reis_agent.panel.app import create_panel_app
    build_demo(tmp_path / "demo")
    app = create_panel_app(profile_settings(tmp_path / "demo"))
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    response = client.get("/ready")
    assert response.status_code == 200 and response.json() == {
        "status": "ready", "checks": {"banco": "ok", "esquema": "ok", "wal": "ok", "disco": "ok"}}
    assert response.headers["cache-control"] == "no-store"
    assert str(tmp_path) not in response.text
    from rocha_reis_agent.main import create_app
    memory = TestClient(create_app(config_module.Settings(_env_file=None)))  # type: ignore[call-arg]
    assert memory.get("/ready").status_code == 503 and memory.get("/health").json() == {"status": "ok"}


def test_readiness_probe_only_talks_to_loopback():
    for url in ("http://exemplo.com:8000/ready", "https://127.0.0.1:8000/ready", "http://10.8.0.1:8000/ready",
                "http://127.0.0.1/ready"):
        with pytest.raises(deploy.DeployError, match="127.0.0.1"):
            deploy.probe_http([url], timeout=0.1)


# ------------------------------------------------------------------ worker
def test_worker_notifies_systemd_writes_heartbeat_and_stops_cleanly(tmp_path, monkeypatch):
    from rocha_reis_agent import worker
    build_demo(tmp_path / "demo")
    settings = profile_settings(tmp_path / "demo")
    real_connect = _REAL_CONNECT
    # A trava de rede do conftest vale para tudo; aqui só o socket LOCAL do systemd (AF_UNIX) é liberado.
    monkeypatch.setattr(socket.socket, "connect",
                        lambda self, addr: real_connect(self, addr) if isinstance(addr, str) else pytest.fail("rede"))
    address = str(tmp_path / "notify.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(address)
    server.settimeout(10)
    monkeypatch.setenv("NOTIFY_SOCKET", address)
    stop, result = Event(), {}
    heartbeat = tmp_path / "worker.json"
    thread = Thread(target=lambda: result.update(code=worker.run(settings, stop, idle_seconds=0.05,
                                                                  heartbeat=heartbeat)))
    thread.start()
    messages = [server.recv(256).decode() for _ in range(3)]
    stop.set()
    thread.join(timeout=10)
    server.close()
    assert messages[0].startswith("READY=1") and "WATCHDOG=1" in messages[1:]
    assert result["code"] == 0
    beat = json.loads(heartbeat.read_text())
    assert beat["pid"] == os.getpid() and time.time() - beat["at"] < 10
    assert stat.S_IMODE(heartbeat.stat().st_mode) == 0o600


def test_worker_gives_up_after_persistent_failures(tmp_path, monkeypatch):
    from rocha_reis_agent import worker
    build_demo(tmp_path / "demo")
    settings = profile_settings(tmp_path / "demo")
    monkeypatch.setattr(worker, "MAX_CONSECUTIVE_FAILURES", 3)
    import rocha_reis_agent.channels.service as service
    monkeypatch.setattr(service, "drain", lambda *a, **k: (_ for _ in ()).throw(OSError("disco")))
    assert worker.run(settings, Event(), idle_seconds=0.01) == 1  # sai com erro: o systemd reinicia


def test_worker_send_log_only_exists_for_fake_transport(tmp_path):
    from rocha_reis_agent import worker
    from rocha_reis_agent.config import ConfigurationError
    build_demo(tmp_path / "demo")
    settings = profile_settings(tmp_path / "demo").model_copy(update={"knowledge_source": "file"})
    with pytest.raises(ConfigurationError, match="KNOWLEDGE_SOURCE"):
        worker.build_pipeline(settings)


# ------------------------------------------------------------------ alertas
def test_alerts_are_ok_on_a_fresh_profile_and_critical_without_backup(tmp_path):
    build_demo(tmp_path / "demo")
    settings = profile_settings(tmp_path / "demo")
    result = alerts.evaluate(settings.storage_path, backups=tmp_path / "demo" / "backups")  # type: ignore[arg-type]
    codes = {a["codigo"] for a in result["alertas"]}
    assert "backup_atrasado" not in codes  # a demonstração cria um backup na criação
    empty = alerts.evaluate(settings.storage_path, backups=tmp_path / "vazio",  # type: ignore[arg-type]
                            heartbeat=tmp_path / "sem-batimento.json")
    assert {"backup_atrasado", "worker_sem_batimento"} <= {a["codigo"] for a in empty["alertas"]}
    assert empty["nivel"] == "CRITICO" and alerts.exit_code(empty) == 2
    text = alerts.dumps(empty)
    assert "5511" not in text and "Quero" not in text  # sem telefone nem texto de cliente


def test_alerts_detect_stalled_queues_unknown_sends_and_orphans(tmp_path):
    build_demo(tmp_path / "demo")
    settings = profile_settings(tmp_path / "demo")
    database = settings.storage_path
    later = datetime.now(timezone.utc) + timedelta(hours=2)
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("UPDATE channel_inbox SET status='PENDING' WHERE id=(SELECT min(id) FROM channel_inbox)")
        connection.commit()
    result = alerts.evaluate(database, now=later)  # type: ignore[arg-type]
    codes = {a["codigo"]: a["nivel"] for a in result["alertas"]}
    assert codes["entrada_parada"] == "CRITICO"
    assert codes["envio_incerto"] == "CRITICO"  # a demonstração tem um envio incerto de propósito
    assert codes.get("cliente_esperando") == "ATENCAO"
    assert alerts.evaluate(tmp_path / "nao-existe.sqlite3")["nivel"] == "CRITICO"


def test_alerts_cli_exit_code(tmp_path, capsys):
    from rocha_reis_agent.ops.__main__ import main
    build_demo(tmp_path / "demo")
    code = main(["alertas", "--perfil", str(tmp_path / "demo")])
    output = json.loads(capsys.readouterr().out)
    assert code == alerts.exit_code(output) and output["nivel"] in alerts.LEVELS


# ------------------------------------------------------------------ cópia externa (simulada)
def _backups(tmp_path: Path, count: int) -> tuple[Path, KeyMaterial]:
    build_demo(tmp_path / "demo")
    database = profile_settings(tmp_path / "demo").storage_path
    key = generate_key(tmp_path / "chave" / "backup.key")
    material = KeyMaterial("keyfile", key_file=key)
    folder = tmp_path / "backups"
    for _ in range(count):
        create_backup(database, folder, material)  # type: ignore[arg-type]
        time.sleep(1.05)  # nomes por segundo
    return folder, material


def test_offsite_sync_verifies_uploads_retains_and_never_sends_keys(tmp_path):
    folder, material = _backups(tmp_path, 3)
    store = offsite.FakeExternalStore(tmp_path / "externo")
    state = tmp_path / "estado-externo.json"
    first = offsite.sync(folder, store, state, keep=2)
    assert len(first["enviados"]) == 2 and not first["removidos_por_retencao"] and not first["falhas"]
    assert first["estado"]["simulado"] is True and first["estado"]["falhas_seguidas"] == 0
    assert offsite.sync(folder, store, state, keep=2)["enviados"] == []  # idempotente: sem ciclo de reenvio
    assert all(n.endswith(".rrbackup") for n in store.list())
    assert not list((tmp_path / "externo").glob("*.key"))
    fetched = offsite.fetch(store, store.list()[-1], tmp_path / "baixado")
    from rocha_reis_agent.ops.backup import verify_backup
    assert verify_backup(fetched, material)["schema_version"] >= 11  # decifra e confere contagens
    with pytest.raises(BackupError, match="sobrescrito"):
        offsite.fetch(store, store.list()[-1], tmp_path / "baixado")


@pytest.mark.parametrize("fault", ["upload", "corrupt", "partial"])
def test_offsite_failures_are_detected_and_reported(tmp_path, fault):
    folder, _ = _backups(tmp_path, 1)
    store = offsite.FakeExternalStore(tmp_path / "externo", fault=fault)
    state = tmp_path / "estado.json"
    result = offsite.sync(folder, store, state)
    assert result["falhas"] and not result["enviados"] and store.list() == []  # nada corrompido fica lá
    assert result["estado"]["falhas_seguidas"] == 1 and "ultimo_sucesso" not in result["estado"]
    stale = alerts.evaluate(profile_settings(tmp_path / "demo").storage_path,  # type: ignore[arg-type]
                            offsite_state=state)
    assert "copia_externa_atrasada" in {a["codigo"] for a in stale["alertas"]}
    assert stat.S_IMODE(state.stat().st_mode) == 0o600


# ------------------------------------------------------------------ logs
def test_private_log_rotates_and_every_file_stays_0600(tmp_path, monkeypatch):
    import logging
    from rocha_reis_agent import observability
    monkeypatch.setattr(observability, "MAX_LOG_BYTES", 2000)
    path = tmp_path / "logs" / "canal.log"
    observability.configure_private_log(path)
    try:
        logger = logging.getLogger("rocha_reis_agent")
        for _ in range(300):
            logger.info("channel_inbound_processed", extra={"outcome": "replied"})
    finally:
        observability.remove_private_logs()
    files = sorted(path.parent.glob("canal.log*"))
    assert len(files) >= 3 and len(files) <= observability.LOG_BACKUPS + 1
    assert all(stat.S_IMODE(f.stat().st_mode) == 0o600 for f in files)
    assert all(f.stat().st_size <= 2200 for f in files)


# ------------------------------------------------------------------ configurações de referência
def test_reference_units_are_hardened_and_never_carry_secrets():
    units = {p.name: p.read_text(encoding="utf-8") for p in (ROOT / "deploy" / "systemd").glob("*.service")}
    assert {"rocha-reis-webhook.service", "rocha-reis-worker.service", "rocha-reis-panel.service",
            "rocha-reis-backup.service", "rocha-reis-alertas.service", "rocha-reis-alerta@.service"} <= set(units)
    for name, text in units.items():
        for option in ("NoNewPrivileges=true", "ProtectSystem=strict", "ProtectHome=true", "PrivateTmp=true",
                       "PrivateDevices=true", "CapabilityBoundingSet=", "SystemCallFilter=@system-service",
                       "SystemCallErrorNumber=EPERM", "UMask=0077"):
            assert option in text, (name, option)
        assert "User=rocha-reis" in text and "User=root" not in text
        for line in text.splitlines():
            if line.startswith(("ExecStart", "Environment=")):
                assert not re.search(r"(?i)(secret|token|senha|password|api_key)=", line), (name, line)
    for name in ("rocha-reis-webhook.service", "rocha-reis-worker.service", "rocha-reis-panel.service"):
        text = units[name]
        assert "Environment=ROCHA_REIS_PROFILE=/etc/rocha-reis-agent" in text
        assert "ExecStartPre=" in text and "validar-instalacao" in text and "Restart=on-failure" in text
        assert "OnFailure=rocha-reis-alerta@%n.service" in text and "MemoryMax=" in text
    worker = units["rocha-reis-worker.service"]
    assert "Type=notify" in worker and "WatchdogSec=" in worker
    for name in ("rocha-reis-webhook.service", "rocha-reis-panel.service"):
        assert "--host 127.0.0.1" in units[name] and "--forwarded-allow-ips 127.0.0.1" in units[name]
    assert "--port 8080" in units["rocha-reis-webhook.service"] and "main:create_app" in units["rocha-reis-webhook.service"]


def test_reference_proxy_vpn_and_firewall_keep_admin_off_the_internet():
    caddy = (ROOT / "deploy" / "caddy" / "Caddyfile.exemplo").read_text(encoding="utf-8")
    assert "trusted_proxies" not in caddy.replace("SEM trusted_proxies", "")  # XFF de cliente nunca é confiável
    public = caddy.split("# 2) PAINEL")[0]
    assert "path /webhooks/whatsapp" in public and "method GET POST" in public and "127.0.0.1:8080" in public
    assert "header_up X-Forwarded-For {remote_host}" in caddy
    for header in ("Cookie delete", "X-Hub-Signature-256 delete", "Authorization delete", "delete hub.verify_token"):
        assert header in caddy
    wireguard = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "deploy" / "wireguard").glob("*.exemplo"))
    assert "<CHAVE-PRIVADA-DO-SERVIDOR>" in wireguard and not re.search(r"[A-Za-z0-9+/]{42,43}=", wireguard)
    assert "AllowedIPs = 10.8.0.1/32" in wireguard and "0.0.0.0/0" not in wireguard
    firewall = (ROOT / "deploy" / "nftables" / "rocha-reis.nft.exemplo").read_text(encoding="utf-8")
    assert "policy drop" in firewall and "udp dport 51820" in firewall and 'iifname "wg0"' in firewall
    assert not re.search(r"dport [^\n]*\b(8000|8080|2019)\b", firewall)


# ------------------------------------------------------------------ concorrência (achado da medição de carga)
def test_session_check_survives_a_concurrent_writer_holding_the_lock(tmp_path):
    """Sob carga, toda requisição abria transação de ESCRITA só para conferir a sessão e virava 500 quando o
    worker segurava o bloqueio. Agora a conferência é leitura e a renovação da ociosidade é melhor-esforço."""
    import sqlite3
    from contextlib import closing
    from rocha_reis_agent.panel.app import create_panel_app
    build_demo(tmp_path / "demo")
    settings = profile_settings(tmp_path / "demo").model_copy(update={"panel_embedded_worker": False})
    app = create_panel_app(settings)
    store = app.state.panel_store
    user = store.user_by_login("ana.demo")[0]
    token, _ = store.create_session(user.id, absolute_hours=12)
    with closing(sqlite3.connect(settings.storage_path, timeout=0.1)) as connection:
        connection.execute("UPDATE panel_sessions SET last_seen_at=? WHERE user_id=?",
                           ((datetime.now(timezone.utc) - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                            user.id))
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")  # outro processo segurando a escrita (ex.: worker)
        started = time.monotonic()
        session = store.session(token, idle_minutes=30)  # precisaria renovar (2 min parado), mas o banco está ocupado
        assert session is not None and session.user.login == "ana.demo"
        assert time.monotonic() - started < 3
        connection.rollback()
    store.session(token, idle_minutes=30)  # livre: renova
    with closing(sqlite3.connect(settings.storage_path)) as connection:
        seen = connection.execute("SELECT last_seen_at FROM panel_sessions WHERE user_id=?", (user.id,)).fetchone()[0]
    assert datetime.fromisoformat(seen.replace("Z", "+00:00")) > datetime.now(timezone.utc) - timedelta(seconds=30)
    store.revoke_session(session.token_hash, actor_id=user.id)
    assert store.session(token, idle_minutes=30) is None  # revogação continua imediata


def test_scheduled_backup_works_with_read_only_profile_directory():
    """No servidor o perfil (/etc/rocha-reis-agent) é SOMENTE LEITURA para os serviços (ReadOnlyPaths). Antes
    o backup automático gravava o estado dentro dele e falharia lá."""
    from rocha_reis_agent.ops import backup_job
    with encrypted_tmp() as base:
        root = installed_root(base)
        paths = deploy.layout(root)
        os.chmod(paths["config"], 0o500)  # como ReadOnlyPaths= no systemd
        try:
            status = backup_job.run_backup_job(paths["config"])
        finally:
            os.chmod(paths["config"], 0o700)
        assert status["falhas_consecutivas"] == 0 and status["copias_primario"] == 1
        assert (paths["estado"] / "estado-backup.json").is_file()
        assert not (paths["config"] / "estado-backup.json").exists()
        assert backup_job.load_status(paths["config"])["ultimo_backup"] == status["ultimo_backup"]
        assert backup_job.backup_health(paths["config"])["saudavel"] is True


def test_release_identification_moved_with_phase_17():
    from rocha_reis_agent import RELEASE_NAME, __version__
    # O número exato acompanha a fase mais recente (ver tests/test_phase18_infra.py).
    assert RELEASE_NAME == "Rocha Reis Agent — RC1 Offline" and __version__.startswith("0.14.0rc1.post")
