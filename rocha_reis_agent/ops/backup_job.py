"""Backup AGENDÁVEL (Fase 13): uma execução = verificar ambiente → copiar → cifrar → verificar →
cópia secundária → retenção → registrar estado. Reaproveita `ops/backup.py` (Fases 10 e 12).

O destino secundário desta fase é um SEGUNDO DIRETÓRIO LOCAL (simulação): valida o procedimento,
mas NÃO protege contra perda física do disco/servidor. Nenhum envio para nuvem.
Nada de segredo em argumentos, logs ou no arquivo de estado (só datas, contagens e mensagens seguras).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sqlite3
from typing import Callable, Iterator

from rocha_reis_agent.ops.backup import (
    BackupError, KeyMaterial, apply_retention, create_backup, list_backups, load_key, restore_backup,
    retention_plan, verify_backup,
)

STATUS_FILE = "estado-backup.json"
DEFAULTS = {"primario": "backups", "secundario": "backups-secundario", "chave": "chaves/backup.key",
            "manter": 14, "dias": 30}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def job_config(profile: Path) -> dict[str, object]:
    data = json.loads((profile / "perfil.json").read_text(encoding="utf-8"))
    return {**DEFAULTS, **data.get("backup", {})}


def _private_dir(path: Path, label: str) -> Path:
    if not path.is_dir():
        raise BackupError(f"Destino {label} indisponível (diretório inexistente ou desmontado).")
    status = path.stat()
    if path.is_symlink() or status.st_mode & 0o077 or status.st_uid != os.geteuid():
        raise BackupError(f"Destino {label} sem permissões privadas (0700).")
    if not os.access(path, os.W_OK):
        raise BackupError(f"Destino {label} sem permissão de escrita.")
    return path


class LocalDirectory:
    """Destino secundário por diretório. Substituível no futuro por armazenamento externo."""

    def __init__(self, path: Path, label: str = "secundário") -> None:
        self.path, self.label = Path(path), label

    def check(self) -> None:
        _private_dir(self.path, self.label)

    def put(self, source: Path, *, fault: Callable[[str], None] = lambda stage: None) -> Path:
        target = self.path / source.name
        if target.exists():
            if sha256(target.read_bytes()).digest() == sha256(source.read_bytes()).digest():
                return target  # repetição idempotente: já copiado
            raise BackupError("Já existe arquivo com o mesmo nome e conteúdo diferente no destino secundário.")
        partial = target.with_name(target.name + ".partial")
        partial.unlink(missing_ok=True)  # sobra de execução interrompida (o lock garante execução única)
        try:
            with open(source, "rb") as src, os.fdopen(os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
                fault("during_copy")
                dst.flush()
                os.fsync(dst.fileno())
            if sha256(partial.read_bytes()).digest() != sha256(source.read_bytes()).digest():
                raise BackupError("Cópia secundária diferente da original.")
            os.rename(partial, target)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        return target


@contextmanager
def _exclusive(lock_path: Path) -> Iterator[None]:
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackupError("Outro backup já está em andamento.") from None
        yield
    finally:
        os.close(descriptor)


def load_status(profile: Path) -> dict[str, object]:
    path = profile / STATUS_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_status(profile: Path, status: dict[str, object]) -> None:
    path = profile / STATUS_FILE
    partial = path.with_name(path.name + ".partial")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(status, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, path)


def _checks(profile: Path, config: dict[str, object]) -> tuple[Path, KeyMaterial, Path, Path]:
    from rocha_reis_agent.ops.demo import profile_settings
    from rocha_reis_agent.persistence.storage_security import volume_protection
    if (profile.stat().st_mode & 0o077) or not (profile / "perfil.json").is_file():
        raise BackupError("Perfil ausente ou sem permissões privadas (0700).")
    settings = profile_settings(profile)
    database = settings.storage_path
    if database is None or not database.is_file():
        raise BackupError("Banco do perfil não encontrado.")
    if settings.requires_encrypted_volume:
        protection = volume_protection(database.parent)
        if not (protection.encrypted and protection.persistent):
            raise BackupError("Volume do banco sem cifragem verificada; backup recusado neste ambiente.")
    primary = _private_dir(profile / str(config["primario"]), "primário")
    secondary = profile / str(config["secundario"])
    key_file = profile / str(config["chave"])
    for folder in (primary, secondary):
        if key_file.absolute().parent == folder.absolute() or folder.absolute() in key_file.absolute().parents:
            raise BackupError("A chave não pode ficar dentro de um diretório de backups.")
    load_key(key_file)  # chave ausente, ilegível ou com permissão frouxa → falha antes de copiar
    return database, KeyMaterial("keyfile", key_file=key_file), primary, secondary


def run_backup_job(profile: Path, *, fault: Callable[[str], None] = lambda stage: None) -> dict[str, object]:
    """Executa uma rodada. Sempre grava o estado; levanta BackupError se algo falhou."""
    profile = Path(profile).absolute()
    config = job_config(profile)
    status = load_status(profile)
    status["ultima_tentativa"] = _now()
    status.pop("ultimo_erro", None)
    try:
        primary_dir = _private_dir(profile / str(config["primario"]), "primário")
        with _exclusive(primary_dir / ".backup.lock"):
            database, material, primary, secondary = _checks(profile, config)
            backup = create_backup(database, primary, material, fault=fault)
            manifest = verify_backup(backup, material)
            destination = LocalDirectory(secondary)
            try:
                destination.check()
                copy = destination.put(backup, fault=fault)
                verify_backup(copy, material)
                status["destino_secundario"] = {"situacao": "ok", "simulado": True, "em": _now()}
            except (BackupError, OSError) as error:
                status["destino_secundario"] = {"situacao": "falhou", "simulado": True, "em": _now(),
                                                "erro": str(error) if isinstance(error, BackupError) else
                                                "Falha de escrita no destino secundário."}
                raise BackupError("Backup primário criado e verificado, mas a cópia secundária falhou.") from None
            removed = 0
            for folder in (primary, secondary):
                plan = retention_plan(folder, keep_last=int(config["manter"]), keep_days=int(config["dias"]))
                removed += len(apply_retention(folder, plan, confirmation=f"APAGAR:{len(plan['remover'])}"))
            status.update({
                "ultimo_sucesso": _now(), "falhas_consecutivas": 0, "ultimo_backup": backup.name,
                "esquema": manifest["schema_version"], "tamanho_bytes": backup.stat().st_size,
                "copias_primario": len([b for b in list_backups(primary) if b["ok"]]),
                "copias_secundario": len([b for b in list_backups(secondary) if b["ok"]]),
                "removidos_pela_retencao": removed,
            })
            return status
    except (BackupError, OSError, sqlite3.Error) as error:
        status["falhas_consecutivas"] = int(status.get("falhas_consecutivas", 0)) + 1  # type: ignore[arg-type]
        status["ultimo_erro"] = str(error) if isinstance(error, BackupError) else "Falha de leitura ou escrita."
        raise BackupError(str(status["ultimo_erro"])) from None
    finally:
        _save_status(profile, status)


def restore_test(profile: Path) -> dict[str, object]:
    """Restaura o backup válido mais recente num diretório ISOLADO, confere e apaga a cópia de teste."""
    profile = Path(profile).absolute()
    config = job_config(profile)
    status = load_status(profile)
    material = KeyMaterial("keyfile", key_file=profile / str(config["chave"]))
    valid = sorted((b for b in list_backups(profile / str(config["primario"])) if b["ok"]),
                   key=lambda b: str(b["created_at"]))
    result: dict[str, object] = {"quando": _now()}
    workspace = profile / f".teste-restauracao-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}"
    try:
        if not valid:
            raise BackupError("Nenhum backup válido para testar.")
        name = str(valid[-1]["file"])
        workspace.mkdir(mode=0o700)
        manifest = restore_backup(profile / str(config["primario"]) / name, workspace / "teste.synthetic.sqlite3", material)
        result.update({"resultado": "ok", "backup": name, "contagens": manifest["table_counts"]})
        return result
    except BackupError as error:
        result.update({"resultado": "falhou", "erro": str(error)})
        raise
    finally:
        shutil.rmtree(workspace, ignore_errors=True)  # a cópia em claro do teste não fica no disco
        status["ultimo_teste_restauracao"] = result
        _save_status(profile, status)


def backup_health(profile: Path, *, max_age_hours: float = 26.0) -> dict[str, object]:
    """Indicadores para monitoramento (sem caminhos nem segredos)."""
    status = load_status(Path(profile))
    age = None
    if status.get("ultimo_sucesso"):
        last = datetime.strptime(str(status["ultimo_sucesso"]), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        age = round((datetime.now(timezone.utc) - last).total_seconds() / 3600, 1)
    healthy = age is not None and age <= max_age_hours and not status.get("falhas_consecutivas")
    return {"saudavel": healthy, "ultimo_sucesso": status.get("ultimo_sucesso"),
            "ultima_tentativa": status.get("ultima_tentativa"), "idade_horas": age,
            "falhas_consecutivas": status.get("falhas_consecutivas", 0), "ultimo_erro": status.get("ultimo_erro"),
            "copias_primario": status.get("copias_primario"), "copias_secundario": status.get("copias_secundario"),
            "destino_secundario": status.get("destino_secundario"),
            "ultimo_teste_restauracao": {k: v for k, v in (status.get("ultimo_teste_restauracao") or {}).items()
                                         if k in ("quando", "resultado", "backup")}}
