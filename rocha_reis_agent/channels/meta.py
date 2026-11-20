"""Adaptador Meta WhatsApp Cloud API: funções puras, sem rede e sem domínio.

Referências (consultadas em 19/11/2026; revalidar antes da ativação real):
- Webhooks Meta: verificação com hub.mode=subscribe, hub.verify_token e
  hub.challenge; POST assinado com X-Hub-Signature-256 = "sha256=" + HMAC-SHA256
  do corpo BRUTO usando o App Secret.
- Business-scoped user IDs: remetente pode chegar sem wa_id/from, apenas com
  from_user_id (BSUID, ex. "US.13491208655302741918").
- Messages API: biz_opaque_callback_data (até 512 caracteres) é devolvido nos
  webhooks de status sent/delivered/read.
"""

from datetime import datetime, timezone
import hashlib
import hmac
import re
from typing import Any

from rocha_reis_agent.channels.contracts import (
    ChannelError, InboundEvent, InboundTextMessage, InboundUnsupported,
    NormalizedBatch, OutboundText, Sender, StatusUpdate,
)

SIGNATURE = re.compile(r"sha256=([0-9a-fA-F]{64})")
CHALLENGE = re.compile(r"[A-Za-z0-9_-]{1,128}")
NUMERIC_ID = re.compile(r"[0-9]{1,32}")
PROVIDER_ID = re.compile(r"[A-Za-z0-9._:=+/-]{1,256}")
BSUID = re.compile(r"[A-Z]{2}(?:\.[A-Z]{2,8})?\.[A-Za-z0-9]{1,128}")
STATUSES = {"sent", "delivered", "read", "failed"}
MAX_ENTRIES = 100
MAX_CHANGES = 100
MAX_ITEMS = 100
MAX_EVENTS = 1000
MAX_TEXT = 4096


def verify_subscription(
    mode: str | None, token: str | None, challenge: str | None, expected_token: str
) -> str | None:
    """Retorna o challenge somente para inscrição válida; nunca registra o token."""
    if mode != "subscribe" or token is None or challenge is None:
        return None
    if not hmac.compare_digest(token.encode("utf-8"), expected_token.encode("utf-8")):
        return None
    if CHALLENGE.fullmatch(challenge) is None:
        return None
    return challenge


def verify_signature(app_secret: bytes, body: bytes, header: str | None) -> bool:
    """Compara em tempo constante o HMAC-SHA256 dos bytes originais do corpo."""
    if not app_secret or header is None:
        return False
    match = SIGNATURE.fullmatch(header.strip())
    if match is None:
        return False
    expected = hmac.new(app_secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, match.group(1).lower())


def sign(app_secret: bytes, body: bytes) -> str:
    """Somente para fixtures/testes locais e para a CLI de simulação."""
    return "sha256=" + hmac.new(app_secret, body, hashlib.sha256).hexdigest()


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.isdigit() or len(value) > 12:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _str(value: Any, pattern: re.Pattern[str]) -> str | None:
    return value if isinstance(value, str) and pattern.fullmatch(value) else None


def _list(value: Any, limit: int, *, required: bool = False) -> list[Any]:
    if value is None and not required:
        return []
    if not isinstance(value, list) or len(value) > limit:
        raise ChannelError("Estrutura do webhook inválida.")
    return value


def parse_webhook(
    payload: Any, *, phone_number_id: str, business_account_id: str | None = None
) -> NormalizedBatch:
    """Normaliza um envelope já AUTENTICADO.

    Estrutura geral inválida → ChannelError (HTTP 400). Itens individuais sem
    identificadores exigidos, campos não suportados ou de outro número → ignorados.
    """
    if not isinstance(payload, dict):
        raise ChannelError("Estrutura do webhook inválida.")
    if payload.get("object") != "whatsapp_business_account":
        return NormalizedBatch((), ignored=1)
    events: list[InboundEvent] = []
    ignored = 0
    for entry in _list(payload.get("entry"), MAX_ENTRIES, required=True):
        if not isinstance(entry, dict):
            raise ChannelError("Estrutura do webhook inválida.")
        if business_account_id is not None and entry.get("id") != business_account_id:
            ignored += 1
            continue
        for change in _list(entry.get("changes"), MAX_CHANGES, required=True):
            if not isinstance(change, dict) or not isinstance(change.get("value", {}), dict):
                raise ChannelError("Estrutura do webhook inválida.")
            value = change.get("value") or {}
            # Apenas o campo "messages" é suportado; ecos (smb_message_echoes),
            # templates, contas etc. são ignorados sem interpretação.
            if change.get("field") != "messages":
                ignored += 1
                continue
            metadata = value.get("metadata")
            number = _str(metadata.get("phone_number_id"), NUMERIC_ID) if isinstance(metadata, dict) else None
            if number is None or number != phone_number_id:
                ignored += 1
                continue
            own = metadata.get("display_phone_number")
            own_digits = re.sub(r"[^0-9]", "", own) if isinstance(own, str) else None
            for message in _list(value.get("messages"), MAX_ITEMS):
                event = _message(message, number, own_digits)
                if event is None:
                    ignored += 1
                else:
                    events.append(event)
            for status in _list(value.get("statuses"), MAX_ITEMS):
                event = _status(status, number)
                if event is None:
                    ignored += 1
                else:
                    events.append(event)
            if len(events) > MAX_EVENTS:
                raise ChannelError("Webhook com eventos em excesso.")
    return NormalizedBatch(tuple(events), ignored)


def _message(item: Any, number: str, own_digits: str | None) -> InboundEvent | None:
    if not isinstance(item, dict):
        return None
    provider_id = _str(item.get("id"), PROVIDER_ID)
    timestamp = _timestamp(item.get("timestamp"))
    wa_id = _str(item.get("from"), NUMERIC_ID)
    user_id = _str(item.get("from_user_id"), BSUID)
    kind = item.get("type")
    if provider_id is None or timestamp is None or not isinstance(kind, str) or not (wa_id or user_id):
        return None
    if wa_id is not None and own_digits and wa_id == own_digits:
        return None  # mensagem do próprio número comercial nunca é tratada como cliente
    sender = Sender(wa_id=wa_id, user_id=user_id)
    text = item.get("text")
    body = text.get("body") if kind == "text" and isinstance(text, dict) else None
    if isinstance(body, str) and body.strip() and len(body) <= MAX_TEXT:
        return InboundTextMessage(number, provider_id, sender, body, timestamp)
    safe_type = kind if re.fullmatch(r"[a-z_]{1,32}", kind) else "unknown"
    if kind == "text":
        safe_type = "text_invalid"
    return InboundUnsupported(number, provider_id, sender, safe_type, timestamp)


def _status(item: Any, number: str) -> StatusUpdate | None:
    if not isinstance(item, dict):
        return None
    provider_id = _str(item.get("id"), PROVIDER_ID)
    status = item.get("status")
    timestamp = _timestamp(item.get("timestamp"))
    if provider_id is None or status not in STATUSES or timestamp is None:
        return None
    callback = item.get("biz_opaque_callback_data")
    callback = callback if isinstance(callback, str) and re.fullmatch(r"[0-9a-f]{32}", callback) else None
    errors = item.get("errors")
    codes = tuple(
        error["code"] for error in errors[:10] if isinstance(error, dict) and type(error.get("code")) is int
    ) if isinstance(errors, list) else ()
    return StatusUpdate(number, provider_id, status, timestamp, callback, codes)


def build_text_payload(message: OutboundText) -> dict[str, object]:
    """Corpo da futura chamada POST /{versão}/{phone_number_id}/messages.

    Não executado nesta fase. preview_url=False evita pré-visualização automática.
    O identificador da operação vai em biz_opaque_callback_data para reconciliar
    envios de resultado incerto pelos webhooks de status.
    """
    payload: dict[str, object] = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "type": "text",
        "text": {"preview_url": False, "body": message.body},
        "biz_opaque_callback_data": message.operation_id,
    }
    if message.recipient.wa_id:
        payload["to"] = message.recipient.wa_id
    else:
        payload["recipient"] = message.recipient.user_id
    return payload
