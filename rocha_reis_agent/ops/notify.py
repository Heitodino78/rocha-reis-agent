"""Aviso EXTERNO de alertas (Fase 18B-0) — só a interface; nenhum canal real existe nesta fase.

O canal (e-mail, mensageria, serviço de monitoramento) é a Decisão 11 de `DECISOES_PENDENTES_SALAO.md`.
Até lá, `DisabledNotifier` não envia nada e o alerta fica no journal (`rocha-reis-alerta@`). O resumo
enviável contém SÓ códigos, níveis e horário — nunca mensagens de cliente, telefones, caminhos ou segredos.
"""

from __future__ import annotations

from typing import Protocol


class Notifier(Protocol):
    enabled: bool

    def notify(self, summary: dict[str, object]) -> None: ...


class DisabledNotifier:
    enabled = False

    def notify(self, summary: dict[str, object]) -> None:  # noqa: D401 — intencionalmente nada
        return None


def summary(result: dict[str, object], *, instance: str = "rocha-reis-preprod") -> dict[str, object]:
    """Conteúdo mínimo de um aviso: o que está errado, não os dados."""
    alerts = result.get("alertas") or []
    return {"instancia": instance, "nivel": result.get("nivel"), "gerado_em": result.get("gerado_em"),
            "alertas": sorted({(str(a["codigo"]), str(a["nivel"])) for a in alerts})}  # type: ignore[index, union-attr]


def dispatch(result: dict[str, object], notifier: Notifier | None = None) -> dict[str, object]:
    notifier = notifier or DisabledNotifier()
    content = summary(result)
    due = content["nivel"] != "OK"
    if due and notifier.enabled:
        notifier.notify(content)
    return {"habilitada": notifier.enabled, "seria_enviada": due, "enviada": bool(due and notifier.enabled),
            "canal": None if not notifier.enabled else type(notifier).__name__}
