"""Fase 12 — ambientes isolados, verificação de cifragem em repouso, migrações seguras,
recuperação, retenção de backups, arquivos auxiliares e métricas sem dados pessoais."""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
from uuid import uuid4

import pytest

from rocha_reis_agent import config as config_module
from rocha_reis_agent.config import Settings
from rocha_reis_agent.domain.contracts import StorageError
from rocha_reis_agent.ops import backup as bk
from rocha_reis_agent.ops.backup import BackupError, KeyMaterial
from rocha_reis_agent.persistence import sqlite as sqlite_module
from rocha_reis_agent.persistence import storage_security
from rocha_reis_agent.persistence.sqlite import SCHEMA_VERSION, SQLiteConversationRepository
from whatsapp_support import APP_SECRET, CONVERSATION_KEY, VERIFY, settings as channel_settings, stack

SECRET_TEXT = "Texto fictício reservado 5512 sobre coloração"


def encrypted(monkeypatch, value: bool = True) -> None:
    """Simula a constatação do kernel (os testes rodam em tmpfs, que nunca é aceito de verdade)."""
    result = storage_security.VolumeProtection(value, True, "dm-crypt LUKS2" if value else "sem dm-crypt",
                                               ("data-root", "cryptdata") if value else ())
    monkeypatch.setattr(storage_security, "volume_protection", lambda path, **_: result)
    from rocha_reis_agent.ops import environments
    monkeypatch.setattr(environments, "volume_protection", lambda path, **_: result)


def private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


# ------------------------------------------------------------ verificação da cifragem (dm-crypt)
def fake_sys(tmp_path: Path, chain: list[tuple[str, str]]) -> Path:
    """Árvore /sys mínima: dev/block/253:<n> → devices/virtual/block/dm-<n> com dm/uuid e slaves."""
    root = tmp_path / "sys"
    previous = None
    for index, (name, uuid) in reversed(list(enumerate(chain))):
        device = root / "devices" / "virtual" / "block" / f"dm-{index}"
        (device / "dm").mkdir(parents=True)
        (device / "dm" / "uuid").write_text(uuid)
        (device / "dm" / "name").write_text(name)
        (device / "slaves").mkdir()
        if previous is not None:
            (device / "slaves" / previous.name).symlink_to(previous)
        previous = device
    (root / "dev" / "block").mkdir(parents=True)
    (root / "dev" / "block" / "253:0").symlink_to(previous)
    return root


@pytest.mark.parametrize("chain,expected", [
    ([("data-root", "LVM-abc"), ("cryptdata", "CRYPT-LUKS2-xyz-cryptdata")], (True, "dm-crypt LUKS2")),
    ([("cryptswap", "CRYPT-PLAIN-cryptswap")], (True, "dm-crypt PLAIN")),
    ([("data-root", "LVM-abc"), ("pv", "LVM-def")], (False, "dispositivo sem dm-crypt na cadeia")),
])
def test_volume_protection_walks_device_mapper_chain(tmp_path, monkeypatch, chain, expected):
    root = fake_sys(tmp_path, chain)
    found = storage_security.volume_protection(tmp_path, sys_root=root, device_id=(253, 0))
    assert (found.encrypted, found.method) == expected and found.persistent


def test_tmpfs_and_unknown_devices_are_never_reported_as_encrypted(tmp_path, monkeypatch):
    assert storage_security.volume_protection(tmp_path, device_id=(0, 50)).persistent is False
    found = storage_security.volume_protection(tmp_path, sys_root=tmp_path / "vazio", device_id=(8, 17))
    assert found.encrypted is False and "não identificado" in found.method


def test_repository_requiring_encryption_refuses_unverified_volume(tmp_path, monkeypatch):
    encrypted(monkeypatch, False)
    with pytest.raises(StorageError, match="cifragem em repouso verificada"):
        SQLiteConversationRepository(tmp_path / "x.synthetic.sqlite3", require_encrypted_volume=True)
    assert not (tmp_path / "x.synthetic.sqlite3").exists()  # nada é criado antes da verificação
    encrypted(monkeypatch, True)
    SQLiteConversationRepository(tmp_path / "x.synthetic.sqlite3", require_encrypted_volume=True)


# ------------------------------------------------------------ ambientes (itens 21–28)
def preprod_values(tmp_path: Path, **changes):
    values = dict(app_env="local", storage_backend="sqlite",
                  storage_path=tmp_path / "rocha-reis.preprod.synthetic.sqlite3", knowledge_source="database",
                  whatsapp_enabled=True, whatsapp_verify_token=VERIFY, whatsapp_app_secret=APP_SECRET,
                  whatsapp_conversation_key=CONVERSATION_KEY, whatsapp_phone_number_id="100000000000009",
                  environment_profile="preproduction", panel_environment_label="PRÉ-PRODUÇÃO SINTÉTICA")
    values.update(changes)
    return values


def test_development_default_is_unmarked_and_unchanged(tmp_path):
    config = channel_settings(tmp_path)
    assert config.environment_profile is None and config.data_classification == "synthetic"
    assert config.requires_encrypted_volume is False


@pytest.mark.parametrize("changes,message", [
    ({"app_env": "test"}, "APP_ENV=local"),
    ({"storage_path": Path("/tmp/x.synthetic.sqlite3")}, "preprod"),
    ({"knowledge_source": "file"}, "conhecimento"),
    ({"panel_environment_label": None}, "rótulo"),
    ({"panel_enabled": True, "panel_embedded_worker": True, "panel_cookie_secure": False}, "worker embutido"),
])
def test_contradictory_preproduction_configurations_are_refused(tmp_path, changes, message):
    with pytest.raises(ValueError, match=message):
        Settings(_env_file=None, **preprod_values(tmp_path, **changes))


def test_preproduction_database_only_opens_with_preproduction_profile(tmp_path):
    for profile in (None, "development", "demo"):
        with pytest.raises(ValueError, match="pré-produção"):
            Settings(_env_file=None, **preprod_values(tmp_path, environment_profile=profile))


def test_demo_and_test_profile_rules(tmp_path):
    with pytest.raises(ValueError, match="demo"):
        channel_settings(tmp_path, environment_profile="demo")
    channel_settings(tmp_path, environment_profile="demo", storage_path=tmp_path / "demo.synthetic.sqlite3")
    with pytest.raises(ValueError, match="APP_ENV=test"):
        channel_settings(tmp_path, environment_profile="test")
    channel_settings(tmp_path, environment_profile="test", app_env="test")


def test_database_remembers_its_environment(tmp_path):
    path = tmp_path / "demo.synthetic.sqlite3"
    SQLiteConversationRepository(path, environment="demo")
    SQLiteConversationRepository(path, environment="demo")  # mesmo ambiente: ok, repetido
    for other in ("preproduction", "development", None):
        with pytest.raises(StorageError, match="demo"):
            SQLiteConversationRepository(path, environment=other)


def test_existing_unmarked_database_cannot_become_preproduction(tmp_path, monkeypatch):
    encrypted(monkeypatch)
    folder = private_dir(tmp_path / "dados")
    path = folder / "old.synthetic.sqlite3"
    SQLiteConversationRepository(path)  # banco antigo, sem marcação
    with pytest.raises(StorageError, match="criado como pré-produção"):
        SQLiteConversationRepository(path, environment="preproduction", require_encrypted_volume=True)
    SQLiteConversationRepository(path, environment="development")  # outros ambientes adotam a marcação


def test_preproduction_requires_private_directory(tmp_path, monkeypatch):
    encrypted(monkeypatch)
    folder = tmp_path / "aberto"
    folder.mkdir()
    os.chmod(folder, 0o755)
    with pytest.raises(StorageError, match="0700"):
        SQLiteConversationRepository(folder / "a.preprod.synthetic.sqlite3", environment="preproduction",
                                     require_encrypted_volume=True)


def test_preproduction_profile_is_synthetic_isolated_and_never_recreated(tmp_path, monkeypatch):
    from rocha_reis_agent.ops import environments
    encrypted(monkeypatch)
    profile = tmp_path / "preprod"
    result = environments.build_preproduction(profile)
    assert "Somente dados SINTÉTICOS" in result["aviso"]
    for path, mode in ((profile, 0o700), (profile / "dados", 0o700), (profile / "chaves", 0o700),
                       (profile / "perfil.json", 0o600), (profile / "credenciais-preprod.txt", 0o600),
                       (profile / "chaves" / "backup.key", 0o600)):
        assert path.stat().st_mode & 0o777 == mode, path
    report = environments.diagnose(profile)
    assert report["banco"]["ambiente_gravado"] == "preproduction"
    assert report["banco"]["classificacao"] == "synthetic" and report["banco"]["esquema"] == SCHEMA_VERSION
    assert report["dados_reais"]["situacao"] == "BLOQUEADOS"
    statuses = {r["requisito"]: r["situacao"] for r in report["dados_reais"]["requisitos"]}
    assert statuses["Trava de código REAL_DATA_ACTIVATION_AUTHORIZED aberta após revisão"] == "pendente"
    assert all(v is False for v in report["integracoes"]["travas"].values())
    password = (profile / "credenciais-preprod.txt").read_text().splitlines()[1].split("\t")[2]
    assert password not in json.dumps(result) and password not in json.dumps(report)
    with pytest.raises(BackupError, match="nunca é recriada"):
        environments.build_preproduction(profile)
    from rocha_reis_agent.knowledge_catalog import KnowledgeScope
    from rocha_reis_agent.persistence.sqlite_knowledge import SQLiteKnowledgeRepository
    from rocha_reis_agent.ops.demo import profile_settings
    from rocha_reis_agent.bootstrap import configured_repository
    repository = configured_repository(profile_settings(profile))
    knowledge = SQLiteKnowledgeRepository(repository)
    assert knowledge.scope() == KnowledgeScope.SYNTHETIC and knowledge.records() == []


def test_preproduction_refuses_unverified_volume(tmp_path, monkeypatch):
    from rocha_reis_agent.ops import environments
    encrypted(monkeypatch, False)
    with pytest.raises(BackupError, match="cifragem verificada"):
        environments.build_preproduction(tmp_path / "preprod")
    assert not (tmp_path / "preprod").exists()


def test_real_data_stays_blocked_everywhere(tmp_path):
    assert config_module.REAL_DATA_ACTIVATION_AUTHORIZED is False
    with pytest.raises(ValueError, match="Dados reais bloqueados"):
        channel_settings(tmp_path, data_classification="real")
    with pytest.raises(ValueError, match="Dados reais bloqueados"):
        Settings(_env_file=None, **preprod_values(tmp_path, data_classification="real"))
    path = tmp_path / "r.synthetic.sqlite3"
    SQLiteConversationRepository(path)
    with closing(sqlite3.connect(path)) as connection:  # banco "promovido" por fora do sistema
        connection.execute("UPDATE metadata SET value='real' WHERE key='data_classification'")
        connection.commit()
    with pytest.raises(StorageError, match="dados reais"):
        SQLiteConversationRepository(path)


def test_integrations_stay_locked():
    assert (config_module.GRAPH_TRANSPORT_ACTIVATION_AUTHORIZED, config_module.HOMOLOGATION_TRANSPORT_ACTIVATION_AUTHORIZED,
            config_module.GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED) == (False, False, False)


# ------------------------------------------------------------ migrações (itens 15–20)
def build_old_database(tmp_path: Path, monkeypatch) -> Path:
    """Banco na versão 9 com conversas, inbox/outbox, auditoria e conhecimento antigo."""
    monkeypatch.setattr(sqlite_module, "SCHEMA_VERSION", 9)
    s = stack(tmp_path)
    from rocha_reis_agent.channels.service import drain
    from whatsapp_support import envelope, text_message
    from rocha_reis_agent.persistence.sqlite_knowledge import SQLiteKnowledgeRepository
    from rocha_reis_agent.knowledge_catalog import KnowledgeScope
    from whatsapp_support import catalog
    knowledge = SQLiteKnowledgeRepository(s.repository)
    knowledge.initialize(KnowledgeScope.SYNTHETIC)
    knowledge.import_catalog(catalog(), author="fixture-sintetica")
    for index in range(3):
        s.ingestor.ingest(envelope(text_message(f"wamid.{uuid4().hex}", "Quero falar com uma pessoa",
                                                f"551190000040{index}")))
    drain(s.inbound, s.outbound, "w")
    monkeypatch.setattr(sqlite_module, "SCHEMA_VERSION", SCHEMA_VERSION)
    return s.repository.path


def dump(path: Path) -> dict[str, list]:
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        tables = [r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {t: connection.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables if t != "metadata"}


def test_old_database_upgrades_preserving_everything(tmp_path, monkeypatch):
    path = build_old_database(tmp_path, monkeypatch)
    before = dump(path)
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (9,)
        assert connection.execute("SELECT count(*) FROM metadata WHERE key='data_classification'").fetchone() == (0,)
    repository = SQLiteConversationRepository(path)
    after = dump(path)
    changed = {t for t in before if before[t] != after.get(t)} - {"audit"}  # abrir registra auditoria técnica
    assert changed == set() and len(after["knowledge_revisions"]) == len(before["knowledge_revisions"])
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        assert connection.execute("SELECT value FROM metadata WHERE key='data_classification'").fetchone() == ("synthetic",)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    from rocha_reis_agent.persistence.sqlite_knowledge import SQLiteKnowledgeRepository
    from rocha_reis_agent.knowledge_projection import authorize_knowledge
    active = authorize_knowledge(SQLiteKnowledgeRepository(repository).get())  # documentos antigos válidos
    assert {s.id for s in active.base.services} == {"corte", "escova"}
    assert next(s for s in active.base.services if s.id == "corte").price_kind == "fixed"


def test_new_database_starts_at_current_version(tmp_path):
    path = tmp_path / "novo.synthetic.sqlite3"
    SQLiteConversationRepository(path)
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        assert dict(connection.execute("SELECT key,value FROM metadata").fetchall())["data_classification"] == "synthetic"


def test_interrupted_migration_rolls_back_completely_and_can_be_retried(tmp_path, monkeypatch):
    path = build_old_database(tmp_path, monkeypatch)
    SQLiteConversationRepository(path)  # versão atual
    before = dump(path)
    broken = tmp_path / "migrations"
    shutil.copytree(sqlite_module.MIGRATIONS, broken)
    (broken / f"{SCHEMA_VERSION + 1:03d}_quebrada.sql").write_text(
        "CREATE TABLE fase12_parcial (id INTEGER PRIMARY KEY) STRICT;\n"
        "INSERT INTO metadata VALUES ('migracao_parcial', 'x');\n"
        "SELECT funcao_inexistente()")
    monkeypatch.setattr(sqlite_module, "MIGRATIONS", broken)
    monkeypatch.setattr(sqlite_module, "SCHEMA_VERSION", SCHEMA_VERSION + 1)
    with pytest.raises(StorageError):
        SQLiteConversationRepository(path)
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master")}
        assert "fase12_parcial" not in tables
        assert connection.execute("SELECT count(*) FROM metadata WHERE key='migracao_parcial'").fetchone() == (0,)
    assert {t: v for t, v in dump(path).items() if t != "audit"} == {t: v for t, v in before.items() if t != "audit"}
    (broken / f"{SCHEMA_VERSION + 1:03d}_quebrada.sql").write_text(
        "CREATE TABLE fase12_parcial (id INTEGER PRIMARY KEY) STRICT")  # corrigida: nova tentativa aplica
    SQLiteConversationRepository(path)
    SQLiteConversationRepository(path)  # repetida: nada a fazer
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION + 1,)


def test_migration_010_is_idempotent(tmp_path):
    path = tmp_path / "i.synthetic.sqlite3"
    SQLiteConversationRepository(path)
    sql = (sqlite_module.MIGRATIONS / "010_data_classification.sql").read_text(encoding="utf-8")
    with closing(sqlite3.connect(path)) as connection:
        for statement in sqlite_module._statements(sql):
            connection.execute(statement)
        connection.commit()
        assert connection.execute("SELECT count(*) FROM metadata WHERE key='data_classification'").fetchone() == (1,)


# ------------------------------------------------------------ recuperação (itens 36–44)
def test_backup_of_an_old_version_restores_and_upgrades(tmp_path, monkeypatch):
    path = build_old_database(tmp_path, monkeypatch)  # ainda na versão 9
    key = bk.generate_key(tmp_path / "chaves" / "k.key")
    material = KeyMaterial("keyfile", key_file=key)
    backup = bk.create_backup(path, tmp_path / "backups", material)
    assert bk.read_manifest(backup)["schema_version"] == 9
    target = private_dir(tmp_path / "restaurado") / "r.synthetic.sqlite3"
    bk.restore_backup(backup, target, material)
    SQLiteConversationRepository(target)  # migra a cópia restaurada, nunca o original
    with closing(sqlite3.connect(target)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        statuses = dict(connection.execute("SELECT status,count(*) FROM channel_outbox GROUP BY status").fetchall())
    assert statuses == {"ACCEPTED": 3}
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (9,)
    after = bk.create_backup(target, tmp_path / "backups", material)  # backup depois da migração
    assert bk.verify_backup(after, material)["schema_version"] == SCHEMA_VERSION


def test_backup_and_verify_leave_no_plaintext_temporary_files(tmp_path, monkeypatch):
    import tempfile
    path = build_old_database(tmp_path, monkeypatch)
    spool = private_dir(tmp_path / "tmpdir")
    monkeypatch.setenv("TMPDIR", str(spool))
    monkeypatch.setattr(tempfile, "tempdir", None)
    key = bk.generate_key(tmp_path / "chaves" / "k.key")
    material = KeyMaterial("keyfile", key_file=key)
    backup = bk.create_backup(path, tmp_path / "backups", material)
    bk.verify_backup(backup, material)
    assert list(spool.iterdir()) == []
    assert sorted(p.name for p in (tmp_path / "backups").iterdir()) == [backup.name]
    with closing(sqlite3.connect(path)) as raw:
        assert raw.execute("PRAGMA journal_mode").fetchone() == ("wal",)  # WAL/SHM ficam no diretório do banco
    repository = SQLiteConversationRepository(path)
    with repository.read() as connection:
        assert connection.execute("PRAGMA temp_store").fetchone()[0] == 2  # MEMORY


def test_retention_plan_and_confirmation(tmp_path, monkeypatch):
    path = build_old_database(tmp_path, monkeypatch)
    key = bk.generate_key(tmp_path / "chaves" / "k.key")
    material = KeyMaterial("keyfile", key_file=key)
    folder = tmp_path / "backups"
    made = []
    for _ in range(4):
        made.append(bk.create_backup(path, folder, material))
    # Datas distintas e controladas no manifesto não são editáveis (AAD): usa-se o relógio da política.
    (folder / "estranho.rrbackup").write_bytes(b"nao identificado")
    with pytest.raises(BackupError):
        bk.retention_plan(folder, keep_last=0)
    plan = bk.retention_plan(folder, keep_last=2)
    assert len(plan["manter"]) == 2 and len(plan["remover"]) == 2 and plan["nao_identificados"] == ["estranho.rrbackup"]
    recent = bk.retention_plan(folder, keep_last=1, keep_days=7)
    assert recent["remover"] == []  # tudo é recente: nada sai
    old = bk.retention_plan(folder, keep_last=1, keep_days=7, now=datetime.now(timezone.utc) + timedelta(days=30))
    assert len(old["remover"]) == 3
    with pytest.raises(BackupError, match="APAGAR:2"):
        bk.apply_retention(folder, plan, confirmation="sim")
    removed = bk.apply_retention(folder, plan, confirmation="APAGAR:2")
    assert len(removed) == 2 and (folder / "estranho.rrbackup").exists()
    assert len([b for b in bk.list_backups(folder) if b["ok"]]) == 2


# ------------------------------------------------------------ logs e métricas (itens 31–33)
def test_logs_and_metrics_carry_no_personal_content(tmp_path, monkeypatch, caplog):
    from rocha_reis_agent.ops import environments
    from rocha_reis_agent.ops.demo import build_demo
    profile = tmp_path / "demo"
    caplog.set_level(logging.DEBUG)
    build_demo(profile)
    from rocha_reis_agent.ops.demo import send_fictitious
    send_fictitious(profile, SECRET_TEXT, sender="5511900000377")
    report = json.dumps(environments.metrics(profile, backups=profile / "backups"))
    logs = caplog.text
    for value in (SECRET_TEXT, "5511900000377", "coloração"):
        assert value not in report and value not in logs
    assert '"validos": 1' in report
