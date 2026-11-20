"""Snapshots otimistas e commits curtos; somente dados sintéticos locais.

Nenhuma conexão/transação fica aberta durante a geração. O commit compara versões
sob BEGIN IMMEDIATE; leitores recebem cópias completas de uma transação de leitura.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from hashlib import sha256
import fcntl
import json
import logging
import os
from pathlib import Path
import sqlite3
from time import monotonic, sleep
from uuid import uuid4

from pydantic import TypeAdapter

from rocha_reis_agent.domain.contracts import (
    AgentResult, CapacityExhausted, ConcurrentUpdate, ConversationState, SimulationError, StorageError, validate_identifier,
)
from rocha_reis_agent.domain.conversations import (
    Conversation, HistoryEntry, SimulationLimits, StateChange, utc_now,
)

logger = logging.getLogger("rocha_reis_agent")
APPLICATION_ID = 1381126963
SCHEMA_VERSION = 5
MIGRATIONS = Path(__file__).parent / "migrations"
RESULT = TypeAdapter(AgentResult)


@dataclass
class _Work:
    originals: dict[str, Conversation | None] = field(default_factory=dict)
    versions: dict[str, int | None] = field(default_factory=dict)
    staged: dict[str, Conversation] = field(default_factory=dict)
    # Efeitos de adaptadores do mesmo arquivo (inbox/outbox) gravados no MESMO commit.
    effects: list[Callable[[sqlite3.Connection], None]] = field(default_factory=list)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _token(value: str) -> str:
    return sha256(value.encode()).hexdigest()[:24]


class SQLiteConversationRepository:
    """Um arquivo identificado, sem fallback para memória e sem clientes reais.

    Cada instância pode atender threads; processos compartilham somente o arquivo.
    fault_hook é um ponto de injeção confiável para testes de escrita/rollback.
    """

    durable = True
    # Idempotência por conversa sem limite permanente: só os eventos recentes entram
    # no snapshot; eventos antigos são consultados diretamente (lookup_event).
    rolling_event_window = True
    # Ao atingir o limite de conversas ATIVAS, arquiva a mais ociosa elegível.
    archives_inactive = True

    def __init__(
        self, path: Path, *, limits: SimulationLimits | None = None,
        busy_timeout_ms: int = 1000, max_bytes: int = 64 * 1024 * 1024,
        retention_days: int = 7, audit_days: int = 30, audit_limit: int = 10000,
        archive_idle_hours: float = 24.0,
        fault_hook: Callable[[str], None] | None = None,
    ) -> None:
        self.path = Path(path).absolute()
        self.limits = limits or SimulationLimits()
        if (type(busy_timeout_ms) is not int or not 1 <= busy_timeout_ms <= 5000
            or type(max_bytes) is not int or max_bytes < 1024 * 1024
            or type(retention_days) is not int or not 1 <= retention_days <= 365
            or type(audit_days) is not int or not 1 <= audit_days <= 365
            or type(audit_limit) is not int or not 1 <= audit_limit <= 100000):
            raise StorageError("Configuração do armazenamento sintético inválida.")
        self.busy_timeout_ms = busy_timeout_ms
        self.max_bytes = max_bytes
        self.retention_days = retention_days
        self.audit_days = audit_days
        self.audit_limit = audit_limit
        if not isinstance(archive_idle_hours, (int, float)) or not 1 <= archive_idle_hours <= 24 * 365:
            raise StorageError("Configuração do armazenamento sintético inválida.")
        self.archive_idle_hours = float(archive_idle_hours)
        self.fault_hook = fault_hook or (lambda stage: None)
        self._ready = False
        self._work: ContextVar[_Work | None] = ContextVar(f"rr-work-{id(self)}", default=None)
        try:
            self._initialize()
            self._ready = True
        except StorageError:
            raise
        except (OSError, sqlite3.Error, ValueError):
            raise StorageError("Armazenamento sintético indisponível ou incompatível.") from None

    def _initialize(self) -> None:
        if not self.path.name.endswith(".synthetic.sqlite3"):
            raise StorageError("Use um arquivo local com sufixo .synthetic.sqlite3.")
        if any(part.is_symlink() for part in (self.path, *self.path.parents)):
            raise StorageError("Links simbólicos não são permitidos para o banco sintético.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Coordena apenas criação/migração no Linux. O processamento concorrente
        # usa versões e locks do SQLite, nunca este lock de inicialização.
        lock_path = str(self.path) + ".init.lock"
        existed = os.path.exists(lock_path)
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        # Detectar volume sem permissões POSIX (ex.: vfat) ANTES de criar o banco.
        status = os.fstat(descriptor)
        if status.st_mode & 0o777 != 0o600 or status.st_uid != os.geteuid():
            os.close(descriptor)
            if not existed:
                os.unlink(lock_path)
            raise StorageError("Volume sem permissões privadas 0600; use armazenamento local Linux privado.")
        try:
            deadline = monotonic() + self.busy_timeout_ms / 1000
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if monotonic() >= deadline:
                        raise ConcurrentUpdate("Inicialização do banco ocupada; tente novamente.") from None
                    sleep(min(0.005, max(0, deadline - monotonic())))
            self._initialize_locked()
        finally:
            os.close(descriptor)

    def _initialize_locked(self) -> None:
        fresh = False
        try:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
            fresh = True
        with self._connection() as connection:
            if not fresh:
                if connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
                    raise StorageError("Arquivo não reconhecido como banco sintético do projeto.")
                purpose = connection.execute("SELECT value FROM metadata WHERE key='purpose'").fetchone()
                if purpose is None or purpose[0] != "rocha-reis-synthetic-only":
                    raise StorageError("Banco fora do escopo sintético permitido.")
            connection.execute("BEGIN IMMEDIATE")
            try:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version > SCHEMA_VERSION or (not fresh and version < 1):
                    raise StorageError("Versão do armazenamento não suportada.")
                for number in range(version + 1, SCHEMA_VERSION + 1):
                    path = next(MIGRATIONS.glob(f"{number:03d}_*.sql"))
                    # executescript faria COMMIT implícito. Executar cada declaração
                    # separadamente preserva a transação de TODA a migração.
                    for statement in path.read_text(encoding="utf-8").split(";"):
                        if statement.strip():
                            connection.execute(statement)
                    connection.execute(f"PRAGMA user_version={number:d}")
                connection.execute(f"PRAGMA application_id={APPLICATION_ID:d}")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            if connection.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() != "wal":
                raise StorageError("O armazenamento local exige suporte a WAL.")
        os.chmod(self.path, 0o600)
        if self.path.stat().st_mode & 0o777 != 0o600 or self.path.stat().st_uid != os.geteuid():
            raise StorageError("Volume sem permissões privadas 0600; use armazenamento local Linux privado.")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True,
                                     timeout=self.busy_timeout_ms / 1000, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            if self._ready:
                self._ensure_scope(connection)
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA secure_delete=ON")
            page_size = connection.execute("PRAGMA page_size").fetchone()[0]
            connection.execute(f"PRAGMA max_page_count={self.max_bytes // page_size:d}")
            yield connection
        finally:
            connection.close()

    def _ensure_scope(self, connection: sqlite3.Connection) -> None:
        purpose = connection.execute("SELECT value FROM metadata WHERE key='purpose'").fetchone()
        if (connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
            or purpose is None or purpose[0] != "rocha-reis-synthetic-only"
            or self.path.stat().st_mode & 0o777 != 0o600):
            raise StorageError("Armazenamento fora do escopo sintético privado permitido.")

    @staticmethod
    def _translate(error: Exception) -> StorageError:
        if isinstance(error, sqlite3.OperationalError) and getattr(error, "sqlite_errorcode", 0) & 255 in (5, 6):
            return ConcurrentUpdate("Armazenamento ocupado; reenvie o mesmo evento.")
        return StorageError("Falha no armazenamento; nenhuma resposta desta tentativa foi confirmada.")

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._work.get() is not None:
            raise StorageError("Transações aninhadas não são suportadas.")
        work = _Work()
        token = self._work.set(work)
        try:
            yield
            if work.staged or work.effects:
                self._commit(work)
        except StorageError:
            self._failure()
            raise
        except SimulationError:
            raise
        except Exception as error:
            self._failure()
            raise self._translate(error) from None
        finally:
            self._work.reset(token)

    def _failure(self) -> None:
        logger.error("storage_operation_failed")
        # Falha de auditoria é explícita no log; não pode converter falha em sucesso.
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._audit(connection, "failure_rollback", "storage", "storage", "rolled_back")
                connection.commit()
        except (sqlite3.Error, OSError, StorageError):
            logger.error("storage_failure_audit_unavailable")

    def get(self, conversation_id: str) -> Conversation | None:
        validate_identifier(conversation_id)
        work = self._work.get()
        if work is None:
            raise StorageError("Leitura exige unidade de trabalho.")
        if conversation_id not in work.originals:
            with self._connection() as connection:
                connection.execute("BEGIN")
                try:
                    row = connection.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
                    value = self._load(connection, row) if row else None
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
            work.originals[conversation_id] = value
            work.versions[conversation_id] = row["version"] if row else None
        return deepcopy(work.originals[conversation_id])

    def _load(self, connection: sqlite3.Connection, row: sqlite3.Row) -> Conversation:
        key = row["id"]
        context = connection.execute("SELECT * FROM context WHERE conversation_id=?", (key,)).fetchone()
        if context is None:
            raise StorageError("Armazenamento inconsistente; recuperação manual necessária.")
        events = connection.execute(
            "SELECT * FROM (SELECT * FROM events WHERE conversation_id=? ORDER BY processed_at DESC, event_id DESC LIMIT ?) "
            "ORDER BY processed_at, event_id", (key, self.limits.max_events_per_session),
        ).fetchall()
        history = [HistoryEntry(
            entry["role"], entry["content"], datetime.fromisoformat(entry["recorded_at"]), entry["event_id"],
            tuple(json.loads(entry["fact_references"])), entry["prompt_version"],
        ) for entry in connection.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY ordinal", (key,))]
        changes = [StateChange(
            ConversationState(entry["previous"]), ConversationState(entry["current"]),
            entry["action"], entry["operator_id"], datetime.fromisoformat(entry["recorded_at"]),
        ) for entry in connection.execute("SELECT * FROM transitions WHERE conversation_id=? ORDER BY ordinal", (key,))]
        results = {event["event_id"]: RESULT.validate_json(event["result"]) for event in events}
        value = Conversation(
            key, row["session_id"], ConversationState(row["state"]), bool(row["closed"]),
            history, changes, tuple(json.loads(context["services"])), context["age"],
            {event["event_id"]: event["digest"] for event in events}, results,
            archived=bool(row["archived"]),
        )
        self._validate(value)
        return value

    def save(self, conversation: Conversation) -> None:
        work = self._work.get()
        if work is None or conversation.conversation_id not in work.originals:
            raise StorageError("Escrita exige leitura anterior na unidade de trabalho.")
        self._validate(conversation)
        work.staged[conversation.conversation_id] = deepcopy(conversation)

    def stage_effect(self, effect: Callable[[sqlite3.Connection], None]) -> None:
        """Uso interno da camada de persistência: gravar junto do commit da conversa.

        Se o commit falhar ou houver conflito, o efeito é descartado com a tentativa.
        """
        work = self._work.get()
        if work is None:
            raise StorageError("Efeito transacional exige unidade de trabalho.")
        work.effects.append(effect)

    @contextmanager
    def write_transaction(self) -> Iterator[sqlite3.Connection]:
        """Transação curta independente (BEGIN IMMEDIATE) para adaptadores do mesmo arquivo."""
        if self._work.get() is not None:
            raise StorageError("Transações aninhadas não são suportadas.")
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    yield connection
                    self.fault_hook("before_channel_commit")
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
        except SimulationError:
            raise
        except (sqlite3.Error, OSError) as error:
            self._failure()
            raise self._translate(error) from None

    def _validate(self, value: Conversation) -> None:
        validate_identifier(value.conversation_id)
        validate_identifier(value.session_id)
        if (not isinstance(value.state, ConversationState) or type(value.closed) is not bool
            or type(value.context_age) is not int or value.context_age < 0
            or len(value.service_context) > 20
            or type(value.archived) is not bool
            or set(value.processed_events) != set(value.event_results)):
            raise StorageError("Registro de conversa inválido ou limite persistente atingido.")
        for service in value.service_context:
            validate_identifier(service)
        for key, digest in value.processed_events.items():
            validate_identifier(key)
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise StorageError("Registro de evento inválido.")
            result = RESULT.validate_python(asdict(value.event_results[key]))
            if result.event_id != key or result.conversation_id != value.conversation_id or result.duplicate:
                raise StorageError("Resultado não corresponde ao evento.")
        for entry in value.history:
            if (entry.role not in ("client", "assistant", "operator") or not entry.text
                or len(entry.text) > 8192 or entry.timestamp.utcoffset() is None):
                raise StorageError("Registro de mensagem inválido.")
        for change in value.transitions:
            if (not isinstance(change.previous, ConversationState)
                or not isinstance(change.current, ConversationState) or change.timestamp.utcoffset() is None):
                raise StorageError("Registro de transição inválido.")

    def count(self) -> int:
        try:
            with self._connection() as connection:
                return connection.execute("SELECT count(*) FROM conversations WHERE retired=0 AND archived=0").fetchone()[0]
        except (sqlite3.Error, OSError) as error:
            raise self._translate(error) from None

    def _commit(self, work: _Work) -> None:
        if sum(p.stat().st_size for p in (self.path, Path(str(self.path) + "-wal")) if p.exists()) >= self.max_bytes:
            raise StorageError("Limite do banco sintético atingido; execute manutenção local.")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for key, value in work.staged.items():
                    row = connection.execute("SELECT version,retired FROM conversations WHERE id=?", (key,)).fetchone()
                    actual = row["version"] if row else None
                    if actual != work.versions[key]:
                        raise ConcurrentUpdate("Conversa alterada durante processamento; tente novamente.")
                    original = work.originals[key]
                    if value == original:
                        self._audit(connection, "deduplication", key, "client", "duplicate")
                        continue
                    if row and row["retired"]:
                        raise StorageError("Conversa sintética excluída; use outro identificador fictício.")
                    self._write(connection, value, original, actual)
                for effect in work.effects:
                    effect(connection)
                self.fault_hook("before_commit")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def _write(self, connection: sqlite3.Connection, value: Conversation,
               original: Conversation | None, version: int | None) -> None:
        key = value.conversation_id
        now = utc_now().isoformat()
        if original is None:
            self._ensure_capacity(connection, key)
            connection.execute("INSERT INTO conversations(id,session_id,client_id,state,closed,archived,version,created_at,updated_at) VALUES(?,?,?,?,?,?,1,?,?)",
                               (key, value.session_id, key, value.state.value, int(value.closed), int(value.archived), now, now))
            self._audit(connection, "conversation_created", key, "client", "committed")
        else:
            # Reset elimina conteúdo, mas conserva idempotência no adaptador durável.
            if value.session_id != original.session_id:
                value.processed_events = {**original.processed_events, **value.processed_events}
                value.event_results = {**original.event_results, **value.event_results}
            if not original.processed_events.keys() <= value.processed_events.keys():
                raise StorageError("Eventos duráveis não podem ser descartados por edição.")
            if original.archived and not value.archived:
                self._ensure_capacity(connection, key)
                self._audit(connection, "conversation_reactivated", key, "client", "committed")
            connection.execute("UPDATE conversations SET session_id=?,state=?,closed=?,archived=?,version=?,updated_at=? WHERE id=?",
                               (value.session_id, value.state.value, int(value.closed), int(value.archived), version + 1, now, key))
        self.fault_hook("after_conversation")
        connection.execute("INSERT INTO context VALUES(?,?,?) ON CONFLICT(conversation_id) DO UPDATE SET services=excluded.services,age=excluded.age",
                           (key, _json(value.service_context), value.context_age))
        for event_id, digest in value.processed_events.items():
            existing = connection.execute("SELECT digest FROM events WHERE conversation_id=? AND event_id=?", (key, event_id)).fetchone()
            if existing:
                if existing[0] != digest:
                    raise StorageError("ID de evento reutilizado com conteúdo diferente.")
                continue
            result = RESULT.dump_json(value.event_results[event_id]).decode()
            connection.execute("INSERT INTO events VALUES(?,?,?,'client_message','COMPLETED',?,?,?)",
                               (key, event_id, value.session_id, digest, result, now))
            self._audit(connection, "message_processed", key, "client", "committed")
        self.fault_hook("after_events")
        connection.execute("DELETE FROM messages WHERE conversation_id=?", (key,))
        for ordinal, entry in enumerate(value.history[-self.limits.max_history_entries:]):
            connection.execute("INSERT INTO messages(conversation_id,ordinal,event_id,role,content,recorded_at,fact_references,prompt_version) VALUES(?,?,?,?,?,?,?,?)",
                               (key, ordinal, entry.event_id, entry.role, entry.text, entry.timestamp.isoformat(), _json(entry.fact_references), entry.prompt_version))
        old_changes = original.transitions if original else []
        for change in value.transitions:
            if change not in old_changes:
                self._audit(connection, "state_" + change.action, key,
                            "operator" if change.operator_id else "client", "committed")
        if original and value.session_id != original.session_id:
            self._audit(connection, "session_reset", key, "operator", "committed")
        if original and value.history and value.history[-1].role == "operator" and value.history != original.history:
            self._audit(connection, "operator_reply", key, "operator", "committed")
        connection.execute("DELETE FROM transitions WHERE conversation_id=?", (key,))
        for ordinal, change in enumerate(value.transitions[-self.limits.max_history_entries:]):
            connection.execute("INSERT INTO transitions(conversation_id,ordinal,previous,current,action,operator_id,recorded_at) VALUES(?,?,?,?,?,?,?)",
                               (key, ordinal, change.previous.value, change.current.value, change.action, change.operator_id, change.timestamp.isoformat()))
        self.fault_hook("after_history")

    def _ensure_capacity(self, connection: sqlite3.Connection, key: str) -> None:
        """Limite de conversas ATIVAS. Se cheio, arquiva a mais ociosa elegível no mesmo commit.

        Elegível: não excluída, não arquivada, ociosa há archive_idle_hours, sem atendimento
        humano em andamento (WAITING_HUMAN/HUMAN_ACTIVE abertos) e sem item pendente,
        em envio ou incerto no canal. Arquivar não apaga dados nem muda estado.
        """
        active = connection.execute("SELECT count(*) FROM conversations WHERE retired=0 AND archived=0").fetchone()[0]
        if active < self.limits.max_conversations:
            return
        cutoff = (utc_now() - timedelta(hours=self.archive_idle_hours)).isoformat()
        row = connection.execute(
            "SELECT c.id FROM conversations AS c WHERE c.retired=0 AND c.archived=0 AND c.id<>? AND c.updated_at<? "
            "AND NOT (c.closed=0 AND c.state IN ('WAITING_HUMAN','HUMAN_ACTIVE')) "
            "AND NOT EXISTS (SELECT 1 FROM channel_outbox AS o WHERE o.conversation_id=c.id "
            "AND o.status IN ('PENDING','RETRY','SENDING','UNKNOWN')) "
            "AND NOT EXISTS (SELECT 1 FROM channel_inbox AS i WHERE i.conversation_id=c.id "
            "AND i.status IN ('PENDING','PROCESSING')) ORDER BY c.updated_at LIMIT 1", (key, cutoff),
        ).fetchone()
        if row is None:
            raise CapacityExhausted("Limite de conversas ativas atingido; nenhuma conversa ociosa elegível para arquivamento.")
        # Versão incrementada: snapshot concorrente dessa conversa será invalidado e relido.
        connection.execute("UPDATE conversations SET archived=1,version=version+1 WHERE id=?", (row["id"],))
        self._audit(connection, "conversation_archived", row["id"], "storage", "committed")

    def lookup_event(self, conversation_id: str, event_id: str) -> tuple[str, AgentResult] | None:
        """Idempotência além da janela do snapshot: consulta direta, dentro da unidade de trabalho."""
        validate_identifier(conversation_id)
        validate_identifier(event_id)
        if self._work.get() is None:
            raise StorageError("Leitura exige unidade de trabalho.")
        with self._connection() as connection:
            row = connection.execute("SELECT digest,result FROM events WHERE conversation_id=? AND event_id=?",
                                     (conversation_id, event_id)).fetchone()
        return (row["digest"], RESULT.validate_json(row["result"])) if row else None

    def _audit(self, connection: sqlite3.Connection, operation: str, entity: str,
               origin: str, outcome: str) -> None:
        connection.execute("INSERT INTO audit(operation,entity,origin,outcome,recorded_at,correlation) VALUES(?,?,?,?,?,?)",
                           (operation, _token(entity), origin, outcome, utc_now().isoformat(), uuid4().hex))
        connection.execute("DELETE FROM audit WHERE recorded_at < ?", ((utc_now() - timedelta(days=self.audit_days)).isoformat(),))
        connection.execute("DELETE FROM audit WHERE id IN (SELECT id FROM audit ORDER BY id DESC LIMIT -1 OFFSET ?)", (self.audit_limit,))

    def audit(self, conversation_id: str | None = None) -> list[dict[str, object]]:
        try:
            with self._connection() as connection:
                if conversation_id is None:
                    rows = connection.execute("SELECT * FROM audit ORDER BY id").fetchall()
                else:
                    validate_identifier(conversation_id)
                    rows = connection.execute("SELECT * FROM audit WHERE entity=? ORDER BY id", (_token(conversation_id),)).fetchall()
                return [dict(row) for row in rows]
        except (sqlite3.Error, OSError) as error:
            raise self._translate(error) from None

    def bind_knowledge(self, serialized: str) -> None:
        """Impede reinício com outro catálogo sem migração confiável deliberada."""
        fingerprint = sha256(serialized.encode()).hexdigest()
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                current = connection.execute("SELECT value FROM metadata WHERE key='knowledge_hash'").fetchone()
                if current is not None and current[0] != fingerprint:
                    raise StorageError("Catálogo incompatível com o banco; use outro banco sintético.")
                connection.execute("INSERT OR IGNORE INTO metadata VALUES('knowledge_hash',?)", (fingerprint,))
                connection.commit()
        except (sqlite3.Error, OSError) as error:
            raise self._translate(error) from None

    def retire(self, conversation_id: str, *, confirmation: str) -> None:
        validate_identifier(conversation_id)
        if confirmation != f"DELETE_SYNTHETIC:{conversation_id}":
            raise StorageError("Exclusão exige confirmação DELETE_SYNTHETIC:CLIENTE.")
        self._retire_keys((conversation_id,))

    def purge_expired(self, *, confirmation: str) -> int:
        """Retenção: anonimiza conteúdo de conversas inativas SEM bloquear o cliente.

        Preserva chaves de idempotência (eventos/inbox), estado da conversa e auditoria.
        Não toca conversas com atendimento humano em andamento nem com envio pendente,
        em curso ou incerto. A conversa fica arquivada e é reativada por nova mensagem.
        Exclusão definitiva por pedido explícito continua em retire().
        """
        if confirmation != "PURGE_SYNTHETIC":
            raise StorageError("Retenção exige confirmação PURGE_SYNTHETIC.")
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    cutoff = (utc_now() - timedelta(days=self.retention_days)).isoformat()
                    rows = connection.execute(
                        "SELECT c.id FROM conversations AS c WHERE c.retired=0 AND c.updated_at<? "
                        "AND (c.anonymized_at IS NULL OR c.updated_at>c.anonymized_at) "
                        "AND NOT (c.closed=0 AND c.state IN ('WAITING_HUMAN','HUMAN_ACTIVE')) "
                        "AND NOT EXISTS (SELECT 1 FROM channel_outbox AS o WHERE o.conversation_id=c.id "
                        "AND o.status IN ('PENDING','RETRY','SENDING','UNKNOWN')) "
                        "AND NOT EXISTS (SELECT 1 FROM channel_inbox AS i WHERE i.conversation_id=c.id "
                        "AND i.status IN ('PENDING','PROCESSING')) ORDER BY c.id", (cutoff,),
                    ).fetchall()
                    now = utc_now().isoformat()
                    for row in rows:
                        key = row["id"]
                        connection.execute("DELETE FROM messages WHERE conversation_id=?", (key,))
                        connection.execute("DELETE FROM transitions WHERE conversation_id=?", (key,))
                        connection.execute("UPDATE context SET services='[]',age=0 WHERE conversation_id=?", (key,))
                        for event in connection.execute("SELECT event_id,result FROM events WHERE conversation_id=?", (key,)).fetchall():
                            result = RESULT.validate_json(event["result"])
                            minimal = AgentResult(key, result.event_id, result.state)
                            connection.execute("UPDATE events SET result=? WHERE conversation_id=? AND event_id=?",
                                               (RESULT.dump_json(minimal).decode(), key, event["event_id"]))
                        connection.execute("UPDATE channel_inbox SET payload='{}' WHERE conversation_id=? AND status IN ('DONE','FAILED')", (key,))
                        connection.execute("UPDATE channel_outbox SET body='',recipient='{}' WHERE conversation_id=?", (key,))
                        # updated_at preservado (não é atividade do cliente); anonymized_at marca o ciclo.
                        connection.execute("UPDATE conversations SET archived=1,anonymized_at=?,version=version+1 WHERE id=?", (now, key))
                        self._audit(connection, "retention_anonymized", key, "operator", "committed")
                    self.fault_hook("before_retention_commit")
                    connection.commit()
                    return len(rows)
                except BaseException:
                    connection.rollback()
                    raise
        except StorageError:
            raise
        except (sqlite3.Error, OSError, ValueError) as error:
            self._failure()
            raise self._translate(error) from None

    def _retire_keys(self, keys: tuple[str, ...] | None) -> int:
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    if keys is None:
                        rows = connection.execute("SELECT id FROM conversations WHERE retired=0 AND updated_at < ? ORDER BY id",
                                                  ((utc_now() - timedelta(days=self.retention_days)).isoformat(),)).fetchall()
                        selected = tuple(row[0] for row in rows)
                    else:
                        selected = keys
                    for key in selected:
                        row = connection.execute("SELECT retired FROM conversations WHERE id=?", (key,)).fetchone()
                        if row is None:
                            raise StorageError("Conversa não encontrada.")
                        connection.execute("DELETE FROM messages WHERE conversation_id=?", (key,))
                        connection.execute("DELETE FROM transitions WHERE conversation_id=?", (key,))
                        connection.execute("UPDATE context SET services='[]',age=0 WHERE conversation_id=?", (key,))
                        for event in connection.execute("SELECT event_id,result FROM events WHERE conversation_id=?", (key,)).fetchall():
                            result = RESULT.validate_json(event["result"])
                            minimal = AgentResult(key, result.event_id, result.state)
                            connection.execute("UPDATE events SET result=? WHERE conversation_id=? AND event_id=?",
                                               (RESULT.dump_json(minimal).decode(), key, event["event_id"]))
                        # Canal: remover texto e endereço ainda guardados para essa conversa.
                        connection.execute("UPDATE channel_inbox SET payload='{}' WHERE conversation_id=?", (key,))
                        connection.execute("UPDATE channel_outbox SET body='',recipient='{}' WHERE conversation_id=? AND status NOT IN ('SENDING','UNKNOWN')", (key,))
                        connection.execute("UPDATE channel_outbox SET status='SUPPRESSED',outcome='conversation_deleted',body='',recipient='{}',lease_owner=NULL,lease_expires_at=NULL WHERE conversation_id=? AND status IN ('PENDING','RETRY')", (key,))
                        connection.execute("UPDATE conversations SET state='RESOLVED',closed=1,retired=1,version=version+1,updated_at=? WHERE id=?", (utc_now().isoformat(), key))
                        self._audit(connection, "synthetic_deleted", key, "operator", "retired")
                    self.fault_hook("before_retention_commit")
                    connection.commit()
                    return len(selected)
                except BaseException:
                    connection.rollback()
                    raise
        except StorageError:
            raise
        except (sqlite3.Error, OSError, ValueError) as error:
            self._failure()
            raise self._translate(error) from None
