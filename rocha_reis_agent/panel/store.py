"""Persistência do painel no MESMO SQLite (fonte única de verdade das conversas).

Toda consulta de conversa aplica a regra de visibilidade no SQL:
- ADMIN: todas as conversas não excluídas;
- OPERATOR: fila (WAITING_HUMAN, aberta, sem responsável) + conversas atribuídas a ele.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import sqlite3
from typing import Any
from uuid import uuid4

from rocha_reis_agent.application.handoff import VerifiedOperator
from rocha_reis_agent.domain.contracts import StorageError
from rocha_reis_agent.domain.conversations import utc_now
from rocha_reis_agent.panel.security import PanelError, csrf_for, new_token, token_hash
from rocha_reis_agent.persistence.sqlite import SQLiteConversationRepository
from rocha_reis_agent.persistence.sqlite_channel import iso, parse_iso

SETTINGS_DEFAULTS = {"operators_can_transfer": 1, "operators_can_resume": 0}
FILTERS = {
    "all": "1=1",
    "waiting": "c.state='WAITING_HUMAN' AND c.closed=0",
    "human_active": "c.state='HUMAN_ACTIVE' AND c.closed=0",
    "auto": "c.state='AUTO' AND c.closed=0",
    "resolved": "(c.state='RESOLVED' OR c.closed=1)",
    "mine": "a.user_id=:me",
}


@dataclass(frozen=True, slots=True)
class PanelUser:
    id: str
    login: str
    display_name: str
    role: str
    active: bool
    must_change_password: bool

    @property
    def operator(self) -> VerifiedOperator:
        return VerifiedOperator(self.id, self.role)  # type: ignore[arg-type]

    def public(self) -> dict[str, object]:
        return {"id": self.id, "login": self.login, "display_name": self.display_name, "role": self.role,
                "active": self.active, "must_change_password": self.must_change_password}


@dataclass(frozen=True, slots=True)
class PanelSession:
    token_hash: str
    user: PanelUser
    csrf_token: str


def _user(row: sqlite3.Row) -> PanelUser:
    return PanelUser(row["id"], row["login"], row["display_name"], row["role"], bool(row["active"]),
                     bool(row["must_change_password"]))


def visibility(user: PanelUser) -> str:
    if user.role == "ADMIN":
        return "c.retired=0"
    return ("c.retired=0 AND ((c.state='WAITING_HUMAN' AND c.closed=0 AND a.user_id IS NULL) "
            "OR a.user_id=:me)")


def delivery_label(row: sqlite3.Row | None) -> dict[str, object] | None:
    """Rótulo honesto: criado na outbox ≠ enviado; aceito ≠ entregue."""
    if row is None:
        return None
    status, delivery = row["status"], row["delivery_status"]
    if status == "ACCEPTED":
        key = delivery or "accepted"
    else:
        key = {"PENDING": "pending", "RETRY": "pending", "SENDING": "sending", "FAILED": "failed",
               "UNKNOWN": "unknown", "SUPPRESSED": "cancelled"}[status]
    return {"key": key, "outcome": row["outcome"] if status in ("FAILED", "SUPPRESSED", "UNKNOWN") else None}


class SQLitePanelStore:
    def __init__(self, repository: SQLiteConversationRepository, *, clock: Callable[[], datetime] = utc_now) -> None:
        self.repository = repository
        self.clock = clock

    def _tx(self):  # type: ignore[no-untyped-def]
        return self.repository.write_transaction()

    def _read(self):  # type: ignore[no-untyped-def]
        return self.repository.read()

    # ------------------------------------------------------------------ usuários
    def count_users(self) -> int:
        with self._read() as connection:
            return connection.execute("SELECT count(*) FROM panel_users").fetchone()[0]

    def create_user(self, *, login: str, display_name: str, password_hash: str, role: str,
                    must_change_password: bool, actor_id: str | None) -> PanelUser:
        user_id = uuid4().hex
        now = iso(self.clock())
        try:
            with self._tx() as connection:
                connection.execute(
                    "INSERT INTO panel_users(id,login,display_name,password_hash,role,active,must_change_password,"
                    "created_at,updated_at) VALUES(?,?,?,?,?,1,?,?,?)",
                    (user_id, login, display_name, password_hash, role, int(must_change_password), now, now),
                )
                self._audit(connection, actor_id or user_id, "user_created", "user", user_id, "success")
        except StorageError:
            if self.user_by_login(login) is not None:
                raise PanelError(409, "Já existe um funcionário com esse login.") from None
            raise
        return PanelUser(user_id, login, display_name, role, True, must_change_password)

    def user_by_login(self, login: str) -> tuple[PanelUser, str] | None:
        with self._read() as connection:
            row = connection.execute("SELECT * FROM panel_users WHERE login=?", (login,)).fetchone()
        return (_user(row), row["password_hash"]) if row else None

    def user(self, user_id: str) -> PanelUser | None:
        with self._read() as connection:
            row = connection.execute("SELECT * FROM panel_users WHERE id=?", (user_id,)).fetchone()
        return _user(row) if row else None

    def users(self) -> list[dict[str, object]]:
        with self._read() as connection:
            return [{**_user(row).public(), "created_at": row["created_at"], "last_login_at": row["last_login_at"]}
                    for row in connection.execute("SELECT * FROM panel_users ORDER BY display_name, login")]

    def active_operators(self) -> list[dict[str, object]]:
        with self._read() as connection:
            return [{"id": r["id"], "display_name": r["display_name"], "role": r["role"]} for r in connection.execute(
                "SELECT id,display_name,role FROM panel_users WHERE active=1 ORDER BY display_name")]

    def set_active(self, user_id: str, active: bool, *, actor_id: str) -> None:
        with self._tx() as connection:
            row = connection.execute("SELECT role,active FROM panel_users WHERE id=?", (user_id,)).fetchone()
            if row is None:
                raise PanelError(404, "Funcionário não encontrado.")
            if not active:
                if user_id == actor_id:
                    raise PanelError(409, "Você não pode desativar a própria conta.")
                admins = connection.execute("SELECT count(*) FROM panel_users WHERE role='ADMIN' AND active=1 AND id<>?",
                                            (user_id,)).fetchone()[0]
                if row["role"] == "ADMIN" and admins == 0:
                    raise PanelError(409, "Não é possível desativar o último administrador ativo.")
                # Desativar revoga imediatamente todas as sessões e libera responsabilidades.
                connection.execute("UPDATE panel_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                                   (iso(self.clock()), user_id))
            connection.execute("UPDATE panel_users SET active=?,updated_at=? WHERE id=?",
                               (int(active), iso(self.clock()), user_id))
            self._audit(connection, actor_id, "user_activated" if active else "user_deactivated", "user", user_id, "success")

    def set_password(self, user_id: str, password_hash: str, *, must_change: bool, actor_id: str,
                     keep_session: str | None = None) -> None:
        with self._tx() as connection:
            connection.execute("UPDATE panel_users SET password_hash=?,must_change_password=?,updated_at=? WHERE id=?",
                               (password_hash, int(must_change), iso(self.clock()), user_id))
            connection.execute("UPDATE panel_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL "
                               "AND token_hash IS NOT ?", (iso(self.clock()), user_id, keep_session))
            self._audit(connection, actor_id, "password_changed" if actor_id == user_id else "password_reset",
                        "user", user_id, "success")

    # ------------------------------------------------------------------- sessões
    def create_session(self, user_id: str, *, absolute_hours: int) -> tuple[str, str]:
        token = new_token()
        csrf = csrf_for(token)
        now = self.clock()
        with self._tx() as connection:
            connection.execute(
                "INSERT INTO panel_sessions(token_hash,user_id,csrf_token,created_at,last_seen_at,expires_at) "
                "VALUES(?,?,?,?,?,?)",
                # Só hashes: token de sessão e verificador do CSRF (o CSRF em si é derivado do token).
                (token_hash(token), user_id, token_hash(csrf), iso(now), iso(now), iso(now + timedelta(hours=absolute_hours))),
            )
            connection.execute("UPDATE panel_users SET last_login_at=? WHERE id=?", (iso(now), user_id))
            self._audit(connection, user_id, "login", "session", None, "success")
        return token, csrf

    def session(self, token: str | None, *, idle_minutes: int, touch: bool = True) -> PanelSession | None:
        """Sessão válida: não revogada, não expirada (absoluta e ociosa) e usuário ativo.

        touch=False (stream SSE) não renova a ociosidade: só ações do usuário a renovam.
        """
        if not token or len(token) > 128:
            return None
        hashed = token_hash(token)
        now = self.clock()
        with self._tx() as connection:
            row = connection.execute(
                "SELECT s.*, u.id AS uid, u.login, u.display_name, u.role, u.active, u.must_change_password "
                "FROM panel_sessions AS s JOIN panel_users AS u ON u.id=s.user_id WHERE s.token_hash=?", (hashed,),
            ).fetchone()
            if (row is None or row["revoked_at"] is not None or not row["active"]
                    or parse_iso(row["expires_at"]) <= now
                    or now - parse_iso(row["last_seen_at"]) > timedelta(minutes=idle_minutes)):
                return None
            if touch and now - parse_iso(row["last_seen_at"]) > timedelta(seconds=30):
                connection.execute("UPDATE panel_sessions SET last_seen_at=? WHERE token_hash=?", (iso(now), hashed))
            user = PanelUser(row["uid"], row["login"], row["display_name"], row["role"], True,
                             bool(row["must_change_password"]))
            return PanelSession(hashed, user, csrf_for(token))

    def revoke_session(self, hashed: str, *, actor_id: str) -> None:
        with self._tx() as connection:
            connection.execute("UPDATE panel_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                               (iso(self.clock()), hashed))
            self._audit(connection, actor_id, "logout", "session", None, "success")

    # ---------------------------------------------------------- força bruta
    def recent_failures(self, keys: list[str], *, window_minutes: int) -> int:
        since = iso(self.clock() - timedelta(minutes=window_minutes))
        with self._tx() as connection:
            return max(connection.execute("SELECT count(*) FROM panel_login_attempts WHERE key_hash=? AND attempted_at>=?",
                                          (key, since)).fetchone()[0] for key in keys)

    def record_failure(self, keys: list[str], *, actor_id: str | None, outcome: str) -> None:
        with self._tx() as connection:
            for key in keys:
                connection.execute("INSERT INTO panel_login_attempts(key_hash,attempted_at) VALUES(?,?)",
                                   (key, iso(self.clock())))
            connection.execute("DELETE FROM panel_login_attempts WHERE attempted_at<?",
                               (iso(self.clock() - timedelta(days=1)),))
            self._audit(connection, actor_id, "login_failed", "session", None, outcome)

    def clear_failures(self, key: str) -> None:
        with self._tx() as connection:
            connection.execute("DELETE FROM panel_login_attempts WHERE key_hash=?", (key,))

    # ----------------------------------------------------------------- auditoria
    def _audit(self, connection: sqlite3.Connection, actor_id: str | None, action: str, entity_type: str,
               entity_id: str | None, outcome: str, operation_id: str | None = None) -> None:
        connection.execute(
            "INSERT INTO panel_audit(operation_id,actor_user_id,action,entity_type,entity_id,outcome,at) "
            "VALUES(?,?,?,?,?,?,?)",
            (operation_id or uuid4().hex, actor_id, action, entity_type, entity_id, outcome, iso(self.clock())),
        )

    def audit(self, actor_id: str | None, action: str, entity_type: str, entity_id: str | None, outcome: str) -> None:
        with self._tx() as connection:
            self._audit(connection, actor_id, action, entity_type, entity_id, outcome)

    def stage_audit(self, actor_id: str, action: str, entity_type: str, entity_id: str | None,
                    operation_id: str | None = None) -> None:
        """Auditoria gravada no MESMO commit da alteração de conversa."""
        self.repository.stage_effect(
            lambda connection: self._audit(connection, actor_id, action, entity_type, entity_id, "success", operation_id))

    def audit_entries(self, *, limit: int = 100, before: int | None = None) -> list[dict[str, object]]:
        with self._read() as connection:
            rows = connection.execute(
                "SELECT p.*, u.display_name AS actor FROM panel_audit AS p LEFT JOIN panel_users AS u "
                "ON u.id=p.actor_user_id WHERE (? IS NULL OR p.id<?) ORDER BY p.id DESC LIMIT ?",
                (before, before, min(limit, 200)),
            ).fetchall()
            return [dict(row) for row in rows]

    # ----------------------------------------------------------------- settings
    def settings(self) -> dict[str, bool]:
        with self._read() as connection:
            stored = {r["key"]: r["value"] for r in connection.execute("SELECT key,value FROM panel_settings")}
        return {key: bool(stored.get(key, default)) for key, default in SETTINGS_DEFAULTS.items()}

    def update_settings(self, values: dict[str, bool], *, actor_id: str) -> None:
        with self._tx() as connection:
            for key, value in values.items():
                if key not in SETTINGS_DEFAULTS or type(value) is not bool:
                    raise PanelError(422, "Configuração desconhecida ou inválida.")
                connection.execute(
                    "INSERT INTO panel_settings(key,value,updated_at,updated_by) VALUES(?,?,?,?) ON CONFLICT(key) "
                    "DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at,updated_by=excluded.updated_by",
                    (key, int(value), iso(self.clock()), actor_id))
                self._audit(connection, actor_id, f"setting_{key}_{'on' if value else 'off'}", "settings", key, "success")

    # -------------------------------------------------------------- atribuições
    def assignment(self, conversation_id: str) -> str | None:
        with self._read() as connection:
            row = connection.execute("SELECT user_id FROM panel_assignments WHERE conversation_id=?",
                                     (conversation_id,)).fetchone()
        return row["user_id"] if row else None

    def stage_assign(self, conversation_id: str, user_id: str, *, expected: str | None, actor_id: str) -> None:
        now = iso(self.clock())

        def effect(connection: sqlite3.Connection) -> None:
            from rocha_reis_agent.persistence.sqlite_channel import AssignmentLost
            current = connection.execute("SELECT user_id FROM panel_assignments WHERE conversation_id=?",
                                         (conversation_id,)).fetchone()
            if (current["user_id"] if current else None) != expected:
                raise AssignmentLost("Responsável mudou durante a operação.")
            connection.execute(
                "INSERT INTO panel_assignments(conversation_id,user_id,assigned_at,assigned_by) VALUES(?,?,?,?) "
                "ON CONFLICT(conversation_id) DO UPDATE SET user_id=excluded.user_id,assigned_at=excluded.assigned_at,"
                "assigned_by=excluded.assigned_by", (conversation_id, user_id, now, actor_id))

        self.repository.stage_effect(effect)

    def stage_unassign(self, conversation_id: str) -> None:
        self.repository.stage_effect(lambda connection: connection.execute(
            "DELETE FROM panel_assignments WHERE conversation_id=?", (conversation_id,)))

    def mark_read(self, user_id: str, conversation_id: str) -> None:
        with self._tx() as connection:
            connection.execute(
                "INSERT INTO panel_read_state(user_id,conversation_id,read_at) VALUES(?,?,?) ON CONFLICT(user_id,"
                "conversation_id) DO UPDATE SET read_at=excluded.read_at", (user_id, conversation_id, utc_now().isoformat()))

    # ---------------------------------------------------------------- conversas
    _LIST = (
        "SELECT c.id, c.state, c.closed, c.archived, c.updated_at, a.user_id AS assigned_id, "
        "u.display_name AS assigned_name, "
        "(SELECT role FROM messages m WHERE m.conversation_id=c.id ORDER BY ordinal DESC LIMIT 1) AS last_role, "
        "(SELECT substr(content,1,140) FROM messages m WHERE m.conversation_id=c.id ORDER BY ordinal DESC LIMIT 1) AS last_text, "
        "(SELECT recorded_at FROM messages m WHERE m.conversation_id=c.id ORDER BY ordinal DESC LIMIT 1) AS last_at, "
        "(SELECT substr(content,1,140) FROM messages m WHERE m.conversation_id=c.id AND m.role='client' "
        " ORDER BY ordinal DESC LIMIT 1) AS last_client_text, "
        "(SELECT count(*) FROM messages m WHERE m.conversation_id=c.id AND m.role='client' AND "
        " m.recorded_at > coalesce((SELECT read_at FROM panel_read_state r WHERE r.user_id=:me AND r.conversation_id=c.id),'')) AS unread, "
        "(SELECT status FROM channel_outbox o WHERE o.conversation_id=c.id ORDER BY o.rowid DESC LIMIT 1) AS last_send_status "
        "FROM conversations AS c LEFT JOIN panel_assignments AS a ON a.conversation_id=c.id "
        "LEFT JOIN panel_users AS u ON u.id=a.user_id "
    )

    def list_conversations(self, user: PanelUser, *, filter_key: str, query: str | None, cursor: str | None,
                           page_size: int) -> dict[str, object]:
        if filter_key not in FILTERS:
            raise PanelError(422, "Filtro desconhecido.")
        params: dict[str, Any] = {"me": user.id, "limit": page_size + 1}
        where = [visibility(user), FILTERS[filter_key]]
        if query:
            if len(query) > 80:
                raise PanelError(422, "Busca muito longa.")
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params["q"] = f"%{escaped}%"
            where.append("(c.id LIKE :q ESCAPE '\\' OR EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id=c.id "
                         "AND m.content LIKE :q ESCAPE '\\'))")
        if cursor:
            try:
                before_at, before_id = cursor.split("|", 1)
            except ValueError:
                raise PanelError(422, "Cursor inválido.") from None
            params.update(before_at=before_at, before_id=before_id)
            where.append("(c.updated_at < :before_at OR (c.updated_at = :before_at AND c.id < :before_id))")
        sql = self._LIST + "WHERE " + " AND ".join(where) + " ORDER BY c.updated_at DESC, c.id DESC LIMIT :limit"
        with self._read() as connection:
            rows = connection.execute(sql, params).fetchall()
        items = [self._summary(row) for row in rows[:page_size]]
        next_cursor = f"{rows[page_size - 1]['updated_at']}|{rows[page_size - 1]['id']}" if len(rows) > page_size else None
        return {"items": items, "next_cursor": next_cursor}

    @staticmethod
    def _summary(row: sqlite3.Row) -> dict[str, object]:
        return {
            "id": row["id"], "label": f"Cliente ··{row['id'][-6:]}", "state": row["state"],
            "closed": bool(row["closed"]), "archived": bool(row["archived"]), "updated_at": row["updated_at"],
            "assigned": {"id": row["assigned_id"], "name": row["assigned_name"]} if row["assigned_id"] else None,
            "last_message": {"role": row["last_role"], "text": row["last_text"], "at": row["last_at"]}
            if row["last_role"] else None,
            # Triagem: o que o CLIENTE disse por último (a resposta padrão do assistente não ajuda).
            "last_client_text": row["last_client_text"],
            "unread": row["unread"], "last_send_status": row["last_send_status"],
            "needs_human": row["state"] == "WAITING_HUMAN" and not row["closed"],
        }

    def visible_conversation(self, user: PanelUser, conversation_id: str) -> dict[str, object] | None:
        sql = self._LIST + "WHERE c.id=:cid AND " + visibility(user)
        with self._read() as connection:
            row = connection.execute(sql, {"me": user.id, "cid": conversation_id}).fetchone()
        return self._summary(row) if row else None

    def messages(self, conversation_id: str) -> list[dict[str, object]]:
        with self._read() as connection:
            rows = connection.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY ordinal",
                                      (conversation_id,)).fetchall()
            result = []
            for row in rows:
                outbox = None
                author = None
                if row["role"] == "assistant" and row["event_id"]:
                    outbox = connection.execute(
                        "SELECT status,delivery_status,outcome FROM channel_outbox WHERE conversation_id=? "
                        "AND source_event_id=? ORDER BY rowid DESC LIMIT 1", (conversation_id, row["event_id"])).fetchone()
                elif row["role"] == "operator" and row["operation_id"]:
                    outbox = connection.execute(
                        "SELECT o.status,o.delivery_status,o.outcome,u.display_name FROM channel_outbox o LEFT JOIN "
                        "panel_users u ON u.id=o.operator_user_id WHERE o.id=?", (row["operation_id"],)).fetchone()
                    author = outbox["display_name"] if outbox else None
                result.append({"ordinal": row["ordinal"], "role": row["role"], "text": row["content"],
                               "at": row["recorded_at"], "author": author, "delivery": delivery_label(outbox)})
            return result

    def transitions(self, conversation_id: str) -> list[dict[str, object]]:
        with self._read() as connection:
            rows = connection.execute(
                "SELECT t.previous,t.current,t.action,t.recorded_at,u.display_name AS actor FROM transitions t "
                "LEFT JOIN panel_users u ON 'panel:'||u.id=t.operator_id WHERE t.conversation_id=? ORDER BY t.ordinal",
                (conversation_id,)).fetchall()
            return [dict(row) for row in rows]

    def overview(self, user: PanelUser) -> dict[str, object]:
        with self._read() as connection:
            counts = {row[0]: row[1] for row in connection.execute(
                "SELECT CASE WHEN c.closed=1 THEN 'CLOSED' ELSE c.state END, count(*) FROM conversations AS c "
                "LEFT JOIN panel_assignments AS a ON a.conversation_id=c.id WHERE " + visibility(user) + " GROUP BY 1",
                {"me": user.id})}
            mine = connection.execute("SELECT count(*) FROM panel_assignments WHERE user_id=?", (user.id,)).fetchone()[0]
            halted = connection.execute("SELECT reason,set_at FROM channel_control WHERE key='dispatch_halt'").fetchone()
            outbox = {row[0]: row[1] for row in connection.execute(
                "SELECT status,count(*) FROM channel_outbox GROUP BY status")} if user.role == "ADMIN" else None
        return {"conversations": counts, "assigned_to_me": mine,
                "dispatch_halted": dict(halted) if halted else None, "outbox": outbox}

    # ---------------------------------------------------------- feed de mudanças
    def latest_change(self) -> int:
        with self._read() as connection:
            return connection.execute("SELECT coalesce(max(id),0) FROM change_log").fetchone()[0]

    def changes_since(self, user: PanelUser, cursor: int, *, limit: int = 500) -> dict[str, object]:
        """IDs visíveis alterados após o cursor. Conteúdo NUNCA vai no stream."""
        with self._read() as connection:
            low = connection.execute("SELECT coalesce(min(id),0), coalesce(max(id),0) FROM change_log").fetchone()
            if cursor and low[0] and cursor < low[0] - 1:
                return {"reset": True, "cursor": low[1], "conversations": [], "other": False}
            rows = connection.execute("SELECT id,conversation_id FROM change_log WHERE id>? ORDER BY id LIMIT ?",
                                      (cursor, limit)).fetchall()
            if not rows:
                return {"reset": False, "cursor": cursor, "conversations": [], "other": False}
            ids = sorted({row["conversation_id"] for row in rows})
            marks = ",".join("?" for _ in ids)
            visible = [row[0] for row in connection.execute(
                f"SELECT c.id FROM conversations AS c LEFT JOIN panel_assignments AS a ON a.conversation_id=c.id "
                f"WHERE c.id IN ({marks}) AND " + visibility(user).replace(":me", "?"),
                (*ids, *([user.id] if user.role != "ADMIN" else [])))]
            return {"reset": False, "cursor": rows[-1]["id"], "conversations": visible,
                    "other": len(visible) < len(ids)}

    def prune_changes(self, keep: int = 20000) -> None:
        with self._tx() as connection:
            connection.execute("DELETE FROM change_log WHERE id < (SELECT coalesce(max(id),0) FROM change_log) - ?", (keep,))
