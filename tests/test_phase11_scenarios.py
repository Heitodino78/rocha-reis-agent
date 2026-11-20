"""Fase 11 — os 10 cenários de atendimento realistas, de ponta a ponta e no MESMO banco:
webhook (ingestão) → inbox → núcleo com conhecimento APROVADO no banco → outbox → transporte
fake, com o painel (API HTTP real) para as pessoas e para o conhecimento. Dados fictícios."""

from contextlib import closing
import sqlite3
from uuid import uuid4

import pytest

from panel_support import Panel, add_user, create_panel_app, panel_settings
from rocha_reis_agent.bootstrap import build_agent, configured_channel, configured_limits, configured_repository, queue_policy
from rocha_reis_agent.channels.fake_transport import FakeWhatsAppTransport
from rocha_reis_agent.channels.service import InboundProcessor, OutboxDispatcher, drain
from rocha_reis_agent.knowledge_catalog import KnowledgeScope
from rocha_reis_agent.persistence.sqlite_knowledge import SQLiteKnowledgeRepository
from whatsapp_support import catalog, envelope, text_message

ANA_CLIENT, BIA_CLIENT, CARLA, DORA, ELIS = (f"55119000031{i:02d}" for i in range(5))


class Salon:
    """Painel + pipeline do canal sobre o mesmo arquivo; `restart()` simula novo processo."""

    def __init__(self, tmp_path) -> None:
        self.tmp_path = tmp_path
        self.settings = panel_settings(tmp_path, knowledge_source="database")
        self.app = create_panel_app(self.settings)
        knowledge = SQLiteKnowledgeRepository(self.app.state.conversation_repository)
        knowledge.initialize(KnowledgeScope.SYNTHETIC)
        knowledge.import_catalog(catalog(), author="fixture-sintetica")
        self.ids = {login: add_user(self.app, login, role) for login, role in
                    (("admin.teste", "ADMIN"), ("ana.teste", "OPERATOR"), ("bia.teste", "OPERATOR"))}
        self.panels = {}
        for login in self.ids:
            self.panels[login] = Panel(self.app)
            self.panels[login].login(login)
        self.restart()

    def restart(self, transport: FakeWhatsAppTransport | None = None) -> None:
        repository = configured_repository(self.settings)
        self.store, self.ingestor = configured_channel(self.settings, repository)
        agent = build_agent(None, configured_limits(self.settings), conversation_repository=repository,
                            knowledge_repository=SQLiteKnowledgeRepository(repository))
        policy = queue_policy(self.settings)
        self.transport = transport or FakeWhatsAppTransport()
        self.inbound = InboundProcessor(self.store, agent, policy)
        self.outbound = OutboxDispatcher(self.store, self.transport, policy)

    def say(self, sender: str, text: str) -> list[str]:
        self.ingestor.ingest(envelope(text_message(f"wamid.{uuid4().hex}", text, sender)))
        return drain(self.inbound, self.outbound, "salon")

    def last_reply(self) -> str:
        return self.transport.deliveries[-1].body

    def cid(self, panel: Panel, filter_key: str) -> str:
        items = panel.get(f"/conversations?filter={filter_key}").json()["items"]
        assert len(items) == 1, items
        return items[0]["id"]


@pytest.fixture
def salon(tmp_path):
    return Salon(tmp_path)


def test_1_simple_question_answered_from_approved_knowledge(salon):
    assert salon.say(ANA_CLIENT, "Quanto custa o corte?") == ["replied", "ACCEPTED"]
    assert "Corte de teste: R$ 45,00" in salon.last_reply()


def test_2_missing_price_is_never_invented(salon):
    salon.say(ANA_CLIENT, "Quanto custa a escova?")
    reply = salon.last_reply()
    assert "Não tenho preço confirmado para Escova de teste" in reply and "R$" not in reply


def test_3_booking_returns_only_the_approved_link_without_confirming(salon):
    salon.say(ANA_CLIENT, "Quero agendar um corte para sábado às 10h, tem horário?")
    reply = salon.last_reply().lower()
    assert "https://example.invalid/booksy-sintetico" in reply
    for forbidden in ("confirmado", "agendado para", "está reservado", "temos horário", "disponível às"):
        assert forbidden not in reply


def test_4_human_request_goes_to_queue_and_operator_answers(salon):
    ana = salon.panels["ana.teste"]
    assert salon.say(CARLA, "Quero falar com uma pessoa, por favor") == ["replied", "ACCEPTED"]
    cid = salon.cid(ana, "waiting")
    assert ana.post(f"/conversations/{cid}/assume").status_code == 204
    assert ana.post(f"/conversations/{cid}/reply", {"text": "Oi! Sou a Ana.", "idempotency_key": uuid4().hex}
                    ).status_code == 200
    assert drain(salon.inbound, salon.outbound, "salon") == ["ACCEPTED"]
    assert salon.last_reply() == "Oi! Sou a Ana."
    salon.say(CARLA, "Quanto custa o corte?")  # durante o atendimento humano o robô não responde
    assert salon.last_reply() == "Oi! Sou a Ana."
    detail = ana.get(f"/conversations/{cid}").json()
    assert detail["state"] == "HUMAN_ACTIVE" and detail["messages"][-1]["text"] == "Quanto custa o corte?"
    assert "new_message" in [n["type"] for n in ana.get("/notifications").json()["items"]]


def test_5_complaint_is_escalated_to_people(salon):
    salon.say(DORA, "Estou muito insatisfeita, o atendimento de ontem foi péssimo")
    admin = salon.panels["admin.teste"]
    items = admin.get("/conversations?filter=waiting").json()["items"]
    assert len(items) == 1 and items[0]["needs_human"]
    assert "human_requested" in [n["type"] for n in admin.get("/notifications").json()["items"]]


def test_6_transfer_changes_owner_consistently(salon):
    ana, bia = salon.panels["ana.teste"], salon.panels["bia.teste"]
    salon.say(CARLA, "Quero um atendente")
    cid = salon.cid(ana, "waiting")
    ana.post(f"/conversations/{cid}/assume")
    assert ana.post(f"/conversations/{cid}/transfer", {"target_user_id": salon.ids["bia.teste"]}).status_code == 204
    assert ana.get(f"/conversations/{cid}").status_code == 404  # saiu do escopo da Ana
    detail = bia.get(f"/conversations/{cid}").json()
    assert detail["assigned"]["id"] == salon.ids["bia.teste"]
    assert ana.post(f"/conversations/{cid}/reply", {"text": "x", "idempotency_key": uuid4().hex}).status_code == 404
    assert bia.post(f"/conversations/{cid}/reply", {"text": "Bia aqui.", "idempotency_key": uuid4().hex}
                    ).status_code == 200


def test_7_send_failure_is_shown_and_not_retried(salon):
    ana = salon.panels["ana.teste"]
    salon.say(CARLA, "Quero falar com uma pessoa")
    cid = salon.cid(ana, "waiting")
    ana.post(f"/conversations/{cid}/assume")
    ana.post(f"/conversations/{cid}/reply", {"text": "Vai falhar.", "idempotency_key": uuid4().hex})
    salon.restart(FakeWhatsAppTransport("reject"))
    drain(salon.inbound, salon.outbound, "salon")
    assert ana.get(f"/conversations/{cid}").json()["messages"][-1]["delivery"]["key"] == "failed"
    assert drain(salon.inbound, salon.outbound, "salon") == [] and len(salon.transport.deliveries) == 1
    assert "send_failed" in [n["type"] for n in ana.get("/notifications").json()["items"]]


def test_8_restart_preserves_history_states_and_assignment(salon):
    ana = salon.panels["ana.teste"]
    salon.say(ANA_CLIENT, "Quanto custa o corte?")
    salon.say(CARLA, "Quero falar com uma pessoa")
    cid = salon.cid(ana, "waiting")
    ana.post(f"/conversations/{cid}/assume")
    before = ana.get(f"/conversations/{cid}").json()
    salon.restart()
    reopened = create_panel_app(salon.settings)  # novo processo do painel, mesmo banco
    again = Panel(reopened)
    again.login("ana.teste")
    after = again.get(f"/conversations/{cid}").json()
    assert after["state"] == "HUMAN_ACTIVE" and after["assigned"] == before["assigned"]
    assert after["messages"] == before["messages"]
    assert salon.say(ELIS, "Quanto custa o corte?") == ["replied", "ACCEPTED"]  # pipeline segue após o reinício


def test_9_new_approved_version_replaces_the_old_one(salon):
    admin = salon.panels["admin.teste"]
    salon.say(ANA_CLIENT, "Quanto custa o corte?")
    assert "R$ 45,00" in salon.last_reply()
    draft = admin.post("/knowledge/drafts", {"kind": "price", "key": "corte", "fields": {"amount": "52,00"},
                                             "source": "Tabela fictícia nova", "expected_version": 1})
    assert draft.status_code == 201
    salon.say(BIA_CLIENT, "Quanto custa o corte?")
    assert "R$" not in salon.last_reply()  # em revisão: nem o valor antigo nem o novo
    version = draft.json()["version"]
    assert admin.post("/knowledge/approve", {"fact_id": "price:corte", "version": version}).status_code == 200
    salon.say(DORA, "Quanto custa o corte?")
    assert "R$ 52,00" in salon.last_reply() and "45" not in salon.last_reply()
    latest = admin.get("/knowledge/history?fact_id=price:corte").json()[-1]
    assert admin.post("/knowledge/revoke", {"fact_id": "price:corte", "version": latest["version"]}).status_code == 200
    salon.say(ELIS, "Quanto custa o corte?")
    assert "R$" not in salon.last_reply()  # revogado deixa de ser usado


def test_10_operator_cannot_run_admin_actions(salon):
    ana = salon.panels["ana.teste"]
    attempts = [
        ana.post("/knowledge/drafts", {"kind": "price", "key": "corte", "fields": {"amount": "1,00"},
                                       "source": "x", "expected_version": 1}),
        ana.post("/knowledge/approve", {"fact_id": "price:corte", "version": 1}),
        ana.put("/settings", {"operators_can_resume": True}),
        ana.post("/users", {"login": "novo.teste", "display_name": "Novo", "role": "ADMIN",
                            "password": "Senha-Ficticia-2026"}),
        ana.post("/dispatch/halt", {"reason": "teste"}),
        ana.get("/audit"), ana.get("/system"), ana.get("/retired-contacts"),
    ]
    assert [r.status_code for r in attempts] == [403] * len(attempts)
    salon.say(ANA_CLIENT, "Quanto custa o corte?")
    assert "R$ 45,00" in salon.last_reply()  # nada mudou
    with closing(sqlite3.connect(salon.app.state.conversation_repository.path)) as connection:
        denied = connection.execute("SELECT count(*) FROM panel_audit WHERE outcome='denied'").fetchone()[0]
    assert denied >= 1


def test_synthetic_knowledge_can_never_become_official(salon):
    knowledge = SQLiteKnowledgeRepository(salon.app.state.conversation_repository)
    with pytest.raises(Exception):
        knowledge.initialize(KnowledgeScope.OFFICIAL)
    assert knowledge.scope() == KnowledgeScope.SYNTHETIC
    salon.say(ANA_CLIENT, "Quanto custa o corte?")
    assert salon.last_reply().startswith("[SIMULAÇÃO — DADOS FICTÍCIOS]")
