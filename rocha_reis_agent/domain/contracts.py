"""Contratos do simulador, sem dependência de transporte ou provedor de IA."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
import re
import unicodedata


class SimulationError(ValueError):
    """Erro operacional seguro para apresentação no simulador."""


class InvalidInput(SimulationError):
    pass


class StorageError(SimulationError):
    """Falha controlada; nunca inclui SQL, caminho ou conteúdo armazenado."""


class CapacityExhausted(StorageError):
    """Limite de conversas ativas atingido sem conversa elegível para arquivamento.

    Transitório: não é recusa permanente do cliente; o canal reprocessa com backoff.
    """


class ConcurrentUpdate(StorageError):
    """Snapshot vencido; somente operações locais sem efeitos externos podem repetir."""


def validate_identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise InvalidInput("Use um identificador fictício de 1 a 64 letras, números, _ ou -.")
    return value


def normalize_message(value: str, max_chars: int = 2000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_chars:
        raise InvalidInput(f"Envie texto não vazio com até {max_chars} caracteres.")
    if any(unicodedata.category(c).startswith("C") and c not in "\n\r\t" for c in value):
        raise InvalidInput("O texto contém caracteres de controle não permitidos.")
    return " ".join(value.split())


def normalize_for_matching(text: str) -> str:
    """Mesma equivalência para detecção e conflitos de conteúdo publicado."""
    folded = unicodedata.normalize("NFKD", text.casefold())
    unaccented = "".join(c for c in folded if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]", " ", unaccented).split())


class ConversationState(StrEnum):
    AUTO = "AUTO"
    WAITING_HUMAN = "WAITING_HUMAN"
    HUMAN_ACTIVE = "HUMAN_ACTIVE"
    RESOLVED = "RESOLVED"
    # Compatibilidade com os nomes declarados na Fase 0; não havia persistência.
    BOT_ACTIVE = AUTO
    PENDING_HUMAN = WAITING_HUMAN


class Intent(StrEnum):
    GREETING = "greeting"
    SERVICES = "services"
    PRICE = "price"
    HOURS = "hours"
    LOCATION = "location"
    PROFESSIONALS = "professionals"
    BOOKING = "booking"
    HUMAN = "human"
    POLICY = "policy"
    CANCELLATION_POLICY = "cancellation_policy"
    FAQ = "faq"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class InboundMessage:
    """Texto validado; identificadores técnicos não são autenticação."""

    event_id: str
    conversation_id: str
    text: str = field(repr=False)
    received_at: datetime

    def __post_init__(self) -> None:
        validate_identifier(self.event_id)
        validate_identifier(self.conversation_id)
        object.__setattr__(self, "text", normalize_message(self.text))
        if not isinstance(self.received_at, datetime) or self.received_at.utcoffset() is None:
            raise InvalidInput("A mensagem precisa de timestamp com fuso horário.")


@dataclass(frozen=True, slots=True)
class Detection:
    intents: tuple[Intent, ...]
    service_ids: tuple[str, ...] = ()
    use_context: bool = False
    catalog_requested: bool = False
    faq_index: int | None = None


@dataclass(frozen=True, slots=True)
class AgentResult:
    conversation_id: str
    event_id: str
    state: ConversationState
    reply: str | None = field(default=None, repr=False)
    intents: tuple[Intent, ...] = ()
    duplicate: bool = False
    fact_references: tuple[str, ...] = ()
    prompt_version: str | None = None
