"""Identificação da versão candidata (Fase 14). Somente leitura; sem rede."""

from __future__ import annotations

from hashlib import sha256
from importlib import metadata
from pathlib import Path
import platform
import subprocess

ROOT = Path(__file__).resolve().parents[2]
# O documento da RC registra a impressão digital; incluí-lo faria o valor mudar ao ser anotado.
SELF_REFERENCING = {"docs/RELEASE_CANDIDATE_RC1.md", "docs/homologacao/RC1_MANIFESTO.json",
                    "docs/RELATORIO_PRONTIDAO_RC1.md"}
MANIFEST = ROOT / "docs" / "homologacao" / "RC1_MANIFESTO.json"
PACKAGES = ("fastapi", "uvicorn", "pydantic", "pydantic-settings", "argon2-cffi", "cryptography", "google-genai",
            "httpx2")


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip()


def project_files() -> list[str]:
    """Arquivos do projeto (versionados + novos não ignorados) — o que uma entrega conteria."""
    listed = _git("ls-files", "-co", "--exclude-standard")
    return sorted(line for line in (listed or "").splitlines()
                  if line and (ROOT / line).is_file() and line not in SELF_REFERENCING)


def release_info() -> dict[str, object]:
    from rocha_reis_agent import RELEASE_NAME, __version__
    from rocha_reis_agent import config as c
    from rocha_reis_agent.persistence.sqlite import MIGRATIONS, SCHEMA_VERSION
    files = project_files()
    digest = sha256()
    for name in files:  # impressão digital do CONTEÚDO (identifica uma árvore não versionada)
        digest.update(name.encode() + b"\0" + sha256((ROOT / name).read_bytes()).digest())
    status = _git("status", "--porcelain")
    return {
        "nome": RELEASE_NAME, "versao": __version__,
        "git": {"branch": _git("branch", "--show-current"), "commit_base": _git("rev-parse", "--short", "HEAD"),
                "alteracoes_nao_commitadas": len(status.splitlines()) if status else 0,
                "versionada": status == ""},
        "impressao_digital_conteudo": digest.hexdigest()[:16], "arquivos": len(files),
        "python": platform.python_version(),
        "dependencias": {name: _version(name) for name in PACKAGES},
        "esquema_banco": SCHEMA_VERSION,
        "migracoes": {p.name: sha256(p.read_bytes()).hexdigest()[:12] for p in sorted(MIGRATIONS.glob("*.sql"))},
        "travas": {"meta_graph": c.GRAPH_TRANSPORT_ACTIVATION_AUTHORIZED,
                   "meta_homologacao": c.HOMOLOGATION_TRANSPORT_ACTIVATION_AUTHORIZED,
                   "gemini_real": c.GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED,
                   "dados_reais": c.REAL_DATA_ACTIVATION_AUTHORIZED},
    }


def _version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _hashes() -> dict[str, str]:
    return {name: sha256((ROOT / name).read_bytes()).hexdigest()[:16] for name in project_files()}


def freeze(path: Path = MANIFEST) -> dict[str, object]:
    """Fase 15: congela a candidata — hash de CADA arquivo do projeto, para a homologação usar sempre o
    mesmo código. Não é commit: só permite verificar depois se algo mudou e o quê."""
    import json
    from datetime import datetime, timezone
    info = release_info()
    manifest = {"nome": info["nome"], "versao": info["versao"], "commit_base": info["git"]["commit_base"],
                "versionada_no_git": info["git"]["versionada"], "impressao_digital": info["impressao_digital_conteudo"],
                "congelado_em": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "esquema_banco": info["esquema_banco"], "travas": info["travas"], "arquivos": _hashes()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {k: v for k, v in manifest.items() if k != "arquivos"} | {"arquivos": len(manifest["arquivos"])}


def verify(path: Path = MANIFEST) -> dict[str, object]:
    import json
    if not path.is_file():
        return {"identica": False, "erro": "Manifesto da candidata não encontrado (rode ops congelar-rc)."}
    frozen = json.loads(path.read_text(encoding="utf-8"))
    current = _hashes()
    expected = frozen["arquivos"]
    changed = sorted(n for n in current.keys() & expected.keys() if current[n] != expected[n])
    added, removed = sorted(current.keys() - expected.keys()), sorted(expected.keys() - current.keys())
    from rocha_reis_agent import __version__
    return {"identica": not (changed or added or removed) and __version__ == frozen["versao"],
            "versao_congelada": frozen["versao"], "versao_atual": __version__,
            "impressao_congelada": frozen["impressao_digital"], "congelado_em": frozen["congelado_em"],
            "alterados": changed, "novos": added, "removidos": removed}
