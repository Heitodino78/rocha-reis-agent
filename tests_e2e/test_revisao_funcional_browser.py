"""Revisão funcional (22/11/2026) — Chromium contra o painel REAL em 127.0.0.1.

1. Ciclo completo encadeado, conferido no BANCO a cada passo e com o servidor REINICIADO no meio:
   criar funcionário → 1º acesso com troca de senha → receber → assumir → responder → transferir → encerrar →
   cliente volta a escrever (reabre na fila) → desativar com atendimento em andamento → redistribuição →
   reinício → persistência.
2. Regressão do layout: tabelas sem conteúdo escondido, cartões rotulados, palavras inteiras e filtros
   visíveis em 320–1920 px, temas claro e escuro; ações alcançáveis pelo teclado; nomes longos.
"""

from contextlib import closing
import re
import sqlite3

import pytest
from playwright.sync_api import expect

from harness import PanelServer
from test_panel_browser import browser, live, login, open_page  # noqa: F401 — fixture reutilizada
from test_phase11_browser import page_for
from test_phase14_browser import demo, enter  # noqa: F401 — fixture reutilizada

pytestmark = pytest.mark.filterwarnings("ignore::ResourceWarning")
TEMP, NEW = "Temporaria-Carla-2026", "Nova-Senha-Carla-2026"


def q(server, sql, *params):  # type: ignore[no-untyped-def]
    with closing(sqlite3.connect(server.app.state.conversation_repository.path)) as c:
        return c.execute(sql, params).fetchall()


def state(server):  # type: ignore[no-untyped-def]
    return q(server, "SELECT c.state, c.closed, (SELECT u.login FROM panel_assignments a JOIN panel_users u ON "
                     "u.id=a.user_id WHERE a.conversation_id=c.id) FROM conversations c")


def test_complete_attendance_cycle_survives_restart(browser, tmp_path):
    with PanelServer(tmp_path) as srv:
        srv.user("admin.rf", "ADMIN", name="Admin RF"); srv.user("ana.rf", name="Ana RF")
        admin = open_page(browser, srv); login(admin, "admin.rf"); live(admin)
        # 1. criar funcionário
        admin.locator(".nav-item[data-view='team']").click()
        admin.fill("#new-login", "carla.rf"); admin.fill("#new-name", "Carla RF"); admin.fill("#new-user-password", TEMP)
        admin.get_by_role("button", name="Criar acesso").click()
        expect(admin.locator("#users-body tr", has_text="Carla RF")).to_contain_text("senha temporária")
        assert q(srv, "SELECT role, active, must_change_password FROM panel_users WHERE login='carla.rf'") == [("OPERATOR", 1, 1)]
        # 2. primeiro acesso com troca obrigatória de senha
        carla = open_page(browser, srv)
        carla.fill("#login-input", "carla.rf"); carla.fill("#password-input", TEMP); carla.press("#password-input", "Enter")
        expect(carla.locator("#password-view")).to_be_visible()
        carla.fill("#current-password", TEMP); carla.fill("#new-password", NEW); carla.fill("#confirm-password", NEW)
        carla.get_by_role("button", name="Salvar senha").click()
        expect(carla.locator("#app-view")).to_be_visible(); live(carla)
        assert q(srv, "SELECT must_change_password FROM panel_users WHERE login='carla.rf'") == [(0,)]
        # 3. receber: aparece ao vivo na fila e no sino, sem recarregar
        assert srv.customer("wamid.rf1", "Quero falar com uma pessoa") == ["replied", "ACCEPTED"]
        carla.locator(".nav-item[data-filter='waiting']").click()
        expect(carla.locator(".conv")).to_have_count(1, timeout=10000)
        expect(carla.locator(".sidebar [data-notif-count]")).not_to_have_text("", timeout=10000)
        assert state(srv) == [("WAITING_HUMAN", 0, None)]
        # 4. assumir
        carla.locator(".conv").first.click()
        carla.get_by_role("button", name="Assumir atendimento").click()
        expect(carla.locator("#detail-owner")).to_have_text("Responsável: Carla RF")
        assert state(srv) == [("HUMAN_ACTIVE", 0, "carla.rf")]
        # 5. responder (transporte simulado)
        carla.fill("#composer-input", "Olá! Aqui é a Carla (fictícia).")
        carla.locator("#composer-send").click()
        expect(carla.locator(".msg.operator .delivery")).to_have_text(re.compile("Aceita pela plataforma"), timeout=10000)
        assert q(srv, "SELECT status FROM channel_outbox o JOIN panel_users u ON u.id=o.operator_user_id "
                      "WHERE u.login='carla.rf'") == [("ACCEPTED",)]
        # 6. transferir para a Ana
        carla.get_by_role("button", name="Transferir").click()
        carla.select_option("#transfer-target", label="Ana RF (Atendente)")
        carla.locator("#confirm-ok").click()
        expect(carla.locator("#detail")).to_be_hidden(timeout=10000)
        assert state(srv) == [("HUMAN_ACTIVE", 0, "ana.rf")]
        ana = open_page(browser, srv); login(ana, "ana.rf"); live(ana)
        ana.locator(".nav-item[data-filter='mine']").click()
        expect(ana.locator(".conv")).to_have_count(1, timeout=10000)
        # 7. encerrar
        ana.locator(".conv").first.click()
        ana.get_by_role("button", name="Finalizar atendimento").click()
        ana.locator("#confirm-ok").click()
        expect(ana.locator("#detail-state")).to_have_text("Encerrado")
        assert state(srv) == [("RESOLVED", 0, "ana.rf")]
        # 8. cliente volta a escrever: reabre na fila (antes: gravada em silêncio, ninguém sabia)
        assert srv.customer("wamid.rf2", "Oi, voltei. Quero remarcar") == ["recorded_without_reply"]
        assert state(srv) == [("WAITING_HUMAN", 0, None)]
        carla.locator(".nav-item[data-filter='waiting']").click()
        expect(carla.locator(".conv")).to_have_count(1, timeout=10000)
        carla.locator(".conv").first.click()
        carla.locator(".timeline summary").click()
        expect(carla.locator("#transitions")).to_contain_text("cliente voltou a escrever; voltou para a fila")
        expect(carla.locator("#transitions")).not_to_contain_text("client_returned")
        carla.get_by_role("button", name="Assumir atendimento").click()
        expect(carla.locator("#detail-owner")).to_have_text("Responsável: Carla RF")
        # 9. desativar a Carla com o atendimento em andamento: sai na hora
        admin.locator(".nav-item[data-view='team']").click()
        admin.locator("#users-body tr", has_text="Carla RF").get_by_role("button", name="Desativar").click()
        admin.locator("#confirm-ok").click()
        expect(admin.locator("#users-body tr", has_text="Carla RF")).to_contain_text("Desativado")
        expect(carla.locator("#login-view")).to_be_visible(timeout=15000)
        # 10. redistribuição: volta para a fila, sem responsável, visível para quem atende
        assert state(srv) == [("WAITING_HUMAN", 0, None)]
        ana.locator(".nav-item[data-filter='waiting']").click()
        expect(ana.locator(".conv")).to_have_count(1, timeout=10000)
        before = (state(srv), q(srv, "SELECT role, content FROM messages ORDER BY ordinal"),
                  q(srv, "SELECT login, active FROM panel_users ORDER BY login"),
                  q(srv, "SELECT status, count(*) FROM channel_outbox GROUP BY status"))
        # Carla (transferiu) e Ana (encerrou) perdem o acesso à conversa; a atualização ao vivo que chega depois
        # recebe o 404 ESPERADO ("não está mais disponível"), como em test_full_operator_flow_with_live_updates.
        errors = admin.errors + [e for e in carla.errors + ana.errors if "404" not in e]
        for page in (admin, carla, ana):
            page.context.close()
        assert errors == []
    # 11. reinício do sistema (mesmo banco, processo novo)
    with PanelServer(tmp_path) as srv:
        # 12. persistência de estados, mensagens, equipe e envios
        after = (state(srv), q(srv, "SELECT role, content FROM messages ORDER BY ordinal"),
                 q(srv, "SELECT login, active FROM panel_users ORDER BY login"),
                 q(srv, "SELECT status, count(*) FROM channel_outbox GROUP BY status"))
        assert after == before
        ana = open_page(browser, srv); login(ana, "ana.rf")
        ana.locator(".nav-item[data-filter='waiting']").click()
        ana.locator(".conv").first.click()
        expect(ana.locator(".msg.operator")).to_contain_text("Aqui é a Carla")
        expect(ana.locator(".msg.client").last).to_contain_text("Quero remarcar")
        blocked = open_page(browser, srv)
        blocked.fill("#login-input", "carla.rf"); blocked.fill("#password-input", NEW); blocked.press("#password-input", "Enter")
        expect(blocked.locator("#login-error")).to_be_visible()
        expect(blocked.locator("#app-view")).to_be_hidden()
        assert ana.errors == []


# ------------------------------------------------------------------ layout
LAYOUT = """() => {
  const vis = (n) => !!(n.offsetWidth || n.offsetHeight || n.getClientRects().length);
  const out = [];
  for (const w of document.querySelectorAll('.table-wrap')) {
    if (vis(w) && w.scrollWidth > w.clientWidth + 1) out.push('tabela escondendo conteúdo: ' + w.querySelector('table').getAttribute('aria-label'));
  }
  for (const td of document.querySelectorAll('.responsive-table tbody td')) {
    if (!vis(td) || getComputedStyle(td).display !== 'grid') continue;
    if (!td.dataset.label) out.push('cartão sem rótulo');
    if (getComputedStyle(td, '::before').whiteSpace !== 'nowrap') out.push('rótulo pode quebrar');
  }
  const chips = document.querySelector('.chips');
  if (chips && vis(chips) && chips.scrollWidth > chips.clientWidth + 1) out.push('filtros cortados');
  for (const c of document.querySelectorAll('.page:not([hidden]) select, .page:not([hidden]) input, .workspace .chip')) {
    const r = c.getBoundingClientRect();
    if (vis(c) && r.width && r.right > innerWidth + 1) out.push('fora da tela: ' + (c.id || c.className));
  }
  for (const td of document.querySelectorAll('#knowledge-body td:first-child')) {
    if (vis(td) && getComputedStyle(td).overflowWrap === 'anywhere') out.push('tipo pode quebrar no meio da palavra');
  }
  return out;
}"""


def go(page, view):  # type: ignore[no-untyped-def]
    if page.viewport_size["width"] <= 1100:
        page.locator("#menu-toggle").click()
    page.locator(f".nav-item[data-view='{view}']").first.click()
    page.wait_for_timeout(400)


@pytest.mark.parametrize("dark", [False, True], ids=["claro", "escuro"])
@pytest.mark.parametrize("width", [320, 390, 768, 1024, 1366, 1920])
def test_tables_cards_words_and_filters_fit_every_width(browser, demo, width, dark):
    page = page_for(browser, demo, width=width, height=900, dark=dark, mobile=width < 800)
    enter(page, demo, "admin.demo")
    page.wait_for_timeout(400)
    assert page.evaluate(LAYOUT) == [], "conversas"
    for view in ("team", "knowledge", "audit", "system"):
        go(page, view)
        assert page.evaluate(LAYOUT) == [], view
    assert page.errors == []


def test_card_actions_are_reachable_by_keyboard_and_long_names_fit(browser, demo):
    page = page_for(browser, demo, width=390, height=844, mobile=True)
    enter(page, demo, "admin.demo")
    go(page, "team")
    long_name = "Maria Aparecida dos Santos Oliveira Lima Pereira"
    page.fill("#new-login", "maria.longa"); page.fill("#new-name", long_name)
    page.fill("#new-user-password", "Temporaria-Maria-2026")
    page.get_by_role("button", name="Criar acesso").click()
    row = page.locator("#users-body tr", has_text=long_name)
    expect(row).to_be_visible()
    assert page.evaluate(LAYOUT) == []
    box, viewport = row.bounding_box(), page.viewport_size
    assert box and box["x"] >= 0 and box["x"] + box["width"] <= viewport["width"] + 1  # type: ignore[index]
    # Teclado: Tab a partir do topo da página alcança "Redefinir senha" de um cartão, visível ao focar.
    page.locator("#team-title").evaluate("(h) => { h.tabIndex = -1; h.focus(); }")
    reached = None
    for _ in range(80):
        page.keyboard.press("Tab")
        label = page.evaluate("() => document.activeElement && document.activeElement.textContent.trim()")
        if label == "Redefinir senha":
            reached = page.evaluate("""() => { const r = document.activeElement.getBoundingClientRect();
              return r.top >= 0 && r.bottom <= innerHeight && r.right <= innerWidth; }""")
            break
    assert reached is True
    assert page.errors == []


def test_late_401_from_the_previous_session_does_not_end_the_new_one(browser, tmp_path):
    """Corrida achada na regressão (1 em 8 execuções de test_session_expiry_…): um pedido feito pela sessão
    ANTERIOR que responde 401 depois do novo login derrubava a sessão nova (e apagava a senha digitada)."""
    with PanelServer(tmp_path) as srv:
        srv.user("ana.401", name="Ana 401")
        page = open_page(browser, srv); login(page, "ana.401"); live(page)
        held = []

        def hold_first(route):  # type: ignore[no-untyped-def]
            if not held:
                held.append(route)  # pedido da sessão 1 fica "no ar"
            else:
                route.continue_()

        page.route("**/api/panel/overview", hold_first)
        page.locator(".nav-item[data-view='overview']").click()
        for _ in range(50):
            if held:
                break
            page.wait_for_timeout(100)
        assert held, "o pedido da sessão 1 não foi feito"
        with closing(sqlite3.connect(srv.app.state.conversation_repository.path)) as c:  # sessão expira no servidor
            c.execute("DELETE FROM panel_sessions"); c.commit()
        expect(page.locator("#login-view")).to_be_visible(timeout=10000)
        login(page, "ana.401")  # sessão 2
        held[0].fulfill(status=401, content_type="application/json", body='{"detail": "Sessão expirada."}')
        page.wait_for_timeout(1500)
        expect(page.locator("#app-view")).to_be_visible()   # antes: voltava para o login
        expect(page.locator("#login-view")).to_be_hidden()
        assert page.evaluate("async () => (await fetch('/api/panel/auth/session')).status") == 200
        page.unroute("**/api/panel/overview")
        assert [e for e in page.errors if "401" not in e] == []
