"""Fase 9C — orçamento PROPOSTO da homologação real, calculado a partir dos pedidos REAIS do adaptador.

Executa cada etapa pré-registrada com o GeminiProvider sobre HTTP simulado (nenhuma rede),
mede o tamanho exato de cada pedido que seria enviado (instrução de sistema + dados) e a
quantidade de chamadas, e precifica pela tabela verificada `ai/models.json`.

Premissas (explícitas; NÃO são medições):
- tokens de entrada esperados = caracteres ÷ 3,5 (português); pessimista/teto = caracteres ÷ 2
  (mesma base da reserva de pior caso do adaptador);
- saída (inclui raciocínio): esperado 300, pessimista 1.500, teto = max_output_tokens por tentativa;
- teto também multiplica pelas tentativas (max_retries + 1);
- margem de 50% sobre o pessimista para cobranças fora da medição (repetições manuais, erros).
"""

from contextlib import contextmanager
from datetime import date
import json
import math
from pathlib import Path
import tempfile
from typing import Any, Iterator

from rocha_reis_agent.ai.gemini_provider import GeminiProvider
from rocha_reis_agent.ai.model_registry import load_registry
from rocha_reis_agent.evaluation.e2e import run_e2e
from rocha_reis_agent.evaluation.providers import simulated_gemini

EXPECTED_CHARS_PER_TOKEN = 3.5
WORST_CHARS_PER_TOKEN = 2
OUTPUT_EXPECTED, OUTPUT_PESSIMISTIC = 300, 1500
MARGIN = 1.5


@contextmanager
def _measure() -> Iterator[list[int]]:
    sizes: list[int] = []
    original = GeminiProvider.build_messages

    def recording(self: GeminiProvider, request: Any) -> tuple[str, str]:
        system, text = original(self, request)
        sizes.append(len(system) + len(text))
        return system, text
    GeminiProvider.build_messages = recording  # type: ignore[method-assign]
    try:
        yield sizes
    finally:
        GeminiProvider.build_messages = original  # type: ignore[method-assign]


def measure_stage(stage: str, *, prompt_version: str = "v4", model: str = "gemini-3.1-flash-lite") -> list[int]:
    """Tamanho (caracteres) de cada pedido que a etapa enviaria ao provedor."""
    from rocha_reis_agent.evaluation.runner import DEFAULT_CATALOG, DEFAULT_SCENARIOS, evaluate
    from rocha_reis_agent.evaluation.scenarios import load_scenarios
    with _measure() as sizes:
        if stage == "e2e":
            run_e2e(lambda repo: simulated_gemini(repo, model=model)[0], catalog_path=DEFAULT_CATALOG,
                    prompt_version=prompt_version)
        else:
            evaluate(load_scenarios(DEFAULT_SCENARIOS), provider="gemini-simulated", prompt_version=prompt_version,
                     model=model, stage=stage, criteria_path=None)
    return list(sizes)


def price(sizes: list[int], model: str, *, max_output_tokens: int = 2048, max_retries: int = 1,
          day: date | None = None) -> dict[str, Any]:
    rates = load_registry().rates(model, day)
    if rates is None:
        raise ValueError("Modelo fora do registro verificado.")
    n = len(sizes)
    expected_in = sum(math.ceil(s / EXPECTED_CHARS_PER_TOKEN) for s in sizes)
    worst_in = sum(math.ceil(s / WORST_CHARS_PER_TOKEN) for s in sizes)
    usd = lambda tokens_in, tokens_out: (tokens_in * rates.input + tokens_out * rates.output) / 1e6  # noqa: E731
    expected = usd(expected_in, OUTPUT_EXPECTED * n)
    pessimistic = usd(worst_in, OUTPUT_PESSIMISTIC * n)
    ceiling = usd(worst_in, max_output_tokens * n) * (max_retries + 1)
    return {"model": model, "requests": n, "chars_mean": round(sum(sizes) / n, 1) if n else 0,
            "chars_max": max(sizes) if sizes else 0, "input_tokens_expected": expected_in,
            "usd_expected": round(expected, 4), "usd_pessimistic": round(pessimistic, 4),
            "usd_ceiling_all_retries_full_output": round(ceiling, 4),
            "usd_with_margin": round(pessimistic * MARGIN, 4)}


def plan(models: tuple[str, ...] = ("gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-3.8-flash"),
         stages: tuple[str, ...] = ("smoke", "pilot", "e2e", "full", "repeat"), *, prompt_version: str = "v4",
         day: date | None = None) -> dict[str, Any]:
    sizes = {stage: measure_stage(stage, prompt_version=prompt_version) for stage in stages}
    table = {model: {stage: price(sizes[stage], model, day=day) for stage in stages} for model in models}
    return {"assumptions": {"expected_chars_per_token": EXPECTED_CHARS_PER_TOKEN,
                            "worst_chars_per_token": WORST_CHARS_PER_TOKEN, "output_expected": OUTPUT_EXPECTED,
                            "output_pessimistic": OUTPUT_PESSIMISTIC, "margin": MARGIN,
                            "registry_verified_at": load_registry().verified_at,
                            "priced_on": (day or date.today()).isoformat(),
                            "note": "ESTIMATIVA. Não é medição nem fatura; o orçamento interno não impede "
                                    "cobranças fora do escopo (use também limites de faturamento do Google)."},
            "stage_requests": {stage: len(v) for stage, v in sizes.items()}, "by_model": table}


def main_plan(output: Path | None = None) -> dict[str, Any]:
    with tempfile.TemporaryDirectory():
        result = plan()
    if output is not None:
        output.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return result
