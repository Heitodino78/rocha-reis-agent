"""Revisão final — Chromium: a administração redefine a senha pela tela Equipe; desativar devolve o
atendimento à fila e a linha do tempo explica o motivo em português."""

import pytest
from playwright.sync_api import expect

from harness import PanelServer
from test_phase10_browser import A11Y
from test_phase11_browser import browser, login, page_for  # noqa: F401 — fixture reutilizada

pytestmark = pytest.mark.filterwarnings("ignore::ResourceWarning")
TEMPORARY = "Temporaria-Redefinida-2026"


@pytest.fixture
def server(tmp_path):
    with PanelServer(tmp_path) as srv:
        srv.user("admin.teste", "ADMIN", name="Admin Teste")
        srv.user("ana.teste", name="Ana Atendente")
        srv.user("bia.teste", name="Bia Atendente")
        srv.customer("wamid.r1", "Quero falar com uma pessoa")
        yield srv


def test_admin_resets_password_from_team_screen(browser, server):  # noqa: F811
    admin = page_for(browser, server)
    login(admin, "admin.teste")
    admin.locator(".nav-item[data-view='team']").click()
    row = admin.get_by_role("row", name="Ana Atendente")
    row.get_by_role("button", name="Redefinir senha").click()
    admin.fill("#reset-password-new", TEMPORARY)
    admin.fill("#reset-password-again", "outra-senha-diferente")
    assert admin.evaluate(A11Y) == []
    admin.locator("#confirm-ok").click()
    expect(admin.locator(".toast.error", has_text="não conferem")).to_be_visible()  # nada alterado
    row.get_by_role("button", name="Redefinir senha").click()
    admin.fill("#reset-password-new", TEMPORARY)
    admin.fill("#reset-password-again", TEMPORARY)
    admin.locator("#confirm-ok").click()
    expect(admin.locator(".toast", has_text="Senha redefinida")).to_be_visible()
    ana = page_for(browser, server)
    ana.fill("#login-input", "ana.teste")
    ana.fill("#password-input", TEMPORARY)
    ana.press("#password-input", "Enter")
    expect(ana.locator("#password-view")).to_be_visible()  # troca obrigatória no próximo acesso
    assert admin.errors == [] and ana.errors == []


def test_deactivation_requeues_and_timeline_explains_why(browser, server):  # noqa: F811
    ana = page_for(browser, server)
    login(ana, "ana.teste")
    ana.locator(".nav-item[data-filter='waiting']").click()
    ana.locator(".conv").first.click()
    ana.get_by_role("button", name="Assumir atendimento").click()
    expect(ana.locator("#detail-owner")).to_have_text("Responsável: Ana Atendente")
    admin = page_for(browser, server)
    login(admin, "admin.teste")
    admin.locator(".nav-item[data-view='team']").click()
    admin.get_by_role("row", name="Ana Atendente").get_by_role("button", name="Desativar").click()
    expect(admin.locator("#confirm-text")).to_contain_text("voltam para a fila de espera")
    admin.locator("#confirm-ok").click()
    expect(admin.get_by_role("row", name="Ana Atendente")).to_contain_text("Desativado")
    bia = page_for(browser, server)
    login(bia, "bia.teste")
    bia.locator(".nav-item[data-filter='waiting']").click()
    bia.locator(".conv").first.click()
    expect(bia.locator("#detail-state")).to_have_text("Aguardando atendimento")
    bia.locator(".timeline summary").click()
    expect(bia.locator("#transitions")).to_contain_text("responsável desativado; voltou para a fila")
    expect(bia.locator("#transitions")).not_to_contain_text("owner_deactivated")
    bia.get_by_role("button", name="Assumir atendimento").click()
    expect(bia.locator("#detail-owner")).to_have_text("Responsável: Bia Atendente")
    assert ana.errors == [] and admin.errors == [] and bia.errors == []
