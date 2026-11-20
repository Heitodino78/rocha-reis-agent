"""Fase 15 — os cenários revisados da 11B funcionam como estão escritos, na demonstração real:
AT-16 (sair numa aba; a outra reconhece a sessão encerrada e preserva o rascunho) e AT-09 (falha
de envio entendida e resposta escrita de novo)."""

import re

import pytest
from playwright.sync_api import expect

from test_phase11_browser import page_for
from test_phase14_browser import browser, demo, enter  # noqa: F401 (fixtures)

pytestmark = pytest.mark.filterwarnings("ignore::ResourceWarning")


def test_at16_logout_in_one_tab_other_tab_keeps_draft(browser, demo):  # noqa: F811
    first = page_for(browser, demo)
    enter(first, demo, "ana.demo")
    second = first.context.new_page()  # mesma sessão do navegador, outra aba
    second.errors = []
    second.goto(demo.origin + "/panel/")
    expect(second.locator("#app-view")).to_be_visible()
    second.locator(".nav-item[data-filter='mine']").click()
    second.locator(".conv").first.click()
    second.fill("#composer-input", "Rascunho da segunda aba (fictício)")
    first.locator(".sidebar [data-action='logout']").click()
    expect(first.locator("#login-view")).to_be_visible()
    # A segunda aba percebe sozinha (atualização ao vivo), sem precisar clicar em nada.
    expect(second.locator("#login-view")).to_be_visible(timeout=10000)
    expect(second.locator("#login-error")).to_have_text("Sua sessão terminou. Entre novamente. Seu rascunho foi mantido.")
    second.fill("#login-input", "ana.demo")
    second.fill("#password-input", demo.credentials["ana.demo"])
    second.press("#password-input", "Enter")
    expect(second.locator("#composer-input")).to_have_value("Rascunho da segunda aba (fictício)")
    # 401 esperado no console: a requisição feita com a sessão já encerrada.
    assert [e for e in second.errors if "401" not in e] == []


def test_at09_failed_message_is_understood_and_rewritten(browser, demo):  # noqa: F811
    page = page_for(browser, demo)
    enter(page, demo, "bia.demo")
    page.locator(".nav-item[data-filter='mine']").click()
    page.locator(".conv", has=page.locator(".send-alert", has_text="Falha de envio")).first.click()
    expect(page.locator("#detail-alert")).to_contain_text("NÃO foi enviada")
    expect(page.locator(".msg.operator .delivery").last).to_contain_text("Falhou")
    page.fill("#composer-input", "Oi, aqui é a Bia de novo (demonstração).")
    page.locator("#composer-send").click()
    expect(page.locator(".msg.operator .delivery").last).to_have_text(re.compile("Aceita pela plataforma"), timeout=15000)
    assert page.errors == []
