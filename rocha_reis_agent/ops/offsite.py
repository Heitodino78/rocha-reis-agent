"""Cópia EXTERNA dos backups (Fase 17) — interface e adaptador FALSO; nenhum provedor real.

Uma pasta no mesmo disco NÃO é backup externo (a "secundária" da Fase 13 é só simulação). Este módulo
define o contrato que um destino externo real terá de cumprir e o testa com `FakeExternalStore`, que
permite injetar falhas (envio que falha, conteúdo corrompido, envio parcial). Regras:

- só arquivos `.rrbackup` JÁ CIFRADOS e íntegros saem; a chave nunca é enviada;
- cada envio é conferido baixando de volta e comparando o SHA-256; divergência apaga o remoto e conta falha;
- retenção remota: mantém os N mais recentes (padrão 14);
- estado em arquivo 0600 (último sucesso, falhas seguidas) para os alertas locais.

Integração futura (não feita): um adaptador para armazenamento de objetos compatível com S3 ou similar,
em outro provedor/região, com credencial de escrita sem permissão de apagar além da retenção — decisão e
custo dependem do salão. Nada aqui cria conta, envia dados ou usa credencial.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Protocol

from rocha_reis_agent.ops.backup import BackupError, list_backups, read_manifest


class ExternalStore(Protocol):
    simulated: bool

    def put(self, name: str, data: bytes) -> None: ...
    def get(self, name: str) -> bytes: ...
    def list(self) -> list[str]: ...
    def delete(self, name: str) -> None: ...


class ExternalError(Exception):
    pass


class FakeExternalStore:
    """Destino externo SIMULADO (diretório local marcado como falso) com falhas injetáveis."""

    simulated = True

    def __init__(self, directory: Path, *, fault: str | None = None) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        marker = self.directory / ".destino-externo-FALSO"
        if not marker.exists():
            marker.write_text("destino externo SIMULADO — não é cópia fora do servidor\n", encoding="utf-8")
        self.fault = fault  # None | "upload" | "corrupt" | "partial"

    def _path(self, name: str) -> Path:
        if "/" in name or name.startswith(".") or not name.endswith(".rrbackup"):
            raise ExternalError("Nome de objeto inválido.")
        return self.directory / name

    def put(self, name: str, data: bytes) -> None:
        if self.fault == "upload":
            raise ExternalError("Falha simulada no envio.")
        if self.fault == "corrupt":
            data = data[:-1] + bytes([data[-1] ^ 0xFF])
        if self.fault == "partial":
            data = data[: len(data) // 2]
        path = self._path(name)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)

    def get(self, name: str) -> bytes:
        return self._path(name).read_bytes()

    def list(self) -> list[str]:
        return sorted(p.name for p in self.directory.glob("*.rrbackup"))

    def delete(self, name: str) -> None:
        self._path(name).unlink(missing_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_state(path: Path) -> dict[str, object]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(path: Path, state: dict[str, object]) -> None:
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=1)
    os.replace(temporary, path)


def sync(backups: Path, store: ExternalStore, state_path: Path, *, keep: int = 14) -> dict[str, object]:
    """Envia os backups válidos que faltam no destino, confere cada um e aplica a retenção remota.

    Fase 18B-0: execução ÚNICA (lock ao lado do estado: um timer e um comando manual nunca enviam juntos);
    com um destino que não permite exclusão pelo cliente (`client_deletes` falso — o adaptador S3 real), a
    retenção remota fica com o provedor (Object Lock / regras do bucket / ciclo de vida) e nada é apagado.
    """
    if not 1 <= keep <= 365:
        raise BackupError("Retenção externa deve ficar entre 1 e 365 cópias.")
    import fcntl
    lock = os.open(str(state_path) + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackupError("Outro envio ao destino externo já está em andamento.") from None
        return _sync(backups, store, state_path, keep=keep)
    finally:
        os.close(lock)


class _Skip(Exception):
    pass


def _sync(backups: Path, store: ExternalStore, state_path: Path, *, keep: int) -> dict[str, object]:
    client_deletes = bool(getattr(store, "client_deletes", True))
    state = load_state(state_path)
    local = [b for b in list_backups(backups) if b["ok"]]
    sent: list[str] = []
    failures: list[dict[str, object]] = []
    reachable = True
    try:
        remote = set(store.list())
    except (ExternalError, OSError) as error:  # destino fora do ar: registra a falha (alerta) e não envia nada
        remote, local, reachable = set(), [], False
        failures.append({"arquivo": None, "erro": str(error) if isinstance(error, ExternalError) else "E/S"})
    # Só os `keep` mais recentes: enviar um mais antigo que a retenção apagaria em seguida criaria um ciclo
    # de reenvio a cada rodada.
    for item in sorted(local, key=lambda b: str(b["created_at"]))[-keep:]:
        name = str(item["file"])
        if name in remote:
            continue
        path = Path(backups) / name
        read_manifest(path)  # garante que é um backup cifrado do projeto (nunca chave ou banco em claro)
        data = path.read_bytes()
        try:
            store.put(name, data)
            if hashlib.sha256(store.get(name)).hexdigest() != hashlib.sha256(data).hexdigest():
                if not client_deletes:
                    raise ExternalError("Cópia externa diferente da local; mantida no destino (exclusão "
                                        "bloqueada) — conferir manualmente.")
                store.delete(name)
                raise ExternalError("Cópia externa diferente da local.")
            sent.append(name)
        except (ExternalError, OSError) as error:
            failures.append({"arquivo": name, "erro": str(error) if isinstance(error, ExternalError) else "E/S"})
    removed: list[str] = []
    remote_total: int | None = None
    try:
        if not reachable:
            raise _Skip
        remaining = sorted(store.list())
        if client_deletes:
            for name in remaining[: max(0, len(remaining) - keep)]:
                store.delete(name)
                removed.append(name)
            remaining = sorted(store.list())
        remote_total = len(remaining)
    except _Skip:
        pass
    except (ExternalError, OSError) as error:
        failures.append({"arquivo": None, "erro": str(error) if isinstance(error, ExternalError) else "E/S"})
    now = _now()
    state.update(simulado=bool(getattr(store, "simulated", True)), ultima_execucao=now, remoto_total=remote_total,
                 retencao_remota="pelo servidor" if client_deletes else "pelo provedor (Object Lock/regras do bucket)")
    stats = getattr(store, "stats", None)
    if stats is not None and hasattr(stats, "as_dict"):
        state["ultima_transferencia"] = stats.as_dict()
    if failures:
        state["falhas_seguidas"] = int(state.get("falhas_seguidas", 0)) + 1  # type: ignore[arg-type]
        state["ultima_falha"] = now
    else:
        state["falhas_seguidas"] = 0
        state["ultimo_sucesso"] = now
    _save_state(state_path, state)
    return {"enviados": sent, "falhas": failures, "removidos_por_retencao": removed, "estado": state}


def fetch(store: ExternalStore, name: str, into: Path) -> Path:
    """Baixa um backup do destino externo para um diretório privado (para `ops verificar`/`restaurar`)."""
    into = Path(into)
    into.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(into, 0o700)
    target = into / name
    if target.exists():
        raise BackupError("Arquivo já existe no destino; nada foi sobrescrito.")
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(store.get(name))
    return target
