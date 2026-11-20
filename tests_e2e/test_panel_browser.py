"""Testes de navegador REAIS (Chromium headless) contra o painel em 127.0.0.1.

Executar com o venv de e2e (ver requirements-e2e.txt):
    /tmp/rocha-reis-e2e-venv/bin/python -m pytest -q -p no:cacheprovider tests_e2e
Somente rede local: qualquer requisição fora da origem do painel é abortada.
"""

import re

import pytest
from playwright.sync_api import Page, expect, sync_playwright

from harness import PASSWORD, PanelServer

pytestmark = pytest.mark.filterwarnings("ignore::ResourceWarning")


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def server(tmp_path, request):
    changes = getattr(request, "param", {})
    with PanelServer(tmp_path, **changes) as srv:
        srv.ids = {"admin": srv.user("admin.teste", "ADMIN", name="Admin Teste"),
                   "ana": srv.user("ana.teste", name="Ana Atendente"),
                   "bia": srv.user("bia.teste", name="Bia Atendente")}
        yield srv


def open_page(browser, server, *, mobile=False) -> Page:
    context = browser.new_context(viewport={"width": 390, "height": 844} if mobile else {"width": 1366, "height": 860},
                                  is_mobile=mobile, has_touch=mobile)
    page = context.new_page()
    page.errors = []
    page.on("pageerror", lambda error: page.errors.append(str(error)))
    page.on("console", lambda msg: page.errors.append(msg.text) if msg.type == "error" and "401" not in msg.text else None)
    page.route(re.compile(r"^(?!" + re.escape(server.origin) + ").*"), lambda route: route.abort())
    page.on("dialog", lambda dialog: page.errors.append(f"dialog inesperado: {dialog.message}"))
    page.goto(server.origin + "/panel/")
    return page


def login(page: Page, login_name: str, password: str = PASSWORD) -> None:
    page.fill("#login-input", login_name)
    page.fill("#password-input", password)
    page.press("#password-input", "Enter")  # teclado, sem mouse
    expect(page.locator("#app-view")).to_be_visible()


def live(page: Page) -> None:
    expect(page.locator("#connection-side")).to_have_text("Ao vivo", timeout=10000)


def test_login_errors_logout_and_keyboard(browser, server):
    page = open_page(browser, server)
    expect(page.locator("#login-input")).to_be_focused()
    page.fill("#login-input", "ana.teste"); page.fill("#password-input", "senha-errada-123")
    page.keyboard.press("Enter")
    expect(page.locator("#login-error")).to_have_text("Login ou senha inválidos.")
    login(page, "ana.teste")
    live(page)
    expect(page.locator("#user-name")).to_have_text("Ana Atendente")
    expect(page.locator(".admin-only").first).to_be_hidden()  # atendente não vê Equipe/Auditoria
    page.locator(".sidebar [data-action='logout']").click()
    expect(page.locator("#login-view")).to_be_visible()
    expect(page.locator("#login-error")).to_have_text("Você saiu do painel.")
    assert page.errors == []


def test_full_operator_flow_with_live_updates(browser, server):
    server.customer("wamid.b1", "Quero falar com uma pessoa, por favor")
    ana = open_page(browser, server)
    login(ana, "ana.teste"); live(ana)
    admin = open_page(browser, server)
    login(admin, "admin.teste"); live(admin)
    admin.locator(".nav-item[data-filter='all']").click()

    ana.locator(".conv").first.click()
    expect(ana.locator("#detail-state")).to_have_text("Aguardando atendimento")
    ana.get_by_role("button", name="Assumir atendimento").click()
    expect(ana.locator("#detail-owner")).to_have_text("Responsável: Ana Atendente")
    # Outro navegador vê a mudança de responsável sem recarregar.
    expect(admin.locator(".conv .tag")).to_have_text("Com Ana Atendente", timeout=10000)

    ana.fill("#composer-input", "Olá! Aqui é a Ana (atendente fictícia).")
    ana.locator("#composer-send").dblclick()  # clique duplo não duplica
    expect(ana.locator(".msg.operator")).to_have_count(1)
    expect(ana.locator(".msg.operator .delivery")).to_have_text(re.compile("Aceita pela plataforma"), timeout=10000)

    server.customer("wamid.b2", "Obrigada, Ana!")
    expect(ana.locator(".msg.client").last).to_have_text(re.compile("Obrigada, Ana!"), timeout=10000)  # sem refresh

    ana.get_by_role("button", name="Finalizar atendimento").click()
    expect(ana.locator("#confirm-dialog")).to_be_visible()
    ana.locator("#confirm-ok").click()
    expect(ana.locator("#detail-state")).to_have_text("Encerrado")
    expect(ana.get_by_role("button", name="Retomar automação")).to_have_count(0)  # atendente não retoma

    admin.locator(".conv").first.click()
    admin.get_by_role("button", name="Retomar automação").click()
    admin.locator("#confirm-ok").click()
    expect(admin.locator("#detail-state")).to_have_text("Automático")
    expect(admin.locator("#detail-automation")).to_have_text("· Automação ativa")
    assert ana.errors == [] and admin.errors == []


def test_transfer_moves_conversation_between_browsers(browser, server):
    server.customer("wamid.t1", "Quero um atendente")
    ana, bia = open_page(browser, server), open_page(browser, server)
    login(ana, "ana.teste"); login(bia, "bia.teste"); live(bia)
    ana.locator(".conv").first.click()
    ana.get_by_role("button", name="Assumir atendimento").click()
    expect(ana.locator("#detail-owner")).to_have_text("Responsável: Ana Atendente")
    ana.get_by_role("button", name="Transferir").click()
    ana.select_option("#transfer-target", label="Bia Atendente (Atendente)")
    ana.locator("#confirm-ok").click()
    expect(ana.locator("#detail")).to_be_hidden(timeout=10000)  # Ana perdeu o acesso
    bia.locator(".nav-item[data-filter='mine']").click()
    expect(bia.locator(".conv")).to_have_count(1, timeout=10000)
    bia.locator(".conv").first.click()
    expect(bia.locator("#detail-owner")).to_have_text("Responsável: Bia Atendente")
    expect(bia.locator("#composer-input")).to_be_enabled()


def test_customer_html_is_rendered_as_text_xss(browser, server):
    payload = '<img src=x onerror="document.title=\'pwned\'"><script>document.title="pwned"</script>'
    server.customer("wamid.x0", "Quero falar com uma pessoa")
    server.customer("wamid.x1", "Mensagem com HTML " + payload)  # pausa: fica como última mensagem
    page = open_page(browser, server)
    login(page, "ana.teste")
    expect(page.locator(".conv-preview").first).to_contain_text("<img src=x")
    page.locator(".conv").first.click()
    expect(page.locator(".msg.client .msg-text").last).to_contain_text("<script>")
    assert page.locator(".messages img, .messages script, .conversation-list img").count() == 0
    assert page.title() != "pwned" and page.errors == []


def test_mobile_flow_list_detail_back_and_menu(browser, server):
    server.customer("wamid.m1", "Quero falar com uma pessoa")
    page = open_page(browser, server, mobile=True)
    login(page, "ana.teste")
    expect(page.locator("#connection")).to_have_text("Ao vivo", timeout=10000)
    page.locator(".conv").first.tap()
    expect(page.locator("#list-pane")).to_be_hidden()
    page.get_by_role("button", name="Assumir atendimento").tap()
    page.fill("#composer-input", "Resposta pelo celular (fictícia).")
    page.locator("#composer-send").tap()
    expect(page.locator(".msg.operator")).to_have_count(1)
    page.locator("#back-btn").tap()
    expect(page.locator("#list-pane")).to_be_visible()
    page.locator("#menu-toggle").tap()
    expect(page.locator("#sidebar")).to_have_class(re.compile("open"))
    page.keyboard.press("Escape")
    expect(page.locator("#sidebar")).not_to_have_class(re.compile("open"))
    assert page.errors == []


def test_deactivated_operator_is_logged_out_live(browser, server):
    page = open_page(browser, server)
    login(page, "ana.teste"); live(page)
    admin = open_page(browser, server)
    login(admin, "admin.teste")
    admin.locator(".nav-item[data-view='team']").click()
    admin.get_by_role("row", name=re.compile("Ana Atendente")).get_by_role("button", name="Desativar").click()
    admin.locator("#confirm-ok").click()
    expect(page.locator("#login-view")).to_be_visible(timeout=10000)
    expect(page.locator("#login-error")).to_have_text("Sua sessão terminou. Entre novamente.")


@pytest.mark.parametrize("server", [{"panel_sse_max_seconds": 2.0}], indirect=True)
def test_stream_reconnects_and_resyncs(browser, server):
    server.customer("wamid.c0", "Quero falar com uma pessoa")
    page = open_page(browser, server)
    login(page, "admin.teste"); live(page)
    page.locator(".nav-item[data-filter='all']").click()
    page.wait_for_timeout(3500)  # o servidor rotacionou o stream ao menos uma vez
    server.customer("wamid.c1", "Quanto custa o corte?", "5511900000077")
    expect(page.locator(".conv")).to_have_count(2, timeout=10000)
    live(page)


def test_admin_creates_operator_who_must_change_password(browser, server):
    admin = open_page(browser, server)
    login(admin, "admin.teste")
    admin.locator(".nav-item[data-view='team']").click()
    admin.fill("#new-login", "caio.teste"); admin.fill("#new-name", "Caio Atendente")
    admin.fill("#new-user-password", "Temporaria-Caio-2026")
    admin.get_by_role("button", name="Criar acesso").click()
    expect(admin.get_by_role("row", name=re.compile("Caio Atendente"))).to_contain_text("senha temporária")
    caio = open_page(browser, server)
    caio.fill("#login-input", "caio.teste"); caio.fill("#password-input", "Temporaria-Caio-2026")
    caio.press("#password-input", "Enter")
    expect(caio.locator("#password-view")).to_be_visible()
    caio.fill("#current-password", "Temporaria-Caio-2026")
    caio.fill("#new-password", "Nova-Senha-Caio-2026"); caio.fill("#confirm-password", "Nova-Senha-Caio-2026")
    caio.get_by_role("button", name="Salvar senha").click()
    expect(caio.locator("#app-view")).to_be_visible()
    admin.locator(".nav-item[data-view='audit']").click()
    expect(admin.locator("#audit-body")).to_contain_text("Criou acesso")
    assert admin.errors == [] and caio.errors == []
