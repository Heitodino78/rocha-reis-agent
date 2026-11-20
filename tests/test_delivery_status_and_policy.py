"""Status de entrega (correlação confiável, ordem, duplicatas) e política de envio."""

from datetime import datetime, timedelta, timezone

import pytest

from rocha_reis_agent.channels.fake_transport import FakeWhatsAppTransport
from rocha_reis_agent.channels.send_policy import SendContext, authorize_free_text
from rocha_reis_agent.channels.service import drain
from whatsapp_support import Clock, envelope, stack, status, text_message


def sent(tmp_path, **kwargs):
    s = stack(tmp_path, **kwargs)
    s.ingestor.ingest(envelope(text_message("wamid.1", "Quanto custa o corte?")))
    drain(s.inbound, s.outbound, "w1")
    return s, s.store.outbox_rows()[0]


def deliver_status(s, *items):
    s.ingestor.ingest(envelope(*items))
    return drain(s.inbound, s.outbound, "w1")


# ---------------------------------------------------------------- status (21–26)
def test_accepted_is_not_delivery_and_normal_progression_is_recorded(tmp_path):
    s, row = sent(tmp_path)
    assert row["status"] == "ACCEPTED" and row["delivery_status"] is None  # 26
    pid = row["provider_message_id"]
    assert deliver_status(s, status(pid, "sent"), status(pid, "delivered"), status(pid, "read")) == [
        "status_sent", "status_delivered", "status_read"]
    final = s.store.outbox_rows()[0]
    assert final["delivery_status"] == "read" and final["delivery_updated_at"] and final["status_anomalies"] == 0


def test_duplicate_status_is_deduplicated(tmp_path):
    s, row = sent(tmp_path)
    payload = envelope(status(row["provider_message_id"], "delivered"))
    s.ingestor.ingest(payload)
    assert s.ingestor.ingest(payload).duplicates == 1
    assert drain(s.inbound, s.outbound, "w1") == ["status_delivered"]


def test_out_of_order_status_never_regresses(tmp_path):
    s, row = sent(tmp_path)
    pid = row["provider_message_id"]
    assert deliver_status(s, status(pid, "read")) == ["status_read"]
    assert deliver_status(s, status(pid, "delivered"), status(pid, "sent")) == [
        "status_out_of_order_ignored", "status_out_of_order_ignored"]
    final = s.store.outbox_rows()[0]
    assert final["delivery_status"] == "read" and final["status_anomalies"] == 2


def test_uncorrelated_and_coincidental_statuses_are_not_associated(tmp_path):
    s, row = sent(tmp_path)
    assert deliver_status(s, status("wamid.OTHER", "delivered")) == ["status_unmatched"]
    # Mesmo horário/conteúdo não importa: sem ID ou callback confiável não há associação.
    assert s.store.outbox_rows()[0]["delivery_status"] is None


def test_failure_after_acceptance_is_recorded_without_changing_request_state(tmp_path):
    s, row = sent(tmp_path)
    pid = row["provider_message_id"]
    assert deliver_status(s, status(pid, "sent"), status(pid, "failed")) == ["status_sent", "status_failed"]
    final = s.store.outbox_rows()[0]
    assert final["status"] == "ACCEPTED" and final["delivery_status"] == "failed"
    assert final["delivery_error_code"] == 131026
    assert deliver_status(s, status(pid, "delivered")) == ["status_out_of_order_ignored"]


def test_failed_after_read_is_contradiction_and_ignored(tmp_path):
    s, row = sent(tmp_path)
    pid = row["provider_message_id"]
    deliver_status(s, status(pid, "read"))
    assert deliver_status(s, status(pid, "failed")) == ["status_out_of_order_ignored"]
    assert s.store.outbox_rows()[0]["delivery_status"] == "read"


def test_callback_cannot_reopen_a_rejected_send(tmp_path):
    s, row = sent(tmp_path, transport=FakeWhatsAppTransport("reject"))
    assert row["status"] == "FAILED"
    assert deliver_status(s, status("wamid.FORJADO", "delivered", callback=row["id"])) == ["status_unmatched"]
    assert s.store.outbox_rows()[0]["status"] == "FAILED"


# -------------------------------------------------------- política de envio (27–30)
NOW = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)


def context(**changes):
    values = dict(now=NOW, window_opened_at=NOW - timedelta(hours=1), window_seconds=23 * 3600,
                  required_state="AUTO", current_state="AUTO", required_session="s1", current_session="s1",
                  retired=False, has_recipient=True, has_body=True)
    values.update(changes)
    return SendContext(**values)


def test_valid_window_is_authorized():
    assert authorize_free_text(context()).allowed


@pytest.mark.parametrize("changes,reason", [
    ({"window_opened_at": NOW - timedelta(hours=23, seconds=1)}, "outside_service_window"),
    ({"window_opened_at": NOW - timedelta(days=3)}, "outside_service_window"),
    ({"window_opened_at": None}, "insufficient_evidence"),
    ({"window_opened_at": NOW + timedelta(hours=1)}, "insufficient_evidence"),
    ({"window_opened_at": datetime(2026, 11, 19, 11)}, "insufficient_evidence"),
    ({"window_seconds": 25 * 3600}, "insufficient_evidence"),
    ({"has_recipient": False}, "insufficient_evidence"),
    ({"has_body": False}, "insufficient_evidence"),
    ({"current_state": "HUMAN_ACTIVE"}, "conversation_state_changed"),
    ({"current_session": "s2"}, "conversation_state_changed"),
    ({"current_state": None, "current_session": None}, "conversation_missing"),
    ({"retired": True}, "conversation_deleted"),
])
def test_unauthorized_or_insufficient_evidence_blocks_sending(changes, reason):
    decision = authorize_free_text(context(**changes))
    assert not decision.allowed and decision.reason == reason


def test_small_provider_clock_skew_is_tolerated():
    assert authorize_free_text(context(window_opened_at=NOW + timedelta(minutes=4))).allowed


def test_window_is_reevaluated_before_each_attempt_after_waiting_in_outbox(tmp_path):
    clock = Clock()
    s = stack(tmp_path, clock=clock, transport=FakeWhatsAppTransport(script=["not_sent", "accept"]))
    s.ingestor.ingest(envelope(text_message("wamid.1", "Olá")))
    s.inbound.process_one("w1")
    assert s.outbound.dispatch_one("w1") == "RETRY"
    clock.advance(23 * 3600 + 2)  # timestamp da Meta tem resolução de segundos
    assert s.outbound.dispatch_one("w1") is None
    assert s.store.outbox_rows()[0]["outcome"] == "outside_service_window"
    assert len(s.transport.deliveries) == 1


def test_delayed_webhook_uses_older_source_timestamp(tmp_path):
    """Evento entregue com atraso: a janela conta do timestamp da Meta, não do recebimento."""
    s = stack(tmp_path)
    old = str(int((datetime.now(timezone.utc) - timedelta(hours=23, minutes=30)).timestamp()))
    s.ingestor.ingest(envelope(text_message("wamid.late", "Quanto custa o corte?", ts=old)))
    assert drain(s.inbound, s.outbound, "w1") == ["replied"]
    assert s.store.outbox_rows()[0]["outcome"] == "outside_service_window" and s.transport.deliveries == []


def test_delivered_then_late_sent_does_not_regress(tmp_path):
    s, row = sent(tmp_path)
    pid = row["provider_message_id"]
    assert deliver_status(s, status(pid, "delivered"), status(pid, "sent")) == [
        "status_delivered", "status_out_of_order_ignored"]
    final = s.store.outbox_rows()[0]
    assert final["delivery_status"] == "delivered" and final["status_anomalies"] == 1
