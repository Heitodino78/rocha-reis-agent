"""Outbox transacional: commit antes do envio, incerteza, retries, leases e concorrência."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Barrier

import pytest

from rocha_reis_agent.channels.contracts import SendAccepted
from rocha_reis_agent.channels.fake_transport import FakeWhatsAppTransport
from rocha_reis_agent.domain.contracts import StorageError
from rocha_reis_agent.persistence.sqlite_channel import LeaseLost
from whatsapp_support import Clock, envelope, stack, status, text_message

ROOT = Path(__file__).resolve().parents[1]
WORKER = Path(__file__).with_name("channel_worker.py")


def queued(s, wamid="wamid.1", text="Quanto custa o corte?"):
    s.ingestor.ingest(envelope(text_message(wamid, text)))
    assert s.inbound.process_one("w1") == "replied"
    return s.store.outbox_rows()[-1]


def test_pending_reply_is_persisted_with_conversation_in_same_commit(tmp_path):
    s = stack(tmp_path)
    row = queued(s)
    assert row["status"] == "PENDING" and "R$ 45,00" in row["body"] and row["attempts"] == 0
    assert json.loads(row["recipient"])["wa_id"]
    history = s.agent.conversations.snapshot(row["conversation_id"]).history
    assert history[-1].text == row["body"] and row["required_state"] == "AUTO"
    assert s.transport.deliveries == []  # nada enviado dentro da transação de negócio


def test_sending_state_is_committed_before_transport_call(tmp_path):
    seen = []
    s = stack(tmp_path)

    def inspect(message):
        other = stack(tmp_path)  # outra conexão enxerga somente dados confirmados
        seen.append(other.store.outbox_rows()[0]["status"])

    s.outbound.transport = FakeWhatsAppTransport(on_send=inspect)
    queued(s)
    assert s.outbound.dispatch_one("w1") == "ACCEPTED"
    assert seen == ["SENDING"]


def test_restart_between_commit_and_send_delivers_pending_reply(tmp_path):
    first = stack(tmp_path)
    queued(first)
    del first
    second = stack(tmp_path)
    assert second.outbound.dispatch_one("w2") == "ACCEPTED"
    assert len(second.transport.deliveries) == 1


def test_retryable_failure_uses_backoff_then_succeeds_once(tmp_path):
    clock = Clock()
    s = stack(tmp_path, clock=clock, transport=FakeWhatsAppTransport(script=["not_sent", "throttle", "accept"]))
    queued(s)
    assert s.outbound.dispatch_one("w1") == "RETRY"
    assert s.outbound.dispatch_one("w1") is None  # backoff ainda não venceu
    clock.advance(s.policy.backoff(1))
    assert s.outbound.dispatch_one("w1") == "RETRY"
    clock.advance(s.policy.backoff(2))
    assert s.outbound.dispatch_one("w1") == "ACCEPTED"
    assert sum(d.reached_provider for d in s.transport.deliveries) == 1
    assert s.store.outbox_rows()[0]["attempts"] == 3


def test_retries_are_limited_then_definitive_failure(tmp_path):
    clock = Clock()
    s = stack(tmp_path, clock=clock, transport=FakeWhatsAppTransport("throttle"))
    queued(s)
    results = []
    for _ in range(5):
        results.append(s.outbound.dispatch_one("w1"))
        clock.advance(1000)
    assert results == ["RETRY", "RETRY", "FAILED", None, None]
    row = s.store.outbox_rows()[0]
    assert row["status"] == "FAILED" and row["body"] == "" and row["recipient"] == "{}"


def test_definitive_rejection_is_not_retried(tmp_path):
    s = stack(tmp_path, transport=FakeWhatsAppTransport("reject"))
    queued(s)
    assert s.outbound.dispatch_one("w1") == "FAILED"
    assert s.outbound.dispatch_one("w1") is None and len(s.transport.deliveries) == 1


@pytest.mark.parametrize("behavior", ["timeout", "ambiguous", "crash"])
def test_uncertain_result_is_never_blindly_resent(tmp_path, behavior):
    clock = Clock()
    s = stack(tmp_path, clock=clock, transport=FakeWhatsAppTransport(behavior))
    row = queued(s)
    assert s.outbound.dispatch_one("w1") == "UNKNOWN"
    clock.advance(10 ** 5)
    assert s.outbound.dispatch_one("w1") is None
    assert len(s.transport.deliveries) == 1
    kept = s.store.outbox_rows()[0]
    assert kept["status"] == "UNKNOWN" and kept["body"] == row["body"]  # mantido para reconciliação


def test_status_webhook_reconciles_uncertain_send_via_callback(tmp_path):
    s = stack(tmp_path, transport=FakeWhatsAppTransport("timeout"))
    row = queued(s)
    s.outbound.dispatch_one("w1")
    provider_id = s.transport.accepted_for(row["id"])[0].provider_message_id
    s.ingestor.ingest(envelope(status(provider_id, "delivered", callback=row["id"])))
    assert s.inbound.process_one("w1") == "status_delivered"
    final = s.store.outbox_rows()[0]
    assert final["status"] == "ACCEPTED" and final["provider_message_id"] == provider_id
    assert final["delivery_status"] == "delivered" and final["outcome"] == "reconciled_by_status"


def test_manual_resend_of_uncertain_requires_explicit_risk_confirmation(tmp_path):
    s = stack(tmp_path, transport=FakeWhatsAppTransport(script=["timeout", "accept"]))
    row = queued(s)
    s.outbound.dispatch_one("w1")
    for wrong in ("", "yes", "RESEND_UNCERTAIN:other"):
        with pytest.raises(StorageError):
            s.store.requeue_unknown(row["id"], confirmation=wrong)
    s.store.requeue_unknown(row["id"], confirmation=f"RESEND_UNCERTAIN:{row['id']}")
    assert s.outbound.dispatch_one("w1") == "ACCEPTED"
    assert s.store.outbox_rows()[0]["outcome"] == "accepted"


def test_expired_send_lease_becomes_unknown_and_late_truth_is_recorded(tmp_path):
    clock = Clock()
    s = stack(tmp_path, clock=clock)
    queued(s)
    claimed = s.store.claim_outbound("dead", lease_seconds=30, window_seconds=3600)
    clock.advance(31)
    assert s.outbound.dispatch_one("w2") is None  # recupera para UNKNOWN, não reenvia
    assert s.store.outbox_rows()[0]["status"] == "UNKNOWN" and s.transport.deliveries == []
    # O worker "morto" ainda obtém o resultado: aceite comprovado vence o incerto.
    status_after = s.store.finish_outbound(claimed, "dead", SendAccepted("wamid.LATE"),
                                           max_attempts=3, backoff=s.policy.backoff)
    assert status_after == "ACCEPTED"


def test_two_instances_never_send_the_same_message(tmp_path):
    base = stack(tmp_path)
    queued(base)
    instances = [stack(tmp_path) for _ in range(4)]
    barrier = Barrier(4)

    def run(instance):
        barrier.wait(timeout=10)
        return instance.outbound.dispatch_one(f"w{id(instance)}")

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, instances))
    assert sorted(map(str, results)) == ["ACCEPTED", "None", "None", "None"]
    assert sum(len(i.transport.deliveries) for i in instances) == 1


def test_two_inbound_processors_never_create_two_replies(tmp_path):
    base = stack(tmp_path)
    base.ingestor.ingest(envelope(text_message("wamid.1", "Quanto custa o corte?")))
    instances = [stack(tmp_path) for _ in range(4)]
    barrier = Barrier(4)

    def run(instance):
        barrier.wait(timeout=10)
        return instance.inbound.process_one(f"w{id(instance)}")

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, instances))
    assert sorted(map(str, results)) == ["None", "None", "None", "replied"]
    assert len(base.store.outbox_rows()) == 1


def test_lost_inbound_lease_rolls_back_whole_attempt(tmp_path):
    clock = Clock()
    s = stack(tmp_path, clock=clock)
    s.ingestor.ingest(envelope(text_message("wamid.1", "Quanto custa o corte?")))
    stale = s.store.claim_inbound("slow", lease_seconds=60, max_attempts=5)
    clock.advance(61)
    assert s.inbound.process_one("fast") == "replied"
    with pytest.raises(LeaseLost):
        s.inbound._process(stale, "slow")
    assert len(s.store.outbox_rows()) == 1
    key = s.store.inbox_rows()[0]["conversation_id"]
    assert len(s.agent.conversations.snapshot(key).history) == 2


def test_rollback_keeps_inbox_pending_without_outbox_or_conversation(tmp_path):
    clock = Clock()
    s = stack(tmp_path, clock=clock)
    s.ingestor.ingest(envelope(text_message("wamid.1", "Quanto custa o corte?")))

    def fail(stage):
        if stage == "before_commit":
            raise sqlite3.OperationalError("SYNTHETIC_PRIVATE_FAILURE")

    s.repository.fault_hook = fail
    assert s.inbound.process_one("w1") == "retry_scheduled"
    s.repository.fault_hook = lambda stage: None
    assert s.repository.count() == 0 and s.store.outbox_rows() == []
    (row,) = s.store.inbox_rows()
    assert row["status"] == "PENDING" and row["payload"] != "{}"
    assert s.inbound.process_one("w1") is None
    clock.advance(s.policy.backoff(1))
    assert s.inbound.process_one("w1") == "replied"
    assert len(s.store.outbox_rows()) == 1


def test_inbound_storage_failures_are_bounded(tmp_path):
    clock = Clock()
    s = stack(tmp_path, clock=clock, channel_inbox_max_attempts=2)
    s.ingestor.ingest(envelope(text_message("wamid.1", "Olá")))

    def fail(stage):
        if stage == "before_commit":
            raise sqlite3.OperationalError("SYNTHETIC")

    s.repository.fault_hook = fail
    assert s.inbound.process_one("w1") == "retry_scheduled"
    clock.advance(1000)
    assert s.inbound.process_one("w1") == "failed_after_retries"
    assert s.store.inbox_rows()[0]["status"] == "FAILED"


def test_reply_outside_service_window_is_suppressed(tmp_path):
    clock = Clock()
    s = stack(tmp_path, clock=clock)
    queued(s)
    clock.advance(23 * 3600 + 1)
    assert s.outbound.dispatch_one("w1") is None
    row = s.store.outbox_rows()[0]
    assert row["status"] == "SUPPRESSED" and row["outcome"] == "outside_service_window"
    assert s.transport.deliveries == []


def environment():
    values = {k: v for k, v in os.environ.items()
              if not k.startswith(("APP_", "SIM_", "AI_", "STORAGE_", "WHATSAPP_", "CHANNEL_"))}
    values["PYTHONPATH"] = str(ROOT)
    return values


def worker(directory, mode):
    return subprocess.run([sys.executable, str(WORKER), str(directory), mode], capture_output=True,
                          text=True, cwd=directory, env=environment(), timeout=60)


def test_real_process_restarts_webhook_processing_crash_during_send_and_recovery(tmp_path):
    posted = worker(tmp_path, "post")
    assert posted.returncode == 0, posted.stderr
    assert json.loads(posted.stdout) == {"status": 200}
    again = worker(tmp_path, "post")  # reentrega após reinício: aceita como duplicata
    assert json.loads(again.stdout) == {"status": 200}
    processed = worker(tmp_path, "process")
    assert json.loads(processed.stdout) == {"inbound": "replied"}
    crashed = worker(tmp_path, "crash_during_send")
    assert crashed.returncode == 75
    recovered = worker(tmp_path, "recover")
    assert recovered.returncode == 0, recovered.stderr
    data = json.loads(recovered.stdout)
    assert data == {"steps": [], "sent": 0, "outbox": ["UNKNOWN"], "outcome": ["lease_expired_during_send"]}
    with closing(sqlite3.connect(tmp_path / "channel.synthetic.sqlite3")) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT count(*) FROM channel_inbox").fetchone() == (1,)


def test_real_process_restart_between_commit_and_send(tmp_path):
    assert json.loads(worker(tmp_path, "post").stdout) == {"status": 200}
    assert json.loads(worker(tmp_path, "process").stdout) == {"inbound": "replied"}
    data = json.loads(worker(tmp_path, "dispatch").stdout)
    assert data["steps"] == ["ACCEPTED"] and data["sent"] == 1 and "R$ 45,00" in data["bodies"][0]
    assert json.loads(worker(tmp_path, "dispatch").stdout)["sent"] == 0
