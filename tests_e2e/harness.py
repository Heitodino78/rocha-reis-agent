"""Servidor real do painel (uvicorn em 127.0.0.1, porta livre) para testes de navegador."""

import socket
import sys
import threading
import time
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from panel_support import PASSWORD, add_user, customer, panel_settings  # noqa: E402
from rocha_reis_agent.panel.app import create_panel_app  # noqa: E402


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class PanelServer:
    def __init__(self, directory: Path, **changes: object) -> None:
        self.directory = directory
        self.port = free_port()
        self.origin = f"http://127.0.0.1:{self.port}"
        values: dict[str, object] = {"panel_sse_poll_seconds": 0.3, "panel_sse_max_seconds": 60.0}
        values.update(changes)
        self.settings = panel_settings(directory, panel_origin=self.origin, panel_embedded_worker=True, **values)
        self.app = create_panel_app(self.settings)
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning",
                                                    access_log=False, lifespan="on"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> "PanelServer":
        self.thread.start()
        deadline = time.monotonic() + 15
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("Servidor do painel não iniciou.")
            time.sleep(0.05)
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)

    def user(self, login: str, role: str = "OPERATOR", **kwargs: object) -> str:
        return add_user(self.app, login, role, **kwargs)

    def customer(self, wamid: str, text: str, sender: str = "5511900000001") -> list[str]:
        return customer(self.directory, wamid, text, sender)


__all__ = ["PASSWORD", "PanelServer"]
