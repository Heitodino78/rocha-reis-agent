"""Apoio aos testes do painel: app real, SQLite temporário, usuários e conversas sintéticas."""

from pathlib import Path

from fastapi.testclient import TestClient

from rocha_reis_agent.panel.app import create_panel_app
from rocha_reis_agent.panel.security import hash_password
from rocha_reis_agent.panel.store import SQLitePanelStore
from whatsapp_support import CLIENT_A, CLIENT_B, envelope, settings, stack, text_message
from rocha_reis_agent.channels.service import drain

ORIGIN = "http://127.0.0.1:8000"
PASSWORD = "Sintetica-Senha-2026!"


def panel_settings(tmp_path: Path, **changes: object):
    values: dict[str, object] = dict(app_env="test", panel_enabled=True, panel_cookie_secure=False,
                                     panel_origin=ORIGIN, panel_sse_poll_seconds=0.1, panel_sse_max_seconds=2.0)
    values.update(changes)
    return settings(tmp_path, **values)


def add_user(app, login: str, role: str = "OPERATOR", *, name: str | None = None, must_change: bool = False) -> str:
    store: SQLitePanelStore = app.state.panel_store
    return store.create_user(login=login, display_name=name or login.title(), password_hash=hash_password(PASSWORD),
                             role=role, must_change_password=must_change, actor_id=None).id


class Panel:
    """Cliente HTTP autenticado (cookie + Origin + CSRF), como o navegador do painel."""

    def __init__(self, app) -> None:
        self.client = TestClient(app, base_url=ORIGIN)
        self.csrf = ""

    def login(self, login: str, password: str = PASSWORD):
        response = self.client.post("/api/panel/auth/login", json={"login": login, "password": password},
                                    headers={"Origin": ORIGIN})
        if response.status_code == 200:
            self.csrf = response.json()["csrf_token"]
        return response

    def headers(self) -> dict[str, str]:
        return {"Origin": ORIGIN, "X-CSRF-Token": self.csrf}

    def get(self, path: str, **kwargs):
        return self.client.get(f"/api/panel{path}", **kwargs)

    def post(self, path: str, json: dict | None = None, **kwargs):
        return self.client.post(f"/api/panel{path}", json=json if json is not None else {}, headers=self.headers(), **kwargs)

    def put(self, path: str, json: dict):
        return self.client.put(f"/api/panel{path}", json=json, headers=self.headers())


def customer(tmp_path: Path, wamid: str, text: str, sender: str = CLIENT_A, **kwargs) -> list[str]:
    """Mensagem sintética pelo canal (inbox → núcleo → outbox fake), no MESMO banco do painel."""
    s = stack(tmp_path, **kwargs)
    s.ingestor.ingest(envelope(text_message(wamid, text, sender)))
    return drain(s.inbound, s.outbound, "test-worker")


def conversation_id(panel: Panel, filter_key: str = "all") -> str:
    items = panel.get(f"/conversations?filter={filter_key}").json()["items"]
    assert items, "nenhuma conversa visível"
    return items[0]["id"]


__all__ = ["CLIENT_A", "CLIENT_B", "ORIGIN", "PASSWORD", "Panel", "add_user", "conversation_id", "create_panel_app",
           "customer", "panel_settings"]
