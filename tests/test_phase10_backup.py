"""Fase 10 — backup criptografado, verificação, restauração isolada, promoção protegida,
demonstração isolada e iniciador local."""

from contextlib import closing
import json
import os
from pathlib import Path
import socket
import sqlite3
import struct
from uuid import uuid4

import pytest

from panel_support import Panel, add_user, conversation_id, create_panel_app, customer, panel_settings
from rocha_reis_agent.channels.fake_transport import FakeWhatsAppTransport
from rocha_reis_agent.channels.service import drain
from rocha_reis_agent.ops import backup as bk
from rocha_reis_agent.ops.backup import BackupError, KeyMaterial
from rocha_reis_agent.persistence.sqlite import SCHEMA_VERSION
from whatsapp_support import CLIENT_A, CLIENT_B, stack

SECRET_TEXT = "Texto fictício muito específico 9137 para busca"
PASSPHRASE = "frase-senha-ficticia-longa"
TABLES = ("conversations", "messages", "transitions", "channel_inbox", "channel_outbox", "panel_assignments",
          "panel_audit", "panel_users", "audit", "events")


@pytest.fixture
def populated(tmp_path):
    """Banco com estados variados: automático, aguardando, humano, envio aceito, falho e incerto."""
    app = create_panel_app(panel_settings(tmp_path))
    add_user(app, "admin.teste", "ADMIN")
    ana_id = add_user(app, "ana.teste")
    ana = Panel(app)
    ana.login("ana.teste")
    customer(tmp_path, f"wamid.{uuid4().hex}", f"Quanto custa o corte? {SECRET_TEXT}", CLIENT_B)
    customer(tmp_path, f"wamid.{uuid4().hex}", "Quero falar com uma pessoa", CLIENT_A)
    cid = conversation_id(ana, "waiting")
    ana.post(f"/conversations/{cid}/assume")
    for behavior in ("accept", "reject", "timeout"):
        ana.post(f"/conversations/{cid}/reply", {"text": f"Resposta {behavior}", "idempotency_key": uuid4().hex})
        s = stack(tmp_path, transport=FakeWhatsAppTransport(behavior))
        drain(s.inbound, s.outbound, "w")
    key_dir = tmp_path / "chaves"
    key = bk.generate_key(key_dir / "backup.key")
    return {"db": app.state.conversation_repository.path, "key": KeyMaterial("keyfile", key_file=key),
            "backups": tmp_path / "backups", "tmp": tmp_path, "ana_id": ana_id}


def dump(path: Path) -> dict[str, list[tuple]]:
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        return {table: connection.execute(f"SELECT * FROM {table} ORDER BY 1,2").fetchall() for table in TABLES}


# ------------------------------------------------------------ criação e verificação
def test_backup_roundtrip_preserves_every_operational_state(populated):
    backup = bk.create_backup(populated["db"], populated["backups"], populated["key"])
    assert backup.stat().st_mode & 0o777 == 0o600 and backup.parent.stat().st_mode & 0o777 == 0o700
    assert not list(populated["backups"].glob("*.partial"))
    manifest = bk.verify_backup(backup, populated["key"])
    assert manifest["format_version"] == 1 and manifest["schema_version"] == SCHEMA_VERSION
    target = populated["tmp"] / "isolado" / "restaurado.synthetic.sqlite3"
    bk.restore_backup(backup, target, populated["key"])
    assert target.stat().st_mode & 0o777 == 0o600
    original, restored = dump(populated["db"]), dump(target)
    assert original == restored
    with closing(sqlite3.connect(target)) as connection:
        statuses = {row[0] for row in connection.execute("SELECT status FROM channel_outbox")}
    assert {"ACCEPTED", "FAILED", "UNKNOWN"} <= statuses  # incerto continua incerto (nada reenviado)
    assert restored["panel_assignments"] and restored["panel_audit"] and restored["channel_inbox"]


def test_backup_is_encrypted_and_has_no_plaintext_content(populated):
    backup = bk.create_backup(populated["db"], populated["backups"], populated["key"])
    data = backup.read_bytes()
    assert SECRET_TEXT.encode() not in data and b"Quanto custa" not in data and CLIENT_A.encode() not in data
    assert b"SQLite format 3" not in data
    manifest = bk.read_manifest(backup)  # legível sem chave, mas sem conteúdo nem segredo
    assert set(manifest) >= {"format", "format_version", "created_at", "schema_version", "content_sha256",
                             "table_counts", "cipher", "kdf"}
    assert "key" not in json.dumps(manifest).lower().replace("keyfile", "")


def test_passphrase_backup_and_wrong_passphrase(populated):
    good = KeyMaterial("passphrase", passphrase=PASSPHRASE)
    backup = bk.create_backup(populated["db"], populated["backups"], good)
    assert bk.read_manifest(backup)["kdf"]["name"] == "scrypt"
    assert bk.verify_backup(backup, good)["schema_version"] == SCHEMA_VERSION
    with pytest.raises(BackupError, match="Chave incorreta"):
        bk.verify_backup(backup, KeyMaterial("passphrase", passphrase=PASSPHRASE + "x"))
    with pytest.raises(BackupError, match="mínimo 12"):
        bk.create_backup(populated["db"], populated["backups"], KeyMaterial("passphrase", passphrase="curta"))
    with pytest.raises(BackupError, match="frase-senha"):
        bk.verify_backup(backup, populated["key"])


def test_wrong_key_is_refused(populated, tmp_path):
    backup = bk.create_backup(populated["db"], populated["backups"], populated["key"])
    other = bk.generate_key(tmp_path / "outra" / "k.key")
    with pytest.raises(BackupError, match="Chave incorreta"):
        bk.verify_backup(backup, KeyMaterial("keyfile", key_file=other))
    target = tmp_path / "r" / "x.synthetic.sqlite3"
    with pytest.raises(BackupError):
        bk.restore_backup(backup, target, KeyMaterial("keyfile", key_file=other))
    assert not target.exists()


@pytest.mark.parametrize("where", ["ciphertext", "manifest", "truncate", "magic"])
def test_corrupted_or_tampered_backup_is_refused(populated, where):
    backup = bk.create_backup(populated["db"], populated["backups"], populated["key"])
    data = bytearray(backup.read_bytes())
    if where == "ciphertext":
        data[-40] ^= 0x01
    elif where == "manifest":
        index = data.index(b'"created_at":"') + 15
        data[index] = ord("1") if data[index] != ord("1") else ord("2")
    elif where == "truncate":
        data = data[: len(data) // 2]
    else:
        data[0:8] = b"NOTABKUP"
    backup.write_bytes(bytes(data))
    with pytest.raises(BackupError):
        bk.verify_backup(backup, populated["key"])


@pytest.mark.parametrize("field,value,message", [("format_version", 2, "formato"), ("schema_version", 99, "esquema")])
def test_incompatible_versions_are_refused_before_decrypting(populated, field, value, message):
    backup = bk.create_backup(populated["db"], populated["backups"], populated["key"])
    manifest, header, rest = bk._parse(backup)
    manifest[field] = value
    new_header = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    backup.write_bytes(bk.MAGIC + struct.pack(">I", len(new_header)) + new_header + rest)
    with pytest.raises(BackupError, match=message):
        bk.verify_backup(backup, populated["key"])


def test_missing_files_and_key_rules(populated, tmp_path):
    with pytest.raises(BackupError, match="não encontrado"):
        bk.verify_backup(tmp_path / "nada.rrbackup", populated["key"])
    with pytest.raises(BackupError, match="ausente"):
        bk.create_backup(tmp_path / "nada.synthetic.sqlite3", populated["backups"], populated["key"])
    with pytest.raises(BackupError, match="não encontrado"):
        bk.create_backup(populated["db"], populated["backups"], KeyMaterial("keyfile", key_file=tmp_path / "k.key"))
    # A chave não pode morar com os backups.
    inside = bk.generate_key(populated["backups"] / "chave.key")
    with pytest.raises(BackupError, match="mesmo diretório"):
        bk.create_backup(populated["db"], populated["backups"], KeyMaterial("keyfile", key_file=inside))
    # Chave legível por outros é recusada; chave existente nunca é sobrescrita.
    loose = bk.generate_key(tmp_path / "solta" / "k.key")
    os.chmod(loose, 0o644)
    with pytest.raises(BackupError, match="privado"):
        bk.load_key(loose)
    with pytest.raises(BackupError, match="já existe"):
        bk.generate_key(loose)


# ------------------------------------------------------------ restauração e falhas
def test_restore_never_overwrites_existing_target(populated):
    backup = bk.create_backup(populated["db"], populated["backups"], populated["key"])
    before = populated["db"].read_bytes()
    with pytest.raises(BackupError, match="nunca sobrescreve"):
        bk.restore_backup(backup, populated["db"], populated["key"])
    assert populated["db"].read_bytes() == before
    with pytest.raises(BackupError, match="synthetic"):
        bk.restore_backup(backup, populated["tmp"] / "x.sqlite3", populated["key"])


def test_failure_during_restore_leaves_no_partial_or_final_file(populated):
    backup = bk.create_backup(populated["db"], populated["backups"], populated["key"])
    target = populated["tmp"] / "falha" / "r.synthetic.sqlite3"

    def boom(stage):
        if stage == "during_write":
            raise OSError("disco cheio simulado")

    with pytest.raises(OSError):
        bk.restore_backup(backup, target, populated["key"], fault=boom)
    assert not target.exists() and not list(target.parent.glob("*.partial"))
    bk.restore_backup(backup, target, populated["key"])  # nova tentativa limpa funciona
    assert dump(target) == dump(populated["db"])


def test_failure_during_backup_leaves_no_file(populated):
    def boom(stage):
        if stage == "during_write":
            raise OSError("falha simulada")

    with pytest.raises(OSError):
        bk.create_backup(populated["db"], populated["backups"], populated["key"], fault=boom)
    assert list(populated["backups"].iterdir()) == []


def test_backup_while_app_is_writing_is_consistent(populated):
    """Snapshot pela API online: transação aberta em outra conexão não entra pela metade."""
    with closing(sqlite3.connect(populated["db"], isolation_level=None)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE conversations SET updated_at='x-nao-confirmado'")
        backup = bk.create_backup(populated["db"], populated["backups"], populated["key"])
        writer.execute("ROLLBACK")
    target = populated["tmp"] / "c" / "r.synthetic.sqlite3"
    bk.restore_backup(backup, target, populated["key"])
    with closing(sqlite3.connect(target)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM conversations WHERE updated_at='x-nao-confirmado'").fetchone() == (0,)


# ------------------------------------------------------------ promoção
def test_promote_requires_confirmation_and_idle_database_and_keeps_safety_copy(populated):
    backup = bk.create_backup(populated["db"], populated["backups"], populated["key"])
    restored = populated["tmp"] / "iso" / "r.synthetic.sqlite3"
    bk.restore_backup(backup, restored, populated["key"])
    active = populated["db"]
    # Banco ativo muda depois do backup (nova conversa).
    customer(populated["tmp"], f"wamid.{uuid4().hex}", "Mensagem posterior ao backup", "5511900000077")
    changed = dump(active)
    with pytest.raises(BackupError, match="Confirmação incorreta"):
        bk.promote(restored, active, confirmation="SUBSTITUIR")
    with closing(sqlite3.connect(active, isolation_level=None)) as busy:
        busy.execute("BEGIN IMMEDIATE")
        with pytest.raises(BackupError, match="em uso"):
            bk.promote(restored, active, confirmation=f"SUBSTITUIR:{active.name}")
        busy.execute("ROLLBACK")
    assert dump(active) == changed  # nada mudou nas tentativas recusadas
    safety = bk.promote(restored, active, confirmation=f"SUBSTITUIR:{active.name}")
    assert dump(active) == dump(restored) and dump(safety) == changed
    assert safety.stat().st_mode & 0o777 == 0o600 and safety.name.endswith(".synthetic.sqlite3")
    # O app volta a abrir o banco promovido normalmente.
    from rocha_reis_agent.persistence.sqlite import SQLiteConversationRepository
    assert SQLiteConversationRepository(active).count() >= 2


def test_list_backups_reports_good_and_bad_files(populated):
    good = bk.create_backup(populated["db"], populated["backups"], populated["key"])
    (populated["backups"] / "lixo.rrbackup").write_bytes(b"nao e backup")
    listed = {item["file"]: item for item in bk.list_backups(populated["backups"])}
    assert listed[good.name]["ok"] is True and listed["lixo.rrbackup"]["ok"] is False


def test_cli_backup_verify_restore_with_passphrase_from_env(populated, monkeypatch, capsys):
    from rocha_reis_agent.ops.__main__ import main
    monkeypatch.setenv("ROCHA_REIS_BACKUP_PASSPHRASE", PASSPHRASE)
    assert main(["backup", "--banco", str(populated["db"]), "--destino", str(populated["backups"]),
                 "--frase-senha"]) == 0
    path = json.loads(capsys.readouterr().out)["backup"]
    assert main(["verificar", path, "--frase-senha"]) == 0
    target = populated["tmp"] / "cli" / "r.synthetic.sqlite3"
    assert main(["restaurar", path, "--para", str(target), "--frase-senha"]) == 0
    output = capsys.readouterr()
    assert PASSPHRASE not in output.out + output.err
    monkeypatch.setenv("ROCHA_REIS_BACKUP_PASSPHRASE", "errada-mas-longa-o-bastante")
    assert main(["verificar", path, "--frase-senha"]) == 2
    assert "Chave incorreta" in capsys.readouterr().err


# ------------------------------------------------------------ demonstração e iniciador
def test_demo_profile_is_isolated_reproducible_and_protected(tmp_path):
    from rocha_reis_agent.ops.demo import build_demo
    profile = tmp_path / "demo"
    result = build_demo(profile)
    assert Path(result["banco"]).is_relative_to(profile)
    for path in (profile, profile / "dados", profile / "backups", profile / "chaves"):
        assert path.stat().st_mode & 0o777 == 0o700
    for path in (profile / "perfil.json", profile / "credenciais-demo.txt", Path(result["banco"])):
        assert path.stat().st_mode & 0o777 == 0o600
    assert result["contagens"]["panel_users"] == 3 and result["contagens"]["conversations"] == 12
    passwords = [line.split("\t")[2] for line in (profile / "credenciais-demo.txt").read_text().splitlines()[1:]]
    assert all(p not in json.dumps(result) for p in passwords)
    with closing(sqlite3.connect(result["banco"])) as connection:
        statuses = {r[0] for r in connection.execute("SELECT status FROM channel_outbox")}
        blocked = connection.execute("SELECT count(*) FROM channel_inbox WHERE outcome='retired_contact_blocked'"
                                     ).fetchone()[0]
    assert {"ACCEPTED", "FAILED", "UNKNOWN"} <= statuses and blocked == 1
    assert dump(Path(result["banco"])) == dump(Path(result["restauracao_isolada"]))
    with pytest.raises(BackupError, match="já existe"):
        build_demo(profile)
    stranger = tmp_path / "nao-demo"
    stranger.mkdir()
    (stranger / "importante.txt").write_text("não apagar")
    with pytest.raises(BackupError, match="não é um perfil"):
        build_demo(stranger, reset=True)
    assert (stranger / "importante.txt").exists()
    again = build_demo(profile, reset=True)
    assert again["contagens"]["conversations"] == 12


def test_launcher_preflight_checks(tmp_path):
    from rocha_reis_agent.ops.demo import build_demo
    from rocha_reis_agent.ops.launcher import LaunchError, preflight
    with pytest.raises(LaunchError, match="Perfil não encontrado"):
        preflight(tmp_path / "nada", 8000)
    profile = tmp_path / "demo"
    build_demo(profile)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    assert any("travas reais desligadas" in item for item in preflight(profile, free))
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        with pytest.raises(LaunchError, match="ocupada"):
            preflight(profile, busy.getsockname()[1])
    with pytest.raises(LaunchError, match="porta"):
        preflight(profile, 80)
    os.chmod(profile / "perfil.json", 0o644)
    with pytest.raises(LaunchError, match="Permissões"):
        preflight(profile, free)
