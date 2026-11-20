"""Orçamento verificável de chamadas a provedores pagos (neutro em relação ao provedor).

Antes de cada chamada reserva-se o PIOR caso estimado; a chamada só sai se a reserva
couber nos tetos diário (persistido em `ai_usage`) e por execução (`RunBudget`, em
memória e compartilhável). Depois, a reserva é liberada pelo consumo RETORNADO pelo
provedor. Falhas com consumo desconhecido contabilizam o pior caso: incerteza nunca é
contada como zero. Todos os valores são ESTIMATIVAS pela tabela `ai/models.json`.
"""

from datetime import timedelta
from pathlib import Path
from threading import Lock

from rocha_reis_agent.ai.contracts import AIProviderError
from rocha_reis_agent.domain.conversations import utc_now
from rocha_reis_agent.persistence.sqlite_ai import SQLiteUsageLedger

# Consumo final desconhecido: registra-se a reserva de pior caso.
UNCERTAIN_OUTCOMES = frozenset({"timeout", "network_error", "provider_unavailable", "unexpected_error",
                                "unexpected_model", "usage_missing"})
# Recusados antes do processamento: sem cobrança.
UNBILLED_OUTCOMES = frozenset({"rate_limited", "auth_error", "permission_error", "bad_request",
                               "model_not_found", "concurrency_limited"})


class BudgetExceeded(AIProviderError):
    """Limite de consumo atingido ANTES da chamada: nada foi enviado ao provedor."""


class RunBudget:
    """Contadores de UMA execução (processo ou avaliação), compartilháveis entre instâncias."""

    def __init__(self, max_requests: int | None = None, max_cost_usd: float | None = None,
                 per_model_max_requests: dict[str, int] | None = None, *,
                 halt_file: "Path | None" = None, spent_micros: int = 0, spent_requests: int = 0,
                 spent_by_model: dict[str, int] | None = None) -> None:
        self.max_requests = max_requests
        self.max_cost_micros = None if max_cost_usd is None else max_cost_usd * 1e6
        self.per_model_max_requests = dict(per_model_max_requests or {})
        # Fase 9C: arquivo de interrupção emergencial; existindo, nenhuma chamada nova sai.
        self.halt_file = halt_file
        self.lock = Lock()
        # Contadores podem começar com o consumo JÁ registrado no período autorizado
        # (livro-razão persistente): o teto vale para todas as execuções, não só esta.
        self.requests = spent_requests
        self.requests_by_model: dict[str, int] = dict(spent_by_model or {})
        self.cost_micros = spent_micros
        self.reserved_micros = 0

    def halted(self) -> bool:
        return self.halt_file is not None and self.halt_file.exists()


def micros(tokens: int, rate_per_mtok: float) -> float:
    return tokens * rate_per_mtok  # tokens × US$/MTok = micro-dólares


class BudgetGuard:
    def __init__(self, ledger: SQLiteUsageLedger, run: RunBudget, *, max_requests_per_day: int,
                 max_cost_usd_per_day: float) -> None:
        self.ledger = ledger
        self.run = run
        self.max_requests_per_day = max_requests_per_day
        self.max_cost_micros_per_day = max_cost_usd_per_day * 1e6

    def reserve(self, model: str, worst: int | None) -> None:
        """Checa todos os tetos com a reserva; levanta BudgetExceeded sem chamar o provedor."""
        if worst is None:
            raise BudgetExceeded("Tarifa do modelo desconhecida: orçamento não verificável.")
        window = self.ledger.window(utc_now() - timedelta(days=1))
        run = self.run
        if run.halted():
            raise BudgetExceeded("Interrupção emergencial ativa: nenhuma chamada nova.")
        with run.lock:
            if window["requests"] >= self.max_requests_per_day:
                raise BudgetExceeded("Limite diário de requisições atingido.")
            if run.max_requests is not None and run.requests >= run.max_requests:
                raise BudgetExceeded("Limite de requisições desta execução atingido.")
            limit = run.per_model_max_requests.get(model)
            if limit is not None and run.requests_by_model.get(model, 0) >= limit:
                raise BudgetExceeded("Limite de requisições do modelo nesta execução atingido.")
            pending = run.reserved_micros + worst
            if window["unpriced"] or window["estimated_cost_micros"] + pending > self.max_cost_micros_per_day:
                raise BudgetExceeded("Limite diário de custo estimado atingido.")
            if run.max_cost_micros is not None and run.cost_micros + pending > run.max_cost_micros:
                raise BudgetExceeded("Limite de custo estimado desta execução atingido.")
            run.reserved_micros += worst
            run.requests += 1
            run.requests_by_model[model] = run.requests_by_model.get(model, 0) + 1

    def release(self, worst: int, spent: int | None) -> None:
        with self.run.lock:
            self.run.reserved_micros -= worst
            self.run.cost_micros += worst if spent is None else spent
