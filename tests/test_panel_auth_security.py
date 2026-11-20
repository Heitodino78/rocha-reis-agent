"""Fase 7 — autenticação, sessões, CSRF, RBAC, IDOR e superfícies expostas."""

from contextlib import closing
from datetime import timedelta
import io
import logging
import sqlite3

import pytest
from fastapi.testclient import TestClient

from panel_support import (
    ORIGIN, PASSWORD, Panel, add_user, conversation_id, create_panel_app, customer, panel_settings,
)
from rocha_reis_agent.config import ConfigurationError
from rocha_reis_agent.main import create_app
from whatsapp_support import APP_SECRET, CONVERSATION_KEY, VERIFY, settings as channel_settings


@pytest.fixture
def app(tmp_path):
    application = create_panel_app(panel_settings(tmp_path))
    add_user(application, "admin.teste", "ADMIN", name="Admin Teste")
    add_user(application, "ana.teste", name="Ana Teste")
    add_user(application, "bia.teste", name="Bia Teste")
    return application


def db(app):
    return closing(sqlite3.connect(app.state.conversation_repository.path))


# ------------------------------------------------------------ A1: primeiro admin
def test_first_admin_is_created_only_once_with_hidden_validated_password(tmp_path, monkeypatch, capsys):
    from rocha_reis_agent.panel import admin_cli
    for name, value in {
        "APP_ENV": "test", "STORAGE_BACKEND": "sqlite", "STORAGE_PATH": str(tmp_path / "p.synthetic.sqlite3"),
        "WHATSAPP_ENABLED": "true", "WHATSAPP_VERIFY_TOKEN": VERIFY, "WHATSAPP_APP_SECRET": APP_SECRET,
        "WHATSAPP_CONVERSATION_KEY": CONVERSATION_KEY, "WHATSAPP_PHONE_NUMBER_ID": "100000000000001",
        "PANEL_ENABLED": "true", "PANEL_COOKIE_SECURE": "false",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr("sys.stdin", io.StringIO("curta\n"))
    assert admin_cli.main(["create-admin", "--login", "dona.teste", "--name", "Dona", "--password-stdin"]) == 2
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
    assert admin_cli.main(["create-admin", "--login", "dona.teste", "--name", "Dona", "--password-stdin"]) == 0
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
    assert admin_cli.main(["create-admin", "--login", "outra.teste", "--name", "Outra", "--password-stdin"]) == 2
    output = capsys.readouterr()
    assert PASSWORD not in output.out + output.err and "Já existem funcionários" in output.err
    with closing(sqlite3.connect(tmp_path / "p.synthetic.sqlite3")) as connection:
        (stored,) = connection.execute("SELECT password_hash FROM panel_users").fetchone()
    assert stored.startswith("$argon2id$") and PASSWORD not in stored


# --------------------------------------------------------------- A2–A10: sessão
def test_valid_login_sets_hardened_cookie_and_returns_csrf(app):
    panel = Panel(app)
    response = panel.login("ana.teste")
    assert response.status_code == 200 and len(response.json()["csrf_token"]) >= 32
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    assert "password_hash" not in response.text and PASSWORD not in response.text
    assert panel.get("/auth/session").json()["user"]["role"] == "OPERATOR"


@pytest.mark.parametrize("login,password", [("ninguem.teste", PASSWORD), ("ana.teste", "Senha-Errada-2026!"), ("", ""), ("ANA.TESTE ", "x")])
def test_invalid_login_has_single_generic_message(app, login, password):
    response = Panel(app).login(login, password)
    assert response.status_code in (401, 422) and response.json()["detail"] in ("Login ou senha inválidos.", "Dados inválidos.")


def test_login_requires_panel_origin(app):
    client = TestClient(app, base_url=ORIGIN)
    for headers in ({}, {"Origin": "https://evil.example"}):
        response = client.post("/api/panel/auth/login", json={"login": "ana.teste", "password": PASSWORD}, headers=headers)
        assert response.status_code == 403


def test_deactivated_user_is_rejected_and_sessions_are_revoked_immediately(app):
    ana, admin = Panel(app), Panel(app)
    ana.login("ana.teste"); admin.login("admin.teste")
    ana_id = ana.get("/auth/session").json()["user"]["id"]
    assert admin.post(f"/users/{ana_id}/deactivate").status_code == 204
    assert ana.get("/auth/session").status_code == 401
    assert Panel(app).login("ana.teste").status_code == 401
    assert admin.post(f"/users/{ana_id}/activate").status_code == 204
    assert Panel(app).login("ana.teste").status_code == 200


def test_logout_invalidates_session_server_side(app):
    panel = Panel(app)
    panel.login("ana.teste")
    token = panel.client.cookies.get("rr_panel")
    assert panel.post("/auth/logout").status_code == 204
    replay = TestClient(app, base_url=ORIGIN, cookies={"rr_panel": token})
    assert replay.get("/api/panel/auth/session").status_code == 401


def test_idle_and_absolute_expiration(app):
    store = app.state.panel_store
    panel = Panel(app)
    panel.login("ana.teste")
    real = store.clock
    store.clock = lambda: real() + timedelta(minutes=31)
    assert panel.get("/auth/session").status_code == 401  # ociosidade
    store.clock = real
    other = Panel(app)
    other.login("bia.teste")
    store.clock = lambda: real() + timedelta(hours=13)
    assert other.get("/auth/session").status_code == 401  # validade absoluta
    store.clock = real


def test_brute_force_is_throttled_even_with_correct_password(app):
    for _ in range(5):
        assert Panel(app).login("ana.teste", "Senha-Errada-2026!").status_code == 401
    response = Panel(app).login("ana.teste")
    assert response.status_code == 429 and "Muitas tentativas" in response.json()["detail"]
    # O limite por IP também protege contra rodízio de logins.
    for index in range(5):
        Panel(app).login(f"inexistente{index}.teste", "Senha-Errada-2026!")
    assert Panel(app).login("bia.teste").status_code == 429


def test_session_fixation_old_session_is_revoked_on_new_login(app):
    panel = Panel(app)
    panel.login("ana.teste")
    old = panel.client.cookies.get("rr_panel")
    panel.login("ana.teste")
    assert panel.client.cookies.get("rr_panel") != old
    assert TestClient(app, base_url=ORIGIN, cookies={"rr_panel": old}).get("/api/panel/auth/session").status_code == 401


@pytest.mark.parametrize("headers", [{}, {"Origin": ORIGIN}, {"Origin": ORIGIN, "X-CSRF-Token": "forjado"},
                                     {"Origin": "https://evil.example", "X-CSRF-Token": "PLACEHOLDER"}])
def test_csrf_protection_on_state_changing_requests(app, headers):
    panel = Panel(app)
    panel.login("admin.teste")
    if headers.get("X-CSRF-Token") == "PLACEHOLDER":
        headers = {**headers, "X-CSRF-Token": panel.csrf}
    response = panel.client.put("/api/panel/settings", json={"operators_can_resume": True}, headers=headers)
    assert response.status_code == 403
    assert panel.get("/settings").json()["permissions"]["operators_can_resume"] is False


def test_temporary_password_must_be_changed_and_change_revokes_other_sessions(app):
    admin = Panel(app)
    admin.login("admin.teste")
    created = admin.post("/users", {"login": "caio.teste", "display_name": "Caio", "role": "OPERATOR",
                                    "password": "Temporaria-Caio-2026"})
    assert created.status_code == 201 and created.json()["must_change_password"] is True
    caio, other = Panel(app), Panel(app)
    caio.login("caio.teste", "Temporaria-Caio-2026"); other.login("caio.teste", "Temporaria-Caio-2026")
    assert caio.get("/conversations").status_code == 403
    assert caio.post("/auth/password", {"current_password": "errada-errada-1", "new_password": "Nova-Senha-Caio-2026"}).status_code == 403
    assert caio.post("/auth/password", {"current_password": "Temporaria-Caio-2026", "new_password": "curta"}).status_code == 422
    ok = caio.post("/auth/password", {"current_password": "Temporaria-Caio-2026", "new_password": "Nova-Senha-Caio-2026"})
    assert ok.status_code == 200 and ok.json()["user"]["must_change_password"] is False
    assert caio.get("/conversations").status_code == 200
    assert other.get("/auth/session").status_code == 401


# ------------------------------------------------------------- B11–B18: RBAC/IDOR
def test_admin_and_operator_permissions_are_enforced_by_backend(app, tmp_path):
    admin, ana = Panel(app), Panel(app)
    admin.login("admin.teste"); ana.login("ana.teste")
    assert admin.get("/users").status_code == 200 and admin.get("/audit").status_code == 200
    for method, path in (("get", "/users"), ("get", "/audit"), ("get", "/settings")):
        assert getattr(ana, method)(path).status_code == 403
    assert ana.post("/users", {"login": "x.teste", "display_name": "X", "role": "ADMIN", "password": PASSWORD}).status_code == 403
    assert ana.put("/settings", {"operators_can_resume": True}).status_code == 403
    assert ana.post("/dispatch/halt", {"reason": "x"}).status_code == 403
    assert ana.get("/conversations").status_code == 200 and ana.get("/overview").status_code == 200
    assert any(e["action"] == "admin_access" and e["outcome"] == "denied" for e in admin.get("/audit").json())


def test_unauthenticated_requests_are_rejected(app):
    client = TestClient(app, base_url=ORIGIN)
    for path in ("/conversations", "/overview", "/users", "/events", "/conversations/wa-x"):
        assert client.get(f"/api/panel{path}").status_code == 401


def test_operator_cannot_reach_conversations_outside_scope_idor(app, tmp_path):
    customer(tmp_path, "wamid.auto", "Quanto custa o corte?")  # conversa AUTO: fora do escopo do atendente
    admin, ana, bia = Panel(app), Panel(app), Panel(app)
    admin.login("admin.teste"); ana.login("ana.teste"); bia.login("bia.teste")
    hidden = conversation_id(admin, "auto")
    assert ana.get("/conversations?filter=all").json()["items"] == []
    for response in (ana.get(f"/conversations/{hidden}"), ana.get("/conversations/wa-inexistente"),
                     ana.post(f"/conversations/{hidden}/assume"), ana.post(f"/conversations/{hidden}/read"),
                     ana.get("/conversations/..%2F..%2Fusers")):
        assert response.status_code == 404
    # Conversa assumida por Ana: Bia não vê nem age (transferir/responder).
    customer(tmp_path, "wamid.h1", "Quero falar com uma pessoa", "5511900000009")
    target = conversation_id(ana, "waiting")
    assert ana.post(f"/conversations/{target}/assume").status_code == 204
    bia_id = bia.get("/auth/session").json()["user"]["id"]
    assert bia.get(f"/conversations/{target}").status_code == 404
    assert bia.post(f"/conversations/{target}/transfer", {"target_user_id": bia_id}).status_code == 404
    assert bia.post(f"/conversations/{target}/reply", {"text": "oi", "idempotency_key": "k" * 32}).status_code == 404


def test_operator_cannot_transfer_or_resume_without_permission(app, tmp_path):
    customer(tmp_path, "wamid.h1", "Quero falar com uma pessoa")
    admin, ana = Panel(app), Panel(app)
    admin.login("admin.teste"); ana.login("ana.teste")
    cid = conversation_id(ana, "waiting")
    ana.post(f"/conversations/{cid}/assume")
    bia_id = next(u["id"] for u in admin.get("/users").json() if u["login"] == "bia.teste")
    admin.put("/settings", {"operators_can_transfer": False})
    assert ana.post(f"/conversations/{cid}/transfer", {"target_user_id": bia_id}).status_code == 403
    ana.post(f"/conversations/{cid}/resolve")
    assert ana.post(f"/conversations/{cid}/resume").status_code == 403
    assert ana.post(f"/conversations/{cid}/request-human").status_code == 403


def test_admin_cannot_deactivate_self_or_last_admin(app):
    admin = Panel(app)
    admin.login("admin.teste")
    me = admin.get("/auth/session").json()["user"]["id"]
    assert admin.post(f"/users/{me}/deactivate").status_code == 409


# ------------------------------------------------------------- G: superfícies
def test_security_headers_csp_and_trusted_host(app):
    client = TestClient(app, base_url=ORIGIN)
    response = client.get("/panel/")
    assert response.status_code == 200
    csp = response.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "unsafe-inline" not in csp
    assert response.headers["x-frame-options"] == "DENY" and response.headers["x-content-type-options"] == "nosniff"
    assert "<script>" not in response.text  # nenhum script inline
    assert TestClient(app, base_url="http://evil.example").get("/panel/").status_code == 400  # DNS rebinding
    assert client.get("/docs").status_code == 404 and client.get("/openapi.json").status_code == 404


def test_panel_and_webhook_apps_are_separate(app, tmp_path):
    client = TestClient(app, base_url=ORIGIN)
    assert client.post("/webhooks/whatsapp", content=b"{}").status_code in (404, 405)
    with TestClient(create_app(channel_settings(tmp_path)), base_url="http://testserver") as webhook:
        assert webhook.get("/panel/").status_code == 404
        assert webhook.post("/api/panel/auth/login", json={}).status_code == 404


@pytest.mark.parametrize("changes", [
    {"panel_origin": "http://192.168.0.10:8000"},           # http fora do loopback
    {"panel_origin": "https://painel.example", "panel_cookie_secure": False},
    {"storage_backend": "memory", "storage_path": None},
    {"panel_embedded_worker": True, "whatsapp_transport": "graph"},
])
def test_unsafe_panel_configuration_is_refused(tmp_path, changes):
    with pytest.raises(Exception):
        panel_settings(tmp_path, **changes)


def test_disabled_panel_does_not_start(tmp_path):
    with pytest.raises(ConfigurationError):
        create_panel_app(channel_settings(tmp_path))


def test_audit_is_append_only_and_contains_no_secrets(app):
    admin = Panel(app)
    admin.login("admin.teste")
    Panel(app).login("ana.teste", "Senha-Errada-2026!")
    with db(app) as connection:
        for sql in ("DELETE FROM panel_audit", "UPDATE panel_audit SET outcome='success'"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                connection.execute(sql)
        dump = "\n".join(connection.iterdump())
    token = admin.client.cookies.get("rr_panel")
    for private in (PASSWORD, "Senha-Errada", admin.csrf, token):
        assert private not in dump  # nem senha, nem token de sessão, nem CSRF em texto
    actions = {e["action"] for e in admin.get("/audit").json()}
    assert {"login", "login_failed", "user_created"} <= actions


def test_logs_never_contain_passwords_tokens_or_cookies(app, caplog):
    with caplog.at_level(logging.DEBUG):
        panel = Panel(app)
        panel.login("ana.teste")
        Panel(app).login("ana.teste", "Senha-Errada-2026!")
        panel.post("/auth/logout")
    text = caplog.text + "".join(str(r.__dict__) for r in caplog.records if not r.name.startswith("httpx"))
    for private in (PASSWORD, "Senha-Errada-2026!", panel.csrf):
        assert private not in text
