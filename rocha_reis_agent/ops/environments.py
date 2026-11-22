"""Ambientes locais (Fase 12): perfil de PRÉ-PRODUÇÃO sintético, diagnóstico e métricas.

Nada aqui libera dados reais, liga Meta/Gemini, abre portas ou contrata serviços. O perfil de
pré-produção usa somente dados sintéticos e exige volume com cifragem VERIFICADA.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import sqlite3

from rocha_reis_agent import config as config_module
from rocha_reis_agent.ops.backup import BackupError, generate_key, list_backups
from rocha_reis_agent.ops.demo import _private, _write_private, profile_settings
from rocha_reis_agent.persistence.storage_security import volume_protection

PREPROD_MARKER = ".rocha-reis-preprod"
PREPROD_DB = "rocha-reis.preprod.synthetic.sqlite3"


def default_preprod() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "rocha-reis-agent" / "preprod"


def build_preproduction(profile: Path) -> dict[str, object]:
    """Cria (nunca recria nem apaga) um perfil de pré-produção com dados SINTÉTICOS."""
    profile = Path(profile).absolute()
    if profile.exists():
        raise BackupError("O perfil já existe; a pré-produção nunca é recriada ou apagada automaticamente.")
    protection = volume_protection(profile.parent)
    if not (protection.encrypted and protection.persistent):
        raise BackupError(f"Volume sem cifragem verificada: {protection.describe()}. Use um volume LUKS/dm-crypt.")
    _private(profile)
    (profile / PREPROD_MARKER).write_text("pré-produção — somente dados sintéticos nesta fase\n", encoding="utf-8")
    for name in ("dados", "backups", "backups-secundario", "chaves"):
        _private(profile / name)
    # Fase 13: HTTPS local (certificado de desenvolvimento) e segundo fator OBRIGATÓRIO, sem exceção.
    from rocha_reis_agent.ops.tls import create_dev_certificate
    create_dev_certificate(profile / "tls")
    generate_key(profile / "chaves" / "mfa.key")
    config = {"version": 1, "kind": "preproduction", "profile": "preproduction", "database": PREPROD_DB,
              "tls": True, "mfa_key": "chaves/mfa.key", "settings": {
        "app_env": "local", "storage_backend": "sqlite", "whatsapp_enabled": True, "whatsapp_transport": "fake",
        "whatsapp_verify_token": secrets.token_urlsafe(32), "whatsapp_app_secret": secrets.token_urlsafe(32),
        "whatsapp_conversation_key": secrets.token_urlsafe(32), "whatsapp_phone_number_id": "100000000000009",
        "whatsapp_business_account_id": "200000000000009", "panel_enabled": True, "panel_cookie_secure": True,
        "panel_mfa_required": True,
        "panel_embedded_worker": False, "knowledge_source": "database", "ai_provider": "fake",
        "panel_environment_label": "PRÉ-PRODUÇÃO SINTÉTICA", "data_classification": "synthetic",
    }}
    _write_private(profile / "perfil.json", json.dumps(config, indent=2) + "\n")
    settings = profile_settings(profile)
    from rocha_reis_agent.bootstrap import configured_knowledge, configured_repository
    from rocha_reis_agent.knowledge_catalog import KnowledgeScope
    from rocha_reis_agent.panel.security import hash_password
    from rocha_reis_agent.panel.store import SQLitePanelStore
    repository = configured_repository(settings)
    knowledge = configured_knowledge(settings, repository)
    assert knowledge is not None
    knowledge.initialize(KnowledgeScope.SYNTHETIC)  # base vazia: nada oficial nesta fase
    password = f"Pre-{secrets.token_urlsafe(14)}-7!"
    SQLitePanelStore(repository).create_user(login="admin.preprod", display_name="Administração (pré-produção)",
                                             password_hash=hash_password(password), role="ADMIN",
                                             must_change_password=True, actor_id=None)
    _write_private(profile / "credenciais-preprod.txt",
                   "# Acesso inicial da PRÉ-PRODUÇÃO SINTÉTICA (troca obrigatória no 1º acesso)\n"
                   f"admin.preprod\tADMIN\t{password}\n")
    generate_key(profile / "chaves" / "backup.key")
    return {"perfil": str(profile), "banco": str(settings.storage_path), "volume": protection.describe(),
            "painel": f"{settings.panel_origin}/panel/ (certificado de desenvolvimento, autoassinado)",
            "credenciais": str(profile / "credenciais-preprod.txt"),
            "aviso": "Somente dados SINTÉTICOS. Dados reais continuam bloqueados (ops ambiente lista os requisitos)."}


def _mode(path: Path) -> str | None:
    return f"{path.stat().st_mode & 0o777:o}" if path.exists() else None


def real_data_requirements(settings, protection) -> list[dict[str, str]]:  # type: ignore[no-untyped-def]
    """Condições para dados pessoais reais. Itens 'manual' não são verificáveis por software."""
    def item(name: str, ok: bool | None) -> dict[str, str]:
        return {"requisito": name, "situacao": "ok" if ok else ("manual (não verificável)" if ok is None else "pendente")}
    return [
        item("Trava de código REAL_DATA_ACTIVATION_AUTHORIZED aberta após revisão", config_module.REAL_DATA_ACTIVATION_AUTHORIZED),
        item("Banco em volume com cifragem em repouso verificada", protection.encrypted and protection.persistent),
        item("Painel atrás de HTTPS com cookie Secure", settings.panel_cookie_secure and settings.panel_origin.startswith("https://")),
        item("Segundo fator obrigatório no painel (implementado na Fase 13)", settings.panel_mfa_required),
        item("Backup diário automático fora da máquina e restauração testada (local simulado: Fase 13)", None),
        item("Custódia da chave de backup definida (duas pessoas)", None),
        item("Base legal, aviso de privacidade e retenção aprovados pelo responsável pelos dados", None),
        item("Homologação Meta (6B) e Gemini (9C-B) concluídas", False),
        item("Validação do painel pela equipe do salão (11B)", None),
    ]


def diagnose(profile: Path) -> dict[str, object]:
    """Somente leitura: perfil, permissões, cifragem verificada, marcação do banco e travas."""
    profile = Path(profile).absolute()
    if not (profile / "perfil.json").is_file():
        raise BackupError(f"Perfil não encontrado em {profile}.")
    data = json.loads((profile / "perfil.json").read_text(encoding="utf-8"))
    settings = profile_settings(profile)
    database = settings.storage_path
    assert database is not None
    protection = volume_protection(database.parent)
    metadata: dict[str, str] = {}
    schema = None
    if database.exists():
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
            metadata = dict(connection.execute("SELECT key,value FROM metadata").fetchall())
            schema = connection.execute("PRAGMA user_version").fetchone()[0]
    return {
        "perfil": data.get("profile", "demo"), "diretorio": str(profile),
        "permissoes": {"perfil": _mode(profile), "perfil.json": _mode(profile / "perfil.json"),
                       "dados": _mode(database.parent), "banco": _mode(database)},
        "banco": {"arquivo": database.name, "existe": database.exists(), "esquema": schema,
                  "ambiente_gravado": metadata.get("environment_profile"),
                  "classificacao": metadata.get("data_classification")},
        "protecao_em_repouso": {"banco": protection.describe(),
                                "backups": volume_protection(profile / "backups").describe()},
        "integracoes": {"transporte": settings.whatsapp_transport, "ia": settings.ai_provider,
                        "travas": config_module.activation_locks()},
        "seguranca": _security(profile, data, settings),
        "dados_reais": {"situacao": "BLOQUEADOS", "requisitos": real_data_requirements(settings, protection)},
    }


def _security(profile: Path, data: dict, settings) -> dict[str, object]:  # type: ignore[no-untyped-def]
    """Fase 13: segundo fator, HTTPS local, chaves e backup automático (somente leitura)."""
    from rocha_reis_agent.ops.backup_job import backup_health
    key = profile / data["mfa_key"] if data.get("mfa_key") else None
    certificate = profile / "tls" / "dev.crt"
    days = None
    if certificate.is_file():
        from rocha_reis_agent.ops.tls import certificate_days_left
        days = certificate_days_left(certificate)
    return {
        "segundo_fator": {"obrigatorio": settings.panel_mfa_required, "chave_configurada": key is not None,
                          "chave_privada": _mode(key) == "600" if key and key.exists() else False},
        "https": {"origem": settings.panel_origin.split("://")[0], "cookie_secure": settings.panel_cookie_secure,
                  "certificado": "desenvolvimento (autoassinado)" if days is not None else None,
                  "dias_restantes": days},
        "proxies_confiaveis": settings.panel_trusted_proxies or "nenhum",
        "backup_automatico": backup_health(profile),
    }


def metrics(profile: Path, *, backups: Path | None = None, now: datetime | None = None) -> dict[str, object]:
    """Indicadores locais SEM dados pessoais (só contagens e tempos), lidos em modo somente leitura."""
    settings = profile_settings(Path(profile).absolute())
    database = settings.storage_path
    assert database is not None
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=1)).isoformat()
    since_z = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as c:
        def grouped(sql: str, *params: object) -> dict[str, int]:
            return {str(k): v for k, v in c.execute(sql, params)}
        latencies = sorted(
            (datetime.fromisoformat(done.replace("Z", "+00:00")) - datetime.fromisoformat(got.replace("Z", "+00:00")))
            .total_seconds() * 1000
            for got, done in c.execute("SELECT received_at, updated_at FROM channel_inbox WHERE status='DONE' "
                                       "AND kind='message' AND updated_at>=?", (since_z,)))
        result: dict[str, object] = {
            "gerado_em": now.isoformat(timespec="seconds"),
            "entrada": grouped("SELECT status,count(*) FROM channel_inbox GROUP BY status"),
            "entrada_desfechos_24h": grouped("SELECT outcome,count(*) FROM channel_inbox WHERE updated_at>=? "
                                             "GROUP BY outcome", since_z),
            "saida": grouped("SELECT status,count(*) FROM channel_outbox GROUP BY status"),
            "contadores_canal": grouped("SELECT name,value FROM channel_metrics"),
            "conversas": grouped("SELECT state,count(*) FROM conversations WHERE retired=0 GROUP BY state"),
            "aguardando_sem_responsavel": c.execute(
                "SELECT count(*) FROM conversations c LEFT JOIN panel_assignments a ON a.conversation_id=c.id "
                "WHERE c.state='WAITING_HUMAN' AND c.closed=0 AND a.user_id IS NULL").fetchone()[0],
            "tempo_processamento_ms_24h": {
                "amostras": len(latencies),
                "mediana": round(latencies[len(latencies) // 2], 1) if latencies else None,
                "p95": round(latencies[max(0, int(len(latencies) * 0.95) - 1)], 1) if latencies else None},
            "falhas_login_24h": c.execute("SELECT count(*) FROM panel_login_attempts WHERE attempted_at>=?",
                                          (since,)).fetchone()[0],
            "falhas_armazenamento_24h": c.execute("SELECT count(*) FROM audit WHERE operation='failure_rollback' "
                                                  "AND recorded_at>=?", (since,)).fetchone()[0],
            "envios_interrompidos": c.execute("SELECT count(*) FROM channel_control").fetchone()[0] > 0,
        }
    if backups is not None:
        valid = [b for b in list_backups(backups) if b["ok"]]
        latest = max((str(b["created_at"]) for b in valid), default=None)
        result["backup"] = {"validos": len(valid), "mais_recente": latest,
                            "idade_horas": round((now - datetime.strptime(latest, "%Y-%m-%dT%H:%M:%SZ").replace(
                                tzinfo=timezone.utc)).total_seconds() / 3600, 1) if latest else None}
    return result
