"""Inbox e outbox duráveis do canal WhatsApp no MESMO arquivo SQLite das conversas.

Garantias (detalhadas em docs/INTEGRACAO_META_FASE4.md):
- Ingestão: todos os eventos de um webhook são gravados em uma única transação;
  sucesso HTTP só após commit. Chave única por número comercial + hash do ID externo.
- Processamento: conclusão da inbox, efeitos da conversa e criação da outbox são
  gravados no mesmo commit (efeitos encenados no repositório de conversas).
- Envio: SENDING é confirmado ANTES da chamada ao transporte; lease vencido em
  SENDING vira UNKNOWN e nunca é reenviado automaticamente.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import sqlite3
from uuid import uuid4

from rocha_reis_agent.channels.contracts import (
    InboundEvent, InboundKind, InboundTextMessage, InboundUnsupported, IngestReport,
    SendAccepted, SendOutcome, SendRejected, Sender, StatusUpdate,
)
from rocha_reis_agent.channels.homologation import GuardCounts, HomologationGuard
from rocha_reis_agent.channels.send_policy import SendContext, authorize_free_text
from rocha_reis_agent.domain.contracts import ConversationState, StorageError
from rocha_reis_agent.domain.conversations import utc_now
from rocha_reis_agent.persistence.sqlite import SQLiteConversationRepository


class LeaseLost(StorageError):
    """O item foi reivindicado por outro worker; a tentativa inteira é descartada."""


class AssignmentLost(StorageError):
    """O operador perdeu a responsabilidade pela conversa antes do commit."""


@dataclass(frozen=True, slots=True)
class ContactWindow:
    business_number_id: str
    recipient: Sender = field(repr=False)
    last_inbound_at: datetime


def iso(value: datetime) -> str:
    """Formato fixo e ordenável lexicograficamente (UTC, microssegundos)."""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_iso(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True, slots=True)
class ClaimedInbound:
    id: int
    kind: InboundKind
    business_number_id: str
    conversation_id: str | None
    payload: dict[str, object] = field(repr=False)
    source_timestamp: datetime
    received_at: datetime
    attempts: int


@dataclass(frozen=True, slots=True)
class ClaimedOutbound:
    id: str
    business_number_id: str
    conversation_id: str
    recipient: Sender
    body: str = field(repr=False)
    attempts: int


class SQLiteChannelStore:
    channel = "whatsapp"

    def __init__(
        self, repository: SQLiteConversationRepository, *, clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.repository = repository
        self.clock = clock

    # ---------------------------------------------------------------- ingestão
    def ingest(self, entries: Sequence[tuple[InboundEvent, str | None]]) -> IngestReport:
        """Grava todos os eventos ou nenhum. Reentregas iguais viram duplicata;
        mesmo ID com conteúdo diferente é conflito (não sobrescreve nem processa)."""
        accepted = duplicates = conflicts = 0
        now = iso(self.clock())
        with self.repository.write_transaction() as connection:
            for event, conversation_id in entries:
                key, payload = self._inbox_record(event)
                digest = _hash(_json(payload))
                existing = connection.execute(
                    "SELECT digest FROM channel_inbox WHERE channel=? AND business_number_id=? AND dedup_key=?",
                    (self.channel, event.business_number_id, key),
                ).fetchone()
                if existing is not None:
                    if existing["digest"] == digest:
                        duplicates += 1
                    else:
                        conflicts += 1
                    continue
                connection.execute(
                    "INSERT INTO channel_inbox(channel,business_number_id,dedup_key,kind,digest,payload,status,"
                    "conversation_id,attempts,next_attempt_at,source_timestamp,received_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,'PENDING',?,0,?,?,?,?)",
                    (self.channel, event.business_number_id, key, event.kind.value, digest, _json(payload),
                     conversation_id, now, iso(event.source_timestamp), now, now),
                )
                accepted += 1
            for name, value in (("webhook_events_accepted", accepted), ("webhook_events_duplicate", duplicates),
                                ("webhook_events_conflict", conflicts)):
                if value:
                    self._bump(connection, name, value)
        return IngestReport(accepted, duplicates, conflicts)

    @staticmethod
    def _inbox_record(event: InboundEvent) -> tuple[str, dict[str, object]]:
        if isinstance(event, StatusUpdate):
            return f"st:{_hash(event.provider_message_id + ':' + event.status)}", {
                "provider_message_id": event.provider_message_id, "status": event.status,
                "callback": event.callback_data, "errors": list(event.error_codes),
            }
        payload: dict[str, object] = {
            "provider_message_id": event.provider_message_id,
            "wa_id": event.sender.wa_id, "user_id": event.sender.user_id,
        }
        if isinstance(event, InboundTextMessage):
            payload["text"] = event.text
        elif isinstance(event, InboundUnsupported):
            payload["type"] = event.message_type
        return f"msg:{_hash(event.provider_message_id)}", payload

    # ---------------------------------------------------------- processamento
    def claim_inbound(self, worker: str, *, lease_seconds: float, max_attempts: int) -> ClaimedInbound | None:
        """Reivindica o próximo item pronto, respeitando ordem por conversa."""
        now = self.clock()
        with self.repository.write_transaction() as connection:
            # Lease vencido com tentativas esgotadas → falha definitiva, sem nova tentativa.
            connection.execute(
                "UPDATE channel_inbox SET status='FAILED',outcome='lease_expired_max_attempts',lease_owner=NULL,"
                "lease_expires_at=NULL,updated_at=? WHERE status='PROCESSING' AND lease_expires_at<=? AND attempts>=?",
                (iso(now), iso(now), max_attempts),
            )
            row = connection.execute(
                "SELECT * FROM channel_inbox AS i WHERE ((i.status='PENDING' AND i.next_attempt_at<=?) "
                "OR (i.status='PROCESSING' AND i.lease_expires_at<=?)) AND NOT EXISTS ("
                "SELECT 1 FROM channel_inbox AS o WHERE i.conversation_id IS NOT NULL "
                "AND o.conversation_id=i.conversation_id AND o.id<i.id AND o.status IN ('PENDING','PROCESSING')) "
                "ORDER BY i.id LIMIT 1",
                (iso(now), iso(now)),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE channel_inbox SET status='PROCESSING',lease_owner=?,lease_expires_at=?,attempts=attempts+1,"
                "updated_at=? WHERE id=?",
                (worker, iso(now + timedelta(seconds=lease_seconds)), iso(now), row["id"]),
            )
            return ClaimedInbound(
                row["id"], InboundKind(row["kind"]), row["business_number_id"], row["conversation_id"],
                json.loads(row["payload"]), parse_iso(row["source_timestamp"]), parse_iso(row["received_at"]),
                row["attempts"] + 1,
            )

    @staticmethod
    def _complete(connection: sqlite3.Connection, item_id: int, worker: str, status: str,
                  outcome: str, now: str, *, redact: bool) -> None:
        cursor = connection.execute(
            "UPDATE channel_inbox SET status=?,outcome=?,lease_owner=NULL,lease_expires_at=NULL,updated_at=?"
            + (",payload='{}'" if redact else "") +
            " WHERE id=? AND status='PROCESSING' AND lease_owner=?",
            (status, outcome, now, item_id, worker),
        )
        if cursor.rowcount != 1:
            raise LeaseLost("Item reivindicado por outro processador; tentativa descartada.")

    def stage_conversation_effects(
        self, item: ClaimedInbound, worker: str, *, outcome: str, reply: str | None,
        conversation_id: str, session_id: str, state: ConversationState, event_id: str,
    ) -> None:
        """Encenar, no commit da conversa, conclusão da inbox e eventual outbox."""
        now = iso(self.clock())
        recipient = _json({"wa_id": item.payload.get("wa_id"), "user_id": item.payload.get("user_id")})
        window = iso(min(item.source_timestamp, item.received_at))

        def effect(connection: sqlite3.Connection) -> None:
            self._complete(connection, item.id, worker, "DONE", outcome, now, redact=True)
            if item.payload.get("wa_id") or item.payload.get("user_id"):
                # Endereço e janela da última mensagem do cliente (resposta humana futura).
                connection.execute(
                    "INSERT INTO channel_contacts(conversation_id,business_number_id,recipient,last_inbound_at,updated_at) "
                    "VALUES(?,?,?,?,?) ON CONFLICT(conversation_id) DO UPDATE SET recipient=excluded.recipient,"
                    "business_number_id=excluded.business_number_id,"
                    "last_inbound_at=max(last_inbound_at,excluded.last_inbound_at),updated_at=excluded.updated_at",
                    (conversation_id, item.business_number_id, recipient, window, now),
                )
            if reply is None:
                return
            too_long = len(reply) > 4096
            connection.execute(
                "INSERT INTO channel_outbox(id,channel,business_number_id,conversation_id,session_id,source_inbox_id,"
                "source_event_id,required_state,recipient,body,window_opened_at,status,outcome,attempts,"
                "next_attempt_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?)",
                (uuid4().hex, self.channel, item.business_number_id, conversation_id, session_id, item.id,
                 event_id, state.value, "{}" if too_long else recipient, "" if too_long else reply, window,
                 "FAILED" if too_long else "PENDING", "body_too_long" if too_long else None, now, now, now),
            )

        self.repository.stage_effect(effect)

    def contact_window(self, conversation_id: str) -> ContactWindow | None:
        with self.repository.read() as connection:
            row = connection.execute("SELECT * FROM channel_contacts WHERE conversation_id=?", (conversation_id,)).fetchone()
        if row is None:
            return None
        recipient = json.loads(row["recipient"])
        if not (recipient.get("wa_id") or recipient.get("user_id")):
            return None
        return ContactWindow(row["business_number_id"], Sender(wa_id=recipient.get("wa_id"), user_id=recipient.get("user_id")),
                             parse_iso(row["last_inbound_at"]))

    def operator_message(self, idempotency_key: str) -> dict[str, object] | None:
        with self.repository.read() as connection:
            row = connection.execute("SELECT id,conversation_id,operator_user_id,status FROM channel_outbox "
                                     "WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            return dict(row) if row else None

    def stage_operator_message(
        self, *, operation_id: str, idempotency_key: str, conversation_id: str, session_id: str,
        operator_user_id: str, body: str, contact: ContactWindow,
    ) -> None:
        """Encenar, no commit da conversa, a resposta humana na outbox (origem operador).

        Reconfere no próprio commit que o operador continua responsável pela conversa.
        """
        now = iso(self.clock())
        recipient = _json({"wa_id": contact.recipient.wa_id, "user_id": contact.recipient.user_id})

        def effect(connection: sqlite3.Connection) -> None:
            row = connection.execute("SELECT user_id FROM panel_assignments WHERE conversation_id=?",
                                     (conversation_id,)).fetchone()
            if row is None or row["user_id"] != operator_user_id:
                raise AssignmentLost("Responsabilidade pela conversa mudou; mensagem não registrada.")
            connection.execute(
                "INSERT INTO channel_outbox(id,channel,business_number_id,conversation_id,session_id,source_inbox_id,"
                "source_event_id,required_state,recipient,body,window_opened_at,status,attempts,next_attempt_at,"
                "created_at,updated_at,origin,operator_user_id,idempotency_key) "
                "VALUES(?,?,?,?,?,NULL,NULL,'HUMAN_ACTIVE',?,?,?,'PENDING',0,?,?,?,'operator',?,?)",
                (operation_id, self.channel, contact.business_number_id, conversation_id, session_id, recipient, body,
                 iso(contact.last_inbound_at), now, now, now, operator_user_id, idempotency_key),
            )
            self._bump(connection, "operator_messages_queued")

        self.repository.stage_effect(effect)

    def finish_inbound(self, item: ClaimedInbound, worker: str, *, outcome: str,
                       failed: bool = False, conversation_id: str | None = None) -> None:
        now = iso(self.clock())
        with self.repository.write_transaction() as connection:
            if conversation_id is not None:
                connection.execute("UPDATE channel_inbox SET conversation_id=? WHERE id=?", (conversation_id, item.id))
            # Falha definitiva preserva o conteúdo para investigação local (sujeito à exclusão).
            self._complete(connection, item.id, worker, "FAILED" if failed else "DONE", outcome, now,
                           redact=not failed)

    def retry_inbound(self, item: ClaimedInbound, worker: str, *, outcome: str,
                      delay_seconds: float, max_attempts: int) -> bool:
        """Devolve à fila com backoff; após o limite, marca FAILED. Retorna se haverá nova tentativa."""
        now = self.clock()
        with self.repository.write_transaction() as connection:
            if item.attempts >= max_attempts:
                self._complete(connection, item.id, worker, "FAILED", outcome, iso(now), redact=False)
                return False
            cursor = connection.execute(
                "UPDATE channel_inbox SET status='PENDING',outcome=?,lease_owner=NULL,lease_expires_at=NULL,"
                "next_attempt_at=?,updated_at=? WHERE id=? AND status='PROCESSING' AND lease_owner=?",
                (outcome, iso(now + timedelta(seconds=delay_seconds)), iso(now), item.id, worker),
            )
            if cursor.rowcount != 1:
                raise LeaseLost("Item reivindicado por outro processador.")
            return True

    # Transições válidas do status de ENTREGA (diferente do estado da requisição).
    DELIVERY_NEXT = {
        None: {"sent", "delivered", "read", "failed"},
        "sent": {"delivered", "read", "failed"},
        "delivered": {"read"},
        "read": set(),
        "failed": set(),
    }

    def apply_status(self, item: ClaimedInbound, worker: str) -> str:
        """Status atualiza a outbox; nunca gera resposta.

        Correlação confiável apenas: (1) ID do provedor já registrado; ou (2) nosso
        identificador de operação em biz_opaque_callback_data, e somente para linhas
        que podem ter chegado à Meta (SENDING/UNKNOWN). Nunca por horário ou conteúdo.
        """
        now = iso(self.clock())
        payload = item.payload
        provider_id = str(payload["provider_message_id"])
        status = str(payload["status"])
        callback = payload.get("callback")
        errors = payload.get("errors") or []
        with self.repository.write_transaction() as connection:
            row = connection.execute("SELECT * FROM channel_outbox WHERE provider_message_id=?", (provider_id,)).fetchone()
            if row is None and isinstance(callback, str):
                row = connection.execute(
                    "SELECT * FROM channel_outbox WHERE id=? AND provider_message_id IS NULL "
                    "AND status IN ('SENDING','UNKNOWN')", (callback,),
                ).fetchone()
            if row is None:
                outcome = "status_unmatched"
            else:
                if row["status"] in ("SENDING", "UNKNOWN"):
                    # O ID no status prova que a Meta aceitou a requisição.
                    connection.execute(
                        "UPDATE channel_outbox SET status='ACCEPTED',outcome='reconciled_by_status',"
                        "provider_message_id=?,lease_owner=NULL,lease_expires_at=NULL,body='',recipient='{}',"
                        "updated_at=? WHERE id=?", (provider_id, now, row["id"]),
                    )
                current = row["delivery_status"]
                if status in self.DELIVERY_NEXT[current]:
                    code = errors[0] if status == "failed" and errors and type(errors[0]) is int else None
                    connection.execute(
                        "UPDATE channel_outbox SET delivery_status=?,delivery_error_code=?,delivery_updated_at=?,"
                        "updated_at=? WHERE id=?", (status, code, now, now, row["id"]),
                    )
                    outcome = f"status_{status}"
                elif status == current:
                    outcome = "status_repeated"
                else:
                    # Regressão (fora de ordem) ou contradição: não sobrescreve o estado mais avançado.
                    connection.execute("UPDATE channel_outbox SET status_anomalies=status_anomalies+1,updated_at=? "
                                       "WHERE id=?", (now, row["id"]))
                    outcome = "status_out_of_order_ignored"
            self._complete(connection, item.id, worker, "DONE", outcome, now, redact=False)
        return outcome

    # ------------------------------------------------------------------ envio
    def claim_outbound(self, worker: str, *, lease_seconds: float, window_seconds: float,
                       guard: HomologationGuard | None = None) -> ClaimedOutbound | None:
        now = self.clock()
        with self.repository.write_transaction() as connection:
            # Interrupção emergencial: nenhuma tentativa nova nem retry; outbox preservada.
            if connection.execute("SELECT 1 FROM channel_control WHERE key='dispatch_halt'").fetchone():
                self._bump(connection, "dispatch_blocked_halt")
                return None
            # Cenário D/B: processo morreu durante o envio → resultado incerto, nunca reenviar.
            connection.execute(
                "UPDATE channel_outbox SET status='UNKNOWN',outcome='lease_expired_during_send',lease_owner=NULL,"
                "lease_expires_at=NULL,updated_at=? WHERE status='SENDING' AND lease_expires_at<=?",
                (iso(now), iso(now)),
            )
            while True:
                row = connection.execute(
                    "SELECT o.*, c.state AS current_state, c.session_id AS current_session, c.retired, "
                    "a.user_id AS assigned_user FROM channel_outbox AS o "
                    "LEFT JOIN conversations AS c ON c.id=o.conversation_id "
                    "LEFT JOIN panel_assignments AS a ON a.conversation_id=o.conversation_id "
                    "WHERE o.status IN ('PENDING','RETRY') AND o.next_attempt_at<=? AND NOT EXISTS ("
                    "SELECT 1 FROM channel_outbox AS p WHERE p.conversation_id=o.conversation_id "
                    "AND p.rowid<o.rowid AND p.status IN ('PENDING','RETRY','SENDING')) "
                    "ORDER BY o.rowid LIMIT 1", (iso(now),),
                ).fetchone()
                if row is None:
                    return None
                recipient = json.loads(row["recipient"])
                try:
                    opened: datetime | None = parse_iso(row["window_opened_at"])
                except ValueError:
                    opened = None
                # Reavaliada a CADA tentativa, imediatamente antes do envio (inclusive RETRY).
                decision = authorize_free_text(SendContext(
                    now=now, window_opened_at=opened, window_seconds=window_seconds,
                    required_state=row["required_state"], current_state=row["current_state"],
                    required_session=row["session_id"], current_session=row["current_session"],
                    retired=bool(row["retired"]),
                    has_recipient=bool(recipient.get("wa_id") or recipient.get("user_id")),
                    has_body=bool(row["body"]),
                ))
                reason = None if decision.allowed else decision.reason
                if reason is None and row["origin"] == "operator" and row["assigned_user"] != row["operator_user_id"]:
                    # Resposta humana só sai enquanto o autor continua responsável pela conversa.
                    reason = "operator_assignment_changed"
                ledger_hash = None
                if reason is None and guard is not None:
                    sender = Sender(wa_id=recipient.get("wa_id"), user_id=recipient.get("user_id"))
                    ledger_hash = guard.recipient_hash(sender)
                    verdict = guard.evaluate(
                        now=now, business_number_id=row["business_number_id"], recipient=sender,
                        counts=self._ledger_counts(connection, guard, ledger_hash, now),
                    )
                    if verdict.action == "wait":
                        # Sem efeito na outbox: aguarda nova autorização, janela de taxa ou nova execução.
                        self._bump(connection, f"dispatch_blocked_{verdict.reason}")
                        return None
                    if verdict.action == "suppress":
                        reason = verdict.reason
                if reason is not None:
                    self._bump(connection, f"send_suppressed_{reason}")
                    connection.execute(
                        "UPDATE channel_outbox SET status='SUPPRESSED',outcome=?,body='',recipient='{}',updated_at=? "
                        "WHERE id=?", (reason, iso(now), row["id"]),
                    )
                    continue
                connection.execute(
                    "UPDATE channel_outbox SET status='SENDING',lease_owner=?,lease_expires_at=?,attempts=attempts+1,"
                    "updated_at=? WHERE id=?",
                    (worker, iso(now + timedelta(seconds=lease_seconds)), iso(now), row["id"]),
                )
                if guard is not None and ledger_hash is not None:
                    # Tentativa reservada ANTES do transporte: conta para os limites mesmo se o processo cair.
                    connection.execute(
                        "INSERT INTO channel_send_ledger(outbox_id,authorization_id,run_id,recipient_hash,attempted_at) "
                        "VALUES(?,?,?,?,?)",
                        (row["id"], guard.authorization.authorization_id, guard.run_id, ledger_hash, iso(now)),
                    )
                self._bump(connection, "send_attempts")
                return ClaimedOutbound(
                    row["id"], row["business_number_id"], row["conversation_id"],
                    Sender(wa_id=recipient.get("wa_id"), user_id=recipient.get("user_id")),
                    row["body"], row["attempts"] + 1,
                )

    def finish_outbound(self, claimed: ClaimedOutbound, worker: str, outcome: SendOutcome, *,
                        max_attempts: int, backoff: Callable[[int], float]) -> str:
        now = self.clock()
        with self.repository.write_transaction() as connection:
            row = connection.execute("SELECT status,lease_owner,provider_message_id FROM channel_outbox WHERE id=?",
                                     (claimed.id,)).fetchone()
            owned = row is not None and row["status"] == "SENDING" and row["lease_owner"] == worker
            if isinstance(outcome, SendAccepted):
                if owned or (row is not None and row["status"] == "UNKNOWN"):
                    connection.execute(
                        "UPDATE channel_outbox SET status='ACCEPTED',outcome='accepted',provider_message_id=?,"
                        "lease_owner=NULL,lease_expires_at=NULL,body='',recipient='{}',updated_at=? WHERE id=?",
                        (outcome.provider_message_id, iso(now), claimed.id),
                    )
                    return "ACCEPTED"
                return str(row["status"]) if row else "MISSING"
            if not owned:
                return str(row["status"]) if row else "MISSING"
            if isinstance(outcome, SendRejected):
                if outcome.retryable and claimed.attempts < max_attempts:
                    connection.execute(
                        "UPDATE channel_outbox SET status='RETRY',outcome=?,lease_owner=NULL,lease_expires_at=NULL,"
                        "next_attempt_at=?,updated_at=? WHERE id=?",
                        (outcome.code, iso(now + timedelta(seconds=backoff(claimed.attempts))), iso(now), claimed.id),
                    )
                    return "RETRY"
                connection.execute(
                    "UPDATE channel_outbox SET status='FAILED',outcome=?,lease_owner=NULL,lease_expires_at=NULL,"
                    "body='',recipient='{}',updated_at=? WHERE id=?", (outcome.code, iso(now), claimed.id),
                )
                return "FAILED"
            connection.execute(
                "UPDATE channel_outbox SET status='UNKNOWN',outcome=?,lease_owner=NULL,lease_expires_at=NULL,"
                "updated_at=? WHERE id=?", (outcome.code, iso(now), claimed.id),
            )
            return "UNKNOWN"

    def requeue_unknown(self, operation_id: str, *, confirmation: str) -> None:
        """Decisão humana local: aceitar o risco de duplicar uma mensagem incerta."""
        if confirmation != f"RESEND_UNCERTAIN:{operation_id}":
            raise StorageError("Reenvio incerto exige confirmação RESEND_UNCERTAIN:OPERACAO.")
        with self.repository.write_transaction() as connection:
            cursor = connection.execute(
                "UPDATE channel_outbox SET status='RETRY',outcome='manual_resend_accepting_duplicate_risk',"
                "next_attempt_at=?,updated_at=? WHERE id=? AND status='UNKNOWN'",
                (iso(self.clock()), iso(self.clock()), operation_id),
            )
            if cursor.rowcount != 1:
                raise StorageError("Operação não está em estado incerto.")

    @staticmethod
    def _ledger_counts(connection: sqlite3.Connection, guard: HomologationGuard, recipient_hash: str,
                       now: datetime) -> GuardCounts:
        auth = guard.authorization.authorization_id

        def count(sql: str, *params: object) -> int:
            return int(connection.execute(sql, (auth, *params)).fetchone()[0])

        base = "SELECT count(*) FROM channel_send_ledger WHERE authorization_id=?"
        return GuardCounts(
            total=count(base),
            per_recipient=count(base + " AND recipient_hash=?", recipient_hash),
            per_run=count(base + " AND run_id=?", guard.run_id),
            last_minute=count(base + " AND attempted_at>=?", iso(now - timedelta(minutes=1))),
        )

    # ------------------------------------------------- métricas e interrupção
    @staticmethod
    def _bump(connection: sqlite3.Connection, name: str, amount: int = 1) -> None:
        connection.execute(
            "INSERT INTO channel_metrics(name,value) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET value=value+excluded.value",
            (name[:64], amount),
        )

    def bump(self, *names: str) -> None:
        """Contadores sem dados pessoais; falha de gravação nunca interrompe o fluxo."""
        try:
            with self.repository.write_transaction() as connection:
                for name in names:
                    self._bump(connection, name)
        except StorageError:
            pass

    def halt(self, reason: str, *, set_by: str) -> None:
        """Interrupção emergencial: bloqueia novas tentativas e retries; não altera a outbox."""
        reason = " ".join(str(reason).split())[:200]
        if not reason or not set_by:
            raise StorageError("Interrupção exige motivo e responsável.")
        with self.repository.write_transaction() as connection:
            connection.execute(
                "INSERT INTO channel_control(key,reason,set_by,set_at) VALUES('dispatch_halt',?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET reason=excluded.reason,set_by=excluded.set_by,set_at=excluded.set_at",
                (reason, set_by[:64], iso(self.clock())),
            )
            self.repository._audit(connection, "dispatch_halted", "channel", "operator", "committed")
            self._bump(connection, "dispatch_halts")

    def resume_dispatch(self, *, confirmation: str, set_by: str) -> None:
        if confirmation != "RESUME_DISPATCH":
            raise StorageError("Retomada de envios exige confirmação RESUME_DISPATCH.")
        with self.repository.write_transaction() as connection:
            if connection.execute("DELETE FROM channel_control WHERE key='dispatch_halt'").rowcount != 1:
                raise StorageError("Envios não estão interrompidos.")
            self.repository._audit(connection, "dispatch_resumed", "channel", "operator", "committed")

    def halt_status(self) -> dict[str, str] | None:
        with self.repository.write_transaction() as connection:
            row = connection.execute("SELECT reason,set_by,set_at FROM channel_control WHERE key='dispatch_halt'").fetchone()
            return dict(row) if row else None

    def metrics(self) -> dict[str, object]:
        """Contadores + agregados derivados. Nenhum telefone, texto ou ID externo."""
        with self.repository.write_transaction() as connection:
            counters = {row["name"]: row["value"] for row in connection.execute("SELECT name,value FROM channel_metrics")}
            def grouped(sql: str) -> dict[str, int]:
                return {str(row[0]): row[1] for row in connection.execute(sql)}
            return {
                "counters": counters,
                "inbox_status": grouped("SELECT status,count(*) FROM channel_inbox GROUP BY status"),
                "inbox_outcome": grouped("SELECT coalesce(outcome,'none'),count(*) FROM channel_inbox GROUP BY 1"),
                "outbox_status": grouped("SELECT status,count(*) FROM channel_outbox GROUP BY status"),
                "outbox_outcome": grouped("SELECT coalesce(outcome,'none'),count(*) FROM channel_outbox GROUP BY 1"),
                "delivery_status": grouped("SELECT coalesce(delivery_status,'none'),count(*) FROM channel_outbox GROUP BY 1"),
                "status_anomalies": connection.execute("SELECT coalesce(sum(status_anomalies),0) FROM channel_outbox").fetchone()[0],
                "human_transitions": grouped("SELECT operation,count(*) FROM audit WHERE operation LIKE 'state_%' GROUP BY 1"),
                "dispatch_halted": connection.execute("SELECT count(*) FROM channel_control").fetchone()[0] == 1,
            }

    def trace(self, inbox_id: int) -> dict[str, object]:
        """Fluxo de um evento por IDs técnicos (inbox → outbox → status), sem conteúdo."""
        with self.repository.write_transaction() as connection:
            inbox = connection.execute(
                "SELECT id,kind,status,outcome,attempts,received_at,updated_at FROM channel_inbox WHERE id=?", (inbox_id,),
            ).fetchone()
            if inbox is None:
                raise StorageError("Evento não encontrado.")
            outbox = [dict(row) for row in connection.execute(
                "SELECT id,status,outcome,attempts,delivery_status,status_anomalies,created_at,updated_at,"
                "provider_message_id IS NOT NULL AS has_provider_id FROM channel_outbox WHERE source_inbox_id=?", (inbox_id,))]
            statuses = []
            for item in outbox:
                provider = connection.execute("SELECT provider_message_id FROM channel_outbox WHERE id=?", (item["id"],)).fetchone()[0]
                if provider:
                    statuses += [dict(row) for row in connection.execute(
                        "SELECT id,status,outcome,received_at FROM channel_inbox WHERE kind='status' "
                        "AND json_extract(payload,'$.provider_message_id')=? ORDER BY id", (provider,))]
            ledger = [dict(row) for row in connection.execute(
                "SELECT authorization_id,run_id,attempted_at FROM channel_send_ledger WHERE outbox_id IN "
                "(SELECT id FROM channel_outbox WHERE source_inbox_id=?) ORDER BY id", (inbox_id,))]
            return {"inbox": dict(inbox), "outbox": outbox, "statuses": statuses, "send_ledger": ledger}

    # ------------------------------------------------------------- inspeção
    def counts(self) -> dict[str, dict[str, int]]:
        with self.repository.write_transaction() as connection:
            result: dict[str, dict[str, int]] = {"inbox": {}, "outbox": {}}
            for table, name in (("channel_inbox", "inbox"), ("channel_outbox", "outbox")):
                for row in connection.execute(f"SELECT status, count(*) AS n FROM {table} GROUP BY status"):
                    result[name][row["status"]] = row["n"]
            return result

    def outbox_rows(self) -> list[dict[str, object]]:
        """Inspeção local/testes: inclui corpo apenas enquanto não redigido."""
        with self.repository.write_transaction() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM channel_outbox ORDER BY rowid")]

    def inbox_rows(self) -> list[dict[str, object]]:
        with self.repository.write_transaction() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM channel_inbox ORDER BY id")]
