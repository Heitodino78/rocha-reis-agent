"""Estado do servidor EM CAMADAS (Fase 18B-0) — o que está de pé, do mais básico ao atendimento.

Cada camada só é avaliada se a anterior estiver de pé; as seguintes ficam "nao_avaliado" (nunca se
presume que algo funciona). Somente leitura: abre o banco em modo `ro`, consulta /ready só em 127.0.0.1.

    servidor_ligado → disco_montado → banco_disponivel → worker_funcionando → webhook_pronto →
    painel_pronto → atendimento_operacional

`operacao_24_7` é informativa e fica FALSA enquanto a recuperação após reinício depender de desbloqueio
manual sem prazo de resposta acordado (perfil sem `continuidade.prazo_desbloqueio_minutos`).
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Callable
from urllib.parse import urlsplit

ORDER = ("servidor_ligado", "disco_montado", "banco_disponivel", "worker_funcionando", "webhook_pronto",
         "painel_pronto", "atendimento_operacional")
HEARTBEAT_MAX_SECONDS = 180.0


def _uptime() -> float | None:
    try:
        return round(float(Path("/proc/uptime").read_text().split()[0]), 1)
    except (OSError, ValueError, IndexError):
        return None


def server_state(root: Path, *, probe: Callable[..., int | None] | None = None,
                 now: datetime | None = None) -> dict[str, object]:
    from rocha_reis_agent.ops import deploy
    from rocha_reis_agent.worker import read_heartbeat
    probe = probe or deploy.http_status
    now = now or datetime.now(timezone.utc)
    paths = deploy.layout(root)
    layers: dict[str, dict[str, object]] = {}
    try:
        profile = json.loads((paths["config"] / "perfil.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        profile = None

    def run() -> None:
        layers["servidor_ligado"] = {"ok": True, "ligado_ha_segundos": _uptime()}
        if profile is None:
            layers["disco_montado"] = {"ok": False, "motivo": "perfil da instância ausente ou ilegível"}
            return
        spec = profile.get("volume") or {}
        if spec.get("id"):
            status = deploy.volume_status(root, spec)
            layers["disco_montado"] = {"ok": status.ready, **status.as_dict()}
        else:
            layers["disco_montado"] = {"ok": not deploy.is_server_root(root), "modo": "laboratorio",
                                       "motivo": None if not deploy.is_server_root(root) else
                                       "servidor sem volume dedicado identificado"}
        if not layers["disco_montado"]["ok"]:
            return
        database = Path(profile["database"])
        try:
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)) as c:
                check = c.execute("PRAGMA quick_check").fetchone()[0]
                schema = c.execute("PRAGMA user_version").fetchone()[0]
                halted = c.execute("SELECT count(*) FROM channel_control WHERE key='dispatch_halt'").fetchone()[0] > 0
                unknown = c.execute("SELECT count(*) FROM channel_outbox WHERE status='UNKNOWN'").fetchone()[0]
                pending = c.execute("SELECT count(*) FROM channel_inbox WHERE status IN ('PENDING','PROCESSING')"
                                    ).fetchone()[0]
        except (sqlite3.Error, OSError):
            layers["banco_disponivel"] = {"ok": False, "motivo": "banco não pôde ser aberto para leitura"}
            return
        layers["banco_disponivel"] = {"ok": check == "ok", "integridade": check, "esquema": schema,
                                      "entrada_pendente": pending, "envios_incertos": unknown}
        if check != "ok":
            return
        beat = read_heartbeat(paths["estado"] / "worker.json")
        age = (round(now.timestamp() - float(beat["at"]), 1) if beat and isinstance(beat.get("at"), (int, float))
               else None)
        layers["worker_funcionando"] = {"ok": age is not None and age <= HEARTBEAT_MAX_SECONDS,
                                        "batimento_ha_segundos": age}
        if not layers["worker_funcionando"]["ok"]:
            return
        webhook = probe("http://127.0.0.1:8080/ready")
        layers["webhook_pronto"] = {"ok": webhook == 200, "http": webhook}
        if webhook != 200:
            return
        host = urlsplit(str(profile.get("panel_origin") or "")).hostname or "127.0.0.1"
        panel = probe("http://127.0.0.1:8000/ready", host=host)
        layers["painel_pronto"] = {"ok": panel == 200, "http": panel}
        if panel != 200:
            return
        # UNKNOWN não para o atendimento, mas exige conferência humana antes de qualquer reenvio (nunca automático).
        layers["atendimento_operacional"] = {"ok": not halted, "envios_interrompidos": halted,
                                             "envios_incertos_para_conferir": unknown}

    run()
    for name in ORDER:
        layers.setdefault(name, {"ok": False, "nao_avaliado": True})
    continuity = (profile or {}).get("continuidade") or {}
    deadline = continuity.get("prazo_desbloqueio_minutos")
    first_down = next((n for n in ORDER if not layers[n]["ok"]), None)
    return {"gerado_em": now.isoformat(timespec="seconds"), "camadas": layers,
            "primeira_camada_parada": first_down, "atendimento_operacional": first_down is None,
            "operacao_24_7": bool(deadline) and first_down is None,
            "continuidade": {"desbloqueio": "manual", "prazo_desbloqueio_minutos": deadline,
                             "observacao": None if deadline else
                             "24/7 NÃO declarado: após reinício o atendimento só volta com desbloqueio manual, "
                             "sem prazo de resposta acordado"}}


def exit_code(state: dict[str, object]) -> int:
    """0 atendendo; 3 aguardando desbloqueio do volume; 1 qualquer outra camada parada."""
    if state["atendimento_operacional"]:
        return 0
    disk = state["camadas"]["disco_montado"]  # type: ignore[index]
    return 3 if disk.get("estado") == "AGUARDANDO_DESBLOQUEIO" else 1
