"""Normalização pura do envelope Meta: sem confiar em campos externos."""

import pytest

from rocha_reis_agent.channels.contracts import (
    ChannelError, InboundTextMessage, InboundUnsupported, OutboundText, Sender, StatusUpdate,
)
from rocha_reis_agent.channels.meta import build_text_payload, parse_webhook, sign, verify_signature
from rocha_reis_agent.channels.service import conversation_id_for, event_id_for
from whatsapp_support import (
    APP_SECRET, CLIENT_A, CLIENT_B, DISPLAY, PHONE_ID, WABA_ID, envelope, media_message, status, text_message,
)


def parse(payload, **kwargs):
    return parse_webhook(payload, phone_number_id=PHONE_ID, business_account_id=WABA_ID, **kwargs)


def test_text_message_is_normalized_without_profile_name():
    batch = parse(envelope(text_message("wamid.1", "Olá")))
    (event,) = batch.events
    assert isinstance(event, InboundTextMessage)
    assert event.text == "Olá" and event.sender.wa_id == CLIENT_A and event.business_number_id == PHONE_ID
    assert "Cliente Fictício" not in repr(event) and CLIENT_A not in repr(event)


def test_bsuid_only_sender_is_supported_and_preferred_for_identity():
    (only,) = parse(envelope(text_message("wamid.1", "Olá", None, user_id="BR.13491208655302741918"))).events
    assert only.sender.wa_id is None and only.sender.identity_key == "bsuid:BR.13491208655302741918"
    (both,) = parse(envelope(text_message("wamid.2", "Olá", CLIENT_A, user_id="BR.1349"))).events
    assert both.sender.identity_key == "bsuid:BR.1349"
    payload = build_text_payload(OutboundText("a" * 32, PHONE_ID, only.sender, "Oi"))
    assert payload["recipient"] == "BR.13491208655302741918" and "to" not in payload


def test_own_business_number_is_never_treated_as_customer():
    assert parse(envelope(text_message("wamid.1", "eco", DISPLAY))).events == ()


@pytest.mark.parametrize("kind", ["image", "audio", "video", "document", "sticker", "location", "reaction", "interactive"])
def test_unsupported_types_are_not_interpreted(kind):
    (event,) = parse(envelope(media_message("wamid.1", kind))).events
    assert isinstance(event, InboundUnsupported) and event.message_type == kind


@pytest.mark.parametrize("message", [
    {"from": CLIENT_A, "id": "wamid.1", "timestamp": "1", "type": "text", "text": {"body": "   "}},
    {"from": CLIENT_A, "id": "wamid.1", "timestamp": "1", "type": "text", "text": {"body": "x" * 4097}},
    {"from": CLIENT_A, "id": "wamid.1", "timestamp": "1", "type": "text"},
])
def test_text_without_supported_content_is_unsupported(message):
    (event,) = parse(envelope({"messages": [message]})).events
    assert isinstance(event, InboundUnsupported) and event.message_type == "text_invalid"


@pytest.mark.parametrize("message", [
    {"from": CLIENT_A, "timestamp": "1", "type": "text", "text": {"body": "sem id"}},
    {"from": CLIENT_A, "id": "wamid.1", "type": "text", "text": {"body": "sem timestamp"}},
    {"from": CLIENT_A, "id": "wamid.1", "timestamp": "-5", "type": "text", "text": {"body": "x"}},
    {"from": CLIENT_A, "id": "wamid 1", "timestamp": "1", "type": "text", "text": {"body": "x"}},
    {"from": "+55 11", "id": "wamid.1", "timestamp": "1", "type": "text", "text": {"body": "x"}},
    {"from_user_id": "../etc", "id": "wamid.1", "timestamp": "1", "type": "text", "text": {"body": "x"}},
    {"id": "wamid.1", "timestamp": "1", "type": "text", "text": {"body": "sem remetente"}},
    {"from": CLIENT_A, "id": "wamid.1", "timestamp": "1", "text": {"body": "sem tipo"}},
    "texto",
])
def test_items_missing_required_identifiers_are_ignored(message):
    batch = parse(envelope({"messages": [message]}))
    assert batch.events == () and batch.ignored == 1


def test_statuses_are_separate_and_carry_callback_and_error_codes():
    payload = envelope(status("wamid.OUT", "failed", callback="a" * 32), status("wamid.OUT", "bogus"))
    (event,) = parse(payload).events
    assert isinstance(event, StatusUpdate) and event.status == "failed"
    assert event.callback_data == "a" * 32 and event.error_codes == (131026,)
    (no_cb,) = parse(envelope(status("wamid.X", "read", callback="não-hex"))).events
    assert no_cb.callback_data is None


def test_other_waba_and_number_are_ignored():
    assert parse(envelope(text_message("wamid.1", "Olá"), waba="999")).ignored == 1
    assert parse(envelope(text_message("wamid.1", "Olá"), number="999")).ignored == 1


def test_excessive_collections_are_rejected():
    many = {"messages": [text_message(f"wamid.{i}", "x")["messages"][0] for i in range(101)]}
    with pytest.raises(ChannelError):
        parse(envelope(many))


def test_signature_helpers_and_constant_time_comparison_inputs():
    body = b'{"a":1}'
    assert verify_signature(APP_SECRET.encode(), body, sign(APP_SECRET.encode(), body))
    assert verify_signature(APP_SECRET.encode(), body, sign(APP_SECRET.encode(), body).upper().replace("SHA256", "sha256"))
    assert not verify_signature(b"", body, sign(b"", body))
    assert not verify_signature(APP_SECRET.encode(), body, None)


def test_identifiers_are_pseudonymous_stable_and_isolated():
    a1, a2, b = (parse(envelope(text_message(f"wamid.{i}", "x", s))).events[0]
                 for i, s in ((1, CLIENT_A), (2, CLIENT_A), (3, CLIENT_B)))
    key = b"k" * 32
    assert conversation_id_for(a1, key) == conversation_id_for(a2, key) != conversation_id_for(b, key)
    assert conversation_id_for(a1, key) != conversation_id_for(a1, b"j" * 32)
    assert CLIENT_A not in conversation_id_for(a1, key)
    assert event_id_for("wamid.HBgL+/=") != event_id_for("wamid.HBgL+/-")
    assert len(event_id_for("x" * 256)) <= 64


def test_graph_payload_contract_for_future_adapter():
    message = OutboundText("b" * 32, PHONE_ID, Sender(wa_id=CLIENT_A), "Resposta")
    assert build_text_payload(message) == {
        "messaging_product": "whatsapp", "recipient_type": "individual", "type": "text",
        "text": {"preview_url": False, "body": "Resposta"}, "biz_opaque_callback_data": "b" * 32, "to": CLIENT_A,
    }
