"""Fase 16 — redesign: avisos ao entrar sem inundar a tela, tabelas sem colunas escondidas, gaveta do menu
com fundo que fecha ao tocar fora, e as telas do redesign sem rolagem horizontal nem erro de contraste."""

import pytest
from playwright.sync_api import expect

from test_phase10_browser import A11Y
from test_phase11_browser import CONTRAST, page_for
from test_phase14_browser import browser, demo, enter  # noqa: F401 — fixtures reutilizadas

pytestmark = pytest.mark.filterwarnings("ignore::ResourceWarning")

# Nenhuma coluna de tabela visível pode terminar fora do quadro (antes: "Nova ve…" cortado e ações escondidas).
TABLES_FIT = """() => [...document.querySelectorAll('.table-wrap')].filter((w) => w.offsetWidth)
  .filter((w) => w.scrollWidth > w.clientWidth + 1).map((w) => w.querySelector('table').getAttribute('aria-label'))"""


def test_login_with_pending_notices_shows_one_summary_not_a_flood(browser, demo):
    page = page_for(browser, demo, width=1440, height=900)
    enter(page, demo, "admin.demo")
    expect(page.locator(".toast")).to_have_count(1)
    expect(page.locator(".toast")).to_contain_text("pendente(s)")
    count = page.locator(".sidebar [data-notif-count]")
    expect(count).not_to_have_text("")  # os avisos continuam no sino
    page.locator(".sidebar [data-notif-toggle]").click()
    assert page.locator("#notif-list li").count() >= 2
    assert page.errors == []


def test_admin_tables_show_every_column_without_sideways_scroll_on_desktop(browser, demo):
    page = page_for(browser, demo, width=1440, height=900)
    enter(page, demo, "admin.demo")
    for view in ("team", "audit", "knowledge", "system"):
        page.locator(f".nav-item[data-view='{view}']").click()
        page.wait_for_timeout(400)
        assert page.evaluate(TABLES_FIT) == [], view
    page.locator(".nav-item[data-view='team']").click()
    expect(page.locator("#users-body tr")).to_have_count(3)
    page.wait_for_timeout(300)
    # Leitura num único passo: a tabela pode ser redesenhada por eventos ao vivo entre duas consultas.
    rows = page.evaluate("""() => [...document.querySelectorAll('#users-body tr')].map((tr) => ({
      cells: tr.cells.length, display: getComputedStyle(tr.cells[tr.cells.length - 1]).display,
      heights: [...tr.cells].map((c) => c.getBoundingClientRect().height) }))""")
    for row in rows:
        assert row["cells"] == 7 and row["display"] == "table-cell"  # ações dentro de uma célula de verdade
        assert max(row["heights"]) - min(row["heights"]) < 1  # linha alinhada
    assert page.errors == []


@pytest.mark.parametrize("dark", [False, True], ids=["claro", "escuro"])
def test_redesigned_demo_views_pass_contrast_and_layout_checks(browser, demo, dark):
    page = page_for(browser, demo, width=1440, height=900, dark=dark)
    assert page.evaluate(CONTRAST) == [], "login"
    enter(page, demo, "admin.demo")
    for view in ("overview", "team", "audit", "settings", "system", "knowledge", "help", "account"):
        page.locator(f".nav-item[data-view='{view}']").click()
        page.wait_for_timeout(350)
        assert page.evaluate(CONTRAST) == [], view
        assert page.evaluate(A11Y) == [], view
    assert page.errors == []


def test_tablet_menu_drawer_has_backdrop_that_closes_it(browser, demo):
    page = page_for(browser, demo, width=820, height=1180, mobile=True)
    enter(page, demo, "ana.demo")
    page.locator("#menu-toggle").click()
    expect(page.locator("#sidebar")).to_have_class("sidebar open")
    expect(page.locator("#scrim")).to_be_visible()
    expect(page.locator(".sidebar .nav-item[aria-current='page']")).to_be_focused()
    page.mouse.click(780, 600)  # fora da gaveta
    expect(page.locator("#scrim")).to_be_hidden()
    expect(page.locator("#menu-toggle")).to_have_attribute("aria-expanded", "false")
    expect(page.locator("#menu-toggle")).to_be_focused()
    assert page.evaluate(A11Y) == []
    assert page.errors == []
