"""Orquestração do canal: webhook → inbox → núcleo existente → outbox → transporte.

Regras de negócio permanecem no AgentService; este módulo só traduz eventos do
canal e controla filas. Logs contêm apenas nomes estáticos e códigos de resultado.
"""

from dataclasses import dataclass, replace
from hashlib import sha256
import hmac
import logging
from collections.abc import Callable
from typing import Any

from rocha_reis_agent.application.service import AgentService
from rocha_reis_agent.channels.contracts import (
    InboundEvent, InboundKind, IngestReport, OutboundText, Sender, SendOutcome, SendUncertain,
    StatusUpdate, WhatsAppTransport,
)
from rocha_reis_agent.channels.homologation import HomologationGuard
from rocha_reis_agent.channels.meta import parse_webhook
from rocha_reis_agent.domain.contracts import (
    AgentResult, CapacityExhausted, ConcurrentUpdate, InboundMessage, InvalidInput, SimulationError, StorageError,
)
from rocha_reis_agent.persistence.sqlite_channel import (
    ClaimedInbound, ClaimedOutbound, LeaseLost, SQLiteChannelStore,
)

logger = logging.getLogger("rocha_reis_agent")


def conversation_id_for(event: InboundEvent, key: bytes) -> str | None:
    """Pseudônimo estável por número comercial + remetente (HMAC; sem telefone em claro)."""
    if isinstance(event, StatusUpdate):
        return None
    material = f"whatsapp:{event.business_number_id}:{event.sender.identity_key}".encode("utf-8")
    return "wa-" + hmac.new(key, material, sha256).hexdigest()[:40]


def event_id_for(provider_message_id: str) -> str:
    """ID externo (wamid) contém caracteres fora do formato interno; usar hash estável."""
    return "wa-" + sha256(provider_message_id.encode("utf-8")).hexdigest()[:40]


@dataclass(frozen=True, slots=True)
class QueuePolicy:
    inbox_lease_seconds: float = 60.0
    inbox_max_attempts: int = 5
    outbox_lease_seconds: float = 30.0
    outbox_max_attempts: int = 3
    backoff_base_seconds: float = 2.0
    backoff_max_seconds: float = 300.0
    send_timeout_seconds: float = 10.0
    # Janela de atendimento de 24h com margem de segurança (padrão 23h).
    service_window_seconds: float = 23 * 3600

    def backoff(self, attempt: int) -> float:
        return min(self.backoff_max_seconds, self.backoff_base_seconds * 2 ** max(0, attempt - 1))


class WebhookIngestor:
    def __init__(self, store: SQLiteChannelStore, *, phone_number_id: str,
                 business_account_id: str | None, conversation_key: bytes,
                 sender_filter: Callable[[Sender], bool] | None = None) -> None:
        self.store = store
        # Homologação: remetentes fora da allowlist são descartados sem persistir conteúdo.
        self.sender_filter = sender_filter
        self.phone_number_id = phone_number_id
        self.business_account_id = business_account_id
        self.conversation_key = conversation_key

    def ingest(self, payload: Any) -> IngestReport:
        """Chamada SOMENTE após verificar a assinatura. Nenhuma IA é executada aqui."""
        batch = parse_webhook(payload, phone_number_id=self.phone_number_id,
                              business_account_id=self.business_account_id)
        events = batch.events
        filtered = 0
        if self.sender_filter is not None:
            kept = tuple(e for e in events if isinstance(e, StatusUpdate) or self.sender_filter(e.sender))
            filtered, events = len(events) - len(kept), kept
            if filtered:
                self.store.bump(*["webhook_ignored_not_allowlisted"] * filtered)
        entries = [(event, conversation_id_for(event, self.conversation_key)) for event in events]
        report = self.store.ingest(entries) if entries else IngestReport()
        return replace(report, ignored=batch.ignored + filtered)


class InboundProcessor:
    def __init__(self, store: SQLiteChannelStore, agent: AgentService, policy: QueuePolicy) -> None:
        self.store = store
        self.agent = agent
        self.policy = policy

    def process_one(self, worker: str) -> str | None:
        item = self.store.claim_inbound(
            worker, lease_seconds=self.policy.inbox_lease_seconds, max_attempts=self.policy.inbox_max_attempts,
        )
        if item is None:
            return None
        try:
            outcome = self._process(item, worker)
        except LeaseLost:
            outcome = "lease_lost"
        logger.info("channel_inbound_processed", extra={"outcome": outcome})
        return outcome

    def _process(self, item: ClaimedInbound, worker: str) -> str:
        if item.kind == InboundKind.STATUS:
            return self.store.apply_status(item, worker)
        if item.kind == InboundKind.UNSUPPORTED and item.conversation_id is not None \
                and not self.store.repository.is_retired(item.conversation_id):
            # U1 (22/11): áudio/imagem/documento… numa conversa com atendimento humano ou ENCERRADA não pode
            # sumir: entra no histórico como aviso (conteúdo nunca interpretado) e reabre a encerrada.
            outcome = self._process_media(item, worker)
            if outcome is not None:
                return outcome
        if item.kind == InboundKind.UNSUPPORTED or item.conversation_id is None:
            # Tipos não suportados nunca são interpretados nem respondidos automaticamente.
            self.store.finish_inbound(item, worker, outcome="unsupported_type_ignored")
            return "unsupported_type_ignored"
        event_id = event_id_for(str(item.payload["provider_message_id"]))
        try:
            message = InboundMessage(event_id, item.conversation_id, str(item.payload["text"]), item.source_timestamp)
        except InvalidInput:
            self.store.finish_inbound(item, worker, outcome="invalid_input", failed=True)
            return "invalid_input"
        conversation_id = item.conversation_id
        # Fase 10: contato cujos dados foram excluídos voltou a escrever. A decisão é do
        # controlador dos dados (DADOS_PENDENTES); padrão = bloquear, mas de forma VISÍVEL
        # (aviso ao ADMIN, conteúdo descartado) e reversível pelo painel.
        if self.store.repository.is_retired(conversation_id):
            if self.store.retired_contact_policy() == "allow_new_conversation":
                self.store.repository.reinstate(conversation_id, origin="client")
            else:
                self.store.finish_inbound(item, worker, outcome="retired_contact_blocked")
                return "retired_contact_blocked"
        outcomes: list[str] = []

        def effects(result: AgentResult, session_id: str) -> None:
            outcome = "duplicate" if result.duplicate else "replied" if result.reply else "recorded_without_reply"
            outcomes[:] = [outcome]
            self.store.stage_conversation_effects(
                item, worker, outcome=outcome, reply=None if result.duplicate else result.reply,
                conversation_id=conversation_id, session_id=session_id, state=result.state, event_id=event_id,
            )

        try:
            self.agent.handle(message, effects=effects)
        except LeaseLost:
            raise
        except CapacityExhausted:
            # Não é recusa do cliente: aguarda arquivamento/capacidade com backoff e alerta operacional.
            logger.warning("channel_capacity_exhausted")
            retried = self.store.retry_inbound(
                item, worker, outcome="capacity_exhausted",
                delay_seconds=self.policy.backoff(item.attempts), max_attempts=self.policy.inbox_max_attempts,
            )
            return "capacity_retry_scheduled" if retried else "capacity_exhausted"
        except (ConcurrentUpdate, StorageError):
            # Transitório: nada desta tentativa foi confirmado; repetir com backoff limitado.
            retried = self.store.retry_inbound(
                item, worker, outcome="storage_retry",
                delay_seconds=self.policy.backoff(item.attempts), max_attempts=self.policy.inbox_max_attempts,
            )
            return "retry_scheduled" if retried else "failed_after_retries"
        except SimulationError:
            # Recusa determinística do núcleo (sessão encerrada, limite, ID com conteúdo diferente).
            self.store.finish_inbound(item, worker, outcome="rejected_by_core", failed=True)
            return "rejected_by_core"
        return outcomes[0]

    def _process_media(self, item: ClaimedInbound, worker: str) -> str | None:
        """Mídia na conversa (ver AgentService.handle_media). None = tratamento anterior (ignorada)."""
        handle = getattr(self.agent, "handle_media", None)
        if handle is None:
            return None
        conversation_id = str(item.conversation_id)
        event_id = event_id_for(str(item.payload["provider_message_id"]))
        outcomes: list[str] = []

        def effects(result: AgentResult, session_id: str) -> None:
            outcome = "duplicate" if result.duplicate else "media_recorded_without_reply"
            outcomes[:] = [outcome]
            self.store.stage_conversation_effects(
                item, worker, outcome=outcome, reply=None, conversation_id=conversation_id,
                session_id=session_id, state=result.state, event_id=event_id,
            )

        try:
            result = handle(event_id, conversation_id, str(item.payload.get("type") or "unknown"),
                            item.source_timestamp, effects=effects)
        except LeaseLost:
            raise
        except (ConcurrentUpdate, StorageError):
            retried = self.store.retry_inbound(
                item, worker, outcome="storage_retry",
                delay_seconds=self.policy.backoff(item.attempts), max_attempts=self.policy.inbox_max_attempts,
            )
            return "retry_scheduled" if retried else "failed_after_retries"
        except SimulationError:
            self.store.finish_inbound(item, worker, outcome="rejected_by_core", failed=True)
            return "rejected_by_core"
        return outcomes[0] if result is not None and outcomes else None


class OutboxDispatcher:
    def __init__(self, store: SQLiteChannelStore, transport: WhatsAppTransport, policy: QueuePolicy, *,
                 guard: HomologationGuard | None = None, halted_by_config: bool = False) -> None:
        self.store = store
        self.transport = transport
        self.policy = policy
        self.guard = guard
        # CHANNEL_DISPATCH_HALTED=true só pode interromper; nunca reativa um halt do banco.
        self.halted_by_config = halted_by_config

    def dispatch_one(self, worker: str) -> str | None:
        if self.halted_by_config:
            self.store.bump("dispatch_blocked_config_halt")
            return None
        claimed = self.store.claim_outbound(
            worker, lease_seconds=self.policy.outbox_lease_seconds,
            window_seconds=self.policy.service_window_seconds, guard=self.guard,
        )
        if claimed is None:
            return None
        outcome = self._send(claimed)
        try:
            status = self.store.finish_outbound(
                claimed, worker, outcome, max_attempts=self.policy.outbox_max_attempts, backoff=self.policy.backoff,
            )
        except StorageError:
            # Permanece SENDING; ao vencer o lease vira UNKNOWN (nunca reenvio cego).
            status = "UNRECORDED"
        logger.info("channel_outbound_dispatched", extra={"outcome": status})
        return status

    def _send(self, claimed: ClaimedOutbound) -> SendOutcome:
        message = OutboundText(claimed.id, claimed.business_number_id, claimed.recipient, claimed.body)
        try:
            return self.transport.send_text(message, timeout_seconds=self.policy.send_timeout_seconds)
        except Exception:
            # Exceção do adaptador: não sabemos se houve transmissão → incerto.
            return SendUncertain("adapter_exception")


def drain(inbound: InboundProcessor, outbound: OutboxDispatcher, worker: str, *, limit: int = 1000) -> list[str]:
    """Processa até esvaziar (ou atingir o limite), para demonstração e testes."""
    results: list[str] = []
    for _ in range(limit):
        step = inbound.process_one(worker) or outbound.dispatch_one(worker)
        if step is None:
            break
        results.append(step)
    return results
