"""Fase 8 — tela de conhecimento do painel em navegador REAL (Chromium headless).

Executar com o venv de e2e (ver requirements-e2e.txt):
    /tmp/rocha-reis-e2e-venv/bin/python -m pytest -q -p no:cacheprovider tests_e2e
"""

import pytest
from playwright.sync_api import expect, sync_playwright

from harness import ROOT, PanelServer
from test_panel_browser import login, open_page
from rocha_reis_agent.catalog_io import load_synthetic_catalog
from rocha_reis_agent.knowledge_catalog import KnowledgeScope
from rocha_reis_agent.persistence.sqlite_knowledge import SQLiteKnowledgeRepository

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
        repo = SQLiteKnowledgeRepository(srv.app.state.conversation_repository)
        repo.initialize(KnowledgeScope.SYNTHETIC)
        repo.import_catalog(load_synthetic_catalog(ROOT / "fixtures/salon.versioned.synthetic.json"), author="fixture")
        srv.user("admin.teste", "ADMIN", name="Admin Teste")
        srv.user("ana.teste", name="Ana Atendente")
        yield srv


def row(page, fact_id):
    return page.locator("#knowledge-body tr", has=page.locator("td", has_text=fact_id)).first


def test_price_version_is_suspended_until_approved_and_history_is_kept(browser, server):
    page = open_page(browser, server)
    login(page, "admin.teste")
    page.locator(".nav-item[data-view='knowledge']").click()
    expect(page.locator("#knowledge-scope")).to_contain_text("SINTÉTICA")
    price = row(page, "price:corte")
    expect(price).to_contain_text("R$ 45,00")
    expect(price).to_contain_text("Aprovado")

    price.get_by_role("button", name="Nova versão").click()
    expect(page.locator("#knowledge-amount")).to_have_value("45,00")
    page.fill("#knowledge-amount", "52,00")
    page.fill("#knowledge-source", "Tabela sintética de teste e2e")
    page.get_by_role("button", name="Salvar rascunho").click()
    expect(row(page, "price:corte")).to_contain_text("Rascunho")
    expect(row(page, "price:corte").locator("td").nth(5)).to_have_text("Não")  # suspenso no atendimento

    page.fill("#knowledge-question", "Quanto custa o corte?")
    page.get_by_role("button", name="Ver prévia").click()
    expect(page.locator("#knowledge-preview")).to_contain_text("Não tenho preço confirmado")
    expect(page.locator("#knowledge-preview")).not_to_contain_text("R$ 45")

    row(page, "price:corte").get_by_role("button", name="Aprovar").click()
    page.locator("#confirm-ok").click()
    expect(row(page, "price:corte")).to_contain_text("Aprovado")
    expect(row(page, "price:corte")).to_contain_text("R$ 52,00")
    page.get_by_role("button", name="Ver prévia").click()
    expect(page.locator("#knowledge-preview")).to_contain_text("R$ 52,00")

    row(page, "price:corte").get_by_role("button", name="Histórico").click()
    expect(page.locator("#knowledge-history li")).to_have_count(3)  # v1 aprovado, v2 rascunho, v3 aprovado
    expect(page.locator("#ai-status")).to_contain_text("BLOQUEADO")
    assert page.errors == []


def test_invalid_booksy_link_is_refused_and_operator_has_no_access(browser, server):
    page = open_page(browser, server)
    login(page, "admin.teste")
    page.locator(".nav-item[data-view='knowledge']").click()
    row(page, "booksy").get_by_role("button", name="Nova versão").click()
    page.fill("#knowledge-url", "https://golpe.example/booksy")
    page.fill("#knowledge-source", "teste")
    page.get_by_role("button", name="Salvar rascunho").click()
    expect(page.locator("#knowledge-error")).to_contain_text("Link Booksy inválido")
    # O Chromium registra no console o 422 esperado da validação; nenhum outro erro é aceito.
    assert [e for e in page.errors if "status of 422" not in e] == []

    other = open_page(browser, server)
    login(other, "ana.teste")
    expect(other.locator(".nav-item[data-view='knowledge']")).to_be_hidden()
    status = other.evaluate("fetch('/api/panel/knowledge').then(r => r.status)")
    assert status == 403  # o backend recusa mesmo que a interface fosse burlada
