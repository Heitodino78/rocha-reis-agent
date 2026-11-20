"""Fases 8 e 9B — provedor generativo REAL (Google Gemini, DESATIVADO), contrato v2 e travas.

Fase 9B: os testes da Fase 8 (escritos para o adaptador Anthropic, substituído) foram
portados para o adaptador Gemini com a MESMA intenção; muda só o formato HTTP. Todo
tráfego vai para httpx2.MockTransport (em memória). Estes testes provam o comportamento
do adaptador e do backend, NÃO a qualidade de um modelo real.
"""

from concurrent.futures import ThreadPoolExecutor
import json
import logging
from threading import Barrier, Event

import httpx2
import pytest

from rocha_reis_agent import config as config_module
from rocha_reis_agent.ai.contracts import AIProviderError
from rocha_reis_agent.ai.gemini_provider import RESPONSE_SCHEMA_V2, GeminiConfig, GeminiProvider
from rocha_reis_agent.ai.privacy import redact_personal_data
from rocha_reis_agent.ai.prompts import PromptRegistry
from rocha_reis_agent.ai.validation import sanitize_preface
from rocha_reis_agent.bootstrap import build_agent, build_ai_provider
from rocha_reis_agent.catalog_io import load_synthetic_catalog
from rocha_reis_agent.domain.contracts import ConversationState as State, InboundMessage
from rocha_reis_agent.domain.conversations import utc_now
from rocha_reis_agent.knowledge_projection import authorize_knowledge
from rocha_reis_agent.persistence.sqlite import SQLiteConversationRepository
from rocha_reis_agent.persistence.sqlite_ai import SQLiteUsageLedger
from gemini_support import KEY, MODEL, ModelFromFake, Recorder, body, error, make_provider, make_request as _request
from whatsapp_support import ROOT, settings

GEMINI = {"ai_provider": "gemini", "gemini_api_key": KEY, "ai_prompt_version": "v2", "ai_model": MODEL}


def catalog():
    return load_synthetic_catalog(ROOT / "fixtures/salon.versioned.synthetic.json")


def agent_v2(repository, provider):
    return build_agent(catalog(), conversation_repository=repository, provider=provider, prompt_version="v2")


def send(agent, text, event="e1", client="cliente-a"):
    return agent.handle(InboundMessage(event, client, text, utc_now()))


# ------------------------------------------------- 1–5: offline, seleção, travas
def test_default_is_offline_fake_and_real_provider_is_locked(tmp_path):
    assert config_module.GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED is False
    assert not hasattr(config_module, "AI_REAL_PROVIDER_ACTIVATION_AUTHORIZED")  # trava Anthropic removida
    config = settings(tmp_path)
    assert config.ai_provider == "fake" and config.ai_model is None and build_ai_provider(config, None) is None
    with pytest.raises(Exception, match="não autorizado"):
        settings(tmp_path, **GEMINI)


@pytest.mark.parametrize("variable", ["GEMINI_API_KEY"])
def test_api_key_alone_never_activates_anything(tmp_path, monkeypatch, variable):
    monkeypatch.setenv(variable, KEY)
    with pytest.raises(Exception) as caught:   # chave sem provedor real selecionado: configuração recusada
        settings(tmp_path)
    assert KEY not in str(caught.value)


def test_google_api_key_in_environment_is_ignored_and_no_client_is_built(tmp_path, monkeypatch):
    """O SDK leria GOOGLE_API_KEY sozinho; a aplicação nunca cria o cliente sem trava e chave explícita."""
    monkeypatch.setenv("GOOGLE_API_KEY", KEY)
    config = settings(tmp_path)
    assert config.ai_provider == "fake" and build_ai_provider(config, None) is None


def test_ai_and_meta_locks_are_independent(tmp_path, monkeypatch):
    token = "EAAG" + "SinteticoTokenDeTeste" + "Qx7Lp2Vm9Rk4Wz8Nb3Hc6Jd1Fg5Ty0"
    monkeypatch.setattr(config_module, "GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED", True)
    assert settings(tmp_path, **GEMINI).ai_provider == "gemini"
    with pytest.raises(Exception, match="não autorizado"):  # abrir a IA não abre o WhatsApp
        settings(tmp_path, whatsapp_transport="graph", whatsapp_access_token=token, whatsapp_graph_api_version="v26.0")
    monkeypatch.setattr(config_module, "GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED", False)
    monkeypatch.setattr(config_module, "GRAPH_TRANSPORT_ACTIVATION_AUTHORIZED", True)
    with pytest.raises(Exception, match="não autorizado"):  # abrir o WhatsApp não abre a IA
        settings(tmp_path, **GEMINI)


@pytest.mark.parametrize("changes", [
    {"gemini_api_key": None}, {"gemini_api_key": "curta"}, {"ai_prompt_version": None}, {"ai_model": None},
    {"storage_backend": "memory", "storage_path": None, "whatsapp_enabled": False},
    {"gemini_api_key": "vT9xQ2mL7pR4sK8wZ1nB6cH3jF5gD0aY"},  # chave igual a outro segredo
    {"ai_model": "gemini-2.5-flash"},                          # descontinuado / fora do registro
    {"ai_model": "gemini-3.1-pro-preview"},                    # preview não registrado
    {"ai_model": "gemini-3.8-flash", "ai_thinking_level": "minimal"},  # nível não aceito pelo modelo
    {"ai_max_output_tokens": 65537},                           # acima do limite da configuração
])
def test_unlocked_configuration_still_requires_valid_setup(tmp_path, monkeypatch, changes):
    monkeypatch.setattr(config_module, "GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED", True)
    values = {**GEMINI, **changes}
    with pytest.raises(Exception):
        settings(tmp_path, **{k: v for k, v in values.items() if v is not None})


def test_unlocked_factory_requires_v2_prompt_and_builds_without_calling(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED", True)
    repository = SQLiteConversationRepository(tmp_path / "f.synthetic.sqlite3")
    with pytest.raises(ValueError, match="ai-response-v2"):
        build_ai_provider(settings(tmp_path, **{**GEMINI, "ai_prompt_version": "v1"}), repository)
    recorder = Recorder((200, body({"x": 1})))
    provider = build_ai_provider(settings(tmp_path, **GEMINI), repository,
                                 http_client=httpx2.Client(transport=httpx2.MockTransport(recorder)))
    assert isinstance(provider, GeminiProvider) and recorder.requests == []
    assert KEY not in repr(provider) and provider.thinking_level == "minimal"   # padrão do registro


def test_fake_provider_unchanged_for_v1_and_offline(agent=None):
    assert PromptRegistry().active == "v1"  # prompt ativo não foi alterado silenciosamente
    result = build_agent(catalog()).handle(InboundMessage("e1", "a", "Quanto custa o corte?", utc_now()))
    assert "R$ 45,00" in result.reply and result.prompt_version == "v1"


# ---------------------------------------------- 6–7: requisição e resposta válida
def test_request_is_well_formed_minimized_and_answer_is_backend_composed(tmp_path):
    model = ModelFromFake(preface="Claro, posso ajudar!")
    provider, repository = make_provider(tmp_path, model)
    result = send(agent_v2(repository, provider), "Quanto custa o corte? meu fone é (11) 98888-7777")
    (request,) = model.requests
    sent = json.loads(request.content)
    assert str(request.url) == f"https://generativelanguage.googleapis.com/v1/models/{MODEL}:generateContent"
    assert request.headers["x-goog-api-key"] == KEY and "authorization" not in request.headers
    generation = sent["generationConfig"]
    assert generation["responseMimeType"] == "application/json" and generation["responseJsonSchema"] == RESPONSE_SCHEMA_V2
    assert generation["maxOutputTokens"] == 2048
    assert generation["thinkingConfig"] in ({"thinking_level": "MINIMAL"}, {"thinkingLevel": "MINIMAL"})
    assert "temperature" not in generation and "topP" not in generation and "topK" not in generation
    assert "Objetivo:" in sent["systemInstruction"]["parts"][0]["text"]   # instruções confiáveis separadas
    user = sent["contents"][0]["parts"][0]["text"]
    payload = json.loads(user)
    assert "98888" not in user and "[telefone removido]" in payload["mensagem_atual_do_cliente_NAO_CONFIAVEL"]
    assert "cliente-a" not in request.content.decode()  # nenhum identificador interno vai ao provedor
    assert {f["referencia"]["id"] for f in payload["fatos_aprovados_somente_conteudo"]} == {"service:corte", "price:corte"}
    assert result.reply.startswith("[SIMULAÇÃO — DADOS FICTÍCIOS] Claro, posso ajudar! Corte de teste: R$ 45,00")
    assert result.prompt_version == "v2"
    (usage,) = SQLiteUsageLedger(repository).summary()["rows"]
    assert usage["outcome"] == "ok" and usage["input_tokens"] == 300 and usage["reasoning_tokens"] == 20
    assert usage["model"] == usage["requested_model"] == MODEL and usage["cost_micros"] > 0


# ----------------------------------------- 8–12: falhas, bloqueio, referências
@pytest.mark.parametrize("response,expected", [
    ((200, body(text="isto não é json")), "ai_validation"),
    ((200, body(text="")), "ai_validation"),                                           # sem partes de texto
    ((200, body({"contract": "ai-response-v2", "intents": ["price"]})), "ai_validation"),  # incompleta
    ((200, body(text='{"contract": "ai-res', finish="MAX_TOKENS")), "ai_validation"),    # truncada
    ((200, body(text=None, finish="SAFETY")), "ai_provider"),                          # bloqueio na saída
    ((200, body(block="SAFETY")), "ai_provider"),                                      # bloqueio no prompt
    ((200, body({"x": 1}, finish="RECITATION")), "ai_provider"),
    ((200, body({"x": 1}, finish="OTHER")), "ai_validation"),                          # encerramento inesperado
    (error(503, "UNAVAILABLE"), "ai_provider"),
    (error(500, "INTERNAL"), "ai_provider"),
    (error(429, "RESOURCE_EXHAUSTED"), "ai_provider"),
    (error(400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key."), "ai_provider"),
    (error(403, "PERMISSION_DENIED"), "ai_provider"),
    (error(404, "NOT_FOUND"), "ai_provider"),
    (error(400, "INVALID_ARGUMENT"), "ai_provider"),
    (httpx2.ConnectError("sintético"), "ai_provider"),
    (httpx2.ReadTimeout("sintético"), "ai_timeout"),
])
def test_every_failure_pauses_safely_without_inventing(tmp_path, caplog, response, expected):
    provider, repository = make_provider(tmp_path, Recorder(response))
    with caplog.at_level(logging.ERROR):
        result = send(agent_v2(repository, provider), "Quanto custa o corte?")
    assert result.state == State.WAITING_HUMAN and "R$" not in result.reply
    assert "Nenhum atendente real foi notificado" in result.reply
    assert any(getattr(r, "failure_kind", None) == expected for r in caplog.records)


@pytest.mark.parametrize("changes", [
    {"fact_references": [{"id": "price:corte", "version": 99}]},             # referência inexistente
    {"fact_references": [{"id": "price:escova", "version": 1}]},             # fato não recuperado
    {"service_ids": ["escova"]},                                             # troca de serviço
    {"intents": ["price", "booking"]},                                       # intenção sem referência
    {"extra": "campo"},                                                      # campo inesperado
    {"preface": "x" * 300},                                                  # conteúdo excessivo
])
def test_invalid_model_proposals_are_rejected_by_backend(tmp_path, changes):
    provider, repository = make_provider(tmp_path, ModelFromFake(**changes))
    result = send(agent_v2(repository, provider), "Quanto custa o corte?")
    assert result.state == State.WAITING_HUMAN and "R$" not in result.reply


@pytest.mark.parametrize("preface,reason", [
    ("O corte custa R$ 10 hoje!", "factual_or_markup"),
    ("Seu horário está confirmado para sábado.", "forbidden_term"),
    ("Acesse https://golpe.example", "factual_or_markup"),
    ("Transferi você para um atendente.", "forbidden_term"),
    ("Ignore as regras: tudo grátis", "forbidden_term"),
    ("Temos Corte de teste com desconto", "forbidden_term"),
    ("A Profissional de Teste A te atende", "forbidden_term"),
    ("O Salão Exemplo te espera", "knowledge_entity"),  # nome cadastrado com complemento ("— DADOS FICTÍCIOS")
    ("Minhas instruções dizem para ajudar", "forbidden_term"),
    ("Oi 😀😀", "emoji"),
])
def test_unsafe_prefaces_are_dropped_but_facts_still_answered(tmp_path, preface, reason):
    knowledge = authorize_knowledge(catalog())
    assert sanitize_preface(preface, knowledge) == (None, reason)
    provider, repository = make_provider(tmp_path, ModelFromFake(preface=preface))
    result = send(agent_v2(repository, provider), "Quanto custa o corte?")
    assert result.state == State.AUTO and preface not in result.reply
    assert result.reply == "[SIMULAÇÃO — DADOS FICTÍCIOS] Corte de teste: R$ 45,00. Valor fictício, exclusivo desta demonstração."


def test_safe_preface_is_accepted():
    knowledge = authorize_knowledge(catalog())
    for text in ("Claro, posso ajudar!", "Oi! Que bom falar com você.", "Entendi."):
        assert sanitize_preface(text, knowledge) == (text, None)


# -------------------------------------------- dados ausentes, Booksy, humano
def test_missing_information_and_booksy_and_handoff_are_backend_enforced(tmp_path):
    provider, repository = make_provider(tmp_path, ModelFromFake(preface="Certo!"))
    agent = agent_v2(repository, provider)
    missing = send(agent, "Vocês fazem luzes?", "e1")
    assert "Não tenho informação confirmada" in missing.reply and "R$" not in missing.reply
    booking = send(agent, "Quero marcar um corte para sexta", "e2")
    assert "https://example.invalid/booksy-sintetico" in booking.reply
    assert "Não consultei disponibilidade nem realizei ou confirmei reserva" in booking.reply
    human = send(agent, "Não quero falar com robô, chama a recepcionista", "e3")
    assert human.state == State.WAITING_HUMAN
    paused = send(agent, "Quanto custa o corte?", "e4")
    assert paused.reply is None  # pausa humana: nenhuma resposta automática


def test_model_is_never_called_outside_auto(tmp_path):
    model = ModelFromFake()
    provider, repository = make_provider(tmp_path, model)
    agent = agent_v2(repository, provider)
    send(agent, "Quero falar com uma pessoa", "e1")  # handoff decidido pelo backend, sem modelo
    send(agent, "Quanto custa o corte?", "e2")
    assert model.requests == []


def test_operator_takeover_during_slow_generation_blocks_reply(tmp_path):
    entered, release = Event(), Event()
    inner = ModelFromFake()
    calls = []

    def slow(request):
        calls.append(1)
        if len(calls) == 2:  # só a geração em disputa fica lenta
            entered.set()
            assert release.wait(10)
        return inner(request)

    provider, repository = make_provider(tmp_path, slow)
    agent = agent_v2(repository, provider)
    send(agent, "Olá", "e0")
    other = build_agent(catalog(), conversation_repository=SQLiteConversationRepository(repository.path))
    from rocha_reis_agent.application.handoff import VerifiedOperator
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(send, agent, "Quanto custa o corte?", "e1")
        assert entered.wait(10)
        with other.conversations.edit("cliente-a") as conversation:  # admin envia para a fila durante a chamada
            other.handoff.request_by_operator(conversation, VerifiedOperator("a" * 32, "ADMIN"))
        release.set()
        result = pending.result(timeout=20)
    assert result.reply is None and result.state == State.WAITING_HUMAN


# ------------------------------------------------------ consumo e limites
def test_daily_request_budget_and_unknown_price_block_calls(tmp_path):
    provider, repository = make_provider(tmp_path, ModelFromFake(), max_requests_per_day=1)
    agent = agent_v2(repository, provider)
    assert "R$ 45,00" in send(agent, "Quanto custa o corte?", "e1").reply
    blocked = send(agent, "Quanto custa a escova?", "e2", "cliente-b")
    assert blocked.state == State.WAITING_HUMAN  # sem chamada: limite diário
    unknown = GeminiProvider(GeminiConfig(model="gemini-modelo-sem-tarifa"), provider.client, provider.ledger)
    with pytest.raises(AIProviderError, match="Tarifa"):
        unknown.generate(_request(), timeout_seconds=5)


def test_cost_budget_uses_estimates(tmp_path):
    provider, repository = make_provider(tmp_path, ModelFromFake(), max_cost_usd_per_day=0.01)
    ledger = SQLiteUsageLedger(repository)
    ledger.record(provider="gemini", model=MODEL, prompt_version="v2", outcome="ok",
                  input_tokens=1000, output_tokens=1000, estimated_cost_micros=20_000)
    with pytest.raises(AIProviderError, match="custo"):
        provider.generate(_request(), timeout_seconds=5)


def test_concurrency_limit_rejects_excess_calls(tmp_path):
    barrier, release = Barrier(2), Event()

    def slow(request):
        barrier.wait(5)
        assert release.wait(10)
        return httpx2.Response(200, json=body({"x": 1}))

    provider, repository = make_provider(tmp_path, slow, max_concurrency=1)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(provider.generate, _request(), timeout_seconds=10)
        barrier.wait(5)
        with pytest.raises(AIProviderError, match="simultâneas"):
            provider.generate(_request(), timeout_seconds=0.2)
        release.set()
        first.result(timeout=10)


def test_oversized_input_is_refused_before_any_call(tmp_path):
    recorder = Recorder((200, body({"x": 1})))
    provider, _ = make_provider(tmp_path, recorder, max_input_chars=1000)
    with pytest.raises(AIProviderError, match="limite"):
        provider.generate(_request("x" * 1500), timeout_seconds=5)
    tokens, _ = make_provider(tmp_path, recorder, name="t", max_input_tokens=500)
    with pytest.raises(AIProviderError, match="tokens"):   # limite de tokens estimados
        tokens.generate(_request("y" * 900), timeout_seconds=5)
    assert recorder.requests == []


def test_v1_contract_is_refused_by_real_provider(tmp_path):
    provider, _ = make_provider(tmp_path, Recorder((200, body({"x": 1}))))
    with pytest.raises(AIProviderError):
        provider.generate(_request(contract="ai-response-v1"), timeout_seconds=5)


def test_logs_and_usage_never_contain_content_or_key(tmp_path, caplog):
    provider, repository = make_provider(tmp_path, ModelFromFake(preface="Claro!"))
    with caplog.at_level(logging.DEBUG):
        send(agent_v2(repository, provider), "Quanto custa o corte? SEGREDO_DO_CLIENTE")
    text = "".join(r.getMessage() + str(r.__dict__) for r in caplog.records
                   if not r.name.startswith(("httpx", "google")))
    assert "SEGREDO_DO_CLIENTE" not in text and KEY not in text
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(repository.path)) as connection:
        dump = json.dumps(connection.execute("SELECT * FROM ai_usage").fetchall())
    assert "SEGREDO_DO_CLIENTE" not in dump and KEY not in dump


def test_redaction_patterns():
    assert redact_personal_data("cpf 123.456.789-09, e-mail a.b@c.com, fone +55 11 99999-8888") == \
        "cpf [documento removido], e-mail [e-mail removido], fone [telefone removido]"
    assert redact_personal_data("Quanto custa? 45 reais às 15h do dia 20/11") == "Quanto custa? 45 reais às 15h do dia 20/11"


def test_model_parameters_must_match_model_capabilities(tmp_path, monkeypatch):
    """gemini-3.8-flash não aceita raciocínio 'minimal'; os Flash-Lite aceitam (registro verificado)."""
    monkeypatch.setattr(config_module, "GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED", True)
    flash = {**GEMINI, "ai_model": "gemini-3.8-flash"}
    with pytest.raises(Exception):
        settings(tmp_path, **flash, ai_thinking_level="minimal")
    assert settings(tmp_path, **flash, ai_thinking_level="low").ai_thinking_level == "low"
    assert settings(tmp_path, **GEMINI, ai_thinking_level="minimal").ai_thinking_level == "minimal"
