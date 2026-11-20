"""Composição da aplicação local; iniciar com Uvicorn em modo factory."""

from fastapi import FastAPI

from rocha_reis_agent.api.errors import handle_unexpected_errors
from rocha_reis_agent.api.health import router as health_router
from rocha_reis_agent.config import ConfigurationError, Settings, load_settings
from rocha_reis_agent.bootstrap import configured_channel, configured_repository
from rocha_reis_agent.api.whatsapp import build_router
from rocha_reis_agent.observability import configure_private_log


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings if settings is not None else load_settings()
    app = FastAPI(
        title=configured.app_name,
        debug=False,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = configured
    if configured.channel_log_path is not None:
        configure_private_log(configured.channel_log_path)
    app.state.conversation_repository = configured_repository(configured)
    app.middleware("http")(handle_unexpected_errors)
    app.include_router(health_router)
    if configured.whatsapp_enabled:
        # Settings já validou segredos/armazenamento; nenhuma rota é criada se desabilitado.
        try:
            store, ingestor = configured_channel(configured, app.state.conversation_repository)
        except ValueError:
            # Autorização de homologação inválida bloqueia a inicialização, sem detalhes.
            raise ConfigurationError("Canal ou autorização de homologação inválidos.") from None
        assert configured.whatsapp_verify_token is not None and configured.whatsapp_app_secret is not None
        app.state.channel_store = store
        app.include_router(build_router(
            ingestor=ingestor,
            verify_token=configured.whatsapp_verify_token.get_secret_value(),
            app_secret=configured.whatsapp_app_secret.get_secret_value().encode("utf-8"),
            max_body_bytes=configured.whatsapp_max_body_bytes,
            count=store.bump,
        ))
    return app
