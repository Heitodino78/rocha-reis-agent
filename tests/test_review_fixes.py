"""Revisão final — regressões dos defeitos confirmados na revisão do projeto:
contagem de "Meus atendimentos", desativação com atendimentos em andamento, histórico com posições
estáveis além do limite, redefinição de senha pela administração e transição auditada."""

from uuid import uuid4

import pytest

from panel_support import PASSWORD, Panel, add_user, conversation_id, create_panel_app, customer, panel_settings

CAP = 24  # limite de histórico pequeno para atravessá-lo no teste


@pytest.fixture
def env(tmp_path):
    app = create_panel_app(panel_settings(tmp_path, sim_max_history_entries=CAP, panel_history_page_size=10))
    ids = {login: add_user(app, login, role) for login, role in
           (("admin.teste", "ADMIN"), ("ana.teste", "OPERATOR"), ("bia.teste", "OPERATOR"))}
    panels = {login: Panel(app) for login in ids}
    for login, panel in panels.items():
        panel.login(login)
    return app, ids, panels, tmp_path


def say(tmp_path, text, sender="5511900000901"):
    customer(tmp_path, f"wamid.{uuid4().hex}", text, sender, sim_max_history_entries=CAP)


def assume(env, sender="5511900000901"):
    app, ids, panels, tmp_path = env
    say(tmp_path, "Quero falar com uma pessoa", sender)
    cid = conversation_id(panels["ana.teste"], "waiting")
    assert panels["ana.teste"].post(f"/conversations/{cid}/assume").status_code == 204
    return cid


def test_mine_counts_and_lists_only_ongoing_attendances(env):
    app, ids, panels, tmp_path = env
    ana = panels["ana.teste"]
    cid = assume(env)
    assert ana.get("/overview").json()["assigned_to_me"] == 1
    assert [i["id"] for i in ana.get("/conversations?filter=mine").json()["items"]] == [cid]
    assert ana.post(f"/conversations/{cid}/resolve").status_code == 204
    # Finalizado: some de "Meus atendimentos" e do contador, continua visível em "Encerradas".
    assert ana.get("/overview").json()["assigned_to_me"] == 0
    assert ana.get("/conversations?filter=mine").json()["items"] == []
    assert [i["id"] for i in ana.get("/conversations?filter=resolved").json()["items"]] == [cid]
    assert ana.get(f"/conversations/{cid}").status_code == 200


def test_deactivating_returns_ongoing_attendances_to_the_queue(env):
    app, ids, panels, tmp_path = env
    admin, bia = panels["admin.teste"], panels["bia.teste"]
    cid = assume(env)
    assert admin.post(f"/users/{ids['ana.teste']}/deactivate").status_code == 204
    detail = admin.get(f"/conversations/{cid}").json()
    assert detail["state"] == "WAITING_HUMAN" and detail["assigned"] is None
    assert detail["transitions"][-1]["action"] == "owner_deactivated"
    assert admin.get("/overview").json()["orphaned_active"] == 0
    actions = [r["action"] for r in admin.get("/audit").json()]
    assert "conversation_requeued" in actions and "user_deactivated" in actions
    # Outra atendente vê na fila e assume normalmente; a automação continua suspensa.
    assert cid in [i["id"] for i in bia.get("/conversations?filter=waiting").json()["items"]]
    assert bia.post(f"/conversations/{cid}/assume").status_code == 204
    assert admin.get(f"/conversations/{cid}").json()["state"] == "HUMAN_ACTIVE"


def test_finished_attendance_of_deactivated_person_is_not_requeued(env):
    app, ids, panels, tmp_path = env
    admin, ana = panels["admin.teste"], panels["ana.teste"]
    cid = assume(env)
    assert ana.post(f"/conversations/{cid}/resolve").status_code == 204
    assert admin.post(f"/users/{ids['ana.teste']}/deactivate").status_code == 204
    assert admin.get(f"/conversations/{cid}").json()["state"] == "RESOLVED"


def test_overview_flags_ongoing_attendance_left_with_inactive_person(env):
    app, ids, panels, tmp_path = env
    admin = panels["admin.teste"]
    cid = assume(env)
    # Simula a devolução interrompida (ex.: falha de gravação): desativa direto no banco do painel.
    with app.state.panel_store._tx() as connection:
        connection.execute("UPDATE panel_users SET active=0 WHERE id=?", (ids["ana.teste"],))
    assert admin.get("/overview").json()["orphaned_active"] == 1
    assert panels["bia.teste"].get("/overview").json()["orphaned_active"] is None  # só a administração
    assert admin.get(f"/conversations/{cid}").json()["state"] == "HUMAN_ACTIVE"


def test_history_positions_stay_stable_past_the_limit(env):
    app, ids, panels, tmp_path = env
    admin = panels["admin.teste"]
    say(tmp_path, "Oi")
    cid = conversation_id(admin, "all")
    seen = []
    for index in range(CAP // 2 + 4):  # cliente + assistente por mensagem: atravessa o limite várias vezes
        say(tmp_path, f"Quanto custa o corte? ({index})")
        with app.state.panel_store._read() as connection:
            rows = connection.execute("SELECT ordinal FROM messages WHERE conversation_id=? ORDER BY ordinal",
                                      (cid,)).fetchall()
        ordinals = [r[0] for r in rows]
        assert len(ordinals) <= CAP and ordinals == sorted(set(ordinals))
        seen.append(ordinals[-1])
    assert seen == sorted(set(seen))  # a última posição sempre cresce (nunca renumera)
    # Paginação: páginas não se repetem mesmo com mensagem nova entre uma página e outra.
    first = admin.get(f"/conversations/{cid}").json()  # como o painel: abre com a página mais recente
    assert first["has_more"] and first["before"] is not None
    say(tmp_path, "Mais uma pergunta sobre o corte")
    older = admin.get(f"/conversations/{cid}/messages?before={first['before']}").json()
    shown = [m["text"] for m in first["messages"]]
    loaded = [m["text"] for m in older["messages"]]
    client_texts = [t for t in shown + loaded if t.startswith("Quanto custa")]
    assert len(client_texts) == len(set(client_texts))  # nenhuma pergunta do cliente repetida
    assert max(m["ordinal"] for m in older["messages"]) < min(m["ordinal"] for m in first["messages"])


def test_new_message_notice_keeps_arriving_past_the_limit(env):
    app, ids, panels, tmp_path = env
    ana = panels["ana.teste"]
    cid = assume(env)
    keys = []
    for index in range(CAP + 3):
        say(tmp_path, f"Ainda estou aguardando ({index})")
        notices = [n["key"] for n in ana.get("/notifications").json()["items"]
                   if n["type"] == "new_message" and n["conversation_id"] == cid]
        keys.append(notices[0] if notices else None)
    assert None not in keys and len(set(keys)) == len(keys)  # cada mensagem nova gera um aviso novo


def test_admin_resets_password_and_person_must_change_it(env):
    app, ids, panels, tmp_path = env
    admin, ana = panels["admin.teste"], panels["ana.teste"]
    temporary = "Temporaria-Redefinida-2026"
    assert admin.post(f"/users/{ids['ana.teste']}/reset-password", {"password": temporary}).status_code == 204
    assert ana.get("/auth/session").status_code == 401  # sessão antiga encerrada
    again = Panel(app)
    assert again.login("ana.teste", PASSWORD).status_code == 401
    assert again.login("ana.teste", temporary).status_code == 200
    assert again.get("/auth/session").json()["user"]["must_change_password"] is True
    assert panels["bia.teste"].post(f"/users/{ids['ana.teste']}/reset-password",
                                    {"password": temporary}).status_code == 403
    assert "password_reset" in [r["action"] for r in admin.get("/audit").json()]


def test_release_identification_moved_with_the_review_fixes():
    from rocha_reis_agent import RELEASE_NAME, __version__
    assert RELEASE_NAME == "Rocha Reis Agent — RC1 Offline"
    assert __version__ not in ("0.14.0rc1", "0.14.0rc1.post1", "0.14.0rc1.post2")  # revisão final em diante
