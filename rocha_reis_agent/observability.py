"""Log privado do canal: arquivo 0600 do usuário, só nomes de eventos e campos técnicos.

Não registra texto, telefone, BSUID, wamid, tokens, assinaturas nem query strings.
Somente as chaves de `ALLOWED_EXTRAS` (contadores e códigos de classificação) são
anexadas à linha; qualquer outro atributo extra é descartado.
"""

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path

from rocha_reis_agent.domain.contracts import StorageError

ALLOWED_EXTRAS = ("outcome", "code", "failure_kind", "accepted", "duplicates", "conflicts", "ignored")
_MARKER = "_rocha_reis_private_log"
# Fase 17: rotação (antes o arquivo crescia sem limite numa operação 24/7). ~30 MB no máximo.
MAX_LOG_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 5


class _PrivateRotatingHandler(RotatingFileHandler):
    """Rotaciona por tamanho; todo arquivo (inclusive o novo após a rotação) nasce 0600, sem seguir links."""

    def _open(self):  # type: ignore[no-untyped-def]
        descriptor = os.open(self.baseFilename, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        return os.fdopen(descriptor, "a", encoding=self.encoding)


class _TechnicalFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = " ".join(f"{k}={getattr(record, k)}" for k in ALLOWED_EXTRAS if hasattr(record, k))
        stamp = self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z")
        # record.msg é sempre um nome estático no código do projeto; argumentos não são interpolados.
        return f"{stamp} {record.levelname} {record.msg}" + (f" {fields}" if fields else "")


def configure_private_log(path: Path) -> None:
    """Anexa (uma vez) um handler de arquivo privado ao logger do projeto."""
    path = Path(path).absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise StorageError("Log não pode usar links simbólicos.")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if info.st_mode & 0o777 != 0o600 or info.st_uid != os.geteuid():
            raise StorageError("Volume sem permissões privadas 0600 para o log.")
    finally:
        os.close(descriptor)
    logger = logging.getLogger("rocha_reis_agent")
    if any(getattr(h, _MARKER, None) == str(path) for h in logger.handlers):
        return
    handler = _PrivateRotatingHandler(path, mode="a", maxBytes=MAX_LOG_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8")
    handler.setFormatter(_TechnicalFormatter())
    setattr(handler, _MARKER, str(path))
    logger.addHandler(handler)
    if logger.level == logging.NOTSET or logger.level > logging.INFO:
        logger.setLevel(logging.INFO)


def remove_private_logs() -> None:
    """Fecha e remove handlers privados (encerramento do processo e testes)."""
    logger = logging.getLogger("rocha_reis_agent")
    for handler in list(logger.handlers):
        if getattr(handler, _MARKER, None) is not None:
            logger.removeHandler(handler)
            handler.close()
