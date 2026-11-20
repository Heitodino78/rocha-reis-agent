"""Fase 11 — conflitos no conhecimento, prévia simulada e mensagens de erro compreensíveis."""

import pytest

from panel_support import Panel, add_user, create_panel_app, panel_settings
from rocha_reis_agent.knowledge_catalog import KnowledgeScope
from rocha_reis_agent.persistence.sqlite_knowledge import SQLiteKnowledgeRepository
from whatsapp_support import catalog


@pytest.fixture
def admin(tmp_path):
    app = create_panel_app(panel_settings(tmp_path, knowledge_source="database"))
    knowledge = SQLiteKnowledgeRepository(app.state.conversation_repository)
    knowledge.initialize(KnowledgeScope.SYNTHETIC)
    knowledge.import_catalog(catalog(), author="fixture-sintetica")
    add_user(app, "admin.teste", "ADMIN")
    panel = Panel(app)
    panel.login("admin.teste")
    return panel


def draft(panel, kind, key, fields, expected=None):
    return panel.post("/knowledge/drafts", {"kind": kind, "key": key, "fields": fields,
                                            "source": "Teste fictício", "expected_version": expected})


def test_service_name_or_alias_colliding_with_another_approved_service_cannot_be_approved(admin):
    created = draft(admin, "service", "corte-feminino", {"name": "Corte feminino", "aliases": "Corte de cabelo"})
    assert created.status_code == 201
    response = admin.post("/knowledge/approve", {"fact_id": "service:corte-feminino", "version": 1})
    assert response.status_code == 409 and "corte de cabelo" in response.json()["detail"]
    assert "service:corte" in response.json()["detail"]
    denied = [e for e in admin.get("/audit").json() if e["action"] == "knowledge_approved"]
    assert denied and denied[0]["outcome"] == "denied"
    # Sem o apelido repetido a aprovação passa.
    fixed = draft(admin, "service", "corte-feminino", {"name": "Corte feminino", "aliases": "corte longo"}, expected=1)
    assert admin.post("/knowledge/approve", {"fact_id": "service:corte-feminino",
                                             "version": fixed.json()["version"]}).status_code == 200


def test_new_version_of_the_same_service_is_not_a_conflict(admin):
    created = draft(admin, "service", "corte", {"name": "Corte de teste", "aliases": "corte, corte de cabelo",
                                                "description": "Descrição revisada (fictícia)."}, expected=1)
    assert admin.post("/knowledge/approve", {"fact_id": "service:corte",
                                             "version": created.json()["version"]}).status_code == 200


def test_duplicate_faq_question_is_blocked(admin):
    created = draft(admin, "faq", "levar", {"questions": "O que levar para o teste?", "answer": "Outra resposta."})
    response = admin.post("/knowledge/approve", {"fact_id": "faq:levar", "version": created.json()["version"]})
    assert response.status_code == 409 and "faq:0" in response.json()["detail"]


def test_conflicts_already_in_the_base_are_listed_as_pending(admin, tmp_path):
    repository = SQLiteKnowledgeRepository(admin.client.app.state.conversation_repository)
    from rocha_reis_agent.knowledge import Service
    from rocha_reis_agent.knowledge_catalog import Approval, ServiceFact
    repository.append(fact_id="service:outro", expected_version=None, status=Approval.APPROVED,
                      data=ServiceFact(service=Service(id="outro", name="Outro", aliases=("escova",))),
                      source="importação antiga fictícia", reviewed_by="teste", author="teste")
    details = [p["detail"] for p in admin.get("/knowledge").json()["pending"]]
    assert any(d.startswith("Conflito: 'escova'") for d in details)


def test_preview_is_flagged_as_simulated(admin):
    body = admin.post("/knowledge/preview", {"question": "Quanto custa o corte?"}).json()
    assert body["simulated"] is True and "R$ 45,00" in body["reply"]


def test_framework_errors_are_portuguese_and_do_not_echo_input(admin):
    response = admin.get("/conversations/wa-x/messages?before=<script>")
    assert response.status_code == 422 and response.json() == {"detail": "Dados da requisição inválidos."}
    assert "<script>" not in response.text
    missing = admin.get("/rota-que-nao-existe")
    assert missing.status_code == 404 and missing.json() == {"detail": "Recurso não encontrado."}
    assert admin.client.delete("/api/panel/overview", headers=admin.headers()).status_code == 405


def test_fictitious_message_command_only_works_on_demo_profiles_and_fake_numbers(tmp_path):
    from rocha_reis_agent.ops.backup import BackupError
    from rocha_reis_agent.ops.demo import build_demo, send_fictitious
    with pytest.raises(BackupError, match="não encontrado"):
        send_fictitious(tmp_path / "nada", "Oi")
    profile = tmp_path / "demo"
    build_demo(profile)
    for number in ("5511999999999", "abc", "+5511900000201"):
        with pytest.raises(BackupError, match="fictícios"):
            send_fictitious(profile, "Oi", sender=number)
    assert send_fictitious(profile, "Quero falar com uma pessoa", sender="5511900000299")["enviada_de"] == "5511900000299"
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(profile / "dados" / "demo.synthetic.sqlite3")) as connection:
        assert connection.execute("SELECT count(*) FROM channel_inbox WHERE status='PENDING'").fetchone() == (1,)


def test_same_key_race_after_version_check_is_idempotent_not_an_error(tmp_path):
    """Corrida real (achada na Fase 11): a outra requisição grava a mesma chave entre a verificação
    e o commit; o UNIQUE da outbox reverte esta e a resposta deve ser o mesmo envio, não erro."""
    from uuid import uuid4
    from panel_support import conversation_id, customer
    app = create_panel_app(panel_settings(tmp_path))
    add_user(app, "ana.teste")
    ana = Panel(app)
    ana.login("ana.teste")
    customer(tmp_path, f"wamid.{uuid4().hex}", "Quero falar com uma pessoa")
    cid = conversation_id(ana, "waiting")
    ana.post(f"/conversations/{cid}/assume")
    key = uuid4().hex
    first = ana.post(f"/conversations/{cid}/reply", {"text": "Mensagem única", "idempotency_key": key}).json()
    assert app.state.panel_service.channel is app.state.channel_store
    real = app.state.channel_store.operator_message
    calls = []

    def stale_once(value):  # a 1ª verificação "não vê" a gravação concorrente
        calls.append(value)
        return None if len(calls) == 1 else real(value)

    app.state.channel_store.operator_message = stale_once
    try:
        second = ana.post(f"/conversations/{cid}/reply", {"text": "Mensagem única", "idempotency_key": key})
    finally:
        app.state.channel_store.operator_message = real
    assert second.status_code == 200 and second.json() == {**first, "duplicate": True}
    assert sum(r["origin"] == "operator" for r in app.state.channel_store.outbox_rows()) == 1
