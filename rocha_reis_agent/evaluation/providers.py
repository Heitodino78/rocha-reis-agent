"""Provedores usados SOMENTE na avaliação offline.

- `MutatingProvider`: simula um modelo que erra ou é manipulado (saídas inválidas,
  referências inventadas, preços na abertura, promessas, vazamento de instruções).
  Mede se o BACKEND contém o dano; não representa a taxa de erro de um modelo real.
- `simulated_gemini`: o adaptador real `GeminiProvider` (SDK google-genai) sobre
  `httpx2.MockTransport` (nenhuma conexão), exercitando requisição, minimização, saída
  estruturada, parsing, erros e registro de consumo. O "modelo" por trás é o fake.
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


HTTP_MODES = {
    # modo → (status HTTP, status gRPC, mensagem) — formato de erro da Gemini API
    "http_rate_limited": (429, "RESOURCE_EXHAUSTED", "simulado"),
    "http_auth_error": (400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key."),
    "http_server_error": (503, "UNAVAILABLE", "simulado"),
    "http_model_not_found": (404, "NOT_FOUND", "simulado"),
}
SIMULATION_KEY = "chave-sintetica-da-avaliacao-offline-sem-valor-0000"


def gemini_payload(http_request: "httpx2.Request") -> dict[str, Any]:  # type: ignore[name-defined]
    """Dados que o adaptador enviou (conteúdo do usuário em generateContent)."""
    body = json.loads(http_request.content)
    return json.loads(body["contents"][0]["parts"][0]["text"])


def gemini_response(text: str | None, *, finish: str = "STOP", model: str, prompt_tokens: int = 300,
                    output_tokens: int = 120, thoughts: int = 40, cached: int = 0,
                    block_reason: str | None = None) -> dict[str, Any]:
    """Corpo REST de generateContent (camelCase) com consumo SINTÉTICO."""
    body: dict[str, Any] = {"modelVersion": model, "usageMetadata": {
        "promptTokenCount": prompt_tokens, "candidatesTokenCount": output_tokens, "thoughtsTokenCount": thoughts,
        "cachedContentTokenCount": cached, "totalTokenCount": prompt_tokens + output_tokens + thoughts}}
    if block_reason:
        body["promptFeedback"] = {"blockReason": block_reason}
        return body
    parts = [{"text": text}] if text else []
    body["candidates"] = [{"content": {"role": "model", "parts": parts}, "finishReason": finish}]
    return body


def simulated_gemini(repository: object, *, preface: str | None = "Claro, posso ajudar!",  # type: ignore[no-untyped-def]
                     model: str = "gemini-3.1-flash-lite", http_mode: str | None = None, **config: object):
    """GeminiProvider real com transporte HTTP em memória. Tokens de uso são SINTÉTICOS.

    `http_mode` simula respostas reais da API (429, chave inválida, 503, 404, truncamento,
    bloqueio de segurança, JSON inválido, modelo efetivo diferente do pedido).
    """
    import httpx2

    from rocha_reis_agent.ai.gemini_provider import GeminiConfig, GeminiProvider, create_client
    from rocha_reis_agent.persistence.sqlite_ai import SQLiteUsageLedger

    fake = FakeAIProvider()

    def handler(http_request: httpx2.Request) -> httpx2.Response:
        if http_mode in HTTP_MODES:
            status, grpc, message = HTTP_MODES[http_mode]
            return httpx2.Response(status, json={"error": {"code": status, "message": message, "status": grpc}})
        requested = http_request.url.path.rsplit("/", 1)[-1].split(":")[0]
        payload = gemini_payload(http_request)
        request = AIRequest(
            message=UntrustedText(text=payload["mensagem_atual_do_cliente_NAO_CONFIAVEL"]),
            service_context=tuple(payload["servico_em_contexto"]),
            history=tuple(UntrustedText(text=t) for t in payload["historico_relacionado_do_cliente_NAO_CONFIAVEL"]),
            facts=tuple({"reference": f["referencia"], "data": f["dados"]}
                        for f in payload["fatos_aprovados_somente_conteudo"]),
            scope="SYNTHETIC", prompt_version="v2", objective="simulado", system_instructions=("simulado",),
            output_contract="ai-response-v2",
        )
        proposal = fake.generate(request, timeout_seconds=5).model_dump(mode="json")
        proposal["preface"] = preface
        text, finish, served = json.dumps(proposal), "STOP", requested
        # Tokens sintéticos proporcionais ao tamanho (≈4 caracteres/token); NÃO são medições reais.
        size = len(http_request.content) // 4
        if http_mode == "http_truncated":
            text, finish = text[: len(text) // 2], "MAX_TOKENS"
        elif http_mode == "http_refusal":
            text, finish = None, "SAFETY"
        elif http_mode == "http_invalid_json":
            text = text[:-1]  # encerramento normal, mas JSON corrompido
        elif http_mode == "http_unexpected_model":
            served = "gemini-2.5-flash"  # modelo efetivo diferente do solicitado
        return httpx2.Response(200, json=gemini_response(text, finish=finish, model=served, prompt_tokens=size))

    client = create_client(SIMULATION_KEY, http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    ledger = SQLiteUsageLedger(repository)  # type: ignore[arg-type]
    settings: dict[str, object] = {"max_requests_per_day": 100000, "max_cost_usd_per_day": 1000.0, "max_retries": 0}
    settings.update(config)
    run_budget = settings.pop("run_budget", None)
    return GeminiProvider(GeminiConfig(model=model, **settings), client, ledger, run_budget=run_budget), ledger  # type: ignore[arg-type]
