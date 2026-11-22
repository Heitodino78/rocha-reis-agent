"""U1 (revisão funcional, 22/11/2026) — cortesia isolada depois do encerramento NÃO reabre o atendimento.

Regra PROVISÓRIA de produto (validação do salão na 11B). Todos os testes conferem o resultado REAL: estado e
responsável gravados no banco, histórico, transições, avisos do painel e saída (outbox) — não só o classificador.
Rede bloqueada (conftest); transporte e IA simulados; as cinco travas continuam False.
"""

from __future__ import annotations

from contextlib import closing
import sqlite3
from threading import Barrier, Thread

import pytest

from panel_support import Panel, add_user, conversation_id, create_panel_app, customer, panel_settings
from whatsapp_support import envelope, media_message, stack

from rocha_reis_agent.channels.service import drain

from rocha_reis_agent.application.courtesy import is_isolated_courtesy

COURTESY = ["Obrigado", "Obrigada!", "Valeu!!!", "Ok", "Beleza", "Show", "Tudo certo.", "👍", "🙏", "Obrigado 😊",
            "OBRIGADAAA 🙏🏽", "ok, obrigada!", "Muito obrigada pelo atendimento ❤️", "  valeu  "]
REOPEN = {
    "nova_solicitacao": "Quero agendar um horário.",
    "remarcar": "Preciso mudar meu agendamento.",
    "outra_duvida": "Tenho outra dúvida.",
    "preco": "Quanto custa o corte?",
    "pedido_humano": "Quero falar com uma pessoa.",
    "reclamacao": "O atendimento ainda não resolveu meu problema.",
    "misto_ajuda": "Obrigado, mas preciso de ajuda.",
    "misto_agendar": "Valeu, queria agendar amanhã.",
    "misto_problema": "Ok, mas não consegui resolver.",
    "misto_preco": "Obrigada! Qual o preço da escova?",
    "ambigua_entao": "Então...",
    "ambigua_ainda_nao": "Ainda não",
    "ambigua_pode_ser": "Pode ser",
    "ambigua_interrogacao": "?",
    "negacao_tudo_certo": "Não está tudo certo",
    "ok_interrogacao": "Ok?",
    "emoji_fora_da_lista": "😡",
    "saudacao": "Bom dia",
}


# ------------------------------------------------------------------ apoio
@pytest.fixture
def team(tmp_path):  # type: ignore[no-untyped-def]
    app = create_panel_app(panel_settings(tmp_path))
    ids = {"admin": add_user(app, "admin.u1", "ADMIN"), "ana": add_user(app, "ana.u1"), "bia": add_user(app, "bia.u1")}
    panels = {name: Panel(app) for name in ids}
    for name, panel in panels.items():
        assert panel.login(f"{name}.u1").status_code == 200
    return app, ids, panels, tmp_path


def q(app, sql, *params):  # type: ignore[no-untyped-def]
    with closing(sqlite3.connect(app.state.conversation_repository.path)) as c:
        return c.execute(sql, params).fetchall()


def snapshot(app) -> dict[str, object]:  # type: ignore[no-untyped-def]
    (state, closed_flag), = q(app, "SELECT state, closed FROM conversations")
    return {"state": state, "closed": closed_flag,
            "owner": q(app, "SELECT user_id FROM panel_assignments"),
            "client_msgs": [r[0] for r in q(app, "SELECT content FROM messages WHERE role='client' ORDER BY ordinal")],
            "all_msgs": q(app, "SELECT role, content FROM messages ORDER BY ordinal"),
            "reopened": q(app, "SELECT count(*) FROM transitions WHERE action='client_returned'")[0][0],
            "transitions": q(app, "SELECT action FROM transitions ORDER BY ordinal"),
            "outbox": q(app, "SELECT count(*) FROM channel_outbox")[0][0]}


def kinds(panel: Panel) -> set[str]:
    return {n["type"] for n in panel.get("/notifications").json()["items"]}


def closed_by_ana(team) -> str:  # type: ignore[no-untyped-def]
    app, ids, p, directory = team
    assert customer(directory, "wamid.u1.0", "Quero falar com uma pessoa") == ["replied", "ACCEPTED"]
    cid = conversation_id(p["ana"], "waiting")
    assert p["ana"].post(f"/conversations/{cid}/assume").status_code == 204
    assert p["ana"].post(f"/conversations/{cid}/resolve").status_code == 204
    assert snapshot(app)["state"] == "RESOLVED"
    return cid


def media(directory, wamid: str, kind: str) -> list[str]:  # type: ignore[no-untyped-def]
    s = stack(directory)
    s.ingestor.ingest(envelope(media_message(wamid, kind)))
    return drain(s.inbound, s.outbound, "test-worker")


# ------------------------------------------------------------------ 1–4, 15, 16, 19: cortesia isolada
@pytest.mark.parametrize("text", COURTESY)
def test_isolated_courtesy_is_recorded_and_the_conversation_stays_closed(team, text):
    app, ids, p, directory = team
    closed_by_ana(team)
    before = snapshot(app)
    assert customer(directory, "wamid.u1.c", text) == ["recorded_without_reply"]  # sem resposta automática
    after = snapshot(app)
    assert after["state"] == "RESOLVED" and after["closed"] == 0                 # continua ENCERRADA
    assert after["client_msgs"] == before["client_msgs"] + [text.strip()]        # registrada no histórico
    assert after["all_msgs"][:-1] == before["all_msgs"]                           # nada anterior se perde
    assert after["transitions"] == before["transitions"] and after["reopened"] == 0
    assert after["owner"] == before["owner"] == [(ids["ana"],)]                   # responsável histórico intacto
    assert after["outbox"] == before["outbox"]                                    # nenhum envio criado
    for who in ("admin", "ana", "bia"):                                           # nenhum aviso de atendimento
        assert not kinds(p[who]) & {"human_requested", "new_message", "assigned_to_me"}, who
    assert p["ana"].get("/conversations?filter=waiting").json()["items"] == []


# ------------------------------------------------------------------ 5–9, 17, 19: reabre
@pytest.mark.parametrize("text", REOPEN.values(), ids=REOPEN.keys())
def test_request_mixed_or_ambiguous_message_reopens_to_the_human_queue(team, text):
    app, ids, p, directory = team
    cid = closed_by_ana(team)
    before = snapshot(app)
    assert customer(directory, "wamid.u1.r", text) == ["recorded_without_reply"]  # nunca resposta automática
    after = snapshot(app)
    assert after["state"] == "WAITING_HUMAN" and after["closed"] == 0  # automação continua suspensa (não AUTO)
    assert after["client_msgs"] == before["client_msgs"] + [text]
    assert after["all_msgs"][:-1] == before["all_msgs"]
    assert after["transitions"] == before["transitions"] + [("client_returned",)]
    assert after["owner"] == []  # sai do responsável anterior: fila sem responsável (regra de negócio ADR-134)
    assert after["outbox"] == before["outbox"]
    for who in ("ana", "bia", "admin"):
        assert "human_requested" in kinds(p[who]), who
        assert [i["id"] for i in p[who].get("/conversations?filter=waiting").json()["items"]] == [cid], who


# ------------------------------------------------------------------ 10, 11: mídia
@pytest.mark.parametrize("kind", ["audio", "image", "document", "location", "interactive", "tipo_desconhecido"])
def test_media_after_closure_is_kept_and_reopens(team, kind):
    app, ids, p, directory = team
    closed_by_ana(team)
    before = snapshot(app)
    assert media(directory, "wamid.u1.m", kind) == ["media_recorded_without_reply"]
    after = snapshot(app)
    assert after["state"] == "WAITING_HUMAN" and after["owner"] == [] and after["outbox"] == before["outbox"]
    note = after["client_msgs"][-1]
    assert note.startswith("[O cliente enviou ") and "não exibido nem interpretado" in note
    assert "media-sintetica" not in note and "image/jpeg" not in note  # conteúdo/identificador nunca exposto
    assert "human_requested" in kinds(p["bia"])


def test_media_during_human_attendance_reaches_the_owner_and_never_changes_state(team):
    app, ids, p, directory = team
    customer(directory, "wamid.u1.h0", "Quero falar com uma pessoa")
    cid = conversation_id(p["ana"], "waiting")
    p["ana"].post(f"/conversations/{cid}/assume")
    before = snapshot(app)
    assert media(directory, "wamid.u1.h1", "audio") == ["media_recorded_without_reply"]
    after = snapshot(app)
    assert after["state"] == "HUMAN_ACTIVE" and after["owner"] == [(ids["ana"],)]
    assert after["transitions"] == before["transitions"] and after["outbox"] == before["outbox"]
    assert "um áudio" in after["client_msgs"][-1]
    assert "new_message" in kinds(p["ana"])
    shown = p["ana"].get(f"/conversations/{cid}").json()
    assert "um áudio" in str(shown)


def test_media_in_automatic_conversation_keeps_previous_behaviour(team):
    """Fora do escopo de U1: em AUTO (e de contato novo) a mídia segue registrada só na entrada, sem resposta."""
    app, ids, p, directory = team
    assert media(directory, "wamid.u1.a0", "image") == ["unsupported_type_ignored"]  # contato sem conversa
    customer(directory, "wamid.u1.a1", "Quanto custa o corte?")
    before = snapshot(app)
    assert media(directory, "wamid.u1.a2", "audio") == ["unsupported_type_ignored"]
    assert snapshot(app) == before


# ------------------------------------------------------------------ 12: duplicadas
@pytest.mark.parametrize("text,state", [("Obrigada 😊", "RESOLVED"), ("Quero remarcar", "WAITING_HUMAN")])
def test_duplicate_delivery_is_processed_once(team, text, state):
    app, ids, p, directory = team
    closed_by_ana(team)
    customer(directory, "wamid.u1.d", text)
    first = snapshot(app)
    assert customer(directory, "wamid.u1.d", text) in ([], ["duplicate"])  # a Meta reentrega o mesmo wamid
    assert snapshot(app) == first and first["state"] == state
    assert first["client_msgs"].count(text) == 1


def test_duplicate_media_is_processed_once(team):
    app, ids, p, directory = team
    closed_by_ana(team)
    media(directory, "wamid.u1.dm", "audio")
    first = snapshot(app)
    assert media(directory, "wamid.u1.dm", "audio") in ([], ["duplicate"])
    assert snapshot(app) == first and first["reopened"] == 1


# ------------------------------------------------------------------ 13: simultâneas
@pytest.mark.parametrize("texts", [("Obrigada 😊", "Quero remarcar"), ("Quero remarcar", "Mudei de ideia, quero outro horário"),
                                   ("Valeu", "Obrigado!")], ids=["cortesia+pedido", "dois_pedidos", "duas_cortesias"])
def test_simultaneous_messages_reopen_at_most_once_and_lose_nothing(team, texts):
    """Dois processos do núcleo (repositórios independentes no MESMO arquivo) disputam a mesma conversa."""
    from rocha_reis_agent.channels.service import event_id_for
    from rocha_reis_agent.domain.contracts import InboundMessage
    from rocha_reis_agent.domain.conversations import utc_now
    app, ids, p, directory = team
    key = closed_by_ana(team)
    agents = [stack(directory).agent for _ in texts]
    barrier, errors = Barrier(len(texts)), []

    def run(agent, index, text):  # type: ignore[no-untyped-def]
        try:
            barrier.wait(timeout=10)
            agent.handle(InboundMessage(event_id_for(f"wamid.u1.s{index}"), key, text, utc_now()))
        except Exception as error:  # noqa: BLE001
            errors.append(error)

    threads = [Thread(target=run, args=(a, i, t)) for i, (a, t) in enumerate(zip(agents, texts))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert errors == []
    after = snapshot(app)
    assert sorted(after["client_msgs"][-2:]) == sorted(texts)  # as duas gravadas, nenhuma perdida
    courtesy = [is_isolated_courtesy(t) for t in texts]
    assert after["reopened"] == (0 if all(courtesy) else 1)    # nunca duas reaberturas
    assert after["state"] == ("RESOLVED" if all(courtesy) else "WAITING_HUMAN")
    assert after["outbox"] == 1  # só a confirmação do pedido inicial


# ------------------------------------------------------------------ 14: reinício
def test_state_and_history_survive_restart(team):
    app, ids, p, directory = team
    closed_by_ana(team)
    customer(directory, "wamid.u1.p1", "Obrigada 😊")
    kept = snapshot(app)
    restarted = create_panel_app(panel_settings(directory))  # processo novo, mesmo banco
    assert snapshot(restarted) == kept and kept["state"] == "RESOLVED"
    customer(directory, "wamid.u1.p2", "Esqueci de perguntar: vocês abrem domingo")
    reopened = snapshot(restarted)
    assert reopened["state"] == "WAITING_HUMAN" and reopened["client_msgs"][-2:] == ["Obrigada 😊",
                                                                                      "Esqueci de perguntar: vocês abrem domingo"]
    assert snapshot(create_panel_app(panel_settings(directory))) == reopened


# ------------------------------------------------------------------ 18: funcionário desativado
def test_deactivated_former_owner_never_gets_the_reopened_conversation(team):
    app, ids, p, directory = team
    cid = closed_by_ana(team)
    stale_ana = app.state.panel_service.store.user(ids["ana"])
    assert p["admin"].post(f"/users/{ids['ana']}/deactivate", {}).status_code == 204
    customer(directory, "wamid.u1.x1", "Obrigada!")
    assert snapshot(app)["state"] == "RESOLVED"
    customer(directory, "wamid.u1.x2", "Quero remarcar")
    assert snapshot(app)["owner"] == [] and snapshot(app)["state"] == "WAITING_HUMAN"
    assert p["ana"].post(f"/conversations/{cid}/assume").status_code == 401  # sessão revogada
    from rocha_reis_agent.panel.security import PanelError
    with pytest.raises(PanelError):
        app.state.panel_service.assume(stale_ana, cid)  # requisição já autenticada: recusada na transação
    assert snapshot(app)["owner"] == []
    assert p["bia"].post(f"/conversations/{cid}/assume").status_code == 204
    assert snapshot(app)["owner"] == [(ids["bia"],)]
    assert q(app, "SELECT count(*) FROM panel_assignments a JOIN panel_users u ON u.id=a.user_id WHERE u.active=0") == [(0,)]


# ------------------------------------------------------------------ retomada explícita continua igual
def test_resume_after_courtesy_needs_no_reclose_and_stays_administrative(team):
    app, ids, p, directory = team
    cid = closed_by_ana(team)
    customer(directory, "wamid.u1.z1", "Valeu!")
    assert p["ana"].post(f"/conversations/{cid}/resume").status_code == 403    # atendente não retoma
    assert p["admin"].post(f"/conversations/{cid}/resume").status_code == 204  # sem re-encerrar (continua RESOLVED)
    assert customer(directory, "wamid.u1.z2", "Quanto custa o corte?") == ["replied", "ACCEPTED"]


# ------------------------------------------------------------------ 20: nenhuma integração real
def test_no_real_integration_is_reachable(team):
    from rocha_reis_agent import config
    from rocha_reis_agent.channels.fake_transport import FakeWhatsAppTransport
    assert not any(config.activation_locks().values())
    app, ids, p, directory = team
    closed_by_ana(team)
    s = stack(directory)
    assert isinstance(s.transport, FakeWhatsAppTransport)
    s.ingestor.ingest(envelope(media_message("wamid.u1.n1", "audio")))
    drain(s.inbound, s.outbound, "w")
    assert s.transport.deliveries == []  # nada a enviar: nem para o cliente, nem "fingindo" envio


# ------------------------------------------------------------------ classificador (complemento)
@pytest.mark.parametrize("text", COURTESY + ["Tá bom", "👍🏻👍🏻", "ok ok", "Perfeito!", "Combinado"])
def test_classifier_accepts_only_whitelisted_courtesy(text):
    assert is_isolated_courtesy(text)


@pytest.mark.parametrize("text", list(REOPEN.values()) + ["", "   ", "!", "Ok...", "obrigado 2", "sim", "x" * 100,
                                                           "obrigado " * 9, "Obrigada, não", "certo?", "👍 mas e amanhã"])
def test_classifier_rejects_everything_else(text):
    assert not is_isolated_courtesy(text)
