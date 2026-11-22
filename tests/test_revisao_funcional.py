"""Revisão funcional (22/11/2026) — um teste para cada defeito funcional corrigido, mais a varredura de
permissões por chamada DIRETA às rotas e a verificação das cinco travas em todos os relatórios.

- Cliente que volta a escrever num atendimento ENCERRADO ficava sem resposta, fora da fila e sem aviso.
- Quinta trava (backup externo real) não aparecia no painel, em validar-instalacao, no diagnóstico nem no
  manifesto.
- Linha PRONTO sem o caminho das senhas (no VS Code em Flatpak a pasta de dados é outra).
- Guia de implantação com ordem impossível e cópia de unidade com nome inválido.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
import time
from uuid import uuid4

import pytest

from panel_support import Panel, add_user, conversation_id, create_panel_app, customer, panel_settings

ROOT = Path(__file__).resolve().parents[1]
LOCKS = ("GRAPH_TRANSPORT_ACTIVATION_AUTHORIZED", "HOMOLOGATION_TRANSPORT_ACTIVATION_AUTHORIZED",
         "GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED", "REAL_DATA_ACTIVATION_AUTHORIZED",
         "EXTERNAL_BACKUP_ACTIVATION_AUTHORIZED")


@pytest.fixture
def team(tmp_path):  # type: ignore[no-untyped-def]
    app = create_panel_app(panel_settings(tmp_path))
    ids = {"admin": add_user(app, "admin.rf", "ADMIN"), "ana": add_user(app, "ana.rf"), "bia": add_user(app, "bia.rf")}
    panels = {name: Panel(app) for name in ids}
    for name, panel in panels.items():
        assert panel.login(f"{name}.rf").status_code == 200
    return app, ids, panels, tmp_path


def _db(app):  # type: ignore[no-untyped-def]
    from contextlib import closing
    return closing(sqlite3.connect(app.state.conversation_repository.path))


# ------------------------------------------------------------------ reabertura pelo cliente
def test_client_writing_after_resolution_goes_back_to_the_human_queue(team):
    app, ids, p, directory = team
    assert customer(directory, "wamid.rf1", "Quero falar com uma pessoa") == ["replied", "ACCEPTED"]
    cid = conversation_id(p["ana"], "waiting")
    assert p["ana"].post(f"/conversations/{cid}/assume").status_code == 204
    assert p["ana"].post(f"/conversations/{cid}/reply", {"text": "Oi!", "idempotency_key": uuid4().hex}).status_code == 200
    assert p["ana"].post(f"/conversations/{cid}/resolve").status_code == 204
    with _db(app) as c:
        assert c.execute("SELECT state FROM conversations").fetchone()[0] == "RESOLVED"
        assert c.execute("SELECT user_id FROM panel_assignments").fetchone()[0] == ids["ana"]  # histórico visível
    # Antes: 'recorded_without_reply' e a conversa ficava ENCERRADA, fora da fila, sem aviso nenhum.
    assert customer(directory, "wamid.rf2", "Oi, voltei. Quero remarcar") == ["recorded_without_reply"]
    with _db(app) as c:
        assert c.execute("SELECT state, closed FROM conversations").fetchone() == ("WAITING_HUMAN", 0)
        assert c.execute("SELECT count(*) FROM panel_assignments").fetchone()[0] == 0
        last = c.execute("SELECT previous, current, action, operator_id FROM transitions ORDER BY ordinal DESC").fetchone()
        assert last == ("RESOLVED", "WAITING_HUMAN", "client_returned", None)
        replies = c.execute("SELECT count(*) FROM messages WHERE role='assistant'").fetchone()[0]
    for who in ("ana", "bia"):
        waiting = p[who].get("/conversations?filter=waiting").json()["items"]
        assert [item["id"] for item in waiting] == [cid], who
        kinds = {n["type"] for n in p[who].get("/notifications").json()["items"]}
        assert "human_requested" in kinds, who
    # Sem resposta automática de conteúdo (a automação continua suspensa) e nova mensagem idem.
    assert customer(directory, "wamid.rf3", "Alguém aí?") == ["recorded_without_reply"]
    with _db(app) as c:
        assert c.execute("SELECT count(*) FROM messages WHERE role='assistant'").fetchone()[0] == replies
    # Outra pessoa assume normalmente; retomar a automação continua exigindo a administração.
    assert p["bia"].post(f"/conversations/{cid}/assume").status_code == 204
    assert p["bia"].post(f"/conversations/{cid}/resolve").status_code == 204
    assert p["bia"].post(f"/conversations/{cid}/resume").status_code == 403
    assert p["admin"].post(f"/conversations/{cid}/resume").status_code == 204


def test_duplicate_delivery_after_resolution_reopens_only_once(team):
    app, ids, p, directory = team
    customer(directory, "wamid.rf4", "Quero falar com uma pessoa")
    cid = conversation_id(p["ana"], "waiting")
    p["ana"].post(f"/conversations/{cid}/assume"); p["ana"].post(f"/conversations/{cid}/resolve")
    customer(directory, "wamid.rf5", "Voltei")
    assert customer(directory, "wamid.rf5", "Voltei") in ([], ["duplicate"])  # a Meta reentrega: descartada
    with _db(app) as c:
        reopened = c.execute("SELECT count(*) FROM transitions WHERE action='client_returned'").fetchone()[0]
    assert reopened == 1


def _stale_active(app, user_id):  # type: ignore[no-untyped-def]
    """Retrato do funcionário AINDA ativo, tirado antes da desativação (a janela da corrida)."""
    return app.state.panel_service.store.user(user_id)


def test_transfer_racing_a_deactivation_never_leaves_an_inactive_owner(team, monkeypatch):
    """Corrida real: o destino foi conferido como ativo ANTES da transação; a desativação entra no meio."""
    app, ids, p, directory = team
    customer(directory, "wamid.rf8", "Quero falar com uma pessoa")
    cid = conversation_id(p["ana"], "waiting")
    assert p["ana"].post(f"/conversations/{cid}/assume").status_code == 204
    stale = _stale_active(app, ids["bia"])
    assert p["admin"].post(f"/users/{ids['bia']}/deactivate", {}).status_code == 204
    store = app.state.panel_service.store
    real_user = store.user
    monkeypatch.setattr(store, "user", lambda uid: stale if uid == ids["bia"] else real_user(uid))
    response = p["ana"].post(f"/conversations/{cid}/transfer", {"target_user_id": ids["bia"]})
    assert response.status_code == 409, response.text  # antes: 204 e a conversa ia para a Bia desativada
    with _db(app) as c:
        assert c.execute("SELECT c.state, a.user_id FROM conversations c JOIN panel_assignments a "
                         "ON a.conversation_id=c.id").fetchone() == ("HUMAN_ACTIVE", ids["ana"])
        orphans = c.execute("SELECT count(*) FROM panel_assignments a JOIN panel_users u ON u.id=a.user_id "
                            "WHERE u.active=0").fetchone()[0]
    assert orphans == 0
    assert p["admin"].get("/overview").json().get("orphaned_active", 0) == 0


def test_assume_in_flight_while_the_operator_is_deactivated_is_refused(team):
    from rocha_reis_agent.panel.security import PanelError
    app, ids, p, directory = team
    customer(directory, "wamid.rf9", "Quero falar com uma pessoa")
    cid = conversation_id(p["bia"], "waiting")
    stale = _stale_active(app, ids["bia"])  # requisição da Bia já autenticada...
    assert p["admin"].post(f"/users/{ids['bia']}/deactivate", {}).status_code == 204  # ...e a desativação entra
    with pytest.raises(PanelError) as refused:
        app.state.panel_service.assume(stale, cid)
    assert refused.value.status == 409
    with _db(app) as c:
        assert c.execute("SELECT state FROM conversations").fetchone()[0] == "WAITING_HUMAN"
        assert c.execute("SELECT count(*) FROM panel_assignments").fetchone()[0] == 0


def test_handoff_reopen_is_only_from_resolved():
    from rocha_reis_agent.application.handoff import HumanHandoffManager
    from rocha_reis_agent.domain.contracts import ConversationState as State, SimulationError
    from rocha_reis_agent.domain.conversations import Conversation
    conversation = Conversation(conversation_id="c1", session_id="s1")
    for state in (State.AUTO, State.WAITING_HUMAN, State.HUMAN_ACTIVE):
        conversation.state = state
        with pytest.raises(SimulationError):
            HumanHandoffManager().reopen_by_client(conversation)


# ------------------------------------------------------------------ permissões por chamada direta
ADMIN_ONLY = [
    ("get", "/users", None), ("post", "/users", {"login": "x.y", "display_name": "X", "role": "ADMIN",
                                                 "password": "Senha-Longa-2026!x"}),
    ("post", "/users/{bia}/deactivate", {}), ("post", "/users/{bia}/activate", {}),
    ("post", "/users/{bia}/mfa-reset", {"justification": "teste de permissão"}),
    ("post", "/users/{bia}/reset-password", {"password": "Outra-Senha-2026!x"}),
    ("get", "/audit", None), ("get", "/settings", None),
    ("put", "/settings", {"operators_can_transfer": True, "operators_can_resume": True, "urgent_after_minutes": 1}),
    ("get", "/system", None), ("get", "/retired-contacts", None),
    ("post", "/retired-contacts/qualquer/reinstate", {"confirmation": "REATIVAR"}),
    ("post", "/dispatch/halt", {"reason": "teste"}), ("post", "/dispatch/resume", {}),
    ("get", "/knowledge", None), ("get", "/knowledge/history?fact_id=x", None),
    ("post", "/knowledge/drafts", {"kind": "hours", "data": {"text": "x"}}),
    ("post", "/knowledge/approve", {"record_id": "x"}), ("post", "/knowledge/preview", {"text": "oi"}),
    ("get", "/ai/usage", None),
]


def test_operator_cannot_reach_any_administrative_route_directly(team):
    app, ids, p, _ = team
    ana = p["ana"]
    with _db(app) as c:
        before = (c.execute("SELECT count(*) FROM panel_users").fetchone()[0],
                  c.execute("SELECT value FROM panel_settings WHERE key='operators_can_resume'").fetchone(),
                  c.execute("SELECT count(*) FROM channel_control").fetchone()[0],
                  c.execute("SELECT active FROM panel_users WHERE id=?", (ids["bia"],)).fetchone()[0])
    statuses = {}
    for method, path, body in ADMIN_ONLY:
        path = path.format(bia=ids["bia"])
        response = (ana.get(path) if method == "get" else ana.put(path, body) if method == "put"
                    else ana.post(path, body))
        statuses[f"{method.upper()} {path}"] = response.status_code
    assert {k: v for k, v in statuses.items() if v != 403} == {}, statuses
    with _db(app) as c:
        after = (c.execute("SELECT count(*) FROM panel_users").fetchone()[0],
                 c.execute("SELECT value FROM panel_settings WHERE key='operators_can_resume'").fetchone(),
                 c.execute("SELECT count(*) FROM channel_control").fetchone()[0],
                 c.execute("SELECT active FROM panel_users WHERE id=?", (ids["bia"],)).fetchone()[0])
        denied = c.execute("SELECT count(*) FROM panel_audit WHERE action='admin_access' AND outcome='denied'").fetchone()[0]
    assert after == before and denied >= len(ADMIN_ONLY)


def test_every_panel_route_is_classified(team):
    """Nenhuma rota nova escapa da varredura acima sem decisão explícita."""
    app, *_ = team
    def walk(items):  # type: ignore[no-untyped-def]
        for r in items:
            if hasattr(r, "path"):
                yield r
            elif hasattr(r, "original_router"):  # FastAPI recente: roteador incluído de forma preguiçosa
                yield from walk(r.original_router.routes)
    routes = sorted({(m, r.path.removeprefix("/api/panel")) for r in walk(app.routes) if r.path.startswith("/api/panel")
                     for m in getattr(r, "methods", set()) if m in ("GET", "POST", "PUT", "DELETE")})
    assert len(routes) > 40
    swept = {(m.upper(), re.sub(r"\?.*", "", p.replace("{bia}", "{user_id}").replace("qualquer", "{conversation_id}")))
             for m, p, _ in ADMIN_ONLY}
    operator_allowed = {
        ("POST", "/auth/login"), ("POST", "/auth/mfa"), ("GET", "/auth/mfa/status"), ("POST", "/auth/mfa/setup"),
        ("POST", "/auth/mfa/activate"), ("POST", "/auth/mfa/recovery-codes"), ("POST", "/auth/mfa/disable"),
        ("POST", "/auth/logout"), ("GET", "/auth/session"), ("POST", "/auth/password"), ("GET", "/overview"),
        ("GET", "/conversations"), ("GET", "/conversations/{conversation_id}"),
        ("GET", "/conversations/{conversation_id}/messages"), ("GET", "/notifications"),
        ("POST", "/conversations/{conversation_id}/read"), ("POST", "/conversations/{conversation_id}/assume"),
        ("POST", "/conversations/{conversation_id}/resolve"), ("POST", "/conversations/{conversation_id}/resume"),
        ("POST", "/conversations/{conversation_id}/request-human"), ("POST", "/conversations/{conversation_id}/transfer"),
        ("POST", "/conversations/{conversation_id}/reply"), ("GET", "/operators"), ("GET", "/events"),
    }
    knowledge_actions = {("POST", f"/knowledge/{n}") for n in ("approve", "reject", "revoke")}
    unclassified = [r for r in routes if r not in swept | operator_allowed | knowledge_actions
                    and not r[1].startswith("/knowledge/{")]
    assert unclassified == [], unclassified


def test_operator_conversation_limits_hold(team):
    app, ids, p, directory = team
    customer(directory, "wamid.rf6", "Quero falar com uma pessoa")
    cid = conversation_id(p["ana"], "waiting")
    assert p["ana"].post(f"/conversations/{cid}/assume").status_code == 204
    # Bia não vê nem age na conversa da Ana; nem encaminha conversas automáticas.
    assert p["bia"].get(f"/conversations/{cid}").status_code == 404
    assert p["bia"].post(f"/conversations/{cid}/resolve").status_code == 404
    assert p["bia"].post(f"/conversations/{cid}/reply", {"text": "x", "idempotency_key": uuid4().hex}).status_code == 404
    customer(directory, "wamid.rf7", "Quanto custa o corte?", sender="5511900000902")
    auto = [i["id"] for i in p["admin"].get("/conversations?filter=auto").json()["items"]]
    assert auto and p["bia"].post(f"/conversations/{auto[0]}/request-human").status_code == 403


# ------------------------------------------------------------------ cinco travas
def test_five_locks_exist_are_false_and_are_reported_everywhere(tmp_path):
    from rocha_reis_agent import config as c
    declared = re.findall(r"^([A-Z_]+_AUTHORIZED): Final = (\w+)", (ROOT / "rocha_reis_agent" / "config.py").read_text(), re.M)
    assert sorted(declared) == sorted((name, "False") for name in LOCKS), declared
    assert not any(getattr(c, name) for name in LOCKS)
    from rocha_reis_agent.ops import deploy, release
    assert set(release.release_info()["travas"]) == {"meta_graph", "meta_homologacao", "gemini_real", "dados_reais",
                                                    "backup_externo"}
    assert set(deploy.validate_installation(tmp_path / "vazia")["travas"].values()) == {False}
    assert len(deploy.validate_installation(tmp_path / "vazia")["travas"]) == 5
    app = create_panel_app(panel_settings(tmp_path))
    add_user(app, "admin.tr", "ADMIN")
    admin = Panel(app); admin.login("admin.tr")
    assert admin.get("/system").json()["locks"] == {"meta_graph": False, "meta_homologation": False, "gemini_real": False,
                                                    "real_data": False, "external_backup": False}
    js = (ROOT / "rocha_reis_agent" / "panel" / "static" / "app.js").read_text()
    for key in ("meta_graph", "meta_homologation", "gemini_real", "real_data", "external_backup"):
        assert re.search(rf"\b{key}: \"", js), key  # rótulo legível na tela Sistema
    # Diagnóstico de ambiente (ops ambiente) sobre um perfil real e diagnóstico do servidor (ops estado-servidor).
    from rocha_reis_agent.ops import environments, readiness
    from rocha_reis_agent.ops.demo import build_demo
    build_demo(tmp_path / "demo")
    names = {"meta_graph", "meta_homologacao", "gemini_real", "dados_reais", "backup_externo"}
    report = environments.diagnose(tmp_path / "demo")
    found = [v for v in _walk(report) if isinstance(v, dict) and set(v) == names]
    assert found and all(set(v.values()) == {False} for v in found)
    assert readiness.server_state(tmp_path / "vazia", probe=lambda *a, **k: None)["travas"] == dict.fromkeys(names, False)


def _walk(value):  # type: ignore[no-untyped-def]
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)


@pytest.mark.parametrize("lock", LOCKS)
def test_single_source_of_locks_sees_each_of_the_five(monkeypatch, lock):
    """Cada trava declarada em config.py aparece na fonte única (aberta SÓ em memória, por monkeypatch)."""
    from rocha_reis_agent import config as c
    assert not any(c.activation_locks().values())
    monkeypatch.setattr(c, lock, True)
    assert sum(c.activation_locks().values()) == 1


def test_closed_external_backup_lock_blocks_every_real_endpoint_before_any_socket(monkeypatch):
    from rocha_reis_agent.ops import offsite, s3_store
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("conexão aberta"))
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("conexão aberta"))
    for endpoint in ("https://s3.us-west-004.backblazeb2.com", "https://x.r2.cloudflarestorage.com", "https://s3.amazonaws.com"):
        with pytest.raises(offsite.ExternalError, match="trava"):
            s3_store.validate_config(s3_store.S3Config(endpoint, "us-west-004", "rr-backups"))


# ------------------------------------------------------------------ PRONTO e guia
def test_ready_line_prints_the_full_credentials_path(tmp_path):
    profile = tmp_path / "demo com espaço"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0)); port = probe.getsockname()[1]
    subprocess.run([sys.executable, "-m", "rocha_reis_agent.ops", "demo", "--perfil", str(profile)], cwd=ROOT,
                   check=True, capture_output=True, timeout=120)
    process = subprocess.Popen([sys.executable, "-m", "rocha_reis_agent.ops", "iniciar", "--perfil", str(profile),
                                "--porta", str(port)], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines: list[str] = []
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            line = process.stdout.readline()  # type: ignore[union-attr]
            lines.append(line)
            if "Para encerrar" in line or not line:
                break
    finally:
        process.terminate(); process.wait(timeout=20); process.stdout.close()  # type: ignore[union-attr]
    text = "".join(lines)
    assert "PRONTO" in text and f"Senhas (só neste computador): {profile / 'credenciais-demo.txt'}" in text, text
    assert "Demo-" not in text  # a senha em si nunca é impressa


def test_deployment_guide_order_is_executable_as_written():
    guide = (ROOT / "docs" / "GUIA_IMPLANTACAO_SERVIDOR.md").read_text(encoding="utf-8")
    section4 = guide[guide.index("## 4. Instalação"):guide.index("## 5.")]
    assert "cp deploy/systemd/* " not in section4  # copiaria a montagem com nome inválido
    assert "cp deploy/systemd/*.service deploy/systemd/*.timer deploy/systemd/*.target" in section4
    assert section4.index("ops instalar") < section4.index("marcar-volume") < section4.index("preparar-servidor")
    # Servidor limpo: sem código nem bibliotecas; o instalador roda de um ambiente temporário (simulado em 22/11).
    assert not re.search(r"^python3\.13 -m rocha_reis_agent\.ops instalar", section4, re.M)
    assert section4.index("tar -xzf") < section4.index("-m venv") < section4.index("requirements.lock") \
        < section4.index(".venv-instalador/bin/python -m rocha_reis_agent.ops instalar") < section4.index("marcar-volume --ponto")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "--perfil ~/.local/share/rocha-reis-agent/demo" not in readme  # fixo: errado no VS Code em Flatpak
    step3 = guide[guide.index("3. **Preparar o volume**"):guide.index("4. **Desbloquear")]
    assert "marcar-volume" not in step3 and "systemctl start 'var-lib" not in step3  # nada que ainda não exista
    assert "mount -o nodev,nosuid,noexec /dev/mapper/rocha-reis-dados /var/lib/rocha-reis-agent" in step3


@pytest.mark.parametrize("lock", LOCKS)
def test_startup_and_demo_script_refuse_to_run_with_any_lock_open(tmp_path, monkeypatch, lock):
    """A trava é aberta SÓ na memória deste teste (monkeypatch); o código continua com as cinco em False."""
    from rocha_reis_agent import config as c
    from rocha_reis_agent.ops import launcher, rehearsal
    from rocha_reis_agent.ops.backup import BackupError
    from rocha_reis_agent.ops.demo import build_demo
    build_demo(tmp_path / "demo")
    assert launcher.preflight(tmp_path / "demo", 8765)  # tudo fechado: segue
    monkeypatch.setattr(c, lock, True)
    with pytest.raises(launcher.LaunchError, match="Trava"):
        launcher.preflight(tmp_path / "demo", 8765)
    with pytest.raises(BackupError, match="trava"):
        rehearsal.run_rehearsal(tmp_path / "roteiro")


# ------------------------------------------------------------------ roteiros documentados (README)
@pytest.mark.parametrize("script", ["phase1.demo.txt", "phase2.demo.txt"])
def test_documented_cli_demo_scripts_run_without_errors_and_resume(script):
    """Com o ADR-134 o "/operator resume" logo após o cliente voltar falhava ("Transição inválida") e a
    demonstração do README terminava sem retomar a automação."""
    with (ROOT / "examples" / script).open(encoding="utf-8") as stdin:
        result = subprocess.run([sys.executable, "-m", "rocha_reis_agent.simulator", "--fixture"], cwd=ROOT, text=True,
                                stdin=stdin, capture_output=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert "Erro:" not in result.stdout, result.stdout
    assert "client_returned: RESOLVED -> WAITING_HUMAN" in result.stdout
    assert "resolve: WAITING_HUMAN -> RESOLVED" in result.stdout and "Estado: AUTO" in result.stdout


def test_documented_two_process_demo_resumes(tmp_path):
    database = tmp_path / "dados"; database.mkdir(mode=0o700)
    path = database / "p3.synthetic.sqlite3"
    outputs = []
    for script in ("phase3.start.demo.txt", "phase3.resume.demo.txt"):
        with (ROOT / "examples" / script).open(encoding="utf-8") as stdin:
            result = subprocess.run([sys.executable, "-m", "rocha_reis_agent.simulator", "--fixture", "--database",
                                     str(path)], cwd=ROOT, text=True, stdin=stdin, capture_output=True, timeout=120)
        assert result.returncode == 0, result.stderr
        outputs.append(result.stdout)
    assert all("Erro:" not in out for out in outputs), outputs
    assert "Estado: AUTO" in outputs[1]


# ------------------------------------------------------------------ reinício imediato (Ctrl+C e iniciar de novo)
_REAL_CONNECT = socket.socket.connect  # capturado na importação, antes do bloqueio de rede do conftest


def _time_wait_port() -> int:
    """Porta com conexão em TIME_WAIT do lado do servidor (como após o Ctrl+C com o navegador aberto)."""
    server = socket.socket(); server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0)); server.listen(1); port = server.getsockname()[1]
    client = socket.socket(); _REAL_CONNECT(client, ("127.0.0.1", port))  # só 127.0.0.1 (como test_phase17_server)
    accepted, _ = server.accept()
    accepted.close(); server.close()   # o SERVIDOR fecha primeiro → TIME_WAIT na porta dele
    time.sleep(0.1); client.close()
    return port


def test_restart_right_after_stop_is_not_refused_as_busy_port(tmp_path):
    from rocha_reis_agent.ops import launcher
    from rocha_reis_agent.ops.demo import build_demo
    build_demo(tmp_path / "demo")
    port = _time_wait_port()
    assert launcher.preflight(tmp_path / "demo", port)  # antes: "Porta N ocupada" por ~60 s após o Ctrl+C
    with socket.socket() as busy:  # uma porta realmente em uso continua recusada
        busy.bind(("127.0.0.1", 0)); busy.listen(1)
        with pytest.raises(launcher.LaunchError, match="ocupada"):
            launcher.preflight(tmp_path / "demo", busy.getsockname()[1])


# ------------------------------------------------------------------ pacote adulterado (achado na regressão)
@pytest.fixture(scope="module")
def built_package(tmp_path_factory):  # type: ignore[no-untyped-def]
    from rocha_reis_agent.ops import deploy
    return deploy.build_package(tmp_path_factory.mktemp("pacote"))


def test_any_single_corrupted_byte_is_refused_cleanly(built_package, tmp_path):
    """Antes, conforme a posição, o byte trocado levantava zlib.error cru (falha intermitente de
    test_update_refuses_wrong_confirmation_and_tampered_package, que só troca o byte do MEIO)."""
    from rocha_reis_agent.ops import deploy
    data = built_package.read_bytes()
    original = deploy.verify_package(built_package, tmp_path / "original")
    files = lambda base: {p.relative_to(base): p.read_bytes() for p in base.rglob("*") if p.is_file()}  # noqa: E731
    reference = files(tmp_path / "original")
    positions = sorted({20, len(data) // 2, len(data) - 12} | set(range(64, len(data) - 16, max(1, len(data) // 97))))
    refused = 0
    for index, position in enumerate(positions):
        corrupted = bytearray(data); corrupted[position] ^= 0xFF
        target = tmp_path / f"adulterado-{index}.tar.gz"; target.write_bytes(bytes(corrupted))
        try:
            manifest = deploy.verify_package(target, tmp_path / f"x{index}")
        except deploy.DeployError:
            refused += 1  # recusa CONTROLADA (nunca zlib.error/erro cru)
        else:
            # Byte sem efeito no conteúdo (preenchimento final do tar, campo do cabeçalho gzip não verificado):
            # aceitar só se o que seria instalado é IDÊNTICO ao original.
            assert manifest == original and files(tmp_path / f"x{index}") == reference, position
    assert refused >= len(positions) * 0.9, (refused, len(positions))
