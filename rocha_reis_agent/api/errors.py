"""Resposta segura para erros HTTP inesperados antes de iniciar a resposta."""

import logging
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse

logger = logging.getLogger("rocha_reis_agent")


async def handle_unexpected_errors(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    try:
        return await call_next(request)
    except Exception:
        # Não registrar exceção, URL, corpo, headers ou parâmetros do cliente.
        logger.error("request_failed")
        return JSONResponse(
            status_code=500, content={"detail": "Erro interno do servidor."}
        )
