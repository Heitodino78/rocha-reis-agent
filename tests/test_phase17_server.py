"""Fase 17 — servidor SIMULADO (raiz descartável em volume cifrado): serviços a partir da versão
instalada, isolamento webhook × painel, worker 24/7 com morte abrupta, atualização e reversão."""

from contextlib import closing
import json
from pathlib import Path
import signal
import sqlite3
import time
import urllib.error
import urllib.request

import pytest

from rocha_reis_agent.channels.meta import sign
from rocha_reis_agent.ops import deploy
from server_support import (
    PYTHON, Services, encrypted_tmp, installed_root, package_from, source_copy, tune_profile, wait_until,
)
from whatsapp_support import envelope, text_message

PHONE, WABA = "100000000000009", "200000000000009"
pytestmark = pytest.mark.filterwarnings("ignore::ResourceWarning")
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
import socket  # noqa: E402
_REAL = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Como em test_phase13_ops: só 127.0.0.1 (serviços locais do teste); qualquer outro destino falha."""
    def check(addr: object) -> None:
        host = addr if isinstance(addr, str) else addr[0]  # type: ignore[index]
        if str(host) not in LOOPBACK:
            pytest.fail("Rede proibida nos testes locais (somente 127.0.0.1).")

    monkeypatch.setattr(socket.socket, "connect", lambda self, addr: (check(addr), _REAL[0](self, addr))[1])
    monkeypatch.setattr(socket.socket, "connect_ex", lambda self, addr: (check(addr), _REAL[1](self, addr))[1])
    monkeypatch.setattr(socket, "create_connection",
                        lambda addr, *a, **k: (check(addr), _REAL[2](addr, *a, **k))[1])
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k: (check(host), _REAL[3](host, *a, **k))[1])


@pytest.fixture
def server():
    with encrypted_tmp() as base:
        root = installed_root(base)
        # Leases curtos para observar a recuperação sem esperar 30 s (o valor de produção segue o padrão).
        tune_profile(root, channel_outbox_lease_seconds=2.0, channel_inbox_lease_seconds=2.0)
        services = Services(root)
        try:
            yield base, root, services
        finally:
            services.stop(timeout=10)


def database(root: Path) -> Path:
    data = json.loads((deploy.layout(root)["config"] / "perfil.json").read_text(encoding="utf-8"))
    return Path(data["database"])


def query(root: Path, sql: str, *params: object) -> list[tuple]:
    with closing(sqlite3.connect(database(root).as_uri() + "?mode=ro", uri=True)) as connection:
        return connection.execute(sql, params).fetchall()


def post_webhook(root: Path, services: Services, wamid: str, text: str, sender: str = "5511900000077") -> int:
    secret = json.loads((deploy.layout(root)["config"] / "perfil.json").read_text())["settings"]["whatsapp_app_secret"]
    raw = json.dumps(envelope(text_message(wamid, text, sender), number=PHONE, waba=WABA)).encode()
    request = urllib.request.Request(f"http://127.0.0.1:{services.ports['webhook']}/webhooks/whatsapp", data=raw,
                                     headers={"Content-Type": "application/json",
                                              "X-Hub-Signature-256": sign(secret.encode(), raw)})
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status


def status_of(url: str, *, method: str = "GET", host: str | None = None) -> int:
    request = urllib.request.Request(url, method=method, headers={"Host": host} if host else {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def test_services_run_from_installed_release_and_answer_end_to_end(server):
    base, root, services = server
    services.start()
    assert services.ready(), "serviços não ficaram prontos"
    assert post_webhook(root, services, "wamid.f17.1", "Quanto custa o corte?") == 200
    assert wait_until(lambda: query(root, "SELECT count(*) FROM channel_outbox WHERE status='ACCEPTED'")[0][0] == 1)
    assert query(root, "SELECT status FROM channel_inbox WHERE kind='message'") == [("DONE",)]
    beat = json.loads(services.heartbeat.read_text())
    assert time.time() - beat["at"] < 10 and beat["processed"] >= 2
    # Isolamento: o aplicativo do webhook (o único publicado) não tem rotas do painel, por nenhum caminho.
    webhook = f"http://127.0.0.1:{services.ports['webhook']}"
    for path in ("/panel/", "/api/panel/auth/session", "/api/panel/auth/login", "/webhooks/whatsapp/../../api/panel/users",
                 "/%2e%2e/api/panel/overview", "//api/panel/overview", "/API/PANEL/overview", "/docs", "/openapi.json"):
        assert status_of(webhook + path) in (404, 405), path
    assert status_of(webhook + "/api/panel/auth/login", method="POST") in (404, 405)
    # O painel não aceita host forjado (DNS rebinding) nem tem a rota do webhook.
    panel = f"http://127.0.0.1:{services.ports['panel']}"
    assert status_of(panel + "/ready", host="evil.example") == 400
    assert status_of(panel + "/webhooks/whatsapp", host="painel.interno") == 404
    codes = services.stop()
    # Encerramento controlado: o uvicorn termina as conexões e sai repassando o SIGTERM (-15), o que o systemd
    # trata como saída limpa; o worker sai com 0. None seria processo morto à força por não encerrar.
    assert codes["worker"] == 0 and codes["webhook"] in (0, -signal.SIGTERM) and codes["panel"] in (0, -signal.SIGTERM)


def test_worker_killed_mid_flight_restarts_without_duplicate_or_blind_resend(server):
    base, root, services = server
    services.start(("webhook",))
    assert services.ready(timeout=30) or True
    for index in range(25):
        post_webhook(root, services, f"wamid.f17.k{index}", f"Quanto custa o corte? ({index})", f"55119000001{index:02d}")
    services.start(("worker",))
    assert wait_until(lambda: services.send_log.exists() and len(services.send_log.read_text().splitlines()) >= 3, 30)
    services.kill("worker")  # kill -9 no meio do trabalho
    killed_at = len(services.send_log.read_text().splitlines())
    services.start(("worker",))
    assert wait_until(lambda: query(root, "SELECT count(*) FROM channel_inbox WHERE kind='message' AND status='DONE'")
                      [0][0] == 25, 60)
    assert wait_until(lambda: query(root, "SELECT count(*) FROM channel_outbox WHERE status IN "
                                          "('PENDING','RETRY','SENDING')")[0][0] == 0, 30)
    sends = [json.loads(line)["operation"] for line in services.send_log.read_text().splitlines()]
    assert len(sends) == len(set(sends))  # nada enviado duas vezes, mesmo com o processo morto no meio
    statuses = dict(query(root, "SELECT status, count(*) FROM channel_outbox GROUP BY status"))
    assert sum(statuses.values()) == 25 and set(statuses) <= {"ACCEPTED", "UNKNOWN"}
    # Incerto (morto entre enviar e registrar) fica UNKNOWN para decisão humana — nunca reenviado.
    for (operation,) in query(root, "SELECT id FROM channel_outbox WHERE status='UNKNOWN'"):
        assert sends.count(operation) <= 1
    assert killed_at >= 3


def test_worker_refuses_to_start_with_inconsistent_configuration(server):
    base, root, services = server
    tune_profile(root, knowledge_source="file")  # pré-produção exige conhecimento no banco
    release = deploy.layout(root)["current"].resolve()
    import subprocess
    completed = subprocess.run([PYTHON, "-m", "rocha_reis_agent.worker"], cwd=release, env=services._env(release),
                               capture_output=True, text=True, timeout=60)
    assert completed.returncode == 2 and "Configuração inválida" in completed.stderr
    assert "whatsapp_app_secret" not in completed.stderr.lower()  # sem valores na mensagem


def _update(base, root, services, package, *, confirm=None):  # type: ignore[no-untyped-def]
    manifest = deploy.verify_package(package, base / f"conferido-{package.stem}")
    return deploy.update(package, root, database=database(root),
                         key_file=deploy.layout(root)["chaves"] / "backup.key",
                         confirmation=confirm or f"ATUALIZAR:{manifest['versao']}",
                         stop=lambda: services.stop(), start=lambda: services.start(), ready=lambda: services.ready(20),
                         python=PYTHON, create_venv=False)


def _version(base: Path, label: str, **kwargs) -> Path:  # type: ignore[no-untyped-def]
    edits = {"rocha_reis_agent/__init__.py": ('__version__ = "', f'__version__ = "{label}.')}
    edits.update(kwargs.pop("edits", {}))
    source = source_copy(base / f"fonte-{label}", edits=edits, extra=kwargs.get("extra"))
    return package_from(source, base / f"pacote-{label}", tuple((kwargs.get("extra") or {}).keys()))


def test_update_succeeds_keeps_data_and_records_history(server):
    base, root, services = server
    services.start()
    assert services.ready()
    post_webhook(root, services, "wamid.f17.u1", "Quero falar com uma pessoa")
    assert wait_until(lambda: query(root, "SELECT count(*) FROM conversations")[0][0] == 1)
    before = deploy.current_release(root)
    package = _version(base, "9")
    report = _update(base, root, services, package)
    assert report["resultado"] == "ATUALIZADA", report
    assert report["etapas"][:3] == ["pacote_verificado", "compatibilidade_conferida", "versao_preparada"]
    assert "backup_verificado" in report["etapas"] and "migracao_ensaiada_em_copia" in report["etapas"]
    assert deploy.current_release(root) == report["id"] != before
    assert query(root, "SELECT count(*) FROM conversations")[0][0] == 1  # dados preservados
    history = (deploy.layout(root)["estado"] / "implantacoes.jsonl").read_text().splitlines()
    assert json.loads(history[-1])["resultado"] == "ATUALIZADA"
    # Reversão só do código para a anterior (mesmo esquema): permitida.
    services.stop()
    assert deploy.rollback_code(root)["ativa"] == before
    services.start()
    assert services.ready()


def test_update_refuses_wrong_confirmation_and_tampered_package(server):
    base, root, services = server
    package = _version(base, "8")
    with pytest.raises(deploy.DeployError, match="Confirmação incorreta"):
        _update(base, root, services, package, confirm="ATUALIZAR:outra")
    tampered = base / "adulterado.tar.gz"
    data = bytearray(package.read_bytes())
    data[len(data) // 2] ^= 0xFF
    tampered.write_bytes(bytes(data))
    with pytest.raises(deploy.DeployError):
        _update(base, root, services, tampered, confirm="ATUALIZAR:8.0.14.0rc1.post3")


def test_failed_start_rolls_back_code_automatically(server):
    base, root, services = server
    services.start()
    assert services.ready()
    before = deploy.current_release(root)
    broken = _version(base, "7", edits={"rocha_reis_agent/api/health.py": (
        'router = APIRouter()', 'raise RuntimeError("versão quebrada de teste")\nrouter = APIRouter()')})
    report = _update(base, root, services, broken)
    logs = {p.name: p.read_text(errors="replace")[-1500:] for p in root.glob("servico-*.log")}
    assert report["resultado"] == "REVERTIDA", (report, logs)
    assert deploy.current_release(root) == before and services.ready()
    assert report["esquema_depois"] == report["esquema_antes"]


def test_bad_migration_is_caught_on_a_copy_before_touching_the_live_database(server):
    base, root, services = server
    services.start()
    assert services.ready()
    before = deploy.current_release(root)
    bad = _version(base, "6", edits={"rocha_reis_agent/persistence/sqlite.py": ("SCHEMA_VERSION = 11",
                                                                                 "SCHEMA_VERSION = 12")},
                   extra={"rocha_reis_agent/persistence/migrations/012_teste_quebrada.sql": "CREATE TABLE (;\n"})
    report = _update(base, root, services, bad)
    assert report["resultado"] == "CANCELADA_ANTES_DA_TROCA" and report["servico_anterior_pronto"] is True, report
    assert deploy.current_release(root) == before
    assert query(root, "PRAGMA user_version") == [(11,)]  # banco em uso intocado


def test_migrated_database_requires_confirmed_restore_and_old_code_never_opens_it(server):
    base, root, services = server
    services.start()
    assert services.ready()
    post_webhook(root, services, "wamid.f17.m1", "Oi")
    assert wait_until(lambda: query(root, "SELECT count(*) FROM conversations")[0][0] == 1)
    before = deploy.current_release(root)
    # Migração válida, mas o webhook novo não sobe DEPOIS de migrar o banco.
    migrated = _version(base, "5", edits={
        "rocha_reis_agent/persistence/sqlite.py": ("SCHEMA_VERSION = 11", "SCHEMA_VERSION = 12"),
        "rocha_reis_agent/main.py": ("    app.middleware(\"http\")(handle_unexpected_errors)",
                                     "    raise RuntimeError(\"falha depois de migrar\")")},
        extra={"rocha_reis_agent/persistence/migrations/012_teste.sql":
               "CREATE TABLE f17_teste (id INTEGER PRIMARY KEY) STRICT;\n"})
    report = _update(base, root, services, migrated)
    assert report["resultado"] == "REVERTIDA_AGUARDANDO_RESTAURACAO_DO_BANCO", report
    assert report["esquema_antes"] == 11 and report["esquema_depois"] == 12
    assert deploy.current_release(root) == before
    services.stop()
    # O código anterior recusa o esquema novo: não sobe (sem corromper nada).
    services.start(("webhook",))
    assert not services.ready(timeout=8)
    services.stop()
    with pytest.raises(deploy.DeployError, match="Confirmação incorreta"):
        deploy.restore_database(root, database=database(root), key_file=deploy.layout(root)["chaves"] / "backup.key",
                                backup_name=report["backup"], confirmation="sim")
    restored = deploy.restore_database(root, database=database(root),
                                       key_file=deploy.layout(root)["chaves"] / "backup.key",
                                       backup_name=report["backup"], confirmation=f"RESTAURAR:{report['backup']}")
    assert restored["esquema"] == 11
    assert query(root, "SELECT count(*) FROM conversations")[0][0] == 1  # estado de antes da atualização
    services.start()
    assert services.ready()


def test_update_refuses_package_with_older_database_schema(server):
    base, root, services = server
    older = _version(base, "4", edits={"rocha_reis_agent/persistence/sqlite.py": ("SCHEMA_VERSION = 11",
                                                                                   "SCHEMA_VERSION = 10")})
    with pytest.raises(deploy.DeployError, match="MAIS ANTIGO"):
        _update(base, root, services, older)


def test_operational_drill_crash_restart_and_isolated_restore():
    from rocha_reis_agent.ops.opsdrill import run_drill
    with encrypted_tmp() as base:
        report = run_drill(base / "ensaio", python=PYTHON)
        assert report["resultado"] == "OK", report
        steps = {s["etapa"]: s for s in report["etapas"]}
        assert list(steps) == ["instalacao", "inicializacao", "mensagens", "atendimento_humano", "backup",
                               "queda_e_reinicio", "restauracao_isolada"]
        assert steps["queda_e_reinicio"]["envios_duplicados"] == 0
        assert steps["restauracao_isolada"]["segundo_fator_ok"] is True
        assert steps["restauracao_isolada"]["conversas_restauradas"] == steps["restauracao_isolada"]["conversas_no_backup"]
        assert oct((base / "ensaio" / "relatorio-ensaio.json").stat().st_mode & 0o777) == "0o600"
        with pytest.raises(Exception, match="vazio"):
            run_drill(base / "ensaio", python=PYTHON)  # nunca reaproveita um destino com conteúdo
