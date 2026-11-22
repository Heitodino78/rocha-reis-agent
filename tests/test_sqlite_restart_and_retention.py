"""Reinício real entre processos, interrupção abrupta, CLI e exclusão controlada."""

from contextlib import closing
from datetime import timedelta
from io import StringIO
import json
import os
from pathlib import Path
import sqlite3
import selectors
import subprocess
import sys

import pytest

from rocha_reis_agent.bootstrap import build_agent
from rocha_reis_agent.catalog_io import load_synthetic_catalog
from rocha_reis_agent.domain.contracts import InboundMessage, StorageError
from rocha_reis_agent.domain.conversations import utc_now
from rocha_reis_agent.persistence.sqlite import SQLiteConversationRepository
from rocha_reis_agent.simulator import Simulator

ROOT = Path(__file__).resolve().parents[1]
WORKER = Path(__file__).with_name("persistence_worker.py")


def environment():
    values = {key: value for key, value in os.environ.items() if not key.startswith(("APP_", "SIM_", "AI_", "STORAGE_", "WHATSAPP_", "CHANNEL_"))}
    values["PYTHONPATH"] = str(ROOT)
    return values


def cli(path, script, cwd):
    result = subprocess.run([sys.executable, "-m", "rocha_reis_agent.simulator", "--fixture", "--database", str(path)],
                            input=script, text=True, capture_output=True, cwd=cwd, env=environment(), timeout=20)
    assert result.returncode == 0, result.stderr
    return result.stdout


def worker(path, operation, cwd):
    return subprocess.run([sys.executable, str(WORKER), str(path), operation],
                          capture_output=True, text=True, cwd=cwd, env=environment(), timeout=20)


def catalog():
    return load_synthetic_catalog(ROOT / "fixtures/salon.versioned.synthetic.json")


def deliver(agent, event="e1", text="Quanto custa o corte?"):
    return agent.handle(InboundMessage(event, "cliente-a", text, utc_now()))


def test_real_process_restart_preserves_context_waiting_human_history_audit_and_dedup(tmp_path):
    path = tmp_path / "restart.synthetic.sqlite3"
    first = cli(path, "/start cliente-a\n/send service Informações sobre corte\n/send human Quero um humano\n/state\n/quit\n", tmp_path)
    assert "WAITING_HUMAN" in first
    recovered = worker(path, "inspect", tmp_path)
    assert recovered.returncode == 0, recovered.stderr
    data = json.loads(recovered.stdout)
    assert data["state"] == "WAITING_HUMAN" and data["context"] == ["corte"]
    assert data["events"] == ["human", "service"] and data["history"] == 4
    assert "state_client_request" in data["audit"]
    second = cli(path, "/use cliente-a\n/send waiting E quanto custa?\n/send human Quero um humano\n/operator assume\n/quit\n", tmp_path)
    assert "[automático pausado; mensagem registrada]" in second
    assert "[duplicata ignorada]" in second
    assert "R$" not in second and "HUMAN_ACTIVE" in second
    third = cli(path, "/use cliente-a\n/send active E quanto custa?\n/operator resolve\n/send resolved Pode retomar?\n/operator resolve\n/operator resume\n/send price E quanto custa?\n/provenance\n/storage-audit\n/quit\n", tmp_path)
    assert third.count("[automático pausado; mensagem registrada]") == 2
    assert "Estado: AUTO" in third and "R$ 45,00" in third
    assert "prompt=v1" in third and "price:corte@1" in third
    assert "state_resume: committed" in third
    fourth = cli(path, "/use cliente-a\n/send price E quanto custa?\n/state\n/quit\n", tmp_path)
    assert "[duplicata ignorada]" in fourth and "R$" not in fourth


def test_abrupt_process_exit_rolls_back_incomplete_operation_then_retries(tmp_path):
    path = tmp_path / "crash.synthetic.sqlite3"
    cli(path, "/start cliente-a\n/send initial Informações sobre corte\n/quit\n", tmp_path)
    before = json.loads(worker(path, "inspect", tmp_path).stdout)
    crashed = worker(path, "crash", tmp_path)
    assert crashed.returncode == 73
    after = worker(path, "inspect", tmp_path)
    assert after.returncode == 0, after.stderr
    assert json.loads(after.stdout) == before
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    retried = cli(path, "/use cliente-a\n/send interrupted Quero um humano\n/send interrupted Quero um humano\n/state\n/quit\n", tmp_path)
    assert retried.count("Nenhum atendente real foi notificado") == 1
    assert "[duplicata ignorada]" in retried and "WAITING_HUMAN" in retried


def test_two_processes_duplicate_inflight_event_commit_only_once(tmp_path):
    path = tmp_path / "race.synthetic.sqlite3"
    cli(path, "/start cliente-a\n/quit\n", tmp_path)
    processes = [subprocess.Popen([sys.executable, str(WORKER), str(path), "race"],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, cwd=tmp_path, env=environment()) for _ in range(2)]
    try:
        for process in processes:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                assert selector.select(timeout=15), "Worker did not reach generation barrier"
                assert process.stdout.readline().strip() == "READY"
        results = []
        for process in processes:
            output, errors = process.communicate("COMMIT\n", timeout=20)
            assert process.returncode == 0, errors
            results.append(json.loads(output))
        assert sum(item["duplicate"] for item in results) == 1
        assert sum(item["reply"] for item in results) == 1
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)
    data = json.loads(worker(path, "inspect", tmp_path).stdout)
    assert data["events"] == ["shared"] and data["history"] == 2


def test_parallel_initialization_of_new_database_is_safe(tmp_path):
    path = tmp_path / "new.synthetic.sqlite3"
    processes = [subprocess.Popen([sys.executable, str(WORKER), str(path), "initialize"],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, cwd=tmp_path, env=environment()) for _ in range(3)]
    try:
        for process in processes:
            output, errors = process.communicate(timeout=20)
            assert process.returncode == 0, errors
            assert output.strip() == "INITIALIZED"
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)


def test_delete_requires_confirmation_redacts_text_and_prevents_reprocessing(tmp_path):
    repository = SQLiteConversationRepository(tmp_path / "delete.synthetic.sqlite3")
    agent = build_agent(catalog(), conversation_repository=repository)
    deliver(agent, text="Informações sobre corte SYNTHETIC_PRIVATE_TEXT")
    before = agent.conversations.snapshot("cliente-a")
    for confirmation in ("", "DELETE_SYNTHETIC:cliente-b", "yes"):
        with pytest.raises(StorageError, match="confirmação"):
            repository.retire("cliente-a", confirmation=confirmation)
    assert agent.conversations.snapshot("cliente-a") == before
    repository.retire("cliente-a", confirmation="DELETE_SYNTHETIC:cliente-a")
    redacted = agent.conversations.snapshot("cliente-a")
    assert redacted.closed and not redacted.history and not redacted.service_context
    assert not redacted.event_results["e1"].reply and not redacted.event_results["e1"].fact_references
    assert deliver(agent, text="Informações sobre corte SYNTHETIC_PRIVATE_TEXT").duplicate
    with pytest.raises(StorageError, match="excluída"):
        agent.conversations.reset("cliente-a")
    with pytest.raises(Exception, match="Sessão encerrada"):
        deliver(agent, "new")
    with closing(sqlite3.connect(repository.path)) as connection:
        assert connection.execute("SELECT count(*) FROM messages").fetchone()[0] == 0
        assert "SYNTHETIC_PRIVATE_TEXT" not in str(connection.execute("SELECT result FROM events").fetchall())
    assert any(a["operation"] == "synthetic_deleted" for a in repository.audit("cliente-a"))


def test_retention_uses_last_update_and_does_not_touch_recent_conversations(tmp_path):
    repository = SQLiteConversationRepository(tmp_path / "retention.synthetic.sqlite3", retention_days=2)
    agent = build_agent(catalog(), conversation_repository=repository)
    deliver(agent)
    agent.handle(InboundMessage("b1", "cliente-b", "Olá", utc_now()))
    with closing(sqlite3.connect(repository.path)) as connection:
        connection.execute("UPDATE conversations SET updated_at=? WHERE id='cliente-a'", ((utc_now() - timedelta(days=3)).isoformat(),))
        connection.commit()
    with pytest.raises(StorageError): repository.purge_expired(confirmation="yes")
    assert repository.purge_expired(confirmation="PURGE_SYNTHETIC") == 1
    assert not agent.conversations.snapshot("cliente-a").history
    assert agent.conversations.snapshot("cliente-b").history
    assert repository.purge_expired(confirmation="PURGE_SYNTHETIC") == 0


def test_audit_capacity_is_independent_of_context_and_event_retention(tmp_path):
    repository = SQLiteConversationRepository(tmp_path / "audit.synthetic.sqlite3", audit_limit=3)
    agent = build_agent(catalog(), conversation_repository=repository)
    for i in range(5): deliver(agent, f"e{i}")
    assert len(repository.audit()) == 3
    assert len(agent.conversations.snapshot("cliente-a").processed_events) == 5
    with closing(sqlite3.connect(repository.path)) as connection:
        connection.execute("UPDATE audit SET recorded_at=?", ((utc_now() - timedelta(days=31)).isoformat(),))
        connection.commit()
    assert deliver(agent).duplicate
    assert len(repository.audit()) == 1
    assert repository.audit()[0]["operation"] == "deduplication"


def test_retention_partial_failure_rolls_back(tmp_path):
    repository = SQLiteConversationRepository(tmp_path / "rollback.synthetic.sqlite3")
    agent = build_agent(catalog(), conversation_repository=repository)
    deliver(agent)
    before = agent.conversations.snapshot("cliente-a")
    def fail(stage):
        if stage == "before_retention_commit": raise sqlite3.OperationalError("PRIVATE_SYNTHETIC")
    repository.fault_hook = fail
    with pytest.raises(StorageError): repository.retire("cliente-a", confirmation="DELETE_SYNTHETIC:cliente-a")
    assert agent.conversations.snapshot("cliente-a") == before


def test_database_capacity_refuses_writes_without_evicting_keys(tmp_path):
    repository = SQLiteConversationRepository(tmp_path / "bounded.synthetic.sqlite3", max_bytes=1048576)
    agent = build_agent(catalog(), conversation_repository=repository)
    deliver(agent)
    before = agent.conversations.snapshot("cliente-a")
    repository.max_bytes = repository.path.stat().st_size
    with pytest.raises(StorageError, match="Limite do banco"):
        deliver(agent, "new")
    assert agent.conversations.snapshot("cliente-a") == before


def test_cli_destructive_commands_require_explicit_confirmation_and_sqlite(tmp_path):
    repository = SQLiteConversationRepository(tmp_path / "cli.synthetic.sqlite3")
    agent = build_agent(catalog(), conversation_repository=repository)
    output = StringIO()
    Simulator(agent, output).run(StringIO("/start cliente-a\n/send e1 Olá\n/reset\n/reset CONFIRM_SYNTHETIC_RESET\n/send e1 Olá\n/delete\n/delete DELETE_SYNTHETIC:cliente-a\n/quit\n"))
    text = output.getvalue()
    assert "Use /reset CONFIRM_SYNTHETIC_RESET" in text
    assert "eventos preservados" in text and "[duplicata ignorada]" in text
    assert "Exclusão exige confirmação" in text and "Conteúdo sintético excluído" in text
    memory_output = StringIO()
    Simulator(build_agent(catalog()), memory_output).run(StringIO("/delete DELETE_SYNTHETIC:cliente-a\n/quit\n"))
    assert "exige banco sintético" in memory_output.getvalue()


def test_scope_is_rechecked_before_deletion_not_only_at_initialization(tmp_path):
    repository = SQLiteConversationRepository(tmp_path / "scope.synthetic.sqlite3")
    agent = build_agent(catalog(), conversation_repository=repository)
    deliver(agent)
    with closing(sqlite3.connect(repository.path)) as connection:
        connection.execute("UPDATE metadata SET value='outside-authorized-scope' WHERE key='purpose'")
        connection.commit()
    with pytest.raises(StorageError, match="escopo"):
        repository.retire("cliente-a", confirmation="DELETE_SYNTHETIC:cliente-a")
    with closing(sqlite3.connect(repository.path)) as connection:
        assert connection.execute("SELECT count(*) FROM messages").fetchone()[0] == 2
