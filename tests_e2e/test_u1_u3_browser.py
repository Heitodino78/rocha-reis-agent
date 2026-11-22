"""U1 e U3 (revisão funcional, 22/11/2026) — Chromium contra o painel REAL em 127.0.0.1.

U1: cortesia isolada depois do encerramento fica no histórico e a conversa continua ENCERRADA, sem aviso;
pedido (ou áudio) reabre na fila, ao vivo, para quem atende. Regra PROVISÓRIA (validação do salão na 11B).
U3: o separador "·" nunca começa uma linha; sem rolagem lateral; textos inteiros.
"""

import re

import pytest
from playwright.sync_api import expect

from harness import PanelServer
from panel_support import conversation_id  # noqa: F401 — garante tests/ no caminho
from rocha_reis_agent.channels.service import drain
from test_panel_browser import browser, live, login, open_page  # noqa: F401 — fixture reutilizada
from test_phase11_browser import page_for
from test_phase14_browser import demo, enter  # noqa: F401 — fixture reutilizada
from whatsapp_support import envelope, media_message, stack

pytestmark = pytest.mark.filterwarnings("ignore::ResourceWarning")

# Cada "·" visível precisa estar na MESMA linha do caractere visível anterior (nunca no início da linha).
DOTS = r"""() => {
  const out = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walker.nextNode())) {
    const el = node.parentElement;
    if (!el || el.closest('[hidden]') || !el.getClientRects().length) continue;
    const text = node.data;
    for (let i = text.indexOf('·'); i !== -1; i = text.indexOf('·', i + 1)) {
      if (text.slice(0, i).trim() === '') { out.push('linha começa com ·: ' + text.trim().slice(0, 40)); continue; }
      let j = i - 1; while (j >= 0 && /\s/.test(text[j])) j--;
      const a = document.createRange(); a.setStart(node, j); a.setEnd(node, j + 1);
      const b = document.createRange(); b.setStart(node, i); b.setEnd(node, i + 1);
      const ra = a.getClientRects()[0], rb = b.getClientRects()[0];
      if (ra && rb && Math.abs(ra.top - rb.top) > 2) out.push('· quebrado para a linha seguinte: ' + text.trim().slice(0, 40));
    }
  }
  if (document.documentElement.scrollWidth > innerWidth + 1) out.push('rolagem lateral');
  return out;
}"""


@pytest.mark.parametrize("dark", [False, True], ids=["claro", "escuro"])
@pytest.mark.parametrize("width", [320, 390, 768, 1366])
def test_u3_separator_never_starts_a_line(browser, demo, width, dark):
    page = page_for(browser, demo, width=width, height=844, dark=dark, mobile=width < 800)
    enter(page, demo, "admin.demo")
    if width <= 1100:
        page.locator("#menu-toggle").click()
    page.locator(".nav-item[data-filter='all']").first.click()
    for index in range(min(4, page.locator(".conv").count())):
        if width <= 720 and index:
            page.locator(".back-btn").click()
        page.locator(".conv").nth(index).click()
        automation = page.locator("#detail-automation")
        expect(automation).to_be_visible()
        expect(automation).to_have_text(re.compile(r"^Automação (ativa|suspensa)$"))
        box = automation.bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= width + 1
        assert page.evaluate(DOTS) == [], index
    page.locator("[data-notif-toggle]:visible").first.click()
    expect(page.locator("#notif-panel")).to_be_visible()
    assert page.evaluate(DOTS) == []
    assert page.errors == []


def _media(directory, wamid, kind="audio"):  # type: ignore[no-untyped-def]
    s = stack(directory)
    s.ingestor.ingest(envelope(media_message(wamid, kind)))
    return drain(s.inbound, s.outbound, "browser-test")


def test_u1_courtesy_stays_closed_request_and_audio_reopen_live(browser, tmp_path):
    with PanelServer(tmp_path) as srv:
        srv.user("admin.u1", "ADMIN", name="Admin U1"); srv.user("ana.u1", name="Ana U1"); srv.user("bia.u1", name="Bia U1")
        admin = open_page(browser, srv); login(admin, "admin.u1"); live(admin)
        ana = open_page(browser, srv); login(ana, "ana.u1"); live(ana)
        bia = open_page(browser, srv, mobile=True); login(bia, "bia.u1"); live(bia)
        assert srv.customer("wamid.b1", "Quero falar com uma pessoa") == ["replied", "ACCEPTED"]
        ana.locator(".nav-item[data-filter='waiting']").click()
        ana.locator(".conv").first.click()
        ana.get_by_role("button", name="Assumir atendimento").click()
        ana.get_by_role("button", name="Finalizar atendimento").click(); ana.locator("#confirm-ok").click()
        expect(ana.locator("#detail-state")).to_have_text("Encerrado")
        expect(ana.locator("#detail-automation")).to_have_text("Automação suspensa")
        bell = bia.locator(".topbar [data-notif-count], [data-notif-count]:visible").first
        before_bell = bell.inner_text()

        # Cortesia: aparece no histórico, a conversa continua ENCERRADA, nenhum aviso novo, fila vazia.
        assert srv.customer("wamid.b2", "Obrigada 😊") == ["recorded_without_reply"]
        expect(ana.locator(".msg.client").last).to_contain_text("Obrigada 😊", timeout=10000)
        expect(ana.locator("#detail-state")).to_have_text("Encerrado")
        ana.wait_for_timeout(1500)  # tempo de sobra para o stream entregar qualquer aviso
        assert bell.inner_text() == before_bell
        admin.locator(".nav-item[data-filter='waiting']").click()
        expect(admin.locator(".conv")).to_have_count(0)
        admin.locator(".nav-item[data-filter='resolved']").click()
        expect(admin.locator(".conv")).to_have_count(1)

        # Pedido misto: reabre ao vivo, sem responsável, com aviso; a linha do tempo explica.
        assert srv.customer("wamid.b3", "Obrigada! Qual o preço da escova?") == ["recorded_without_reply"]
        expect(ana.locator("#detail-state")).not_to_have_text("Encerrado", timeout=10000)
        bia.locator("#menu-toggle").tap(); bia.locator(".nav-item[data-filter='waiting']").first.click()
        expect(bia.locator(".conv")).to_have_count(1, timeout=10000)
        expect(bell).not_to_have_text(before_bell, timeout=10000)
        bia.locator(".conv").first.click()
        expect(bia.locator(".msg.client").last).to_contain_text("Qual o preço da escova?")
        expect(bia.locator(".msg.client", has_text="Obrigada 😊")).to_have_count(1)  # histórico preservado
        bia.locator(".timeline summary").click()
        expect(bia.locator("#transitions")).to_contain_text("cliente voltou a escrever; voltou para a fila")
        expect(bia.locator("#detail-automation")).to_have_text("Automação suspensa")  # IA não volta sozinha
        bia.get_by_role("button", name="Assumir atendimento").click()
        expect(bia.locator("#detail-owner")).to_have_text("Responsável: Bia U1")
        bia.get_by_role("button", name="Finalizar atendimento").click(); bia.locator("#confirm-ok").click()
        expect(bia.locator("#detail-state")).to_have_text("Encerrado")

        # Áudio depois do encerramento: nunca descartado, nunca interpretado; reabre.
        assert _media(tmp_path, "wamid.b4") == ["media_recorded_without_reply"]
        admin.locator(".nav-item[data-filter='waiting']").click()
        expect(admin.locator(".conv")).to_have_count(1, timeout=10000)
        admin.locator(".conv").first.click()
        expect(admin.locator(".msg.client").last).to_contain_text("O cliente enviou um áudio")
        # Retomada só pela administração, depois de re-encerrar (regra preservada).
        admin.get_by_role("button", name="Finalizar atendimento").click(); admin.locator("#confirm-ok").click()
        admin.get_by_role("button", name="Retomar automação").click(); admin.locator("#confirm-ok").click()
        expect(admin.locator("#detail-automation")).to_have_text("Automação ativa")
        errors = admin.errors + [e for e in ana.errors + bia.errors if "404" not in e]
        for page in (admin, ana, bia):
            page.context.close()
        assert errors == []
