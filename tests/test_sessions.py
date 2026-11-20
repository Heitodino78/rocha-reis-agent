"""Idempotência local, sessões, concorrência, contexto e limites de memória."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from rocha_reis_agent.domain.contracts import InboundMessage, SimulationError
from rocha_reis_agent.domain.conversations import SimulationLimits
from rocha_reis_agent.simulator import build_agent


def test_repeated_event_has_one_reply_and_no_duplicate_history(agent, send):
    first = send("Quanto custa o corte?", event_id="evento-1")
    second = send("Quanto custa o corte?", event_id="evento-1")
    assert first.reply
    assert second.duplicate
    assert second.reply is None
    assert len(agent.conversations.snapshot("cliente-a").history) == 2


def test_same_content_with_new_event_id_is_a_new_message(agent, send):
    assert send("Olá", event_id="evento-1").reply
    assert send("Olá", event_id="evento-2").reply
    assert len(agent.conversations.snapshot("cliente-a").history) == 4


def test_reused_event_id_with_changed_payload_is_rejected_atomically(agent, send):
    send("Olá", event_id="evento-1")
    before = agent.conversations.snapshot("cliente-a")
    with pytest.raises(SimulationError, match="conteúdo diferente"):
        send("Quero humano", event_id="evento-1")
    assert agent.conversations.snapshot("cliente-a") == before


def test_concurrent_duplicate_delivery_is_serialized(agent):
    message = InboundMessage("evento-1", "cliente-a", "Olá", datetime.now(timezone.utc))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(agent.handle, [message] * 8))
    assert sum(result.reply is not None for result in results) == 1
    assert sum(result.duplicate for result in results) == 7
    assert len(agent.conversations.snapshot("cliente-a").history) == 2


def test_snapshot_mutation_does_not_change_saved_conversation(agent, send):
    send("Quero informações sobre corte", event_id="evento-1")
    snapshot = agent.conversations.snapshot("cliente-a")
    snapshot.history.clear()
    snapshot.processed_events.clear()
    assert len(agent.conversations.snapshot("cliente-a").history) == 2
    assert send("Quero informações sobre corte", event_id="evento-1").duplicate


def test_close_blocks_messages_and_reset_starts_new_session(agent, send):
    send("Quero informações sobre corte", event_id="evento-1")
    session = agent.conversations.snapshot("cliente-a").session_id
    agent.conversations.close("cliente-a")
    with pytest.raises(SimulationError, match="Sessão encerrada"):
        send("Olá")
    agent.conversations.reset("cliente-a")
    fresh = agent.conversations.snapshot("cliente-a")
    assert fresh.session_id != session
    assert not fresh.history and not fresh.service_context and not fresh.processed_events
    assert not send("Olá", event_id="evento-1").duplicate


def test_start_does_not_silently_reset_existing_session(agent, send):
    send("Olá")
    with pytest.raises(SimulationError, match="Conversa existente"):
        agent.conversations.start("cliente-a")
    assert len(agent.conversations.snapshot("cliente-a").history) == 2


def test_history_is_bounded_without_evicting_processed_events(knowledge):
    agent = build_agent(knowledge, SimulationLimits(max_history_entries=3))
    for index in range(5):
        agent.handle(InboundMessage(f"evento-{index}", "cliente-a", "Olá", datetime.now(timezone.utc)))
    conversation = agent.conversations.snapshot("cliente-a")
    assert len(conversation.history) == 3
    assert len(conversation.processed_events) == 5
    assert agent.handle(InboundMessage("evento-0", "cliente-a", "Olá", datetime.now(timezone.utc))).duplicate


def test_session_event_limit_rejects_new_events_but_keeps_deduplication(knowledge):
    agent = build_agent(knowledge, SimulationLimits(max_events_per_session=1))
    first = InboundMessage("evento-1", "cliente-a", "Olá", datetime.now(timezone.utc))
    agent.handle(first)
    with pytest.raises(SimulationError, match="Limite de eventos"):
        agent.handle(InboundMessage("evento-2", "cliente-a", "Olá", datetime.now(timezone.utc)))
    assert agent.handle(first).duplicate


def test_conversation_limit_does_not_overwrite_other_customers(knowledge):
    agent = build_agent(knowledge, SimulationLimits(max_conversations=1))
    agent.conversations.start("cliente-a")
    with pytest.raises(SimulationError, match="Limite de conversas"):
        agent.conversations.start("cliente-b")
    assert agent.conversations.snapshot("cliente-a").conversation_id == "cliente-a"


def test_service_context_expires_after_configured_turns(agent, send):
    agent.conversations.limits = SimulationLimits(context_turns=2)
    send("Quero informações sobre corte")
    send("Olá")
    send("Bom dia")
    assert "De qual serviço" in send("E quanto custa?").reply
