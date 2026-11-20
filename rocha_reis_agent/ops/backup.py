"""Backup e restauração do banco sintético (Fase 10).

Princípios:
- Cópia CONSISTENTE pela API de backup online do SQLite (funciona com WAL e com o
  aplicativo aberto), nunca cópia de arquivo.
- Integridade verificada antes de cifrar e depois de decifrar (PRAGMA integrity_check,
  foreign_key_check, SHA-256 do conteúdo).
- Criptografia AUTENTICADA com biblioteca consolidada (`cryptography`, AES-256-GCM);
  o manifesto vai em claro como dado associado: qualquer alteração invalida o arquivo.
  A chave NUNCA é gravada junto do backup (arquivo de chave separado ou frase-senha).
- Restauração SEMPRE para um arquivo novo e isolado; substituir o banco ativo é um
  passo separado (`promote`), com confirmação explícita e cópia de segurança anterior.
- Escrita atômica: `.partial` → fsync → rename. Falha no meio não deixa arquivo final.

O banco ativo NÃO é criptografado por este módulo; apenas os backups.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from contextlib import closing, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import sqlite3
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from rocha_reis_agent.persistence.sqlite import APPLICATION_ID, SCHEMA_VERSION

FORMAT = "rocha-reis-backup"
FORMAT_VERSION = 1
MAGIC = b"RRBACKUP"
SUFFIX = ".rrbackup"
KEY_BYTES = 32
NONCE_BYTES = 12
MAX_MANIFEST = 64 * 1024
# scrypt: parâmetros recomendados para uso interativo (RFC 7914 / documentação da cryptography).
SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}
COUNTED_TABLES = ("conversations", "messages", "transitions", "events", "channel_inbox", "channel_outbox",
                  "panel_users", "panel_assignments", "panel_audit", "audit", "knowledge_revisions")
PURPOSE = "rocha-reis-synthetic-only"


class BackupError(Exception):
    """Erro com mensagem segura para o operador (sem segredo nem conteúdo)."""


@dataclass(frozen=True)
class KeyMaterial:
    """Chave de 32 bytes vinda de arquivo separado OU derivada de frase-senha."""

    kind: str                    # "keyfile" | "passphrase"
    key_file: Path | None = None
    passphrase: str | None = None

    def derive(self, kdf: dict[str, object] | None) -> bytes:
        if self.kind == "keyfile":
            assert self.key_file is not None
            return load_key(self.key_file)
        if not self.passphrase or len(self.passphrase) < 12:
            raise BackupError("Frase-senha ausente ou curta (mínimo 12 caracteres).")
        if not kdf or kdf.get("name") != "scrypt":
            raise BackupError("Este backup não foi criado com frase-senha.")
        salt = base64.b64decode(str(kdf["salt"]))
        return Scrypt(salt=salt, length=KEY_BYTES, n=int(kdf["n"]), r=int(kdf["r"]),  # type: ignore[arg-type]
                      p=int(kdf["p"])).derive(self.passphrase.encode("utf-8"))  # type: ignore[arg-type]


# ------------------------------------------------------------------ utilitários
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise BackupError("Diretório de backup não pode ser link simbólico.")
    os.chmod(path, 0o700)
    status = path.stat()
    if status.st_mode & 0o777 != 0o700 or status.st_uid != os.geteuid():
        raise BackupError("Diretório sem permissões privadas (0700); use um volume Linux local.")
    return path


def _write_atomic(target: Path, data: bytes, *, fault: Callable[[str], None]) -> None:
    partial = target.with_name(target.name + ".partial")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data[: len(data) // 2])
            fault("during_write")
            handle.write(data[len(data) // 2:])
            handle.flush()
            os.fsync(handle.fileno())
        if target.exists():
            raise BackupError("Arquivo de destino já existe; nada foi sobrescrito.")
        os.rename(partial, target)
        directory = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        with suppress(FileNotFoundError):
            partial.unlink()
        raise


def generate_key(path: Path) -> Path:
    """Cria arquivo de chave (32 bytes aleatórios, base64) com permissão 0600. Nunca sobrescreve."""
    path = Path(path).absolute()
    _private_dir(path.parent)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        raise BackupError("Arquivo de chave já existe; não será sobrescrito.") from None
    with os.fdopen(descriptor, "w", encoding="ascii") as handle:
        handle.write(base64.b64encode(secrets.token_bytes(KEY_BYTES)).decode("ascii") + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return path


def load_key(path: Path) -> bytes:
    path = Path(path).absolute()
    try:
        status = path.lstat()
    except FileNotFoundError:
        raise BackupError("Arquivo de chave não encontrado.") from None
    if path.is_symlink() or status.st_mode & 0o077 or status.st_uid != os.geteuid():
        raise BackupError("Arquivo de chave deve ser privado (0600) e do próprio usuário.")
    try:
        key = base64.b64decode(path.read_text(encoding="ascii").strip(), validate=True)
    except (ValueError, UnicodeDecodeError):
        raise BackupError("Arquivo de chave inválido.") from None
    if len(key) != KEY_BYTES:
        raise BackupError("Arquivo de chave inválido.")
    return key


def _check_key_location(material: KeyMaterial, backup_dir: Path) -> None:
    if material.kind == "keyfile" and material.key_file is not None:
        key_dir = Path(material.key_file).absolute().parent
        backup_dir = backup_dir.absolute()
        if key_dir == backup_dir or backup_dir in key_dir.parents:
            raise BackupError("A chave não pode ficar no mesmo diretório dos backups.")


def _inspect(connection: sqlite3.Connection) -> dict[str, object]:
    """Verificações de integridade e contagens (sem ler conteúdo de mensagens)."""
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise BackupError("Falha na verificação de integridade do banco.")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise BackupError("Falha na verificação de integridade referencial.")
    if connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
        raise BackupError("Arquivo não reconhecido como banco do projeto.")
    purpose = connection.execute("SELECT value FROM metadata WHERE key='purpose'").fetchone()
    if purpose is None or purpose[0] != PURPOSE:
        raise BackupError("Banco fora do escopo sintético permitido.")
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    counts = {name: connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0]
              for name in COUNTED_TABLES if name in tables}
    return {"schema_version": connection.execute("PRAGMA user_version").fetchone()[0], "table_counts": counts}


def _snapshot(source: Path) -> bytes:
    """Cópia consistente via API de backup online, lida em modo somente leitura, para a MEMÓRIA.

    Fase 12: nenhum arquivo temporário em claro (antes ia para o diretório temporário do sistema,
    fora do volume protegido). O cabeçalho é marcado como diário clássico (bytes 18–19 = 1), como o
    SQLite faz ao sair do modo WAL, para que a imagem abra em qualquer modo.
    """
    if not source.name.endswith(".synthetic.sqlite3") or not source.is_file() or source.is_symlink():
        raise BackupError("Banco de origem ausente ou fora do padrão .synthetic.sqlite3.")
    try:
        with closing(sqlite3.connect(source.absolute().as_uri() + "?mode=ro", uri=True, timeout=5)) as src, \
                closing(sqlite3.connect(":memory:")) as dst:
            src.backup(dst)
            image = bytearray(dst.serialize())
    except sqlite3.Error:
        raise BackupError("Não foi possível ler o banco de origem de forma consistente.") from None
    if image[18:20] == b"\x02\x02":
        image[18:20] = b"\x01\x01"
    return bytes(image)


def _memory(image: bytes) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.deserialize(image)
    return connection


# ------------------------------------------------------------------ operações
def create_backup(source: Path, backup_dir: Path, material: KeyMaterial, *,
                  fault: Callable[[str], None] = lambda stage: None) -> Path:
    backup_dir = _private_dir(Path(backup_dir).absolute())
    _check_key_location(material, backup_dir)
    kdf: dict[str, object] | None = None
    if material.kind == "passphrase":
        kdf = {"name": "scrypt", "salt": base64.b64encode(secrets.token_bytes(16)).decode("ascii"), **SCRYPT}
    key = material.derive(kdf)
    plaintext = _snapshot(Path(source))
    with closing(_memory(plaintext)) as connection:
        details = _inspect(connection)
    created = _now()
    manifest = {
        "format": FORMAT, "format_version": FORMAT_VERSION, "created_at": created,
        "application_id": APPLICATION_ID, **details,
        "content_sha256": sha256(plaintext).hexdigest(), "content_bytes": len(plaintext),
        "cipher": "AES-256-GCM", "kdf": kdf or {"name": "keyfile"},
        "note": "Backup do banco SINTÉTICO do protótipo. O banco ativo não é criptografado.",
    }
    header = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    nonce = secrets.token_bytes(NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, MAGIC + header)
    fault("after_encrypt")
    name = f"rocha-reis-{created.replace(':', '').replace('-', '')}-{secrets.token_hex(3)}{SUFFIX}"
    target = backup_dir / name
    _write_atomic(target, MAGIC + struct.pack(">I", len(header)) + header + nonce + ciphertext, fault=fault)
    return target


def read_manifest(path: Path) -> dict[str, object]:
    """Lê o manifesto SEM a chave (não contém segredo nem conteúdo). Não prova autenticidade."""
    manifest, _, _ = _parse(Path(path))
    return manifest


def _parse(path: Path) -> tuple[dict[str, object], bytes, bytes]:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise BackupError("Arquivo de backup não encontrado.") from None
    except OSError:
        raise BackupError("Arquivo de backup ilegível.") from None
    if not data.startswith(MAGIC) or len(data) < len(MAGIC) + 4:
        raise BackupError("Arquivo não é um backup do projeto ou está corrompido.")
    size = struct.unpack(">I", data[len(MAGIC):len(MAGIC) + 4])[0]
    start = len(MAGIC) + 4
    if size > MAX_MANIFEST or len(data) < start + size + NONCE_BYTES + 16:
        raise BackupError("Arquivo não é um backup do projeto ou está corrompido.")
    header = data[start:start + size]
    try:
        manifest = json.loads(header.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BackupError("Manifesto do backup corrompido.") from None
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise BackupError("Arquivo não é um backup do projeto.")
    return manifest, header, data[start + size:]


def _decrypt(path: Path, material: KeyMaterial) -> tuple[dict[str, object], bytes]:
    manifest, header, rest = _parse(Path(path))
    if manifest.get("format_version") != FORMAT_VERSION:
        raise BackupError(f"Versão de formato de backup incompatível ({manifest.get('format_version')}).")
    version = manifest.get("schema_version")
    if not isinstance(version, int) or not 1 <= version <= SCHEMA_VERSION:
        raise BackupError(f"Backup de esquema {version} incompatível com esta versão (até {SCHEMA_VERSION}).")
    kdf = manifest.get("kdf") if isinstance(manifest.get("kdf"), dict) else None
    if material.kind == "keyfile" and kdf and kdf.get("name") != "keyfile":
        raise BackupError("Este backup exige a frase-senha, não arquivo de chave.")
    key = material.derive(kdf if material.kind == "passphrase" else None)
    try:
        plaintext = AESGCM(key).decrypt(rest[:NONCE_BYTES], rest[NONCE_BYTES:], MAGIC + header)
    except InvalidTag:
        raise BackupError("Chave incorreta ou arquivo de backup alterado/corrompido.") from None
    if sha256(plaintext).hexdigest() != manifest.get("content_sha256"):
        raise BackupError("Conteúdo não confere com o manifesto.")
    return manifest, plaintext


def verify_backup(path: Path, material: KeyMaterial) -> dict[str, object]:
    """Decifra em diretório temporário privado, verifica integridade e contagens; nada é restaurado."""
    manifest, plaintext = _decrypt(Path(path), material)
    try:
        with closing(_memory(plaintext)) as connection:  # verificação em memória: nada em claro no disco
            details = _inspect(connection)
    except sqlite3.DatabaseError:
        raise BackupError("Conteúdo do backup não é um banco válido.") from None
    if details["table_counts"] != manifest.get("table_counts") or details["schema_version"] != manifest["schema_version"]:
        raise BackupError("Contagens do backup não conferem com o manifesto.")
    return manifest


def restore_backup(path: Path, target: Path, material: KeyMaterial, *,
                   fault: Callable[[str], None] = lambda stage: None) -> dict[str, object]:
    """Restaura para um arquivo NOVO e isolado. Recusa destino existente (nunca sobrescreve)."""
    target = Path(target).absolute()
    if not target.name.endswith(".synthetic.sqlite3"):
        raise BackupError("Destino deve terminar em .synthetic.sqlite3.")
    if target.exists() or Path(str(target) + "-wal").exists():
        raise BackupError("Destino já existe; a restauração nunca sobrescreve. Escolha um caminho novo.")
    _private_dir(target.parent)
    manifest = verify_backup(path, material)
    _, plaintext = _decrypt(Path(path), material)
    _write_atomic(target, plaintext, fault=fault)
    with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)) as connection:
        _inspect(connection)
    return manifest


def promote(restored: Path, active: Path, *, confirmation: str,
            fault: Callable[[str], None] = lambda stage: None) -> Path:
    """Substitui o CONTEÚDO do banco ativo pelo restaurado, após cópia de segurança.

    Exige a frase exata `SUBSTITUIR:<nome do arquivo ativo>` e que o painel/worker esteja
    parado (lock exclusivo do SQLite; falha se houver conexão ativa). Retorna o caminho
    da cópia de segurança do banco anterior (mesmo diretório, permissão 0600).
    """
    restored, active = Path(restored).absolute(), Path(active).absolute()
    if confirmation != f"SUBSTITUIR:{active.name}":
        raise BackupError(f"Confirmação incorreta. Digite exatamente: SUBSTITUIR:{active.name}")
    if restored == active or not restored.is_file() or not active.is_file():
        raise BackupError("Informe um banco restaurado e um banco ativo existentes e distintos.")
    with closing(sqlite3.connect(restored.as_uri() + "?mode=ro", uri=True)) as connection:
        details = _inspect(connection)
    if int(details["schema_version"]) > SCHEMA_VERSION:  # type: ignore[arg-type]
        raise BackupError("Banco restaurado de versão mais nova que este software.")
    safety = active.with_name(f"{active.name.removesuffix('.synthetic.sqlite3')}.pre-restore-"
                              f"{_now().replace(':', '').replace('-', '')}.synthetic.sqlite3")
    try:
        with closing(sqlite3.connect(active, timeout=0.5, isolation_level=None)) as dst:
            # Lock exclusivo: se o aplicativo estiver aberto, falha em vez de trocar dados sob ele.
            dst.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            dst.execute("BEGIN EXCLUSIVE")
            dst.execute("ROLLBACK")
            with closing(sqlite3.connect(safety)) as copy:
                dst.backup(copy)
            os.chmod(safety, 0o600)
            fault("before_replace")
            with closing(sqlite3.connect(restored.as_uri() + "?mode=ro", uri=True)) as src:
                src.backup(dst)  # atômico do ponto de vista do SQLite
    except sqlite3.OperationalError:
        raise BackupError("Banco ativo em uso: pare o painel e o worker antes de substituir.") from None
    with closing(sqlite3.connect(active.as_uri() + "?mode=ro", uri=True)) as connection:
        _inspect(connection)
    return safety


def list_backups(backup_dir: Path) -> list[dict[str, object]]:
    backup_dir = Path(backup_dir)
    if not backup_dir.is_dir():
        return []
    result = []
    for path in sorted(backup_dir.glob(f"*{SUFFIX}")):
        try:
            manifest = read_manifest(path)
            result.append({"file": path.name, "bytes": path.stat().st_size, "created_at": manifest.get("created_at"),
                           "schema_version": manifest.get("schema_version"),
                           "format_version": manifest.get("format_version"), "ok": True})
        except BackupError as error:
            result.append({"file": path.name, "ok": False, "error": str(error)})
    return result


def retention_plan(backup_dir: Path, *, keep_last: int, keep_days: int | None = None,
                   now: datetime | None = None) -> dict[str, list[str]]:
    """Fase 12 — quais backups SAIRIAM pela política (nada é apagado aqui).

    Mantém os `keep_last` mais recentes válidos e, se `keep_days`, todos os mais novos que isso.
    Nunca propõe apagar o backup válido mais recente nem arquivos que não se identificam como
    backup do projeto (esses são listados para conferência humana).
    """
    if keep_last < 1 or (keep_days is not None and keep_days < 1):
        raise BackupError("Retenção exige manter ao menos 1 backup e prazo de ao menos 1 dia.")
    now = now or datetime.now(timezone.utc)
    valid, unknown = [], []
    for item in list_backups(backup_dir):
        if item["ok"] and isinstance(item.get("created_at"), str):
            valid.append(item)
        else:
            unknown.append(str(item["file"]))
    valid.sort(key=lambda item: str(item["created_at"]), reverse=True)
    remove = []
    for index, item in enumerate(valid):
        created = datetime.strptime(str(item["created_at"]), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        recent = keep_days is not None and (now - created).days < keep_days
        if index >= keep_last and not recent:
            remove.append(str(item["file"]))
    return {"manter": [str(i["file"]) for i in valid if str(i["file"]) not in remove],
            "remover": remove, "nao_identificados": unknown}


def apply_retention(backup_dir: Path, plan: dict[str, list[str]], *, confirmation: str) -> list[str]:
    if confirmation != f"APAGAR:{len(plan['remover'])}":
        raise BackupError(f"Confirmação incorreta. Digite exatamente: APAGAR:{len(plan['remover'])}")
    removed = []
    for name in plan["remover"]:
        target = Path(backup_dir) / name
        if target.parent != Path(backup_dir) or not name.endswith(SUFFIX) or target.is_symlink():
            raise BackupError("Arquivo fora do diretório de backups; nada mais foi apagado.")
        target.unlink()
        removed.append(name)
    return removed
