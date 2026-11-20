"""Fase 14 — Chromium sobre a DEMONSTRAÇÃO real da RC1 (mesmo construtor e worker embutido usados por
`sh iniciar.sh`): administração no notebook, atendente no celular, acessibilidade no tablet."""

import re
import socket
import threading
import time

import pytest
import uvicorn
from playwright.sync_api import Page, expect, sync_playwright

from rocha_reis_agent.ops.demo import build_demo, profile_settings, send_fictitious
from rocha_reis_agent.panel.app import create_panel_app
from test_phase10_browser import A11Y
from test_phase11_browser import CONTRAST, page_for

pytestmark = pytest.mark.filterwarnings("ignore::ResourceWarning")


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


class DemoServer:
    def __init__(self, profile, port: int) -> None:
        self.profile = profile
        self.settings = profile_settings(profile, port=port)
        self.origin = self.settings.panel_origin
        self.server = uvicorn.Server(uvicorn.Config(create_panel_app(self.settings), host="127.0.0.1", port=port,
                                                    log_level="warning", access_log=False, lifespan="on"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.credentials = {line.split("\t")[0]: line.split("\t")[2] for line in
                            (profile / "credenciais-demo.txt").read_text().splitlines()[1:]}


@pytest.fixture
def demo(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    build_demo(tmp_path / "demo")
    server = DemoServer(tmp_path / "demo", port)
    server.thread.start()
    deadline = time.monotonic() + 15
    while not server.server.started:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    yield server
    server.server.should_exit = True
    server.thread.join(timeout=10)


def enter(page: Page, demo: DemoServer, login: str) -> None:
    page.fill("#login-input", login)
    page.fill("#password-input", demo.credentials[login])
    page.press("#password-input", "Enter")
    expect(page.locator("#app-view")).to_be_visible()
    expect(page.locator("#env-banner")).to_contain_text("DEMONSTRAÇÃO")


def test_admin_on_notebook_sees_attention_approves_knowledge_and_version(browser, demo):
    page = page_for(browser, demo, width=1280, height=720)
    enter(page, demo, "admin.demo")
    page.locator(".nav-item[data-view='overview']").click()
    expect(page.locator("#attention-list")).to_contain_text("resultado incerto")
    page.locator(".nav-item[data-view='knowledge']").click()
    row = page.locator("#knowledge-body tr", has=page.locator("td", has_text=re.compile(r"^price:escova$")))
    expect(row).to_contain_text("Rascunho")
    row.get_by_role("button", name="Aprovar").click()
    page.locator("#confirm-ok").click()
    expect(row.locator("td").nth(4)).to_have_text("Aprovado")
    page.fill("#knowledge-question", "Quanto custa a coloração?")
    page.get_by_role("button", name="Ver prévia").click()
    expect(page.locator("#knowledge-preview")).to_contain_text("a partir de R$ 120,00")
    page.locator(".nav-item[data-view='system']").click()
    expect(page.locator("#system-status")).to_contain_text("RC1 Offline")
    expect(page.locator("#system-locks li")).to_have_count(4)
    page.locator(".nav-item[data-view='help']").click()
    expect(page.locator("#release-info")).to_contain_text("Não é versão de produção")
    assert page.errors == []


def test_attendant_on_phone_handles_a_new_customer_end_to_end(browser, demo):
    page = page_for(browser, demo, width=390, height=844, mobile=True)
    enter(page, demo, "ana.demo")
    expect(page.locator("#connection")).to_have_text("Ao vivo", timeout=10000)
    send_fictitious(demo.profile, "Quero falar com uma pessoa, sou cliente nova", sender="5511900000777")
    expect(page.locator(".topbar [data-notif-count]")).not_to_have_text("", timeout=15000)  # worker embutido processou
    page.locator(".topbar [data-notif-toggle]").tap()
    page.locator(".notif", has_text="aguardando").first.tap()
    expect(page.locator("#detail-state")).to_have_text("Aguardando atendimento")
    page.get_by_role("button", name="Assumir atendimento").tap()
    page.fill("#composer-input", "Olá! Aqui é a Ana (demonstração).")
    page.locator("#composer-send").tap()
    expect(page.locator(".msg.operator .delivery").last).to_have_text(re.compile("Aceita pela plataforma"), timeout=15000)
    page.get_by_role("button", name="Finalizar atendimento").tap()
    page.locator("#confirm-ok").tap()
    expect(page.locator("#detail-state")).to_have_text("Encerrado")
    expect(page.locator("#composer-hint")).to_contain_text("somente a administração pode retomá-la")
    assert page.evaluate(A11Y) == []
    assert page.errors == []


def test_tablet_accessibility_and_contrast_with_demo_data(browser, demo):
    page = page_for(browser, demo, width=820, height=1180, mobile=True)
    enter(page, demo, "admin.demo")
    for view in ("overview", "conversations", "team", "audit", "settings", "system", "knowledge", "account", "help"):
        page.locator("#menu-toggle").tap()
        page.locator(f".nav-item[data-view='{view}']").first.tap()
        page.wait_for_timeout(350)
        assert page.evaluate(A11Y) == [], view
        assert page.evaluate(CONTRAST) == [], view
    assert page.errors == []
