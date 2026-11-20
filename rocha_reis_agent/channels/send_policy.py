"""Autorização de envio de texto livre, independente do transporte e da IA.

Avaliada no claim da outbox, imediatamente antes de cada tentativa. Qualquer
falta de evidência resulta em bloqueio (comportamento conservador).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

# Tolerância para relógio do provedor ligeiramente adiantado em relação ao nosso.
MAX_CLOCK_SKEW = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class SendContext:
    now: datetime
    window_opened_at: datetime | None     # min(timestamp Meta, recebimento local) da mensagem do cliente
    window_seconds: float                 # janela configurada (≤ 24h, padrão 23h)
    required_state: str                   # estado da conversa quando a resposta foi gerada
    current_state: str | None
    required_session: str
    current_session: str | None
    retired: bool
    has_recipient: bool
    has_body: bool


@dataclass(frozen=True, slots=True)
class SendDecision:
    allowed: bool
    reason: str


def authorize_free_text(context: SendContext) -> SendDecision:
    """Somente respostas a uma mensagem recebida do cliente, dentro da janela."""
    if context.current_state is None or context.current_session is None:
        return SendDecision(False, "conversation_missing")
    if context.retired:
        return SendDecision(False, "conversation_deleted")
    if context.current_state != context.required_state or context.current_session != context.required_session:
        # Operador assumiu/resolveu ou sessão reiniciada depois da geração.
        return SendDecision(False, "conversation_state_changed")
    if not context.has_recipient or not context.has_body:
        return SendDecision(False, "insufficient_evidence")
    opened = context.window_opened_at
    if opened is None or opened.utcoffset() is None or opened > context.now + MAX_CLOCK_SKEW:
        return SendDecision(False, "insufficient_evidence")
    if not 0 < context.window_seconds <= 24 * 3600:
        return SendDecision(False, "insufficient_evidence")
    if context.now - opened > timedelta(seconds=context.window_seconds):
        return SendDecision(False, "outside_service_window")
    return SendDecision(True, "authorized")
