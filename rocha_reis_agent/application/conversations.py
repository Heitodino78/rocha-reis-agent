"""Ciclo de sessão e atualização atômica de conversas em memória."""

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import uuid4

from rocha_reis_agent.application.ports import ConversationRepository
from rocha_reis_agent.application.retry import retry_conflicts
from rocha_reis_agent.domain.contracts import SimulationError, validate_identifier
from rocha_reis_agent.domain.conversations import (
    Conversation, SimulationLimits, StateChange, utc_now,
)


class ConversationManager:
    def __init__(
        self, repository: ConversationRepository, limits: SimulationLimits | None = None
    ) -> None:
        self.repository = repository
        self.limits = limits or SimulationLimits()

    def _new(self, conversation_id: str) -> Conversation:
        if self.repository.count() >= self.limits.max_conversations:
            raise SimulationError("Limite de conversas atingido; reinicie o simulador.")
        return Conversation(conversation_id, uuid4().hex)

    @contextmanager
    def edit(self, conversation_id: str, *, create: bool = False) -> Iterator[Conversation]:
        validate_identifier(conversation_id)
        with self.repository.transaction():
            conversation = self.repository.get(conversation_id)
            if conversation is None:
                if not create:
                    raise SimulationError("Conversa não encontrada; inicie uma sessão.")
                conversation = self._new(conversation_id)
            # O adaptador devolve cópia; falhas não salvam alterações parciais.
            yield conversation
            del conversation.history[:-self.limits.max_history_entries]
            del conversation.transitions[:-self.limits.max_history_entries]
            self.repository.save(conversation)

    @retry_conflicts
    def start(self, conversation_id: str) -> Conversation:
        validate_identifier(conversation_id)
        with self.repository.transaction():
            if self.repository.get(conversation_id) is not None:
                raise SimulationError("Conversa existente; use /use ou reinicie com /reset.")
            conversation = self._new(conversation_id)
            self.repository.save(conversation)
            return conversation

    def snapshot(self, conversation_id: str) -> Conversation:
        validate_identifier(conversation_id)
        with self.repository.transaction():
            conversation = self.repository.get(conversation_id)
            if conversation is None:
                raise SimulationError("Conversa não encontrada; inicie uma sessão.")
            return conversation

    @retry_conflicts
    def reset(self, conversation_id: str) -> None:
        """Ação administrativa local: descartar histórico e deduplicação da sessão."""
        with self.edit(conversation_id) as conversation:
            fresh = Conversation(conversation_id, uuid4().hex)
            conversation.session_id = fresh.session_id
            conversation.state = fresh.state
            conversation.closed = False
            conversation.history.clear()
            conversation.transitions.clear()
            conversation.service_context = ()
            conversation.context_age = 0
            conversation.processed_events.clear()
            conversation.event_results.clear()

    @retry_conflicts
    def close(self, conversation_id: str) -> None:
        with self.edit(conversation_id) as conversation:
            if conversation.closed:
                raise SimulationError("A sessão já foi encerrada.")
            conversation.closed = True
            conversation.transitions.append(StateChange(
                conversation.state, conversation.state, "close_session", "operador-local", utc_now()
            ))
