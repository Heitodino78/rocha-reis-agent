"""Adaptador Graph API com httpx2.MockTransport: requisição, classificação e outbox.

Nenhum teste abre conexão: o MockTransport responde em memória e a rede está
bloqueada pelo conftest. Estes testes NÃO provam compatibilidade real com a Meta.
"""

from concurrent.futures import ThreadPoolExecutor
import json
import logging
from threading import Barrier, Lock

import httpx2
import pytest

from rocha_reis_agent import config as config_module
from rocha_reis_agent.bootstrap import build_transport
from rocha_reis_agent.channels.contracts import OutboundText, SendAccepted, SendRejected, SendUncertain, Sender
from rocha_reis_agent.channels.fake_transport import FakeWhatsAppTransport
from rocha_reis_agent.channels.graph_transport import (
    GRAPH_BASE_URL, GraphAPITransport, GraphTransportConfig, build_client, classify_error,
)
from rocha_reis_agent.config import Settings
from whatsapp_support import CLIENT_A, Clock, PHONE_ID, envelope, settings, stack, text_message

TOKEN = "EAAG" + "SinteticoTokenDeTeste" + "Qx7Lp2Vm9Rk4Wz8Nb3Hc6Jd1Fg5Ty0"  # fictício
OP = "c" * 32


class Recorder:
    """Handler do MockTransport com roteiro de respostas ou exceções."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []
        self.lock = Lock()

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        with self.lock:
            self.requests.append(request)
            item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        status, body = item
        content = body if isinstance(body, bytes) else json.dumps(body).encode()
        return httpx2.Response(status, content=content, headers={"Content-Type": "application/json"})


def ok(wamid="wamid.HBgLMTY0NjcwNDM1OTUVAgARGBI1RjQy"):
    return 200, {"messaging_product": "whatsapp", "contacts": [{"input": CLIENT_A, "wa_id": CLIENT_A}],
                 "messages": [{"id": wamid}]}


def err(status, code, message="(#0) Sintético"):
    return status, {"error": {"message": message, "type": "OAuthException", "code": code,
                              "error_data": {"messaging_product": "whatsapp", "details": "sintético"},
                              "fbtrace_id": "Synthetic"}}


def transport(recorder, **changes):
    values = dict(access_token=TOKEN, phone_number_id=PHONE_ID, api_version="v26.0")
    values.update(changes)
    return GraphAPITransport(GraphTransportConfig(**values), build_client(httpx2.MockTransport(recorder)))


def message(recipient=None, body="Resposta autorizada sintética", number=PHONE_ID):
    return OutboundText(OP, number, recipient or Sender(wa_id=CLIENT_A), body)


# ------------------------------------------------------------- requisição (1–3, 31–32)
def test_valid_text_request_is_correctly_formatted_and_authenticated():
    recorder = Recorder(ok())
    outcome = transport(recorder).send_text(message(), timeout_seconds=5)
    (request,) = recorder.requests
    assert request.method == "POST"
    assert str(request.url) == f"https://graph.facebook.com/v26.0/{PHONE_ID}/messages"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers["Accept"] == "application/json"
    assert json.loads(request.content) == {
        "messaging_product": "whatsapp", "recipient_type": "individual", "type": "text", "to": CLIENT_A,
        "text": {"preview_url": False, "body": "Resposta autorizada sintética"},
        "biz_opaque_callback_data": OP,
    }
    assert outcome == SendAccepted("wamid.HBgLMTY0NjcwNDM1OTUVAgARGBI1RjQy")


def test_bsuid_recipient_uses_recipient_field_and_preserves_format():
    recorder = Recorder(ok())
    transport(recorder).send_text(message(Sender(user_id="BR.ENT.11815799212886844830")), timeout_seconds=5)
    body = json.loads(recorder.requests[0].content)
    assert body["recipient"] == "BR.ENT.11815799212886844830" and "to" not in body


def test_phone_recipient_with_bsuid_sends_to_phone_only():
    recorder = Recorder(ok())
    transport(recorder).send_text(message(Sender(wa_id=CLIENT_A, user_id="BR.123")), timeout_seconds=5)
    body = json.loads(recorder.requests[0].content)
    assert body["to"] == CLIENT_A and "recipient" not in body


# ------------------------------------------------------- desabilitado / credenciais (4–6)
def test_real_transport_is_disabled_by_default_and_by_code_lock(tmp_path):
    assert config_module.GRAPH_TRANSPORT_ACTIVATION_AUTHORIZED is False
    assert Settings().whatsapp_transport == "fake"
    assert isinstance(build_transport(settings(tmp_path)), FakeWhatsAppTransport)
    with pytest.raises(Exception) as caught:
        settings(tmp_path, whatsapp_transport="graph", whatsapp_access_token=TOKEN, whatsapp_graph_api_version="v26.0")
    assert "não autorizado" in str(caught.value) and TOKEN not in str(caught.value)


def test_environment_variables_alone_never_enable_real_sending(tmp_path, monkeypatch):
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", TOKEN)
    with pytest.raises(Exception):
        settings(tmp_path)  # token sem seleção explícita do transporte real é recusado


def test_unlocked_factory_validates_configuration_without_sending(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "GRAPH_TRANSPORT_ACTIVATION_AUTHORIZED", True)
    for broken in ({"whatsapp_access_token": None}, {"whatsapp_graph_api_version": None},
                   {"whatsapp_access_token": "curto"}, {"whatsapp_enabled": False, "storage_backend": "memory",
                                                        "storage_path": None}):
        values = {"whatsapp_transport": "graph", "whatsapp_access_token": TOKEN, "whatsapp_graph_api_version": "v26.0"}
        values.update(broken)
        with pytest.raises(Exception):
            settings(tmp_path, **values)
    recorder = Recorder(ok())
    built = build_transport(settings(tmp_path, whatsapp_transport="graph", whatsapp_access_token=TOKEN,
                                     whatsapp_graph_api_version="v26.0"), http_transport=httpx2.MockTransport(recorder))
    assert isinstance(built, GraphAPITransport) and recorder.requests == []
    assert TOKEN not in repr(built) and TOKEN not in repr(built.config)


@pytest.mark.parametrize("changes", [
    {"access_token": ""}, {"access_token": "x" * 10}, {"access_token": TOKEN + " extra"},
    {"api_version": "26.0"}, {"api_version": "v26"}, {"api_version": "../v26.0"},
    {"phone_number_id": "graph.evil.example"}, {"phone_number_id": "123/../../me"},
    {"read_timeout_seconds": 0},
])
def test_invalid_credentials_version_or_destination_are_rejected(changes):
    with pytest.raises(ValueError):
        transport(Recorder(ok()), **changes)


def test_destination_is_fixed_by_application_not_by_message():
    recorder = Recorder(ok())
    outcome = transport(recorder).send_text(message(number="999999"), timeout_seconds=5)
    assert outcome == SendRejected("parameter:business_number_mismatch", retryable=False)
    assert recorder.requests == [] and GRAPH_BASE_URL == "https://graph.facebook.com"


# ----------------------------------------------------------------- redirecionamento (7)
def test_redirect_is_never_followed_and_is_uncertain():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx2.Response(302, headers={"Location": "https://evil.example/steal"})

    outcome = GraphAPITransport(
        GraphTransportConfig(TOKEN, PHONE_ID, "v26.0"), build_client(httpx2.MockTransport(handler)),
    ).send_text(message(), timeout_seconds=5)
    assert outcome == SendUncertain("unclassified:redirect_302") and len(requests) == 1


@pytest.mark.parametrize("kwargs", [{"follow_redirects": True}, {"trust_env": True}])
def test_insecure_http_client_is_refused(kwargs):
    with pytest.raises(ValueError):
        GraphAPITransport(GraphTransportConfig(TOKEN, PHONE_ID, "v26.0"),
                          httpx2.Client(transport=httpx2.MockTransport(Recorder(ok())), **kwargs))


# ------------------------------------------------------ respostas e erros (8–10, 13–17)
@pytest.mark.parametrize("response,expected", [
    (err(400, 131047), SendRejected("service_window:131047", retryable=False)),
    (err(400, 100), SendRejected("parameter:100", retryable=False)),
    (err(401, 190), SendRejected("authentication:190", retryable=False)),
    (err(403, 10), SendRejected("permission:10", retryable=False)),
    (err(403, 250), SendRejected("permission:250", retryable=False)),
    (err(400, 131026), SendRejected("recipient:131026", retryable=False)),
    (err(400, 368), SendRejected("policy:368", retryable=False)),
    (err(429, 130429), SendRejected("rate_limit:130429", retryable=True)),
    (err(400, 131056), SendRejected("rate_limit:131056", retryable=True)),
    (err(400, 4), SendRejected("rate_limit:4", retryable=True)),
    (err(500, 131000), SendRejected("temporary:131000", retryable=True)),
    (err(500, 2), SendUncertain("temporary:2")),
    (err(500, 1), SendUncertain("temporary:1")),
    (err(503, 131016), SendUncertain("temporary:131016")),
    (err(400, 999999), SendRejected("unclassified:999999", retryable=False)),
    (err(500, 999999), SendUncertain("unclassified:999999")),
    ((429, b"rate"), SendRejected("rate_limit:http_429", retryable=True)),
    ((403, b"<html>"), SendRejected("authentication:http_403", retryable=False)),
    ((502, b"<html>bad gateway</html>"), SendUncertain("unclassified:http_502")),
    ((200, b"not json"), SendUncertain("unclassified:success_without_id")),
    ((200, {"messages": []}), SendUncertain("unclassified:success_without_id")),
    ((200, {"messages": [{"id": "id com espaço"}]}), SendUncertain("unclassified:success_without_id")),
    ((200, b"[" * 100000), SendUncertain("unclassified:success_without_id")),
    ((201, {"messages": [{"id": "wamid.X", "message_status": "accepted"}]}), SendAccepted("wamid.X", "accepted")),
])
def test_responses_are_classified_explicitly(response, expected):
    assert transport(Recorder(response)).send_text(message(), timeout_seconds=5) == expected


@pytest.mark.parametrize("exception,expected", [
    (httpx2.ConnectTimeout("sintético"), SendRejected("temporary:connect_failed", retryable=True)),
    (httpx2.ConnectError("sintético"), SendRejected("temporary:connect_failed", retryable=True)),
    (httpx2.PoolTimeout("sintético"), SendRejected("temporary:connect_failed", retryable=True)),
    (httpx2.ReadTimeout("sintético"), SendUncertain("temporary:timeout_after_connect")),
    (httpx2.WriteTimeout("sintético"), SendUncertain("temporary:timeout_after_connect")),
    (httpx2.RemoteProtocolError("sintético"), SendUncertain("temporary:transport_error_after_connect")),
    (httpx2.ReadError("sintético"), SendUncertain("temporary:transport_error_after_connect")),
])
def test_network_failures_distinguish_not_sent_from_uncertain(exception, expected):
    assert transport(Recorder(exception)).send_text(message(), timeout_seconds=5) == expected


def test_classify_error_never_treats_every_http_error_as_transient():
    assert classify_error(400, None) == SendRejected("unclassified:http_400", retryable=False)
    assert not classify_error(404, None).retryable


def test_token_and_private_content_never_reach_logs(caplog):
    with caplog.at_level(logging.DEBUG):
        for response in (ok(), err(401, 190), httpx2.ReadTimeout("x")):
            transport(Recorder(response)).send_text(message(body="TEXTO_PRIVADO_SINTETICO"), timeout_seconds=5)
    text = caplog.text + "".join(str(r.__dict__) for r in caplog.records)
    for private in (TOKEN, "TEXTO_PRIVADO_SINTETICO", CLIENT_A, "Bearer"):
        assert private not in text


# --------------------------------------------------- integração com a outbox (18–20, 30)
def graph_stack(tmp_path, recorder, clock=None):
    s = stack(tmp_path, clock=clock)
    s.outbound.transport = transport(recorder)
    return s


def queue(s, wamid="wamid.1"):
    s.ingestor.ingest(envelope(text_message(wamid, "Quanto custa o corte?")))
    assert s.inbound.process_one("w1") == "replied"


def test_outbox_retries_rate_limit_then_accepts_exactly_one_request_accepted(tmp_path):
    clock = Clock()
    recorder = Recorder(err(429, 130429), ok())
    s = graph_stack(tmp_path, recorder, clock)
    queue(s)
    assert s.outbound.dispatch_one("w1") == "RETRY"
    clock.advance(s.policy.backoff(1))
    assert s.outbound.dispatch_one("w1") == "ACCEPTED"
    row = s.store.outbox_rows()[0]
    assert row["provider_message_id"].startswith("wamid.") and row["delivery_status"] is None
    assert len(recorder.requests) == 2


def test_permanent_error_fails_without_retry(tmp_path):
    clock = Clock()
    recorder = Recorder(err(400, 131047))
    s = graph_stack(tmp_path, recorder, clock)
    queue(s)
    assert s.outbound.dispatch_one("w1") == "FAILED"
    clock.advance(10 ** 4)
    assert s.outbound.dispatch_one("w1") is None and len(recorder.requests) == 1
    assert s.store.outbox_rows()[0]["outcome"] == "service_window:131047"


def test_timeout_after_possible_send_is_unknown_and_never_resent(tmp_path):
    clock = Clock()
    recorder = Recorder(httpx2.ReadTimeout("x"), ok())
    s = graph_stack(tmp_path, recorder, clock)
    queue(s)
    assert s.outbound.dispatch_one("w1") == "UNKNOWN"
    for _ in range(3):
        clock.advance(3600)
        assert s.outbound.dispatch_one("w1") is None
    assert len(recorder.requests) == 1


def test_connect_timeout_before_connection_is_retried_safely(tmp_path):
    clock = Clock()
    recorder = Recorder(httpx2.ConnectTimeout("x"), ok())
    s = graph_stack(tmp_path, recorder, clock)
    queue(s)
    assert s.outbound.dispatch_one("w1") == "RETRY"
    clock.advance(s.policy.backoff(1))
    assert s.outbound.dispatch_one("w1") == "ACCEPTED"


def test_restart_dispatches_pending_with_graph_adapter(tmp_path):
    queue(stack(tmp_path))
    recorder = Recorder(ok())
    restarted = graph_stack(tmp_path, recorder)
    assert restarted.outbound.dispatch_one("w2") == "ACCEPTED" and len(recorder.requests) == 1


def test_concurrent_dispatchers_issue_a_single_http_request(tmp_path):
    queue(stack(tmp_path))
    recorder = Recorder(ok())
    instances = [graph_stack(tmp_path, recorder) for _ in range(4)]
    barrier = Barrier(4)

    def run(instance):
        barrier.wait(timeout=10)
        return instance.outbound.dispatch_one(f"w{id(instance)}")

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, instances))
    assert results.count("ACCEPTED") == 1 and len(recorder.requests) == 1


def test_window_expiring_while_waiting_for_retry_blocks_next_attempt(tmp_path):
    clock = Clock()
    recorder = Recorder(err(429, 130429))
    s = graph_stack(tmp_path, recorder, clock)
    queue(s)
    assert s.outbound.dispatch_one("w1") == "RETRY"
    clock.advance(23 * 3600 + 1)
    assert s.outbound.dispatch_one("w1") is None
    row = s.store.outbox_rows()[0]
    assert row["status"] == "SUPPRESSED" and row["outcome"] == "outside_service_window"
    assert len(recorder.requests) == 1
