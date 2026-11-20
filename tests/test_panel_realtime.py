"""Fase 7 — SSE: cursor persistido, reconexão, isolamento e revogação durante o stream."""

import json

import pytest

from panel_support import ORIGIN, Panel, add_user, conversation_id, create_panel_app, customer, panel_settings
from whatsapp_support import CLIENT_A, CLIENT_B


@pytest.fixture
def env(tmp_path):
    app = create_panel_app(panel_settings(tmp_path, panel_sse_max_seconds=1.5))
    ids = {login: add_user(app, login, role) for login, role in
           (("admin.teste", "ADMIN"), ("ana.teste", "OPERATOR"), ("bia.teste", "OPERATOR"))}
    panels = {login: Panel(app) for login in ids}
    for login, panel in panels.items():
        panel.login(login)
    return app, ids, panels, tmp_path


def read_events(panel, *, headers=None, params="", on_ready=None):
    """Lê o stream até o servidor encerrá-lo (rotação por tempo) e devolve os eventos."""
    events, current = [], {}
    with panel.client.stream("GET", f"/api/panel/events{params}", headers=headers or {}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("id: "):
                current["id"] = int(line[4:])
            elif line.startswith("event: "):
                current["event"] = line[7:]
            elif line.startswith("data: "):
                current["data"] = json.loads(line[6:])
            elif line == "" and current:
                events.append(current)
                if current.get("event") == "ready" and on_ready:
                    on_ready()
                current = {}
    return events


def changed(events):
    return [c for e in events if e.get("event") == "changes" for c in e["data"]["conversations"]]


def test_new_message_and_state_change_are_pushed_live(env):
    app, ids, panels, tmp_path = env
    customer(tmp_path, "wamid.r0", "Quero falar com uma pessoa")
    cid = conversation_id(panels["ana.teste"], "waiting")

    def produce():
        customer(tmp_path, "wamid.r1", "Alguém aí?")
        panels["bia.teste"].post(f"/conversations/{cid}/assume")

    events = read_events(panels["admin.teste"], on_ready=produce)
    assert events[0]["event"] == "ready"
    assert cid in changed(events)
    assert events[-1]["event"] == "rotate"  # stream rotaciona e o cliente reconecta revalidando a sessão
    ids_seen = [e["id"] for e in events if "id" in e]
    assert ids_seen == sorted(ids_seen)  # cursor monotônico


def test_reconnection_with_last_event_id_recovers_missed_changes(env):
    app, ids, panels, tmp_path = env
    store = app.state.panel_store
    cursor = store.latest_change()
    customer(tmp_path, "wamid.m1", "Quero falar com um atendente")  # acontece "desconectado"
    cid = conversation_id(panels["ana.teste"], "waiting")
    events = read_events(panels["ana.teste"], headers={"Last-Event-ID": str(cursor)})
    assert events[0]["data"]["cursor"] == cursor and cid in changed(events)
    later = read_events(panels["ana.teste"], params=f"?cursor={store.latest_change()}")
    assert changed(later) == []  # nada repetido após sincronizar


def test_pruned_cursor_forces_full_resync(env):
    app, ids, panels, tmp_path = env
    for index in range(3):
        customer(tmp_path, f"wamid.z{index}", "Olá", f"55119000001{index:02d}")
    store = app.state.panel_store
    store.prune_changes(keep=0)
    events = read_events(panels["admin.teste"], params="?cursor=1")
    assert any(e.get("event") == "reset" for e in events)


def test_events_respect_operator_isolation(env):
    app, ids, panels, tmp_path = env
    customer(tmp_path, "wamid.a0", "Quero falar com uma pessoa", CLIENT_A)
    cid = conversation_id(panels["ana.teste"], "waiting")
    panels["ana.teste"].post(f"/conversations/{cid}/assume")
    cursor = app.state.panel_store.latest_change()
    customer(tmp_path, "wamid.a1", "Mensagem privada do cliente A", CLIENT_A)
    customer(tmp_path, "wamid.auto", "Quanto custa o corte?", CLIENT_B)  # AUTO: só admin vê
    bia = read_events(panels["bia.teste"], params=f"?cursor={cursor}")
    ana = read_events(panels["ana.teste"], params=f"?cursor={cursor}")
    admin = read_events(panels["admin.teste"], params=f"?cursor={cursor}")
    assert cid not in changed(bia) and cid in changed(ana)
    assert any(e["data"].get("other") for e in bia if e.get("event") == "changes")  # sem IDs nem conteúdo
    assert len(set(changed(admin))) == 2
    raw = json.dumps(bia + ana)
    assert "Mensagem privada" not in raw and "custa" not in raw  # stream nunca leva conteúdo


def test_session_revoked_during_stream_closes_it(env):
    app, ids, panels, tmp_path = env
    admin = panels["admin.teste"]

    def revoke():
        admin.post(f"/users/{ids['ana.teste']}/deactivate")

    events = read_events(panels["ana.teste"], on_ready=revoke)
    assert events[-1]["event"] == "auth"


def test_stream_requires_authentication(env):
    app, *_ = env
    from fastapi.testclient import TestClient
    assert TestClient(app, base_url=ORIGIN).get("/api/panel/events").status_code == 401
