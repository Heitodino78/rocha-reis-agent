"""Supervisor LOCAL (Fase 17) no papel do systemd, para ensaios e testes do servidor simulado.

Sobe webhook, painel e worker da versão ATIVA (`opt/rocha-reis-agent/current`) de uma raiz descartável,
com ROCHA_REIS_PROFILE apontando o perfil — os mesmos comandos de deploy/systemd, mas sem instalar nada no
sistema. Só 127.0.0.1, portas livres escolhidas na hora. Não é para produção: lá quem supervisiona é o
systemd (Restart=, WatchdogSec=).
"""

from __future__ import annotations

import os
from pathlib import Path
import signal
import socket
import subprocess
import sys

from rocha_reis_agent.ops import deploy


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class LocalServices:
    def __init__(self, root: Path, *, python: str | None = None, panel_host: str = "painel.interno") -> None:
        self.root = Path(root)
        self.python = python or sys.executable
        self.panel_host = panel_host
        self.ports = {"webhook": free_port(), "panel": free_port()}
        self.processes: dict[str, subprocess.Popen] = {}
        state = deploy.layout(root)["estado"]
        self.heartbeat = state / "worker.json"
        self.send_log = state / "envios-simulados.jsonl"
        self.exit_codes: dict[str, int | None] = {}

    def _env(self, release: Path) -> dict[str, str]:
        return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.root),
                "PYTHONPATH": str(release), "PYTHONDONTWRITEBYTECODE": "1",
                "ROCHA_REIS_PROFILE": str(deploy.layout(self.root)["config"]),
                "WORKER_HEARTBEAT_FILE": str(self.heartbeat), "WORKER_FAKE_SEND_LOG": str(self.send_log)}

    def start(self, only: tuple[str, ...] = ("webhook", "panel", "worker")) -> None:
        release = deploy.layout(self.root)["current"].resolve()
        commands = {
            "webhook": [self.python, "-m", "uvicorn", "rocha_reis_agent.main:create_app", "--factory", "--host",
                        "127.0.0.1", "--port", str(self.ports["webhook"]), "--no-access-log",
                        "--timeout-graceful-shutdown", "5"],
            "panel": [self.python, "-m", "uvicorn", "rocha_reis_agent.panel.app:create_panel_app", "--factory",
                      "--host", "127.0.0.1", "--port", str(self.ports["panel"]), "--no-access-log",
                      "--timeout-graceful-shutdown", "3"],
            "worker": [self.python, "-m", "rocha_reis_agent.worker"],
        }
        for name in only:
            descriptor = os.open(self.root / f"servico-{name}.log", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                self.processes[name] = subprocess.Popen(commands[name], cwd=release, env=self._env(release),
                                                        stdout=subprocess.DEVNULL, stderr=descriptor)
            finally:
                os.close(descriptor)

    def stop(self, *, sig: int = signal.SIGTERM, timeout: float = 20.0) -> dict[str, int | None]:
        for process in self.processes.values():
            if process.poll() is None:
                process.send_signal(sig)
        for name, process in self.processes.items():
            try:
                self.exit_codes[name] = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
                self.exit_codes[name] = None
        self.processes = {}
        return dict(self.exit_codes)

    def kill(self, name: str) -> None:
        """Queda abrupta (kill -9), como falta de energia para aquele processo."""
        process = self.processes.pop(name)
        process.send_signal(signal.SIGKILL)
        process.wait(timeout=10)

    def urls(self) -> list[str]:
        return [f"http://127.0.0.1:{self.ports['webhook']}/ready", f"http://127.0.0.1:{self.ports['panel']}/ready"]

    def ready(self, timeout: float = 30.0) -> bool:
        panel = self.urls()[1]
        return deploy.probe_http(self.urls(), timeout=timeout, hosts={panel: self.panel_host})
