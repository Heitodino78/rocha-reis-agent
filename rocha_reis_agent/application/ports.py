"""Interfaces usadas pelo núcleo; adaptadores reais continuam fora do escopo."""

from contextlib import AbstractContextManager
from typing import Protocol

from rocha_reis_agent.domain.contracts import Detection
from rocha_reis_agent.domain.conversations import Conversation
from rocha_reis_agent.knowledge import KnowledgeBase
from rocha_reis_agent.knowledge_catalog import KnowledgeCatalog


class ConversationRepository(Protocol):
    """Cópias isoladas; save só é confirmado na saída bem-sucedida de transaction.

    Adaptador durável detecta snapshots vencidos com ConcurrentUpdate e confirma
    conversa/contexto/histórico/eventos/auditoria juntos. Não promete atomicidade
    com provedores ou canais externos. get/save exigem unidade de trabalho.
    """
    def transaction(self) -> AbstractContextManager[None]: ...
    def get(self, conversation_id: str) -> Conversation | None: ...
    def save(self, conversation: Conversation) -> None: ...
    def count(self) -> int: ...


class KnowledgeRepository(Protocol):
    def get(self) -> KnowledgeBase | KnowledgeCatalog: ...


class IntentDetector(Protocol):
    def detect(self, text: str, knowledge: KnowledgeBase) -> Detection: ...
