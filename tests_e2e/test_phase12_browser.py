"""Fase 12 — Chromium: categorias, profissionais, duração e tipo de preço no painel de
conhecimento (desktop e celular), validação no servidor, rejeição, histórico e permissões."""

import re

import pytest
from playwright.sync_api import Page, expect, sync_playwright

from harness import PanelServer
from rocha_reis_agent.knowledge_catalog import KnowledgeScope
from rocha_reis_agent.persistence.sqlite_knowledge import SQLiteKnowledgeRepository
from test_phase10_browser import A11Y
from test_phase11_browser import CONTRAST, login, page_for
from whatsapp_support import catalog

pytestmark = pytest.mark.filterwarnings("ignore::ResourceWarning")


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
        yield srv


def row(page: Page, fact_id: str):
    return page.locator("#knowledge-body tr", has=page.locator("td", has_text=re.compile(rf"^{re.escape(fact_id)}$")))


def save_draft(page: Page, kind: str, key: str, name: str, source: str = "Teste fictício") -> None:
    page.select_option("#knowledge-kind", kind)
    page.fill("#knowledge-key", key)
    page.fill("#knowledge-name", name)
    page.fill("#knowledge-source", source)
    page.get_by_role("button", name="Salvar rascunho").click()
    expect(row(page, f"{kind}:{key}")).to_contain_text("Rascunho")


def approve(page: Page, fact_id: str) -> None:
    row(page, fact_id).get_by_role("button", name="Aprovar").click()
    page.locator("#confirm-ok").click()
    expect(row(page, fact_id).locator("td").nth(4)).to_have_text("Aprovado")


def open_knowledge(page: Page, *, mobile: bool = False) -> None:
    if mobile:
        page.locator("#menu-toggle").tap()
    page.locator(".nav-item[data-view='knowledge']").click()
    expect(page.locator("#knowledge-scope")).to_contain_text("SINTÉTICA")


@pytest.mark.parametrize("mobile", [False, True], ids=["desktop", "celular"])
def test_admin_builds_structured_service_end_to_end(browser, server, mobile):
    page = page_for(browser, server, width=390 if mobile else 1366, height=844 if mobile else 860, mobile=mobile)
    login(page, "admin.teste")
    open_knowledge(page, mobile=mobile)
    save_draft(page, "category", "cabelo", "Cabelo (fictícia)")
    approve(page, "category:cabelo")
    save_draft(page, "professional", "pro-a", "Profissional Fictícia A")
    approve(page, "professional:pro-a")
    # Rejeição de um rascunho fica no histórico e não é usada.
    save_draft(page, "professional", "pro-z", "Profissional Fictícia Z")
    row(page, "professional:pro-z").get_by_role("button", name="Rejeitar").click()
    page.locator("#confirm-ok").click()
    expect(row(page, "professional:pro-z").locator("td").nth(4)).to_have_text("Rejeitado")

    # Nova versão do serviço existente com categoria, duração e profissional.
    row(page, "service:corte").get_by_role("button", name="Nova versão").click()
    expect(page.locator("#knowledge-category option", has_text="Cabelo (fictícia)")).to_have_count(1)
    expect(page.locator("#knowledge-team label", has_text="Fictícia Z")).to_have_count(0)  # rejeitado não aparece
    page.select_option("#knowledge-category", "cabelo")
    page.fill("#knowledge-duration-min", "60")
    page.fill("#knowledge-duration-max", "40")  # inválido: o servidor recusa
    page.locator("#knowledge-team label", has_text="Fictícia A").click()
    page.fill("#knowledge-source", "Teste fictício de duração")
    page.get_by_role("button", name="Salvar rascunho").click()
    expect(page.locator("#knowledge-error")).to_contain_text("Duração inválida")
    page.fill("#knowledge-duration-min", "40")
    page.fill("#knowledge-duration-max", "60")
    page.get_by_role("button", name="Salvar rascunho").click()
    expect(row(page, "service:corte")).to_contain_text("categoria: Cabelo (fictícia)")
    expect(row(page, "service:corte")).to_contain_text("40 min–1h")
    approve(page, "service:corte")
    expect(row(page, "category:cabelo")).to_contain_text("serviços: Corte de teste")  # relacionamento visível

    page.fill("#knowledge-question", "Vocês fazem corte?")
    page.get_by_role("button", name="Ver prévia").click()
    preview = page.locator("#knowledge-preview")
    expect(preview).to_contain_text("Prévia SIMULADA")
    expect(preview).to_contain_text("Duração estimada: entre 40 min e 1h (pode variar).")
    expect(preview).to_contain_text("Realizado por: Profissional Fictícia A.")

    row(page, "service:corte").get_by_role("button", name="Histórico").click()
    expect(page.locator("#knowledge-history li")).to_have_count(3)
    assert page.evaluate(A11Y) == []
    assert page.evaluate(CONTRAST) == []
    assert page.errors == []


def test_price_types_in_the_form(browser, server):
    page = page_for(browser, server)
    login(page, "admin.teste")
    open_knowledge(page)
    row(page, "price:corte").get_by_role("button", name="Nova versão").click()
    page.select_option("#knowledge-pricing", "on_evaluation")
    expect(page.locator("#knowledge-amount")).to_be_hidden()
    page.select_option("#knowledge-pricing", "range")
    expect(page.locator("#knowledge-amount-max")).to_be_visible()
    page.fill("#knowledge-amount", "45")
    page.fill("#knowledge-amount-max", "80")
    page.fill("#knowledge-source", "Teste fictício de faixa")
    page.get_by_role("button", name="Salvar rascunho").click()
    expect(row(page, "price:corte")).to_contain_text("R$ 45,00 a R$ 80,00")
    approve(page, "price:corte")
    page.fill("#knowledge-question", "Quanto custa o corte?")
    page.get_by_role("button", name="Ver prévia").click()
    expect(page.locator("#knowledge-preview")).to_contain_text("de R$ 45,00 a R$ 80,00")
    assert page.errors == []


def test_operator_never_sees_or_reaches_knowledge_management(browser, server):
    page = page_for(browser, server)
    login(page, "ana.teste")
    expect(page.locator(".nav-item[data-view='knowledge']")).to_be_hidden()
    status = page.evaluate("""async () => (await fetch('/api/panel/knowledge')).status""")
    assert status == 403
    assert page.errors == []
