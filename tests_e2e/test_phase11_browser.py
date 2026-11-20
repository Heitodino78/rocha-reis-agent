"""Fase 11 — Chromium: contraste (tema claro e escuro), notebook/tablet, ajuda por teclado,
diálogo de confirmação acessível e conflito de conhecimento visível ao administrador."""

import re

import pytest
from playwright.sync_api import Page, expect, sync_playwright

from harness import PASSWORD, PanelServer
from rocha_reis_agent.knowledge_catalog import KnowledgeScope
from rocha_reis_agent.persistence.sqlite_knowledge import SQLiteKnowledgeRepository
from test_phase10_browser import A11Y
from whatsapp_support import catalog

pytestmark = pytest.mark.filterwarnings("ignore::ResourceWarning")

# Contraste WCAG 2.x (AA): 4,5:1 para texto comum e 3:1 para texto grande. Fundo efetivo = primeiro
# ancestral com cor de fundo opaca. Elementos desabilitados são isentos pela própria norma.
CONTRAST = """() => {
  const rgb = (v) => (v.match(/[\\d.]+/g) || []).map(Number);
  const lum = ([r, g, b]) => [r, g, b].map((c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; })
    .reduce((acc, c, i) => acc + c * [0.2126, 0.7152, 0.0722][i], 0);
  const background = (node) => {
    for (let n = node; n; n = n.parentElement) {
      const c = rgb(getComputedStyle(n).backgroundColor);
      if (c.length >= 3 && (c.length === 3 || c[3] > 0.9)) return c;
    }
    return [255, 255, 255];
  };
  const problems = [];
  for (const node of document.querySelectorAll('body *')) {
    if (!node.offsetWidth && !node.offsetHeight) continue;
    const own = [...node.childNodes].some((c) => c.nodeType === 3 && c.textContent.trim());
    if (!own || node.closest('[disabled], [aria-disabled=true], .visually-hidden')) continue;
    const style = getComputedStyle(node);
    const fg = rgb(style.color), bg = background(node);
    const [a, b] = [lum(fg), lum(bg)].sort((x, y) => y - x);
    const ratio = (a + 0.05) / (b + 0.05);
    const size = parseFloat(style.fontSize), bold = Number(style.fontWeight) >= 700;
    const need = size >= 24 || (bold && size >= 18.66) ? 3 : 4.5;
    if (ratio < need) problems.push(`${ratio.toFixed(2)} < ${need}: ${node.tagName}.${node.className} "${node.textContent.trim().slice(0, 30)}"`);
  }
  return [...new Set(problems)];
}"""


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def server(tmp_path):
    with PanelServer(tmp_path, knowledge_source="database") as srv:
        knowledge = SQLiteKnowledgeRepository(srv.app.state.conversation_repository)
        knowledge.initialize(KnowledgeScope.SYNTHETIC)
        knowledge.import_catalog(catalog(), author="fixture-sintetica")
        srv.user("admin.teste", "ADMIN", name="Admin Teste")
        srv.user("ana.teste", name="Ana Atendente")
        srv.customer("wamid.p1", "Quero falar com uma pessoa")
        srv.customer("wamid.p2", "Quanto custa o corte?", "5511900000002")
        yield srv


def page_for(browser, server, *, width=1366, height=860, dark=False, mobile=False) -> Page:
    context = browser.new_context(viewport={"width": width, "height": height}, is_mobile=mobile, has_touch=mobile,
                                  color_scheme="dark" if dark else "light")
    page = context.new_page()
    page.errors = []
    page.on("pageerror", lambda error: page.errors.append(str(error)))
    page.route(re.compile(r"^(?!" + re.escape(server.origin) + ").*"), lambda route: route.abort())
    page.goto(server.origin + "/panel/")
    return page


def login(page: Page, name: str) -> None:
    page.fill("#login-input", name)
    page.fill("#password-input", PASSWORD)
    page.press("#password-input", "Enter")
    expect(page.locator("#app-view")).to_be_visible()


def visit(page: Page, view: str, *, menu: bool = False) -> None:
    if menu:
        page.locator("#menu-toggle").click()
    page.locator(f".nav-item[data-view='{view}']").first.click()
    page.wait_for_timeout(350)


@pytest.mark.parametrize("dark", [False, True], ids=["claro", "escuro"])
def test_text_contrast_meets_wcag_aa_on_every_view(browser, server, dark):
    page = page_for(browser, server, dark=dark)
    assert page.evaluate(CONTRAST) == [], "login"
    login(page, "admin.teste")
    for view in ("overview", "conversations", "team", "audit", "settings", "system", "knowledge", "help"):
        visit(page, view)
        assert page.evaluate(CONTRAST) == [], view
    visit(page, "conversations")
    page.locator(".nav-item[data-filter='waiting']").click()
    page.locator(".conv").first.click()
    page.locator(".sidebar [data-notif-toggle]").click()
    assert page.evaluate(CONTRAST) == [], "conversa + avisos"
    assert page.errors == []


@pytest.mark.parametrize("size", [(1280, 720), (820, 1180)], ids=["notebook", "tablet"])
def test_notebook_and_tablet_layouts(browser, server, size):
    width, height = size
    tablet = width < 1100
    page = page_for(browser, server, width=width, height=height, mobile=tablet)
    login(page, "ana.teste")
    for view in ("overview", "conversations", "help"):
        visit(page, view, menu=tablet)
        assert page.evaluate(A11Y) == [], view
    visit(page, "conversations", menu=tablet)
    page.locator(".nav-item[data-filter='waiting']").first.click() if not tablet else None
    page.locator(".conv").first.click()
    page.get_by_role("button", name="Assumir atendimento").click()
    composer = page.locator("#composer-input")
    expect(composer).to_be_enabled()
    box = page.locator("#composer-send").bounding_box()
    assert box and box["x"] + box["width"] <= width and box["y"] + box["height"] <= height  # visível sem rolar a página
    assert box["height"] >= 40  # alvo de toque confortável
    assert page.errors == []


def test_help_is_reachable_by_keyboard_and_explains_who_resumes(browser, server):
    page = page_for(browser, server)
    login(page, "ana.teste")
    help_item = page.locator(".nav-item[data-view='help']")
    for _ in range(40):  # só Tab, sem mouse
        page.keyboard.press("Tab")
        if page.evaluate("document.activeElement.dataset && document.activeElement.dataset.view") == "help":
            break
    expect(help_item).to_be_focused()
    page.keyboard.press("Enter")
    expect(page.locator("#help-view")).to_be_visible()
    expect(page.locator("#help-view")).to_contain_text("Retomar automação")
    assert page.errors == []


def test_confirm_dialog_is_keyboard_operable_and_cancel_is_safe(browser, server):
    page = page_for(browser, server)
    login(page, "ana.teste")
    page.locator(".nav-item[data-filter='waiting']").click()
    page.locator(".conv").first.click()
    page.get_by_role("button", name="Assumir atendimento").click()
    page.get_by_role("button", name="Finalizar atendimento").click()
    dialog = page.locator("#confirm-dialog")
    expect(dialog).to_be_visible()
    assert page.evaluate("document.activeElement.closest('#confirm-dialog') !== null")  # foco dentro do modal
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(page.locator("#detail-state")).to_have_text("Em atendimento humano")  # cancelar não executa
    expect(page.locator("#composer-hint")).to_contain_text("Janela de atendimento aberta")
    page.get_by_role("button", name="Finalizar atendimento").click()
    page.locator("#confirm-ok").press("Enter")
    expect(page.locator("#detail-state")).to_have_text("Encerrado")
    expect(page.locator("#composer-hint")).to_contain_text("somente a administração pode retomá-la")
    assert page.errors == []


def test_admin_sees_conflict_message_and_simulated_preview(browser, server):
    page = page_for(browser, server)
    login(page, "admin.teste")
    visit(page, "knowledge")
    page.select_option("#knowledge-kind", "service")
    page.fill("#knowledge-key", "corte-curto")
    page.fill("#knowledge-name", "Corte curto")
    page.fill("#knowledge-aliases", "corte")
    page.fill("#knowledge-source", "Teste fictício de conflito")
    page.get_by_role("button", name="Salvar rascunho").click()
    row = page.locator("#knowledge-body tr", has=page.locator("td", has_text="service:corte-curto"))
    row.get_by_role("button", name="Aprovar").click()
    page.locator("#confirm-ok").click()
    expect(page.locator(".toast.error")).to_contain_text("Conflito")
    expect(row.locator("td").nth(4)).to_have_text("Rascunho")  # continua pendente, nada publicado
    page.fill("#knowledge-question", "Quanto custa o corte?")
    page.get_by_role("button", name="Ver prévia").click()
    expect(page.locator("#knowledge-preview")).to_contain_text("Prévia SIMULADA")
    assert page.errors == []
