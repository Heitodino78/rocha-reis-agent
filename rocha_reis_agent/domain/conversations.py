"""Estado em memória, histórico limitado e auditoria de transições."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from rocha_reis_agent.domain.contracts import AgentResult, ConversationState


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class SimulationLimits:
    max_message_chars: int = 2000
    max_history_entries: int = 100
    max_events_per_session: int = 1000
    max_conversations: int = 100
    context_turns: int = 5

    def __post_init__(self) -> None:
        if any(type(value) is not int or value < 1 for value in (
            self.max_message_chars, self.max_history_entries,
            self.max_events_per_session, self.max_conversations, self.context_turns,
        )) or self.max_message_chars > 2000:
            raise ValueError("Limites inválidos; texto deve ter no máximo 2000 caracteres.")


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    role: Literal["client", "assistant", "operator"]
    text: str = field(repr=False)
    timestamp: datetime
    event_id: str | None = None
    fact_references: tuple[str, ...] = ()
    prompt_version: str | None = None
    # Fase 7: operação da outbox que levou esta mensagem (respostas do operador).
    operation_id: str | None = None


@dataclass(frozen=True, slots=True)
class StateChange:
    previous: ConversationState
    current: ConversationState
    action: str
    operator_id: str | None
    timestamp: datetime


@dataclass(slots=True)
class Conversation:
    conversation_id: str
    session_id: str
    state: ConversationState = ConversationState.AUTO
    closed: bool = False
    history: list[HistoryEntry] = field(default_factory=list, repr=False)
    transitions: list[StateChange] = field(default_factory=list)
    service_context: tuple[str, ...] = ()
    context_age: int = 0
    # Hashes de conteúdo detectam reutilização conflitante de um ID de evento.
    processed_events: dict[str, str] = field(default_factory=dict, repr=False)
    event_results: dict[str, AgentResult] = field(default_factory=dict, repr=False)
    # Arquivada: fora do limite de conversas ativas; estado e conteúdo preservados.
    # Uma nova mensagem do cliente reativa a conversa (ConversationManager.edit).
    archived: bool = False
