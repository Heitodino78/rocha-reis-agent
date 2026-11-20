"""Fase 13 — Chromium: verificação em duas etapas no painel (desktop e celular), cadastro,
login em duas etapas, código errado, código de recuperação, política obrigatória, etapa
intermediária sem acesso, redefinição administrativa e acessibilidade básica."""

import base64
import re

import pytest
from playwright.sync_api import Page, expect, sync_playwright

from harness import PASSWORD, PanelServer
from rocha_reis_agent.ops.backup import generate_key
from rocha_reis_agent.panel import mfa
from test_phase10_browser import A11Y
from test_phase11_browser import CONTRAST, page_for

pytestmark = pytest.mark.filterwarnings("ignore::ResourceWarning")


class Clock:
    def __init__(self) -> None:
        self.value = 1_900_000_000.0

    def __call__(self) -> float:
        return self.value


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(mfa, "_now", fake)  # o servidor de teste roda neste processo
    return fake


def start(tmp_path, **changes):
    key = generate_key(tmp_path.parent / f"{tmp_path.name}-chaves" / "mfa.key")
    server = PanelServer(tmp_path, panel_mfa_key_file=key, **changes)
    server.__enter__()
    server.ids = {"admin": server.user("admin.teste", "ADMIN", name="Admin Teste"),
                  "ana": server.user("ana.teste", name="Ana Atendente")}
    return server


@pytest.fixture
def server(tmp_path, clock):
    srv = start(tmp_path)
    yield srv
    srv.__exit__(None, None, None)


@pytest.fixture
def strict_server(tmp_path, clock):
    srv = start(tmp_path, panel_mfa_required=True)
    yield srv
    srv.__exit__(None, None, None)


def password_login(page: Page, name: str) -> None:
    page.fill("#login-input", name)
    page.fill("#password-input", PASSWORD)
    page.press("#password-input", "Enter")


def enroll_in_page(page: Page, clock: Clock) -> tuple[bytes, list[str]]:
    page.fill("#mfa-setup-password", PASSWORD)
    page.get_by_role("button", name="Começar").click()
    expect(page.locator(".mfa-secret")).to_have_text(re.compile(r"^([A-Z2-7]{4} ){7}[A-Z2-7]{4}$"))
    secret_text = page.locator(".mfa-secret").inner_text().replace(" ", "")
    secret = base64.b32decode(secret_text + "=" * (-len(secret_text) % 8))
    page.fill("#mfa-setup-code", mfa.code_for(secret))
    page.get_by_role("button", name="Ativar", exact=True).click()
    expect(page.locator(".recovery-codes li")).to_have_count(10)
    codes = page.locator(".recovery-codes li").all_inner_texts()
    page.get_by_role("button", name="Já guardei os códigos").click()
    clock.value += 30
    return secret, codes


def logout(page: Page, mobile: bool = False) -> None:
    if mobile:
        page.locator("#menu-toggle").tap()
    page.locator(".sidebar [data-action='logout']").click()
    expect(page.locator("#login-view")).to_be_visible()


@pytest.mark.parametrize("mobile", [False, True], ids=["desktop", "celular"])
def test_enroll_then_two_step_login_with_wrong_and_right_codes(browser, server, clock, mobile):
    page = page_for(browser, server, width=390 if mobile else 1366, height=844 if mobile else 860, mobile=mobile)
    password_login(page, "ana.teste")
    expect(page.locator("#app-view")).to_be_visible()
    if mobile:
        page.locator("#menu-toggle").tap()
    page.locator(".nav-item[data-view='account']").click()
    expect(page.locator("#mfa-status")).to_contain_text("Desativada")
    page.get_by_role("button", name="Ativar verificação em duas etapas").click()
    secret, codes = enroll_in_page(page, clock)
    expect(page.locator("#mfa-status")).to_contain_text("Ativada")
    assert page.locator(".mfa-secret").count() == 0  # o segredo some da tela depois da ativação
    assert page.evaluate(A11Y) == [] and page.evaluate(CONTRAST) == []
    logout(page, mobile)

    password_login(page, "ana.teste")
    expect(page.locator("#mfa-view")).to_be_visible()
    expect(page.locator("#mfa-code")).to_be_focused()
    status = page.evaluate("async () => (await fetch('/api/panel/overview')).status")
    assert status == 401  # etapa intermediária não é sessão
    assert page.evaluate(A11Y) == []
    page.fill("#mfa-code", "000000")
    page.get_by_role("button", name="Confirmar").click()
    expect(page.locator("#mfa-error")).to_have_text("Código inválido ou já utilizado.")
    page.fill("#mfa-code", mfa.code_for(secret))
    page.get_by_role("button", name="Confirmar").click()
    expect(page.locator("#app-view")).to_be_visible()
    assert page.errors == []


def test_recovery_code_on_phone(browser, server, clock):
    page = page_for(browser, server, width=390, height=844, mobile=True)
    password_login(page, "ana.teste")
    expect(page.locator("#app-view")).to_be_visible()
    page.locator("#menu-toggle").tap()
    page.locator(".nav-item[data-view='account']").click()
    page.get_by_role("button", name="Ativar verificação em duas etapas").click()
    _, codes = enroll_in_page(page, clock)
    logout(page, mobile=True)
    password_login(page, "ana.teste")
    page.get_by_role("button", name="Perdi o celular: usar código de recuperação").click()
    page.fill("#mfa-recovery", codes[3].lower())
    page.get_by_role("button", name="Confirmar").click()
    expect(page.locator("#app-view")).to_be_visible()
    page.locator("#menu-toggle").tap()
    page.locator(".nav-item[data-view='account']").click()
    expect(page.locator("#mfa-status")).to_contain_text("restantes: 9")
    logout(page, mobile=True)
    password_login(page, "ana.teste")
    page.get_by_role("button", name="Perdi o celular: usar código de recuperação").click()
    page.fill("#mfa-recovery", codes[3])
    page.get_by_role("button", name="Confirmar").click()
    expect(page.locator("#mfa-error")).to_have_text("Código inválido ou já utilizado.")  # uso único
    assert page.errors == []


def test_required_policy_forces_enrollment_and_admin_resets_with_justification(browser, strict_server, clock):
    server = strict_server
    admin = page_for(browser, server)
    password_login(admin, "admin.teste")
    expect(admin.locator("#mfa-setup-view")).to_be_visible()  # nada além do cadastro
    status = admin.evaluate("async () => (await fetch('/api/panel/conversations')).status")
    assert status == 403
    assert admin.evaluate(A11Y) == []
    enroll_in_page(admin, clock)
    expect(admin.locator("#app-view")).to_be_visible()
    # Ana com segundo fator ativo (cadastrado direto, como se tivesse feito pelo celular).
    store, cipher = server.app.state.panel_store, server.app.state.mfa_cipher
    secret = mfa.new_secret()
    store.mfa_begin(server.ids["ana"], cipher.encrypt(server.ids["ana"], secret), cipher.key_id)
    store.mfa_activate(server.ids["ana"], 1, [])
    admin.locator(".nav-item[data-view='team']").click()
    row = admin.get_by_role("row", name=re.compile("Ana Atendente"))
    expect(row).to_contain_text("Ativa")
    row.get_by_role("button", name="Redefinir duas etapas").click()
    admin.fill("#reset-reason", "Celular perdido; identidade conferida pessoalmente (teste).")
    admin.fill("#reset-confirm", "REDEFINIR:ana.teste")
    admin.locator("#confirm-ok").click()
    expect(admin.locator(".toast", has_text="Segundo fator redefinido")).to_be_visible()
    expect(admin.get_by_role("row", name=re.compile("Ana Atendente"))).not_to_contain_text("Ativa")
    admin.locator(".nav-item[data-view='audit']").click()
    expect(admin.locator("#audit-body")).to_contain_text("Redefiniu 2 etapas de outra pessoa")
    ana = page_for(browser, server)
    password_login(ana, "ana.teste")
    expect(ana.locator("#mfa-setup-view")).to_be_visible()  # precisa cadastrar de novo
    assert admin.errors == [] and ana.errors == []
