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
from rocha_reis_agent.domain.contracts import ConversationState, StorageError
from rocha_reis_agent.domain.conversations import utc_now
from rocha_reis_agent.persistence.sqlite import SQLiteConversationRepository


class LeaseLost(StorageError):
    """O item foi reivindicado por outro worker; a tentativa inteira é descartada."""


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

    def apply_status(self, item: ClaimedInbound, worker: str) -> str:
        """Status atualiza a outbox; nunca gera resposta. Também reconcilia UNKNOWN."""
        now = iso(self.clock())
        payload = item.payload
        provider_id = str(payload["provider_message_id"])
        status = str(payload["status"])
        callback = payload.get("callback")
        with self.repository.write_transaction() as connection:
            row = connection.execute("SELECT * FROM channel_outbox WHERE provider_message_id=?", (provider_id,)).fetchone()
            if row is None and isinstance(callback, str):
                row = connection.execute(
                    "SELECT * FROM channel_outbox WHERE id=? AND provider_message_id IS NULL", (callback,)
                ).fetchone()
            if row is None:
                outcome = "status_unmatched"
            else:
                if row["status"] in ("SENDING", "UNKNOWN"):
                    # A Meta tem a mensagem: o ID no status prova aceitação da requisição.
                    connection.execute(
                        "UPDATE channel_outbox SET status='ACCEPTED',outcome='reconciled_by_status',"
                        "provider_message_id=?,lease_owner=NULL,lease_expires_at=NULL,body='',recipient='{}',"
                        "updated_at=? WHERE id=?", (provider_id, now, row["id"]),
                    )
                rank = {None: 0, "sent": 1, "delivered": 2, "read": 3, "failed": 4}
                if rank[status] > rank[row["delivery_status"]]:
                    connection.execute("UPDATE channel_outbox SET delivery_status=?,updated_at=? WHERE id=?",
                                       (status, now, row["id"]))
                outcome = f"status_{status}"
            self._complete(connection, item.id, worker, "DONE", outcome, now, redact=False)
        return outcome

    # ------------------------------------------------------------------ envio
    def claim_outbound(self, worker: str, *, lease_seconds: float, window_seconds: float) -> ClaimedOutbound | None:
        now = self.clock()
        with self.repository.write_transaction() as connection:
            # Cenário D/B: processo morreu durante o envio → resultado incerto, nunca reenviar.
            connection.execute(
                "UPDATE channel_outbox SET status='UNKNOWN',outcome='lease_expired_during_send',lease_owner=NULL,"
                "lease_expires_at=NULL,updated_at=? WHERE status='SENDING' AND lease_expires_at<=?",
                (iso(now), iso(now)),
            )
            while True:
                row = connection.execute(
                    "SELECT o.*, c.state AS current_state, c.session_id AS current_session, c.retired "
                    "FROM channel_outbox AS o JOIN conversations AS c ON c.id=o.conversation_id "
                    "WHERE o.status IN ('PENDING','RETRY') AND o.next_attempt_at<=? AND NOT EXISTS ("
                    "SELECT 1 FROM channel_outbox AS p WHERE p.conversation_id=o.conversation_id "
                    "AND p.rowid<o.rowid AND p.status IN ('PENDING','RETRY','SENDING')) "
                    "ORDER BY o.rowid LIMIT 1", (iso(now),),
                ).fetchone()
                if row is None:
                    return None
                reason = None
                if row["retired"] or row["current_state"] != row["required_state"] or row["current_session"] != row["session_id"]:
                    # Corrida com operador/reset: resposta pertence a um estado que não vale mais.
                    reason = "conversation_state_changed"
                elif now - parse_iso(row["window_opened_at"]) > timedelta(seconds=window_seconds):
                    # Fora da janela de atendimento exige template aprovado (não implementado).
                    reason = "outside_service_window"
                if reason is not None:
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
                recipient = json.loads(row["recipient"])
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
