"""Fase 14 — RC1 Offline: roteiro guiado completo (e que detecta falhas), identificação da versão,
conteúdo da demonstração e confinamento das contas fictícias."""

import json

import pytest

from rocha_reis_agent import RELEASE_NAME, __version__
from rocha_reis_agent.ops.rehearsal import run_rehearsal
from rocha_reis_agent.persistence.sqlite import SCHEMA_VERSION


def test_guided_rehearsal_runs_all_flows_on_an_isolated_demo(tmp_path):
    result = run_rehearsal(tmp_path / "roteiro")
    failures = [f for f in result["fluxos"] if not f["ok"]]
    assert result["resultado"] == "OK" and result["aprovados"] == result["total"] == 13, failures
    assert result["perfil_isolado"].startswith(str(tmp_path))
    assert "Não é validação pelo salão" in result["aviso"]


def test_rehearsal_reports_a_broken_flow_instead_of_hiding_it(tmp_path, monkeypatch):
    from rocha_reis_agent.responses import ResponseGenerator
    # Defeito proposital: preço "a partir de" dito como valor final.
    monkeypatch.setattr(ResponseGenerator, "_price", classmethod(
        lambda cls, service: f"{service.name}: {cls._money(service.price)}." if service.price is not None
        else f"Não tenho preço confirmado para {service.name}."))
    result = run_rehearsal(tmp_path / "roteiro")
    by_id = {f["id"]: f for f in result["fluxos"]}
    assert result["resultado"] == "FALHOU" and by_id["F2"]["ok"] is False
    assert "R$ 120,00" in by_id["F2"]["detalhe"]  # o detalhe mostra a resposta errada observada
    assert by_id["F1"]["ok"] is True  # o resto continua sendo verificado


def test_rehearsal_refuses_to_run_with_any_real_lock_open(tmp_path, monkeypatch):
    from rocha_reis_agent import config
    from rocha_reis_agent.ops.backup import BackupError
    monkeypatch.setattr(config, "REAL_DATA_ACTIVATION_AUTHORIZED", True)
    with pytest.raises(BackupError, match="trava"):
        run_rehearsal(tmp_path / "x")
    assert not (tmp_path / "x").exists()


def test_release_identification_is_honest_about_the_tree():
    from rocha_reis_agent.ops.release import release_info
    info = release_info()
    assert info["nome"] == RELEASE_NAME == "Rocha Reis Agent — RC1 Offline" and info["versao"] == __version__
    assert info["esquema_banco"] == SCHEMA_VERSION and len(info["migracoes"]) == SCHEMA_VERSION
    assert set(info["travas"].values()) == {False}
    assert len(info["impressao_digital_conteudo"]) == 16 and info["arquivos"] > 100
    if info["git"]["alteracoes_nao_commitadas"]:
        assert info["git"]["versionada"] is False  # árvore suja nunca é apresentada como versionada
    assert info["dependencias"]["cryptography"] and info["dependencias"]["fastapi"]


def test_demo_has_structured_starting_price_and_safe_practice_draft(tmp_path):
    from rocha_reis_agent.bootstrap import configured_knowledge, configured_repository
    from rocha_reis_agent.knowledge_projection import authorize_knowledge
    from rocha_reis_agent.ops.demo import build_demo, profile_settings
    profile = tmp_path / "demo"
    build_demo(profile)
    settings = profile_settings(profile)
    knowledge = configured_knowledge(settings, configured_repository(settings))
    base = authorize_knowledge(knowledge.get()).base
    services = {s.id: s for s in base.services}
    assert services["coloracao"].price_kind == "starting" and services["corte"].price is not None
    assert knowledge.latest("price:escova").status.value == "DRAFT"  # rascunho não suspende o corte
    assert base.synthetic is True


def test_demo_accounts_exist_only_in_the_demo_database(tmp_path):
    from rocha_reis_agent.ops.demo import build_demo
    from rocha_reis_agent.persistence.sqlite import SQLiteConversationRepository
    from rocha_reis_agent.domain.contracts import StorageError
    profile = tmp_path / "demo"
    build_demo(profile)
    database = profile / "dados" / "demo.synthetic.sqlite3"
    for environment in (None, "development", "preproduction"):
        with pytest.raises(StorageError, match="demo"):
            SQLiteConversationRepository(database, environment=environment)
    other = SQLiteConversationRepository(tmp_path / "outro.synthetic.sqlite3")
    from rocha_reis_agent.panel.store import SQLitePanelStore
    assert SQLitePanelStore(other).count_users() == 0
    lines = (profile / "credenciais-demo.txt").read_text().splitlines()
    assert lines[0].startswith("# Credenciais FICTÍCIAS") and len(lines) == 4
    passwords = [line.split("\t")[2] for line in lines[1:]]
    assert len(set(passwords)) == 3 and all(len(p) >= 20 for p in passwords)  # geradas, não previsíveis
    again = tmp_path / "demo2"
    build_demo(again)
    assert set(passwords).isdisjoint(line.split("\t")[2] for line in (again / "credenciais-demo.txt").read_text().splitlines()[1:])


def test_cli_version_and_rehearsal_exit_codes(tmp_path, capsys):
    from rocha_reis_agent.ops.__main__ import main
    assert main(["versao"]) == 0
    assert json.loads(capsys.readouterr().out)["versao"] == __version__
    assert main(["roteiro", "--destino", str(tmp_path / "r")]) == 0
    assert json.loads(capsys.readouterr().out)["resultado"] == "OK"


def test_login_second_step_and_session_payloads_carry_the_same_fields(tmp_path, monkeypatch):
    """Consolidação: as três respostas que abrem o painel devolvem os mesmos campos."""
    from panel_support import PASSWORD, Panel, add_user, create_panel_app, panel_settings
    app = create_panel_app(panel_settings(tmp_path))
    add_user(app, "ana.teste")
    panel = Panel(app)
    login = panel.login("ana.teste").json()
    session = panel.get("/auth/session").json()
    assert set(login) == set(session) and login["release"] == session["release"]
