"""Fase 10 — painel operacional: configurações tipadas, notificações, visão geral, urgência,
histórico paginado, contatos excluídos, visão de sistema e migração 009."""

from contextlib import closing
from datetime import timedelta
import json
import sqlite3
from uuid import uuid4

import pytest

from panel_support import Panel, add_user, conversation_id, create_panel_app, customer, panel_settings
from rocha_reis_agent.channels.fake_transport import FakeWhatsAppTransport
from rocha_reis_agent.channels.service import drain
from rocha_reis_agent.domain.conversations import utc_now
from rocha_reis_agent.persistence.sqlite import SCHEMA_VERSION
from whatsapp_support import APP_SECRET, CLIENT_A, CLIENT_B, CONVERSATION_KEY, VERIFY, stack

SECRET_TEXT = "Meu segredo fictício 4242 é sobre coloração"


@pytest.fixture
def env(tmp_path):
    app = create_panel_app(panel_settings(tmp_path, panel_history_page_size=10))
    ids = {login: add_user(app, login, role) for login, role in
           (("admin.teste", "ADMIN"), ("ana.teste", "OPERATOR"), ("bia.teste", "OPERATOR"))}
    panels = {}
    for login in ids:
        panels[login] = Panel(app)
        panels[login].login(login)
    return app, ids, panels, tmp_path


def waiting(tmp_path, sender=CLIENT_A, text="Quero falar com uma pessoa"):
    customer(tmp_path, f"wamid.{uuid4().hex}", text, sender)


def reply(panel, cid, text="Resposta da atendente fictícia."):
    return panel.post(f"/conversations/{cid}/reply", {"text": text, "idempotency_key": uuid4().hex})


def kinds(panel):
    return [item["type"] for item in panel.get("/notifications").json()["items"]]


def db(app):
    return closing(sqlite3.connect(app.state.conversation_repository.path))


# ------------------------------------------------------------ configurações tipadas
def test_typed_settings_are_validated_in_backend_and_audited(env):
    app, ids, panels, _ = env
    admin = panels["admin.teste"]
    body = admin.get("/settings").json()
    assert body["permissions"] == {"operators_can_transfer": True, "operators_can_resume": False,
                                   "urgent_after_minutes": 15, "retired_contact_policy": "block"}
    assert admin.put("/settings", {"urgent_after_minutes": 30, "retired_contact_policy": "allow_new_conversation"}
                     ).status_code == 204
    assert admin.get("/settings").json()["permissions"]["urgent_after_minutes"] == 30
    assert admin.get("/settings").json()["permissions"]["retired_contact_policy"] == "allow_new_conversation"
    for invalid in ({"urgent_after_minutes": 0}, {"urgent_after_minutes": 1441}, {"urgent_after_minutes": "15"},
                    {"urgent_after_minutes": True}, {"retired_contact_policy": "delete_all"},
                    {"operators_can_resume": "yes"}, {"whatsapp_transport": "graph"},
                    {"gemini_real_provider_activation_authorized": True}, {"ai_provider": "gemini"}):
        assert admin.put("/settings", invalid).status_code == 422, invalid
    assert panels["ana.teste"].put("/settings", {"urgent_after_minutes": 5}).status_code == 403
    actions = [entry["action"] for entry in admin.get("/audit").json()]
    assert "setting_urgent_after_minutes_changed" in actions
    assert "setting_retired_contact_policy_allow_new_conversation" in actions
    # Travas reais seguem somente leitura e desligadas.
    assert admin.get("/system").json()["locks"] == {"meta_graph": False, "meta_homologation": False,
                                                    "gemini_real": False, "real_data": False}


def test_store_rejects_out_of_range_values_even_without_api(env):
    app, ids, *_ = env
    from rocha_reis_agent.panel.security import PanelError
    for values in ({"urgent_after_minutes": 5000}, {"retired_contact_policy": 1}, {"unknown": True}):
        with pytest.raises(PanelError):
            app.state.panel_store.update_settings(values, actor_id=ids["admin.teste"])


# ------------------------------------------------------------ notificações
def test_human_request_notification_has_stable_key_and_no_content(env):
    app, ids, panels, tmp_path = env
    waiting(tmp_path, text=f"Quero falar com uma pessoa. {SECRET_TEXT}")
    ana = panels["ana.teste"]
    first = ana.get("/notifications").json()["items"]
    second = ana.get("/notifications").json()["items"]  # reconexão / nova consulta: mesma chave
    assert [n["type"] for n in first] == ["human_requested"] and first == second
    raw = json.dumps(first)
    assert SECRET_TEXT not in raw and CLIENT_A not in raw and "coloração" not in raw
    assert first[0]["label"].startswith("Cliente ··") and first[0]["mandatory"] is False
    # Assumida: o aviso some para todos (estado do banco, não fila de eventos).
    cid = first[0]["conversation_id"]
    assert ana.post(f"/conversations/{cid}/assume").status_code == 204
    assert "human_requested" not in kinds(ana) and "human_requested" not in kinds(panels["bia.teste"])


def test_new_request_after_resolution_gets_a_new_key(env):
    app, ids, panels, tmp_path = env
    waiting(tmp_path)
    admin = panels["admin.teste"]
    key1 = admin.get("/notifications").json()["items"][0]["key"]
    cid = conversation_id(admin, "waiting")
    admin.post(f"/conversations/{cid}/assume"); admin.post(f"/conversations/{cid}/resolve")
    admin.post(f"/conversations/{cid}/resume")
    waiting(tmp_path)
    key2 = [n for n in admin.get("/notifications").json()["items"] if n["type"] == "human_requested"][0]["key"]
    assert key1 != key2  # novo pedido = nova notificação (sem reaproveitar a já vista)


def test_transfer_and_new_client_message_notify_only_the_responsible(env):
    app, ids, panels, tmp_path = env
    ana, bia = panels["ana.teste"], panels["bia.teste"]
    waiting(tmp_path)
    cid = conversation_id(ana, "waiting")
    ana.post(f"/conversations/{cid}/assume")
    assert "assigned_to_me" not in kinds(ana)  # quem assumiu não é avisado de si mesmo
    assert ana.post(f"/conversations/{cid}/transfer", {"target_user_id": ids["bia.teste"]}).status_code == 204
    assert "assigned_to_me" in kinds(bia) and "assigned_to_me" not in kinds(ana)
    assert "new_message" not in kinds(bia)  # mensagem anterior à atribuição não conta
    customer(tmp_path, f"wamid.{uuid4().hex}", f"Ainda estou aqui. {SECRET_TEXT}", CLIENT_A)
    items = bia.get("/notifications").json()["items"]
    assert "new_message" in [n["type"] for n in items] and SECRET_TEXT not in json.dumps(items)
    assert "new_message" not in kinds(ana)
    bia.post(f"/conversations/{cid}/read")
    assert "new_message" not in kinds(bia)


@pytest.mark.parametrize("behavior,kind", [("reject", "send_failed"), ("timeout", "send_unknown")])
def test_send_failure_and_unknown_are_mandatory_and_scoped(env, behavior, kind):
    app, ids, panels, tmp_path = env
    ana, bia, admin = panels["ana.teste"], panels["bia.teste"], panels["admin.teste"]
    waiting(tmp_path)
    cid = conversation_id(ana, "waiting")
    ana.post(f"/conversations/{cid}/assume")
    assert reply(ana, cid).status_code == 200
    s = stack(tmp_path, transport=FakeWhatsAppTransport(behavior))
    drain(s.inbound, s.outbound, "w")
    for panel in (ana, admin):
        found = [n for n in panel.get("/notifications").json()["items"] if n["type"] == kind]
        assert len(found) == 1 and found[0]["mandatory"] is True and found[0]["severity"] == "critical"
    assert kind not in kinds(bia)  # falha de conversa alheia não vaza para outra atendente
    # Sem reenvio automático: nova drenagem não toca o transporte.
    assert drain(s.inbound, s.outbound, "w") == [] and len(s.transport.deliveries) == 1


def test_dispatch_halt_is_a_mandatory_system_notification(env):
    app, ids, panels, _ = env
    admin = panels["admin.teste"]
    assert admin.post("/dispatch/halt", {"reason": "Teste de interrupção"}).status_code == 204
    for panel in panels.values():
        halts = [n for n in panel.get("/notifications").json()["items"] if n["type"] == "dispatch_halted"]
        assert len(halts) == 1 and halts[0]["mandatory"] is True


def test_notifications_require_session(env):
    app, *_ = env
    assert Panel(app).get("/notifications").status_code == 401


# ------------------------------------------------------------ visão geral, urgência, filtros
def test_overview_counts_waiting_and_urgency_from_database(env):
    app, ids, panels, tmp_path = env
    waiting(tmp_path, CLIENT_A)
    waiting(tmp_path, CLIENT_B)
    admin = panels["admin.teste"]
    overview = admin.get("/overview").json()
    assert overview["waiting_unassigned"] == 2 and overview["oldest_waiting_minutes"] == 0
    assert overview["urgent_after_minutes"] == 15 and overview["inbox"] == {"DONE": 2}
    assert not any(item["urgent"] for item in admin.get("/conversations?filter=waiting").json()["items"])
    store = app.state.panel_store
    store.clock = lambda: utc_now() + timedelta(minutes=16)
    items = admin.get("/conversations?filter=waiting").json()["items"]
    assert all(item["urgent"] and item["waiting_since"] for item in items)
    assert admin.get("/overview").json()["oldest_waiting_minutes"] >= 16
    # Operador não recebe contadores técnicos da fila de canal.
    assert panels["ana.teste"].get("/overview").json()["inbox"] is None


def test_others_filter_shows_colleagues_attendances_to_admin_only(env):
    app, ids, panels, tmp_path = env
    waiting(tmp_path)
    ana = panels["ana.teste"]
    cid = conversation_id(ana, "waiting")
    ana.post(f"/conversations/{cid}/assume")
    assert [i["id"] for i in panels["admin.teste"].get("/conversations?filter=others").json()["items"]] == [cid]
    assert panels["bia.teste"].get("/conversations?filter=others").json()["items"] == []
    assert ana.get("/conversations?filter=others").json()["items"] == []


# ------------------------------------------------------------ histórico paginado
def test_history_is_paginated_without_gaps_or_overlap_and_respects_scope(env):
    app, ids, panels, tmp_path = env
    admin = panels["admin.teste"]
    for index in range(8):
        customer(tmp_path, f"wamid.{uuid4().hex}", f"Quanto custa o corte? {index}", CLIENT_A)
    cid = conversation_id(admin, "auto")
    detail = admin.get(f"/conversations/{cid}").json()
    assert len(detail["messages"]) == 10 and detail["has_more"] is True
    ordinals = [m["ordinal"] for m in detail["messages"]]
    older = admin.get(f"/conversations/{cid}/messages?before={detail['before']}").json()
    assert older["has_more"] is False
    combined = [m["ordinal"] for m in older["messages"]] + ordinals
    assert combined == sorted(set(combined)) and combined[0] == 0 and len(combined) == 16
    assert panels["ana.teste"].get(f"/conversations/{cid}/messages?before=5").status_code == 404
    assert admin.get(f"/conversations/{cid}/messages?before=abc").status_code == 422


def test_paginated_history_keeps_delivery_labels(env):
    app, ids, panels, tmp_path = env
    ana = panels["ana.teste"]
    waiting(tmp_path)
    cid = conversation_id(ana, "waiting")
    ana.post(f"/conversations/{cid}/assume")
    reply(ana, cid)
    s = stack(tmp_path)
    drain(s.inbound, s.outbound, "w")
    messages = ana.get(f"/conversations/{cid}").json()["messages"]
    assert messages[-1]["role"] == "operator" and messages[-1]["delivery"]["key"] == "accepted"
    assert messages[-1]["author"] == "Ana.Teste"
    assert any(m["role"] == "assistant" and m["delivery"] for m in messages)


# ------------------------------------------------------------ contatos excluídos
def _retire(app, tmp_path, sender=CLIENT_A):
    customer(tmp_path, f"wamid.{uuid4().hex}", "Oi", sender)
    repository = app.state.conversation_repository
    with db(app) as connection:
        cid = connection.execute("SELECT id FROM conversations ORDER BY created_at DESC LIMIT 1").fetchone()[0]
    repository.retire(cid, confirmation=f"DELETE_SYNTHETIC:{cid}")
    return cid


def test_retired_contact_is_blocked_visibly_and_content_discarded_by_default(env):
    app, ids, panels, tmp_path = env
    cid = _retire(app, tmp_path)
    assert customer(tmp_path, f"wamid.{uuid4().hex}", SECRET_TEXT, CLIENT_A) == ["retired_contact_blocked"]
    with db(app) as connection:
        row = connection.execute("SELECT status,payload FROM channel_inbox WHERE outcome='retired_contact_blocked'"
                                 ).fetchone()
        replies = connection.execute("SELECT count(*) FROM channel_outbox WHERE conversation_id=? AND status<>"
                                     "'SUPPRESSED'", (cid,)).fetchone()[0]
        state = connection.execute("SELECT retired,closed FROM conversations WHERE id=?", (cid,)).fetchone()
    assert row[0] == "DONE" and SECRET_TEXT not in row[1]  # conteúdo descartado (redigido)
    assert state == (1, 1)
    admin = panels["admin.teste"]
    assert "retired_contact" in kinds(admin) and "retired_contact" not in kinds(panels["ana.teste"])
    listed = admin.get("/retired-contacts").json()
    assert listed[0]["id"] == cid and listed[0]["attempts"] == 1
    assert admin.get("/overview").json()["retired_contact_attempts"] == 1
    assert replies == 1  # só a resposta da primeira conversa, antes da exclusão


def test_allow_policy_starts_a_new_empty_conversation_for_retired_contact(env):
    app, ids, panels, tmp_path = env
    cid = _retire(app, tmp_path)
    admin = panels["admin.teste"]
    admin.put("/settings", {"retired_contact_policy": "allow_new_conversation"})
    outcome = customer(tmp_path, f"wamid.{uuid4().hex}", "Quanto custa o corte?", CLIENT_A)
    assert "retired_contact_blocked" not in outcome and "ACCEPTED" in outcome
    detail = admin.get(f"/conversations/{cid}").json()
    assert detail["state"] == "AUTO" and [m["text"] for m in detail["messages"]][0] == "Quanto custa o corte?"
    assert len(detail["messages"]) == 2  # nada do histórico excluído volta
    actions = [r for r in app.state.conversation_repository.audit(cid) if r["operation"] == "contact_reinstated"]
    assert actions and actions[0]["origin"] == "client"


def test_manual_reinstatement_requires_admin_and_exact_confirmation(env):
    app, ids, panels, tmp_path = env
    cid = _retire(app, tmp_path)
    admin = panels["admin.teste"]
    assert panels["ana.teste"].post(f"/retired-contacts/{cid}/reinstate",
                                    {"confirmation": f"REATIVAR:{cid[-6:]}"}).status_code == 403
    assert admin.post(f"/retired-contacts/{cid}/reinstate", {"confirmation": "REATIVAR"}).status_code == 409
    assert admin.post(f"/retired-contacts/{cid}/reinstate", {"confirmation": f"REATIVAR:{cid[-6:]}"}).status_code == 204
    assert admin.post(f"/retired-contacts/{cid}/reinstate", {"confirmation": f"REATIVAR:{cid[-6:]}"}).status_code == 409
    outcomes = [(e["action"], e["outcome"]) for e in admin.get("/audit").json() if e["action"] == "contact_reinstated"]
    assert ("contact_reinstated", "denied") in outcomes and ("contact_reinstated", "success") in outcomes
    assert customer(tmp_path, f"wamid.{uuid4().hex}", "Quanto custa o corte?", CLIENT_A)[-1] == "ACCEPTED"
    assert admin.get("/retired-contacts").json() == []


# ------------------------------------------------------------ sistema
def test_system_view_is_admin_only_and_never_exposes_secrets(env):
    app, ids, panels, tmp_path = env
    waiting(tmp_path)
    assert panels["ana.teste"].get("/system").status_code == 403
    response = panels["admin.teste"].get("/system")
    body = response.json()
    assert body["schema_version"] == SCHEMA_VERSION and body["counts"]["conversations"] == 1 and body["transport"] == "fake"
    for secret in (APP_SECRET, CONVERSATION_KEY, VERIFY, CLIENT_A):
        assert secret not in response.text


# ------------------------------------------------------------ migração 009
def test_migration_009_preserves_existing_settings(tmp_path, monkeypatch):
    from rocha_reis_agent.persistence import sqlite as module
    path = tmp_path / "m.synthetic.sqlite3"
    monkeypatch.setattr(module, "SCHEMA_VERSION", 8)
    module.SQLiteConversationRepository(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("INSERT INTO panel_settings(key,value,updated_at) VALUES('operators_can_resume',1,'x')")
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError):  # esquema 8 não aceitava as chaves novas
            connection.execute("INSERT INTO panel_settings(key,value,updated_at) VALUES('urgent_after_minutes',5,'x')")
    monkeypatch.setattr(module, "SCHEMA_VERSION", 9)
    module.SQLiteConversationRepository(path)
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (9,)
        assert connection.execute("SELECT key,value FROM panel_settings").fetchall() == [("operators_can_resume", 1)]
        connection.execute("INSERT INTO panel_settings(key,value,updated_at) VALUES('urgent_after_minutes',5,'x')")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO panel_settings(key,value,updated_at) VALUES('ai_provider',1,'x')")
        indexes = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"transitions_conversation_current", "messages_conversation_role"} <= indexes


def test_new_mutating_routes_require_csrf_and_origin(env):
    app, ids, panels, tmp_path = env
    cid = _retire(app, tmp_path)
    admin = panels["admin.teste"]
    client = admin.client
    for method, path, body in (("put", "/api/panel/settings", {"urgent_after_minutes": 5}),
                               ("post", f"/api/panel/retired-contacts/{cid}/reinstate",
                                {"confirmation": f"REATIVAR:{cid[-6:]}"})):
        no_token = getattr(client, method)(path, json=body, headers={"Origin": admin.headers()["Origin"]})
        bad_origin = getattr(client, method)(path, json=body, headers={**admin.headers(), "Origin": "http://evil.example"})
        assert no_token.status_code == 403 and bad_origin.status_code == 403
    assert admin.get("/settings").json()["permissions"]["urgent_after_minutes"] == 15
    assert admin.get("/retired-contacts").json()[0]["id"] == cid
