"""Cinco travas: detecção AUTOMÁTICA de divergência entre a fonte única (`config.activation_locks()`) e todas as
representações operacionais (revisão funcional, 22/11/2026).

Cada trava é aberta SOMENTE na memória deste teste (monkeypatch); o código continua com as cinco em False.
Uma trava nova em config.py que não chegue a algum relatório faz este arquivo falhar.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from panel_support import Panel, add_user, create_panel_app, panel_settings

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "rocha_reis_agent" / "config.py").read_text(encoding="utf-8")
DECLARED = re.findall(r"^([A-Z_]+_ACTIVATION_AUTHORIZED): Final = (\w+)", SOURCE, re.M)
NAMES = [name for name, _ in DECLARED]


def test_exactly_five_locks_declared_and_all_false():
    from rocha_reis_agent import config
    assert len(DECLARED) == 5 and {value for _, value in DECLARED} == {"False"}, DECLARED
    assert config.activation_locks() == {"meta_graph": False, "meta_homologacao": False, "gemini_real": False,
                                         "dados_reais": False, "backup_externo": False}


# Portões (não relatórios): cada condição lê a trava da PRÓPRIA integração.
GATES = {
    "rocha_reis_agent/bootstrap.py",            # transporte real (Graph OU homologação, pelo ambiente) e Gemini
    "rocha_reis_agent/evaluation/real_run.py",  # Gemini real; recusa se qualquer trava da Meta estiver aberta
}


def test_no_module_builds_its_own_list_of_locks():
    """Gates individuais leem a PRÓPRIA trava; listas (relatórios) só pela fonte única."""
    offenders = {}
    for path in (ROOT / "rocha_reis_agent").rglob("*.py"):
        if path.name == "config.py" or str(path.relative_to(ROOT)) in GATES:
            continue
        code = "\n".join(line for line in path.read_text(encoding="utf-8").splitlines()
                         if not line.lstrip().startswith("#"))
        used = {name for name in NAMES if re.search(rf"\.{name}\b", code)}
        if len(used) >= 3:
            offenders[str(path.relative_to(ROOT))] = sorted(used)
    assert offenders == {}, offenders


@pytest.fixture(scope="module")
def demo_profile(tmp_path_factory):  # type: ignore[no-untyped-def]
    from rocha_reis_agent.ops.demo import build_demo
    profile = tmp_path_factory.mktemp("travas") / "demo"
    build_demo(profile)
    return profile


def _walk(value):  # type: ignore[no-untyped-def]
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)


@pytest.mark.parametrize("lock", NAMES)
def test_every_operational_representation_matches_the_central_list(tmp_path, monkeypatch, demo_profile, lock):
    from rocha_reis_agent import config
    from rocha_reis_agent.ops import deploy, environments, launcher, readiness, rehearsal, release
    from rocha_reis_agent.ops.backup import BackupError
    from rocha_reis_agent.panel.api import PANEL_LOCK_KEYS
    monkeypatch.setattr(config, lock, True)
    central = config.activation_locks()
    assert len(central) == len(NAMES) and sum(central.values()) == 1, central  # a fonte única vê esta trava

    assert release.release_info()["travas"] == central                                    # manifesto/versão
    assert release.freeze(tmp_path / "manifesto.json")["travas"] == central                # congelamento
    assert deploy.validate_installation(tmp_path / "vazia")["travas"] == central           # validar-instalacao
    assert readiness.server_state(tmp_path / "vazia", probe=lambda *a, **k: None)["travas"] == central  # estado-servidor
    reported = [v for v in _walk(environments.diagnose(demo_profile)) if isinstance(v, dict) and set(v) == set(central)]
    assert reported and all(v == central for v in reported)                                # ops ambiente

    app = create_panel_app(panel_settings(tmp_path))                                       # painel / API administrativa
    add_user(app, "admin.tv", "ADMIN")
    admin = Panel(app); admin.login("admin.tv")
    assert set(PANEL_LOCK_KEYS) == set(central)
    assert admin.get("/system").json()["locks"] == {PANEL_LOCK_KEYS[k]: v for k, v in central.items()}
    js = (ROOT / "rocha_reis_agent" / "panel" / "static" / "app.js").read_text(encoding="utf-8")
    assert all(re.search(rf"\b{key}: \"", js) for key in PANEL_LOCK_KEYS.values())        # rótulo na tela Sistema

    with pytest.raises(launcher.LaunchError, match="Trava"):                               # inicialização
        launcher.preflight(demo_profile, 8765)
    with pytest.raises(BackupError, match="trava"):                                        # ensaio
        rehearsal.run_rehearsal(tmp_path / "roteiro")


def test_official_rc1_manifest_lists_the_five_locks_closed():
    from rocha_reis_agent import config
    manifest = json.loads((ROOT / "docs" / "homologacao" / "RC1_MANIFESTO.json").read_text(encoding="utf-8"))
    assert manifest["travas"] == config.activation_locks()
