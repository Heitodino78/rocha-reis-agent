"""Entradas inválidas, falhas de adaptadores e substituição do detector."""

from datetime import datetime, timezone
import logging

import pytest

from rocha_reis_agent.domain.contracts import (
    ConversationState, Detection, InboundMessage, Intent, InvalidInput,
)


@pytest.mark.parametrize("text", [None, 123, [], "", " \t\n", "x" * 2001, "Olá\x1b[31m", "Olá\x00"])
def test_empty_invalid_or_oversized_message_is_rejected(text):
    with pytest.raises(InvalidInput):
        InboundMessage("evento-1", "cliente-a", text, datetime.now(timezone.utc))


@pytest.mark.parametrize("field,value", [
    ("event_id", ""), ("event_id", "bad id"), ("conversation_id", "../cliente"),
    ("conversation_id", "x" * 65), ("received_at", datetime(2026, 11, 19)),
    ("received_at", "invalid"),
])
def test_invalid_identifiers_and_naive_timestamps_are_rejected(field, value):
    values = dict(event_id="evento-1", conversation_id="cliente-a", text="Olá", received_at=datetime.now(timezone.utc))
    values[field] = value
    with pytest.raises(InvalidInput):
        InboundMessage(**values)


def test_message_whitespace_is_normalized_without_losing_accents():
    message = InboundMessage("evento-1", "cliente-a", "  Olá!\n Quanto\t custa?  ", datetime.now(timezone.utc))
    assert message.text == "Olá! Quanto custa?"
    assert "Olá" not in repr(message)


def test_message_at_limit_is_accepted(send):
    assert send("x" * 2000).reply


@pytest.mark.parametrize("adapter", ["detector", "knowledge", "responses"])
def test_adapter_failure_pauses_safely_without_leaking(agent, send, caplog, adapter):
    marker = "SYNTHETIC_PRIVATE_PROVIDER_FAILURE"

    class BrokenAdapter:
        def detect(self, *args):
            raise RuntimeError(marker)

        def get(self):
            raise RuntimeError(marker)

        def render(self, *args):
            raise RuntimeError(marker)

    setattr(agent, adapter, BrokenAdapter())
    with caplog.at_level(logging.ERROR):
        result = send("Olá", event_id="failure-1")
        duplicate = send("Olá", event_id="failure-1")
    assert result.state == ConversationState.WAITING_HUMAN
    assert "Nenhum atendente real foi notificado" in result.reply
    assert marker not in result.reply
    assert marker not in caplog.text
    assert duplicate.duplicate and duplicate.reply is None
    assert len(caplog.records) == 1
    assert caplog.records[0].getMessage() == "simulation_processing_failed"
    assert caplog.records[0].exc_info is None


def test_detector_can_be_replaced_without_changing_service(agent, send):
    class FixedDetector:
        def detect(self, text, knowledge):
            return Detection((Intent.GREETING,))

    agent.detector = FixedDetector()
    result = send("arbitrary synthetic text")
    assert result.intents == (Intent.GREETING,)
    assert "Olá!" in result.reply
