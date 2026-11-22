"""Fase 18B-0 — adaptador S3 do backup externo contra um servidor S3 FALSO em 127.0.0.1.

Nenhum teste fala com Backblaze, Cloudflare ou qualquer serviço real: a rede fica restrita a 127.0.0.1
(fixture `no_network`) e o adaptador recusa endpoints externos com a trava fechada, ANTES de conectar.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest

from fake_s3 import FakeS3
from rocha_reis_agent import config
from rocha_reis_agent.ops import offsite, s3_store
from rocha_reis_agent.ops.backup import KeyMaterial, create_backup, generate_key, restore_backup
from rocha_reis_agent.ops.offsite import ExternalError
from rocha_reis_agent.ops.s3_store import Credentials, S3Config, S3ExternalStore

ROOT = Path(__file__).resolve().parents[1]
KEY_ID, SECRET = "AKTESTEROCHAREIS01", "segredo-de-teste-0123456789abcdef"
_REAL = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection, socket.getaddrinfo)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):  # type: ignore[no-untyped-def]
    def check(addr: object) -> None:
        host = addr if isinstance(addr, str) else addr[0]  # type: ignore[index]
        if str(host) not in ("127.0.0.1", "localhost"):
            pytest.fail(f"Rede proibida: tentativa de conexão a {host!r}")

    monkeypatch.setattr(socket.socket, "connect", lambda self, addr: (check(addr), _REAL[0](self, addr))[1])
    monkeypatch.setattr(socket.socket, "connect_ex", lambda self, addr: (check(addr), _REAL[1](self, addr))[1])
    monkeypatch.setattr(socket, "create_connection", lambda addr, *a, **k: (check(addr), _REAL[2](addr, *a, **k))[1])
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k: (check(host), _REAL[3](host, *a, **k))[1])


@pytest.fixture()
def s3():  # type: ignore[no-untyped-def]
    with FakeS3() as server:
        yield server


def store(server: FakeS3, *, secret: str = SECRET, key_id: str = KEY_ID, sleep=lambda s: None, **cfg):  # type: ignore[no-untyped-def]
    settings = {"endpoint": server.endpoint, "region": server.region, "bucket": server.bucket,
                "prefix": "rocha-reis/backups/", "provider": server.style, "timeout_seconds": 2.0, **cfg}
    return S3ExternalStore(S3Config(**settings), Credentials(key_id, secret), sleep=sleep)


def backup_file(tmp_path: Path, n: int = 1) -> tuple[list[Path], KeyMaterial, Path]:
    """Backups REAIS (AES-256-GCM) de um banco sintético do projeto."""
    from rocha_reis_agent.persistence.sqlite import SQLiteConversationRepository
    database = tmp_path / "dados" / "teste.synthetic.sqlite3"
    database.parent.mkdir(mode=0o700)
    SQLiteConversationRepository(database)
    (tmp_path / "chaves").mkdir(mode=0o700)
    key = generate_key(tmp_path / "chaves" / "backup.key")
    material = KeyMaterial("keyfile", key_file=key)
    directory = tmp_path / "backups"
    directory.mkdir(mode=0o700)
    files = []
    for _ in range(n):
        files.append(create_backup(database, directory, material))
        time.sleep(1.05)  # nomes com carimbo de tempo diferentes
    return files, material, directory


# ------------------------------------------------------------------ assinatura e trava
def test_sigv4_matches_the_aws_documented_example():
    """Exemplo oficial "GET Object" da documentação do Amazon S3 (Signature Version 4, cabeçalho Authorization)."""
    creds = Credentials("AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
    empty = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    value = s3_store.sign_v4("GET", "/test.txt", {}, {"host": "examplebucket.s3.amazonaws.com", "range": "bytes=0-9",
                                                      "x-amz-content-sha256": empty, "x-amz-date": "20130524T000000Z"},
                             empty, creds, "us-east-1", datetime(2013, 5, 24, tzinfo=timezone.utc))
    assert value.endswith("Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"), value
    assert "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date" in value
    assert repr(creds) == "Credentials(***)" and "wJalr" not in repr(creds)


@pytest.mark.parametrize("endpoint", ["https://s3.us-west-004.backblazeb2.com", "https://abc.r2.cloudflarestorage.com",
                                      "https://10.0.0.5", "http://192.168.0.10:9000"])
def test_lock_refuses_every_real_endpoint_before_connecting(endpoint, tmp_path):
    assert config.EXTERNAL_BACKUP_ACTIVATION_AUTHORIZED is False
    with pytest.raises(ExternalError, match="trava"):
        S3ExternalStore(S3Config(endpoint, "us-west-004", "rr-backups"), Credentials(KEY_ID, SECRET))
    # Pelo perfil: a trava vem ANTES de ler a credencial (que nem existe aqui).
    with pytest.raises(ExternalError, match="trava"):
        s3_store.from_profile(tmp_path, {"endpoint": endpoint, "regiao": "us-west-004", "bucket": "rr-backups"})


def test_config_rules(tmp_path):
    base = dict(endpoint="http://127.0.0.1:9", region="auto", bucket="rr-backups")
    for bad, message in [({"lock_mode": "COMPLIANCE"}, "juntos"), ({"provider": "r2", "lock_mode": "COMPLIANCE",
                                                                    "retention_days": 30}, "R2"),
                         ({"prefix": "../x/"}, "Prefixo"), ({"bucket": "A_B"}, "Bucket"),
                         ({"endpoint": "http://127.0.0.1:9/caminho"}, "esquema"), ({"attempts": 9}, "Tentativas"),
                         ({"retention_days": 5000, "lock_mode": "COMPLIANCE"}, "3000")]:
        with pytest.raises(ExternalError, match=message):
            s3_store.validate_config(S3Config(**{**base, **bad}))  # type: ignore[arg-type]
    with pytest.raises(ExternalError, match="HTTPS"):
        s3_store.validate_config(S3Config(**{**base, "endpoint": "http://s3.exemplo.invalid"}), authorized=True)


def test_credentials_file_rules(tmp_path):
    path = tmp_path / "cred.json"
    with pytest.raises(ExternalError, match="ausente"):
        s3_store.load_credentials(path)
    path.write_text(json.dumps({"access_key_id": KEY_ID, "secret_access_key": SECRET}))
    os.chmod(path, 0o644)
    with pytest.raises(ExternalError, match="0600"):
        s3_store.load_credentials(path)
    os.chmod(path, 0o600)
    assert s3_store.load_credentials(path).access_key_id == KEY_ID


# ------------------------------------------------------------------ operações
def test_upload_download_integrity_and_metadata(s3, tmp_path):
    (backup,), material, _ = backup_file(tmp_path)
    client = store(s3)
    client.put(backup.name, backup.read_bytes())
    head = client.head(backup.name)
    import hashlib
    assert head["sha256"] == hashlib.sha256(backup.read_bytes()).hexdigest() and head["tamanho"] == backup.stat().st_size
    assert client.get(backup.name) == backup.read_bytes()
    assert client.list() == [backup.name]
    meta = s3.current("rocha-reis/backups/" + backup.name)["meta"]
    assert meta == {"x-amz-meta-rr-sha256": head["sha256"], "x-amz-meta-rr-formato": "rocha-reis-backup"}
    stats = client.stats.as_dict()
    assert stats["bytes_enviados"] == backup.stat().st_size and stats["por_operacao"]["put"] == 1


def test_only_encrypted_project_backups_leave_the_server(s3, tmp_path):
    with pytest.raises(ExternalError, match="cifrados"):
        store(s3).put("banco.rrbackup", b"SQLite format 3\x00 em claro")
    with pytest.raises(ExternalError, match="inválido"):
        store(s3).put("../fora.rrbackup", b"RRBACKUPxxxx")
    assert s3.objects == {}


def test_corrupted_download_is_detected(s3, tmp_path):
    (backup,), _, _ = backup_file(tmp_path)
    client = store(s3)
    client.put(backup.name, backup.read_bytes())
    s3.faults.append(("get", "corrupt"))
    with pytest.raises(ExternalError, match="corrompido"):
        client.get(backup.name)


def test_interrupted_upload_is_retried_and_never_leaves_a_partial_object(s3, tmp_path):
    (backup,), _, _ = backup_file(tmp_path)
    client = store(s3)
    s3.faults.append(("put", "drop"))
    client.put(backup.name, backup.read_bytes())
    versions = s3.objects["rocha-reis/backups/" + backup.name]
    assert len(versions) == 1 and versions[0]["data"] == backup.read_bytes()
    assert client.stats.novas_tentativas == 1


def test_timeout_and_unavailable_destination_fail_after_limited_attempts(s3, tmp_path):
    (backup,), _, _ = backup_file(tmp_path)
    waits: list[float] = []
    client = store(s3, attempts=2, timeout_seconds=1.0, sleep=waits.append)
    s3.faults += [("head", "sleep:2"), ("head", "sleep:2")]
    started = time.monotonic()
    with pytest.raises(ExternalError, match="tempo esgotado"):
        client.put(backup.name, backup.read_bytes())
    assert time.monotonic() - started < 6 and waits == [0.5] and client.stats.requisicoes == 2
    s3.stop()
    with pytest.raises(ExternalError, match="indisponível"):
        store(s3, attempts=3).list()


def test_transient_5xx_is_retried_but_auth_and_permission_errors_are_not(s3, tmp_path):
    (backup,), _, _ = backup_file(tmp_path)
    s3.faults.append(("list", "500"))
    s3.faults.append(("get", "500"))  # ListObjects é um GET no bucket
    client = store(s3)
    assert client.list() == [] and client.stats.novas_tentativas == 1
    wrong = store(s3, secret="outro-segredo-0123456789abcdef")
    with pytest.raises(ExternalError, match="Credencial recusada"):
        wrong.list()
    assert wrong.stats.requisicoes == 1  # sem novas tentativas
    with pytest.raises(ExternalError, match="Credencial recusada"):
        store(s3, key_id="AKDESCONHECIDA0001").list()
    s3.keys["AKSOLEITURA000001"] = (SECRET, {"read", "list"})
    reader = store(s3, key_id="AKSOLEITURA000001")
    with pytest.raises(ExternalError, match="Permissão insuficiente"):
        reader.put(backup.name, backup.read_bytes())
    assert reader.stats.requisicoes == 2  # HEAD (404) + PUT recusado, sem repetir


def test_duplicate_is_idempotent_and_overwrite_is_refused(s3, tmp_path):
    (backup,), _, _ = backup_file(tmp_path)
    client = store(s3)
    data = backup.read_bytes()
    client.put(backup.name, data)
    client.put(backup.name, data)  # repetição: nada é reenviado
    assert client.stats.por_operacao["put"] == 1
    tampered = data[:-1] + bytes([data[-1] ^ 1])
    with pytest.raises(ExternalError, match="nada foi sobrescrito"):
        client.put(backup.name, tampered)
    assert [v["data"] for v in s3.objects["rocha-reis/backups/" + backup.name]] == [data]


def test_overwrite_race_is_blocked_by_if_none_match(s3, tmp_path, monkeypatch):
    """Outro envio grava entre o HEAD e o PUT: o servidor responde 412 e o adaptador não sobrescreve."""
    (backup,), _, _ = backup_file(tmp_path)
    data = backup.read_bytes()
    client = store(s3)
    original = client.head
    calls = {"n": 0}

    def racing_head(name):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            store(s3).put(name, data[:-1] + bytes([data[-1] ^ 1]))  # o "outro" grava conteúdo diferente
            return None
        return original(name)

    monkeypatch.setattr(client, "head", racing_head)
    with pytest.raises(ExternalError, match="nada foi sobrescrito"):
        client.put(backup.name, data)
    assert len(s3.objects["rocha-reis/backups/" + backup.name]) == 1


def test_object_lock_retention_and_forbidden_delete_b2_style(s3, tmp_path):
    (backup,), _, _ = backup_file(tmp_path)
    s3.keys[KEY_ID] = (SECRET, {"read", "write", "list", "delete"})
    client = store(s3, lock_mode="COMPLIANCE", retention_days=30, allow_client_delete=True)
    client.put(backup.name, backup.read_bytes())
    head = client.head(backup.name)
    until = datetime.strptime(head["retencao_ate"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert head["modo_bloqueio"] == "COMPLIANCE" and timedelta(days=29) < until - datetime.now(timezone.utc) <= timedelta(days=30)
    with pytest.raises(ExternalError, match="Permissão insuficiente"):  # COMPLIANCE: recusado mesmo com 'delete'
        client.delete(backup.name)
    assert s3.current("rocha-reis/backups/" + backup.name) is not None
    # Exclusão pelo cliente desligada (padrão): recusada SEM nem chegar ao provedor.
    default = store(s3)
    with pytest.raises(ExternalError, match="desativada"):
        default.delete(backup.name)
    assert default.stats.requisicoes == 0


def test_r2_style_bucket_lock_blocks_delete_and_overwrite(tmp_path):
    with FakeS3(style="r2", region="auto", bucket_lock_days=30,
                keys={KEY_ID: (SECRET, {"read", "write", "list", "delete"})}) as server:
        (backup,), _, _ = backup_file(tmp_path)
        client = store(server, allow_client_delete=True)
        client.put(backup.name, backup.read_bytes())
        with pytest.raises(ExternalError, match="retenção"):
            client.delete(backup.name)
        # Na R2 não há Object Lock por objeto: o adaptador recusa a configuração antes de enviar.
        with pytest.raises(ExternalError, match="R2"):
            store(server, lock_mode="COMPLIANCE", retention_days=30)


def test_listing_paginates_and_ignores_foreign_objects(s3, tmp_path):
    files, _, _ = backup_file(tmp_path, n=3)
    client = store(s3)
    for f in files:
        client.put(f.name, f.read_bytes())
    s3.objects["rocha-reis/backups/leia-me.txt"] = [{"data": b"x", "meta": {}, "at": datetime.now(timezone.utc)}]
    s3.objects["outro/" + files[0].name] = [{"data": b"x", "meta": {}, "at": datetime.now(timezone.utc)}]
    s3.page_size = 1
    assert client.list() == sorted(f.name for f in files)
    assert client.stats.por_operacao["list"] == 4


# ------------------------------------------------------------------ sincronização, restauração, reinício
def test_sync_restore_and_provider_side_retention(s3, tmp_path):
    files, material, directory = backup_file(tmp_path, n=2)
    state = tmp_path / "estado-copia-externa.json"
    client = store(s3)
    result = offsite.sync(directory, client, state, keep=1)
    assert result["enviados"] == [files[-1].name] and result["falhas"] == [] and result["removidos_por_retencao"] == []
    saved = json.loads(state.read_text())
    assert saved["simulado"] is False and saved["retencao_remota"].startswith("pelo provedor")
    assert saved["ultima_transferencia"]["bytes_enviados"] == files[-1].stat().st_size
    assert SECRET not in state.read_text() and KEY_ID not in state.read_text()
    # restauração a partir do destino externo, numa pasta NOVA
    restored_dir = tmp_path / "restaurar"
    fetched = offsite.fetch(client, files[-1].name, restored_dir)
    manifest = restore_backup(fetched, restored_dir / "novo.synthetic.sqlite3", material)
    assert manifest["schema_version"] and (restored_dir / "novo.synthetic.sqlite3").exists()
    # segunda rodada: nada a enviar (idempotente)
    assert offsite.sync(directory, store(s3), state, keep=1)["enviados"] == []


def test_sync_records_failure_when_destination_or_credential_is_missing(s3, tmp_path):
    files, _, directory = backup_file(tmp_path)
    state = tmp_path / "estado.json"
    s3.stop()
    result = offsite.sync(directory, store(s3, attempts=1), state)
    assert result["enviados"] == [] and result["falhas"] and json.loads(state.read_text())["falhas_seguidas"] == 1
    from rocha_reis_agent.ops import alerts
    evaluated = alerts.evaluate(tmp_path / "nao-existe.sqlite3", offsite_state=state)
    assert "banco_inacessivel" in {a["codigo"] for a in evaluated["alertas"]}  # sem banco aqui: só o estado conta
    profile = tmp_path / "perfil"
    profile.mkdir()
    with pytest.raises(ExternalError, match="ausente"):
        s3_store.from_profile(profile, {"endpoint": "http://127.0.0.1:9", "regiao": "auto", "bucket": "rr-backups"})


def test_process_killed_during_transfer_leaves_nothing_partial_and_resumes(s3, tmp_path):
    files, _, directory = backup_file(tmp_path)
    state = tmp_path / "estado.json"
    script = (
        "import sys; sys.path[:0] = [sys.argv[1], sys.argv[2]];\n"
        "from pathlib import Path; from rocha_reis_agent.ops import offsite;\n"
        "from rocha_reis_agent.ops.s3_store import S3Config, S3ExternalStore, Credentials;\n"
        "c = S3Config(sys.argv[3], 'us-west-004', 'rr-backups', prefix='rocha-reis/backups/', provider='b2', timeout_seconds=60, attempts=1)\n"
        "offsite.sync(Path(sys.argv[4]), S3ExternalStore(c, Credentials(sys.argv[5], sys.argv[6])), Path(sys.argv[7]))\n")
    s3.faults.append(("put", "hang"))  # o servidor recebe o corpo e "morre" antes de gravar
    child = subprocess.Popen([sys.executable, "-c", script, str(ROOT), str(ROOT / "tests"), s3.endpoint, str(directory),
                              KEY_ID, SECRET, str(state)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 20
    while not any(m == "HEAD" for m, _, _ in s3.log) and time.monotonic() < deadline:  # HEAD respondido (404) ...
        time.sleep(0.05)
    time.sleep(1.0)  # ... e o PUT seguinte já está preso no servidor quando o processo é morto
    child.kill()
    child.wait(timeout=10)
    assert s3.objects == {}, "objeto parcial gravado"
    assert not state.exists() or json.loads(state.read_text()) is not None  # estado nunca fica corrompido
    result = offsite.sync(directory, store(s3), state)
    assert result["enviados"] == [files[0].name]
    assert len(s3.objects[f"rocha-reis/backups/{files[0].name}"]) == 1


def test_cli_s3_destination_is_blocked_for_real_endpoints(tmp_path, capsys):
    from rocha_reis_agent.ops.__main__ import main
    from rocha_reis_agent.ops.demo import build_demo
    build_demo(tmp_path / "demo")
    profile = tmp_path / "demo"
    data = json.loads((profile / "perfil.json").read_text())
    data["backup"] = {"externo": {"provedor": "b2", "endpoint": "https://s3.us-west-004.backblazeb2.com",
                                  "regiao": "us-west-004", "bucket": "rr-backups", "modo_bloqueio": "COMPLIANCE",
                                  "retencao_dias": 30}}
    (profile / "perfil.json").write_text(json.dumps(data))
    assert main(["backup-externo", "--perfil", str(profile), "--s3"]) == 2
    assert "trava" in capsys.readouterr().err
