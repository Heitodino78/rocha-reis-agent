"""Rollback e concorrência sincronizados por barreiras/eventos, sem sleeps."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import logging
import sqlite3
from threading import Barrier, Event, Lock

import pytest

from rocha_reis_agent.ai.fake import FakeAIProvider
from rocha_reis_agent.application.handoff import HandoffAction as Action
from rocha_reis_agent.bootstrap import build_agent
from rocha_reis_agent.domain.contracts import ConcurrentUpdate, ConversationState as State, InboundMessage, StorageError
from rocha_reis_agent.domain.conversations import utc_now
from rocha_reis_agent.persistence.sqlite import SQLiteConversationRepository


def message(event="e1", text="Quanto custa o corte?", client="cliente-a"):
    return InboundMessage(event, client, text, utc_now())


def setup(tmp_path, knowledge):
    path = tmp_path / "test.synthetic.sqlite3"
    repository = SQLiteConversationRepository(path)
    agent = build_agent(knowledge, conversation_repository=repository)
    return path, agent, repository


@pytest.mark.parametrize("stage", ["after_conversation", "after_events", "after_history", "before_commit"])
def test_partial_write_failure_rolls_back_all_business_data(tmp_path, knowledge, caplog, stage):
    path, agent, repository = setup(tmp_path, knowledge)
    agent.handle(message("before", "Informações sobre corte"))
    before = agent.conversations.snapshot("cliente-a")
    marker = "PRIVATE_WRITE_FAILURE"
    def fail(current):
        if current == stage:
            raise sqlite3.OperationalError(marker)
    repository.fault_hook = fail
    with caplog.at_level(logging.ERROR), pytest.raises(StorageError) as caught:
        agent.handle(message("failure", "Quero um humano"))
    assert marker not in str(caught.value) and marker not in caplog.text
    assert agent.conversations.snapshot("cliente-a") == before
    assert any(a["operation"] == "failure_rollback" and a["outcome"] == "rolled_back" for a in repository.audit())
    repository.fault_hook = lambda current: None
    assert agent.handle(message("failure", "Quero um humano")).state == State.WAITING_HUMAN
    assert agent.handle(message("failure", "Quero um humano")).duplicate


def test_failed_first_message_leaves_no_conversation_or_orphan_rows(tmp_path, knowledge):
    path, agent, repository = setup(tmp_path, knowledge)
    def fail(stage):
        if stage == "after_events": raise RuntimeError("SYNTHETIC_FAILURE")
    repository.fault_hook = fail
    with pytest.raises(StorageError): agent.handle(message())
    assert repository.count() == 0
    with closing(sqlite3.connect(path)) as connection:
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
        for table in ("events", "messages", "context", "transitions"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_user_exception_in_edit_rolls_back_without_persisting_changes(tmp_path, knowledge):
    path, agent, repository = setup(tmp_path, knowledge)
    agent.handle(message())
    before = agent.conversations.snapshot("cliente-a")
    with pytest.raises(StorageError):
        with agent.conversations.edit("cliente-a") as conversation:
            conversation.service_context = ("escova",)
            raise RuntimeError("PRIVATE_SYNTHETIC")
    assert agent.conversations.snapshot("cliente-a") == before


def test_write_lock_exhausts_bounded_attempts_and_keeps_state(tmp_path, knowledge, caplog):
    path, agent, repository = setup(tmp_path, knowledge)
    repository.busy_timeout_ms = 1
    agent.handle(message())
    before = agent.conversations.snapshot("cliente-a")
    with closing(sqlite3.connect(path, isolation_level=None)) as lock:
        lock.execute("BEGIN IMMEDIATE")
        with caplog.at_level(logging.ERROR), pytest.raises(ConcurrentUpdate, match="Reenvie"):
            agent.handle(message("blocked"))
        lock.rollback()
    assert agent.conversations.snapshot("cliente-a") == before
    assert len([r for r in caplog.records if r.getMessage() == "storage_operation_failed"]) == 3
    assert agent.handle(message("blocked")).reply


class FirstPairBarrier:
    def __init__(self, barrier):
        self.barrier = barrier
        self.calls = 0
        self.lock = Lock()

    def generate(self, request, *, timeout_seconds):
        with self.lock:
            self.calls += 1
            number = self.calls
        if number <= 2:
            self.barrier.wait(timeout=10)
        return FakeAIProvider().generate(request, timeout_seconds=timeout_seconds)


@pytest.mark.parametrize("same_client,same_event", [(True, False), (False, False), (True, True)])
def test_concurrent_events_with_independent_repositories(tmp_path, knowledge, same_client, same_event):
    path, first, repository = setup(tmp_path, knowledge)
    second = build_agent(knowledge, conversation_repository=SQLiteConversationRepository(path))
    provider = FirstPairBarrier(Barrier(2))
    first.provider = second.provider = provider
    a = message("e1", "Informações sobre corte", "cliente-a")
    b = message("e1" if same_event else "e2", "Informações sobre corte", "cliente-a" if same_client else "cliente-b")
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(first.handle, a), pool.submit(second.handle, b)]
        results = [item.result(timeout=20) for item in pending]
    assert sum(result.duplicate for result in results) == int(same_event)
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT count(*) FROM events").fetchone()[0] == (1 if same_event else 2)
        assert connection.execute("SELECT count(*) FROM messages").fetchone()[0] == (2 if same_event else 4)
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    assert repository.count() == (1 if same_client else 2)
    assert first.conversations.snapshot("cliente-a").service_context == ("corte",)


def test_stale_context_update_cannot_overwrite_committed_context(tmp_path, knowledge):
    path, first, repository = setup(tmp_path, knowledge)
    first.handle(message("initial"))
    second = build_agent(knowledge, conversation_repository=SQLiteConversationRepository(path))
    loaded, release = Event(), Event()
    def stale():
        with pytest.raises(ConcurrentUpdate):
            with first.conversations.edit("cliente-a") as conversation:
                loaded.set()
                assert release.wait(10)
                conversation.service_context = ("corte",)
                conversation.context_age = 999
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(stale)
        assert loaded.wait(10)
        second.handle(message("latest", "Informações sobre escova"))
        release.set()
        pending.result(timeout=15)
    saved = first.conversations.snapshot("cliente-a")
    assert saved.service_context == ("escova",) and saved.context_age == 0
    assert set(saved.processed_events) == {"initial", "latest"}


def test_human_transition_wins_over_inflight_generation_without_database_lock(tmp_path, knowledge):
    path, first, repository = setup(tmp_path, knowledge)
    first.handle(message("initial", "Informações sobre corte"))
    second = build_agent(knowledge, conversation_repository=SQLiteConversationRepository(path))
    entered, release = Event(), Event()
    class PausedProvider:
        def generate(self, request, *, timeout_seconds):
            entered.set()
            assert release.wait(10)
            return FakeAIProvider().generate(request, timeout_seconds=timeout_seconds)
    first.provider = PausedProvider()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(first.handle, message("inflight"))
        assert entered.wait(10)
        # This commit must finish BEFORE provider release: no long DB transaction.
        assert second.handle(message("human", "Quero um humano")).state == State.WAITING_HUMAN
        second.operator_action("cliente-a", Action.ASSUME, operator_id="operador-local")
        release.set()
        result = pending.result(timeout=15)
    assert result.reply is None and result.state == State.HUMAN_ACTIVE
    saved = first.conversations.snapshot("cliente-a")
    assert saved.state == State.HUMAN_ACTIVE and "inflight" in saved.processed_events
    assert len([h for h in saved.history if h.event_id == "inflight"]) == 1


def test_optimistic_version_conflict_is_detected_without_memory_lock(tmp_path, knowledge):
    path, first, repository = setup(tmp_path, knowledge)
    first.handle(message())
    second_repository = SQLiteConversationRepository(path)
    with pytest.raises(ConcurrentUpdate):
        with repository.transaction():
            old = repository.get("cliente-a")
            with second_repository.transaction():
                new = second_repository.get("cliente-a")
                new.context_age += 1
                second_repository.save(new)
            old.context_age += 5
            repository.save(old)
    assert first.conversations.snapshot("cliente-a").context_age == 1
