"""Fase 15 — congelamento verificável da candidata, registro da homologação 11B (em branco, fora do
repositório, sem aprovação automática), isolamento demonstração × pré-produção e iniciador."""

import csv
from hashlib import sha256
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

import pytest

from rocha_reis_agent import __version__
from rocha_reis_agent.ops import release, uat
from rocha_reis_agent.ops.backup import BackupError

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------ congelamento da candidata
def test_freeze_and_verify_detect_changed_added_and_removed_files(tmp_path):
    manifest = tmp_path / "RC1_MANIFESTO.json"
    frozen = release.freeze(manifest)
    assert frozen["versao"] == __version__ and frozen["arquivos"] > 100
    assert set(frozen["travas"].values()) == {False}
    assert release.verify(manifest)["identica"] is True
    data = json.loads(manifest.read_text())
    names = sorted(data["arquivos"])
    data["arquivos"][names[0]] = "0" * 16          # alterado
    del data["arquivos"][names[1]]                  # "novo" para o manifesto
    data["arquivos"]["arquivo/que/sumiu.py"] = "1" * 16  # removido
    manifest.write_text(json.dumps(data))
    result = release.verify(manifest)
    assert result["identica"] is False
    assert result["alterados"] == [names[0]] and result["novos"] == [names[1]]
    assert result["removidos"] == ["arquivo/que/sumiu.py"]
    assert release.verify(tmp_path / "nao-existe.json")["identica"] is False


def test_manifest_and_release_documents_do_not_change_the_fingerprint():
    assert {"docs/homologacao/RC1_MANIFESTO.json", "docs/RELEASE_CANDIDATE_RC1.md",
            "docs/RELATORIO_PRONTIDAO_RC1.md"} <= release.SELF_REFERENCING
    assert not any(name in release.SELF_REFERENCING for name in release.project_files())


# ------------------------------------------------------------ registro da homologação
def test_register_is_blank_stamped_private_and_outside_the_repository(tmp_path):
    result = uat.generate(tmp_path / "sessao.csv")
    assert result["cenarios"] == 31 == len(uat.scenarios())
    path = Path(result["registro"])
    assert path.stat().st_mode & 0o777 == 0o600 and Path(result["problemas"]).stat().st_mode & 0o777 == 0o600
    with open(path, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter=";"))
    assert [r["cenario"] for r in rows][:2] == ["AT-01", "AT-02"] and rows[-1]["cenario"] == "AD-14"
    assert sum(r["perfil"] == "Atendente" for r in rows) == 17 and sum(r["perfil"] == "Administração" for r in rows) == 14
    for row in rows:
        assert all(row[field] == "" for field in uat.FILLED_BY_PEOPLE), row["cenario"]  # nada preenchido
        assert row["versao_testada"] == __version__ and row["resultado_esperado"] and row["criterio"]
        assert "`" not in json.dumps(row)
    with pytest.raises(BackupError, match="FORA do repositório"):
        uat.generate(ROOT / "docs" / "registro.csv")
    with pytest.raises(BackupError, match="nunca é sobrescrito"):
        uat.generate(tmp_path / "sessao.csv")


def test_check_only_reports_and_never_fills_or_approves(tmp_path):
    path = Path(uat.generate(tmp_path / "s.csv")["registro"])
    with open(path, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter=";"))
    rows[0].update(situacao="Aprovado", data="01/12/2026", participante="P1", resultado_observado="Entrou",
                   acompanhado_por="Condutor")
    rows[1].update(situacao="Aprovado")                         # incompleto
    rows[2].update(situacao="talvez")                           # inválido
    rows[3].update(situacao="Reprovado", data="x", participante="P2", resultado_observado="Não achou",
                   acompanhado_por="C", versao_testada="0.0.1")  # outra versão
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=uat.FIELDS, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
    before = sha256(path.read_bytes()).hexdigest()
    report = uat.check(path)
    assert sha256(path.read_bytes()).hexdigest() == before  # a conferência não altera o arquivo
    assert report["resumo"] == {"Aprovado": 2, "Reprovado": 1, "Não executado": 0}
    assert len(report["sem_resultado"]) == 27
    text = " ".join(report["problemas"])
    assert "AT-02" in text and "faltam" in text and "'talvez' inválida" in text and "0.0.1" in text
    assert report["cenarios_chave_secao_7"]["AT-01"] == ["Aprovado"]
    assert report["cenarios_chave_secao_7"]["AD-13"] == ["sem registro"]
    assert "responsável pelo salão" in report["aviso"]


# ------------------------------------------------------------ demonstração não contorna a pré-produção
def test_demo_cannot_be_used_to_bypass_preproduction(tmp_path, monkeypatch):
    from rocha_reis_agent.config import Settings
    from rocha_reis_agent.ops import environments
    from rocha_reis_agent.ops.demo import build_demo, profile_settings
    from rocha_reis_agent.panel.app import create_panel_app
    from rocha_reis_agent.persistence import storage_security
    from panel_support import Panel
    protected = storage_security.VolumeProtection(True, True, "dm-crypt LUKS2")
    monkeypatch.setattr(storage_security, "volume_protection", lambda path, **_: protected)
    monkeypatch.setattr(environments, "volume_protection", lambda path, **_: protected)
    demo, preprod = tmp_path / "demo", tmp_path / "preprod"
    build_demo(demo)
    environments.build_preproduction(preprod)
    demo_values = json.loads((demo / "perfil.json").read_text())["settings"]
    preprod_db = preprod / "dados" / "rocha-reis.preprod.synthetic.sqlite3"
    # 1) Perfil de demonstração apontado para o banco da pré-produção: recusado na configuração.
    with pytest.raises(ValueError, match="pré-produção"):
        Settings(_env_file=None, **{**demo_values, "storage_path": preprod_db, "environment_profile": "demo"})
    # 2) Credenciais da demonstração não existem na pré-produção.
    app = create_panel_app(profile_settings(preprod, port=8443))
    panel = Panel(app)
    panel.client.base_url = "https://127.0.0.1:8443"
    user, password = (demo / "credenciais-demo.txt").read_text().splitlines()[1].split("\t")[::2]
    response = panel.client.post("/api/panel/auth/login", json={"login": user, "password": password},
                                 headers={"Origin": "https://127.0.0.1:8443"})
    assert response.status_code == 401
    # 3) A pré-produção não herda nada "facilitado" da demonstração: 2FA obrigatório e HTTPS.
    settings = profile_settings(preprod)
    assert settings.panel_mfa_required and settings.panel_origin.startswith("https://")


# ------------------------------------------------------------ iniciador
def test_launcher_announces_ready_only_after_the_server_is_up(tmp_path):
    from rocha_reis_agent.ops.demo import build_demo
    build_demo(tmp_path / "demo")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen([sys.executable, "-m", "rocha_reis_agent.ops", "iniciar", "--perfil",
                                str(tmp_path / "demo"), "--porta", str(port)], cwd=ROOT,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    output = ""
    deadline = time.monotonic() + 30
    try:
        while "Ctrl+C" not in output and time.monotonic() < deadline:
            line = process.stdout.readline()
            if not line:
                break
            output += line
    finally:
        process.send_signal(signal.SIGINT)
        output += process.communicate(timeout=20)[0]
    assert output.index("Iniciando o painel") < output.index("PRONTO")
    assert f"http://127.0.0.1:{port}/panel/" in output
    assert "admin.demo, ana.demo, bia.demo" in output and "credenciais-demo.txt" in output
    password = (tmp_path / "demo" / "credenciais-demo.txt").read_text().splitlines()[1].split("\t")[2]
    assert password not in output  # senhas nunca no terminal
    assert "Painel encerrado." in output and process.returncode == 0
