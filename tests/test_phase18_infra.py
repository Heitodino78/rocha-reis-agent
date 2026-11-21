"""Fase 18 — regressões dos achados da validação EXECUTÁVEL (systemd real, Caddy, WireGuard, nftables).

Os testes com serviços reais ficam em tests_infra/ (precisam do systemd do usuário, do binário do Caddy e de
namespaces). Aqui ficam as regressões rápidas e determinísticas de cada defeito encontrado lá, mais a
fidelidade das ferramentas de teste (o que elas mudam nas referências é só o documentado).
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from rocha_reis_agent.ops import deploy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests_infra"))
UNITS = ROOT / "deploy" / "systemd"


# ------------------------------------------------------------------ achado: atualização real sempre cancelada
def test_install_with_venv_records_the_final_interpreter_path(tmp_path, monkeypatch):
    """`instalar`/`atualizar` com venv gravavam em RUNTIME.json o Python da pasta de PREPARO, renomeada logo
    depois: o ensaio de migração de `ops atualizar` falhava com FileNotFoundError em toda atualização real
    (os testes da Fase 17 só usavam create_venv=False). O caminho gravado precisa existir após a instalação."""
    base_python = tmp_path / "sistema" / "python3.13"  # interpretador "do sistema", FORA da árvore da versão
    base_python.parent.mkdir()
    base_python.write_text("#!/bin/sh\n")
    os.chmod(base_python, 0o755)

    real_run = subprocess.run

    def fake_run(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
        if cmd[1:3] == ["-m", "venv"]:  # venv mínimo sem rede: bin/python -> interpretador base (como o venv real)
            (Path(cmd[3]) / "bin").mkdir(parents=True)
            os.symlink(base_python, Path(cmd[3]) / "bin" / "python")
            return subprocess.CompletedProcess(cmd, 0, b"", b"")
        if cmd[1:4] == ["-m", "pip", "install"]:
            return subprocess.CompletedProcess(cmd, 0, b"", b"")
        return real_run(cmd, *args, **kwargs)  # git ls-files do empacotamento

    monkeypatch.setattr(deploy.subprocess, "run", fake_run)
    package = deploy.build_package(tmp_path / "pacotes")
    installed = deploy.install_release(package, tmp_path / "raiz", create_venv=True)
    release = deploy.layout(tmp_path / "raiz")["releases"] / str(installed["id"])
    interpreter = Path(json.loads((release / "RUNTIME.json").read_text())["python"])
    assert interpreter == release / ".venv" / "bin" / "python"
    assert interpreter.exists(), "RUNTIME.json aponta para a pasta de preparo, que já não existe"
    # Achado relacionado: o chmod "somente leitura" seguia o link e alterava o Python do sistema.
    assert base_python.stat().st_mode & 0o777 == 0o755
    assert (release / "rocha_reis_agent" / "__init__.py").stat().st_mode & 0o222 == 0


# ------------------------------------------------------------------ achado: `reverter` reativava a versão quebrada
def test_failed_update_never_becomes_the_rollback_target(tmp_path):
    """Sob o systemd real: A → B (ok) → C (falha na prontidão, reversão automática para B). Depois disso o
    registro de versões dizia anterior=C, e `ops reverter` reativava justamente a versão que acabara de falhar."""
    from tests.server_support import package_from, source_copy
    root = tmp_path / "raiz"

    def version(label: str) -> Path:
        source = source_copy(tmp_path / f"fonte-{label}", edits={"rocha_reis_agent/__init__.py": (
            '__version__ = "', f'__version__ = "{label}.')})
        return package_from(source, tmp_path / f"pacote-{label}")

    first = deploy.install_release(version("1"), root, python=sys.executable, create_venv=False)
    deploy.activate(root, str(first["id"]))
    kwargs = dict(database=tmp_path / "sem-banco.sqlite3", key_file=tmp_path / "sem.key",
                  stop=lambda: None, start=lambda: None, python=sys.executable, create_venv=False)
    pkg_b = version("2")
    b = deploy.update(pkg_b, root, confirmation=f"ATUALIZAR:2.{_base_version()}", ready=lambda: True, **kwargs)
    assert b["resultado"] == "ATUALIZADA"
    answers = iter([False, True])  # C não fica pronta; B volta a ficar
    c = deploy.update(version("3"), root, confirmation=f"ATUALIZAR:3.{_base_version()}",
                      ready=lambda: next(answers), **kwargs)
    assert c["resultado"] == "REVERTIDA" and deploy.current_release(root) == b["id"]
    state = json.loads((deploy.layout(root)["estado"] / "versoes.json").read_text())
    assert state["anterior"] == first["id"] and state["atual"] == b["id"], state
    assert state["anterior"] != c["id"]


def _base_version() -> str:
    from rocha_reis_agent import __version__
    return __version__


# ------------------------------------------------------------------ achados nas unidades sob o systemd real
def _units() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in UNITS.glob("*.service")}


def test_every_unit_disables_core_dumps():
    """O watchdog abortou o worker com `code=dumped`: sem LimitCORE=0, a memória (conversas, segredos) ia para
    o coletor de core dumps do sistema."""
    for name, text in _units().items():
        assert re.search(r"^LimitCORE=0$", text, re.M), name


def test_alerts_unit_can_write_the_sqlite_wal_index():
    """Sob ReadOnlyPaths no diretório do banco, o SQLite em WAL não abre nem para leitura (precisa do -shm):
    o alerta virava 'banco_inacessivel' CRÍTICO a cada 5 min. Só o diretório do banco recebe escrita."""
    text = _units()["rocha-reis-alertas.service"]
    writable = [line.split("=", 1)[1].split() for line in text.splitlines() if line.startswith("ReadWritePaths=")]
    assert [["/var/lib/rocha-reis-agent/dados"]] == writable
    assert "PrivateNetwork=true" in text and "RestrictAddressFamilies=AF_UNIX" in text


def test_alerts_open_the_database_read_only_at_sql_level():
    """A escrita concedida ao diretório é só para o índice do WAL: a consulta continua mode=ro."""
    source = (ROOT / "rocha_reis_agent" / "ops" / "alerts.py").read_text(encoding="utf-8")
    assert 'as_uri() + "?mode=ro"' in source


# ------------------------------------------------------------------ achados no laboratório de rede (Caddy/VPN/nft)
def test_firewall_only_accepts_the_vpn_address_through_the_tunnel():
    """Sem túnel, um vizinho de rede roteava 10.8.0.1 pela interface pública e recebia o painel (HTTP 200):
    a regra do 443 público não olha o destino. A recusa precisa vir ANTES de qualquer aceite público."""
    rules = (ROOT / "deploy" / "nftables" / "rocha-reis.nft.exemplo").read_text(encoding="utf-8")
    lines = [line.strip() for line in rules.splitlines() if line.strip() and not line.strip().startswith("#")]
    guard = lines.index('ip daddr 10.8.0.0/24 iifname != "wg0" drop')
    first_public = next(i for i, line in enumerate(lines) if "tcp dport { 80, 443 }" in line)
    assert guard < first_public and guard < lines.index("ct state established,related accept")


def test_webhook_rate_limit_states_its_burst():
    """O padrão implícito do nftables (rajada 5) descartava a 6ª conexão nova seguida, medido no laboratório."""
    rules = (ROOT / "deploy" / "nftables" / "rocha-reis.nft.exemplo").read_text(encoding="utf-8")
    assert re.search(r"limit rate over 60/minute burst \d+ packets", rules)


def test_caddy_runtime_logs_drop_forwarded_client_address():
    """Avisos do reverse_proxy registram os cabeçalhos enviados ao app, inclusive o X-Forwarded-For com o IP
    completo do aparelho (encontrado no log real do Caddy)."""
    caddy = (ROOT / "deploy" / "caddy" / "Caddyfile.exemplo").read_text(encoding="utf-8")
    assert "request>headers>X-Forwarded-For delete" in caddy and "request>headers>Cookie delete" in caddy


# ------------------------------------------------------------------ alertas: todos os códigos (antes só 7 tinham teste)
ALL_ALERTS = {
    "entrada_parada": "CRITICO", "entrada_acumulada": "ATENCAO", "entrada_falhou": "ATENCAO",
    "capacidade_esgotada": "CRITICO", "saida_parada": "ATENCAO", "envio_incerto": "CRITICO",
    "envio_falhou": "ATENCAO", "envios_interrompidos": "ATENCAO", "cliente_esperando": "ATENCAO",
    "atendimento_orfao": "CRITICO", "falhas_login": "ATENCAO", "falhas_2fa": "ATENCAO",
    "falha_armazenamento": "ATENCAO", "disco_cheio": "CRITICO", "banco_perto_do_limite": "ATENCAO",
    "worker_sem_batimento": "CRITICO", "backup_atrasado": "CRITICO", "copia_externa_atrasada": "ATENCAO",
    "backup_falhando": "ATENCAO", "restauracao_falhou": "CRITICO",
}


def _metrics(now, **overrides):  # type: ignore[no-untyped-def]
    old = (now - __import__("datetime").timedelta(hours=1)).isoformat()
    base = {"entrada_pendente": 0, "entrada_pendente_mais_antiga": None, "entrada_falhas": 0,
            "capacidade_esgotada_24h": 0, "saida_pendente_mais_antiga": None, "saida_incerta": 0, "saida_falha": 0,
            "envios_interrompidos": False, "aguardando_mais_antigo": None, "aguardando_sem_responsavel": 0,
            "atendimento_com_inativo": 0, "falhas_login_24h": 0, "falhas_2fa_24h": 0,
            "falhas_armazenamento_24h": 0, "urgente_apos_minutos": 15, "esquema": 11}
    bad = {"entrada_pendente": 60, "entrada_pendente_mais_antiga": old, "entrada_falhas": 2,
           "capacidade_esgotada_24h": 1, "saida_pendente_mais_antiga": old, "saida_incerta": 1, "saida_falha": 1,
           "aguardando_mais_antigo": old, "atendimento_com_inativo": 1, "falhas_login_24h": 31,
           "falhas_2fa_24h": 11, "falhas_armazenamento_24h": 1}
    return {**base, **(bad if overrides.pop("all_bad", False) else {}), **overrides}


def test_every_alert_code_fires_with_its_documented_level(tmp_path, monkeypatch):
    from collections import namedtuple
    from datetime import datetime, timezone
    from rocha_reis_agent.ops import alerts
    now = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)
    database = tmp_path / "banco.sqlite3"
    database.write_bytes(b"x" * 900)
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(alerts, "collect", lambda path, now: _metrics(now, all_bad=True))
    monkeypatch.setattr(alerts.shutil, "disk_usage", lambda path: usage(100, 97, 3))
    beat = tmp_path / "batimento.json"
    beat.write_text(json.dumps({"at": now.timestamp() - 600}))
    offsite = tmp_path / "externo.json"
    offsite.write_text("{}")
    result = alerts.evaluate(database, backups=tmp_path / "sem-backups", heartbeat=beat, offsite_state=offsite,
                             database_limit_bytes=1000, now=now,
                             backup_status={"falhas_consecutivas": 2,
                                            "ultimo_teste_restauracao": {"resultado": "falhou", "quando": "x"}})
    fired = {a["codigo"]: a["nivel"] for a in result["alertas"]}
    expected = {k: v for k, v in ALL_ALERTS.items() if k != "envios_interrompidos"}
    assert fired == expected, set(fired) ^ set(expected)
    assert alerts.exit_code(result) == 2
    # Interrupção manual dos envios: avisa a interrupção e NÃO acusa "saída parada" (é intencional).
    monkeypatch.setattr(alerts, "collect", lambda path, now: _metrics(now, all_bad=True, envios_interrompidos=True))
    paused = {a["codigo"] for a in alerts.evaluate(database, now=now)["alertas"]}
    assert "envios_interrompidos" in paused and "saida_parada" not in paused
    # Disco "baixo" (entre 5% e 15%) é o degrau anterior de disco_cheio.
    monkeypatch.setattr(alerts.shutil, "disk_usage", lambda path: usage(100, 90, 10))
    monkeypatch.setattr(alerts, "collect", lambda path, now: _metrics(now))
    calm = alerts.evaluate(database, now=now, backup_status={"falhas_consecutivas": 0,
                                                              "ultimo_teste_restauracao": {"resultado": "ok"}})
    assert {a["codigo"] for a in calm["alertas"]} == {"disco_baixo"} and alerts.exit_code(calm) == 1


def test_monitoring_doc_lists_exactly_the_implemented_alerts():
    doc = (ROOT / "docs" / "MONITORAMENTO_E_ALERTAS.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"^\| `([a-z_]+)`(?: / `([a-z_]+)`)?", doc, re.M))
    names = {n for pair in documented for n in pair if n}
    source = (ROOT / "rocha_reis_agent" / "ops" / "alerts.py").read_text(encoding="utf-8")
    implemented = set(re.findall(r'add\("([a-z_]+)"', source))
    assert names == implemented, names ^ implemented


# ------------------------------------------------------------------ fidelidade das ferramentas de teste
def test_systemd_harness_changes_only_the_documented_lines(monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    from fase18_systemd import render_unit
    root, python_home = Path("/home/x/raiz"), Path("/home/x/py")
    for name, text in _units().items():
        rendered = render_unit(text, root, python_home)
        original = {l for l in text.splitlines() if l and not l.startswith("#")}
        kept = {l for l in rendered.splitlines() if l and not l.startswith("#")}
        removed = {l for l in original - kept
                   if not any(p in l for p in ("/opt/", "/etc/rocha", "/var/lib/", "/var/log/", "--raiz /"))}
        assert removed <= {"User=rocha-reis", "Group=rocha-reis", "ProtectHome=true"}, (name, removed)
        added = {l for l in kept - original if not str(root) in l}
        assert added <= {"ProtectHome=tmpfs", f"BindReadOnlyPaths={python_home}",
                         "BindReadOnlyPaths=-/run/user/1000/systemd/notify"}, (name, added)
        for directive in ("NoNewPrivileges=true", "ProtectSystem=strict", "SystemCallFilter=@system-service",
                          "CapabilityBoundingSet=", "LimitCORE=0"):
            assert directive in rendered, (name, directive)


def test_wireguard_test_keys_match_rfc7748_and_reference_parses():
    import wgnl
    # RFC 7748 §6.1 (par de chaves de Alice)
    private = bytes.fromhex("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
    public = bytes.fromhex("8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a")
    assert wgnl.pubkey(base64.b64encode(private).decode()) == base64.b64encode(public).decode()
    template = (ROOT / "deploy" / "wireguard" / "wg0.conf.exemplo").read_text(encoding="utf-8")
    key = wgnl.genkey()
    text = (template.replace("<CHAVE-PRIVADA-DO-SERVIDOR>", key).replace("<CHAVE-PUBLICA-DO-APARELHO>", wgnl.pubkey(key))
            .replace("<CHAVE-PRE-COMPARTILHADA-DO-APARELHO>", wgnl.genpsk()))
    config = wgnl.parse(text)
    assert config.listen_port == 51820 and config.addresses == ["10.8.0.1/24"]
    assert [p.allowed_ips for p in config.peers] == [["10.8.0.10/32"]]


def test_release_identification_moved_with_phase_18():
    from rocha_reis_agent import RELEASE_NAME, __version__
    from rocha_reis_agent import config as c
    assert RELEASE_NAME == "Rocha Reis Agent — RC1 Offline" and __version__ == "0.14.0rc1.post5"
    assert not any((c.GRAPH_TRANSPORT_ACTIVATION_AUTHORIZED, c.HOMOLOGATION_TRANSPORT_ACTIVATION_AUTHORIZED,
                    c.GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED, c.REAL_DATA_ACTIVATION_AUTHORIZED))


@pytest.mark.parametrize("name", ["fase18_systemd.py", "fase18_rede.py", "wgnl.py"])
def test_infra_tools_are_not_shipped_in_the_runtime_package(name):
    assert not any(f.startswith("tests_infra/") for f in deploy.runtime_files(ROOT))
    assert (ROOT / "tests_infra" / name).is_file()
