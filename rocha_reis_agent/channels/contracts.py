"""Contratos internos do canal: o domínio nunca recebe o JSON da Meta.

Identificadores externos são dados não confiáveis; só são usados como chaves
opacas (hash) e como endereço de resposta. Nenhum campo prova autenticidade:
a autenticidade vem exclusivamente da assinatura verificada antes do parser.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol


class ChannelError(ValueError):
    """Payload ou configuração do canal inválidos; mensagem sem dados privados."""


class InboundKind(StrEnum):
    MESSAGE = "message"
    STATUS = "status"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class Sender:
    """Endereço de resposta. Desde 2026 a Meta pode omitir o telefone (wa_id) e
    enviar somente o BSUID (business-scoped user ID)."""

    wa_id: str | None = field(default=None, repr=False)
    user_id: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.wa_id and not self.user_id:
            raise ChannelError("Remetente sem identificador utilizável.")

    @property
    def identity_key(self) -> str:
        # BSUID é estável mesmo quando o telefone é ocultado; preferir quando presente.
        return f"bsuid:{self.user_id}" if self.user_id else f"wa:{self.wa_id}"


@dataclass(frozen=True, slots=True)
class InboundTextMessage:
    business_number_id: str
    provider_message_id: str = field(repr=False)
    sender: Sender
    text: str = field(repr=False)
    source_timestamp: datetime
    kind: Literal[InboundKind.MESSAGE] = InboundKind.MESSAGE


@dataclass(frozen=True, slots=True)
class InboundUnsupported:
    """Mensagem de cliente com tipo não suportado: conteúdo nunca é interpretado."""

    business_number_id: str
    provider_message_id: str = field(repr=False)
    sender: Sender
    message_type: str
    source_timestamp: datetime
    kind: Literal[InboundKind.UNSUPPORTED] = InboundKind.UNSUPPORTED


@dataclass(frozen=True, slots=True)
class StatusUpdate:
    """Atualização de status de mensagem ENVIADA pela empresa; nunca gera resposta."""

    business_number_id: str
    provider_message_id: str = field(repr=False)
    status: Literal["sent", "delivered", "read", "failed"]
    source_timestamp: datetime
    callback_data: str | None = field(default=None, repr=False)
    error_codes: tuple[int, ...] = ()
    kind: Literal[InboundKind.STATUS] = InboundKind.STATUS


InboundEvent = InboundTextMessage | InboundUnsupported | StatusUpdate


@dataclass(frozen=True, slots=True)
class NormalizedBatch:
    events: tuple[InboundEvent, ...]
    ignored: int = 0


@dataclass(frozen=True, slots=True)
class IngestReport:
    accepted: int = 0
    duplicates: int = 0
    conflicts: int = 0
    ignored: int = 0


# ---- Saída -----------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class OutboundText:
    operation_id: str
    business_number_id: str
    recipient: Sender
    body: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class SendAccepted:
    """A Meta ACEITOU a requisição. Não significa entrega ao cliente."""

    provider_message_id: str
    # message_status devolvido pela Meta quando presente (ex.: "accepted"); informativo.
    detail: str = "accepted"


@dataclass(frozen=True, slots=True)
class SendRejected:
    """Rejeição explícita. retryable=True somente quando há garantia de não envio
    (ex.: falha antes de transmitir, limite de taxa explícito)."""

    code: str
    retryable: bool


@dataclass(frozen=True, slots=True)
class SendUncertain:
    """Não se sabe se a Meta aceitou (timeout após envio, resposta ambígua)."""

    code: str


SendOutcome = SendAccepted | SendRejected | SendUncertain


class WhatsAppTransport(Protocol):
    def send_text(self, message: OutboundText, *, timeout_seconds: float) -> SendOutcome:
        """Adaptadores devem impor o timeout e classificar o resultado sem exceções.

        Qualquer exceção que escape é tratada pelo despachante como SendUncertain.
        """
        ...
