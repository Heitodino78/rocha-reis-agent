"""API do painel: sessão por cookie HttpOnly, CSRF por token + Origin, RBAC no servidor, SSE."""

import asyncio
from dataclasses import dataclass
from hashlib import sha256
import hmac
import json
from time import monotonic

from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from rocha_reis_agent.config import Settings
from rocha_reis_agent.domain.contracts import StorageError
from rocha_reis_agent.panel.security import PanelError, hash_password, normalize_login, validate_password, verify_password
from rocha_reis_agent.panel.service import PanelService
from rocha_reis_agent.panel.store import PanelSession, SQLitePanelStore
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
    operators_can_transfer: bool | None = None
    operators_can_resume: bool | None = None


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

    @property
    def cookie(self) -> str:
        # Prefixo __Host- exige Secure: usado sempre que o cookie for Secure.
        return "__Host-rr_panel" if self.settings.panel_cookie_secure else "rr_panel"


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

    def authenticated(request: Request, *, allow_password_change: bool = False, touch: bool = True) -> PanelSession:
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
        return session

    def admin(request: Request) -> PanelSession:
        session = authenticated(request)
        if session.user.role != "ADMIN":
            store.audit(session.user.id, "admin_access", "user", None, "denied")
            raise PanelError(403, "Ação reservada a administradores.")
        return session

    def session_payload(session: PanelSession) -> dict[str, object]:
        return {"user": session.user.public(), "csrf_token": session.csrf_token, "permissions": store.settings()}

    # ---------------------------------------------------------- autenticação
    @router.post("/auth/login")
    async def login(request: Request) -> Response:
        check_origin(request)
        body = await _json(request, LoginBody)
        try:
            login_name = normalize_login(body.login)  # type: ignore[attr-defined]
        except PanelError:
            login_name = "invalid"
        client = request.client.host if request.client else "unknown"
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
            previous = request.cookies.get(ctx.cookie)
            if previous:  # proteção contra fixação: a sessão anterior nunca é reaproveitada
                old = store.session(previous, idle_minutes=settings.panel_session_idle_minutes)
                if old is not None:
                    store.revoke_session(old.token_hash, actor_id=old.user.id)
            token, csrf = store.create_session(found[0].id, absolute_hours=settings.panel_session_absolute_hours)
            return token, csrf, found[0].public()

        result = await run_in_threadpool(attempt)
        if result is None:
            raise PanelError(401, "Login ou senha inválidos.")
        token, csrf, user = result
        response = JSONResponse({"user": user, "csrf_token": csrf, "permissions": store.settings()})
        response.set_cookie(ctx.cookie, token, httponly=True, secure=settings.panel_cookie_secure, samesite="strict",
                            path="/", max_age=settings.panel_session_absolute_hours * 3600)
        return response

    @router.post("/auth/logout")
    async def logout(request: Request) -> Response:
        session = authenticated(request, allow_password_change=True)
        store.revoke_session(session.token_hash, actor_id=session.user.id)
        response = Response(status_code=204)
        response.delete_cookie(ctx.cookie, path="/", secure=settings.panel_cookie_secure, httponly=True, samesite="strict")
        return response

    @router.get("/auth/session")
    async def current(request: Request) -> dict[str, object]:
        return session_payload(authenticated(request, allow_password_change=True))

    @router.post("/auth/password")
    async def change_password(request: Request) -> dict[str, object]:
        session = authenticated(request, allow_password_change=True)
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
        refreshed = store.session(request.cookies.get(ctx.cookie), idle_minutes=settings.panel_session_idle_minutes)
        assert refreshed is not None
        return session_payload(refreshed)

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
        return {**summary, "messages": store.messages(conversation_id), "transitions": store.transitions(conversation_id),
                "window": {"last_inbound_at": contact.last_inbound_at.isoformat() if contact else None,
                           "seconds": settings.channel_service_window_hours * 3600}}

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
        service.set_active(admin(request).user, user_id, False)

    @router.post("/users/{user_id}/activate", status_code=204)
    async def activate(request: Request, user_id: str) -> None:
        service.set_active(admin(request).user, user_id, True)

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
                "transport": settings.whatsapp_transport, "environment": settings.app_env}

    @router.put("/settings", status_code=204)
    async def put_settings(request: Request) -> None:
        session = admin(request)
        body = await _json(request, SettingsBody)
        values = {k: v for k, v in body.model_dump().items() if v is not None}
        store.update_settings(values, actor_id=session.user.id)

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
            raise PanelError(409, "Conhecimento em arquivo: a gestão pelo painel exige KNOWLEDGE_SOURCE=database.")
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
                "real_provider_code_lock": config_module.AI_REAL_PROVIDER_ACTIVATION_AUTHORIZED,
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
