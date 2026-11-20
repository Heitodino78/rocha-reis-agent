"""Apoio aos testes do adaptador Gemini: respostas REST sintéticas sobre httpx2.MockTransport.

Nenhuma conexão é aberta: o cliente oficial `google-genai` recebe um httpx2.Client com
transporte em memória. Os corpos seguem o formato REST de generateContent (camelCase).
"""

import json

import httpx2

from rocha_reis_agent.ai.contracts import AIRequest, UntrustedText
from rocha_reis_agent.ai.fake import FakeAIProvider
from rocha_reis_agent.ai.gemini_provider import GeminiConfig, GeminiProvider, create_client
from rocha_reis_agent.evaluation.providers import gemini_payload, gemini_response
from rocha_reis_agent.persistence.sqlite import SQLiteConversationRepository
from rocha_reis_agent.persistence.sqlite_ai import SQLiteUsageLedger

KEY = "AIza" + "SinteticaDeTesteGemini" + "Qx7Lp2Vm9Rk4Wz8Nb"
MODEL = "gemini-3.1-flash-lite"


def body(payload=None, *, text=None, finish="STOP", usage=(300, 80, 20), model=MODEL, cached=0, block=None):
    content = text if text is not None else (json.dumps(payload) if payload is not None else None)
    return gemini_response(content, finish=finish, model=model, prompt_tokens=usage[0], output_tokens=usage[1],
                           thoughts=usage[2], cached=cached, block_reason=block)


def error(code, status, message="simulado"):
    return code, {"error": {"code": code, "message": message, "status": status}}


class Recorder:
    """Responde em sequência: (status, corpo), exceção ou função(request)."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item(request)
        status, payload = item
        return httpx2.Response(status, json=payload)


def ai_request_from(http_request, prompt_version="v2"):
    payload = gemini_payload(http_request)
    return AIRequest(
        message=UntrustedText(text=payload["mensagem_atual_do_cliente_NAO_CONFIAVEL"]),
        service_context=tuple(payload["servico_em_contexto"]),
        history=tuple(UntrustedText(text=t) for t in payload["historico_relacionado_do_cliente_NAO_CONFIAVEL"]),
        facts=tuple({"reference": f["referencia"], "data": f["dados"]} for f in payload["fatos_aprovados_somente_conteudo"]),
        scope="SYNTHETIC", prompt_version=prompt_version, objective="x", system_instructions=("x",),
        output_contract="ai-response-v2",
    )


class ModelFromFake:
    """'Modelo' que responde com JSON v2 derivado do pedido recebido (+ alterações do teste)."""

    def __init__(self, model=MODEL, **changes):
        self.changes = changes
        self.model = model
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        data = FakeAIProvider().generate(ai_request_from(request), timeout_seconds=5).model_dump(mode="json")
        data.update(self.changes)
        return httpx2.Response(200, json=body(data, model=self.model))


def make_provider(tmp_path, handler, name="ai", run_budget=None, **config):
    repository = SQLiteConversationRepository(tmp_path / f"{name}.synthetic.sqlite3")
    client = create_client(KEY, http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    values = {"model": MODEL, "max_retries": 0}
    values.update(config)
    provider = GeminiProvider(GeminiConfig(**values), client, SQLiteUsageLedger(repository), run_budget=run_budget)
    return provider, repository


def make_request(text="Quanto custa o corte?", contract="ai-response-v2"):
    return AIRequest(message=UntrustedText(text=text), service_context=(), history=(), facts=(), scope="SYNTHETIC",
                     prompt_version="v2", objective="x", system_instructions=("x",), output_contract=contract)
