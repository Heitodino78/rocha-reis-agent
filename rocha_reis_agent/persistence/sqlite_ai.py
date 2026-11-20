"""Registro de consumo do provedor de IA (somente metadados) e consulta de orçamento."""

from datetime import datetime, timedelta
from typing import Any

from rocha_reis_agent.domain.conversations import utc_now
from rocha_reis_agent.persistence.sqlite import SQLiteConversationRepository


class SQLiteUsageLedger:
    def __init__(self, repository: SQLiteConversationRepository) -> None:
        self.repository = repository

    def record(self, *, provider: str, model: str, prompt_version: str, outcome: str, input_tokens: int = 0,
               output_tokens: int = 0, cache_read_tokens: int = 0, cache_write_tokens: int = 0,
               reasoning_tokens: int = 0, requested_model: str | None = None,
               latency_ms: int = 0, estimated_cost_micros: int | None = None, at: datetime | None = None) -> None:
        # Independente da conversa: o gasto ocorreu mesmo que a resposta seja descartada.
        with self.repository.write_transaction(independent=True) as connection:
            connection.execute(
                "INSERT INTO ai_usage(at,provider,model,prompt_version,outcome,input_tokens,output_tokens,"
                "cache_read_tokens,cache_write_tokens,latency_ms,estimated_cost_micros,reasoning_tokens,requested_model)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ((at or utc_now()).isoformat(), provider, model[:80], prompt_version, outcome[:40], input_tokens,
                 output_tokens, cache_read_tokens, cache_write_tokens, latency_ms, estimated_cost_micros,
                 reasoning_tokens, requested_model[:80] if requested_model else None))

    def window(self, since: datetime) -> dict[str, Any]:
        """Totais desde `since`. unpriced>0 indica chamadas cujo custo não pôde ser estimado."""
        with self.repository.read() as connection:
            row = connection.execute(
                "SELECT count(*), coalesce(sum(estimated_cost_micros),0), "
                "sum(CASE WHEN estimated_cost_micros IS NULL AND (input_tokens+output_tokens+reasoning_tokens)>0 "
                "THEN 1 ELSE 0 END) "
                "FROM ai_usage WHERE at>=?", (since.isoformat(),)).fetchone()
        return {"requests": row[0], "estimated_cost_micros": row[1], "unpriced": row[2] or 0}

    def summary(self, *, days: int = 1) -> dict[str, Any]:
        since = utc_now() - timedelta(days=days)
        with self.repository.read() as connection:
            rows = connection.execute(
                "SELECT provider, model, requested_model, prompt_version, outcome, count(*) AS n, "
                "sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens, "
                "sum(reasoning_tokens) AS reasoning_tokens, sum(cache_read_tokens) AS cache_read_tokens, "
                "avg(latency_ms) AS avg_latency_ms, max(latency_ms) AS max_latency_ms, "
                "sum(estimated_cost_micros) AS cost_micros FROM ai_usage WHERE at>=? "
                "GROUP BY provider, model, requested_model, prompt_version, outcome ORDER BY n DESC", (since.isoformat(),)).fetchall()
        return {"since": since.isoformat(), "rows": [dict(row) for row in rows]}
