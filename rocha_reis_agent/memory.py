"""Adaptadores locais: cópias isoladas e transação serializada em um processo."""

from copy import deepcopy
from threading import RLock
from contextlib import contextmanager
from collections.abc import Iterator

from rocha_reis_agent.domain.conversations import Conversation
from rocha_reis_agent.knowledge import KnowledgeBase
from rocha_reis_agent.knowledge_catalog import FactRevision, KnowledgeCatalog


class InMemoryConversationRepository:
    def __init__(self) -> None:
        self._items: dict[str, Conversation] = {}
        self._lock = RLock()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._lock:
            yield

    def get(self, conversation_id: str) -> Conversation | None:
        return deepcopy(self._items.get(conversation_id))

    def save(self, conversation: Conversation) -> None:
        self._items[conversation.conversation_id] = deepcopy(conversation)

    def count(self) -> int:
        return len(self._items)


class InMemoryKnowledgeRepository:
    def __init__(self, knowledge: KnowledgeBase) -> None:
        self._knowledge = knowledge

    def get(self) -> KnowledgeBase:
        return self._knowledge


class VersionedKnowledgeRepository:
    """Autoria local confiável, sem endpoint de alteração ou persistência durável."""

    def __init__(self, catalog: KnowledgeCatalog) -> None:
        self._catalog = KnowledgeCatalog.model_validate(catalog.model_dump())
        self._lock = RLock()

    def get(self) -> KnowledgeCatalog:
        with self._lock:
            return self._catalog

    def append_revision(self, revision: FactRevision) -> None:
        with self._lock:
            self._catalog = self._catalog.append(revision)
