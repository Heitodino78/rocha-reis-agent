"""Fase 7 — CLI local do painel (demonstração fictícia) e migração 6 sobre dados existentes."""

from contextlib import closing
import io
import sqlite3

import pytest

from panel_support import PASSWORD, Panel, create_panel_app, panel_settings
from rocha_reis_agent.persistence.sqlite import SQLiteConversationRepository
from whatsapp_support import APP_SECRET, CONVERSATION_KEY, VERIFY, envelope, stack, text_message


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    for name, value in {
        "APP_ENV": "test", "STORAGE_BACKEND": "sqlite", "STORAGE_PATH": str(tmp_path / "channel.synthetic.sqlite3"),
        "WHATSAPP_ENABLED": "true", "WHATSAPP_VERIFY_TOKEN": VERIFY, "WHATSAPP_APP_SECRET": APP_SECRET,
        "WHATSAPP_CONVERSATION_KEY": CONVERSATION_KEY, "WHATSAPP_PHONE_NUMBER_ID": "100000000000001",
        "WHATSAPP_BUSINESS_ACCOUNT_ID": "200000000000002", "PANEL_ENABLED": "true", "PANEL_COOKIE_SECURE": "false",
    }.items():
        monkeypatch.setenv(name, value)
    return tmp_path


def test_seed_demo_and_say_create_fictitious_conversations_through_webhook(cli_env, monkeypatch, capsys):
    from rocha_reis_agent.panel import admin_cli
    assert admin_cli.main(["seed-demo"]) == 2  # exige administrador antes
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
    assert admin_cli.main(["create-admin", "--login", "admin.teste", "--name", "Admin", "--password-stdin"]) == 0
    assert admin_cli.main(["seed-demo"]) == 0
    assert admin_cli.main(["say", "--from", "5511900000106", "Quanto custa o corte?", "--process"]) == 0
    output = capsys.readouterr().out
    assert "Demonstração fictícia criada" in output and "HTTP 200" in output and "replied" in output
    app = create_panel_app(panel_settings(cli_env))
    admin = Panel(app)
    admin.login("admin.teste")
    states = sorted(i["state"] for i in admin.get("/conversations?filter=all").json()["items"])
    assert states == ["AUTO", "AUTO", "AUTO", "RESOLVED", "WAITING_HUMAN", "WAITING_HUMAN"]
    booking = admin.get("/conversations?filter=all&q=agendar").json()["items"]
    detail = admin.get(f"/conversations/{booking[0]['id']}").json()
    assert "example.invalid/booksy-sintetico" in detail["messages"][-1]["text"]  # Booksy só por link fictício


def test_panel_app_health_and_root_redirect(tmp_path):
    from fastapi.testclient import TestClient
    client = TestClient(create_panel_app(panel_settings(tmp_path)), base_url="http://127.0.0.1:8000")
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/", follow_redirects=False).headers["location"] == "/panel/"


def test_migration_6_on_genuine_v5_database_preserves_outbox_and_ledger(tmp_path):
    from rocha_reis_agent.persistence.sqlite import APPLICATION_ID, MIGRATIONS, _statements
    path = tmp_path / "fase6.synthetic.sqlite3"
    with closing(sqlite3.connect(path, isolation_level=None)) as connection:
        connection.execute("BEGIN")
        for number in range(1, 6):
            for statement in _statements(next(MIGRATIONS.glob(f"{number:03d}_*.sql")).read_text(encoding="utf-8")):
                connection.execute(statement)
        connection.execute("PRAGMA user_version=5")
        connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
        now = "2026-11-19T12:00:00+00:00"
        connection.execute("INSERT INTO conversations(id,session_id,client_id,state,closed,version,created_at,updated_at) "
                           "VALUES('wa-legado','s1','wa-legado','AUTO',0,1,?,?)", (now, now))
        connection.execute("INSERT INTO channel_inbox(channel,business_number_id,dedup_key,kind,digest,payload,status,"
                           "conversation_id,attempts,next_attempt_at,source_timestamp,received_at,updated_at) VALUES("
                           "'whatsapp','100','msg:legado-0001','message',?,'{}','DONE','wa-legado',1,?,?,?,?)",
                           ("a" * 64, now, now, now, now))
        connection.execute("INSERT INTO channel_outbox(id,channel,business_number_id,conversation_id,session_id,"
                           "source_inbox_id,source_event_id,required_state,recipient,body,window_opened_at,status,"
                           "attempts,next_attempt_at,provider_message_id,created_at,updated_at) VALUES(?,'whatsapp','100',"
                           "'wa-legado','s1',1,'wa-ev','AUTO','{}','',?,'ACCEPTED',1,?,'wamid.LEGADO',?,?)",
                           ("b" * 32, now, now, now, now))
        connection.execute("INSERT INTO channel_send_ledger(outbox_id,authorization_id,run_id,recipient_hash,attempted_at) "
                           "VALUES(?,'HML-1',?,?,?)", ("b" * 32, "c" * 32, "d" * 64, now))
        connection.execute("COMMIT")
    import os
    os.chmod(path, 0o600)
    SQLiteConversationRepository(path)  # aplica a migração 6 (reconstrução da outbox)
    with closing(sqlite3.connect(path)) as connection:
        from rocha_reis_agent.persistence.sqlite import SCHEMA_VERSION
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)  # 5 → 6 → …
        row = connection.execute("SELECT id,status,provider_message_id,origin,source_inbox_id FROM channel_outbox").fetchone()
        assert row == ("b" * 32, "ACCEPTED", "wamid.LEGADO", "agent", 1)
        assert connection.execute("SELECT outbox_id FROM channel_send_ledger").fetchone() == ("b" * 32,)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        connection.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError):  # FK do ledger continua apontando para a outbox nova
            connection.execute("DELETE FROM channel_outbox")
    assert {"panel_users", "panel_sessions", "panel_assignments", "panel_audit", "change_log", "channel_contacts"} <= tables


def test_change_log_triggers_record_every_write_path(tmp_path):
    s = stack(tmp_path)
    s.ingestor.ingest(envelope(text_message("wamid.c1", "Olá")))
    s.inbound.process_one("w")
    s.outbound.dispatch_one("w")
    with closing(sqlite3.connect(s.repository.path)) as connection:
        kinds = [r[0] for r in connection.execute("SELECT kind FROM change_log ORDER BY id")]
    assert kinds.count("conversation") >= 1 and kinds.count("outbox") >= 2  # criação + mudança de status
