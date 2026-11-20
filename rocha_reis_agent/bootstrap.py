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
from rocha_reis_agent.channels.service import QueuePolicy, WebhookIngestor
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
    )


def build_agent(
    knowledge: KnowledgeBase | KnowledgeCatalog | None = None, limits: SimulationLimits | None = None,
    *, provider: AIProvider | None = None, prompt_version: str | None = None,
    ai_history_limit: int = 4, ai_timeout_seconds: float = 2.0,
    conversation_repository: ConversationRepository | None = None,
) -> AgentService:
    value = knowledge if knowledge is not None else KnowledgeBase()
    repository = VersionedKnowledgeRepository(value) if isinstance(value, KnowledgeCatalog) else InMemoryKnowledgeRepository(value)
    if isinstance(conversation_repository, SQLiteConversationRepository):
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
    ingestor = WebhookIngestor(
        store, phone_number_id=settings.whatsapp_phone_number_id,
        business_account_id=settings.whatsapp_business_account_id,
        conversation_key=settings.whatsapp_conversation_key.get_secret_value().encode("utf-8"),
    )
    return store, ingestor
