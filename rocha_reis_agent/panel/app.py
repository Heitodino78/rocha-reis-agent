"""Aplicação do painel: SEPARADA do app do webhook (nenhuma rota administrativa lá).

Executar somente em 127.0.0.1 nesta fase:
    uvicorn rocha_reis_agent.panel.app:create_panel_app --factory --host 127.0.0.1 --port 8000 \
        --no-access-log --timeout-graceful-shutdown 5
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging
from pathlib import Path
from threading import Event, Thread
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from rocha_reis_agent.api.errors import handle_unexpected_errors
from rocha_reis_agent.api.health import router as health_router
from rocha_reis_agent.application.conversations import ConversationManager
from rocha_reis_agent.application.handoff import HumanHandoffManager
from rocha_reis_agent.bootstrap import (
    build_agent, build_ai_provider, configured_dispatcher, configured_knowledge, configured_limits,
    configured_repository, queue_policy,
)
from rocha_reis_agent.catalog_io import load_synthetic_catalog
from rocha_reis_agent.channels.fake_transport import FakeWhatsAppTransport
from rocha_reis_agent.channels.service import InboundProcessor, drain
from rocha_reis_agent.config import ConfigurationError, Settings, load_settings
from rocha_reis_agent.panel.api import PanelContext, build_panel_router
from rocha_reis_agent.panel.security import PanelError
from rocha_reis_agent.panel.service import PanelService
from rocha_reis_agent.panel.store import SQLitePanelStore
from rocha_reis_agent.persistence.sqlite_channel import SQLiteChannelStore

logger = logging.getLogger("rocha_reis_agent")
STATIC = Path(__file__).parent / "static"
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'")


def _embedded_worker(settings: Settings, repository, store: SQLiteChannelStore, panel_store: SQLitePanelStore,  # type: ignore[no-untyped-def]
                     stop: Event) -> None:
    """Demonstração local: drena inbox/outbox com transporte FAKE a cada segundo."""
    knowledge_repository = configured_knowledge(settings, repository)
    catalog = None if knowledge_repository is not None else load_synthetic_catalog(
        Path(__file__).resolve().parents[2] / "fixtures" / "salon.versioned.synthetic.json")
    agent = build_agent(catalog, configured_limits(settings), conversation_repository=repository,
                        prompt_version=settings.ai_prompt_version, ai_history_limit=settings.ai_history_limit,
                        ai_timeout_seconds=settings.ai_timeout_seconds, knowledge_repository=knowledge_repository,
                        provider=build_ai_provider(settings, repository))
    inbound = InboundProcessor(store, agent, queue_policy(settings))
    outbound = configured_dispatcher(settings, store, FakeWhatsAppTransport(), run_id=uuid4().hex)
    while not stop.wait(1.0):
        try:
            drain(inbound, outbound, "panel-embedded", limit=50)
            panel_store.prune_changes()
        except Exception:
            logger.error("panel_embedded_worker_failed")


def create_panel_app(settings: Settings | None = None) -> FastAPI:
    configured = settings if settings is not None else load_settings()
    if not configured.panel_enabled:
        raise ConfigurationError("Painel desabilitado. Defina PANEL_ENABLED=true (somente local).")
    repository = configured_repository(configured)
    channel = SQLiteChannelStore(repository)
    store = SQLitePanelStore(repository)
    service = PanelService(
        conversations=ConversationManager(repository, configured_limits(configured)),  # type: ignore[arg-type]
        handoff=HumanHandoffManager(), store=store, channel=channel,
        window_seconds=configured.channel_service_window_hours * 3600,
    )
    stop = Event()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        thread = None
        if configured.panel_embedded_worker:
            thread = Thread(target=_embedded_worker, args=(configured, repository, channel, store, stop),
                            name="panel-embedded-worker", daemon=True)
            thread.start()
        yield
        stop.set()
        if thread is not None:
            thread.join(timeout=5)

    app = FastAPI(title="rocha-reis-panel", debug=False, docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)
    app.state.settings = configured
    app.state.panel_store = store
    app.state.panel_service = service
    app.state.channel_store = channel
    app.state.conversation_repository = repository

    host = urlsplit(configured.panel_origin).hostname or "127.0.0.1"
    # Bloqueia DNS rebinding: só o host configurado (e "testserver" em testes locais).
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[host, "testserver"] if configured.app_env == "test" else [host])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response: Response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.middleware("http")(handle_unexpected_errors)

    @app.exception_handler(PanelError)
    async def panel_error(request: Request, error: PanelError) -> JSONResponse:
        return JSONResponse(status_code=error.status, content={"detail": error.message})

    app.include_router(health_router)
    knowledge_repository = configured_knowledge(configured, repository)
    knowledge = None
    if knowledge_repository is not None:
        from rocha_reis_agent.panel.knowledge import KnowledgeService
        knowledge = KnowledgeService(knowledge_repository, store,
                                     require_distinct_approver=configured.knowledge_require_distinct_approver)
    from rocha_reis_agent.persistence.sqlite_ai import SQLiteUsageLedger
    app.state.knowledge_service = knowledge
    app.include_router(build_panel_router(PanelContext(configured, store, service, channel, knowledge,
                                                       SQLiteUsageLedger(repository))))
    app.mount("/panel", StaticFiles(directory=STATIC, html=True), name="panel")

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/panel/", status_code=307)

    return app
