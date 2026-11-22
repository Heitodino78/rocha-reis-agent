"""Empacotamento, instalação, atualização e reversão (Fase 17) — para um servidor Linux FUTURO.

Nada aqui acessa a rede, contrata serviço ou toca no sistema sem pedido explícito. Os testes e o ensaio
local usam uma RAIZ descartável (`--raiz DIR`) que reproduz a árvore do servidor:

    opt/rocha-reis-agent/releases/<versão>-<impressão>/   código de cada versão (somente leitura)
    opt/rocha-reis-agent/current -> releases/<id>          versão ativa (troca atômica do link)
    opt/rocha-reis-agent/pacotes/                          pacotes recebidos (verificados antes do uso)
    etc/rocha-reis-agent/                                  perfil da instância: perfil.json (0600) — diretório 0700
    etc/rocha-reis-agent/chaves/                           chaves de backup e do 2º fator (0700), fora dos dados
    var/lib/rocha-reis-agent/dados/                        banco SQLite + WAL (0700)
    var/lib/rocha-reis-agent/backups/                      backups cifrados locais (0700)
    var/lib/rocha-reis-agent/estado/                       batimento do worker, histórico de implantações (0700)
    var/log/rocha-reis-agent/                              log técnico privado opcional (0700)

Regras: pacote só é usado depois de conferido arquivo a arquivo; versão de banco nunca regride; antes de
atualizar há backup VERIFICADO e a migração é ensaiada numa CÓPIA; falha na prontidão volta a versão
anterior do código — mas, se o banco já foi migrado, a versão anterior não o abre e a recuperação exige
restaurar o backup com confirmação explícita (nunca automática).
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Callable
import socket
from urllib.parse import urlsplit

from rocha_reis_agent.ops.backup import BackupError, KeyMaterial, create_backup, restore_backup, verify_backup

APP = "rocha-reis-agent"
MANIFEST_NAME = "RELEASE.json"
# Só o necessário para EXECUTAR (sem testes nem documentação de desenvolvimento).
RUNTIME_PREFIXES = ("rocha_reis_agent/", "prompts/", "fixtures/", "knowledge/", "evals/", "deploy/")
RUNTIME_FILES = ("requirements.lock", "requirements.in", "pyproject.toml", "README.md", ".env.example",
                 "iniciar.sh", "AGENTS.md")
EXECUTABLE = ("iniciar.sh",)
MIN_PYTHON = (3, 13)
KEEP_RELEASES = 3


class DeployError(Exception):
    """Falha de implantação com mensagem segura (sem segredos nem conteúdo de banco)."""


# ------------------------------------------------------------------ layout
def layout(root: Path) -> dict[str, Path]:
    root = Path(root).absolute()
    return {
        "app": root / "opt" / APP, "releases": root / "opt" / APP / "releases", "current": root / "opt" / APP / "current",
        "pacotes": root / "opt" / APP / "pacotes", "config": root / "etc" / APP, "chaves": root / "etc" / APP / "chaves",
        "dados": root / "var" / "lib" / APP / "dados", "backups": root / "var" / "lib" / APP / "backups",
        "estado": root / "var" / "lib" / APP / "estado", "logs": root / "var" / "log" / APP,
        # Cópia secundária SIMULADA (mesmo disco): não é backup externo (ver ops/offsite.py).
        "secundario": root / "var" / "lib" / APP / "backups-secundario",
    }


PRIVATE = ("config", "chaves", "dados", "backups", "secundario", "estado", "logs")


def prepare_layout(root: Path, *, include_data: bool = True) -> dict[str, Path]:
    """Cria a árvore com permissões restritas (idempotente; nunca afrouxa permissões existentes).

    Fase 18B-0: `include_data=False` (instalação do CÓDIGO) não cria as pastas do volume de dados — com o
    volume ainda bloqueado elas nasceriam no disco de sistema, debaixo do ponto de montagem."""
    paths = layout(root)
    for name, path in paths.items():
        if name == "current" or (not include_data and name in VOLUME_DIRS):
            continue
        if path.is_symlink():
            raise DeployError(f"Diretório '{name}' não pode ser link simbólico.")
        path.mkdir(parents=True, exist_ok=True)
        mode = 0o700 if name in PRIVATE else 0o755
        os.chmod(path, mode)
    return paths


def check_layout(root: Path) -> list[str]:
    """Problemas de permissão/tipo na árvore instalada (vazio = ok)."""
    problems = []
    for name, path in layout(root).items():
        if name == "current":
            if path.exists() and not path.is_symlink():
                problems.append("current deve ser link simbólico para uma versão")
            continue
        if not path.exists():
            problems.append(f"{name}: ausente")
            continue
        if path.is_symlink():
            problems.append(f"{name}: link simbólico")
            continue
        mode = path.stat().st_mode & 0o777
        if name in PRIVATE and mode & 0o077:
            problems.append(f"{name}: permissão {oct(mode)} (esperado 0700)")
    config = layout(root)["config"]
    for item in (config / "perfil.json", *sorted(layout(root)["chaves"].glob("*.key"))):
        if item.is_symlink():
            problems.append(f"{item.name}: link simbólico")
        elif item.exists() and item.stat().st_mode & 0o077:
            problems.append(f"{item.name}: permissão {oct(item.stat().st_mode & 0o777)} (esperado 0600)")
    if os.geteuid() == 0 and os.environ.get("ROCHA_REIS_ALLOW_ROOT") != "1":
        problems.append("serviço rodando como root (use o usuário dedicado rocha-reis)")
    return problems


# ------------------------------------------------------------------ instância (perfil do servidor)
DATABASE = "rocha-reis.preprod.synthetic.sqlite3"


SERVER_VOLUME = Path("/var/lib") / APP          # ponto de montagem do volume LUKS dedicado no servidor
VOLUME_DIRS = ("dados", "backups", "secundario", "estado")


def is_server_root(root: Path) -> bool:
    return Path(root).resolve() == Path("/")


def volume_mount_for(root: Path, mount_point: Path | None) -> Path:
    """Ponto de montagem do volume: no servidor é SEMPRE /var/lib/rocha-reis-agent; num laboratório pode ser
    um ancestral (ex.: disco de teste montado pelo udisks, com a raiz de teste dentro dele)."""
    data_root = layout(root)["dados"].parent
    mount = Path(mount_point).absolute() if mount_point else data_root
    if is_server_root(root) and mount != SERVER_VOLUME:
        raise DeployError(f"No servidor o volume de dados é montado em {SERVER_VOLUME}.")
    if mount != data_root and mount not in data_root.parents:
        raise DeployError("O ponto de montagem precisa conter var/lib/rocha-reis-agent.")
    return mount


def volume_status(root: Path, spec: dict[str, object] | None = None, *, require_writable: bool = False):  # type: ignore[no-untyped-def]
    """Estado do volume dedicado da instância (spec = seção "volume" do perfil)."""
    from rocha_reis_agent.persistence.storage_security import check_data_volume
    if spec is None:
        spec = json.loads((layout(root)["config"] / "perfil.json").read_text(encoding="utf-8")).get("volume") or {}
    if not spec.get("id"):
        raise DeployError("Perfil sem volume dedicado identificado (instância de laboratório).")
    paths = layout(root)
    return check_data_volume(Path(str(spec["ponto_montagem"])), spec, paths=tuple(paths[n] for n in VOLUME_DIRS),
                             require_writable=require_writable)


def prepare_instance(root: Path, *, panel_origin: str = "https://painel.interno", volume_mount: Path | None = None,
                     laboratorio: bool = False) -> dict[str, object]:
    """Cria a instância de PRÉ-PRODUÇÃO SINTÉTICA no layout do servidor (nunca recria nem apaga).

    O perfil (etc/rocha-reis-agent/perfil.json, 0600) aponta para caminhos ABSOLUTOS: banco e backups em
    var/lib, chaves em etc/.../chaves (fora dos dados e dos backups). Exige volume com cifragem VERIFICADA.
    Segundo fator obrigatório, cookie Secure, origem HTTPS, transporte e IA simulados, só dados sintéticos.

    Fase 18B-0: por padrão exige o volume LUKS DEDICADO já montado e marcado (`ops marcar-volume`); a
    identidade (UUID LUKS + id da marca) vai para o perfil, FORA do volume. `laboratorio=True` (só fora da
    raiz "/") dispensa o volume dedicado para ensaios locais — e fica registrado no perfil.
    """
    import secrets
    from rocha_reis_agent.ops.backup import generate_key
    from rocha_reis_agent.ops.demo import _write_private, profile_settings
    from rocha_reis_agent.persistence.storage_security import check_data_volume, volume_protection
    if not panel_origin.startswith("https://"):
        raise DeployError("A origem do painel no servidor precisa ser HTTPS (endereço da VPN).")
    if laboratorio and is_server_root(root):
        raise DeployError("Modo laboratório não é permitido na raiz do servidor.")
    if (layout(root)["config"] / "perfil.json").exists():
        raise DeployError("A instância já existe; nunca é recriada ou apagada automaticamente.")
    volume_spec: dict[str, object] = {"modo": "laboratorio"}
    if not laboratorio:
        # Conferido ANTES de criar as pastas: com o volume bloqueado elas iriam parar no disco de sistema.
        mount = volume_mount_for(root, volume_mount)
        status = check_data_volume(mount, None, require_writable=True)
        if not status.ready:
            raise DeployError(f"Volume de dados dedicado não verificado ({status.state}): {status.summary()}.")
        volume_spec = {"ponto_montagem": str(mount), "id": status.volume_id, "luks_uuid": status.luks_uuid}
    paths = prepare_layout(root)
    profile = paths["config"] / "perfil.json"
    protection = volume_protection(paths["dados"])
    if not (protection.encrypted and protection.persistent):
        raise DeployError(f"Volume dos dados sem cifragem verificada: {protection.describe()}.")
    if not laboratorio:
        status = volume_status(root, volume_spec, require_writable=True)
        if not status.ready:
            raise DeployError(f"Pastas de dados fora do volume dedicado: {status.summary()}.")
    config = {"version": 1, "kind": "preproduction", "profile": "preproduction", "volume": volume_spec,
              "database": str(paths["dados"] / DATABASE), "panel_origin": panel_origin,
              "mfa_key": str(paths["chaves"] / "mfa.key"),
              "backup": {"primario": str(paths["backups"]), "secundario": str(paths["secundario"]),
                         "chave": str(paths["chaves"] / "backup.key"),
                         # Estado fora de /etc (somente leitura para os serviços no servidor).
                         "estado": str(paths["estado"] / "estado-backup.json")},
              "settings": {
                  "app_env": "local", "storage_backend": "sqlite", "whatsapp_enabled": True,
                  "whatsapp_transport": "fake", "whatsapp_verify_token": secrets.token_urlsafe(32),
                  "whatsapp_app_secret": secrets.token_urlsafe(32), "whatsapp_conversation_key": secrets.token_urlsafe(32),
                  "whatsapp_phone_number_id": "100000000000009", "whatsapp_business_account_id": "200000000000009",
                  "panel_enabled": True, "panel_cookie_secure": True, "panel_mfa_required": True,
                  "panel_embedded_worker": False, "knowledge_source": "database", "ai_provider": "fake",
                  "panel_environment_label": "PRÉ-PRODUÇÃO SINTÉTICA", "data_classification": "synthetic",
                  "channel_log_path": str(paths["logs"] / "canal.log"),
                  # Capacidade (Fase 17): o padrão de 100 conversas ATIVAS (arquivamento só após 24 h paradas)
                  # esgotaria num dia movimentado; 1000 é o teto aceito. Alerta "capacidade_esgotada" vigia.
                  "sim_max_conversations": 1000, "storage_max_bytes": 512 * 1024 * 1024,
                  # Três processos gravam no mesmo arquivo: 1 s de espera recusava webhooks em rajadas (medido).
                  "storage_busy_timeout_ms": 5000,
              }}
    generate_key(paths["chaves"] / "mfa.key")
    generate_key(paths["chaves"] / "backup.key")
    _write_private(profile, json.dumps(config, indent=2, ensure_ascii=False) + "\n")
    settings = profile_settings(paths["config"])
    from rocha_reis_agent.bootstrap import configured_knowledge, configured_repository
    from rocha_reis_agent.knowledge_catalog import KnowledgeScope
    from rocha_reis_agent.panel.security import hash_password
    from rocha_reis_agent.panel.store import SQLitePanelStore
    repository = configured_repository(settings)
    knowledge = configured_knowledge(settings, repository)
    assert knowledge is not None
    knowledge.initialize(KnowledgeScope.SYNTHETIC)
    password = f"Srv-{secrets.token_urlsafe(14)}-7!"
    SQLitePanelStore(repository).create_user(login="admin.preprod", display_name="Administração (pré-produção)",
                                             password_hash=hash_password(password), role="ADMIN",
                                             must_change_password=True, actor_id=None)
    _write_private(paths["config"] / "credenciais-iniciais.txt",
                   "# Acesso inicial da PRÉ-PRODUÇÃO SINTÉTICA (troca obrigatória e 2º fator no 1º acesso). "
                   "Entregue pessoalmente e apague este arquivo.\n"
                   f"admin.preprod\tADMIN\t{password}\n")
    return {"perfil": str(paths["config"]), "banco": DATABASE, "volume": protection.describe(),
            "origem_painel": panel_origin, "credenciais": "etc/rocha-reis-agent/credenciais-iniciais.txt (0600)"}


def validate_installation(root: Path) -> dict[str, object]:
    """Checagem ANTES de iniciar cada serviço (ExecStartPre): recusa configuração insegura sem exceções."""
    from rocha_reis_agent import config as c
    from rocha_reis_agent.config import ConfigurationError
    problems = check_layout(root)
    paths = layout(root)
    locks = {"meta_graph": c.GRAPH_TRANSPORT_ACTIVATION_AUTHORIZED,
             "meta_homologacao": c.HOMOLOGATION_TRANSPORT_ACTIVATION_AUTHORIZED,
             "gemini_real": c.GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED, "dados_reais": c.REAL_DATA_ACTIVATION_AUTHORIZED}
    settings = None
    volume_report: dict[str, object] = {}
    if not (paths["config"] / "perfil.json").is_file():
        problems.append("perfil.json ausente (crie a instância com ops preparar-servidor)")
    else:
        from rocha_reis_agent.ops.demo import profile_settings
        try:
            settings = profile_settings(paths["config"])
        except (ConfigurationError, ValueError, OSError, KeyError, TypeError):
            problems.append("perfil.json inválido (configuração recusada pelas regras de ambiente)")
    if settings is not None:
        from rocha_reis_agent.persistence.storage_security import volume_protection
        database = Path(settings.storage_path)  # type: ignore[arg-type]
        if paths["dados"] not in database.parents:
            problems.append("banco fora de var/lib/rocha-reis-agent/dados")
        if settings.panel_mfa_key_file is None or paths["chaves"] not in Path(settings.panel_mfa_key_file).parents:
            problems.append("chave do 2º fator fora de etc/rocha-reis-agent/chaves")
        if not settings.panel_mfa_required or not settings.panel_cookie_secure \
                or not settings.panel_origin.startswith("https://"):
            problems.append("painel sem 2º fator obrigatório, cookie Secure ou origem HTTPS")
        if settings.panel_embedded_worker:
            problems.append("worker embutido ligado (no servidor o worker é um serviço próprio)")
        protection = volume_protection(paths["dados"])
        if not (protection.encrypted and protection.persistent):
            problems.append("volume dos dados sem cifragem verificada")
        # Fase 18B-0: identidade do volume dedicado (UUID LUKS + marca) e proibição do modo laboratório em "/".
        spec = json.loads((paths["config"] / "perfil.json").read_text(encoding="utf-8")).get("volume") or {}
        if not spec.get("id"):
            volume_report = {"modo": "laboratorio"}
            if is_server_root(root):
                problems.append("servidor sem volume de dados dedicado identificado (ops marcar-volume)")
        else:
            try:
                volume_mount_for(root, Path(str(spec["ponto_montagem"])))
                status = volume_status(root, spec)
                volume_report = status.as_dict()
                if not status.ready:
                    problems.append(f"volume de dados {status.state}: {status.summary()}")
            except DeployError as error:
                volume_report = {"estado": "RECUSADO"}
                problems.append(str(error))
        if settings.data_classification != "synthetic" and not c.REAL_DATA_ACTIVATION_AUTHORIZED:
            problems.append("dados não sintéticos com a trava de dados reais fechada")
    current = paths["current"]
    if not current.is_symlink() or not (current / MANIFEST_NAME).is_file():
        problems.append("nenhuma versão ativa (link current)")
    return {"ok": not problems, "problemas": problems, "travas": locks, "versao_ativa": current_release(root),
            "volume": volume_report if settings is not None else None}


# ------------------------------------------------------------------ pacote
def runtime_files(source_root: Path) -> list[str]:
    from rocha_reis_agent.ops.release import project_files
    return [name for name in project_files()
            if name.startswith(RUNTIME_PREFIXES) or name in RUNTIME_FILES]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_package(destination: Path, *, source_root: Path | None = None) -> Path:
    """Pacote REPRODUZÍVEL (mesmo conteúdo → mesmos bytes): ordem fixa, datas e donos zerados."""
    import re
    from rocha_reis_agent.ops.release import ROOT
    root = Path(source_root or ROOT)
    # Identificação lida da ÁRVORE empacotada (não do código importado por este processo).
    init = (root / "rocha_reis_agent" / "__init__.py").read_text(encoding="utf-8")
    storage = (root / "rocha_reis_agent" / "persistence" / "sqlite.py").read_text(encoding="utf-8")
    version_match = re.search(r'^__version__ = "([^"]+)"', init, re.M)
    name_match = re.search(r'^RELEASE_NAME = "([^"]+)"', init, re.M)
    schema_match = re.search(r"^SCHEMA_VERSION = (\d+)", storage, re.M)
    if not (version_match and name_match and schema_match):
        raise DeployError("Árvore sem versão, nome ou versão de esquema identificáveis.")
    __version__, RELEASE_NAME, SCHEMA_VERSION = version_match[1], name_match[1], int(schema_match[1])
    names = runtime_files(root)
    hashes = {name: _sha((root / name).read_bytes()) for name in names}
    digest = hashlib.sha256("".join(f"{n}\0{h}\n" for n, h in sorted(hashes.items())).encode()).hexdigest()
    manifest = {"nome": RELEASE_NAME, "versao": __version__, "impressao": digest, "esquema_banco": SCHEMA_VERSION,
                "python_minimo": ".".join(map(str, MIN_PYTHON)), "arquivos": hashes}
    destination = Path(destination).absolute()
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / f"{APP}-{__version__}-{digest[:12]}.tar.gz"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        def add(name: str, data: bytes) -> None:
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.uid, info.gid, info.uname, info.gname = len(data), 0, 0, 0, "", ""
            info.mode = 0o755 if name in EXECUTABLE else 0o644
            archive.addfile(info, io.BytesIO(data))
        add(MANIFEST_NAME, (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode())
        for name in sorted(names):
            add(name, (root / name).read_bytes())
    compressed = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=compressed, mtime=0) as gz:
        gz.write(buffer.getvalue())
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_bytes(compressed.getvalue())
    os.replace(temporary, target)
    return target


def _safe_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = []
    for member in archive.getmembers():
        path = PurePosixPath(member.name)
        if (not member.isfile() or path.is_absolute() or ".." in path.parts or not member.name
                or member.name.startswith("/") or "\\" in member.name):
            raise DeployError("Pacote recusado: entrada insegura (link, dispositivo, caminho absoluto ou '..').")
        members.append(member)
    return members


def verify_package(package: Path, into: Path) -> dict[str, object]:
    """Extrai SÓ arquivos regulares para `into` (vazio) e confere cada hash; recusa extras e faltas."""
    into = Path(into)
    if into.exists() and any(into.iterdir()):
        raise DeployError("Destino da extração precisa estar vazio.")
    into.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(package, mode="r:gz") as archive:
            members = _safe_members(archive)
            contents = {m.name: archive.extractfile(m).read() for m in members}  # type: ignore[union-attr]
    except (tarfile.TarError, OSError, EOFError) as error:
        raise DeployError("Pacote ilegível ou corrompido.") from error
    try:
        manifest = json.loads(contents.pop(MANIFEST_NAME))
        expected: dict[str, str] = manifest["arquivos"]
    except (KeyError, ValueError, TypeError):
        raise DeployError("Pacote sem manifesto válido.") from None
    if set(contents) != set(expected):
        raise DeployError("Pacote com arquivos a mais ou a menos que o manifesto.")
    for name, data in contents.items():
        if _sha(data) != expected[name]:
            raise DeployError(f"Pacote corrompido: hash diferente em {name}.")
    digest = hashlib.sha256("".join(f"{n}\0{h}\n" for n, h in sorted(expected.items())).encode()).hexdigest()
    if digest != manifest.get("impressao"):
        raise DeployError("Impressão digital do pacote não confere.")
    for name, data in contents.items():
        target = into / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        os.chmod(target, 0o755 if name in EXECUTABLE else 0o644)
    (into / MANIFEST_NAME).write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def release_id(manifest: dict[str, object]) -> str:
    return f"{manifest['versao']}-{str(manifest['impressao'])[:12]}"


# ------------------------------------------------------------------ instalação
def install_release(package: Path, root: Path, *, python: str | None = None, create_venv: bool = True,
                    pip_args: tuple[str, ...] = ()) -> dict[str, object]:
    """Instala uma versão ao lado das outras (não ativa). Reutiliza se idêntica; recusa se divergente."""
    paths = prepare_layout(root, include_data=False)
    with tempfile.TemporaryDirectory(dir=paths["releases"], prefix=".novo-") as tmp:
        staging = Path(tmp) / "codigo"
        manifest = verify_package(package, staging)
        identifier = release_id(manifest)
        target = paths["releases"] / identifier
        if target.exists():
            existing = json.loads((target / MANIFEST_NAME).read_text(encoding="utf-8"))
            if existing.get("impressao") != manifest["impressao"]:
                raise DeployError("Já existe uma versão com esse nome e conteúdo diferente.")
            return {"versao": manifest["versao"], "id": identifier, "reutilizada": True, "manifesto": manifest}
        interpreter = python or sys.executable
        if create_venv:
            venv = staging / ".venv"
            subprocess.run([interpreter, "-m", "venv", str(venv)], check=True, capture_output=True)
            subprocess.run([str(venv / "bin" / "python"), "-m", "pip", "install", "--require-hashes", "--no-deps",
                            "-r", str(staging / "requirements.lock"), *pip_args], check=True, capture_output=True)
            # Caminho FINAL (a pasta de preparo é renomeada logo abaixo). Fase 18: gravar o caminho de preparo
            # fazia o ensaio de migração de `ops atualizar` falhar com FileNotFoundError em toda atualização real.
            interpreter = str(target / ".venv" / "bin" / "python")
        (staging / "RUNTIME.json").write_text(json.dumps({"python": interpreter}) + "\n", encoding="utf-8")
        for directory, _, files in os.walk(staging):  # código somente leitura para o serviço
            for name in files:
                path = Path(directory) / name
                if path.is_symlink():
                    # Fase 18: .venv/bin/python é link para o interpretador BASE; chmod seguiria o link e tiraria
                    # a escrita do Python do sistema (/usr/bin/python3.13 no servidor) — fora da árvore da versão.
                    continue
                os.chmod(path, path.stat().st_mode & ~0o222)
        os.replace(staging, target)
    return {"versao": manifest["versao"], "id": identifier, "reutilizada": False, "manifesto": manifest}


def current_release(root: Path) -> str | None:
    link = layout(root)["current"]
    return os.readlink(link).split("/")[-1] if link.is_symlink() else None


def activate(root: Path, identifier: str) -> str | None:
    """Troca ATÔMICA do link `current`; devolve a versão anterior."""
    paths = layout(root)
    if not (paths["releases"] / identifier / MANIFEST_NAME).is_file():
        raise DeployError("Versão não instalada.")
    previous = current_release(root)
    temporary = paths["app"] / f".current-{os.getpid()}"
    if temporary.is_symlink() or temporary.exists():
        temporary.unlink()
    os.symlink(f"releases/{identifier}", temporary)
    os.replace(temporary, paths["current"])
    if previous and previous != identifier and paths["estado"].is_dir():
        state = paths["estado"] / "versoes.json"
        partial = state.with_name(state.name + ".tmp")
        descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"atual": identifier, "anterior": previous, "em": _now()}, handle)
        os.replace(partial, state)
    return previous


def _restore_versions(state: Path, content: bytes | None) -> None:
    if content is None:
        state.unlink(missing_ok=True)
        return
    partial = state.with_name(state.name + ".tmp")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)
    os.replace(partial, state)


def rollback_code(root: Path) -> dict[str, object]:
    """Volta o link `current` para a versão anterior registrada. Recusa se o banco em uso já tem esquema
    mais novo que essa versão (ela não abriria o banco): restaure o banco antes (`reverter --restaurar-banco`).
    Não para nem inicia serviços: faça `systemctl restart` depois e confira /ready."""
    from rocha_reis_agent.ops.demo import profile_settings
    paths = layout(root)
    try:
        state = json.loads((paths["estado"] / "versoes.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise DeployError("Não há versão anterior registrada.") from None
    previous = str(state.get("anterior") or "")
    if not (paths["releases"] / previous / MANIFEST_NAME).is_file():
        raise DeployError("A versão anterior registrada não está mais instalada.")
    if previous == current_release(root):
        raise DeployError("A versão ativa já é a anterior registrada.")
    database = Path(profile_settings(paths["config"]).storage_path)  # type: ignore[arg-type]
    schema = database_schema(database)
    if schema is not None and schema > release_schema(root, previous):
        raise DeployError("O banco em uso tem esquema mais novo que a versão anterior: restaure antes o backup "
                          "feito na atualização (ops reverter --restaurar-banco <backup> --confirmacao RESTAURAR:<backup>).")
    before = activate(root, previous)
    result = {"resultado": "CODIGO_REVERTIDO", "ativa": previous, "desativada": before, "em": _now(),
              "proximo_passo": "reiniciar os serviços e conferir /ready"}
    _history(root, result)
    return result


def release_schema(root: Path, identifier: str) -> int:
    manifest = json.loads((layout(root)["releases"] / identifier / MANIFEST_NAME).read_text(encoding="utf-8"))
    return int(manifest["esquema_banco"])


def database_schema(database: Path) -> int | None:
    if not Path(database).is_file():
        return None
    with closing(sqlite3.connect(Path(database).as_uri() + "?mode=ro", uri=True)) as connection:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])


# ------------------------------------------------------------------ atualização
def _history(root: Path, entry: dict[str, object]) -> None:
    path = layout(root)["estado"] / "implantacoes.jsonl"
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        os.write(descriptor, (json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rehearse_migration(root: Path, identifier: str, database_copy: Path) -> dict[str, object]:
    """Abre a CÓPIA do banco com o código NOVO (aplica migrações) e confere a integridade."""
    release = layout(root)["releases"] / identifier
    runtime = json.loads((release / "RUNTIME.json").read_text(encoding="utf-8"))
    environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(release),
                   "PYTHONDONTWRITEBYTECODE": "1", "HOME": str(database_copy.parent)}
    completed = subprocess.run([runtime["python"], "-B", "-m", "rocha_reis_agent.ops",
                                "verificar-migracao", "--banco", str(database_copy)],
                               cwd=database_copy.parent, env=environment, capture_output=True, text=True, timeout=300)
    if completed.returncode != 0:
        raise DeployError("Ensaio de migração falhou na cópia do banco; nada foi alterado no banco em uso."
                          + (f" ({completed.stderr.strip().splitlines()[-1][:200]})" if completed.stderr.strip() else ""))
    return json.loads(completed.stdout)


def http_status(url: str, *, host: str | None = None, timeout: float = 2.0) -> int | None:
    """GET mínimo SÓ para 127.0.0.1 (verificação local de prontidão). O produto não usa cliente HTTP geral
    (invariante de segurança dos testes); qualquer outro destino é recusado."""
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.hostname != "127.0.0.1" or not parts.port:
        raise DeployError("Verificação de prontidão só em http://127.0.0.1:<porta>.")
    request = (f"GET {parts.path or '/'} HTTP/1.1\r\nHost: {host or f'127.0.0.1:{parts.port}'}\r\n"
               "Connection: close\r\n\r\n").encode("ascii")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
            connection.settimeout(timeout)
            connection.connect(("127.0.0.1", parts.port))
            connection.sendall(request)
            status_line = connection.recv(64).split(b"\r\n", 1)[0].split()
        return int(status_line[1]) if len(status_line) >= 2 and status_line[1].isdigit() else None
    except OSError:
        return None


def probe_http(urls: list[str], *, timeout: float = 30.0, interval: float = 0.5,
               hosts: dict[str, str] | None = None) -> bool:
    """Prontidão: todas as URLs locais respondem 200 dentro do prazo. `hosts` dá o cabeçalho Host por URL
    (o painel só aceita o host da sua origem — proteção contra DNS rebinding)."""
    for url in urls:
        parts = urlsplit(url)
        if parts.scheme != "http" or parts.hostname != "127.0.0.1" or not parts.port:
            raise DeployError("Verificação de prontidão só em http://127.0.0.1:<porta>.")
    deadline = time.monotonic() + timeout
    pending = list(urls)
    while pending and time.monotonic() < deadline:
        if http_status(pending[0], host=(hosts or {}).get(pending[0])) == 200:
            pending.pop(0)
            continue
        time.sleep(interval)
    return not pending


def run_commands(commands: list[list[str]]) -> None:
    for command in commands:
        subprocess.run(command, check=True, capture_output=True, timeout=120)


def update(package: Path, root: Path, *, database: Path, key_file: Path, confirmation: str,
           stop: Callable[[], None], start: Callable[[], None], ready: Callable[[], bool],
           python: str | None = None, create_venv: bool = True, pip_args: tuple[str, ...] = ()) -> dict[str, object]:
    """Fluxo completo. Retorna o relatório; nunca restaura o banco sozinho."""
    _require_volume_if_dedicated(root)  # Fase 18B-0: antes de criar qualquer pasta (volume bloqueado)
    paths = prepare_layout(root)
    report: dict[str, object] = {"iniciado_em": _now(), "etapas": []}
    steps: list[str] = report["etapas"]  # type: ignore[assignment]

    def step(name: str) -> None:
        steps.append(name)

    with tempfile.TemporaryDirectory(prefix="rr-verifica-") as tmp:
        manifest = verify_package(package, Path(tmp) / "x")
    step("pacote_verificado")
    if confirmation != f"ATUALIZAR:{manifest['versao']}":
        raise DeployError(f"Confirmação incorreta. Digite exatamente: ATUALIZAR:{manifest['versao']}")
    minimum = tuple(int(x) for x in str(manifest["python_minimo"]).split("."))
    if sys.version_info[:2] < minimum:
        raise DeployError("Python do servidor abaixo do mínimo da versão.")
    live_schema = database_schema(database)
    if live_schema is not None and int(manifest["esquema_banco"]) < live_schema:  # type: ignore[arg-type]
        raise DeployError("Versão nova tem esquema de banco MAIS ANTIGO que o banco em uso; atualização recusada.")
    step("compatibilidade_conferida")
    previous = current_release(root)
    versions_file = paths["estado"] / "versoes.json"
    versions_before = versions_file.read_bytes() if versions_file.is_file() else None
    report.update(versao_nova=manifest["versao"], anterior=previous, esquema_antes=live_schema)
    material = KeyMaterial("keyfile", key_file=key_file)
    installed = install_release(package, root, python=python, create_venv=create_venv, pip_args=pip_args)
    identifier = str(installed["id"])
    report["id"] = identifier
    step("versao_preparada")
    # Daqui em diante os serviços ficam parados: o backup é o ponto EXATO de restauração (nenhuma mensagem
    # entra entre o backup e a troca; a Meta reenvia webhooks não confirmados).
    backup = None
    try:
        stop()
        step("servicos_parados")
        if live_schema is not None:
            backup = create_backup(database, paths["backups"], material)
            verify_backup(backup, material)
            report["backup"] = backup.name
            step("backup_verificado")
            # Cópia decifrada fica no volume CIFRADO dos dados (estado/), nunca em /tmp, e é apagada no fim.
            with tempfile.TemporaryDirectory(prefix=".ensaio-migracao-", dir=paths["estado"]) as tmp:
                os.chmod(tmp, 0o700)
                copy = Path(tmp) / "ensaio.synthetic.sqlite3"
                restore_backup(backup, copy, material)
                report["ensaio_migracao"] = rehearse_migration(root, identifier, copy)
            step("migracao_ensaiada_em_copia")
    except (BackupError, DeployError, subprocess.SubprocessError, OSError) as error:
        # Nada foi trocado: religa a versão em uso.
        report.update(resultado="CANCELADA_ANTES_DA_TROCA", erro=str(error) if isinstance(error, DeployError) else
                      type(error).__name__)
        try:
            start()
            report["servico_anterior_pronto"] = ready()
        except (subprocess.SubprocessError, OSError):
            report["servico_anterior_pronto"] = False
        report["concluido_em"] = _now()
        _history(root, report)
        return report
    try:
        activate(root, identifier)
        step("versao_ativada")
        start()
        step("servicos_iniciados")
        healthy = ready()
    except (subprocess.SubprocessError, OSError) as error:
        healthy = False
        report["erro"] = type(error).__name__
    if healthy:
        step("prontidao_confirmada")
        report.update(resultado="ATUALIZADA", esquema_depois=database_schema(database), concluido_em=_now())
        _prune_releases(root, keep={identifier, previous} if previous else {identifier})
        _history(root, report)
        return report
    # ---- falha: volta o código; banco só com procedimento explícito
    step("prontidao_falhou")
    after = database_schema(database)
    report["esquema_depois"] = after
    if previous is not None:
        try:
            stop()
        except (subprocess.SubprocessError, OSError):
            pass
        activate(root, previous)
        # Fase 18 (achado no systemd real): activate() registraria a versão QUE FALHOU como "anterior", e um
        # `ops reverter` manual depois disso a reativaria. O registro volta a ser o de antes da tentativa.
        _restore_versions(versions_file, versions_before)
        step("versao_anterior_reativada")
        if after is not None and live_schema is not None and after > live_schema:
            # O código anterior recusa esquema mais novo: não adianta religar; exige restauração confirmada.
            report.update(resultado="REVERTIDA_AGUARDANDO_RESTAURACAO_DO_BANCO",
                          acao=f"ops reverter --raiz ... --restaurar-banco {backup.name if backup else '?'} "
                               f"--confirmacao RESTAURAR:{backup.name if backup else '?'}")
        else:
            try:
                start()
                report["resultado"] = "REVERTIDA" if ready() else "REVERTIDA_SEM_PRONTIDAO"
            except (subprocess.SubprocessError, OSError):
                report["resultado"] = "REVERTIDA_SEM_PRONTIDAO"
    else:
        report["resultado"] = "FALHOU_SEM_VERSAO_ANTERIOR"
    report["concluido_em"] = _now()
    _history(root, report)
    return report


def restore_database(root: Path, *, database: Path, key_file: Path, backup_name: str, confirmation: str) -> dict[str, object]:
    """Reversão do BANCO (serviços parados): restaura o backup pré-atualização e substitui o banco em uso,
    guardando cópia do banco atual. Exige `RESTAURAR:<nome do backup>`."""
    from rocha_reis_agent.ops.backup import promote
    if confirmation != f"RESTAURAR:{backup_name}":
        raise DeployError(f"Confirmação incorreta. Digite exatamente: RESTAURAR:{backup_name}")
    paths = layout(root)
    backup = paths["backups"] / backup_name
    if backup.parent != paths["backups"] or not backup.is_file():
        raise DeployError("Backup não encontrado no diretório de backups da instalação.")
    _require_volume_if_dedicated(root)
    material = KeyMaterial("keyfile", key_file=key_file)
    with tempfile.TemporaryDirectory(dir=paths["dados"], prefix=".restaurando-") as tmp:
        restored = Path(tmp) / "restaurado.synthetic.sqlite3"
        restore_backup(backup, restored, material)
        safety = promote(restored, database, confirmation=f"SUBSTITUIR:{Path(database).name}")
    result = {"restaurado_de": backup_name, "copia_do_banco_anterior": safety.name, "esquema": database_schema(database),
              "em": _now()}
    _history(root, {"resultado": "BANCO_RESTAURADO", **result})
    return result


def _require_volume_if_dedicated(root: Path) -> None:
    """Fase 18B-0: nada substitui ou cria banco se o perfil declara volume dedicado e ele não está PRONTO."""
    profile = layout(root)["config"] / "perfil.json"
    try:
        spec = json.loads(profile.read_text(encoding="utf-8")).get("volume") or {} if profile.exists() else {}
    except (OSError, ValueError):
        raise DeployError("Perfil da instância ilegível.") from None
    if spec.get("id"):
        status = volume_status(root, spec, require_writable=True)
        if not status.ready:
            raise DeployError(f"Volume de dados {status.state}: {status.summary()}. Nada foi alterado.")
    elif is_server_root(root):
        raise DeployError("Servidor sem volume de dados dedicado identificado. Nada foi alterado.")


def _prune_releases(root: Path, *, keep: set[str]) -> None:
    """Mantém as KEEP_RELEASES versões mais recentes (e sempre a ativa e a anterior)."""
    releases = layout(root)["releases"]
    installed = sorted((p for p in releases.iterdir() if p.is_dir() and not p.name.startswith(".")),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    for path in installed[KEEP_RELEASES:]:
        if path.name in keep:
            continue
        for directory, _, files in os.walk(path):
            os.chmod(directory, 0o755)
            for name in files:
                os.chmod(Path(directory) / name, 0o644)
        shutil.rmtree(path)


def split_command(value: str) -> list[str]:
    return shlex.split(value)
