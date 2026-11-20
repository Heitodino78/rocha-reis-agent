"""Adaptador generativo real: Anthropic Messages API (SDK oficial `anthropic`). DESATIVADO.

Este módulo só é importado por `bootstrap.build_ai_provider`, e somente quando a
trava de código `AI_REAL_PROVIDER_ACTIVATION_AUTHORIZED` (config.py) for True — ela
é False nesta fase. Os testes usam `httpx2.MockTransport`: nenhuma conexão é aberta.

Papel do modelo: CLASSIFICAR e propor uma abertura curta (contrato ai-response-v2).
Todos os fatos são montados pelo backend a partir de registros aprovados.

Referência consultada: skill oficial claude-api (Claude Opus 5.5: thinking sempre
ligado, sem temperature/top_p, effort em output_config, saída estruturada via
output_config.format, recusa via stop_reason "refusal", fallback server-side beta).
"""

from dataclasses import dataclass
from datetime import timedelta
import json
from pathlib import Path
from threading import BoundedSemaphore
from time import monotonic
from typing import Any, Literal

import anthropic

from rocha_reis_agent.ai.contracts import AIProviderError, AIProviderTimeout, AIRequest, AIValidationError
from rocha_reis_agent.ai.privacy import redact_personal_data
from rocha_reis_agent.domain.contracts import Intent
from rocha_reis_agent.domain.conversations import utc_now
from rocha_reis_agent.persistence.sqlite_ai import SQLiteUsageLedger

PRICING = json.loads((Path(__file__).parent / "pricing.json").read_text(encoding="utf-8"))
FALLBACK_BETA = "server-side-fallback-2026-07-01"

_REF = {"type": "object", "properties": {"id": {"type": "string"}, "version": {"type": "integer"}},
        "required": ["id", "version"], "additionalProperties": False}
# JSON Schema enviado em output_config.format. A validação definitiva é a do Pydantic
# (AIResponseV2) + validadores do backend: esquema válido não significa conteúdo verdadeiro.
RESPONSE_SCHEMA_V2: dict[str, Any] = {
    "type": "object",
    "properties": {
        "contract": {"type": "string", "enum": ["ai-response-v2"]},
        "intents": {"type": "array", "items": {"type": "string", "enum": [i.value for i in Intent]}},
        "service_ids": {"type": "array", "items": {"type": "string"}},
        "use_context": {"type": "boolean"},
        "catalog_requested": {"type": "boolean"},
        "faq_reference": {"anyOf": [_REF, {"type": "null"}]},
        "fact_references": {"type": "array", "items": _REF},
        "needs_clarification": {"type": "boolean"},
        "human_handoff_suggested": {"type": "boolean"},
        "information_missing": {"type": "boolean"},
        "preface": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "required": ["contract", "intents", "service_ids", "use_context", "catalog_requested", "faq_reference",
                 "fact_references", "needs_clarification", "human_handoff_suggested", "information_missing", "preface"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class AnthropicConfig:
    model: str
    max_output_tokens: int = 4096           # inclui tokens de thinking (sempre ligado no Opus 5.5)
    effort: Literal["low", "medium", "high"] = "medium"
    max_retries: int = 1
    refusal_fallback: Literal["default", "off"] = "default"
    max_concurrency: int = 2
    max_requests_per_day: int = 500
    max_cost_usd_per_day: float = 5.0
    max_input_chars: int = 6000


class AnthropicProvider:
    """Implementa AIProvider. Nunca registra conteúdo; só metadados de consumo."""

    name = "anthropic"

    def __init__(self, config: AnthropicConfig, client: anthropic.Anthropic, ledger: SQLiteUsageLedger) -> None:
        self.config = config
        self.client = client
        self.ledger = ledger
        self._slots = BoundedSemaphore(config.max_concurrency)

    def __repr__(self) -> str:
        return f"AnthropicProvider(model={self.config.model!r})"

    # ------------------------------------------------------------- requisição
    def build_messages(self, request: AIRequest) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """System (confiável, estável → cache) e user (dados rotulados por nível de confiança)."""
        system = "\n".join([f"Objetivo: {request.objective}", *request.system_instructions,
                            "Responda somente com JSON válido no contrato ai-response-v2."])
        payload = {
            "fatos_aprovados_somente_conteudo": [
                {"referencia": {"id": f.reference.id, "version": f.reference.version},
                 "dados": f.data.model_dump(mode="json")} for f in request.facts
            ],
            "servico_em_contexto": list(request.service_context),
            "historico_relacionado_do_cliente_NAO_CONFIAVEL": [redact_personal_data(h.text) for h in request.history],
            "mensagem_atual_do_cliente_NAO_CONFIAVEL": redact_personal_data(request.message.text),
        }
        text = json.dumps(payload, ensure_ascii=False)
        if len(text) > self.config.max_input_chars:
            raise AIProviderError("Entrada excede o limite configurado.")
        system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        messages = [{"role": "user", "content": text}]
        return system_blocks, messages

    # ------------------------------------------------------------- orçamento
    def _check_budget(self) -> None:
        window = self.ledger.window(utc_now() - timedelta(days=1))
        if window["requests"] >= self.config.max_requests_per_day:
            raise AIProviderError("Limite diário de requisições atingido.")
        if self.config.model not in PRICING["models"]:
            raise AIProviderError("Tarifa do modelo desconhecida: orçamento não verificável.")
        if window["unpriced"] or window["estimated_cost_micros"] >= self.config.max_cost_usd_per_day * 1_000_000:
            raise AIProviderError("Limite diário de custo estimado atingido.")

    def estimate_cost_micros(self, usage: Any) -> int | None:
        price = PRICING["models"].get(self.config.model)
        if price is None or usage is None:
            return None
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        dollars = ((usage.input_tokens or 0) * price["input"] + (usage.output_tokens or 0) * price["output"]
                   + cache_read * price.get("cache_read", price["input"]) + cache_write * price["input"] * 1.25) / 1_000_000
        return round(dollars * 1_000_000)

    # -------------------------------------------------------------- chamada
    def generate(self, request: AIRequest, *, timeout_seconds: float) -> str:
        if request.output_contract != "ai-response-v2":
            raise AIProviderError("Provedor real exige o contrato ai-response-v2.")
        self._check_budget()
        if not self._slots.acquire(timeout=min(timeout_seconds, 2.0)):
            self._record(request, "concurrency_limited")
            raise AIProviderError("Limite de chamadas simultâneas atingido.")
        started = monotonic()
        try:
            system, messages = self.build_messages(request)
            kwargs: dict[str, Any] = dict(
                model=self.config.model, max_tokens=self.config.max_output_tokens, system=system, messages=messages,
                output_config={"effort": self.config.effort,
                               "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA_V2}},
            )
            if self.config.refusal_fallback == "default":
                kwargs.update(betas=[FALLBACK_BETA], fallbacks="default")
            client = self.client.with_options(timeout=timeout_seconds, max_retries=self.config.max_retries)
            response = client.beta.messages.create(**kwargs)
        except anthropic.APITimeoutError:
            self._record(request, "timeout", started)
            raise AIProviderTimeout("Tempo esgotado no provedor.") from None
        except anthropic.APIConnectionError:
            self._record(request, "network_error", started)
            raise AIProviderError("Falha de rede com o provedor.") from None
        except anthropic.RateLimitError:
            self._record(request, "rate_limited", started)
            raise AIProviderError("Limite de taxa do provedor.") from None
        except anthropic.AuthenticationError:
            self._record(request, "auth_error", started)
            raise AIProviderError("Credencial do provedor inválida.") from None
        except anthropic.PermissionDeniedError:
            self._record(request, "permission_error", started)
            raise AIProviderError("Permissão negada pelo provedor.") from None
        except anthropic.BadRequestError:
            self._record(request, "bad_request", started)
            raise AIProviderError("Requisição recusada pelo provedor.") from None
        except anthropic.APIStatusError:
            self._record(request, "provider_unavailable", started)
            raise AIProviderError("Provedor indisponível.") from None
        finally:
            self._slots.release()

        usage = getattr(response, "usage", None)
        outcome = {"refusal": "refusal", "max_tokens": "truncated"}.get(str(response.stop_reason), "ok")
        self._record(request, outcome, started, usage)
        # Sempre checar stop_reason ANTES de ler o conteúdo.
        if response.stop_reason == "refusal":
            raise AIProviderError("O provedor recusou a solicitação.")
        if response.stop_reason == "max_tokens":
            raise AIValidationError("Resposta truncada pelo limite de tokens.")
        texts = [block.text for block in response.content if getattr(block, "type", "") == "text"]
        if not texts or not texts[-1].strip():
            raise AIValidationError("Resposta vazia do provedor.")
        return texts[-1]

    def _record(self, request: AIRequest, outcome: str, started: float | None = None, usage: Any = None) -> None:
        self.ledger.record(
            provider=self.name, model=self.config.model, prompt_version=request.prompt_version, outcome=outcome,
            input_tokens=getattr(usage, "input_tokens", 0) or 0, output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            latency_ms=int((monotonic() - started) * 1000) if started else 0,
            estimated_cost_micros=self.estimate_cost_micros(usage) if usage is not None else None,
        )
