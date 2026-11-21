"""Alertas operacionais locais (Fase 17), avaliados sobre as métricas existentes — sem segundo sistema.

Lê o banco em modo SOMENTE LEITURA e devolve um nível (OK / ATENCAO / CRITICO), a lista de alertas
(código, nível, mensagem, valor) e as métricas usadas. Nada de texto de cliente, telefone ou segredo.
Código de saída: 0 OK, 1 ATENÇÃO, 2 CRÍTICO — feito para um timer do systemd (falha vira registro no
journal e aciona a unidade OnFailure). Não envia nada para fora da máquina nesta fase.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import sqlite3

LEVELS = ("OK", "ATENCAO", "CRITICO")


@dataclass(frozen=True)
class Thresholds:
    inbox_pending_minutes: float = 5.0        # entrada parada: worker não está trabalhando
    outbox_pending_minutes: float = 10.0
    inbox_backlog: int = 50
    worker_heartbeat_seconds: float = 180.0
    disk_free_warning_pct: float = 15.0
    disk_free_critical_pct: float = 5.0
    database_warning_pct: float = 80.0        # do limite STORAGE_MAX_BYTES
    backup_max_age_hours: float = 26.0
    offsite_max_age_hours: float = 26.0
    login_failures_24h: int = 30
    mfa_failures_24h: int = 10
    urgent_after_minutes: int | None = None   # padrão: o configurado no painel


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def collect(database: Path, *, now: datetime) -> dict[str, object]:
    """Métricas adicionais às de `ops metricas` (idades de fila, órfãos, 2º fator, configurações)."""
    since = (now - timedelta(days=1)).isoformat()
    with closing(sqlite3.connect(Path(database).as_uri() + "?mode=ro", uri=True)) as c:
        def one(sql: str, *params: object):  # type: ignore[no-untyped-def]
            row = c.execute(sql, params).fetchone()
            return row[0] if row else None
        return {
            "entrada_pendente": one("SELECT count(*) FROM channel_inbox WHERE status IN ('PENDING','PROCESSING')"),
            "entrada_pendente_mais_antiga": one("SELECT min(received_at) FROM channel_inbox "
                                                "WHERE status IN ('PENDING','PROCESSING')"),
            "entrada_falhas": one("SELECT count(*) FROM channel_inbox WHERE status='FAILED'"),
            "capacidade_esgotada_24h": one("SELECT count(*) FROM channel_inbox WHERE outcome IN "
                                           "('capacity_exhausted','capacity_retry_scheduled') AND updated_at>=?",
                                           (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")),
            "saida_pendente_mais_antiga": one("SELECT min(created_at) FROM channel_outbox "
                                              "WHERE status IN ('PENDING','RETRY','SENDING')"),
            "saida_incerta": one("SELECT count(*) FROM channel_outbox WHERE status='UNKNOWN'"),
            "saida_falha": one("SELECT count(*) FROM channel_outbox WHERE status='FAILED'"),
            "envios_interrompidos": (one("SELECT count(*) FROM channel_control WHERE key='dispatch_halt'") or 0) > 0,
            "aguardando_mais_antigo": one(
                "SELECT min((SELECT recorded_at FROM transitions t WHERE t.conversation_id=c.id AND "
                "t.current='WAITING_HUMAN' ORDER BY t.ordinal DESC LIMIT 1)) FROM conversations c "
                "LEFT JOIN panel_assignments a ON a.conversation_id=c.id "
                "WHERE c.state='WAITING_HUMAN' AND c.closed=0 AND a.user_id IS NULL"),
            "aguardando_sem_responsavel": one(
                "SELECT count(*) FROM conversations c LEFT JOIN panel_assignments a ON a.conversation_id=c.id "
                "WHERE c.state='WAITING_HUMAN' AND c.closed=0 AND a.user_id IS NULL"),
            "atendimento_com_inativo": one(
                "SELECT count(*) FROM panel_assignments a JOIN conversations c ON c.id=a.conversation_id "
                "JOIN panel_users u ON u.id=a.user_id WHERE u.active=0 AND c.state='HUMAN_ACTIVE' AND c.closed=0"),
            "falhas_login_24h": one("SELECT count(*) FROM panel_login_attempts WHERE attempted_at>=?", since),
            "falhas_2fa_24h": one("SELECT count(*) FROM panel_audit WHERE action='mfa_failed' AND at>=?", since),
            "falhas_armazenamento_24h": one("SELECT count(*) FROM audit WHERE operation='failure_rollback' "
                                            "AND recorded_at>=?", since),
            "urgente_apos_minutos": one("SELECT value FROM panel_settings WHERE key='urgent_after_minutes'"),
            "esquema": one("PRAGMA user_version"),
        }


def evaluate(database: Path, *, backups: Path | None = None, heartbeat: Path | None = None,
             offsite_state: Path | None = None, database_limit_bytes: int | None = None,
             backup_status: dict[str, object] | None = None,
             now: datetime | None = None, thresholds: Thresholds = Thresholds()) -> dict[str, object]:
    now = now or datetime.now(timezone.utc)
    database = Path(database)
    alerts: list[dict[str, object]] = []

    def add(code: str, level: str, message: str, value: object = None) -> None:
        alerts.append({"codigo": code, "nivel": level, "mensagem": message, "valor": value})

    try:
        m = collect(database, now=now)
    except sqlite3.Error:
        add("banco_inacessivel", "CRITICO", "Banco não pôde ser lido.")
        return _result(alerts, {}, now)

    def minutes_since(value: object) -> float | None:
        moment = _parse(value if isinstance(value, str) else None)
        return round((now - moment).total_seconds() / 60, 1) if moment else None

    inbox_age = minutes_since(m["entrada_pendente_mais_antiga"])
    if inbox_age is not None and inbox_age > thresholds.inbox_pending_minutes:
        add("entrada_parada", "CRITICO", "Mensagens recebidas esperando processamento: worker parado ou travado?",
            inbox_age)
    if (m["entrada_pendente"] or 0) > thresholds.inbox_backlog:
        add("entrada_acumulada", "ATENCAO", "Fila de entrada acumulada.", m["entrada_pendente"])
    if m["entrada_falhas"]:
        add("entrada_falhou", "ATENCAO", "Mensagens recebidas que falharam no processamento.", m["entrada_falhas"])
    if m["capacidade_esgotada_24h"]:
        add("capacidade_esgotada", "CRITICO", "Limite de conversas ativas atingido nas últimas 24 h.",
            m["capacidade_esgotada_24h"])
    outbox_age = minutes_since(m["saida_pendente_mais_antiga"])
    if outbox_age is not None and outbox_age > thresholds.outbox_pending_minutes and not m["envios_interrompidos"]:
        add("saida_parada", "ATENCAO", "Respostas aguardando envio há muito tempo.", outbox_age)
    if m["saida_incerta"]:
        add("envio_incerto", "CRITICO", "Envios com resultado INCERTO: conferir antes de qualquer reenvio.",
            m["saida_incerta"])
    if m["saida_falha"]:
        add("envio_falhou", "ATENCAO", "Envios que falharam.", m["saida_falha"])
    if m["envios_interrompidos"]:
        add("envios_interrompidos", "ATENCAO", "Envios interrompidos pela administração.")
    urgent = thresholds.urgent_after_minutes or m["urgente_apos_minutos"] or 15
    waited = minutes_since(m["aguardando_mais_antigo"])
    if waited is not None and waited >= float(urgent):  # type: ignore[arg-type]
        add("cliente_esperando", "ATENCAO", "Cliente aguardando atendimento humano além do tempo de urgência.", waited)
    if m["atendimento_com_inativo"]:
        add("atendimento_orfao", "CRITICO", "Atendimento em andamento com funcionário desativado.",
            m["atendimento_com_inativo"])
    if (m["falhas_login_24h"] or 0) > thresholds.login_failures_24h:
        add("falhas_login", "ATENCAO", "Muitas falhas de login nas últimas 24 h.", m["falhas_login_24h"])
    if (m["falhas_2fa_24h"] or 0) > thresholds.mfa_failures_24h:
        add("falhas_2fa", "ATENCAO", "Muitas falhas de segundo fator nas últimas 24 h.", m["falhas_2fa_24h"])
    if m["falhas_armazenamento_24h"]:
        add("falha_armazenamento", "ATENCAO", "Falhas de gravação revertidas nas últimas 24 h.",
            m["falhas_armazenamento_24h"])
    # Disco e tamanho do banco
    usage = shutil.disk_usage(database.parent)
    free_pct = round(usage.free * 100 / usage.total, 1)
    m["disco_livre_pct"] = free_pct
    if free_pct < thresholds.disk_free_critical_pct:
        add("disco_cheio", "CRITICO", "Disco do banco quase cheio.", free_pct)
    elif free_pct < thresholds.disk_free_warning_pct:
        add("disco_baixo", "ATENCAO", "Pouco espaço livre no disco do banco.", free_pct)
    size = sum(p.stat().st_size for p in database.parent.glob(database.name + "*") if p.is_file())
    m["banco_bytes"] = size
    if database_limit_bytes:
        pct = round(size * 100 / database_limit_bytes, 1)
        m["banco_pct_limite"] = pct
        if pct >= thresholds.database_warning_pct:
            add("banco_perto_do_limite", "ATENCAO", "Banco perto do limite configurado (STORAGE_MAX_BYTES).", pct)
    # Worker
    if heartbeat is not None:
        from rocha_reis_agent.worker import read_heartbeat
        beat = read_heartbeat(heartbeat)
        age = round(now.timestamp() - float(beat["at"]), 1) if beat and isinstance(beat.get("at"), (int, float)) \
            else None
        m["worker_batimento_segundos"] = age
        if age is None or age > thresholds.worker_heartbeat_seconds:
            add("worker_sem_batimento", "CRITICO", "Worker sem sinal de vida recente.", age)
    # Backups
    if backups is not None:
        from rocha_reis_agent.ops.backup import list_backups
        valid = [b for b in list_backups(backups) if b["ok"]]
        latest = max((str(b["created_at"]) for b in valid), default=None)
        age_h = round((now - _parse(latest)).total_seconds() / 3600, 1) if latest else None  # type: ignore[operator]
        m["backup_idade_horas"] = age_h
        if age_h is None or age_h > thresholds.backup_max_age_hours:
            add("backup_atrasado", "CRITICO", "Sem backup válido recente.", age_h)
    # Fase 18: estado do job de backup (antes ignorado). Uma rodada que falha com um backup antigo ainda
    # dentro de 26 h não aparecia; e o teste semanal de restauração podia falhar sem nenhum alerta.
    if backup_status:
        failures = int(backup_status.get("falhas_consecutivas") or 0)  # type: ignore[call-overload]
        m["backup_falhas_consecutivas"] = failures
        if failures:
            add("backup_falhando", "ATENCAO", "A última rodada do backup automático falhou.", failures)
        restore = backup_status.get("ultimo_teste_restauracao")
        if isinstance(restore, dict):
            m["teste_restauracao"] = restore.get("resultado")
            if restore.get("resultado") == "falhou":
                add("restauracao_falhou", "CRITICO", "O último teste de restauração do backup falhou.",
                    restore.get("quando"))
    if offsite_state is not None:
        from rocha_reis_agent.ops.offsite import load_state
        state = load_state(offsite_state)
        moment = _parse(state.get("ultimo_sucesso"))  # type: ignore[arg-type]
        age_h = round((now - moment).total_seconds() / 3600, 1) if moment else None
        m["externo_idade_horas"] = age_h
        if age_h is None or age_h > thresholds.offsite_max_age_hours:
            add("copia_externa_atrasada", "ATENCAO", "Cópia externa do backup atrasada ou nunca feita.", age_h)
    return _result(alerts, m, now)


def _result(alerts: list[dict[str, object]], metrics: dict[str, object], now: datetime) -> dict[str, object]:
    level = max((LEVELS.index(str(a["nivel"])) for a in alerts), default=0)
    return {"nivel": LEVELS[level], "gerado_em": now.isoformat(timespec="seconds"), "alertas": alerts,
            "metricas": metrics}


def exit_code(result: dict[str, object]) -> int:
    return LEVELS.index(str(result["nivel"]))


def thresholds_dict() -> dict[str, object]:
    return asdict(Thresholds())


def dumps(result: dict[str, object]) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2)
