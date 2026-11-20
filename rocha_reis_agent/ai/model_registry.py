"""Registro VERIFICADO de modelos Gemini (ai/models.json): capacidades, ciclo de vida e tarifas.

Só modelos listados aqui podem ser configurados para o provedor real. A tabela é
conferida manualmente na documentação oficial (data em `verified_at`); um modelo
fora dela, sem saída estruturada, não estável ou sem tarifa conhecida é recusado antes
de qualquer chamada. Custos calculados com estas tarifas são ESTIMATIVAS.
"""

from datetime import date
from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

ThinkingLevel = Literal["minimal", "low", "medium", "high"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Rates(_Model):
    input: float = Field(ge=0)
    output: float = Field(ge=0)          # inclui tokens de raciocínio
    cache_read: float = Field(ge=0)      # tokens de entrada servidos de cache


class ScheduledRates(Rates):
    effective_from: date


class Pricing(Rates):
    schedule: tuple[ScheduledRates, ...] = ()

    def at(self, day: date) -> Rates:
        current = Rates(input=self.input, output=self.output, cache_read=self.cache_read)
        for change in sorted(self.schedule, key=lambda c: c.effective_from):
            if day >= change.effective_from:
                current = Rates(input=change.input, output=change.output, cache_read=change.cache_read)
        return current


class ModelSpec(_Model):
    display_name: str
    family: Literal["flash-lite", "flash", "pro"]
    lifecycle: Literal["stable", "preview", "deprecated"]
    latest_update: str
    input_token_limit: int = Field(gt=0)
    output_token_limit: int = Field(gt=0)
    structured_outputs: bool
    thinking_levels: tuple[ThinkingLevel, ...] = Field(min_length=1)
    default_thinking_level: ThinkingLevel
    free_tier: bool
    pricing: Pricing


class ModelRegistry(_Model):
    schema_version: Literal[2]
    provider: Literal["gemini"]
    api: str
    currency: Literal["USD"]
    unit: Literal["per_million_tokens"]
    tier: Literal["paid_standard"]
    verified_at: str
    sources: tuple[str, ...]
    status: str
    models: dict[str, ModelSpec]
    not_registered: dict[str, str]
    notes: tuple[str, ...]

    def spec(self, model_id: str) -> ModelSpec | None:
        return self.models.get(model_id)

    def rates(self, model_id: str, day: date | None = None) -> Rates | None:
        spec = self.models.get(model_id)
        return None if spec is None else spec.pricing.at(day or date.today())


class ModelNotSupported(ValueError):
    """Modelo fora do registro verificado ou sem a capacidade exigida pelo contrato."""


@lru_cache(maxsize=1)
def load_registry(path: Path | None = None) -> ModelRegistry:
    source = path or Path(__file__).with_name("models.json")
    try:
        return ModelRegistry.model_validate_json(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError, json.JSONDecodeError):
        raise ModelNotSupported("Registro de modelos inválido ou indisponível.") from None


def require_model(model_id: str, *, thinking_level: str | None = None,
                  max_output_tokens: int | None = None) -> ModelSpec:
    """Valida o modelo para o contrato ai-response-v2. Mensagens sem valores privados."""
    spec = load_registry().spec(model_id)
    if spec is None:
        raise ModelNotSupported("Modelo não consta do registro verificado (ai/models.json).")
    if spec.lifecycle != "stable":
        raise ModelNotSupported("Somente modelos estáveis podem ser usados (preview/descontinuado recusado).")
    if not spec.structured_outputs:
        raise ModelNotSupported("Modelo sem saída estruturada: incompatível com o contrato ai-response-v2.")
    if thinking_level is not None and thinking_level not in spec.thinking_levels:
        raise ModelNotSupported("Nível de raciocínio não aceito pelo modelo.")
    if max_output_tokens is not None and max_output_tokens > spec.output_token_limit:
        raise ModelNotSupported("Limite de saída acima do aceito pelo modelo.")
    return spec


def describe(model_id: str) -> dict[str, Any]:
    spec = load_registry().spec(model_id)
    return {} if spec is None else spec.model_dump(mode="json")
