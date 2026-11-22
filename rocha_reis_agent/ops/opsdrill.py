"""Ensaio operacional completo (Fase 17) — LOCAL, ISOLADO, só dados sintéticos e transporte simulado.

    python -m rocha_reis_agent.ops ensaio-operacional --destino DIR_NOVO   (DIR em volume cifrado verificado)

Monta um "servidor" numa raiz descartável a partir do PACOTE da versão atual e exercita: inicialização,
mensagens fictícias pelo webhook assinado, atendimento humano com troca de senha e 2º fator, backup
automático + cópia externa SIMULADA, queda abrupta (kill -9) de todos os processos no meio do trabalho,
reinício, conferência das filas (sem envio duplicado nem reenvio de incerto), integridade e auditoria, e
por fim a RESTAURAÇÃO numa segunda instância nova usando só o backup externo + configuração e chaves
guardadas à parte (o 2º fator precisa continuar funcionando). Mede o tempo de cada etapa.

Não toca em nenhum outro perfil nem no banco principal de nada; não é prova de recuperação num servidor
real (disco, rede e máquina diferentes) — é o ensaio do PROCEDIMENTO.
"""

from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import sys
import time
from urllib.parse import urlsplit

from rocha_reis_agent.ops import deploy
from rocha_reis_agent.ops.backup import BackupError, KeyMaterial, list_backups, restore_backup
from rocha_reis_agent.ops.localserver import LocalServices

PHONE, WABA = "100000000000009", "200000000000009"
ORIGIN = "https://painel.interno"


def _post_local(url: str, body: bytes, headers: dict[str, str], timeout: float = 5.0) -> int | None:
    """POST mínimo SÓ para 127.0.0.1 (entrega do webhook simulado ao serviço local)."""
    parts = urlsplit(url)
    if parts.hostname != "127.0.0.1" or not parts.port:
        raise BackupError("Ensaio só fala com 127.0.0.1.")
    head = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
    request = (f"POST {parts.path} HTTP/1.1\r\nHost: 127.0.0.1:{parts.port}\r\nContent-Length: {len(body)}\r\n"
               f"{head}Connection: close\r\n\r\n").encode("ascii") + body
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(("127.0.0.1", parts.port))
        connection.sendall(request)
        line = connection.recv(64).split(b"\r\n", 1)[0].split()
    return int(line[1]) if len(line) >= 2 and line[1].isdigit() else None


def _envelope(wamid: str, text: str, sender: str) -> bytes:
    message = {"from": sender, "id": wamid, "timestamp": str(int(time.time())), "type": "text", "text": {"body": text}}
    payload = {"object": "whatsapp_business_account", "entry": [{"id": WABA, "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"display_phone_number": "15550000009", "phone_number_id": PHONE},
        "contacts": [{"wa_id": sender, "profile": {"name": "Cliente fictício"}}], "messages": [message]}}]}]}
    return json.dumps(payload).encode()


class _Drill:
    def __init__(self, destination: Path) -> None:
        self.destination = destination
        self.steps: list[dict[str, object]] = []

    def step(self, name: str, started: float, ok: bool, **details: object) -> None:
        self.steps.append({"etapa": name, "ok": bool(ok), "segundos": round(time.monotonic() - started, 2), **details})
        if not ok:
            raise BackupError(f"Etapa '{name}' reprovada: {details}")


def _db(root: Path) -> Path:
    return Path(json.loads((deploy.layout(root)["config"] / "perfil.json").read_text(encoding="utf-8"))["database"])


def _q(root: Path, sql: str, *params: object):  # type: ignore[no-untyped-def]
    with closing(sqlite3.connect(_db(root).as_uri() + "?mode=ro", uri=True)) as connection:
        return connection.execute(sql, params).fetchall()


def _wait(condition, timeout: float = 60.0) -> bool:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.2)
    return False


def _send(root: Path, services: LocalServices, wamid: str, text: str, sender: str) -> int | None:
    from rocha_reis_agent.channels.meta import sign
    secret = json.loads((deploy.layout(root)["config"] / "perfil.json").read_text())["settings"]["whatsapp_app_secret"]
    raw = _envelope(wamid, text, sender)
    return _post_local(f"http://127.0.0.1:{services.ports['webhook']}/webhooks/whatsapp", raw,
                       {"Content-Type": "application/json", "X-Hub-Signature-256": sign(secret.encode(), raw)})


def _panel(root: Path):  # type: ignore[no-untyped-def]
    """Painel em processo sobre o MESMO banco (as ações passam pela API real, com CSRF, 2FA e auditoria)."""
    from rocha_reis_agent.ops.demo import profile_settings
    from rocha_reis_agent.ops.rehearsal import _Panel
    from rocha_reis_agent.panel.app import create_panel_app
    settings = profile_settings(deploy.layout(root)["config"]).model_copy(update={"panel_embedded_worker": False})
    return _Panel(create_panel_app(settings), ORIGIN)


def run_drill(destination: Path, *, python: str | None = None) -> dict[str, object]:
    from rocha_reis_agent.ops import backup_job, offsite
    from rocha_reis_agent.panel import mfa as mfa_module
    from rocha_reis_agent.persistence.storage_security import volume_protection
    destination = Path(destination).absolute()
    if destination.exists() and any(destination.iterdir()):
        raise BackupError("O destino do ensaio precisa ser um diretório novo ou vazio.")
    protection = volume_protection(destination.parent if not destination.exists() else destination)
    if not (protection.encrypted and protection.persistent):
        raise BackupError(f"Ensaio exige volume com cifragem verificada: {protection.describe()}.")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    drill = _Drill(destination)
    root, services, restored_services = destination / "servidor", None, None
    report: dict[str, object] = {"aviso": "Ensaio LOCAL com dados sintéticos e envio simulado; não prova "
                                          "recuperação em servidor real.", "volume": protection.describe()}
    try:
        # 1. instalação a partir do pacote
        t = time.monotonic()
        package = deploy.build_package(destination / "pacotes")
        installed = deploy.install_release(package, root, python=python or sys.executable, create_venv=False)
        deploy.activate(root, str(installed["id"]))
        deploy.prepare_instance(root, panel_origin=ORIGIN, laboratorio=True)
        profile = deploy.layout(root)["config"]
        data = json.loads((profile / "perfil.json").read_text())
        data["settings"].update(channel_outbox_lease_seconds=2.0, channel_inbox_lease_seconds=2.0)
        (profile / "perfil.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        validation = deploy.validate_installation(root)
        drill.step("instalacao", t, validation["ok"], versao=installed["versao"], problemas=validation["problemas"])
        # 2. inicialização
        services = LocalServices(root, python=python)
        t = time.monotonic()
        services.start()
        drill.step("inicializacao", t, services.ready(60))
        # 3. mensagens fictícias
        t = time.monotonic()
        codes = [_send(root, services, f"wamid.ensaio.a{i}", "Quero falar com uma pessoa" if i == 0 else
                       f"Quanto custa o corte? ({i})", f"55119000003{i:02d}") for i in range(20)]
        processed = _wait(lambda: _q(root, "SELECT count(*) FROM channel_inbox WHERE kind='message' AND "
                                           "status='DONE'")[0][0] == 20)
        drill.step("mensagens", t, processed and set(codes) == {200}, recebidas=len(codes))
        # 4. atendimento humano com troca de senha e 2º fator
        t = time.monotonic()
        password = (profile / "credenciais-iniciais.txt").read_text().splitlines()[1].split("\t")[2]
        panel = _panel(root)
        assert panel.login("admin.preprod", password).status_code == 200
        new_password = "Ensaio-Senha-Definitiva-2026!"
        assert panel.post("/auth/password", {"current_password": password, "new_password": new_password}).status_code == 200
        setup = panel.post("/auth/mfa/setup", {"password": new_password}).json()
        secret = __import__("base64").b32decode(setup["secret"].replace(" ", "") + "=" * (-len(setup["secret"].replace(" ", "")) % 8))
        activated = panel.post("/auth/mfa/activate", {"code": mfa_module.code_for(secret)})
        waiting = panel.get("/conversations?filter=waiting").json()["items"]
        cid = waiting[0]["id"] if waiting else ""
        assumed = panel.post(f"/conversations/{cid}/assume").status_code
        replied = panel.post(f"/conversations/{cid}/reply", {"text": "Olá! Atendimento do ensaio (fictício).",
                                                             "idempotency_key": "ensaio-resposta-0001"}).status_code
        sent = _wait(lambda: _q(root, "SELECT count(*) FROM channel_outbox WHERE conversation_id=? AND "
                                      "source_inbox_id IS NULL AND status='ACCEPTED'", cid)[0][0] == 1)
        drill.step("atendimento_humano", t, activated.status_code == 200 and assumed == 204 and replied == 200 and sent)
        # 5. backup automático + cópia externa simulada
        t = time.monotonic()
        status = backup_job.run_backup_job(profile)
        external = offsite.FakeExternalStore(destination / "externo-simulado")
        sync = offsite.sync(deploy.layout(root)["backups"], external, deploy.layout(root)["estado"] / "externo.json")
        conversations_at_backup = _q(root, "SELECT count(*) FROM conversations")[0][0]
        drill.step("backup", t, status["falhas_consecutivas"] == 0 and not sync["falhas"],
                   backup=status["ultimo_backup"], copias_externas=len(external.list()))
        # 6. queda abrupta no meio do trabalho e reinício
        t = time.monotonic()
        services.stop()
        services.start(("webhook",))
        deploy.probe_http([services.urls()[0]], timeout=30)
        for i in range(15):
            _send(root, services, f"wamid.ensaio.b{i}", f"Quanto custa a escova? ({i})", f"55119000004{i:02d}")
        services.start(("panel", "worker"))
        _wait(lambda: services.send_log.exists() and len(services.send_log.read_text().splitlines()) >= 22, 30)
        for name in list(services.processes):
            services.kill(name)  # queda de todos os processos de uma vez
        crashed = time.monotonic()
        services.start()
        back = services.ready(60)
        restart = round(time.monotonic() - crashed, 2)
        drained = _wait(lambda: _q(root, "SELECT count(*) FROM channel_inbox WHERE status IN ('PENDING','PROCESSING')")
                        [0][0] == 0 and _q(root, "SELECT count(*) FROM channel_outbox WHERE status IN "
                                                 "('PENDING','RETRY','SENDING')")[0][0] == 0, 60)
        sends = [json.loads(line)["operation"] for line in services.send_log.read_text().splitlines()]
        statuses = dict(_q(root, "SELECT status,count(*) FROM channel_outbox GROUP BY status"))
        human = _q(root, "SELECT c.state, a.user_id IS NOT NULL FROM conversations c LEFT JOIN panel_assignments a "
                         "ON a.conversation_id=c.id WHERE c.id=?", cid)
        integrity = _q(root, "PRAGMA integrity_check")[0][0]
        drill.step("queda_e_reinicio", t, back and drained and len(sends) == len(set(sends)) and integrity == "ok"
                   and set(statuses) <= {"ACCEPTED", "UNKNOWN"} and human == [("HUMAN_ACTIVE", 1)],
                   reinicio_ate_pronto_s=restart, envios=len(sends), envios_duplicados=len(sends) - len(set(sends)),
                   situacoes=statuses, integridade=integrity)
        audit = {row[0] for row in _q(root, "SELECT action FROM panel_audit")}
        services.stop()
        # 7. restauração numa instância NOVA: backup externo + configuração e chaves guardadas à parte
        t = time.monotonic()
        root2 = destination / "servidor-restaurado"
        deploy.install_release(package, root2, python=python or sys.executable, create_venv=False)
        deploy.activate(root2, str(installed["id"]))
        deploy.prepare_layout(root2)  # Fase 18B-0: pastas de dados só com o "volume" pronto (aqui: laboratório)
        paths1, paths2 = deploy.layout(root), deploy.layout(root2)
        config_text = (paths1["config"] / "perfil.json").read_text().replace(str(root), str(root2))
        os.chmod(paths2["config"], 0o700)
        descriptor = os.open(paths2["config"] / "perfil.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(config_text)
        for key in ("mfa.key", "backup.key"):
            shutil.copy2(paths1["chaves"] / key, paths2["chaves"] / key)
            os.chmod(paths2["chaves"] / key, 0o600)
        latest = sorted(external.list())[-1]
        fetched = offsite.fetch(external, latest, paths2["estado"] / "baixado")
        restore_backup(fetched, _db(root2), KeyMaterial("keyfile", key_file=paths2["chaves"] / "backup.key"))
        fetched.unlink()
        restored_services = LocalServices(root2, python=python)
        restored_services.start()
        ready2 = restored_services.ready(60)
        count2 = _q(root2, "SELECT count(*) FROM conversations")[0][0]
        panel2 = _panel(root2)
        step1 = panel2.login("admin.preprod", new_password).json()
        step2 = panel2.post("/auth/mfa", {"code": mfa_module.code_for(secret, now=time.time() + 30)})
        audit2 = {row[0] for row in _q(root2, "SELECT action FROM panel_audit")}
        drill.step("restauracao_isolada", t, ready2 and count2 == conversations_at_backup
                   and step1.get("mfa_required") is True and step2.status_code == 200
                   and {"conversation_assumed", "operator_message_queued"} <= audit2
                   and validation["ok"] and deploy.validate_installation(root2)["ok"],
                   conversas_no_backup=conversations_at_backup, conversas_restauradas=count2,
                   segundo_fator_ok=step2.status_code == 200, auditoria_preservada=sorted(audit2 & audit))
        report["resultado"] = "OK"
    except (BackupError, AssertionError, deploy.DeployError, OSError, KeyError, IndexError) as error:
        report["resultado"] = "FALHOU"
        report["erro"] = str(error) or type(error).__name__
    finally:
        for running in (services, restored_services):
            if running is not None:
                running.stop(timeout=10)
    report["etapas"] = drill.steps
    descriptor = os.open(destination / "relatorio-ensaio.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    return report
