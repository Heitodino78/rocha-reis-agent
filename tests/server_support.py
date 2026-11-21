"""Suporte dos testes da Fase 17: servidor SIMULADO numa raiz descartável.

A pré-produção exige volume com cifragem VERIFICADA (sem exceção para testes), por isso a raiz fica num
diretório temporário dentro do $HOME quando ele está em LUKS/dm-crypt; sem isso os testes que sobem
serviços são pulados (e dizem o motivo). Os serviços rodam a partir da VERSÃO INSTALADA
(opt/rocha-reis-agent/current), não do repositório, com ROCHA_REIS_PROFILE apontando o perfil — como as
unidades systemd de deploy/systemd.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from typing import Iterator

import pytest

from rocha_reis_agent.ops import deploy
from rocha_reis_agent.ops.localserver import LocalServices, free_port  # noqa: F401
from rocha_reis_agent.persistence.storage_security import volume_protection

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def encrypted_base() -> Path:
    base = Path.home() / ".cache"
    protection = volume_protection(base)
    if not (protection.encrypted and protection.persistent):
        pytest.skip(f"Requer volume com cifragem verificada no $HOME ({protection.describe()}).")
    base.mkdir(parents=True, exist_ok=True)
    return base


@contextmanager
def encrypted_tmp() -> Iterator[Path]:
    path = Path(tempfile.mkdtemp(prefix="rr-f17-teste-", dir=encrypted_base()))
    os.chmod(path, 0o700)
    try:
        yield path
    finally:
        for directory, dirs, files in os.walk(path):  # versões instaladas são somente leitura
            os.chmod(directory, 0o700)
        shutil.rmtree(path, ignore_errors=True)


def source_copy(target: Path, *, edits: dict[str, tuple[str, str]] | None = None,
                extra: dict[str, str] | None = None) -> Path:
    """Cópia dos arquivos de execução do projeto, com alterações para fabricar versões de teste."""
    for name in deploy.runtime_files(ROOT):
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)
    for name, (old, new) in (edits or {}).items():
        text = (target / name).read_text(encoding="utf-8")
        assert old in text, (name, old)
        (target / name).write_text(text.replace(old, new, 1), encoding="utf-8")
    for name, content in (extra or {}).items():
        (target / name).write_text(content, encoding="utf-8")
    return target


def package_from(source: Path, destination: Path, extra_names: tuple[str, ...] = ()) -> Path:
    """Empacota uma cópia alterada (inclui arquivos novos, como uma migração de teste)."""
    original = deploy.runtime_files
    deploy.runtime_files = lambda root: sorted({*original(ROOT), *extra_names})  # type: ignore[assignment]
    try:
        return deploy.build_package(destination, source_root=source)
    finally:
        deploy.runtime_files = original  # type: ignore[assignment]


def tune_profile(root: Path, **settings: object) -> None:
    profile = deploy.layout(root)["config"] / "perfil.json"
    data = json.loads(profile.read_text(encoding="utf-8"))
    data["settings"].update(settings)
    os.chmod(profile, 0o600)
    profile.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


class Services(LocalServices):
    """Mesmo supervisor do ensaio operacional (rocha_reis_agent/ops/localserver.py)."""


def wait_until(condition, timeout: float = 30.0, interval: float = 0.2) -> bool:  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return False


def installed_root(base: Path) -> Path:
    """Pacote da árvore atual → instalação → instância de pré-produção sintética. Devolve a raiz."""
    package = deploy.build_package(base / "pacotes")
    root = base / "raiz"
    result = deploy.install_release(package, root, python=PYTHON, create_venv=False)
    deploy.activate(root, str(result["id"]))
    deploy.prepare_instance(root)
    return root
