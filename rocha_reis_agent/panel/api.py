"""API do painel: sessão por cookie HttpOnly, CSRF por token + Origin, RBAC no servidor, SSE."""

import asyncio
from dataclasses import dataclass
from hashlib import sha256
import hmac
import json
from time import monotonic
from typing import Literal

from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from rocha_reis_agent.config import Settings
from rocha_reis_agent.domain.contracts import StorageError
from rocha_reis_agent.panel.security import PanelError, hash_password, normalize_login, validate_password, verify_password
from rocha_reis_agent.panel.service import PanelService
from rocha_reis_agent.panel.store import PanelSession, PanelUser, SQLitePanelStore
from rocha_reis_agent.persistence.sqlite_channel import SQLiteChannelStore

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class LoginBody(Body):
    login: str = Field(max_length=64)
    password: str = Field(max_length=128)


class PasswordBody(Body):
    current_password: str = Field(max_length=128)
    new_password: str = Field(max_length=128)


class MfaCodeBody(Body):
    code: str | None = Field(default=None, max_length=12)
    recovery_code: str | None = Field(default=None, max_length=20)


class MfaSetupBody(Body):
    password: str = Field(max_length=128)


class MfaDisableBody(Body):
    password: str = Field(max_length=128)
    code: str = Field(max_length=12)


class MfaResetBody(Body):
    justification: str = Field(min_length=10, max_length=300)
    confirmation: str = Field(max_length=80)


class ReplyBody(Body):
    text: str = Field(max_length=4096)
    idempotency_key: str = Field(max_length=64)


class TransferBody(Body):
    target_user_id: str = Field(max_length=32)


class UserBody(Body):
    login: str = Field(max_length=64)
    display_name: str = Field(max_length=80)
    role: str = Field(max_length=16)
    password: str = Field(max_length=128)


class ResetBody(Body):
    password: str = Field(max_length=128)


class SettingsBody(Body):
    # strict: "15" ou "true" (texto) são recusados em vez de convertidos.
    operators_can_transfer: bool | None = Field(default=None, strict=True)
    operators_can_resume: bool | None = Field(default=None, strict=True)
    # Fase 10: validados de novo no store (tipo e faixa). Nenhuma chave liga integração real.
    urgent_after_minutes: int | None = Field(default=None, ge=1, le=1440, strict=True)
    retired_contact_policy: Literal["block", "allow_new_conversation"] | None = None


class ReinstateBody(Body):
    confirmation: str = Field(max_length=64)


class HaltBody(Body):
    reason: str = Field(min_length=1, max_length=200)


class ResumeBody(Body):
    confirmation: str = Field(max_length=32)


class DraftBody(Body):
    kind: str = Field(max_length=32)
    key: str | None = Field(default=None, max_length=64)
    fields: dict[str, str] = Field(default_factory=dict)
    source: str = Field(max_length=240)
    expected_version: int | None = None


class RevisionBody(Body):
    fact_id: str = Field(max_length=80)
    version: int


class PreviewBody(Body):
    question: str = Field(max_length=500)


@dataclass
class PanelContext:
    settings: Settings
    store: SQLitePanelStore
    service: PanelService
    channel: SQLiteChannelStore
    knowledge: object | None = None   # KnowledgeService quando KNOWLEDGE_SOURCE=database
    ledger: object | None = None      # SQLiteUsageLedger (consumo da IA)
    mfa: object | None = None         # MfaCipher quando PANEL_MFA_KEY_FILE está configurado

    @property
    def cookie(self) -> str:
        # Prefixo __Host- exige Secure: usado sempre que o cookie for Secure.
        return "__Host-rr_panel" if self.settings.panel_cookie_secure else "rr_panel"

    @property
    def mfa_cookie(self) -> str:
        """Etapa intermediária do login (senha ok, segundo fator pendente). NÃO é cookie de sessão."""
        return "__Host-rr_panel_mfa" if self.settings.panel_cookie_secure else "rr_panel_mfa"


async def _json(request: Request, model: type[Body]) -> Body:
    raw = await request.body()
    if len(raw) > 16384:
        raise PanelError(413, "Requisição muito grande.")
    try:
        return model.model_validate_json(raw or b"{}")
    except ValidationError:
        raise PanelError(422, "Dados inválidos.") from None


def build_panel_router(ctx: PanelContext) -> APIRouter:
    router = APIRouter(prefix="/api/panel")
    settings = ctx.settings
    store = ctx.store
    service = ctx.service

    def check_origin(request: Request) -> None:
        # Requisições que alteram estado só do próprio painel (CSRF, DNS rebinding).
        if request.headers.get("origin") != settings.panel_origin:
            raise PanelError(403, "Origem da requisição não autorizada.")

    def authenticated(request: Request, *, allow_password_change: bool = False, touch: bool = True,
                      allow_mfa_setup: bool = False) -> PanelSession:
        session = store.session(request.cookies.get(ctx.cookie), idle_minutes=settings.panel_session_idle_minutes,
                                touch=touch)
        if session is None:
            raise PanelError(401, "Sessão ausente ou expirada. Entre novamente.")
        if request.method in UNSAFE:
            check_origin(request)
            sent = request.headers.get("x-csrf-token", "")
            if not hmac.compare_digest(sent.encode(), session.csrf_token.encode()):
                raise PanelError(403, "Token de proteção inválido. Recarregue a página.")
        if session.user.must_change_password and not allow_password_change:
            raise PanelError(403, "Troque sua senha temporária para continuar.")
        if settings.panel_mfa_required and not allow_mfa_setup and not store.mfa_active(session.user.id):
            # Política obrigatória: sem segundo fator ativo, só o cadastro dele (e sair) é permitido.
            raise PanelError(403, "Configure a verificação em duas etapas para continuar.")
        return session

    def set_session_cookie(response: Response, token: str) -> None:
        response.set_cookie(ctx.cookie, token, httponly=True, secure=settings.panel_cookie_secure, samesite="strict",
                            path="/", max_age=settings.panel_session_absolute_hours * 3600)

    def clear_mfa_cookie(response: Response) -> None:
        response.delete_cookie(ctx.mfa_cookie, path="/", secure=settings.panel_cookie_secure, httponly=True,
                               samesite="strict")

    def client_of(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def end_previous_session(request: Request) -> None:
        previous = request.cookies.get(ctx.cookie)
        if previous:  # proteção contra fixação: a sessão anterior nunca é reaproveitada
            old = store.session(previous, idle_minutes=settings.panel_session_idle_minutes)
            if old is not None:
                store.revoke_session(old.token_hash, actor_id=old.user.id)

    def admin(request: Request) -> PanelSession:
        session = authenticated(request)
        if session.user.role != "ADMIN":
            store.audit(session.user.id, "admin_access", "user", None, "denied")
            raise PanelError(403, "Ação reservada a administradores.")
        return session

    def _release() -> str:
        from rocha_reis_agent import RELEASE_NAME, __version__
        return f"{RELEASE_NAME} ({__version__})"  # só para usuários autenticados (nunca no /health público)

    def mfa_state(user_id: str) -> dict[str, object]:
        row = store.mfa_row(user_id)
        enabled = bool(row and row["state"] == "active")
        return {"enabled": enabled, "pending": bool(row and row["state"] == "pending"),
                "required": settings.panel_mfa_required, "available": ctx.mfa is not None,
                "recovery_codes_left": store.mfa_remaining_codes(user_id) if enabled else 0}

    def session_payload(session: PanelSession) -> dict[str, object]:
        return {"user": session.user.public(), "csrf_token": session.csrf_token, "permissions": store.settings(),
                "environment_label": settings.panel_environment_label, "mfa": mfa_state(session.user.id),
                "release": _release()}

    # ---------------------------------------------------------- autenticação
    @router.post("/auth/login")
    async def login(request: Request) -> Response:
        check_origin(request)
        body = await _json(request, LoginBody)
        try:
            login_name = normalize_login(body.login)  # type: ignore[attr-defined]
        except PanelError:
            login_name = "invalid"
        client = client_of(request)
        keys = [sha256(f"login:{login_name}".encode()).hexdigest(), sha256(f"ip:{client}".encode()).hexdigest()]

        def attempt() -> tuple[str, str, dict[str, object]] | None:
            if store.recent_failures(keys, window_minutes=settings.panel_login_window_minutes) >= settings.panel_login_max_failures:
                store.record_failure([], actor_id=None, outcome="denied")
                raise PanelError(429, "Muitas tentativas. Aguarde alguns minutos e tente novamente.")
            found = store.user_by_login(login_name)
            valid = verify_password(found[1] if found else None, body.password)  # type: ignore[attr-defined]
            if not valid or found is None or not found[0].active:
                store.record_failure(keys, actor_id=found[0].id if found else None, outcome="failed")
                return None
            store.clear_failures(keys[0])
            if store.mfa_active(found[0].id):
                # Senha correta, mas NENHUMA sessão é criada até o segundo fator ser confirmado.
                from rocha_reis_agent.panel import mfa as mfa_module
                challenge, hashed = mfa_module.new_challenge()
                store.create_challenge(found[0].id, hashed, mfa_module.challenge_expiry(store.clock()))
                store.audit(found[0].id, "login_password_ok_mfa_pending", "session", None, "success")
                return "mfa", challenge, {}
            end_previous_session(request)
            token, csrf = store.create_session(found[0].id, absolute_hours=settings.panel_session_absolute_hours)
            return token, csrf, found[0].public()

        result = await run_in_threadpool(attempt)
        if result is None:
            raise PanelError(401, "Login ou senha inválidos.")
        token, csrf, user = result
        if token == "mfa":
            response = JSONResponse({"mfa_required": True})
            from rocha_reis_agent.panel.mfa import CHALLENGE_MINUTES
            response.set_cookie(ctx.mfa_cookie, csrf, httponly=True, secure=settings.panel_cookie_secure,
                                samesite="strict", path="/", max_age=CHALLENGE_MINUTES * 60)
            return response
        response = JSONResponse({"user": user, "csrf_token": csrf, "permissions": store.settings(),
                                 "environment_label": settings.panel_environment_label,
                                 "mfa": mfa_state(str(user["id"])), "release": _release()})
        set_session_cookie(response, token)
        return response

    @router.post("/auth/mfa")
    async def verify_mfa(request: Request) -> Response:
        """2ª etapa do login: código TOTP (ou de recuperação) para a etapa pendente do cookie."""
        check_origin(request)
        body = await _json(request, MfaCodeBody)
        from rocha_reis_agent.panel import mfa as mfa_module
        challenge = request.cookies.get(ctx.mfa_cookie) or ""
        hashed = mfa_module.token_hash(challenge) if 20 <= len(challenge) <= 128 else None
        ip_key = sha256(f"ip:{client_of(request)}".encode()).hexdigest()

        def attempt() -> tuple[str, str, PanelUser] | None:
            cipher = mfa_module.require_cipher(ctx.mfa)  # type: ignore[arg-type]
            user_id = store.challenge_attempt(hashed, max_attempts=mfa_module.CHALLENGE_ATTEMPTS) if hashed else None
            if user_id is None:
                raise PanelError(401, "A verificação expirou ou foi encerrada. Entre novamente com login e senha.")
            keys = [sha256(f"mfa:{user_id}".encode()).hexdigest(), ip_key]
            if store.recent_failures(keys, window_minutes=settings.panel_login_window_minutes) >= settings.panel_login_max_failures:
                store.record_failure([], actor_id=user_id, outcome="denied")
                raise PanelError(429, "Muitas tentativas. Aguarde alguns minutos e entre novamente.")
            user = store.user(user_id)
            row = store.mfa_row(user_id)
            ok = False
            if user is not None and user.active and row is not None and row["state"] == "active":
                if body.recovery_code:  # type: ignore[attr-defined]
                    ok = store.mfa_use_recovery(user_id, mfa_module.recovery_hash(user_id, body.recovery_code)) is not None  # type: ignore[attr-defined]
                elif body.code:  # type: ignore[attr-defined]
                    secret = cipher.decrypt(user_id, row["secret_blob"], row["key_id"])
                    step = mfa_module.matching_step(secret, body.code)  # type: ignore[attr-defined]
                    ok = step is not None and store.mfa_consume_step(user_id, step)
            if not ok or user is None:
                store.record_failure(keys, actor_id=user_id, outcome="failed")
                store.audit(user_id, "mfa_failed", "session", None, "failed")
                return None
            if not store.consume_challenge(hashed):  # type: ignore[arg-type]
                raise PanelError(401, "A verificação expirou ou foi encerrada. Entre novamente com login e senha.")
            store.clear_failures(keys[0])
            end_previous_session(request)
            token, csrf = store.create_session(user_id, absolute_hours=settings.panel_session_absolute_hours)
            store.audit(user_id, "mfa_verified", "session", None, "success")
            return token, csrf, user

        try:
            result = await run_in_threadpool(attempt)
        except mfa_module.MfaKeyError:
            store.audit(None, "mfa_key_unavailable", "session", None, "failed")
            raise PanelError(503, "Verificação em duas etapas indisponível. Avise o suporte técnico.") from None
        if result is None:
            raise PanelError(401, "Código inválido ou já utilizado.")
        token, csrf, user = result
        response = JSONResponse({"user": user.public(), "csrf_token": csrf, "permissions": store.settings(),
                                 "environment_label": settings.panel_environment_label, "mfa": mfa_state(user.id),
                                 "release": _release()})
        set_session_cookie(response, token)
        clear_mfa_cookie(response)
        return response

    # ------------------------------------------------- cadastro do segundo fator
    @router.get("/auth/mfa/status")
    async def mfa_status(request: Request) -> dict[str, object]:
        return mfa_state(authenticated(request, allow_mfa_setup=True).user.id)

    @router.post("/auth/mfa/setup")
    async def mfa_setup(request: Request) -> dict[str, object]:
        session = authenticated(request, allow_mfa_setup=True)
        body = await _json(request, MfaSetupBody)
        from rocha_reis_agent.panel import mfa as mfa_module
        cipher = mfa_module.require_cipher(ctx.mfa)  # type: ignore[arg-type]
        found = store.user_by_login(session.user.login)
        if found is None or not verify_password(found[1], body.password):  # type: ignore[attr-defined]
            store.audit(session.user.id, "mfa_setup_started", "user", session.user.id, "denied")
            raise PanelError(403, "Senha incorreta.")
        secret = mfa_module.new_secret()
        store.mfa_begin(session.user.id, cipher.encrypt(session.user.id, secret), cipher.key_id)  # type: ignore[attr-defined]
        # Única vez em que o segredo sai do servidor; nunca é registrado nem mostrado de novo.
        return {"secret": mfa_module.display_secret(secret), "account": session.user.login,
                "issuer": settings.panel_mfa_issuer,
                "uri": mfa_module.provisioning_uri(secret, account=session.user.login, issuer=settings.panel_mfa_issuer)}

    @router.post("/auth/mfa/activate")
    async def mfa_activate(request: Request) -> Response:
        session = authenticated(request, allow_mfa_setup=True)
        body = await _json(request, MfaCodeBody)
        from rocha_reis_agent.panel import mfa as mfa_module
        cipher = mfa_module.require_cipher(ctx.mfa)  # type: ignore[arg-type]
        row = store.mfa_row(session.user.id)
        if row is None or row["state"] != "pending":
            raise PanelError(409, "Não há cadastro pendente. Recomece o cadastro do segundo fator.")
        secret = cipher.decrypt(session.user.id, row["secret_blob"], row["key_id"])  # type: ignore[attr-defined]
        step = mfa_module.matching_step(secret, body.code or "")  # type: ignore[attr-defined]
        if step is None:
            store.audit(session.user.id, "mfa_enabled", "user", session.user.id, "failed")
            raise PanelError(422, "O código não confere. Confira o horário do celular e digite o código atual.")
        codes = mfa_module.new_recovery_codes()
        store.mfa_activate(session.user.id, step, [mfa_module.recovery_hash(session.user.id, c) for c in codes])
        token, csrf = store.rotate_session(session.token_hash, session.user.id,
                                           absolute_hours=settings.panel_session_absolute_hours, reason="mfa_enabled")
        refreshed = store.session(token, idle_minutes=settings.panel_session_idle_minutes)
        assert refreshed is not None
        response = JSONResponse({**session_payload(refreshed), "recovery_codes": codes})
        set_session_cookie(response, token)
        return response

    @router.post("/auth/mfa/recovery-codes")
    async def mfa_new_codes(request: Request) -> dict[str, object]:
        session = authenticated(request)
        body = await _json(request, MfaCodeBody)
        from rocha_reis_agent.panel import mfa as mfa_module
        cipher = mfa_module.require_cipher(ctx.mfa)  # type: ignore[arg-type]
        row = store.mfa_row(session.user.id)
        if row is None or row["state"] != "active":
            raise PanelError(409, "Ative o segundo fator antes.")
        secret = cipher.decrypt(session.user.id, row["secret_blob"], row["key_id"])  # type: ignore[attr-defined]
        step = mfa_module.matching_step(secret, body.code or "")  # type: ignore[attr-defined]
        if step is None or not store.mfa_consume_step(session.user.id, step):
            store.audit(session.user.id, "mfa_recovery_codes_regenerated", "user", session.user.id, "failed")
            raise PanelError(422, "Código inválido ou já utilizado.")
        codes = mfa_module.new_recovery_codes()
        store.mfa_replace_codes(session.user.id, [mfa_module.recovery_hash(session.user.id, c) for c in codes])
        return {"recovery_codes": codes}

    @router.post("/auth/mfa/disable")
    async def mfa_disable(request: Request) -> Response:
        session = authenticated(request)
        if settings.panel_mfa_required:
            raise PanelError(403, "Neste ambiente a verificação em duas etapas é obrigatória.")
        body = await _json(request, MfaDisableBody)
        from rocha_reis_agent.panel import mfa as mfa_module
        cipher = mfa_module.require_cipher(ctx.mfa)  # type: ignore[arg-type]
        found = store.user_by_login(session.user.login)
        row = store.mfa_row(session.user.id)
        secret = cipher.decrypt(session.user.id, row["secret_blob"], row["key_id"]) if row else None  # type: ignore[attr-defined]
        if (found is None or not verify_password(found[1], body.password)  # type: ignore[attr-defined]
                or secret is None or mfa_module.matching_step(secret, body.code) is None):  # type: ignore[attr-defined]
            store.audit(session.user.id, "mfa_disabled", "user", session.user.id, "denied")
            raise PanelError(403, "Senha ou código incorretos.")
        store.mfa_remove(session.user.id, actor_id=session.user.id, justification=None)  # encerra as sessões
        response = Response(status_code=204)
        response.delete_cookie(ctx.cookie, path="/", secure=settings.panel_cookie_secure, httponly=True, samesite="strict")
        return response

    @router.post("/auth/logout")
    async def logout(request: Request) -> Response:
        session = authenticated(request, allow_password_change=True, allow_mfa_setup=True)
        store.revoke_session(session.token_hash, actor_id=session.user.id)
        response = Response(status_code=204)
        response.delete_cookie(ctx.cookie, path="/", secure=settings.panel_cookie_secure, httponly=True, samesite="strict")
        clear_mfa_cookie(response)
        return response

    @router.get("/auth/session")
    async def current(request: Request) -> dict[str, object]:
        return session_payload(authenticated(request, allow_password_change=True, allow_mfa_setup=True))

    @router.post("/auth/password")
    async def change_password(request: Request) -> dict[str, object]:
        session = authenticated(request, allow_password_change=True, allow_mfa_setup=True)
        body = await _json(request, PasswordBody)
        found = store.user_by_login(session.user.login)
        if found is None or not verify_password(found[1], body.current_password):  # type: ignore[attr-defined]
            store.audit(session.user.id, "password_change", "user", session.user.id, "failed")
            raise PanelError(403, "Senha atual incorreta.")
        new = validate_password(body.new_password, session.user.login)  # type: ignore[attr-defined]
        if verify_password(found[1], new):
            raise PanelError(422, "A nova senha deve ser diferente da atual.")
        store.set_password(session.user.id, hash_password(new), must_change=False, actor_id=session.user.id,
                           keep_session=session.token_hash)
        # Fase 13: troca de credencial gera token NOVO (o anterior deixa de valer).
        token, _ = store.rotate_session(session.token_hash, session.user.id,
                                        absolute_hours=settings.panel_session_absolute_hours, reason="password")
        refreshed = store.session(token, idle_minutes=settings.panel_session_idle_minutes)
        assert refreshed is not None
        response = JSONResponse(session_payload(refreshed))
        set_session_cookie(response, token)
        return response

    # ----------------------------------------------------------- conversas
    @router.get("/overview")
    async def overview(request: Request) -> dict[str, object]:
        return store.overview(authenticated(request).user)

    @router.get("/conversations")
    async def conversations(request: Request, filter: str = "all", q: str | None = None,
                            cursor: str | None = None) -> dict[str, object]:
        session = authenticated(request)
        return store.list_conversations(session.user, filter_key=filter, query=q or None, cursor=cursor,
                                        page_size=settings.panel_page_size)

    @router.get("/conversations/{conversation_id}")
    async def conversation(request: Request, conversation_id: str) -> dict[str, object]:
        session = authenticated(request)
        summary = service.require_visible(session.user, conversation_id)
        contact = ctx.channel.contact_window(conversation_id)
        page = store.messages_page(conversation_id, limit=settings.panel_history_page_size)
        return {**summary, "messages": page["messages"], "has_more": page["has_more"], "before": page["before"],
                "transitions": store.transitions(conversation_id),
                "window": {"last_inbound_at": contact.last_inbound_at.isoformat() if contact else None,
                           "seconds": settings.channel_service_window_hours * 3600}}

    @router.get("/conversations/{conversation_id}/messages")
    async def older_messages(request: Request, conversation_id: str, before: int) -> dict[str, object]:
        session = authenticated(request)
        service.require_visible(session.user, conversation_id)
        return store.messages_page(conversation_id, before=before, limit=settings.panel_history_page_size)

    @router.get("/notifications")
    async def notifications(request: Request) -> dict[str, object]:
        session = authenticated(request, touch=False)
        return {"items": await run_in_threadpool(store.notifications, session.user)}

    @router.post("/conversations/{conversation_id}/read", status_code=204)
    async def read(request: Request, conversation_id: str) -> None:
        session = authenticated(request)
        service.require_visible(session.user, conversation_id)
        store.mark_read(session.user.id, conversation_id)

    def action(name: str, function):  # type: ignore[no-untyped-def]
        @router.post(f"/conversations/{{conversation_id}}/{name}", status_code=204)
        async def handler(request: Request, conversation_id: str) -> None:
            session = authenticated(request)
            await run_in_threadpool(function, session.user, conversation_id)
        handler.__name__ = f"conversation_{name.replace('-', '_')}"

    action("assume", service.assume)
    action("resolve", service.resolve)
    action("resume", service.resume)
    action("request-human", service.request_human)

    @router.post("/conversations/{conversation_id}/transfer", status_code=204)
    async def transfer(request: Request, conversation_id: str) -> None:
        session = authenticated(request)
        body = await _json(request, TransferBody)
        await run_in_threadpool(service.transfer, session.user, conversation_id, body.target_user_id)  # type: ignore[attr-defined]

    @router.post("/conversations/{conversation_id}/reply")
    async def reply(request: Request, conversation_id: str) -> dict[str, object]:
        session = authenticated(request)
        body = await _json(request, ReplyBody)
        return await run_in_threadpool(service.reply, session.user, conversation_id,
                                       body.text, body.idempotency_key)  # type: ignore[attr-defined]

    @router.get("/operators")
    async def operators(request: Request) -> list[dict[str, object]]:
        authenticated(request)
        return store.active_operators()

    # -------------------------------------------------------------- administração
    @router.get("/users")
    async def users(request: Request) -> list[dict[str, object]]:
        admin(request)
        return store.users()

    @router.post("/users", status_code=201)
    async def create_user(request: Request) -> dict[str, object]:
        session = admin(request)
        body = await _json(request, UserBody)
        return await run_in_threadpool(lambda: service.create_user(
            session.user, login=body.login, display_name=body.display_name,  # type: ignore[attr-defined]
            role=body.role, password=body.password))  # type: ignore[attr-defined]

    @router.post("/users/{user_id}/deactivate", status_code=204)
    async def deactivate(request: Request, user_id: str) -> None:
        # Atendimentos em andamento da pessoa voltam à fila (cada um auditado como conversation_requeued).
        session = admin(request)
        await run_in_threadpool(service.set_active, session.user, user_id, False)

    @router.post("/users/{user_id}/activate", status_code=204)
    async def activate(request: Request, user_id: str) -> None:
        service.set_active(admin(request).user, user_id, True)

    @router.post("/users/{user_id}/mfa-reset", status_code=204)
    async def mfa_reset(request: Request, user_id: str) -> None:
        """Redefinição administrativa: justificativa, confirmação com o login, autor com 2º fator."""
        session = admin(request)
        body = await _json(request, MfaResetBody)
        target = store.user(user_id)
        if target is None:
            raise PanelError(404, "Funcionário não encontrado.")
        if target.id == session.user.id:
            raise PanelError(409, "Use a sua própria página de segurança para alterar o seu segundo fator.")
        if settings.panel_mfa_required and not store.mfa_active(session.user.id):
            raise PanelError(403, "Ative o seu segundo fator antes de redefinir o de outra pessoa.")
        if body.confirmation != f"REDEFINIR:{target.login}":  # type: ignore[attr-defined]
            store.audit(session.user.id, "mfa_reset_by_admin", "user", target.id, "denied")
            raise PanelError(409, f"Confirmação incorreta. Digite exatamente: REDEFINIR:{target.login}")
        store.mfa_remove(target.id, actor_id=session.user.id, justification=body.justification.strip())  # type: ignore[attr-defined]

    @router.post("/users/{user_id}/reset-password", status_code=204)
    async def reset_password(request: Request, user_id: str) -> None:
        session = admin(request)
        body = await _json(request, ResetBody)
        await run_in_threadpool(service.reset_password, session.user, user_id, body.password)  # type: ignore[attr-defined]

    @router.get("/audit")
    async def audit(request: Request, before: int | None = None) -> list[dict[str, object]]:
        admin(request)
        return store.audit_entries(before=before)

    @router.get("/settings")
    async def get_settings(request: Request) -> dict[str, object]:
        admin(request)
        return {"permissions": store.settings(), "dispatch_halted": ctx.channel.halt_status(),
                "transport": settings.whatsapp_transport, "environment": settings.app_env,
                "environment_label": settings.panel_environment_label}

    @router.put("/settings", status_code=204)
    async def put_settings(request: Request) -> None:
        session = admin(request)
        body = await _json(request, SettingsBody)
        values = {k: v for k, v in body.model_dump().items() if v is not None}
        store.update_settings(values, actor_id=session.user.id)

    from rocha_reis_agent.persistence.storage_security import volume_protection

    def _backup_health(config: Settings) -> dict[str, object] | None:
        from rocha_reis_agent.ops.backup_job import STATUS_FILE, backup_health
        if config.storage_path is None:
            return None
        profile = config.storage_path.parent.parent  # perfis: <perfil>/dados/<banco>
        return backup_health(profile) if (profile / STATUS_FILE).is_file() else None

    @router.get("/system")
    async def system(request: Request) -> dict[str, object]:
        admin(request)
        from rocha_reis_agent import config as config_module
        return {**store.system_status(), "environment": settings.app_env,
                "environment_label": settings.panel_environment_label,
                "transport": settings.whatsapp_transport, "ai_provider": settings.ai_provider,
                "dispatch_halted": ctx.channel.halt_status(),
                # Somente leitura: travas de código. A interface NÃO pode alterá-las.
                "locks": {"meta_graph": config_module.GRAPH_TRANSPORT_ACTIVATION_AUTHORIZED,
                          "meta_homologation": config_module.HOMOLOGATION_TRANSPORT_ACTIVATION_AUTHORIZED,
                          "gemini_real": config_module.GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED,
                          "real_data": config_module.REAL_DATA_ACTIVATION_AUTHORIZED},
                # Fase 13: saúde do backup automático (datas, contagens e resultado; sem caminhos/segredos).
                "backup": _backup_health(settings), "release": _release(),
                # Fase 12: ambiente e proteção em repouso CONSTATADA (dm-crypt), sem caminhos ou segredos.
                "storage": {"environment_profile": settings.environment_profile or "desenvolvimento (sem marcação)",
                            "data_classification": settings.data_classification,
                            "protection": volume_protection(settings.storage_path.parent).describe()
                            if settings.storage_path else "não se aplica"}}

    @router.get("/retired-contacts")
    async def retired_contacts(request: Request) -> list[dict[str, object]]:
        admin(request)
        return store.retired_contacts()

    @router.post("/retired-contacts/{conversation_id}/reinstate", status_code=204)
    async def reinstate(request: Request, conversation_id: str) -> None:
        session = admin(request)
        body = await _json(request, ReinstateBody)
        if body.confirmation != f"REATIVAR:{conversation_id[-6:]}":  # type: ignore[attr-defined]
            store.audit(session.user.id, "contact_reinstated", "conversation", conversation_id, "denied")
            raise PanelError(409, "Confirmação incorreta.")
        try:
            await run_in_threadpool(store.repository.reinstate, conversation_id)
        except StorageError:
            store.audit(session.user.id, "contact_reinstated", "conversation", conversation_id, "failed")
            raise PanelError(409, "Contato não encontrado ou não excluído.") from None
        store.audit(session.user.id, "contact_reinstated", "conversation", conversation_id, "success")

    @router.post("/dispatch/halt", status_code=204)
    async def halt(request: Request) -> None:
        session = admin(request)
        body = await _json(request, HaltBody)
        try:
            ctx.channel.halt(body.reason, set_by=f"panel:{session.user.id}")  # type: ignore[attr-defined]
        except StorageError:
            raise PanelError(422, "Informe o motivo da interrupção.") from None
        store.audit(session.user.id, "dispatch_halted", "dispatch", None, "success")

    @router.post("/dispatch/resume", status_code=204)
    async def resume_dispatch(request: Request) -> None:
        session = admin(request)
        body = await _json(request, ResumeBody)
        try:
            ctx.channel.resume_dispatch(confirmation=body.confirmation, set_by=f"panel:{session.user.id}")  # type: ignore[attr-defined]
        except StorageError:
            store.audit(session.user.id, "dispatch_resumed", "dispatch", None, "denied")
            raise PanelError(409, "Retomada não confirmada ou envios já liberados.") from None
        store.audit(session.user.id, "dispatch_resumed", "dispatch", None, "success")

    # --------------------------------------------------------- conhecimento (ADMIN)
    def knowledge():  # type: ignore[no-untyped-def]
        if ctx.knowledge is None:
            raise PanelError(409, "Neste ambiente as informações do salão vêm de um arquivo fixo e não podem ser "
                                "editadas pelo painel. Suporte técnico: use KNOWLEDGE_SOURCE=database.")
        return ctx.knowledge

    @router.get("/knowledge")
    async def knowledge_overview(request: Request) -> dict[str, object]:
        return knowledge().overview(admin(request).user)  # type: ignore[attr-defined]

    @router.get("/knowledge/history")
    async def knowledge_history(request: Request, fact_id: str) -> list[dict[str, object]]:
        return knowledge().history(admin(request).user, fact_id)  # type: ignore[attr-defined]

    @router.post("/knowledge/drafts", status_code=201)
    async def knowledge_draft(request: Request) -> dict[str, object]:
        session = admin(request)
        body = await _json(request, DraftBody)
        return await run_in_threadpool(lambda: knowledge().draft(  # type: ignore[attr-defined]
            session.user, kind=body.kind, key=body.key, fields=body.fields, source=body.source,  # type: ignore[attr-defined]
            expected_version=body.expected_version))  # type: ignore[attr-defined]

    def revision_action(name: str) -> None:
        @router.post(f"/knowledge/{name}")
        async def handler(request: Request) -> dict[str, object]:
            session = admin(request)
            body = await _json(request, RevisionBody)
            return await run_in_threadpool(getattr(knowledge(), name), session.user, body.fact_id, body.version)  # type: ignore[attr-defined]
        handler.__name__ = f"knowledge_{name}"

    for action_name in ("approve", "reject", "revoke"):
        revision_action(action_name)

    @router.post("/knowledge/preview")
    async def knowledge_preview(request: Request) -> dict[str, object]:
        session = admin(request)
        body = await _json(request, PreviewBody)
        return knowledge().preview(session.user, body.question)  # type: ignore[attr-defined]

    @router.get("/ai/usage")
    async def ai_usage(request: Request) -> dict[str, object]:
        admin(request)
        from rocha_reis_agent import config as config_module
        return {"provider": settings.ai_provider, "model": settings.ai_model,
                "prompt_version": settings.ai_prompt_version or "ativa do registro",
                "real_provider_code_lock": config_module.GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED,
                "limits": {"requests_per_day": settings.ai_max_requests_per_day,
                           "cost_usd_per_day": settings.ai_max_cost_usd_per_day,
                           "max_output_tokens": settings.ai_max_output_tokens},
                "usage_last_24h": ctx.ledger.summary() if ctx.ledger is not None else None}  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ SSE
    @router.get("/events")
    async def events(request: Request, cursor: int | None = None) -> StreamingResponse:
        session = authenticated(request, touch=False)
        token = request.cookies.get(ctx.cookie)
        header = request.headers.get("last-event-id")
        start = int(header) if header and header.isdigit() else cursor if cursor is not None else store.latest_change()

        async def stream():  # type: ignore[no-untyped-def]
            position, started, pinged = start, monotonic(), monotonic()
            yield f"retry: 3000\nid: {position}\nevent: ready\ndata: {json.dumps({'cursor': position})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                if monotonic() - started > settings.panel_sse_max_seconds:
                    yield "event: rotate\ndata: {}\n\n"  # cliente reconecta e revalida a sessão
                    break
                current = await run_in_threadpool(store.session, token,
                                                  idle_minutes=settings.panel_session_idle_minutes, touch=False)
                if current is None or current.user.id != session.user.id:
                    yield "event: auth\ndata: {}\n\n"  # sessão expirada/revogada: encerra o stream
                    break
                change = await run_in_threadpool(store.changes_since, current.user, position)
                if change["reset"]:
                    position = change["cursor"]  # type: ignore[assignment]
                    yield f"id: {position}\nevent: reset\ndata: {{}}\n\n"
                elif change["cursor"] != position:
                    position = change["cursor"]  # type: ignore[assignment]
                    payload = {"conversations": change["conversations"], "other": change["other"]}
                    yield f"id: {position}\nevent: changes\ndata: {json.dumps(payload)}\n\n"
                elif monotonic() - pinged > 15:
                    pinged = monotonic()
                    yield ": ping\n\n"
                await asyncio.sleep(settings.panel_sse_poll_seconds)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    return router
