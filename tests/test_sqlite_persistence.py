"""Integração com arquivos SQLite reais, migrações, contratos e privacidade."""

from contextlib import closing
from dataclasses import replace
from datetime import timedelta
import json
import logging
from pathlib import Path
import sqlite3

import pytest

from rocha_reis_agent.bootstrap import build_agent
from rocha_reis_agent.config import ConfigurationError, Settings, load_settings
from rocha_reis_agent.domain.contracts import InboundMessage, StorageError, SimulationError, ConversationState as State
from rocha_reis_agent.domain.conversations import HistoryEntry, SimulationLimits, utc_now
from rocha_reis_agent.persistence.sqlite import SQLiteConversationRepository, APPLICATION_ID, MIGRATIONS, SCHEMA_VERSION
from rocha_reis_agent.main import create_app
from fastapi.testclient import TestClient


def deliver(agent, event="e1", text="Quanto custa o corte?", client="cliente-a"):
    return agent.handle(InboundMessage(event, client, text, utc_now()))


def make(tmp_path, knowledge, **kwargs):
    repository = SQLiteConversationRepository(tmp_path / "test.synthetic.sqlite3", **kwargs)
    return build_agent(knowledge, conversation_repository=repository), repository


def query(repository, sql, parameters=()):
    with closing(sqlite3.connect(repository.path)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        rows = connection.execute(sql, parameters).fetchall()
        connection.commit()
        return rows


def test_create_history_context_result_timestamps_and_copy_isolation(tmp_path, knowledge):
    agent, repository = make(tmp_path, knowledge)
    result = deliver(agent)
    saved = agent.conversations.snapshot("cliente-a")
    assert result.reply and "R$ 45,00" in result.reply
    assert saved.service_context == ("corte",) and len(saved.history) == 2
    assert saved.event_results["e1"] == result
    assert saved.history[1].fact_references == result.fact_references
    assert saved.history[1].prompt_version == "v1"
    assert query(repository, "SELECT client_id,state,version FROM conversations") == [("cliente-a", "AUTO", 1)]
    assert len(query(repository, "SELECT created_at,updated_at FROM conversations")[0][0]) > 20
    assert query(repository, "SELECT kind,status FROM events") == [("client_message", "COMPLETED")]
    assert {r[0] for r in query(repository, "SELECT role FROM messages")} == {"client", "assistant"}
    saved.history.clear()
    assert len(agent.conversations.snapshot("cliente-a").history) == 2


def test_missing_session_rejected_without_creating_one(tmp_path, knowledge):
    agent, repository = make(tmp_path, knowledge)
    with pytest.raises(SimulationError, match="não encontrada"):
        agent.conversations.snapshot("missing")
    assert repository.count() == 0


def test_same_text_distinct_event_ids_and_client_scoped_key(tmp_path, knowledge):
    agent, repository = make(tmp_path, knowledge)
    assert deliver(agent, "e1").reply
    assert deliver(agent, "e2").reply
    assert deliver(agent, "e1", client="cliente-b").reply
    assert len(query(repository, "SELECT * FROM events")) == 3
    assert len(agent.conversations.snapshot("cliente-a").history) == 4


def test_payload_conflict_rolls_back_and_duplicate_reuses_original_metadata(tmp_path, knowledge):
    agent, repository = make(tmp_path, knowledge)
    first = deliver(agent)
    before = agent.conversations.snapshot("cliente-a")
    with pytest.raises(SimulationError, match="conteúdo diferente"):
        deliver(agent, text="Quero um humano")
    assert agent.conversations.snapshot("cliente-a") == before
    deliver(agent, "e2", "Quero um humano")
    duplicate = deliver(agent)
    assert duplicate.duplicate and duplicate.reply is None
    assert duplicate.state == first.state
    assert duplicate.fact_references == first.fact_references
    assert agent.conversations.snapshot("cliente-a").state == State.WAITING_HUMAN
    assert any(row["operation"] == "deduplication" for row in repository.audit("cliente-a"))


@pytest.mark.parametrize("sql,parameters", [
    ("INSERT INTO context VALUES('missing','[]',0)", ()),
    ("UPDATE conversations SET state='ADMIN'", ()),
    ("UPDATE context SET age=-1", ()),
    ("UPDATE context SET services='broken-json'", ()),
    ("UPDATE messages SET role='system'", ()),
    ("INSERT INTO events SELECT * FROM events", ()),
    ("UPDATE events SET status='STARTED'", ()),
    ("UPDATE events SET digest='bad'", ()),
])
def test_database_constraints_protect_invalid_writes(tmp_path, knowledge, sql, parameters):
    agent, repository = make(tmp_path, knowledge)
    deliver(agent)
    before = agent.conversations.snapshot("cliente-a")
    with pytest.raises(sqlite3.IntegrityError):
        query(repository, sql, parameters)
    assert agent.conversations.snapshot("cliente-a") == before


def test_invalid_domain_record_rolls_back(tmp_path, knowledge):
    agent, repository = make(tmp_path, knowledge)
    deliver(agent)
    before = agent.conversations.snapshot("cliente-a")
    with pytest.raises(StorageError):
        with agent.conversations.edit("cliente-a") as value:
            value.state = "INVALID"
    assert agent.conversations.snapshot("cliente-a") == before


def test_bounded_history_does_not_remove_persistent_idempotency(tmp_path, knowledge):
    limits = SimulationLimits(max_history_entries=3)
    repository = SQLiteConversationRepository(tmp_path / "test.synthetic.sqlite3", limits=limits)
    agent = build_agent(knowledge, limits, conversation_repository=repository)
    for index in range(5):
        deliver(agent, f"e{index}", "Olá")
    assert len(query(repository, "SELECT * FROM messages")) == 3
    assert len(query(repository, "SELECT * FROM events")) == 5
    assert deliver(agent, "e0", "Olá").duplicate


def test_reset_preserves_durable_keys_and_requires_new_event_ids(tmp_path, knowledge):
    agent, repository = make(tmp_path, knowledge)
    first = deliver(agent)
    old_session = agent.conversations.snapshot("cliente-a").session_id
    agent.conversations.reset("cliente-a")
    saved = agent.conversations.snapshot("cliente-a")
    assert saved.session_id != old_session and not saved.history and not saved.service_context
    assert saved.event_results["e1"] == first
    assert deliver(agent).duplicate
    assert deliver(agent, "new-event").reply


def test_event_window_never_blocks_and_idempotency_survives_beyond_window(tmp_path, knowledge):
    """Fase 5: o limite de eventos é janela do snapshot, não bloqueio permanente."""
    limits = SimulationLimits(max_events_per_session=1, max_conversations=1)
    repository = SQLiteConversationRepository(tmp_path / "test.synthetic.sqlite3", limits=limits)
    agent = build_agent(knowledge, limits, conversation_repository=repository)
    deliver(agent)
    agent.conversations.reset("cliente-a")
    for index in range(2, 6):
        assert deliver(agent, f"e{index}").reply  # nenhum bloqueio permanente
    assert len(agent.conversations.snapshot("cliente-a").processed_events) == 1  # janela
    assert deliver(agent).duplicate  # e1, fora da janela, continua deduplicado
    with pytest.raises(SimulationError, match="conteúdo diferente"):
        deliver(agent, "e2", "Quero um humano")
    assert len(query(repository, "SELECT * FROM events")) == 5
    # Limite de conversas ATIVAS: sem conversa ociosa elegível, erro transitório explícito.
    with pytest.raises(SimulationError, match="conversas ativas"):
        agent.conversations.start("cliente-b")


def test_context_is_isolated_and_private_text_never_enters_audit_or_logs(tmp_path, knowledge, caplog):
    agent, repository = make(tmp_path, knowledge)
    marker = "SYNTHETIC_PRIVATE_MESSAGE"
    with caplog.at_level(logging.ERROR):
        deliver(agent, text="Informações sobre corte " + marker)
        other = deliver(agent, "e1", "E quanto custa?", "cliente-b")
    assert "De qual serviço" in other.reply
    assert marker not in caplog.text
    audits = json.dumps(repository.audit())
    assert marker not in audits and "cliente-a" not in audits and "cliente-b" not in audits
    assert "correlation" in audits
    assert all(row["outcome"] == "committed" for row in repository.audit())


def test_foreign_database_and_wrong_suffix_are_rejected_without_mutation(tmp_path):
    path = tmp_path / "foreign.synthetic.sqlite3"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE private_data(value)")
        connection.execute("INSERT INTO private_data VALUES('PRIVATE_STORAGE')")
        connection.commit()
    original = path.read_bytes()
    with pytest.raises(StorageError, match="não reconhecido"):
        SQLiteConversationRepository(path)
    assert path.read_bytes() == original
    with pytest.raises(StorageError, match="sufixo"):
        SQLiteConversationRepository(tmp_path / "other.db")
    assert not (tmp_path / "other.db").exists()


def test_symlink_is_rejected(tmp_path):
    target = tmp_path / "target"
    target.write_text("PRIVATE")
    link = tmp_path / "link.synthetic.sqlite3"
    link.symlink_to(target)
    with pytest.raises(StorageError, match="simbólicos"):
        SQLiteConversationRepository(link)
    assert target.read_text() == "PRIVATE"


def test_new_database_migrations_wal_permissions_and_foreign_keys(tmp_path):
    repository = SQLiteConversationRepository(tmp_path / "test.synthetic.sqlite3")
    assert query(repository, "PRAGMA user_version") == [(SCHEMA_VERSION,)]
    assert query(repository, "PRAGMA application_id") == [(APPLICATION_ID,)]
    assert query(repository, "PRAGMA journal_mode") == [("wal",)]
    assert not query(repository, "PRAGMA foreign_key_check")
    assert repository.path.stat().st_mode & 0o777 == 0o600
    assert "correlation" in {row[1] for row in query(repository, "PRAGMA table_info(audit)")}


def test_migration_from_v1_preserves_existing_audit(tmp_path):
    path = tmp_path / "old.synthetic.sqlite3"
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript((MIGRATIONS / "001_initial.sql").read_text())
        connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
        connection.execute("PRAGMA user_version=1")
        connection.execute("INSERT INTO audit(operation,entity,origin,outcome,recorded_at) VALUES('old','hash','storage','committed',?)", (utc_now().isoformat(),))
        connection.commit()
    repository = SQLiteConversationRepository(path)
    rows = repository.audit()
    assert rows[0]["operation"] == "old" and rows[0]["correlation"] == "migration-v2"
    assert query(repository, "PRAGMA user_version") == [(SCHEMA_VERSION,)]


def test_future_schema_is_rejected_without_downgrade(tmp_path):
    repository = SQLiteConversationRepository(tmp_path / "test.synthetic.sqlite3")
    query(repository, "PRAGMA user_version=99")
    with pytest.raises(StorageError, match="Versão"):
        SQLiteConversationRepository(repository.path)
    assert query(repository, "PRAGMA user_version") == [(99,)]


def test_catalog_fingerprint_prevents_silent_fixture_switch(tmp_path, knowledge):
    agent, repository = make(tmp_path, knowledge)
    deliver(agent)
    with pytest.raises(StorageError, match="Catálogo incompatível"):
        build_agent(knowledge.model_copy(update={"name": "Outra identidade fictícia"}), conversation_repository=repository)
    assert "R$ 45,00" in deliver(agent, "e2").reply


@pytest.mark.parametrize("name,value", [
    ("STORAGE_BACKEND", "postgres-production"), ("STORAGE_BACKEND", "sqlite"),
    ("STORAGE_BUSY_TIMEOUT_MS", "0"), ("STORAGE_MAX_BYTES", "1"),
    ("STORAGE_RETENTION_DAYS", "0"), ("STORAGE_AUDIT_DAYS", "366"),
    ("STORAGE_AUDIT_LIMIT", "0"), ("STORAGE_PATH", "/private/data.db"),
])
def test_configuration_is_validated_without_private_values(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigurationError) as caught:
        load_settings()
    assert value not in str(caught.value)


def test_fastapi_composes_configured_store_and_health_is_still_liveness(tmp_path):
    settings = Settings(storage_backend="sqlite", storage_path=tmp_path / "http.synthetic.sqlite3")
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert isinstance(client.app.state.conversation_repository, SQLiteConversationRepository)
        assert set(client.app.openapi()["paths"]) == {"/health"}


def test_database_deleted_does_not_silently_fall_back_to_memory(tmp_path, knowledge):
    agent, repository = make(tmp_path, knowledge)
    deliver(agent)
    repository.path.unlink()
    with pytest.raises(StorageError):
        deliver(agent, "e2")
    assert not repository.path.exists()


def test_volume_without_private_permissions_is_rejected(tmp_path, monkeypatch):
    # Repository uses os.chmod, emulate a mount ignoring chmod.
    import os
    real_chmod = os.chmod
    monkeypatch.setattr(os, "chmod", lambda path, mode: real_chmod(path, 0o644))
    with pytest.raises(StorageError, match="0600"):
        SQLiteConversationRepository(tmp_path / "unsafe.synthetic.sqlite3")
