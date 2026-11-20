"""Fase 13 — segundo fator (TOTP): cadastro, login em duas etapas, sessão parcial inexistente,
replay, limites, expiração, recuperação, redefinição administrativa, chave e rotação, logs."""

import base64
from contextlib import closing
from datetime import timedelta
import logging
import os
from pathlib import Path
import sqlite3

import pytest

from panel_support import ORIGIN, PASSWORD, Panel, add_user, create_panel_app, panel_settings
from rocha_reis_agent.config import ConfigurationError
from rocha_reis_agent.domain.conversations import utc_now
from rocha_reis_agent.ops.backup import generate_key
from rocha_reis_agent.panel import mfa


class Clock:
    def __init__(self) -> None:
        self.value = 1_900_000_000.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(mfa, "_now", fake)
    return fake


def keys_dir(tmp_path: Path) -> Path:
    """Chaves FORA do diretório do banco (o banco de teste fica na raiz de tmp_path)."""
    return tmp_path.parent / f"{tmp_path.name}-chaves"


def make_app(tmp_path: Path, **changes):
    key = keys_dir(tmp_path) / "mfa.key"
    if not key.exists():
        generate_key(key)
    app = create_panel_app(panel_settings(tmp_path, panel_mfa_key_file=key, **changes))
    return app


@pytest.fixture
def env(tmp_path, clock):
    app = make_app(tmp_path)
    ids = {login: add_user(app, login, role) for login, role in
           (("admin.teste", "ADMIN"), ("ana.teste", "OPERATOR"), ("bia.teste", "OPERATOR"))}
    return app, ids, tmp_path


def logged(app, login):
    panel = Panel(app)
    assert panel.login(login).status_code == 200
    return panel


def secret_of(setup: dict) -> bytes:
    raw = setup["secret"].replace(" ", "")
    return base64.b32decode(raw + "=" * (-len(raw) % 8))


def enroll(panel: Panel, clock: Clock) -> tuple[bytes, list[str]]:
    setup = panel.post("/auth/mfa/setup", {"password": PASSWORD}).json()
    secret = secret_of(setup)
    response = panel.post("/auth/mfa/activate", {"code": mfa.code_for(secret)})
    assert response.status_code == 200, response.text
    panel.csrf = response.json()["csrf_token"]  # sessão rotacionada
    clock.advance(30)
    return secret, response.json()["recovery_codes"]


def password_step(app, login: str) -> Panel:
    panel = Panel(app)
    response = panel.login(login)
    assert response.status_code == 200 and response.json() == {"mfa_required": True}
    return panel


def second_step(panel: Panel, **body):
    response = panel.client.post("/api/panel/auth/mfa", json=body, headers={"Origin": ORIGIN})
    if response.status_code == 200:
        panel.csrf = response.json()["csrf_token"]
    return response


# 1, 2, 15 — cadastro, confirmação e proteção do segredo
def test_enrollment_requires_password_and_valid_code_and_never_stores_secret_in_clear(env, clock, caplog):
    app, ids, tmp_path = env
    caplog.set_level(logging.DEBUG)
    ana = logged(app, "ana.teste")
    assert ana.post("/auth/mfa/setup", {"password": "errada-123456"}).status_code == 403
    setup = ana.post("/auth/mfa/setup", {"password": PASSWORD}).json()
    assert setup["uri"].startswith("otpauth://totp/") and "secret=" in setup["uri"]
    secret = secret_of(setup)
    assert ana.get("/auth/mfa/status").json()["pending"] is True
    assert ana.post("/auth/mfa/activate", {"code": "000000"}).status_code == 422
    assert ana.get("/auth/mfa/status").json()["enabled"] is False
    old_cookie = ana.client.cookies.get("rr_panel")
    response = ana.post("/auth/mfa/activate", {"code": mfa.code_for(secret)})
    assert response.status_code == 200 and len(response.json()["recovery_codes"]) == 10
    ana.csrf = response.json()["csrf_token"]
    assert ana.client.cookies.get("rr_panel") != old_cookie  # sessão rotacionada ao elevar o nível
    stale = Panel(app)
    stale.client.cookies.set("rr_panel", old_cookie)
    assert stale.get("/auth/session").status_code == 401
    # Segredo e códigos nunca em claro no banco (arquivo e WAL) nem nos logs.
    database = app.state.conversation_repository.path
    raw = b"".join(p.read_bytes() for p in database.parent.glob(database.name + "*") if p.is_file())
    for value in (secret, setup["secret"].replace(" ", "").encode(), *(c.encode() for c in response.json()["recovery_codes"])):
        assert value not in raw
    assert setup["secret"].replace(" ", "") not in caplog.text
    assert all(code not in caplog.text for code in response.json()["recovery_codes"])
    # Depois de ativo, o segredo não é mais entregue (status não o contém; novo setup é recusado).
    assert "secret" not in ana.get("/auth/mfa/status").json()
    assert ana.post("/auth/mfa/setup", {"password": PASSWORD}).status_code == 409


# 3, 4, 8, 9 — login em duas etapas; etapa intermediária não é sessão
def test_two_step_login_and_partial_state_reaches_nothing(env, clock):
    app, ids, tmp_path = env
    secret, _ = enroll(logged(app, "ana.teste"), clock)
    partial = password_step(app, "ana.teste")
    assert partial.client.cookies.get("rr_panel_mfa") and not partial.client.cookies.get("rr_panel")
    for path in ("/overview", "/conversations", "/notifications", "/auth/session", "/events", "/auth/mfa/status"):
        assert partial.get(path).status_code == 401, path
    assert partial.post("/conversations/x/assume").status_code == 401
    assert second_step(partial, code="123456").status_code == 401
    response = second_step(partial, code=mfa.code_for(secret))
    assert response.status_code == 200 and response.json()["mfa"]["enabled"] is True
    assert not partial.client.cookies.get("rr_panel_mfa")
    assert partial.get("/overview").status_code == 200


# 5, 6 — expiração e reutilização
def test_challenge_expires_and_codes_cannot_be_reused(env, clock):
    app, ids, tmp_path = env
    secret, _ = enroll(logged(app, "ana.teste"), clock)
    first = password_step(app, "ana.teste")
    code = mfa.code_for(secret)
    assert second_step(first, code=code).status_code == 200
    again = password_step(app, "ana.teste")
    assert second_step(again, code=code).status_code == 401  # mesmo passo: replay recusado
    clock.advance(30)
    store = app.state.panel_store
    store.clock = lambda: utc_now() + timedelta(minutes=6)
    assert second_step(again, code=mfa.code_for(secret)).status_code == 401  # etapa expirada
    store.clock = utc_now
    late = password_step(app, "ana.teste")
    clock.advance(95)
    old = mfa.code_for(secret, now=clock.value - 95)
    assert second_step(late, code=old).status_code == 401  # fora da tolerância de ±30 s
    assert second_step(late, code=mfa.code_for(secret)).status_code == 200


# 7 — limite de tentativas
def test_attempt_limits_kill_the_challenge_and_throttle(env, clock):
    app, ids, tmp_path = env
    secret, _ = enroll(logged(app, "ana.teste"), clock)
    partial = password_step(app, "ana.teste")
    statuses = [second_step(partial, code=f"{n:06d}").status_code for n in range(5)]
    assert statuses == [401] * 5
    clock.advance(30)
    assert second_step(partial, code=mfa.code_for(secret)).status_code in (401, 429)  # etapa morta
    # O IP fica limitado também na 1ª etapa: recomeçar o login não dá novas tentativas.
    assert Panel(app).login("ana.teste").status_code == 429
    actions = [e for e in app.state.panel_store.audit_entries() if e["action"] == "mfa_failed"]
    assert len(actions) >= 5


# 13, 14 — recuperação
def test_recovery_codes_are_single_use_and_regeneration_needs_totp(env, clock):
    app, ids, tmp_path = env
    secret, codes = enroll(logged(app, "ana.teste"), clock)
    partial = password_step(app, "ana.teste")
    response = second_step(partial, recovery_code=codes[0].lower())
    assert response.status_code == 200 and response.json()["mfa"]["recovery_codes_left"] == 9
    reuse = password_step(app, "ana.teste")
    assert second_step(reuse, recovery_code=codes[0]).status_code == 401
    session = partial
    assert session.post("/auth/mfa/recovery-codes", {"code": "000000"}).status_code == 422
    new = session.post("/auth/mfa/recovery-codes", {"code": mfa.code_for(secret)}).json()["recovery_codes"]
    assert set(new).isdisjoint(codes)
    old = password_step(app, "ana.teste")
    assert second_step(old, recovery_code=codes[1]).status_code == 401  # antigos revogados
    assert second_step(old, recovery_code=new[0]).status_code == 200
    # Códigos de outra conta não servem.
    bia_secret, bia_codes = enroll(logged(app, "bia.teste"), clock)
    cross = password_step(app, "ana.teste")
    assert second_step(cross, recovery_code=bia_codes[0]).status_code == 401


# 10, 11, 12 — logout, revogação, usuário desativado
def test_logout_and_deactivation_end_everything(env, clock):
    app, ids, tmp_path = env
    secret, _ = enroll(logged(app, "ana.teste"), clock)
    partial = password_step(app, "ana.teste")
    session = second_step(partial, code=mfa.code_for(secret))
    assert session.status_code == 200
    assert partial.post("/auth/logout").status_code == 204
    assert partial.get("/overview").status_code == 401 and partial.get("/events").status_code == 401
    clock.advance(30)
    waiting = password_step(app, "ana.teste")
    admin = logged(app, "admin.teste")
    assert admin.post(f"/users/{ids['ana.teste']}/deactivate").status_code == 204
    assert second_step(waiting, code=mfa.code_for(secret)).status_code == 401


# política obrigatória
def test_required_policy_allows_only_enrollment_until_active(tmp_path, clock):
    app = make_app(tmp_path, panel_mfa_required=True)
    add_user(app, "ana.teste")
    ana = logged(app, "ana.teste")
    for path in ("/overview", "/conversations", "/notifications", "/events"):
        response = ana.get(path)
        assert response.status_code == 403 and "duas etapas" in response.json()["detail"], path
    assert ana.get("/auth/session").json()["mfa"] == {"enabled": False, "pending": False, "required": True,
                                                      "available": True, "recovery_codes_left": 0}
    enroll(ana, clock)
    assert ana.get("/overview").status_code == 200
    assert ana.post("/auth/mfa/disable", {"password": PASSWORD, "code": "123456"}).status_code == 403


def test_self_disable_when_optional_requires_password_and_code(env, clock):
    app, ids, tmp_path = env
    ana = logged(app, "ana.teste")
    secret, _ = enroll(ana, clock)
    assert ana.post("/auth/mfa/disable", {"password": PASSWORD, "code": "000000"}).status_code == 403
    assert ana.post("/auth/mfa/disable", {"password": PASSWORD, "code": mfa.code_for(secret)}).status_code == 204
    assert ana.get("/overview").status_code == 401  # sessões encerradas
    assert logged(app, "ana.teste").get("/overview").status_code == 200  # volta a ser só senha


# administração
def test_admin_sees_status_only_and_resets_with_justification(tmp_path, clock):
    app = make_app(tmp_path, panel_mfa_required=True)
    ids = {login: add_user(app, login, role) for login, role in
           (("admin.teste", "ADMIN"), ("adm2.teste", "ADMIN"), ("ana.teste", "OPERATOR"))}
    ana = logged(app, "ana.teste")
    secret, _ = enroll(ana, clock)
    admin = logged(app, "admin.teste")
    body = {"justification": "Celular da funcionária foi perdido (teste).", "confirmation": "REDEFINIR:ana.teste"}
    # Administrador sem o próprio segundo fator não redefine o de outros (política obrigatória).
    assert admin.post(f"/users/{ids['ana.teste']}/mfa-reset", body).status_code == 403
    enroll(admin, clock)
    users = admin.get("/users").json()
    assert {u["login"]: u["mfa_enabled"] for u in users}["ana.teste"] is True
    assert all(set(u) >= {"mfa_enabled"} and "secret" not in u and "secret_blob" not in str(u) for u in users)
    assert ana.post(f"/users/{ids['admin.teste']}/mfa-reset", body).status_code == 403  # atendente
    assert admin.post(f"/users/{ids['admin.teste']}/mfa-reset", body).status_code == 409  # a si mesmo
    assert admin.post(f"/users/{ids['ana.teste']}/mfa-reset", {**body, "confirmation": "sim"}).status_code == 409
    assert admin.post(f"/users/{ids['ana.teste']}/mfa-reset", {**body, "justification": "curta"}).status_code == 422
    assert admin.post(f"/users/{ids['ana.teste']}/mfa-reset", body).status_code == 204
    assert ana.get("/overview").status_code == 401  # sessões da funcionária encerradas
    with closing(sqlite3.connect(app.state.conversation_repository.path)) as connection:
        assert connection.execute("SELECT justification FROM panel_mfa_resets").fetchone() == (body["justification"],)
    outcomes = [(e["action"], e["outcome"]) for e in admin.get("/audit").json() if e["action"].startswith("mfa_reset")]
    assert ("mfa_reset_by_admin", "success") in outcomes and ("mfa_reset_by_admin", "denied") in outcomes
    again = logged(app, "ana.teste")
    assert again.get("/overview").status_code == 403  # precisa cadastrar de novo


# chave
def test_missing_or_wrong_key_fails_safely_and_rotation_works(tmp_path, clock):
    with pytest.raises(ConfigurationError, match="segundo fator"):
        create_panel_app(panel_settings(tmp_path, panel_mfa_key_file=keys_dir(tmp_path) / "nao-existe.key"))
    app = make_app(tmp_path)
    add_user(app, "ana.teste")
    secret, _ = enroll(logged(app, "ana.teste"), clock)
    key = keys_dir(tmp_path) / "mfa.key"
    new_key = generate_key(keys_dir(tmp_path) / "nova" / "mfa.key")
    old_cipher, new_cipher = mfa.MfaCipher.from_file(key), mfa.MfaCipher.from_file(new_key)
    wrong = mfa.MfaCipher.from_file(generate_key(keys_dir(tmp_path) / "outra" / "x.key"))
    with pytest.raises(mfa.MfaKeyError):
        mfa.rotate_key(app.state.panel_store, wrong, new_cipher)  # chave antiga errada: nada muda
    assert app.state.panel_store.mfa_row(app.state.panel_store.user_by_login("ana.teste")[0].id)["key_id"] == old_cipher.key_id
    assert mfa.rotate_key(app.state.panel_store, old_cipher, new_cipher) == 1
    rotated = create_panel_app(panel_settings(tmp_path, panel_mfa_key_file=new_key))
    assert second_step(password_step(rotated, "ana.teste"), code=mfa.code_for(secret)).status_code == 200
    stale = create_panel_app(panel_settings(tmp_path, panel_mfa_key_file=key))  # chave antiga após rotação
    clock.advance(30)
    assert second_step(password_step(stale, "ana.teste"), code=mfa.code_for(secret)).status_code == 503


def test_key_must_be_private_and_not_next_to_the_database(tmp_path):
    key = generate_key(keys_dir(tmp_path) / "mfa.key")
    os.chmod(key, 0o644)
    with pytest.raises(ConfigurationError, match="privado"):
        create_panel_app(panel_settings(tmp_path, panel_mfa_key_file=key))
    with pytest.raises(ValueError, match="junto do banco"):
        panel_settings(tmp_path, panel_mfa_key_file=tmp_path / "chaves" / "mfa.key")
    with pytest.raises(ValueError, match="PANEL_MFA_KEY_FILE"):
        panel_settings(tmp_path, panel_mfa_required=True)


# CSRF e Origin nas rotas novas
def test_mfa_routes_require_csrf_and_origin(env, clock):
    app, ids, tmp_path = env
    ana = logged(app, "ana.teste")
    assert ana.client.post("/api/panel/auth/mfa/setup", json={"password": PASSWORD},
                           headers={"Origin": ORIGIN}).status_code == 403
    assert ana.client.post("/api/panel/auth/mfa/setup", json={"password": PASSWORD},
                           headers={**ana.headers(), "Origin": "http://evil.example"}).status_code == 403
    secret, _ = enroll(ana, clock)
    partial = password_step(app, "ana.teste")
    assert partial.client.post("/api/panel/auth/mfa", json={"code": mfa.code_for(secret)},
                               headers={"Origin": "http://evil.example"}).status_code == 403


def test_password_change_rotates_session_token(env, clock):
    app, ids, tmp_path = env
    ana = logged(app, "ana.teste")
    old = ana.client.cookies.get("rr_panel")
    response = ana.post("/auth/password", {"current_password": PASSWORD, "new_password": "Nova-Senha-Teste-2026!"})
    assert response.status_code == 200 and ana.client.cookies.get("rr_panel") != old
    stale = Panel(app)
    stale.client.cookies.set("rr_panel", old)
    assert stale.get("/overview").status_code == 401
