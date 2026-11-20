"""Fase 6A: autorização, travas, allowlist, limites, interrupção e ambiente isolado."""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import os
import sqlite3

import pytest

from rocha_reis_agent import config as config_module
from rocha_reis_agent.bootstrap import build_transport
from rocha_reis_agent.channels.contracts import Sender
from rocha_reis_agent.channels.fake_transport import FakeWhatsAppTransport
from rocha_reis_agent.channels.homologation import (
    GuardCounts, HomologationError, HomologationGuard, load_authorization,
)
from rocha_reis_agent.channels.service import drain
from rocha_reis_agent.config import ConfigurationError, load_settings
from rocha_reis_agent.domain.contracts import StorageError
from rocha_reis_agent.main import create_app
from homolog_support import AUTH_ID, authorization_document, homolog_settings, homolog_stack, write_authorization
from whatsapp_support import CLIENT_A, CLIENT_B, Clock, PHONE_ID, envelope, settings, status, text_message

TOKEN = "EAAG" + "SinteticoTokenDeTeste" + "Qx7Lp2Vm9Rk4Wz8Nb3Hc6Jd1Fg5Ty0"


def say(h, wamid, text="Quanto custa o corte?", sender=CLIENT_A):
    h.ingestor.ingest(envelope(text_message(wamid, text, sender)))
    return drain(h.inbound, h.outbound, "w1")


def queue_reply(h, wamid, sender=CLIENT_A):
    h.ingestor.ingest(envelope(text_message(wamid, "Quanto custa o corte?", sender)))
    assert h.inbound.process_one("w1") == "replied"


# ------------------------------------------------------------- autorização (44–45)
def test_valid_authorization_is_loaded(tmp_path):
    auth = load_authorization(write_authorization(tmp_path), expected_phone_number_id=PHONE_ID)
    assert auth.authorization_id == AUTH_ID and auth.allows(Sender(wa_id=CLIENT_A))
    assert auth.allows(Sender(user_id="BR.99887766")) and not auth.allows(Sender(wa_id=CLIENT_B))
    assert CLIENT_A not in repr(auth)


@pytest.mark.parametrize("changes", [
    {"confirmation": "sim"}, {"confirmation": "AUTORIZO HOMOLOGACAO OUTRO"},
    {"environment": "production"}, {"schema": "v0"}, {"extra": 1},
    {"phone_number_id": "999999999"}, {"allowed_recipients": []},
    {"allowed_recipients": [CLIENT_A, CLIENT_A]}, {"allowed_recipients": ["+55 11 9"]},
    {"allowed_recipients": [str(5511900000000 + i) for i in range(11)]},
    {"authorized_by": ""}, {"executor": "x" * 121},
    {"operations": ["send_text_reply", "broadcast"]}, {"operations": []},
    {"valid_until": "2020-01-01T00:00:00+00:00"},
    {"valid_until": (datetime.now(timezone.utc) + timedelta(days=4)).isoformat()},
    {"valid_from": "2026-11-19T10:00:00"},
    {"limits": {"max_messages_total": 10}},
    {"limits": {"max_messages_total": 101, "max_messages_per_recipient": 5, "max_messages_per_run": 5, "max_messages_per_minute": 3}},
    {"limits": {"max_messages_total": 5, "max_messages_per_recipient": 6, "max_messages_per_run": 5, "max_messages_per_minute": 3}},
    {"limits": {"max_messages_total": 5, "max_messages_per_recipient": 5, "max_messages_per_run": 5, "max_messages_per_minute": 0}},
    {"authorization_id": "../x"},
])
def test_invalid_authorization_blocks_without_echoing_values(tmp_path, changes):
    document = authorization_document(**changes)
    if "extra" in changes:
        document["extra"] = 1
    if "authorization_id" in changes:
        document["confirmation"] = f"AUTORIZO HOMOLOGACAO {changes['authorization_id']}"
    with pytest.raises(HomologationError) as caught:
        load_authorization(write_authorization(tmp_path, document), expected_phone_number_id=PHONE_ID)
    assert CLIENT_A not in str(caught.value) and "fictício" not in str(caught.value)


def test_authorization_file_must_be_private_regular_and_not_symlinked(tmp_path):
    with pytest.raises(HomologationError, match="0600"):
        load_authorization(write_authorization(tmp_path, mode=0o644), expected_phone_number_id=PHONE_ID)
    real = write_authorization(tmp_path)
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(HomologationError, match="simbólicos"):
        load_authorization(link, expected_phone_number_id=PHONE_ID)
    with pytest.raises(HomologationError, match="ausente"):
        load_authorization(tmp_path / "missing.json", expected_phone_number_id=PHONE_ID)
    with pytest.raises(HomologationError):
        load_authorization(type(tmp_path)("relativo.json"), expected_phone_number_id=PHONE_ID)


# --------------------------------------------------------- ambiente isolado e travas
def test_homologation_environment_is_isolated(tmp_path):
    auth = write_authorization(tmp_path)
    with pytest.raises(Exception):  # banco comum em homologação
        settings(tmp_path, app_env="homologation", homolog_authorization_file=auth)
    with pytest.raises(Exception):  # sem arquivo de autorização
        settings(tmp_path, app_env="homologation", storage_path=tmp_path / "x.homolog.synthetic.sqlite3")
    with pytest.raises(Exception):  # banco de homologação fora do ambiente
        settings(tmp_path, storage_path=tmp_path / "x.homolog.synthetic.sqlite3")
    with pytest.raises(Exception):  # autorização fora do ambiente
        settings(tmp_path, homolog_authorization_file=auth)
    assert homolog_settings(tmp_path, auth).app_env == "homologation"


def test_real_transport_requires_both_environment_and_separate_code_lock(tmp_path, monkeypatch):
    assert config_module.GRAPH_TRANSPORT_ACTIVATION_AUTHORIZED is False
    assert config_module.HOMOLOGATION_TRANSPORT_ACTIVATION_AUTHORIZED is False
    graph = dict(whatsapp_transport="graph", whatsapp_access_token=TOKEN, whatsapp_graph_api_version="v26.0")
    with pytest.raises(Exception, match="não autorizado"):
        homolog_settings(tmp_path, **graph)
    # Abrir só a trava de homologação não libera produção/local, e vice-versa.
    monkeypatch.setattr(config_module, "HOMOLOGATION_TRANSPORT_ACTIVATION_AUTHORIZED", True)
    assert homolog_settings(tmp_path, **graph).whatsapp_transport == "graph"
    with pytest.raises(Exception, match="não autorizado"):
        settings(tmp_path, **graph)
    monkeypatch.setattr(config_module, "HOMOLOGATION_TRANSPORT_ACTIVATION_AUTHORIZED", False)
    monkeypatch.setattr(config_module, "GRAPH_TRANSPORT_ACTIVATION_AUTHORIZED", True)
    with pytest.raises(Exception, match="não autorizado"):
        homolog_settings(tmp_path, **graph)


def test_environment_variables_alone_cannot_enable_real_sending(tmp_path, monkeypatch):
    auth = write_authorization(tmp_path)
    for name, value in {
        "APP_ENV": "homologation", "STORAGE_BACKEND": "sqlite",
        "STORAGE_PATH": str(tmp_path / "e.homolog.synthetic.sqlite3"), "WHATSAPP_ENABLED": "true",
        "WHATSAPP_VERIFY_TOKEN": "vT9xQ2mL7pR4sK8wZ1nB6cH3jF5gD0aY", "WHATSAPP_APP_SECRET": "aP3kW8qZ1xR6tM9vC2nL5bH7jD4fG0sY",
        "WHATSAPP_CONVERSATION_KEY": "Kc8Rz2Lp5Wq9Nm3Xv7Bt1Hj6Gd4Fs0Ay", "WHATSAPP_PHONE_NUMBER_ID": PHONE_ID,
        "HOMOLOG_AUTHORIZATION_FILE": str(auth), "WHATSAPP_TRANSPORT": "graph",
        "WHATSAPP_ACCESS_TOKEN": TOKEN, "WHATSAPP_GRAPH_API_VERSION": "v26.0",
    }.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ConfigurationError) as caught:
        load_settings()
    assert TOKEN not in str(caught.value)
    monkeypatch.setenv("WHATSAPP_TRANSPORT", "fake")
    monkeypatch.delenv("WHATSAPP_ACCESS_TOKEN")
    assert isinstance(build_transport(load_settings()), FakeWhatsAppTransport)


def test_invalid_authorization_prevents_app_start(tmp_path):
    config = homolog_settings(tmp_path, write_authorization(tmp_path, authorization_document(confirmation="não")))
    with pytest.raises(ConfigurationError):
        create_app(config)


# ----------------------------------------------------------- allowlist (24, 47)
def test_allowlisted_recipient_is_sent_and_ledger_records_attempt(tmp_path):
    h = homolog_stack(tmp_path)
    assert say(h, "wamid.1") == ["replied", "ACCEPTED"]
    with closing(sqlite3.connect(h.repository.path)) as connection:
        (row,) = connection.execute("SELECT authorization_id,recipient_hash FROM channel_send_ledger").fetchall()
    assert row[0] == AUTH_ID and CLIENT_A not in row[1] and len(row[1]) == 64


def test_inbound_from_non_allowlisted_sender_is_not_persisted(tmp_path):
    h = homolog_stack(tmp_path)
    report = h.ingestor.ingest(envelope(text_message("wamid.x", "Olá dado real", CLIENT_B)))
    assert report.accepted == 0 and report.ignored == 1
    assert h.store.inbox_rows() == [] and h.store.metrics()["counters"]["webhook_ignored_not_allowlisted"] == 1
    # Status (de mensagens nossas) continuam aceitos.
    assert h.ingestor.ingest(envelope(status("wamid.OUT", "sent"))).accepted == 1


def test_non_allowlisted_recipient_in_outbox_is_suppressed_never_sent(tmp_path):
    h = homolog_stack(tmp_path)
    queue_reply(h, "wamid.1")
    with closing(sqlite3.connect(h.repository.path)) as connection:  # adulteração simulada da outbox
        connection.execute("UPDATE channel_outbox SET recipient=?", (json.dumps({"wa_id": CLIENT_B, "user_id": None}),))
        connection.commit()
    assert h.outbound.dispatch_one("w1") is None
    row = h.store.outbox_rows()[0]
    assert row["status"] == "SUPPRESSED" and row["outcome"] == "recipient_not_allowlisted"
    assert h.transport.deliveries == []


def test_wrong_business_number_is_suppressed(tmp_path):
    h = homolog_stack(tmp_path)
    queue_reply(h, "wamid.1")
    with closing(sqlite3.connect(h.repository.path)) as connection:
        connection.execute("UPDATE channel_outbox SET business_number_id='999999999'")
        connection.commit()
    assert h.outbound.dispatch_one("w1") is None
    assert h.store.outbox_rows()[0]["outcome"] == "homolog_number_mismatch"


def test_operations_without_receive_ignore_all_inbound(tmp_path):
    auth = write_authorization(tmp_path, authorization_document(operations=["send_text_reply"]))
    h = homolog_stack(tmp_path, config=homolog_settings(tmp_path, auth))
    assert h.ingestor.ingest(envelope(text_message("wamid.1", "Olá"))).accepted == 0


# ---------------------------------------------------------------- limites (30)
def limited(tmp_path, **limits):
    base = {"max_messages_total": 10, "max_messages_per_recipient": 5, "max_messages_per_run": 5,
            "max_messages_per_minute": 3}
    base.update(limits)
    return write_authorization(tmp_path, authorization_document(limits=base))


def test_rate_limit_waits_without_touching_outbox(tmp_path):
    clock = Clock()
    h = homolog_stack(tmp_path, clock=clock, homolog_authorization_file=limited(tmp_path, max_messages_per_minute=1))
    queue_reply(h, "wamid.1")
    queue_reply(h, "wamid.2")
    assert h.outbound.dispatch_one("w1") == "ACCEPTED"
    assert h.outbound.dispatch_one("w1") is None
    assert h.store.outbox_rows()[1]["status"] == "PENDING"
    assert h.store.metrics()["counters"]["dispatch_blocked_homolog_rate_limit"] == 1
    clock.advance(61)
    assert h.outbound.dispatch_one("w1") == "ACCEPTED"


@pytest.mark.parametrize("limit,reason", [
    ({"max_messages_total": 1, "max_messages_per_recipient": 1, "max_messages_per_run": 1}, "homolog_total_limit"),
    ({"max_messages_per_recipient": 1}, "homolog_recipient_limit"),
    ({"max_messages_per_run": 1}, "homolog_run_limit"),
])
def test_count_limits_stop_sending_and_preserve_outbox(tmp_path, limit, reason):
    h = homolog_stack(tmp_path, homolog_authorization_file=limited(tmp_path, **limit))
    queue_reply(h, "wamid.1")
    queue_reply(h, "wamid.2")
    assert h.outbound.dispatch_one("w1") == "ACCEPTED"
    assert h.outbound.dispatch_one("w1") is None
    assert h.store.outbox_rows()[1]["status"] == "PENDING" and len(h.transport.deliveries) == 1
    assert h.store.metrics()["counters"][f"dispatch_blocked_{reason}"] == 1


def test_new_run_resets_only_the_per_run_limit(tmp_path):
    auth = limited(tmp_path, max_messages_per_run=1)
    first = homolog_stack(tmp_path, homolog_authorization_file=auth)
    queue_reply(first, "wamid.1")
    queue_reply(first, "wamid.2")
    assert first.outbound.dispatch_one("w1") == "ACCEPTED" and first.outbound.dispatch_one("w1") is None
    second = homolog_stack(tmp_path, homolog_authorization_file=auth)
    assert second.outbound.dispatch_one("w2") == "ACCEPTED"


def test_retries_count_against_limits(tmp_path):
    clock = Clock()
    h = homolog_stack(tmp_path, clock=clock, transport=FakeWhatsAppTransport(script=["not_sent", "accept"]),
                      homolog_authorization_file=limited(tmp_path, max_messages_total=1, max_messages_per_recipient=1,
                                                         max_messages_per_run=1))
    queue_reply(h, "wamid.1")
    assert h.outbound.dispatch_one("w1") == "RETRY"
    clock.advance(60)
    assert h.outbound.dispatch_one("w1") is None  # a segunda tentativa excederia o total autorizado
    assert h.store.outbox_rows()[0]["status"] == "RETRY"


@pytest.mark.parametrize("window", ["future", "expired"])
def test_inactive_authorization_period_blocks_sending(tmp_path, window):
    now = datetime.now(timezone.utc)
    start, end = ((now + timedelta(hours=1), now + timedelta(hours=2)) if window == "future"
                  else (now - timedelta(hours=3), now - timedelta(hours=1)))
    auth = write_authorization(tmp_path, authorization_document(valid_from=start.isoformat(), valid_until=end.isoformat()))
    h = homolog_stack(tmp_path, homolog_authorization_file=auth)
    queue_reply(h, "wamid.1")
    assert h.outbound.dispatch_one("w1") is None and h.transport.deliveries == []
    assert h.store.outbox_rows()[0]["status"] == "PENDING"


def test_guard_unit_decisions():
    from rocha_reis_agent.channels.homologation import HomologationAuthorization, HomologationLimits
    now = datetime.now(timezone.utc)
    auth = HomologationAuthorization(AUTH_ID, PHONE_ID, frozenset({CLIENT_A}), now - timedelta(hours=1),
                                     now + timedelta(hours=1), HomologationLimits(2, 2, 2, 2),
                                     frozenset({"receive_webhook", "send_text_reply"}))
    guard = HomologationGuard(auth, recipient_key=b"k" * 32, run_id="a" * 32)
    zero = GuardCounts(0, 0, 0, 0)
    assert guard.evaluate(now=now, business_number_id=PHONE_ID, recipient=Sender(wa_id=CLIENT_A), counts=zero).action == "send"
    assert guard.evaluate(now=now, business_number_id="1", recipient=Sender(wa_id=CLIENT_A), counts=zero).action == "suppress"
    assert guard.evaluate(now=now, business_number_id=PHONE_ID, recipient=Sender(wa_id=CLIENT_B), counts=zero).action == "suppress"
    with pytest.raises(HomologationError):
        HomologationGuard(auth, recipient_key=b"curta", run_id="a" * 32)


# ------------------------------------------------- interrupção emergencial (50)
def test_emergency_halt_blocks_new_attempts_and_retries_preserving_outbox(tmp_path):
    clock = Clock()
    h = homolog_stack(tmp_path, clock=clock, transport=FakeWhatsAppTransport(script=["not_sent", "accept"]))
    queue_reply(h, "wamid.1")
    assert h.outbound.dispatch_one("w1") == "RETRY"
    with pytest.raises(StorageError):
        h.store.halt("   ", set_by="operador-local")
    h.store.halt("Comportamento inesperado no ensaio", set_by="operador-local")
    clock.advance(3600)
    assert h.outbound.dispatch_one("w1") is None and h.outbound.dispatch_one("w1") is None
    row = h.store.outbox_rows()[0]
    assert row["status"] == "RETRY" and row["body"]  # preservada para análise
    assert h.store.halt_status()["reason"] == "Comportamento inesperado no ensaio"
    # Processamento de entrada continua; respostas ficam na outbox.
    assert say(h, "wamid.3") == ["replied"]
    with pytest.raises(StorageError):
        h.store.resume_dispatch(confirmation="sim", set_by="operador-local")
    h.store.resume_dispatch(confirmation="RESUME_DISPATCH", set_by="operador-local")
    assert h.outbound.dispatch_one("w1") == "ACCEPTED"
    operations = [a["operation"] for a in h.repository.audit()]
    assert "dispatch_halted" in operations and "dispatch_resumed" in operations
    assert h.store.metrics()["counters"]["dispatch_blocked_halt"] == 3  # 2 diretos + 1 no drain


def test_configuration_halt_blocks_and_cannot_override_database_halt(tmp_path):
    h = homolog_stack(tmp_path, channel_dispatch_halted=True)
    queue_reply(h, "wamid.1")
    assert h.outbound.dispatch_one("w1") is None and h.transport.deliveries == []
    other = homolog_stack(tmp_path)
    other.store.halt("teste", set_by="operador-local")
    assert other.outbound.dispatch_one("w1") is None  # sem flag de config, o halt do banco continua valendo


def test_halt_applies_outside_homologation_too(tmp_path):
    from whatsapp_support import stack
    s = stack(tmp_path)
    s.ingestor.ingest(envelope(text_message("wamid.1", "Olá")))
    s.inbound.process_one("w1")
    s.store.halt("manutenção", set_by="operador-local")
    assert s.outbound.dispatch_one("w1") is None and s.transport.deliveries == []


# --------------------------------------------------------- logs privados (49)
def test_private_log_file_is_0600_and_contains_no_personal_data(tmp_path):
    from rocha_reis_agent.observability import remove_private_logs
    log = tmp_path / "logs" / "canal.log"
    try:
        h = homolog_stack(tmp_path, channel_log_path=log)
        from rocha_reis_agent.observability import configure_private_log
        configure_private_log(log)
        configure_private_log(log)  # idempotente
        say(h, "wamid.1", "Quanto custa o corte? TEXTO_PRIVADO")
        h.ingestor.ingest(envelope(status(h.store.outbox_rows()[0]["provider_message_id"], "delivered")))
        drain(h.inbound, h.outbound, "w1")
    finally:
        remove_private_logs()
    content = log.read_text(encoding="utf-8")
    assert os.stat(log).st_mode & 0o777 == 0o600 and "channel_outbound_dispatched outcome=ACCEPTED" in content
    for private in (CLIENT_A, "TEXTO_PRIVADO", "wamid.", "R$", AUTH_ID):
        assert private not in content


def test_log_on_insecure_volume_is_refused(tmp_path, monkeypatch):
    from rocha_reis_agent.observability import configure_private_log
    real = os.fstat

    class Insecure:
        def __init__(self, value):
            self._v = value

        def __getattr__(self, name):
            return getattr(self._v, name)

        @property
        def st_mode(self):
            return (self._v.st_mode & ~0o777) | 0o644

    monkeypatch.setattr(os, "fstat", lambda fd: Insecure(real(fd)))
    with pytest.raises(StorageError, match="0600"):
        configure_private_log(tmp_path / "x.log")
