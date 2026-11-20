"""Testes herméticos: configuração isolada e conexões de rede bloqueadas."""

import socket
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import NoReturn
from itertools import count
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from rocha_reis_agent.main import create_app
from rocha_reis_agent.application.service import AgentService
from rocha_reis_agent.domain.contracts import AgentResult, InboundMessage
from rocha_reis_agent.knowledge import KnowledgeBase, load_synthetic_knowledge
from rocha_reis_agent.simulator import build_agent
from rocha_reis_agent.ai.fake import FakeAIProvider


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    import os
    for name in list(os.environ):
        if name.upper().startswith(("APP_", "SIM_", "AI_", "STORAGE_", "WHATSAPP_", "CHANNEL_")):
            monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("Rede proibida nos testes locais.")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket.socket, "sendto", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def knowledge() -> KnowledgeBase:
    return load_synthetic_knowledge(
        Path(__file__).resolve().parents[1] / "fixtures" / "salon.synthetic.json"
    )


@pytest.fixture
def agent(knowledge: KnowledgeBase) -> AgentService:
    return build_agent(knowledge)


@pytest.fixture
def send(agent: AgentService) -> Callable[..., AgentResult]:
    sequence = count(1)

    def deliver(
        text: str, conversation_id: str = "cliente-a", event_id: str | None = None
    ) -> AgentResult:
        return agent.handle(InboundMessage(
            event_id or f"auto-evento-{next(sequence)}", conversation_id, text,
            datetime(2026, 11, 19, 12, tzinfo=timezone.utc),
        ))

    return deliver


@pytest.fixture
def provider_spy():
    class SpyProvider:
        def __init__(self):
            self.requests = []
            self.transform = lambda value, request: value

        def generate(self, request, *, timeout_seconds):
            self.requests.append(request)
            value = FakeAIProvider().generate(request, timeout_seconds=timeout_seconds)
            return self.transform(value.model_dump(mode="json"), request)

    return SpyProvider()
