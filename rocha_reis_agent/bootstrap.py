"""Composição local: seleciona adaptadores sem colocar SQL no domínio."""

from rocha_reis_agent.ai.contracts import AIProvider
from rocha_reis_agent.application.conversations import ConversationManager
from rocha_reis_agent.application.handoff import HumanHandoffManager
from rocha_reis_agent.application.ports import ConversationRepository
from rocha_reis_agent.application.service import AgentService
from rocha_reis_agent.config import Settings
from rocha_reis_agent.domain.conversations import SimulationLimits
from rocha_reis_agent.intents import RuleIntentDetector
from rocha_reis_agent.knowledge import KnowledgeBase
from rocha_reis_agent.knowledge_catalog import KnowledgeCatalog
from rocha_reis_agent.knowledge_projection import legacy_synthetic_catalog
from rocha_reis_agent.memory import InMemoryConversationRepository, InMemoryKnowledgeRepository, VersionedKnowledgeRepository
from rocha_reis_agent.persistence.sqlite import SQLiteConversationRepository
from rocha_reis_agent.persistence.sqlite_channel import SQLiteChannelStore
from rocha_reis_agent.channels.service import OutboxDispatcher, QueuePolicy, WebhookIngestor
from rocha_reis_agent.channels.homologation import HomologationAuthorization, HomologationGuard, load_authorization
from rocha_reis_agent.channels.contracts import WhatsAppTransport
from rocha_reis_agent.channels.fake_transport import FakeWhatsAppTransport
from rocha_reis_agent import config as config_module
from rocha_reis_agent.responses import ResponseGenerator


def configured_limits(settings: Settings) -> SimulationLimits:
    return SimulationLimits(
        max_message_chars=settings.sim_max_message_chars,
        max_history_entries=settings.sim_max_history_entries,
        max_events_per_session=settings.sim_max_events_per_session,
        max_conversations=settings.sim_max_conversations,
        context_turns=settings.sim_context_turns,
    )


def configured_repository(settings: Settings) -> ConversationRepository:
    if settings.storage_backend == "memory":
        return InMemoryConversationRepository()
    return SQLiteConversationRepository(
        settings.storage_path, limits=configured_limits(settings),
        busy_timeout_ms=settings.storage_busy_timeout_ms,
        max_bytes=settings.storage_max_bytes,
        retention_days=settings.storage_retention_days,
        audit_days=settings.storage_audit_days,
        audit_limit=settings.storage_audit_limit,
        archive_idle_hours=settings.storage_archive_idle_hours,
    )


def build_agent(
    knowledge: KnowledgeBase | KnowledgeCatalog | None = None, limits: SimulationLimits | None = None,
    *, provider: AIProvider | None = None, prompt_version: str | None = None,
    ai_history_limit: int = 4, ai_timeout_seconds: float = 2.0,
    conversation_repository: ConversationRepository | None = None,
    knowledge_repository: object | None = None,
) -> AgentService:
    value = knowledge if knowledge is not None else KnowledgeBase()
    repository = VersionedKnowledgeRepository(value) if isinstance(value, KnowledgeCatalog) else InMemoryKnowledgeRepository(value)
    if knowledge_repository is not None:
        # Fase 8: conhecimento no banco (revisões aprovadas pelo painel); sem vínculo por hash de arquivo.
        repository = knowledge_repository  # type: ignore[assignment]
    elif isinstance(conversation_repository, SQLiteConversationRepository):
        catalog = value if isinstance(value, KnowledgeCatalog) else legacy_synthetic_catalog(value)
        conversation_repository.bind_knowledge(catalog.model_dump_json())
    return AgentService(
        ConversationManager(conversation_repository if conversation_repository is not None else InMemoryConversationRepository(), limits),
        repository, RuleIntentDetector(), ResponseGenerator(), HumanHandoffManager(),
        provider=provider, prompt_version=prompt_version,
        ai_history_limit=ai_history_limit, ai_timeout_seconds=ai_timeout_seconds,
    )


def queue_policy(settings: Settings) -> QueuePolicy:
    return QueuePolicy(
        inbox_lease_seconds=settings.channel_inbox_lease_seconds,
        inbox_max_attempts=settings.channel_inbox_max_attempts,
        outbox_lease_seconds=settings.channel_outbox_lease_seconds,
        outbox_max_attempts=settings.channel_outbox_max_attempts,
        backoff_base_seconds=settings.channel_backoff_base_seconds,
        backoff_max_seconds=settings.channel_backoff_max_seconds,
        send_timeout_seconds=settings.whatsapp_send_timeout_seconds,
        service_window_seconds=settings.channel_service_window_hours * 3600,
    )


def configured_channel(settings: Settings, repository: ConversationRepository) -> tuple[SQLiteChannelStore, WebhookIngestor]:
    """Canal exige configuração validada (Settings) e repositório SQLite durável."""
    if not settings.whatsapp_enabled or not isinstance(repository, SQLiteConversationRepository):
        raise ValueError("Canal WhatsApp não habilitado ou sem armazenamento durável.")
    assert settings.whatsapp_conversation_key is not None and settings.whatsapp_phone_number_id is not None
    store = SQLiteChannelStore(repository)
    authorization = homologation_authorization(settings)
    ingestor = WebhookIngestor(
        store, phone_number_id=settings.whatsapp_phone_number_id,
        business_account_id=settings.whatsapp_business_account_id,
        conversation_key=settings.whatsapp_conversation_key.get_secret_value().encode("utf-8"),
        sender_filter=authorization.accepts_inbound if authorization is not None else None,
    )
    return store, ingestor


def homologation_authorization(settings: Settings) -> HomologationAuthorization | None:
    """Em homologação, a autorização é obrigatória e validada inteira; fora dela, inexistente."""
    if settings.app_env != "homologation":
        return None
    assert settings.homolog_authorization_file is not None and settings.whatsapp_phone_number_id is not None
    return load_authorization(settings.homolog_authorization_file,
                              expected_phone_number_id=settings.whatsapp_phone_number_id)


def configured_dispatcher(settings: Settings, store: SQLiteChannelStore, transport: WhatsAppTransport,
                          *, run_id: str) -> OutboxDispatcher:
    """Despachante com interrupção por configuração e, em homologação, guarda obrigatória."""
    guard = None
    authorization = homologation_authorization(settings)
    if authorization is not None:
        assert settings.whatsapp_conversation_key is not None
        guard = HomologationGuard(
            authorization, run_id=run_id,
            recipient_key=settings.whatsapp_conversation_key.get_secret_value().encode("utf-8"),
        )
    return OutboxDispatcher(store, transport, queue_policy(settings), guard=guard,
                            halted_by_config=settings.channel_dispatch_halted)


def build_transport(settings: Settings, *, http_transport: object | None = None) -> WhatsAppTransport:
    """Seleciona o transporte. O real exige a trava de código E configuração validada.

    `http_transport` (httpx2.BaseTransport) existe só para testes com MockTransport.
    """
    if settings.whatsapp_transport == "fake":
        return FakeWhatsAppTransport()
    unlocked = (config_module.HOMOLOGATION_TRANSPORT_ACTIVATION_AUTHORIZED if settings.app_env == "homologation"
                else config_module.GRAPH_TRANSPORT_ACTIVATION_AUTHORIZED)
    if not unlocked:
        raise ValueError("Transporte real não autorizado nesta fase.")
    # Importação tardia: o cliente HTTP só é carregado quando o real é autorizado.
    from rocha_reis_agent.channels.graph_transport import GraphAPITransport, GraphTransportConfig, build_client

    assert settings.whatsapp_access_token is not None and settings.whatsapp_phone_number_id is not None
    assert settings.whatsapp_graph_api_version is not None
    config = GraphTransportConfig(
        access_token=settings.whatsapp_access_token.get_secret_value(),
        phone_number_id=settings.whatsapp_phone_number_id,
        api_version=settings.whatsapp_graph_api_version,
        connect_timeout_seconds=settings.whatsapp_connect_timeout_seconds,
        read_timeout_seconds=settings.whatsapp_send_timeout_seconds,
    )
    return GraphAPITransport(config, build_client(http_transport))  # type: ignore[arg-type]


def configured_knowledge(settings: Settings, repository: ConversationRepository):  # type: ignore[no-untyped-def]
    """Conhecimento no banco (Fase 8) ou None para o modo de arquivo/fixture existente."""
    if settings.knowledge_source != "database":
        return None
    from rocha_reis_agent.persistence.sqlite_knowledge import SQLiteKnowledgeRepository
    assert isinstance(repository, SQLiteConversationRepository)
    return SQLiteKnowledgeRepository(repository)


def build_ai_provider(settings: Settings, repository: ConversationRepository, *, http_client: object | None = None):  # type: ignore[no-untyped-def]
    """None = FakeAIProvider (padrão). O real exige a trava de código E configuração validada."""
    if settings.ai_provider == "fake":
        return None
    if not config_module.AI_REAL_PROVIDER_ACTIVATION_AUTHORIZED:
        raise ValueError("Provedor de IA real não autorizado nesta fase.")
    from rocha_reis_agent.ai.prompts import PromptRegistry
    if PromptRegistry().get(settings.ai_prompt_version).output_contract != "ai-response-v2":
        raise ValueError("Provedor real exige versão de prompt com contrato ai-response-v2.")
    # Importação tardia: o SDK só é carregado quando a IA real é autorizada.
    import anthropic
    from rocha_reis_agent.ai.anthropic_provider import AnthropicConfig, AnthropicProvider
    from rocha_reis_agent.persistence.sqlite_ai import SQLiteUsageLedger

    assert settings.ai_api_key is not None and isinstance(repository, SQLiteConversationRepository)
    client = anthropic.Anthropic(api_key=settings.ai_api_key.get_secret_value(), max_retries=settings.ai_max_retries,
                                 timeout=settings.ai_timeout_seconds, http_client=http_client)  # type: ignore[arg-type]
    config = AnthropicConfig(
        model=settings.ai_model, max_output_tokens=settings.ai_max_output_tokens, effort=settings.ai_effort,
        max_retries=settings.ai_max_retries, refusal_fallback=settings.ai_refusal_fallback,
        max_concurrency=settings.ai_max_concurrency, max_requests_per_day=settings.ai_max_requests_per_day,
        max_cost_usd_per_day=settings.ai_max_cost_usd_per_day, max_input_chars=settings.ai_max_input_chars,
    )
    return AnthropicProvider(config, client, SQLiteUsageLedger(repository))
