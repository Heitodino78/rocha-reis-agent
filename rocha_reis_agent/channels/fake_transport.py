"""Transporte WhatsApp simulado: nenhum socket, nenhuma credencial, nenhum envio real."""

from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Callable, Literal
from uuid import uuid4

from rocha_reis_agent.channels.contracts import (
    OutboundText, SendAccepted, SendOutcome, SendRejected, SendUncertain,
)

Behavior = Literal[
    "accept",            # Meta aceita e devolve ID
    "reject",            # rejeição definitiva (ex.: destinatário inválido)
    "throttle",          # rejeição explícita reprocessável (limite de taxa)
    "not_sent",          # falha antes de transmitir: garantidamente não enviada
    "timeout",           # timeout após enviar: Meta PODE ter aceitado
    "ambiguous",         # resposta 5xx/ilegível: resultado incerto
    "crash",             # exceção inesperada do adaptador
]


@dataclass(frozen=True, slots=True)
class FakeDelivery:
    operation_id: str
    recipient_key: str = field(repr=False)
    body: str = field(repr=False)
    provider_message_id: str | None
    behavior: str
    # Verdadeiro quando, na simulação, a mensagem chegou à "Meta" (aceita ou incerta-aceita).
    reached_provider: bool


class FakeWhatsAppTransport:
    def __init__(
        self, default: Behavior = "accept", *, script: list[Behavior] | None = None,
        on_send: Callable[[OutboundText], None] | None = None,
    ) -> None:
        self.default = default
        self.script: deque[Behavior] = deque(script or [])
        self.deliveries: list[FakeDelivery] = []
        self.on_send = on_send
        self._lock = Lock()

    def send_text(self, message: OutboundText, *, timeout_seconds: float) -> SendOutcome:
        if timeout_seconds <= 0:
            raise ValueError("Timeout inválido.")
        if self.on_send is not None:
            self.on_send(message)
        with self._lock:
            behavior = self.script.popleft() if self.script else self.default
            provider_id = f"wamid.FAKE{uuid4().hex}" if behavior in ("accept", "timeout", "ambiguous") else None
            self.deliveries.append(FakeDelivery(
                message.operation_id, message.recipient.identity_key, message.body,
                provider_id, behavior, provider_id is not None,
            ))
        if behavior == "accept":
            return SendAccepted(provider_id or "")
        if behavior == "reject":
            return SendRejected("fake_rejected", retryable=False)
        if behavior in ("throttle", "not_sent"):
            return SendRejected(f"fake_{behavior}", retryable=True)
        if behavior in ("timeout", "ambiguous"):
            return SendUncertain(f"fake_{behavior}")
        raise RuntimeError("Falha simulada do adaptador.")

    def accepted_for(self, operation_id: str) -> list[FakeDelivery]:
        return [d for d in self.deliveries if d.operation_id == operation_id and d.reached_provider]
