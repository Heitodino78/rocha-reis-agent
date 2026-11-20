"""Apoio aos testes do canal: payloads sintéticos no formato do webhook Meta.

Todos os números, IDs e segredos são fictícios e gerados para teste.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time

from rocha_reis_agent.bootstrap import build_agent, configured_channel, queue_policy
from rocha_reis_agent.catalog_io import load_synthetic_catalog
from rocha_reis_agent.channels.fake_transport import FakeWhatsAppTransport
from rocha_reis_agent.channels.meta import sign
from rocha_reis_agent.channels.service import InboundProcessor, OutboxDispatcher, QueuePolicy, WebhookIngestor
from rocha_reis_agent.config import Settings
from rocha_reis_agent.persistence.sqlite import SQLiteConversationRepository
from rocha_reis_agent.persistence.sqlite_channel import SQLiteChannelStore

ROOT = Path(__file__).resolve().parents[1]
VERIFY = "vT9xQ2mL7pR4sK8wZ1nB6cH3jF5gD0aY"
APP_SECRET = "aP3kW8qZ1xR6tM9vC2nL5bH7jD4fG0sY"
CONVERSATION_KEY = "Kc8Rz2Lp5Wq9Nm3Xv7Bt1Hj6Gd4Fs0Ay"
PHONE_ID = "100000000000001"
WABA_ID = "200000000000002"
DISPLAY = "15550000000"
CLIENT_A = "5511900000001"
CLIENT_B = "5511900000002"


def settings(tmp_path: Path, **changes: object) -> Settings:
    values: dict[str, object] = dict(
        storage_backend="sqlite", storage_path=tmp_path / "channel.synthetic.sqlite3",
        whatsapp_enabled=True, whatsapp_verify_token=VERIFY, whatsapp_app_secret=APP_SECRET,
        whatsapp_conversation_key=CONVERSATION_KEY, whatsapp_phone_number_id=PHONE_ID,
        whatsapp_business_account_id=WABA_ID,
    )
    values.update(changes)
    return Settings(**values)


def now_ts(offset_seconds: int = 0) -> str:
    return str(int(time.time()) + offset_seconds)


def envelope(*values: dict, field: str = "messages", number: str = PHONE_ID, waba: str = WABA_ID) -> dict:
    return {"object": "whatsapp_business_account", "entry": [{"id": waba, "changes": [
        {"field": field, "value": {"messaging_product": "whatsapp",
                                   "metadata": {"display_phone_number": DISPLAY, "phone_number_id": number},
                                   **value}} for value in values]}]}


def text_message(wamid: str, body: str, sender: str | None = CLIENT_A, *, user_id: str | None = None,
                 ts: str | None = None) -> dict:
    message: dict = {"id": wamid, "timestamp": ts or now_ts(), "type": "text", "text": {"body": body}}
    contact: dict = {"profile": {"name": "Cliente Fictício"}}
    if sender:
        message["from"] = sender
        contact["wa_id"] = sender
    if user_id:
        message["from_user_id"] = user_id
        contact["user_id"] = user_id
    return {"contacts": [contact], "messages": [message]}


def media_message(wamid: str, kind: str = "image", sender: str = CLIENT_A) -> dict:
    return {"messages": [{"from": sender, "id": wamid, "timestamp": now_ts(), "type": kind,
                          kind: {"id": "media-sintetica", "mime_type": "image/jpeg"}}]}


def status(wamid: str, value: str = "delivered", *, callback: str | None = None, ts: str | None = None) -> dict:
    item: dict = {"id": wamid, "status": value, "timestamp": ts or now_ts(), "recipient_id": CLIENT_A}
    if callback:
        item["biz_opaque_callback_data"] = callback
    if value == "failed":
        item["errors"] = [{"code": 131026, "title": "Sintético"}]
    return {"statuses": [item]}


def body_bytes(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def signed_headers(raw: bytes, secret: str = APP_SECRET) -> dict[str, str]:
    return {"X-Hub-Signature-256": sign(secret.encode(), raw), "Content-Type": "application/json"}


def post(client, payload: dict | bytes, *, secret: str = APP_SECRET):
    raw = payload if isinstance(payload, bytes) else body_bytes(payload)
    return client.post("/webhooks/whatsapp", content=raw, headers=signed_headers(raw, secret))


class Clock:
    def __init__(self) -> None:
        self.value = datetime.now(timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


@dataclass
class Stack:
    repository: SQLiteConversationRepository
    store: SQLiteChannelStore
    ingestor: WebhookIngestor
    inbound: InboundProcessor
    outbound: OutboxDispatcher
    transport: FakeWhatsAppTransport
    agent: object
    policy: QueuePolicy


def catalog():
    return load_synthetic_catalog(ROOT / "fixtures/salon.versioned.synthetic.json")


def stack(tmp_path: Path, *, transport: FakeWhatsAppTransport | None = None, clock: Clock | None = None,
          path: Path | None = None, **changes: object) -> Stack:
    config = settings(tmp_path, **({"storage_path": path} if path else {}), **changes)
    repository = SQLiteConversationRepository(config.storage_path)
    store, ingestor = configured_channel(config, repository)
    if clock is not None:
        store.clock = clock
    agent = build_agent(catalog(), conversation_repository=repository)
    policy = queue_policy(config)
    transport = transport or FakeWhatsAppTransport()
    return Stack(repository, store, ingestor, InboundProcessor(store, agent, policy),
                 OutboxDispatcher(store, transport, policy), transport, agent, policy)
