"""Regressões de segurança: armazenamento, credenciais, rede e superfície HTTP."""

import os
from pathlib import Path
import re

import pytest
from fastapi.testclient import TestClient

from rocha_reis_agent.config import load_settings
from rocha_reis_agent.domain.contracts import StorageError
from rocha_reis_agent.main import create_app
from rocha_reis_agent.persistence.sqlite import SQLiteConversationRepository
from whatsapp_support import settings

ROOT = Path(__file__).resolve().parents[1]


def test_volume_without_posix_permissions_is_rejected_before_creating_database(tmp_path, monkeypatch):
    """Simula vfat (fmask=0022): todo arquivo aparece 0644 e chmod não tem efeito."""
    real = os.fstat

    class Insecure:
        def __init__(self, value):
            self._value = value

        def __getattr__(self, name):
            return getattr(self._value, name)

        @property
        def st_mode(self):
            return (self._value.st_mode & ~0o777) | 0o644

    monkeypatch.setattr(os, "fstat", lambda fd: Insecure(real(fd)))
    target = tmp_path / "volume" / "vfat.synthetic.sqlite3"
    with pytest.raises(StorageError, match="0600"):
        SQLiteConversationRepository(target)
    assert sorted(p.name for p in target.parent.iterdir()) == []  # nem banco nem lock residual


def test_channel_tables_are_migrated_with_constraints(tmp_path):
    repository = SQLiteConversationRepository(tmp_path / "m.synthetic.sqlite3")
    with repository.write_transaction() as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"channel_inbox", "channel_outbox"} <= tables
        with pytest.raises(Exception):
            connection.execute(
                "INSERT INTO channel_inbox(channel,business_number_id,dedup_key,kind,digest,payload,status,"
                "attempts,next_attempt_at,source_timestamp,received_at,updated_at) "
                "VALUES('sms','1','msg:xxxxxxxx','message',?,'{}','PENDING',0,'','','','')", ("0" * 64,))


def test_no_real_credentials_or_tokens_in_repository():
    patterns = [re.compile(p) for p in (
        r"EAA[A-Za-z0-9]{30,}",                       # tokens de acesso Meta
        r"(?m)^\s*WHATSAPP_(APP_SECRET|VERIFY_TOKEN|CONVERSATION_KEY|ACCESS_TOKEN)=\S+",  # atribuição estilo .env
        r"sk-[A-Za-z0-9]{20,}", r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    )]
    offenders = []
    for path in ROOT.rglob("*"):
        if any(part in {".git", ".venv", "__pycache__", ".pytest_cache"} for part in path.parts) or not path.is_file():
            continue
        if path.suffix not in {".py", ".md", ".json", ".txt", ".toml", ".in", ".example", ".sql"} and path.name != ".env.example":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        offenders += [str(path.relative_to(ROOT)) for p in patterns if p.search(text)]
    assert offenders == []
    assert not (ROOT / ".env").exists()


def test_env_example_keeps_channel_disabled_and_has_no_values(tmp_path, monkeypatch):
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"(?m)^WHATSAPP_ENABLED=false$", example)
    for name in ("WHATSAPP_APP_SECRET", "WHATSAPP_VERIFY_TOKEN", "WHATSAPP_CONVERSATION_KEY"):
        assert re.search(rf"(?m)^{name}=", example) is None  # somente comentado
    (tmp_path / ".env").write_text(example, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert load_settings().whatsapp_enabled is False


def test_network_client_is_confined_to_the_disabled_graph_adapter():
    """Fase 5: só channels/graph_transport.py usa cliente HTTP, e ele não é carregado
    pela aplicação, pela CLI ou pelo worker enquanto a trava de ativação for falsa."""
    allowed = ROOT / "rocha_reis_agent" / "channels" / "graph_transport.py"
    # Fase 18B-0: o adaptador S3 do backup externo é o SEGUNDO (e único outro) cliente de rede: só
    # `http.client` da biblioteca padrão, atrás da trava EXTERNAL_BACKUP_ACTIVATION_AUTHORIZED, e nunca
    # carregado pela aplicação, pela CLI ou pelo worker (conferido no fim deste teste).
    s3_adapter = ROOT / "rocha_reis_agent" / "ops" / "s3_store.py"
    # Fase 8: a avaliação offline importa httpx2 SOMENTE para o transporte em memória
    # (MockTransport). Nenhum cliente com transporte real; a execução bloqueia sockets
    # (tests/test_evaluation.py::offline).
    in_memory = ROOT / "rocha_reis_agent" / "evaluation" / "providers.py"
    source = in_memory.read_text(encoding="utf-8")
    used = set(re.findall(r"httpx2\.([A-Za-z_]+)", source))
    # Fase 9B: o cliente httpx2 da avaliação existe SÓ com transporte em memória.
    assert used <= {"MockTransport", "Request", "Response", "Client"}, used
    assert "base_url" not in source and "httpx2.Client(transport=httpx2.MockTransport(" in source
    assert source.count("httpx2.Client(") == source.count("httpx2.Client(transport=httpx2.MockTransport(")
    # Fase 9B: o adaptador Gemini importa httpx2 SÓ para os tipos de exceção do transporte do SDK.
    gemini = ROOT / "rocha_reis_agent" / "ai" / "gemini_provider.py"
    assert set(re.findall(r"httpx2\.([A-Za-z_]+)", gemini.read_text(encoding="utf-8"))) <= {"TimeoutException", "TransportError"}
    for path in (ROOT / "rocha_reis_agent").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for forbidden in ("import httpx2", "import httpx\n", "import requests", "urllib.request", "http.client",
                          "import aiohttp", "socket.create_connection"):
            if path != allowed and not (path in (in_memory, gemini) and forbidden == "import httpx2") \
                    and not (path == s3_adapter and forbidden == "http.client"):
                assert forbidden not in source, (path.name, forbidden)
        if path != allowed:
            assert "graph.facebook.com" not in source
        # O SDK Google (cliente de rede) só é importado pelo adaptador Gemini; Anthropic não existe mais.
        if path != gemini:
            assert "from google import genai" not in source and "google.genai" not in source, path.name
        assert "import anthropic" not in source, path.name
        if "import graph_transport" in source or "channels.graph_transport import" in source:
            assert path.name == "bootstrap.py"  # importação tardia, atrás da trava
    import subprocess, sys
    probe = ("import sys; import rocha_reis_agent.main, rocha_reis_agent.channels.local_cli, "
             "rocha_reis_agent.simulator, rocha_reis_agent.worker, rocha_reis_agent.ops.__main__; "
             "print('graph_transport' in ' '.join(sys.modules), 'httpx2' in sys.modules, "
             "'rocha_reis_agent.ops.s3_store' in sys.modules)")
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert result.stdout.split()[0] == "False"
    assert result.stdout.split()[2] == "False", result.stdout  # adaptador S3 só sob demanda (backup-externo --s3)


def test_enabled_channel_exposes_only_health_and_webhook_without_admin_routes(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/webhooks/whatsapp").status_code == 403
        for path in ("/docs", "/openapi.json", "/admin", "/operator/resume", "/outbox", "/inbox", "/webhooks"):
            assert client.get(path).status_code == 404
            assert client.post(path).status_code in (404, 405)


def test_channel_cli_runs_offline_end_to_end_and_refuses_disabled_channel(tmp_path, monkeypatch, capsys):
    from rocha_reis_agent.channels import local_cli
    from whatsapp_support import APP_SECRET, CONVERSATION_KEY, VERIFY
    assert local_cli.main(["status"]) == 2
    assert "WHATSAPP_ENABLED=true" in capsys.readouterr().err
    for name, value in {
        "STORAGE_BACKEND": "sqlite", "STORAGE_PATH": str(tmp_path / "cli.synthetic.sqlite3"),
        "WHATSAPP_ENABLED": "true", "WHATSAPP_VERIFY_TOKEN": VERIFY, "WHATSAPP_APP_SECRET": APP_SECRET,
        "WHATSAPP_CONVERSATION_KEY": CONVERSATION_KEY, "WHATSAPP_PHONE_NUMBER_ID": "100000000000001",
        "WHATSAPP_BUSINESS_ACCOUNT_ID": "200000000000002",
    }.items():
        monkeypatch.setenv(name, value)
    fixtures = ROOT / "fixtures" / "meta"
    assert local_cli.main(["post", str(fixtures / "text_booking.synthetic.json")]) == 0
    assert local_cli.main(["post", str(fixtures / "text_booking.synthetic.json")]) == 0
    assert local_cli.main(["work", "--fixture"]) == 0
    output = capsys.readouterr().out
    assert output.count("HTTP 200") == 2 and output.count("[ENVIO SIMULADO") == 1
    assert "example.invalid/booksy-sintetico" in output and VERIFY not in output and APP_SECRET not in output
    assert local_cli.main(["status"]) == 0
    assert '"ACCEPTED": 1' in capsys.readouterr().out
