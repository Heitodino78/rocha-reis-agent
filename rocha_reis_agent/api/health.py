"""Saúde do processo.

/health — sinal de vida (o processo responde); não verifica provedores nem prontidão comercial.
/ready  — prontidão (Fase 17): banco durável legível, esquema na versão do código, WAL e espaço em disco.
Usado pela atualização (`ops atualizar`) e pelos alertas. Responde só "ok"/códigos curtos: sem caminhos,
versões de dependências, segredos ou contagens de dados.
"""

from pathlib import Path
import shutil
import sqlite3
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter()
MIN_FREE_BYTES = 50 * 1024 * 1024


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


def readiness_checks(repository: object) -> dict[str, str]:
    from rocha_reis_agent.domain.contracts import StorageError
    from rocha_reis_agent.persistence.sqlite import SCHEMA_VERSION, SQLiteConversationRepository
    if not isinstance(repository, SQLiteConversationRepository):
        return {"banco": "nao_duravel"}  # memória: serve para testes, não para operar
    checks = {"banco": "falha", "esquema": "falha", "wal": "falha", "disco": "falha"}
    try:
        with repository.read() as connection:
            connection.execute("SELECT 1 FROM conversations LIMIT 1").fetchall()
            checks["banco"] = "ok"
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            checks["esquema"] = "ok" if version == SCHEMA_VERSION else "versao_diferente"
            mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            checks["wal"] = "ok" if str(mode).lower() == "wal" else "desligado"
    except (StorageError, sqlite3.Error, OSError):
        return checks
    try:
        usage = shutil.disk_usage(Path(repository.path).parent)
        checks["disco"] = "ok" if usage.free >= MIN_FREE_BYTES else "cheio"
    except OSError:
        pass
    return checks


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    checks = await run_in_threadpool(readiness_checks, getattr(request.app.state, "conversation_repository", None))
    ok = all(value == "ok" for value in checks.values())
    return JSONResponse({"status": "ready" if ok else "not_ready", "checks": checks}, status_code=200 if ok else 503,
                        headers={"Cache-Control": "no-store"})
