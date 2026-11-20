"""Apoio à homologação offline: autorização sintética 0600 e pilha com guarda obrigatória."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

from rocha_reis_agent.bootstrap import (
    build_agent, configured_channel, configured_dispatcher, configured_limits, configured_repository, queue_policy,
)
from rocha_reis_agent.channels.fake_transport import FakeWhatsAppTransport
from rocha_reis_agent.channels.service import InboundProcessor
from whatsapp_support import CLIENT_A, PHONE_ID, catalog, settings

AUTH_ID = "HML-SINTETICO-001"


def authorization_document(**changes: object) -> dict:
    now = datetime.now(timezone.utc)
    document = {
        "schema": "rocha-reis-homolog-authorization-v1",
        "authorization_id": AUTH_ID,
        "environment": "homologation",
        "authorized_by": "Responsável fictício",
        "executor": "Executor fictício",
        "phone_number_id": PHONE_ID,
        "allowed_recipients": [CLIENT_A, "BR.99887766"],
        "valid_from": (now - timedelta(hours=1)).isoformat(),
        "valid_until": (now + timedelta(hours=24)).isoformat(),
        "limits": {"max_messages_total": 10, "max_messages_per_recipient": 5,
                   "max_messages_per_run": 5, "max_messages_per_minute": 3},
        "operations": ["receive_webhook", "send_text_reply"],
        "confirmation": f"AUTORIZO HOMOLOGACAO {AUTH_ID}",
    }
    document.update(changes)
    return document


def write_authorization(directory: Path, document: dict | None = None, *, mode: int = 0o600) -> Path:
    path = directory / "homolog-authorization.json"
    path.write_text(json.dumps(document or authorization_document()), encoding="utf-8")
    os.chmod(path, mode)
    return path


def homolog_settings(tmp_path: Path, authorization: Path | None = None, **changes: object):
    values: dict[str, object] = dict(app_env="homologation", storage_path=tmp_path / "ensaio.homolog.synthetic.sqlite3")
    values.update(changes)
    if "homolog_authorization_file" not in values:
        # Só grava o documento padrão quando nenhum arquivo foi indicado (não sobrescrever).
        values["homolog_authorization_file"] = authorization or write_authorization(tmp_path)
    return settings(tmp_path, **values)


@dataclass
class HomologStack:
    config: object
    repository: object
    store: object
    ingestor: object
    inbound: InboundProcessor
    outbound: object
    transport: FakeWhatsAppTransport
    agent: object


def homolog_stack(tmp_path: Path, *, transport: FakeWhatsAppTransport | None = None, clock=None,
                  run_id: str | None = None, config=None, **changes: object) -> HomologStack:
    config = config or homolog_settings(tmp_path, **changes)
    repository = configured_repository(config)
    store, ingestor = configured_channel(config, repository)
    if clock is not None:
        store.clock = clock
    agent = build_agent(catalog(), configured_limits(config), conversation_repository=repository)
    transport = transport or FakeWhatsAppTransport()
    dispatcher = configured_dispatcher(config, store, transport, run_id=run_id or uuid4().hex)
    return HomologStack(config, repository, store, ingestor, InboundProcessor(store, agent, queue_policy(config)),
                        dispatcher, transport, agent)
