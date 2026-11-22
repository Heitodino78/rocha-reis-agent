"""Fase 8 (portado na 9B) — integração SEM mocks de persistência: webhook → inbox → núcleo →
adaptador Gemini (HTTP em memória) → validação → outbox → transporte fake, com conhecimento
gerido no banco (revisões aprovadas). Itens 16, 29–34 e 38 da lista de testes."""

import json
import sqlite3
from contextlib import closing

import httpx2

from rocha_reis_agent.ai.fake import FakeAIProvider
from rocha_reis_agent.ai.gemini_provider import GeminiConfig, GeminiProvider, create_client
from rocha_reis_agent.application.handoff import HandoffAction
from rocha_reis_agent.bootstrap import build_agent, configured_channel, configured_limits, configured_repository, queue_policy
from rocha_reis_agent.channels.fake_transport import FakeWhatsAppTransport
from rocha_reis_agent.channels.service import InboundProcessor, OutboxDispatcher, drain
from rocha_reis_agent.domain.contracts import ConversationState as State
from rocha_reis_agent.knowledge_catalog import Approval, KnowledgeScope, PriceFact
from rocha_reis_agent.persistence.sqlite_ai import SQLiteUsageLedger
from rocha_reis_agent.persistence.sqlite_knowledge import SQLiteKnowledgeRepository
from gemini_support import KEY, MODEL, ai_request_from, body
from whatsapp_support import CLIENT_A, CLIENT_B, catalog, envelope, settings, text_message


class Model:
    """'Modelo' em HTTP simulado: classificação determinística + abertura cordial."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.calls += 1
        proposal = FakeAIProvider().generate(ai_request_from(request), timeout_seconds=5).model_dump(mode="json")
        proposal["preface"] = "Claro, posso ajudar!"
        return httpx2.Response(200, json=body(proposal, usage=(400, 90, 30)))


def build(tmp_path, model, *, init=True, **limits):
    config = settings(tmp_path, knowledge_source="database")
    repository = configured_repository(config)
    store, ingestor = configured_channel(config, repository)
    knowledge = SQLiteKnowledgeRepository(repository)
    if init:
        knowledge.initialize(KnowledgeScope.SYNTHETIC)
        knowledge.import_catalog(catalog(), author="teste")
    client = create_client(KEY, http_client=httpx2.Client(transport=httpx2.MockTransport(model)))
    ledger = SQLiteUsageLedger(repository)
    provider = GeminiProvider(GeminiConfig(model=MODEL, max_retries=0, **limits), client, ledger)
    agent = build_agent(None, configured_limits(config), conversation_repository=repository, provider=provider,
                        prompt_version="v2", knowledge_repository=knowledge)
    policy = queue_policy(config)
    transport = FakeWhatsAppTransport()
    return dict(repository=repository, store=store, ingestor=ingestor, knowledge=knowledge, agent=agent, ledger=ledger,
                transport=transport, inbound=InboundProcessor(store, agent, policy),
                outbound=OutboxDispatcher(store, transport, policy))


def say(s, wamid, text, sender=CLIENT_A):
    s["ingestor"].ingest(envelope(text_message(wamid, text, sender)))
    return drain(s["inbound"], s["outbound"], "w")


def bodies(s):
    return [d.body for d in s["transport"].deliveries]


def test_end_to_end_reply_is_backend_composed_persisted_and_sent_through_outbox(tmp_path):
    model = Model()
    s = build(tmp_path, model)
    assert say(s, "wamid.1", "Quanto custa o corte?") == ["replied", "ACCEPTED"]
    (reply,) = bodies(s)
    assert reply == ("[SIMULAÇÃO — DADOS FICTÍCIOS] Claro, posso ajudar! Corte de teste: R$ 45,00. "
                     "Valor fictício, exclusivo desta demonstração.")
    (row,) = s["store"].outbox_rows()
    assert row["status"] == "ACCEPTED" and row["origin"] == "agent"
    history = s["agent"].conversations.snapshot(s["store"].inbox_rows()[0]["conversation_id"]).history
    assert history[-1].prompt_version == "v2" and history[-1].fact_references  # proveniência persistida
    (usage,) = s["ledger"].summary()["rows"]
    assert usage["outcome"] == "ok" and usage["cost_micros"] > 0 and model.calls == 1


def test_duplicate_webhook_does_not_call_the_model_twice_and_survives_restart(tmp_path):
    model = Model()
    s = build(tmp_path, model)
    say(s, "wamid.1", "Vocês fazem escova?")
    assert say(s, "wamid.1", "Vocês fazem escova?") == []  # inbox deduplica o wamid
    assert model.calls == 1 and len(s["store"].outbox_rows()) == 1
    restarted = build(tmp_path, model, init=False)  # novo processo, mesmo banco
    assert say(restarted, "wamid.2", "E quanto custa?") == ["replied", "ACCEPTED"]  # contexto sobreviveu
    assert "Não tenho preço confirmado para Escova de teste" in bodies(restarted)[0]
    assert model.calls == 2


def test_price_under_review_is_never_answered_with_the_old_value(tmp_path):
    s = build(tmp_path, Model())
    knowledge = s["knowledge"]
    knowledge.append(fact_id="price:corte", expected_version=1, status=Approval.DRAFT,
                     data=PriceFact(service_id="corte", amount="50.00"), source="Tabela sintética nova",
                     reviewed_by=None, author="admin-teste")
    say(s, "wamid.1", "Quanto custa o corte?")
    assert "R$" not in bodies(s)[-1] and "Não tenho preço confirmado" in bodies(s)[-1]
    draft = knowledge.latest("price:corte")
    knowledge.append(fact_id="price:corte", expected_version=2, status=Approval.APPROVED, data=draft.data,
                     source=draft.source, reviewed_by="Admin fictício (painel)", author="admin-teste")
    say(s, "wamid.2", "Quanto custa o corte?")
    assert "R$ 50,00" in bodies(s)[-1] and "R$ 45" not in bodies(s)[-1]


def test_human_states_never_call_the_model_nor_enqueue_automatic_replies(tmp_path):
    model = Model()
    s = build(tmp_path, model)
    assert say(s, "wamid.1", "Não quero falar com robô") == ["replied", "ACCEPTED"]  # confirmação única
    cid = s["store"].inbox_rows()[0]["conversation_id"]
    s["agent"].operator_action(cid, HandoffAction.ASSUME, operator_id="operador-local")
    assert say(s, "wamid.2", "Quanto custa o corte?") == ["recorded_without_reply"]
    assert model.calls == 0 and len(s["store"].outbox_rows()) == 1
    s["agent"].operator_action(cid, HandoffAction.RESOLVE, operator_id="operador-local")
    assert say(s, "wamid.3", "Oi?") == ["recorded_without_reply"] and model.calls == 0  # ADR-134 (22/11): cliente voltou → fila; re-encerrar antes da retomada explícita
    s["agent"].operator_action(cid, HandoffAction.RESOLVE, operator_id="operador-local")
    assert model.calls == 0 and len(s["store"].outbox_rows()) == 1
    s["agent"].operator_action(cid, HandoffAction.RESUME, operator_id="operador-local")
    assert say(s, "wamid.4", "Quanto custa o corte?") == ["replied", "ACCEPTED"] and model.calls == 1
    assert say(s, "wamid.5", "Quanto custa o corte?", sender=CLIENT_B)[0] == "replied"  # outro cliente intocado


def test_budget_exhaustion_pauses_safely_through_the_channel(tmp_path):
    s = build(tmp_path, Model(), max_requests_per_day=1)
    say(s, "wamid.1", "Quanto custa o corte?")
    say(s, "wamid.2", "Quanto custa a escova?", sender=CLIENT_B)
    assert "R$" not in bodies(s)[-1] and "Nenhum atendente real foi notificado" in bodies(s)[-1]
    with closing(sqlite3.connect(s["repository"].path)) as connection:
        assert connection.execute("SELECT count(*) FROM ai_usage").fetchone()[0] == 1  # bloqueio antes da chamada
