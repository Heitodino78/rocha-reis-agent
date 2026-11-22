"""Fase 10 — Chromium headless (desktop e celular): avisos, urgência, histórico paginado,
configurações, recuperação de sessão, sistema/contatos excluídos e acessibilidade básica.

Somente 127.0.0.1; qualquer requisição fora da origem do painel é abortada (ver open_page).
"""

from contextlib import closing
import re
import sqlite3

import pytest
from playwright.sync_api import Page, expect, sync_playwright

from harness import PanelServer
from rocha_reis_agent.channels.fake_transport import FakeWhatsAppTransport
from rocha_reis_agent.channels.service import drain
from test_panel_browser import live, login, open_page
from whatsapp_support import stack

pytestmark = pytest.mark.filterwarnings("ignore::ResourceWarning")
SECRET = "Conteúdo fictício reservado 7731"

# Auditoria básica de acessibilidade, executada na página (sem bibliotecas externas):
# nomes acessíveis, rótulos de campos, ids únicos, idioma e títulos.
A11Y = """() => {
  const problems = [];
  const visible = (n) => !!(n.offsetWidth || n.offsetHeight || n.getClientRects().length);
  const name = (n) => (n.getAttribute('aria-label') || n.textContent || n.title || '').trim();
  for (const b of document.querySelectorAll('button, [role=button], a[href]')) {
    if (visible(b) && !name(b)) problems.push('sem nome: ' + b.outerHTML.slice(0, 80));
  }
  for (const f of document.querySelectorAll('input:not([type=hidden]), select, textarea')) {
    if (!visible(f)) continue;
    const labelled = (f.labels && f.labels.length) || f.getAttribute('aria-label') || f.getAttribute('aria-labelledby');
    if (!labelled) problems.push('campo sem rótulo: ' + (f.id || f.outerHTML.slice(0, 60)));
  }
  const ids = [...document.querySelectorAll('[id]')].map((n) => n.id);
  const dup = ids.filter((id, i) => ids.indexOf(id) !== i);
  if (dup.length) problems.push('ids duplicados: ' + [...new Set(dup)].join(','));
  if (document.documentElement.lang !== 'pt-BR') problems.push('idioma ausente');
  if (!document.title) problems.push('sem título');
  // No celular a janela de layout cresce junto com o conteúdo largo; compara-se com a TELA (Fase 12).
  const width = Math.min(window.innerWidth, window.screen.width);
  if (document.documentElement.scrollWidth > width + 1) problems.push(`rolagem horizontal (${document.documentElement.scrollWidth}px > ${width}px)`);
  return problems;
}"""


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def server(tmp_path, request):
    with PanelServer(tmp_path, **getattr(request, "param", {})) as srv:
        srv.ids = {"admin": srv.user("admin.teste", "ADMIN", name="Admin Teste"),
                   "ana": srv.user("ana.teste", name="Ana Atendente"),
                   "bia": srv.user("bia.teste", name="Bia Atendente")}
        yield srv


def db(server):
    return closing(sqlite3.connect(server.app.state.conversation_repository.path))


def bell(page: Page):
    return page.locator(".sidebar [data-notif-toggle]")


def test_live_notification_without_content_no_duplicates_after_reload(browser, server):
    ana = open_page(browser, server)
    login(ana, "ana.teste"); live(ana)
    expect(bell(ana).locator("[data-notif-count]")).to_have_text("")
    server.customer("wamid.n1", f"Quero falar com uma pessoa. {SECRET}")
    expect(bell(ana).locator("[data-notif-count]")).to_have_text("1", timeout=10000)  # sem recarregar
    expect(ana.locator(".toast", has_text="aguardando atendimento humano")).to_have_count(1)
    bell(ana).click()
    panel = ana.locator("#notif-panel")
    expect(panel).to_be_visible()
    expect(panel).to_contain_text("Cliente aguardando atendimento humano.")
    expect(panel).not_to_contain_text(SECRET)  # aviso nunca mostra o texto do cliente
    ana.keyboard.press("Escape")
    expect(panel).to_be_hidden()
    expect(bell(ana).locator("[data-notif-count]")).to_have_text("")  # visto
    ana.reload()
    live(ana)
    ana.wait_for_timeout(1500)
    expect(ana.locator(".toast", has_text="aguardando atendimento humano")).to_have_count(0)  # sem duplicata
    expect(bell(ana).locator("[data-notif-count]")).to_have_text("")
    bell(ana).click()
    ana.locator(".notif", has_text="aguardando").click()  # leva direto à conversa
    expect(ana.locator("#detail-state")).to_have_text("Aguardando atendimento")
    assert ana.errors == []


@pytest.mark.parametrize("server", [{"panel_embedded_worker": False}], indirect=True)
def test_send_failure_is_mandatory_and_explained_in_conversation(browser, server):
    server.customer("wamid.f1", "Quero falar com uma pessoa")
    ana = open_page(browser, server)
    login(ana, "ana.teste"); live(ana)
    # Preferências não desligam avisos obrigatórios.
    bell(ana).click()
    ana.locator(".notif-prefs summary").click()
    for box in ana.locator("[data-notif-pref]:visible").all():
        box.uncheck()
    ana.keyboard.press("Escape")
    ana.locator(".conv").first.click()
    ana.get_by_role("button", name="Assumir atendimento").click()
    ana.fill("#composer-input", "Resposta que vai falhar (fictícia).")
    ana.locator("#composer-send").click()
    expect(ana.locator(".msg.operator .delivery")).to_have_text(re.compile("Na fila"))
    s = stack(server.directory, transport=FakeWhatsAppTransport("reject"))
    drain(s.inbound, s.outbound, "e2e")
    expect(ana.locator(".msg.operator .delivery")).to_have_text(re.compile("Falhou — recusada pela plataforma"),
                                                                timeout=10000)
    expect(ana.locator("#detail-alert")).to_contain_text("NÃO foi enviada")
    expect(bell(ana).locator("[data-notif-count]")).to_have_class(re.compile("critical"), timeout=10000)
    bell(ana).click()
    expect(ana.locator(".notif.critical")).to_contain_text("Falha no envio")
    assert ana.errors == []


def test_urgency_overview_and_typed_settings(browser, server):
    server.customer("wamid.u1", "Quero falar com uma pessoa")
    admin = open_page(browser, server)
    login(admin, "admin.teste"); live(admin)
    admin.locator(".nav-item[data-view='settings']").click()
    expect(admin.locator("#setting-urgent")).to_have_value("15")
    admin.fill("#setting-urgent", "0")
    admin.get_by_role("button", name="Salvar configurações").click()
    expect(admin.locator("#settings-error")).to_contain_text("1 a 1440")
    admin.fill("#setting-urgent", "1")
    admin.select_option("#setting-retired-policy", "allow_new_conversation")
    admin.get_by_role("button", name="Salvar configurações").click()
    expect(admin.locator("#confirm-dialog")).to_contain_text("responsável pelos dados")  # decisão sensível confirma
    admin.locator("#confirm-ok").click()
    expect(admin.locator(".toast", has_text="Configurações salvas")).to_be_visible()
    # Espera "envelhecida" diretamente no banco (sem dormir 1 minuto no teste).
    with db(server) as connection:
        connection.execute("UPDATE transitions SET recorded_at='2020-01-01T00:00:00+00:00' WHERE current='WAITING_HUMAN'")
        connection.commit()
    admin.locator(".nav-item[data-view='overview']").click()
    expect(admin.locator("#attention-list")).to_contain_text("URGENTE")
    admin.locator(".attention-item", has_text="aguardando").get_by_role("button", name="Ver").click()
    expect(admin.locator(".conv .urgent-badge")).to_contain_text("Urgente")
    admin.reload()
    admin.locator(".nav-item[data-view='settings']").click()
    expect(admin.locator("#setting-urgent")).to_have_value("1")
    expect(admin.locator("#setting-retired-policy")).to_have_value("allow_new_conversation")
    admin.locator(".nav-item[data-view='audit']").click()
    expect(admin.locator("#audit-body")).to_contain_text("Alterou o tempo de urgência")
    assert admin.errors == []


@pytest.mark.parametrize("server", [{"panel_history_page_size": 10}], indirect=True)
def test_older_messages_are_loaded_on_demand(browser, server):
    for index in range(8):
        server.customer(f"wamid.h{index}", f"Quanto custa o corte? pergunta {index}")
    admin = open_page(browser, server)
    login(admin, "admin.teste"); live(admin)
    admin.locator(".nav-item[data-filter='auto']").click()
    admin.locator(".conv").first.click()
    expect(admin.locator(".msg")).to_have_count(10)
    admin.get_by_role("button", name="Carregar mensagens anteriores").click()
    expect(admin.locator(".msg")).to_have_count(16)
    expect(admin.locator(".msg.client").first).to_contain_text("pergunta 0")
    expect(admin.get_by_role("button", name="Carregar mensagens anteriores")).to_have_count(0)
    assert admin.errors == []


def test_session_expiry_keeps_open_conversation_and_draft(browser, server):
    server.customer("wamid.s1", "Quero falar com uma pessoa")
    ana = open_page(browser, server)
    login(ana, "ana.teste"); live(ana)
    ana.locator(".conv").first.click()
    ana.get_by_role("button", name="Assumir atendimento").click()
    ana.fill("#composer-input", "Rascunho importante ainda não enviado")
    with db(server) as connection:  # sessão expira/revogada no servidor
        connection.execute("DELETE FROM panel_sessions")
        connection.commit()
    expect(ana.locator("#login-view")).to_be_visible(timeout=10000)
    expect(ana.locator("#login-error")).to_have_text("Sua sessão terminou. Entre novamente. Seu rascunho foi mantido.")
    login(ana, "ana.teste")
    expect(ana.locator("#detail-owner")).to_have_text("Responsável: Ana Atendente")
    expect(ana.locator("#composer-input")).to_have_value("Rascunho importante ainda não enviado")
    # Sair explicitamente descarta rascunhos (computador compartilhado).
    ana.locator(".sidebar [data-action='logout']").click()
    login(ana, "ana.teste")
    ana.locator(".nav-item[data-filter='mine']").click()
    ana.locator(".conv").first.click()
    expect(ana.locator("#composer-input")).to_have_value("")


def test_system_view_locks_and_retired_contact_reinstatement(browser, server):
    server.customer("wamid.r1", "Oi")
    repository = server.app.state.conversation_repository
    with db(server) as connection:
        cid = connection.execute("SELECT id FROM conversations").fetchone()[0]
    repository.retire(cid, confirmation=f"DELETE_SYNTHETIC:{cid}")
    assert server.customer("wamid.r2", "Oi de novo") == ["retired_contact_blocked"]
    admin = open_page(browser, server)
    login(admin, "admin.teste"); live(admin)
    admin.locator(".nav-item[data-view='system']").click()
    expect(admin.locator("#system-locks li")).to_have_count(5)  # Fase 12: dados reais; 18B-0: backup externo
    for item in admin.locator("#system-locks li").all():
        expect(item).to_contain_text("bloqueada")
    row = admin.get_by_role("row", name=re.compile(cid[-6:]))
    expect(row).to_contain_text("1")
    row.get_by_role("button", name="Reativar").click()
    admin.fill("#reinstate-confirm", "errado")
    admin.locator("#confirm-ok").click()
    expect(admin.locator(".toast.error", has_text="Confirmação incorreta")).to_be_visible()
    admin.get_by_role("row", name=re.compile(cid[-6:])).get_by_role("button", name="Reativar").click()
    admin.fill("#reinstate-confirm", f"REATIVAR:{cid[-6:]}")
    admin.locator("#confirm-ok").click()
    expect(admin.locator("#retired-empty")).to_be_visible(timeout=10000)
    ana = open_page(browser, server)
    login(ana, "ana.teste")
    expect(ana.locator(".nav-item[data-view='system']")).to_be_hidden()
    # O único erro aceito é o 409 da confirmação errada, provocado de propósito acima.
    assert [e for e in admin.errors if "409" not in e] == [] and ana.errors == []


def test_mobile_notifications_overview_and_no_horizontal_scroll(browser, server):
    server.customer("wamid.mm1", "Quero falar com uma pessoa")
    page = open_page(browser, server, mobile=True)
    login(page, "admin.teste")
    expect(page.locator("#connection")).to_have_text("Ao vivo", timeout=10000)
    page.locator(".topbar [data-notif-toggle]").tap()
    expect(page.locator("#notif-panel")).to_be_visible()
    box = page.locator("#notif-panel").bounding_box()
    assert box and box["width"] >= 380  # painel em tela cheia no celular
    page.locator("#notif-close").tap()
    expect(page.locator("#notif-panel")).to_be_hidden()
    page.locator("#menu-toggle").tap()
    page.locator(".nav-item[data-view='overview']").tap()
    expect(page.locator("#attention-list")).to_contain_text("aguardando atendimento")
    for view in ("overview", "system", "settings", "team", "audit", "knowledge", "help"):
        page.locator("#menu-toggle").tap()
        page.locator(f".nav-item[data-view='{view}']").tap()
        page.wait_for_timeout(300)
        assert page.evaluate(A11Y) == [], view
    # 409 esperado: conhecimento em arquivo neste servidor (a tela explica; nada quebra).
    assert [e for e in page.errors if "409" not in e] == []


@pytest.mark.parametrize("login_name", ["admin.teste", "ana.teste"])
def test_basic_accessibility_on_desktop_views(browser, server, login_name):
    server.customer("wamid.a1", "Quero falar com uma pessoa")
    page = open_page(browser, server)
    assert page.evaluate(A11Y) == []  # tela de login
    login(page, login_name); live(page)
    views = ["overview", "conversations"] + (["team", "audit", "settings", "system", "knowledge"]
                                              if login_name == "admin.teste" else [])
    for view in views:
        page.locator(f".nav-item[data-view='{view}']").first.click()
        page.wait_for_timeout(300)
        assert page.evaluate(A11Y) == [], view
    page.locator(".nav-item[data-filter='waiting']").click()
    page.locator(".conv").first.click()
    bell(page).click()
    assert page.evaluate(A11Y) == [], "avisos abertos"
    # Teclado: o botão de avisos é alcançável por Tab e o foco vai para o painel.
    expect(page.locator("#notif-close")).to_be_focused()
    # 409 esperado: conhecimento em arquivo (a tela explica que a gestão exige a base no banco).
    assert [e for e in page.errors if "409" not in e] == []
