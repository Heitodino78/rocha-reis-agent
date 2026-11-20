"""Adaptador generativo real: Google Gemini API (SDK oficial `google-genai`). DESATIVADO.

Este módulo só é importado por `bootstrap.build_ai_provider` e pela avaliação real, e
somente quando a trava de código `GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED` (config.py)
for True — ela é False nesta fase. Testes e avaliações usam o MockTransport do httpx2
injetado no cliente: nenhuma conexão é aberta.

Papel do modelo: CLASSIFICAR e propor uma abertura curta (contrato ai-response-v2).
Todos os fatos são montados pelo backend a partir de registros aprovados.

Binding conferido no SDK instalado (google-genai 3.6.1) em 2026-11-20:
`models.generate_content(model, contents, config=GenerateContentConfig(system_instruction,
response_mime_type="application/json", response_json_schema, max_output_tokens,
thinking_config=ThinkingConfig(thinking_level), http_options=HttpOptions(timeout em ms,
retry_options=HttpRetryOptions(attempts))))`; consumo em `usage_metadata`
(prompt/candidates/thoughts/cached_content_token_count); modelo efetivo em `model_version`;
bloqueio em `prompt_feedback.block_reason` ou `finish_reason`; erros em
`google.genai.errors.ClientError/ServerError` (`.code`, `.status`) e exceções do httpx2.

SEGURANÇA: `genai.Client()` sem argumentos lê GEMINI_API_KEY/GOOGLE_API_KEY do ambiente.
Por isso o cliente só é criado por `create_client`, com chave EXPLÍCITA, `vertexai=False`
e `enterprise=False`, e somente depois da trava de código.
"""

from dataclasses import dataclass
import json
import math
from threading import BoundedSemaphore
from time import monotonic
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
import httpx2  # somente tipos de exceção do transporte usado pelo SDK; nenhum cliente é criado aqui

from rocha_reis_agent.ai.budget import (
    UNCERTAIN_OUTCOMES, BudgetExceeded, BudgetGuard, RunBudget, micros,
)
from rocha_reis_agent.ai.contracts import AIProviderError, AIProviderTimeout, AIRequest, AIValidationError
from rocha_reis_agent.ai.model_registry import Rates, load_registry
from rocha_reis_agent.ai.privacy import redact_personal_data
from rocha_reis_agent.domain.contracts import Intent
from rocha_reis_agent.persistence.sqlite_ai import SQLiteUsageLedger

# Estimativa CONSERVADORA de tokens de entrada antes da chamada (português ≈ 3–4
# caracteres/token; 2 superestima de propósito). Não é contagem real.
CHARS_PER_TOKEN_ESTIMATE = 2
BLOCKED_FINISH = frozenset({"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION", "MODEL_ARMOR",
                            "IMAGE_SAFETY", "IMAGE_PROHIBITED_CONTENT", "LANGUAGE"})

_REF = {"type": "object", "properties": {"id": {"type": "string"}, "version": {"type": "integer"}},
        "required": ["id", "version"], "additionalProperties": False}
# JSON Schema enviado em `response_json_schema`. Recursos usados: object/array/string/
# integer/boolean/null, enum, anyOf, required, additionalProperties — todos listados como
# suportados na documentação de saída estruturada. A validação definitiva é a do Pydantic
# (AIResponseV2) + validadores do backend: esquema válido ≠ conteúdo verdadeiro.
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
class GeminiConfig:
    model: str
    max_output_tokens: int = 2048            # inclui tokens de raciocínio
    thinking_level: str | None = None        # None = padrão do modelo (registro)
    max_retries: int = 1
    max_concurrency: int = 2
    max_requests_per_day: int = 500
    max_cost_usd_per_day: float = 5.0
    max_input_chars: int = 6000
    max_input_tokens: int = 4000             # ESTIMADOS (caracteres ÷ 2) antes da chamada
    max_requests_per_run: int | None = None
    max_cost_usd_per_run: float | None = None


def create_client(api_key: str, *, http_client: Any = None) -> genai.Client:
    """Único ponto de criação do cliente: chave explícita, Gemini Developer API (nunca Vertex/ambiente)."""
    if not api_key or len(api_key) < 20:
        raise AIProviderError("Credencial do provedor ausente.")
    options = types.HttpOptions(httpx_client=http_client) if http_client is not None else None
    return genai.Client(vertexai=False, enterprise=False, api_key=api_key, http_options=options)


def _cost(rates: Rates, *, uncached: int, cached: int, output: int) -> int:
    return round(micros(uncached, rates.input) + micros(cached, rates.cache_read) + micros(output, rates.output))


class GeminiProvider:
    """Implementa AIProvider. Nunca registra conteúdo; só metadados de consumo."""

    name = "gemini"

    def __init__(self, config: GeminiConfig, client: genai.Client, ledger: SQLiteUsageLedger,
                 run_budget: RunBudget | None = None) -> None:
        self.config = config
        self.client = client
        self.ledger = ledger
        self.registry = load_registry()
        spec = self.registry.spec(config.model)
        self.thinking_level = config.thinking_level or (spec.default_thinking_level if spec else None)
        self._slots = BoundedSemaphore(config.max_concurrency)
        self.run = run_budget or RunBudget(config.max_requests_per_run, config.max_cost_usd_per_run)
        self.guard = BudgetGuard(ledger, self.run, max_requests_per_day=config.max_requests_per_day,
                                 max_cost_usd_per_day=config.max_cost_usd_per_day)

    def __repr__(self) -> str:
        return f"GeminiProvider(model={self.config.model!r})"

    @property
    def run_requests(self) -> int:
        return self.run.requests

    @property
    def run_cost_micros(self) -> int:
        return self.run.cost_micros

    # ------------------------------------------------------------- requisição
    def build_messages(self, request: AIRequest) -> tuple[str, str]:
        """Instrução de sistema (confiável, estável) e conteúdo (dados rotulados por nível de confiança)."""
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
            raise BudgetExceeded("Entrada excede o limite configurado.")
        if math.ceil((len(system) + len(text)) / CHARS_PER_TOKEN_ESTIMATE) > self.config.max_input_tokens:
            raise BudgetExceeded("Entrada estimada excede o limite de tokens configurado.")
        return system, text

    # ------------------------------------------------------------- orçamento
    def worst_case_micros(self, system: str, text: str) -> int | None:
        """Pior caso de UMA chamada lógica, antes de enviá-la. None = tarifa desconhecida."""
        rates = self.registry.rates(self.config.model)
        if rates is None:
            return None
        tokens_in = math.ceil((len(system) + len(text)) / CHARS_PER_TOKEN_ESTIMATE)
        per_attempt = micros(tokens_in, rates.input) + micros(self.config.max_output_tokens, rates.output)
        return math.ceil(per_attempt * (self.config.max_retries + 1))

    def usage_cost(self, response: Any) -> tuple[int | None, dict[str, int]]:
        """Custo estimado pelo consumo RETORNADO. Tokens de raciocínio cobrados como saída."""
        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            return None, {}
        prompt = (usage.prompt_token_count or 0) + (usage.tool_use_prompt_token_count or 0)
        cached = usage.cached_content_token_count or 0
        output = usage.candidates_token_count or 0
        thoughts = usage.thoughts_token_count or 0
        tokens = {"input": max(prompt - cached, 0), "cached": cached, "output": output, "reasoning": thoughts}
        rates = self.registry.rates(self.config.model)
        if rates is None:
            return None, tokens
        return _cost(rates, uncached=tokens["input"], cached=cached, output=output + thoughts), tokens

    def _served_model(self, response: Any) -> str:
        return str(getattr(response, "model_version", None) or self.config.model)

    def _expected_model(self, served: str) -> bool:
        # Versões com sufixo do mesmo modelo ("gemini-3.1-flash-lite-001") são aceitas.
        return served == self.config.model or served.startswith(self.config.model + "-")

    # -------------------------------------------------------------- chamada
    def generate(self, request: AIRequest, *, timeout_seconds: float) -> str:
        if request.output_contract != "ai-response-v2":
            raise AIProviderError("Provedor real exige o contrato ai-response-v2.")
        system, text = self.build_messages(request)
        worst = self.worst_case_micros(system, text)
        self.guard.reserve(self.config.model, worst)
        assert worst is not None
        if not self._slots.acquire(timeout=min(timeout_seconds, 2.0)):
            self.guard.release(worst, 0)
            self._record(request, "concurrency_limited", cost=0)
            raise AIProviderError("Limite de chamadas simultâneas atingido.")
        started = monotonic()
        outcome = "unexpected_error"  # vira "ok" só depois que a chamada retorna
        try:
            attempts = self.config.max_retries + 1
            # Prazo TOTAL: o SDK repete falhas transitórias (inclusive timeout); cada tentativa recebe uma fração.
            per_attempt_ms = max(1, int(timeout_seconds * 1000 / attempts))
            config = types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_json_schema=RESPONSE_SCHEMA_V2,
                max_output_tokens=self.config.max_output_tokens,
                thinking_config=types.ThinkingConfig(thinking_level=self.thinking_level.upper())
                if self.thinking_level else None,
                http_options=types.HttpOptions(
                    timeout=per_attempt_ms,
                    retry_options=types.HttpRetryOptions(attempts=attempts, initial_delay=0.5, max_delay=2.0)),
            )
            response = self.client.models.generate_content(model=self.config.model, contents=text, config=config)
            outcome = "ok"
        except httpx2.TimeoutException:
            outcome = "timeout"
            raise AIProviderTimeout("Tempo esgotado no provedor.") from None
        except httpx2.TransportError:
            outcome = "network_error"
            raise AIProviderError("Falha de rede com o provedor.") from None
        except genai_errors.ClientError as error:
            outcome = self._client_error_outcome(error)
            raise AIProviderError({
                "rate_limited": "Limite de taxa do provedor.", "auth_error": "Credencial do provedor inválida.",
                "permission_error": "Permissão negada pelo provedor.",
                "model_not_found": "Modelo ou recurso inexistente no provedor.",
            }.get(outcome, "Requisição recusada pelo provedor.")) from None
        except genai_errors.APIError:
            outcome = "provider_unavailable"
            raise AIProviderError("Provedor indisponível.") from None
        except Exception:  # noqa: BLE001 — qualquer outra falha: consumo incerto, falha segura
            raise AIProviderError("Falha inesperada no provedor.") from None
        finally:
            self._slots.release()
            if outcome != "ok":
                cost = worst if outcome in UNCERTAIN_OUTCOMES else 0
                self.guard.release(worst, cost)
                self._record(request, outcome, started, cost=cost)
        return self._read(request, response, worst, started)

    @staticmethod
    def _client_error_outcome(error: genai_errors.ClientError) -> str:
        code, status = error.code, str(error.status or "")
        message = str(error.message or "").lower()
        if code == 429 or status == "RESOURCE_EXHAUSTED":
            return "rate_limited"
        if code == 401 or status == "UNAUTHENTICATED" or (code == 400 and "api key" in message):
            return "auth_error"
        if code == 403 or status == "PERMISSION_DENIED":
            return "permission_error"
        if code == 404 or status == "NOT_FOUND":
            return "model_not_found"
        return "bad_request"

    def _read(self, request: AIRequest, response: Any, worst: int, started: float) -> str:
        served = self._served_model(response)
        spent, tokens = self.usage_cost(response)
        outcome = "ok"
        if not self._expected_model(served):
            outcome, spent = "unexpected_model", worst      # tarifa do modelo efetivo desconhecida: pior caso
        elif spent is None:
            outcome, spent = "usage_missing", worst
        feedback = getattr(response, "prompt_feedback", None)
        candidates = getattr(response, "candidates", None) or []
        reason = getattr(candidates[0], "finish_reason", None) if candidates else None
        finish = str(getattr(reason, "value", reason) or "")
        if outcome == "ok":
            if feedback is not None and getattr(feedback, "block_reason", None):
                outcome = "blocked_prompt"
            elif not candidates:
                outcome = "empty"
            elif finish in BLOCKED_FINISH:
                outcome = "blocked_output"
            elif finish == "MAX_TOKENS":
                outcome = "truncated"
            elif finish not in ("STOP", ""):
                outcome = "unexpected_finish"
        self.guard.release(worst, spent)
        self._record(request, outcome, started, cost=spent, tokens=tokens, served_model=served)
        # Sempre checar bloqueio/encerramento ANTES de ler o conteúdo.
        if outcome == "unexpected_model":
            raise AIValidationError("Resposta de modelo diferente do solicitado.")
        if outcome in ("blocked_prompt", "blocked_output"):
            raise AIProviderError("O provedor bloqueou a solicitação ou a resposta.")
        if outcome == "truncated":
            raise AIValidationError("Resposta truncada pelo limite de tokens.")
        if outcome in ("empty", "unexpected_finish"):
            raise AIValidationError("Encerramento inesperado ou resposta vazia do provedor.")
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None) or []
        texts = [p.text for p in parts if getattr(p, "text", None) and not getattr(p, "thought", False)]
        if not texts or not "".join(texts).strip():
            raise AIValidationError("Resposta vazia do provedor.")
        return "".join(texts)

    def _record(self, request: AIRequest, outcome: str, started: float | None = None, *, cost: int | None,
                tokens: dict[str, int] | None = None, served_model: str | None = None) -> None:
        tokens = tokens or {}
        self.ledger.record(
            provider=self.name, model=served_model or self.config.model, requested_model=self.config.model,
            prompt_version=request.prompt_version, outcome=outcome,
            input_tokens=tokens.get("input", 0), output_tokens=tokens.get("output", 0),
            reasoning_tokens=tokens.get("reasoning", 0), cache_read_tokens=tokens.get("cached", 0),
            latency_ms=int((monotonic() - started) * 1000) if started else 0, estimated_cost_micros=cost,
        )
