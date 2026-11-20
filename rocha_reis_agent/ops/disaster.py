"""Ensaio de recuperação de desastre (Fase 13) — LOCAL, ISOLADO e só com dados fictícios.

Simula: o "servidor" (cópia do perfil) perde o banco; um ambiente NOVO é reconstruído a partir de
(1) configuração guardada à parte, (2) chaves guardadas à parte e (3) o backup mais recente da cópia
SECUNDÁRIA. O perfil de origem nunca é alterado. Mede o tempo de cada etapa (não é RTO acordado).
"""

from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
from time import perf_counter

from rocha_reis_agent.ops.backup import BackupError, KeyMaterial, list_backups, read_manifest, restore_backup
from rocha_reis_agent.ops.backup_job import job_config


def _private(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def drill(source: Path, workspace: Path) -> dict[str, object]:
    source, workspace = Path(source).absolute(), Path(workspace).absolute()
    if workspace.exists() and any(workspace.iterdir()):
        raise BackupError("O diretório do ensaio precisa estar vazio (nada é sobrescrito).")
    if workspace == source or source in workspace.parents:
        raise BackupError("O ensaio não pode rodar dentro do perfil de origem.")
    timings: dict[str, float] = {}
    started = perf_counter()

    def mark(step: str, since: float) -> float:
        timings[step] = round((perf_counter() - since) * 1000, 1)
        return perf_counter()

    config = job_config(source)
    data = json.loads((source / "perfil.json").read_text(encoding="utf-8"))
    database_name = data.get("database", "demo.synthetic.sqlite3")
    _private(workspace)

    # 1. "Servidor" em operação (cópia do perfil) e PERDA do banco.
    t = perf_counter()
    server = workspace / "servidor"
    shutil.copytree(source, server, symlinks=False)
    for leftover in (server / "dados").glob(database_name + "*"):
        leftover.unlink()
    t = mark("1_perda_do_banco_simulada", t)

    # 2. Ambiente NOVO: configuração e chaves vindas da guarda separada (aqui, o perfil de origem).
    rebuilt = _private(workspace / "reconstruido")
    for name in ("dados", "backups", "backups-secundario", "chaves"):
        _private(rebuilt / name)
    shutil.copy2(source / "perfil.json", rebuilt / "perfil.json")
    os.chmod(rebuilt / "perfil.json", 0o600)
    for marker in (".rocha-reis-demo", ".rocha-reis-preprod"):
        if (source / marker).exists():
            shutil.copy2(source / marker, rebuilt / marker)
    for key in sorted((source / "chaves").glob("*.key")):
        shutil.copy2(key, rebuilt / "chaves" / key.name)
        os.chmod(rebuilt / "chaves" / key.name, 0o600)
    if (source / "tls").is_dir():
        shutil.copytree(source / "tls", rebuilt / "tls")
    t = mark("2_configuracao_e_chaves_restauradas", t)

    # 3. Backup mais recente da cópia SECUNDÁRIA (a primária "morreu" junto com o servidor).
    secondary = server / str(config["secundario"])
    valid = sorted((b for b in list_backups(secondary) if b["ok"]), key=lambda b: str(b["created_at"]))
    if not valid:
        raise BackupError("Nenhum backup válido na cópia secundária: recuperação impossível.")
    chosen = secondary / str(valid[-1]["file"])
    manifest = read_manifest(chosen)
    material = KeyMaterial("keyfile", key_file=rebuilt / str(config["chave"]))
    restore_backup(chosen, rebuilt / "dados" / database_name, material)
    t = mark("3_banco_restaurado_da_copia_secundaria", t)

    # 4. Abrir pelo perfil reconstruído (marcação de ambiente, migrações) e conferir tudo.
    from rocha_reis_agent.bootstrap import configured_repository
    from rocha_reis_agent.ops.demo import profile_settings
    from rocha_reis_agent.panel.mfa import MfaCipher
    settings = profile_settings(rebuilt)
    repository = configured_repository(settings)
    with closing(sqlite3.connect(repository.path.as_uri() + "?mode=ro", uri=True)) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        counts = {table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                  for table in manifest["table_counts"]}  # type: ignore[union-attr]
        outbox = dict(connection.execute("SELECT status,count(*) FROM channel_outbox GROUP BY status").fetchall())
        inbox = dict(connection.execute("SELECT status,count(*) FROM channel_inbox GROUP BY status").fetchall())
        users = connection.execute("SELECT count(*) FROM panel_users WHERE active=1").fetchone()[0]
        mfa_rows = connection.execute("SELECT user_id,secret_blob,key_id FROM panel_mfa").fetchall()
    mfa_ok = None
    if settings.panel_mfa_key_file is not None:
        cipher = MfaCipher.from_file(settings.panel_mfa_key_file)
        for user_id, blob, key_id in mfa_rows:
            cipher.decrypt(user_id, blob, key_id)  # levanta se a chave recuperada não servir
        mfa_ok = len(mfa_rows)
    t = mark("4_integridade_e_conferencias", t)

    # 5. Pronto para voltar a funcionar? (mesmas verificações do iniciador; porta livre qualquer)
    from rocha_reis_agent.ops.launcher import preflight
    import socket
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    checks = preflight(rebuilt, port)
    mark("5_verificacoes_de_inicializacao", t)
    timings["total"] = round((perf_counter() - started) * 1000, 1)
    expected = manifest["table_counts"]
    return {
        "resultado": "recuperado" if integrity == "ok" and counts == expected else "divergente",
        "backup_usado": chosen.name, "backup_criado_em": manifest["created_at"],
        "integridade": integrity, "contagens_conferem": counts == expected, "contagens": counts,
        "fila_saida": outbox, "fila_entrada": inbox, "funcionarios_ativos": users,
        "segredos_2_etapas_legiveis": mfa_ok, "verificacoes": checks, "tempos_ms": timings,
        "perdido_desde_o_backup": "tudo o que entrou depois de " + str(manifest["created_at"]),
        "aviso": "Ensaio local com dados fictícios; cópia secundária no mesmo disco. Não comprova recuperação "
                 "após perda física do servidor nem define RTO/RPO.",
    }
