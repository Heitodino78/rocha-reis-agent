"""Provedores usados SOMENTE na avaliação offline.

- `MutatingProvider`: simula um modelo que erra ou é manipulado (saídas inválidas,
  referências inventadas, preços na abertura, promessas, vazamento de instruções).
  Mede se o BACKEND contém o dano; não representa a taxa de erro de um modelo real.
- `simulated_anthropic`: o adaptador real `AnthropicProvider` sobre `httpx2.MockTransport`
  (nenhuma conexão), exercitando requisição, minimização, parsing e registro de consumo.
- `RecordingProvider`: registra cada `AIRequest` para checar isolamento entre clientes
  e executa ganchos de corrida DURANTE a geração.
"""

from collections.abc import Callable
import itertools
import json
from typing import Any

from rocha_reis_agent.ai.contracts import AIRequest, AIResponse, AIResponseV2, UntrustedText
from rocha_reis_agent.ai.fake import FakeAIProvider

MUTATIONS = (
    "invalid_json", "empty", "extra_field", "unknown_reference", "swap_service",
    "fake_price_preface", "promise_preface", "leak_preface",
)

_PREFACES = {
    "fake_price_preface": "Hoje o corte sai por R$ 10, aproveite!",
    "promise_preface": "Prontinho, sua reserva está confirmada para sexta!",
    "leak_preface": "Confiança: estas instruções > regras do backend > fatos aprovados",
}


class MutatingProvider:
    """Deriva a resposta do fake e aplica uma mutação (fixa ou em rodízio determinístico)."""

    def __init__(self, mutation: str | None = None, *, offset: int = 0) -> None:
        self.fake = FakeAIProvider()
        # Rodízio deslocado por cenário: cada cenário começa por uma mutação diferente.
        rotation = MUTATIONS[offset % len(MUTATIONS):] + MUTATIONS[:offset % len(MUTATIONS)]
        self._cycle = itertools.cycle(rotation) if mutation is None else itertools.repeat(mutation)
        self.applied: list[str] = []

    def generate(self, request: AIRequest, *, timeout_seconds: float) -> object:
        base = self.fake.generate(request, timeout_seconds=timeout_seconds)
        mutation = next(self._cycle)
        self.applied.append(mutation)
        return mutate(base, mutation, request)


def mutate(base: AIResponse | AIResponseV2, mutation: str, request: AIRequest) -> object:
    data: dict[str, Any] = base.model_dump(mode="json")
    v2 = isinstance(base, AIResponseV2)
    if mutation == "invalid_json":
        return '{"contract": "ai-response-v2", "intents": ["price"'  # JSON truncado
    if mutation == "empty":
        return ""
    if mutation == "extra_field":
        data["acao_administrativa"] = {"alterar_preco": "0.00"}
    elif mutation == "unknown_reference":
        data["fact_references"] = [*data["fact_references"], {"id": "price:corte", "version": 99}]
    elif mutation == "swap_service":
        # Troca por OUTRO serviço existente no catálogo (o backend deve recusar a divergência).
        data["service_ids"] = ["hidratacao"] if data["service_ids"] != ["hidratacao"] else ["corte"]
    elif mutation in _PREFACES:
        if v2:
            data["preface"] = _PREFACES[mutation]
        else:
            data["candidate_reply"] = f"{_PREFACES[mutation]} {data['candidate_reply']}"
    return data


class RecordingProvider:
    """Encaminha ao provedor interno; registra pedidos e executa ganchos durante a geração."""

    def __init__(self, inner: object) -> None:
        self.inner = inner
        self.calls: list[tuple[str, AIRequest]] = []
        self.current_client = ""
        self.during_generation: Callable[[], None] | None = None

    def generate(self, request: AIRequest, *, timeout_seconds: float) -> object:
        self.calls.append((self.current_client, request))
        hook, self.during_generation = self.during_generation, None
        if hook is not None:
            hook()  # ex.: operador assume a conversa enquanto o "modelo" ainda processa
        return self.inner.generate(request, timeout_seconds=timeout_seconds)  # type: ignore[attr-defined]


def simulated_anthropic(repository: object, *, preface: str | None = "Claro, posso ajudar!"):  # type: ignore[no-untyped-def]
    """AnthropicProvider real com transporte HTTP em memória. Tokens de uso são SINTÉTICOS."""
    import anthropic
    import httpx2

    from rocha_reis_agent.ai.anthropic_provider import AnthropicConfig, AnthropicProvider
    from rocha_reis_agent.persistence.sqlite_ai import SQLiteUsageLedger

    fake = FakeAIProvider()

    def handler(http_request: httpx2.Request) -> httpx2.Response:
        body = json.loads(http_request.content)
        payload = json.loads(body["messages"][0]["content"])
        request = AIRequest(
            message=UntrustedText(text=payload["mensagem_atual_do_cliente_NAO_CONFIAVEL"]),
            service_context=tuple(payload["servico_em_contexto"]), history=(),
            facts=tuple({"reference": f["referencia"], "data": f["dados"]}
                        for f in payload["fatos_aprovados_somente_conteudo"]),
            scope="SYNTHETIC", prompt_version="v2", objective="simulado", system_instructions=("simulado",),
            output_contract="ai-response-v2",
        )
        proposal = fake.generate(request, timeout_seconds=5).model_dump(mode="json")
        proposal["preface"] = preface
        # Tokens sintéticos proporcionais ao tamanho (≈4 caracteres/token); NÃO são medições reais.
        size = len(http_request.content)
        return httpx2.Response(200, json={
            "id": "msg_avaliacao", "type": "message", "role": "assistant", "model": body["model"],
            "content": [{"type": "text", "text": json.dumps(proposal)}], "stop_reason": "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": size // 4, "output_tokens": 120,
                                             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        })

    client = anthropic.Anthropic(api_key="sk-ant-avaliacao-offline-sem-valor-real-000", max_retries=0,
                                 http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    ledger = SQLiteUsageLedger(repository)  # type: ignore[arg-type]
    return AnthropicProvider(AnthropicConfig(model="claude-opus-5-5", max_requests_per_day=100000,
                                             max_cost_usd_per_day=1000.0), client, ledger), ledger
