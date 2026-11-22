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

SETTINGS_DEFAULTS = {"operators_can_transfer": 1, "operators_can_resume": 0,
                     "urgent_after_minutes": 15, "retired_contact_policy": 0}
# Tipos e limites validados no BACKEND (a interface não é confiável).
BOOL_SETTINGS = {"operators_can_transfer", "operators_can_resume"}
RETIRED_POLICIES = {"block": 0, "allow_new_conversation": 1}
FILTERS = {
    "all": "1=1",
    "waiting": "c.state='WAITING_HUMAN' AND c.closed=0",
    "human_active": "c.state='HUMAN_ACTIVE' AND c.closed=0",
    "auto": "c.state='AUTO' AND c.closed=0",
    "resolved": "(c.state='RESOLVED' OR c.closed=1)",
    # Atendimentos EM ANDAMENTO do usuário. Finalizar não remove o responsável (é o que mantém o histórico
    # visível para a atendente); os finalizados aparecem em "Encerradas" (revisão final: antes contavam aqui).
    "mine": "a.user_id=:me AND c.state='HUMAN_ACTIVE' AND c.closed=0",
    # Fase 10: atendimentos humanos de OUTROS funcionários (só aparece para quem pode ver: ADMIN).
    "others": "c.state='HUMAN_ACTIVE' AND c.closed=0 AND a.user_id IS NOT NULL AND a.user_id<>:me",
}
NOTIFICATION_LIMIT = 50


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


def _after(left: str, right: str) -> bool:
    """Compara instantes gravados em formatos ISO distintos (+00:00 e Z)."""
    return datetime.fromisoformat(left) > datetime.fromisoformat(right)


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
            # Fase 13: só o ESTADO do segundo fator (nunca segredo, chave ou códigos).
            return [{**_user(row).public(), "created_at": row["created_at"], "last_login_at": row["last_login_at"],
                     "mfa_enabled": row["mfa_state"] == "active"}
                    for row in connection.execute(
                        "SELECT u.*, m.state AS mfa_state FROM panel_users AS u LEFT JOIN panel_mfa AS m "
                        "ON m.user_id=u.id ORDER BY u.display_name, u.login")]

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
                # Desativar revoga imediatamente todas as sessões. Os atendimentos em andamento voltam à
                # fila em seguida (PanelService.set_active), cada um na própria transação da conversa.
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
        # Fase 17: LEITURA. Antes toda requisição autenticada abria transação de ESCRITA só para conferir a
        # sessão; sob carga (worker gravando, backup) isso estourava o tempo de espera do SQLite e virava 500.
        with self._read() as connection:
            row = connection.execute(
                "SELECT s.*, u.id AS uid, u.login, u.display_name, u.role, u.active, u.must_change_password "
                "FROM panel_sessions AS s JOIN panel_users AS u ON u.id=s.user_id WHERE s.token_hash=?", (hashed,),
            ).fetchone()
        if (row is None or row["revoked_at"] is not None or not row["active"]
                or parse_iso(row["expires_at"]) <= now
                or now - parse_iso(row["last_seen_at"]) > timedelta(minutes=idle_minutes)):
            return None
        if touch and now - parse_iso(row["last_seen_at"]) > timedelta(seconds=30):
            try:
                with self._tx() as connection:  # escrita curta; não reativa sessão revogada entre a leitura e aqui
                    connection.execute("UPDATE panel_sessions SET last_seen_at=? WHERE token_hash=? AND revoked_at IS "
                                       "NULL", (iso(now), hashed))
            except StorageError:
                pass  # banco ocupado: a renovação da ociosidade fica para a próxima requisição (até 30 s depois)
        user = PanelUser(row["uid"], row["login"], row["display_name"], row["role"], True,
                         bool(row["must_change_password"]))
        return PanelSession(hashed, user, csrf_for(token))

    def revoke_session(self, hashed: str, *, actor_id: str) -> None:
        with self._tx() as connection:
            connection.execute("UPDATE panel_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                               (iso(self.clock()), hashed))
            self._audit(connection, actor_id, "logout", "session", None, "success")

    def rotate_session(self, old_hash: str, user_id: str, *, absolute_hours: int, reason: str) -> tuple[str, str]:
        """Novo token após mudança de credencial/nível (senha, segundo fator); o anterior é revogado."""
        token = new_token()
        csrf = csrf_for(token)
        now = self.clock()
        with self._tx() as connection:
            connection.execute("UPDATE panel_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                               (iso(now), old_hash))
            connection.execute(
                "INSERT INTO panel_sessions(token_hash,user_id,csrf_token,created_at,last_seen_at,expires_at) "
                "VALUES(?,?,?,?,?,?)",
                (token_hash(token), user_id, token_hash(csrf), iso(now), iso(now), iso(now + timedelta(hours=absolute_hours))))
            self._audit(connection, user_id, f"session_rotated_{reason}"[:64], "session", None, "success")
        return token, csrf

    # ------------------------------------------------------------ segundo fator (Fase 13)
    def mfa_row(self, user_id: str) -> sqlite3.Row | None:
        with self._read() as connection:
            return connection.execute("SELECT * FROM panel_mfa WHERE user_id=?", (user_id,)).fetchone()

    def mfa_active(self, user_id: str) -> bool:
        row = self.mfa_row(user_id)
        return bool(row and row["state"] == "active")

    def mfa_begin(self, user_id: str, blob: str, key_id: str) -> None:
        with self._tx() as connection:
            row = connection.execute("SELECT state FROM panel_mfa WHERE user_id=?", (user_id,)).fetchone()
            if row is not None and row["state"] == "active":
                raise PanelError(409, "O segundo fator já está ativo nesta conta.")
            connection.execute(
                "INSERT INTO panel_mfa(user_id,state,secret_blob,key_id,last_step,created_at) VALUES(?,?,?,?,0,?) "
                "ON CONFLICT(user_id) DO UPDATE SET state='pending',secret_blob=excluded.secret_blob,"
                "key_id=excluded.key_id,last_step=0,created_at=excluded.created_at,activated_at=NULL",
                (user_id, "pending", blob, key_id, iso(self.clock())))
            self._audit(connection, user_id, "mfa_setup_started", "user", user_id, "success")

    def mfa_activate(self, user_id: str, step: int, code_hashes: list[str]) -> None:
        with self._tx() as connection:
            cursor = connection.execute(
                "UPDATE panel_mfa SET state='active',last_step=?,activated_at=? WHERE user_id=? AND state='pending'",
                (step, iso(self.clock()), user_id))
            if cursor.rowcount != 1:
                raise PanelError(409, "Não há cadastro de segundo fator pendente. Recomece o cadastro.")
            self._replace_codes(connection, user_id, code_hashes)
            self._audit(connection, user_id, "mfa_enabled", "user", user_id, "success")

    def _replace_codes(self, connection: sqlite3.Connection, user_id: str, code_hashes: list[str]) -> None:
        connection.execute("DELETE FROM panel_recovery_codes WHERE user_id=?", (user_id,))
        now = iso(self.clock())
        connection.executemany("INSERT INTO panel_recovery_codes(user_id,code_hash,created_at) VALUES(?,?,?)",
                               [(user_id, code, now) for code in code_hashes])

    def mfa_replace_codes(self, user_id: str, code_hashes: list[str]) -> None:
        with self._tx() as connection:
            self._replace_codes(connection, user_id, code_hashes)
            self._audit(connection, user_id, "mfa_recovery_codes_regenerated", "user", user_id, "success")

    def mfa_consume_step(self, user_id: str, step: int) -> bool:
        """Atômico: um código (passo) só vale uma vez — nem replay, nem passo anterior."""
        with self._tx() as connection:
            cursor = connection.execute("UPDATE panel_mfa SET last_step=? WHERE user_id=? AND state='active' "
                                        "AND last_step<?", (step, user_id, step))
            return cursor.rowcount == 1

    def mfa_use_recovery(self, user_id: str, code_hash: str) -> int | None:
        """Consome o código (uso único). Retorna quantos restam, ou None se inválido/já usado."""
        with self._tx() as connection:
            cursor = connection.execute("UPDATE panel_recovery_codes SET used_at=? WHERE user_id=? AND code_hash=? "
                                        "AND used_at IS NULL", (iso(self.clock()), user_id, code_hash))
            if cursor.rowcount != 1:
                return None
            self._audit(connection, user_id, "mfa_recovery_code_used", "user", user_id, "success")
            return connection.execute("SELECT count(*) FROM panel_recovery_codes WHERE user_id=? AND used_at IS NULL",
                                      (user_id,)).fetchone()[0]

    def mfa_remaining_codes(self, user_id: str) -> int:
        with self._read() as connection:
            return connection.execute("SELECT count(*) FROM panel_recovery_codes WHERE user_id=? AND used_at IS NULL",
                                      (user_id,)).fetchone()[0]

    def mfa_remove(self, user_id: str, *, actor_id: str, justification: str | None) -> None:
        """Desativação própria (sem justificativa) ou redefinição administrativa (com justificativa).
        Revoga TODAS as sessões do usuário e qualquer etapa de login pendente."""
        now = iso(self.clock())
        with self._tx() as connection:
            connection.execute("DELETE FROM panel_recovery_codes WHERE user_id=?", (user_id,))
            connection.execute("DELETE FROM panel_mfa_challenges WHERE user_id=?", (user_id,))
            removed = connection.execute("DELETE FROM panel_mfa WHERE user_id=?", (user_id,)).rowcount
            if not removed:
                raise PanelError(409, "Esta conta não tem segundo fator configurado.")
            connection.execute("UPDATE panel_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                               (now, user_id))
            if justification is not None:
                connection.execute("INSERT INTO panel_mfa_resets(user_id,actor_user_id,justification,at) VALUES(?,?,?,?)",
                                   (user_id, actor_id, justification, now))
            self._audit(connection, actor_id, "mfa_reset_by_admin" if justification is not None else "mfa_disabled",
                        "user", user_id, "success")

    def mfa_reencrypt(self, rows: list[tuple[str, str, str]]) -> int:
        """Rotação da chave: grava os segredos recifrados (user_id, blob, key_id) numa transação."""
        with self._tx() as connection:
            for user_id, blob, key_id in rows:
                connection.execute("UPDATE panel_mfa SET secret_blob=?,key_id=? WHERE user_id=?", (blob, key_id, user_id))
            self._audit(connection, None, "mfa_key_rotated", "settings", None, "success")
        return len(rows)

    def mfa_all(self) -> list[sqlite3.Row]:
        with self._read() as connection:
            return connection.execute("SELECT user_id,secret_blob,key_id FROM panel_mfa").fetchall()

    def create_challenge(self, user_id: str, hashed: str, expires_at: datetime) -> None:
        now = iso(self.clock())
        with self._tx() as connection:
            connection.execute("DELETE FROM panel_mfa_challenges WHERE expires_at<? OR consumed_at IS NOT NULL", (now,))
            connection.execute("INSERT INTO panel_mfa_challenges(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)",
                               (hashed, user_id, now, iso(expires_at)))

    def challenge_attempt(self, hashed: str, *, max_attempts: int) -> str | None:
        """Conta a tentativa e devolve o usuário se a etapa ainda vale (não expirada, não usada, sob o limite)."""
        now = self.clock()
        with self._tx() as connection:
            row = connection.execute("SELECT * FROM panel_mfa_challenges WHERE token_hash=?", (hashed,)).fetchone()
            if (row is None or row["consumed_at"] is not None or parse_iso(row["expires_at"]) <= now
                    or row["attempts"] >= max_attempts):
                return None
            connection.execute("UPDATE panel_mfa_challenges SET attempts=attempts+1 WHERE token_hash=?", (hashed,))
            return row["user_id"]

    def consume_challenge(self, hashed: str) -> bool:
        with self._tx() as connection:
            return connection.execute("UPDATE panel_mfa_challenges SET consumed_at=? WHERE token_hash=? "
                                      "AND consumed_at IS NULL", (iso(self.clock()), hashed)).rowcount == 1

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
    def settings(self) -> dict[str, object]:
        with self._read() as connection:
            stored = {r["key"]: r["value"] for r in connection.execute("SELECT key,value FROM panel_settings")}
        values = {key: stored.get(key, default) for key, default in SETTINGS_DEFAULTS.items()}
        policy = {v: k for k, v in RETIRED_POLICIES.items()}.get(values["retired_contact_policy"], "block")
        return {**{key: bool(values[key]) for key in BOOL_SETTINGS},
                "urgent_after_minutes": int(values["urgent_after_minutes"]), "retired_contact_policy": policy}

    def update_settings(self, values: dict[str, object], *, actor_id: str) -> None:
        with self._tx() as connection:
            for key, value in values.items():
                if key in BOOL_SETTINGS and type(value) is bool:
                    stored, action = int(value), f"setting_{key}_{'on' if value else 'off'}"
                elif key == "urgent_after_minutes" and type(value) is int and 1 <= value <= 1440:
                    stored, action = value, "setting_urgent_after_minutes_changed"
                elif key == "retired_contact_policy" and value in RETIRED_POLICIES:
                    stored, action = RETIRED_POLICIES[value], f"setting_retired_contact_policy_{value}"  # type: ignore[index]
                else:
                    raise PanelError(422, "Configuração desconhecida ou inválida.")
                connection.execute(
                    "INSERT INTO panel_settings(key,value,updated_at,updated_by) VALUES(?,?,?,?) ON CONFLICT(key) "
                    "DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at,updated_by=excluded.updated_by",
                    (key, stored, iso(self.clock()), actor_id))
                self._audit(connection, actor_id, action[:64], "settings", key, "success")

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
            # Revisão (22/11): o destino era conferido como ATIVO só antes da transação. Uma desativação
            # simultânea (que devolve à fila o que a pessoa tem DEPOIS de gravar active=0) deixava a conversa em
            # atendimento com alguém desativado. Conferido aqui, sob o mesmo BEGIN IMMEDIATE do commit.
            target = connection.execute("SELECT active FROM panel_users WHERE id=?", (user_id,)).fetchone()
            if target is None or not target["active"]:
                raise AssignmentLost("Funcionário de destino desativado durante a operação.")
            connection.execute(
                "INSERT INTO panel_assignments(conversation_id,user_id,assigned_at,assigned_by) VALUES(?,?,?,?) "
                "ON CONFLICT(conversation_id) DO UPDATE SET user_id=excluded.user_id,assigned_at=excluded.assigned_at,"
                "assigned_by=excluded.assigned_by", (conversation_id, user_id, now, actor_id))

        self.repository.stage_effect(effect)

    def active_assignments(self, user_id: str) -> list[str]:
        """Conversas em atendimento humano ativo sob responsabilidade do usuário."""
        with self._read() as connection:
            return [row[0] for row in connection.execute(
                "SELECT a.conversation_id FROM panel_assignments AS a JOIN conversations AS c ON c.id=a.conversation_id "
                "WHERE a.user_id=? AND c.state='HUMAN_ACTIVE' AND c.closed=0 ORDER BY a.assigned_at", (user_id,))]

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
        "(SELECT status FROM channel_outbox o WHERE o.conversation_id=c.id ORDER BY o.rowid DESC LIMIT 1) AS last_send_status, "
        "(SELECT recorded_at FROM transitions t WHERE t.conversation_id=c.id AND t.current='WAITING_HUMAN' "
        " ORDER BY t.ordinal DESC LIMIT 1) AS waiting_since "
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
        urgent_after = timedelta(minutes=int(self.settings()["urgent_after_minutes"]))
        items = [self._summary(row, now=self.clock(), urgent_after=urgent_after) for row in rows[:page_size]]
        next_cursor = f"{rows[page_size - 1]['updated_at']}|{rows[page_size - 1]['id']}" if len(rows) > page_size else None
        return {"items": items, "next_cursor": next_cursor}

    @staticmethod
    def _summary(row: sqlite3.Row, *, now: datetime | None = None, urgent_after: timedelta | None = None) -> dict[str, object]:
        needs_human = row["state"] == "WAITING_HUMAN" and not row["closed"]
        waiting_since = row["waiting_since"] if needs_human else None
        urgent = bool(waiting_since and now is not None and urgent_after is not None
                      and now - datetime.fromisoformat(waiting_since) >= urgent_after)
        return {
            "id": row["id"], "label": f"Cliente ··{row['id'][-6:]}", "state": row["state"],
            "closed": bool(row["closed"]), "archived": bool(row["archived"]), "updated_at": row["updated_at"],
            "assigned": {"id": row["assigned_id"], "name": row["assigned_name"]} if row["assigned_id"] else None,
            "last_message": {"role": row["last_role"], "text": row["last_text"], "at": row["last_at"]}
            if row["last_role"] else None,
            # Triagem: o que o CLIENTE disse por último (a resposta padrão do assistente não ajuda).
            "last_client_text": row["last_client_text"],
            "unread": row["unread"], "last_send_status": row["last_send_status"],
            "needs_human": needs_human, "waiting_since": waiting_since, "urgent": urgent,
        }

    def visible_conversation(self, user: PanelUser, conversation_id: str) -> dict[str, object] | None:
        sql = self._LIST + "WHERE c.id=:cid AND " + visibility(user)
        with self._read() as connection:
            row = connection.execute(sql, {"me": user.id, "cid": conversation_id}).fetchone()
        if not row:
            return None
        return self._summary(row, now=self.clock(),
                             urgent_after=timedelta(minutes=int(self.settings()["urgent_after_minutes"])))

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

    def messages_page(self, conversation_id: str, *, before: int | None = None, limit: int = 50) -> dict[str, object]:
        """Histórico paginado (mais recentes primeiro na busca, ordem cronológica no retorno).

        Situação de envio buscada em lote (sem uma consulta por mensagem).
        """
        limit = max(1, min(limit, 200))
        with self._read() as connection:
            rows = connection.execute(
                "SELECT * FROM messages WHERE conversation_id=? AND (? IS NULL OR ordinal<?) ORDER BY ordinal DESC LIMIT ?",
                (conversation_id, before, before, limit + 1)).fetchall()
            has_more = len(rows) > limit
            rows = list(reversed(rows[:limit]))
            events = [r["event_id"] for r in rows if r["role"] == "assistant" and r["event_id"]]
            operations = [r["operation_id"] for r in rows if r["role"] == "operator" and r["operation_id"]]
            by_event: dict[str, sqlite3.Row] = {}
            by_operation: dict[str, sqlite3.Row] = {}
            if events:
                marks = ",".join("?" for _ in events)
                for o in connection.execute(
                        f"SELECT source_event_id,status,delivery_status,outcome FROM channel_outbox WHERE conversation_id=? "
                        f"AND source_event_id IN ({marks}) ORDER BY rowid", (conversation_id, *events)):
                    by_event[o["source_event_id"]] = o          # a última linha por evento prevalece
            if operations:
                marks = ",".join("?" for _ in operations)
                for o in connection.execute(
                        f"SELECT o.id,o.status,o.delivery_status,o.outcome,u.display_name FROM channel_outbox o LEFT JOIN "
                        f"panel_users u ON u.id=o.operator_user_id WHERE o.id IN ({marks})", operations):
                    by_operation[o["id"]] = o
        result = []
        for row in rows:
            outbox, author = None, None
            if row["role"] == "assistant" and row["event_id"]:
                outbox = by_event.get(row["event_id"])
            elif row["role"] == "operator" and row["operation_id"]:
                outbox = by_operation.get(row["operation_id"])
                author = outbox["display_name"] if outbox else None
            result.append({"ordinal": row["ordinal"], "role": row["role"], "text": row["content"],
                           "at": row["recorded_at"], "author": author, "delivery": delivery_label(outbox)})
        return {"messages": result, "has_more": has_more,
                "before": result[0]["ordinal"] if result and has_more else None}

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
            mine = connection.execute(
                "SELECT count(*) FROM panel_assignments AS a JOIN conversations AS c ON c.id=a.conversation_id "
                "WHERE a.user_id=? AND c.state='HUMAN_ACTIVE' AND c.closed=0", (user.id,)).fetchone()[0]
            # Rede de segurança: atendimento em andamento com responsável desativado (ex.: devolução à fila
            # interrompida). Só a administração vê e pode transferir.
            orphaned = connection.execute(
                "SELECT count(*) FROM panel_assignments AS a JOIN conversations AS c ON c.id=a.conversation_id "
                "JOIN panel_users AS u ON u.id=a.user_id WHERE u.active=0 AND c.state='HUMAN_ACTIVE' AND c.closed=0"
            ).fetchone()[0] if user.role == "ADMIN" else None
            halted = connection.execute("SELECT reason,set_at FROM channel_control WHERE key='dispatch_halt'").fetchone()
            outbox = {row[0]: row[1] for row in connection.execute(
                "SELECT status,count(*) FROM channel_outbox GROUP BY status")} if user.role == "ADMIN" else None
            visible = visibility(user)
            waiting = connection.execute(
                "SELECT count(*), min((SELECT recorded_at FROM transitions t WHERE t.conversation_id=c.id AND "
                "t.current='WAITING_HUMAN' ORDER BY t.ordinal DESC LIMIT 1)) FROM conversations AS c LEFT JOIN "
                "panel_assignments AS a ON a.conversation_id=c.id WHERE " + visible +
                " AND c.state='WAITING_HUMAN' AND c.closed=0 AND a.user_id IS NULL", {"me": user.id}).fetchone()
            last_activity = connection.execute(
                "SELECT max(c.updated_at) FROM conversations AS c LEFT JOIN panel_assignments AS a ON "
                "a.conversation_id=c.id WHERE " + visible, {"me": user.id}).fetchone()[0]
            inbox = {row[0]: row[1] for row in connection.execute(
                "SELECT status,count(*) FROM channel_inbox GROUP BY status")} if user.role == "ADMIN" else None
            retired_attempts = connection.execute(
                "SELECT count(*) FROM channel_inbox WHERE outcome='retired_contact_blocked'").fetchone()[0] \
                if user.role == "ADMIN" else None
        urgent_after = int(self.settings()["urgent_after_minutes"])
        oldest = waiting[1]
        return {"conversations": counts, "assigned_to_me": mine,
                "dispatch_halted": dict(halted) if halted else None, "outbox": outbox,
                # Fase 10: o que precisa de atenção, sempre derivado do banco (nada estimado).
                "waiting_unassigned": waiting[0], "oldest_waiting_since": oldest,
                "oldest_waiting_minutes": int((self.clock() - datetime.fromisoformat(oldest)).total_seconds() // 60) if oldest else None,
                "urgent_after_minutes": urgent_after, "last_activity_at": last_activity,
                "inbox": inbox, "retired_contact_attempts": retired_attempts, "orphaned_active": orphaned}

    # ---------------------------------------------------------- notificações (Fase 10)
    def notifications(self, user: PanelUser) -> list[dict[str, object]]:
        """Avisos ATUAIS derivados do estado do banco (não do stream): reconexões e eventos perdidos
        não criam duplicatas nem lacunas. Cada aviso tem chave estável; o cliente guarda as já vistas.
        Nunca inclui texto de mensagem nem dado do cliente além do rótulo ··xxxxxx.
        """
        now = self.clock()
        items: list[dict[str, object]] = []

        def add(key: str, kind: str, severity: str, text: str, conversation_id: str | None, at: str | None,
                mandatory: bool = False) -> None:
            items.append({"key": key, "type": kind, "severity": severity, "text": text, "mandatory": mandatory,
                          "conversation_id": conversation_id,
                          "label": f"Cliente ··{conversation_id[-6:]}" if conversation_id else None, "at": at})

        visible = visibility(user)
        with self._read() as connection:
            for row in connection.execute(
                    "SELECT c.id, (SELECT max(t.ordinal) FROM transitions t WHERE t.conversation_id=c.id AND "
                    "t.current='WAITING_HUMAN') AS ordinal, (SELECT max(t.recorded_at) FROM transitions t WHERE "
                    "t.conversation_id=c.id AND t.current='WAITING_HUMAN') AS at FROM conversations AS c LEFT JOIN "
                    "panel_assignments AS a ON a.conversation_id=c.id WHERE " + visible + " AND c.state='WAITING_HUMAN' "
                    "AND c.closed=0 AND a.user_id IS NULL ORDER BY at LIMIT :limit", {"me": user.id, "limit": NOTIFICATION_LIMIT}):
                add(f"hr:{row['id']}:{row['ordinal']}", "human_requested", "attention",
                    "Cliente aguardando atendimento humano.", row["id"], row["at"])
            for row in connection.execute(
                    "SELECT a.conversation_id AS id, a.assigned_at, a.assigned_by FROM panel_assignments AS a JOIN "
                    "conversations AS c ON c.id=a.conversation_id WHERE a.user_id=? AND a.assigned_by<>? AND "
                    "c.closed=0 AND c.retired=0 AND a.assigned_at>=? LIMIT ?",
                    (user.id, user.id, iso(now - timedelta(days=1)), NOTIFICATION_LIMIT)):
                add(f"as:{row['id']}:{row['assigned_at']}", "assigned_to_me", "info",
                    "Uma conversa foi atribuída a você.", row["id"], row["assigned_at"])
            for row in connection.execute(
                    "SELECT c.id, (SELECT max(m.ordinal) FROM messages m WHERE m.conversation_id=c.id AND m.role='client') "
                    # Momento em que o SISTEMA processou a mensagem (o horário de origem do WhatsApp
                    # tem resolução de segundos e pode ser anterior à atribuição).
                    "AS ordinal, (SELECT max(e.processed_at) FROM messages m JOIN events e ON e.conversation_id="
                    "m.conversation_id AND e.event_id=m.event_id WHERE m.conversation_id=c.id AND "
                    "m.role='client') AS at, a.assigned_at FROM conversations AS c JOIN panel_assignments AS a ON a.conversation_id=c.id "
                    "WHERE a.user_id=? AND c.state='HUMAN_ACTIVE' AND c.closed=0 AND c.retired=0 LIMIT ?",
                    (user.id, NOTIFICATION_LIMIT)):
                read_at = connection.execute("SELECT read_at FROM panel_read_state WHERE user_id=? AND conversation_id=?",
                                             (user.id, row["id"])).fetchone()
                # Só mensagens do cliente DEPOIS da atribuição a esta pessoa e ainda não lidas.
                if row["at"] and _after(row["at"], row["assigned_at"]) and (read_at is None or _after(row["at"], read_at[0])):
                    add(f"nm:{row['id']}:{row['ordinal']}", "new_message", "attention",
                        "Nova mensagem em um atendimento seu.", row["id"], row["at"])
            outbox_scope = "1=1" if user.role == "ADMIN" else (
                "(o.operator_user_id=:me OR o.conversation_id IN (SELECT conversation_id FROM panel_assignments "
                "WHERE user_id=:me))")
            for row in connection.execute(
                    "SELECT o.id,o.conversation_id,o.status,o.updated_at FROM channel_outbox AS o WHERE "
                    "o.status IN ('FAILED','UNKNOWN') AND o.updated_at>=:since AND " + outbox_scope +
                    " ORDER BY o.updated_at DESC LIMIT :limit",
                    {"me": user.id, "since": iso(now - timedelta(days=7)), "limit": NOTIFICATION_LIMIT}):
                unknown = row["status"] == "UNKNOWN"
                add(f"{'su' if unknown else 'sf'}:{row['id']}", "send_unknown" if unknown else "send_failed", "critical",
                    "Envio com resultado INCERTO: confira antes de qualquer reenvio." if unknown
                    else "Falha no envio de uma resposta.", row["conversation_id"], row["updated_at"], mandatory=True)
            halted = connection.execute("SELECT reason,set_at FROM channel_control WHERE key='dispatch_halt'").fetchone()
            if halted:
                add(f"halt:{halted['set_at']}", "dispatch_halted", "critical",
                    "Envios INTERROMPIDOS: nenhuma mensagem sairá até a retomada.", None, halted["set_at"], mandatory=True)
            if user.role == "ADMIN":
                for row in connection.execute(
                        "SELECT id,updated_at FROM channel_inbox WHERE status='FAILED' AND updated_at>=? "
                        "ORDER BY id DESC LIMIT ?", (iso(now - timedelta(days=1)), NOTIFICATION_LIMIT)):
                    add(f"if:{row['id']}", "inbound_failed", "warning",
                        "Uma mensagem recebida não pôde ser processada.", None, row["updated_at"], mandatory=True)
                for row in connection.execute(
                        "SELECT id,conversation_id,updated_at FROM channel_inbox WHERE outcome='retired_contact_blocked' "
                        "AND updated_at>=? ORDER BY id DESC LIMIT ?", (iso(now - timedelta(days=7)), NOTIFICATION_LIMIT)):
                    add(f"rc:{row['id']}", "retired_contact", "warning",
                        "Contato com dados excluídos voltou a escrever (bloqueado pela política atual).",
                        row["conversation_id"], row["updated_at"])
        return items

    def retired_contacts(self) -> list[dict[str, object]]:
        """ADMIN: contatos excluídos e tentativas posteriores (sem conteúdo)."""
        with self._read() as connection:
            rows = connection.execute(
                "SELECT c.id, c.updated_at, (SELECT count(*) FROM channel_inbox i WHERE i.conversation_id=c.id AND "
                "i.outcome='retired_contact_blocked') AS attempts, (SELECT max(updated_at) FROM channel_inbox i WHERE "
                "i.conversation_id=c.id AND i.outcome='retired_contact_blocked') AS last_attempt_at FROM conversations c "
                "WHERE c.retired=1 ORDER BY last_attempt_at DESC, c.updated_at DESC LIMIT 200").fetchall()
        return [{"id": r["id"], "label": f"Cliente ··{r['id'][-6:]}", "retired_at": r["updated_at"],
                 "attempts": r["attempts"], "last_attempt_at": r["last_attempt_at"]} for r in rows]

    def system_status(self) -> dict[str, object]:
        """ADMIN: estado técnico local (contagens; nenhum conteúdo, nenhum segredo)."""
        with self._read() as connection:
            counts = {table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in (
                "conversations", "messages", "events", "channel_inbox", "channel_outbox", "panel_audit", "ai_usage")}
            archived = connection.execute("SELECT count(*) FROM conversations WHERE archived=1").fetchone()[0]
            retired = connection.execute("SELECT count(*) FROM conversations WHERE retired=1").fetchone()[0]
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            pages = connection.execute("PRAGMA page_count").fetchone()[0] * connection.execute("PRAGMA page_size").fetchone()[0]
        return {"counts": counts, "archived": archived, "retired": retired, "schema_version": version,
                "database_bytes": pages}

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
