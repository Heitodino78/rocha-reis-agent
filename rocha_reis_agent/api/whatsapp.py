"""Rotas do webhook oficial Meta. Montadas somente com WHATSAPP_ENABLED=true.

Ordem do POST: limite de tamanho → assinatura sobre bytes brutos → JSON →
normalização → gravação durável → 200. Nenhuma IA ou envio ocorre na requisição.
Logs: somente nomes estáticos; nunca token, assinatura, corpo, telefone ou texto.
"""

import json
import logging

from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, PlainTextResponse

from rocha_reis_agent.channels.contracts import ChannelError
from rocha_reis_agent.channels.meta import verify_signature, verify_subscription
from rocha_reis_agent.channels.service import WebhookIngestor
from rocha_reis_agent.domain.contracts import StorageError

logger = logging.getLogger("rocha_reis_agent")
PATH = "/webhooks/whatsapp"


def _reject(status: int, detail: str, event: str) -> JSONResponse:
    logger.warning(event)
    return JSONResponse(status_code=status, content={"detail": detail})


def build_router(
    *, ingestor: WebhookIngestor, verify_token: str, app_secret: bytes, max_body_bytes: int,
) -> APIRouter:
    router = APIRouter()

    @router.get(PATH, include_in_schema=False)
    async def verify(request: Request) -> Response:
        query = request.query_params
        challenge = verify_subscription(
            query.get("hub.mode"), query.get("hub.verify_token"), query.get("hub.challenge"), verify_token,
        )
        if challenge is None:
            return _reject(403, "Verificação recusada.", "whatsapp_verification_rejected")
        logger.info("whatsapp_verification_accepted")
        return PlainTextResponse(challenge)

    @router.post(PATH, include_in_schema=False)
    async def receive(request: Request) -> Response:
        declared = request.headers.get("content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > max_body_bytes):
            return _reject(413, "Corpo excede o limite.", "whatsapp_webhook_too_large")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > max_body_bytes:
                return _reject(413, "Corpo excede o limite.", "whatsapp_webhook_too_large")
        raw = bytes(body)
        if not verify_signature(app_secret, raw, request.headers.get("x-hub-signature-256")):
            return _reject(401, "Assinatura inválida.", "whatsapp_webhook_bad_signature")
        try:
            payload = json.loads(raw)
        except (ValueError, RecursionError):
            return _reject(400, "Payload inválido.", "whatsapp_webhook_bad_json")
        try:
            report = await run_in_threadpool(ingestor.ingest, payload)
        except ChannelError:
            return _reject(400, "Payload inválido.", "whatsapp_webhook_bad_structure")
        except StorageError:
            # Sem 200: a Meta reentrega e a deduplicação absorve repetições.
            return _reject(503, "Indisponível; tente novamente.", "whatsapp_webhook_storage_unavailable")
        logger.info("whatsapp_webhook_received", extra={
            "accepted": report.accepted, "duplicates": report.duplicates,
            "conflicts": report.conflicts, "ignored": report.ignored,
        })
        return JSONResponse({"status": "received"})

    return router
