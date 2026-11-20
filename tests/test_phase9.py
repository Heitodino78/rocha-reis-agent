"""Fases 9A/9B — homologação OFFLINE da IA generativa (Google Gemini desde a 9B).

Cobre: registro verificado de modelos Gemini, adaptador `GeminiProvider` (erros HTTP,
bloqueio de segurança, truncamento, timeout com prazo total, consumo com raciocínio e
cache, modelo efetivo inesperado, incerteza de custo), orçamento por execução e por
modelo, guarda da Fase 9B, correções de qualidade do detector, avaliação reproduzível
e corridas com o núcleo. Os testes Anthropic da 9A foram portados com a mesma intenção.
Todo tráfego HTTP vai para httpx2.MockTransport; conexões de rede são bloqueadas neste
módulo. Nada aqui valida a qualidade de um modelo generativo real.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket

import httpx2
import pytest

from rocha_reis_agent import config as config_module
from rocha_reis_agent.ai import model_registry
from rocha_reis_agent.ai.budget import UNCERTAIN_OUTCOMES, BudgetExceeded, RunBudget
from rocha_reis_agent.ai.contracts import AIProviderError, AIProviderTimeout, AIRequest, AIValidationError, UntrustedText
from rocha_reis_agent.ai.fake import FakeAIProvider
from rocha_reis_agent.ai.model_registry import ModelNotSupported, load_registry, require_model
from rocha_reis_agent.ai.prompts import PromptRegistry
from rocha_reis_agent.bootstrap import build_agent
from rocha_reis_agent.catalog_io import load_synthetic_catalog
from rocha_reis_agent.domain.contracts import (
    ConversationState as State, InboundMessage, Intent, SimulationError,
)
from rocha_reis_agent.domain.conversations import utc_now
from rocha_reis_agent.evaluation import real_run
from rocha_reis_agent.evaluation.__main__ import compare, main
from rocha_reis_agent.evaluation.oracle import SafetyOracle
from rocha_reis_agent.evaluation.providers import simulated_gemini
from rocha_reis_agent.evaluation.real_run import CONFIRMATION, RealRunRefused, check_real_run
from rocha_reis_agent.evaluation.runner import DEFAULT_CATALOG, DEFAULT_SCENARIOS, evaluate
from rocha_reis_agent.evaluation.scenarios import ScenarioSet, load_scenarios
from rocha_reis_agent.intents import RuleIntentDetector, correct_spelling
from rocha_reis_agent.knowledge_projection import authorize_knowledge
from rocha_reis_agent.persistence.sqlite import SQLiteConversationRepository
from gemini_support import KEY, MODEL, ai_request_from, body as gemini_body, error, make_provider
from whatsapp_support import settings

GEMINI = {"ai_provider": "gemini", "gemini_api_key": KEY, "ai_prompt_version": "v3", "ai_model": MODEL}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Nenhuma conexão externa (sockets reais recusados)."""
    def refuse(*args, **kwargs):
        raise AssertionError("Tentativa de conexão de rede na Fase 9.")
    monkeypatch.setattr(socket.socket, "connect", refuse)


def catalog():
    return load_synthetic_catalog(DEFAULT_CATALOG)


def base():
    return authorize_knowledge(catalog()).base


def repo(tmp_path, name="f9"):
    return SQLiteConversationRepository(tmp_path / f"{name}.synthetic.sqlite3")


def send(agent, text, event="e1", client="c1"):
    return agent.handle(InboundMessage(event, client, text, utc_now()))


def _request(text="Quanto custa o corte?"):
    return AIRequest(message=UntrustedText(text=text), service_context=(), history=(), facts=(), scope="SYNTHETIC",
                     prompt_version="v3", objective="x", system_instructions=("x",), output_contract="ai-response-v2")


def provider_with(tmp_path, handler, name="f9", **config):
    from rocha_reis_agent.persistence.sqlite_ai import SQLiteUsageLedger
    provider, repository = make_provider(tmp_path, handler, name=name, **config)
    return provider, SQLiteUsageLedger(repository), repository


def ok(request=None, **kwargs):
    return httpx2.Response(200, json=gemini_body({"x": 1}, **kwargs))


def rows(ledger):
    return ledger.summary(days=1)["rows"]


# ------------------------------------------------ 1–6: travas, credenciais, modelos
def test_locks_remain_closed_and_real_provider_is_refused(tmp_path):
    assert config_module.GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED is False
    assert config_module.GRAPH_TRANSPORT_ACTIVATION_AUTHORIZED is False          # Meta real bloqueada
    assert config_module.HOMOLOGATION_TRANSPORT_ACTIVATION_AUTHORIZED is False
    with pytest.raises(Exception):
        settings(tmp_path, **GEMINI)


@pytest.mark.parametrize("changes", [
    {"gemini_api_key": None},                                # sem credencial
    {"ai_model": "gemini-modelo-inexistente-9"},             # fora do registro verificado
    {"ai_model": "gemini-3.8-flash", "ai_thinking_level": "minimal"},  # nível não aceito
    {"ai_prompt_version": None},                             # versão de prompt implícita
])
def test_real_configuration_is_validated_even_with_lock_open(tmp_path, monkeypatch, changes):
    monkeypatch.setattr(config_module, "GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED", True)
    values = {**GEMINI, **changes}
    with pytest.raises(Exception):
        settings(tmp_path, **{k: v for k, v in values.items() if v is not None})


def test_registry_matches_verified_documentation():
    registry = load_registry()
    assert registry.provider == "gemini" and registry.tier == "paid_standard"
    assert set(registry.models) == {"gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-3.8-flash"}
    lite31, lite35, flash = (registry.models[m] for m in ("gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-3.8-flash"))
    assert (lite31.pricing.input, lite31.pricing.output, lite31.pricing.cache_read) == (0.25, 1.50, 0.025)
    assert (lite35.pricing.input, lite35.pricing.output, lite35.pricing.cache_read) == (0.30, 2.50, 0.03)
    assert registry.rates("gemini-3.8-flash", date(2026, 12, 31)).output == 3.75
    assert registry.rates("gemini-3.8-flash", date(2027, 1, 1)).output == 7.50   # reajuste publicado
    assert all(m.lifecycle == "stable" and m.structured_outputs and m.input_token_limit == 1_048_576
               and m.output_token_limit == 65_536 for m in registry.models.values())
    assert "minimal" not in flash.thinking_levels and flash.default_thinking_level == "medium"
    assert lite31.default_thinking_level == lite35.default_thinking_level == "minimal"
    assert not any(m.startswith("claude") for m in registry.models)               # Anthropic fora do registro


def test_model_without_structured_output_or_not_stable_is_rejected(monkeypatch):
    registry = load_registry()
    broken = registry.model_copy(update={"models": {
        **registry.models,
        MODEL: registry.models[MODEL].model_copy(update={"structured_outputs": False}),
        "gemini-3.8-flash": registry.models["gemini-3.8-flash"].model_copy(update={"lifecycle": "preview"})}})
    monkeypatch.setattr(model_registry, "load_registry", lambda path=None: broken)
    with pytest.raises(ModelNotSupported, match="estruturada"):
        require_model(MODEL)
    with pytest.raises(ModelNotSupported, match="estáveis"):
        require_model("gemini-3.8-flash")
    with pytest.raises(ModelNotSupported):
        require_model("gemini-3.1-flash-lite-preview")   # ID descontinuado/inventado


# ------------------------------------------------ 7–19: adaptador sobre HTTP simulado
@pytest.mark.parametrize("code,status,message,outcome", [
    (429, "RESOURCE_EXHAUSTED", "x", "rate_limited"), (401, "UNAUTHENTICATED", "x", "auth_error"),
    (400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key.", "auth_error"),
    (404, "NOT_FOUND", "x", "model_not_found"), (403, "PERMISSION_DENIED", "x", "permission_error"),
    (400, "INVALID_ARGUMENT", "x", "bad_request"),
])
def test_rejected_requests_are_mapped_and_cost_nothing(tmp_path, code, status, message, outcome):
    status_code, payload = error(code, status, message)
    provider, ledger, _ = provider_with(tmp_path, lambda r: httpx2.Response(status_code, json=payload))
    with pytest.raises(AIProviderError) as raised:
        provider.generate(_request(), timeout_seconds=5)
    assert KEY not in str(raised.value)
    (row,) = rows(ledger)
    assert row["outcome"] == outcome and row["cost_micros"] == 0


@pytest.mark.parametrize("error_type,outcome,expected", [
    (httpx2.ReadTimeout, "timeout", AIProviderTimeout), (httpx2.ConnectError, "network_error", AIProviderError),
])
def test_uncertain_failures_record_worst_case_cost(tmp_path, error_type, outcome, expected):
    def handler(request):
        raise error_type("simulado", request=request)
    provider, ledger, _ = provider_with(tmp_path, handler)
    with pytest.raises(expected):
        provider.generate(_request(), timeout_seconds=5)
    (row,) = rows(ledger)
    assert outcome in UNCERTAIN_OUTCOMES and row["outcome"] == outcome
    assert row["cost_micros"] > 0 and provider.run_cost_micros == row["cost_micros"]   # incerteza ≠ zero


def test_server_error_is_uncertain_and_never_retried_forever(tmp_path):
    calls = []

    def handler(request):
        calls.append(1)
        status_code, payload = error(503, "UNAVAILABLE")
        return httpx2.Response(status_code, json=payload)
    provider, ledger, _ = provider_with(tmp_path, handler, max_retries=1)
    with pytest.raises(AIProviderError):
        provider.generate(_request(), timeout_seconds=0.5)
    assert len(calls) == 2 and rows(ledger)[0]["outcome"] == "provider_unavailable"   # 1 + 1 retentativa


def test_total_deadline_is_split_between_sdk_attempts(tmp_path):
    seen = []

    def handler(request):
        seen.append(request.extensions["timeout"]["read"])
        return ok()
    provider, _, _ = provider_with(tmp_path, handler, max_retries=1)
    provider.generate(_request(), timeout_seconds=4)
    assert seen == [2.0]   # prazo total 4 s / 2 tentativas possíveis (timeout em ms no SDK)


@pytest.mark.parametrize("kwargs,error_type,outcome", [
    ({"text": '{"contract": "ai-res', "finish": "MAX_TOKENS"}, AIValidationError, "truncated"),
    ({"text": None, "finish": "SAFETY"}, AIProviderError, "blocked_output"),
    ({"text": '{"x":1}', "finish": "PROHIBITED_CONTENT"}, AIProviderError, "blocked_output"),
    ({"block": "SAFETY"}, AIProviderError, "blocked_prompt"),
    ({"text": '{"x":1}', "finish": "MALFORMED_FUNCTION_CALL"}, AIValidationError, "unexpected_finish"),
    ({"text": "   "}, AIValidationError, "ok"),
])
def test_finish_and_block_reasons_are_checked_before_content(tmp_path, kwargs, error_type, outcome):
    provider, ledger, _ = provider_with(tmp_path, lambda r: httpx2.Response(200, json=gemini_body(**kwargs)))
    with pytest.raises(error_type):
        provider.generate(_request(), timeout_seconds=5)
    (row,) = rows(ledger)
    assert row["outcome"] == outcome and row["cost_micros"] > 0   # houve processamento: consumo contabilizado


def test_thoughts_are_billed_as_output_and_cache_at_cache_rate(tmp_path):
    provider, ledger, _ = provider_with(
        tmp_path, lambda r: httpx2.Response(200, json=gemini_body({"x": 1}, usage=(1000, 100, 300), cached=400)))
    provider.generate(_request(), timeout_seconds=5)
    (row,) = rows(ledger)
    # 3.1 Flash-Lite: entrada 0,25; cache 0,025; saída (incl. raciocínio) 1,50 por MTok → micro-dólares
    assert (row["input_tokens"], row["cache_read_tokens"], row["output_tokens"], row["reasoning_tokens"]) == (600, 400, 100, 300)
    assert row["cost_micros"] == round(600 * 0.25 + 400 * 0.025 + 400 * 1.50)


def test_unexpected_effective_model_fails_safe_with_worst_case_cost(tmp_path):
    provider, ledger, _ = provider_with(tmp_path, lambda r: ok(model="gemini-2.5-flash"))
    with pytest.raises(AIValidationError, match="diferente"):
        provider.generate(_request(), timeout_seconds=5)
    (row,) = rows(ledger)
    assert row["model"] == "gemini-2.5-flash" and row["requested_model"] == MODEL
    assert row["outcome"] == "unexpected_model" and row["cost_micros"] > 0
    versioned, ledger2, _ = provider_with(tmp_path, lambda r: ok(model=MODEL + "-001"), name="v")
    versioned.generate(_request(), timeout_seconds=5)              # sufixo de versão do mesmo modelo é aceito
    assert rows(ledger2)[0]["outcome"] == "ok"


def test_missing_usage_records_worst_case(tmp_path):
    def handler(request):
        payload = gemini_body({"x": 1})
        payload.pop("usageMetadata")
        return httpx2.Response(200, json=payload)
    provider, ledger, _ = provider_with(tmp_path, handler)
    provider.generate(_request(), timeout_seconds=5)
    assert rows(ledger)[0]["outcome"] == "usage_missing" and rows(ledger)[0]["cost_micros"] > 0


def test_run_budget_stops_before_sending_and_is_shared(tmp_path):
    calls = []

    def handler(request):
        calls.append(1)
        return ok()
    shared = RunBudget(max_requests=2)
    first, _, _ = provider_with(tmp_path, handler, run_budget=shared)
    second, _, _ = provider_with(tmp_path, handler, name="b", run_budget=shared)
    first.generate(_request(), timeout_seconds=5)
    second.generate(_request(), timeout_seconds=5)
    with pytest.raises(BudgetExceeded, match="execução"):
        first.generate(_request(), timeout_seconds=5)
    assert len(calls) == 2
    tiny, _, _ = provider_with(tmp_path, handler, name="c", max_cost_usd_per_run=0.000001)
    with pytest.raises(BudgetExceeded, match="custo"):
        tiny.generate(_request(), timeout_seconds=5)
    per_model, _, _ = provider_with(tmp_path, handler, name="m",
                                    run_budget=RunBudget(per_model_max_requests={MODEL: 1}))
    per_model.generate(_request(), timeout_seconds=5)
    with pytest.raises(BudgetExceeded, match="modelo"):
        per_model.generate(_request(), timeout_seconds=5)
    assert len(calls) == 3


def test_worst_case_reservation_covers_retries(tmp_path):
    provider, _, _ = provider_with(tmp_path, ok, max_retries=1, max_output_tokens=1000)
    single, _, _ = provider_with(tmp_path, ok, name="s", max_retries=0, max_output_tokens=1000)
    system, text = provider.build_messages(_request())
    assert provider.worst_case_micros(system, text) >= 2 * single.worst_case_micros(system, text) - 1


def test_usage_ledger_has_no_content_or_key(tmp_path):
    secret_text = "Meu CPF é 123.456.789-09, quanto custa o corte?"
    provider, ledger, repository = provider_with(tmp_path, ok)
    provider.generate(_request(secret_text), timeout_seconds=5)
    with repository.read() as connection:
        dump = json.dumps([tuple(r) for r in connection.execute("SELECT * FROM ai_usage").fetchall()])
    assert "CPF" not in dump and "123.456" not in dump and KEY not in dump and "corte" not in dump


# ------------------------------------------------ 16–29: qualidade e contexto
@pytest.mark.parametrize("text,corrected", [
    ("idratacao", "hidratacao"), ("atendemte", "atendente"), ("agendamneto", "agendamento"),
    ("marcas", "marcas"), ("atendimento", "atendimento"), ("pessoal", "pessoal"), ("colocacao", "colocacao"),
])
def test_spelling_correction_is_bounded_to_the_closed_vocabulary(text, corrected):
    assert correct_spelling(text, frozenset({"hidratacao", "coloracao"})) == corrected or (
        text == "colocacao" and correct_spelling(text, frozenset({"hidratacao"})) == "colocacao")


def test_corrected_service_is_disclosed_to_the_client(tmp_path):
    reply = send(build_agent(catalog(), conversation_repository=repo(tmp_path)), "quanto custa a idratação?").reply
    assert "Entendi que você se refere a: Hidratação fictícia." in reply and "R$ 80,00" in reply


@pytest.mark.parametrize("text,intent", [
    ("Vocês aceitam pix?", Intent.POLICY), ("Aceita cartão de crédito?", Intent.POLICY),
    ("Tem desconto?", Intent.PROMOTION), ("Tem cupom de primeira compra?", Intent.PROMOTION),
    ("Qual o horário de atendimento?", Intent.HOURS), ("Quero falar com o gerente", Intent.HUMAN),
    ("Eu já reservei, confirma?", Intent.BOOKING),
])
def test_new_vocabulary(text, intent):
    assert intent in RuleIntentDetector().detect(text, base()).intents


def test_elliptical_question_inherits_price_only_with_fresh_context(tmp_path):
    agent = build_agent(catalog(), conversation_repository=repo(tmp_path))
    assert "R$ 45,00" in send(agent, "Quanto custa o corte?", "e1").reply
    assert "R$ 80,00" in send(agent, "e a hidratação?", "e2").reply           # herda preço
    assert "R$" not in send(agent, "e a escova?", "e3").reply                  # sem preço aprovado
    other = build_agent(catalog(), conversation_repository=repo(tmp_path, "g"))
    send(other, "Vocês fazem corte?", "e1")
    assert "R$" not in send(other, "e a hidratação?", "e2").reply             # pergunta anterior não era de preço


def test_no_inheritance_when_history_cannot_be_sent(tmp_path):              # item 16
    agent = build_agent(catalog(), conversation_repository=repo(tmp_path), ai_history_limit=0)
    send(agent, "Quanto custa o corte?", "e1")
    assert "R$" not in send(agent, "e a hidratação?", "e2").reply


def test_inherited_intent_reaches_the_model_only_as_untrusted_history(tmp_path):
    seen = []
    provider, ledger = simulated_gemini(repo(tmp_path))
    real_generate = provider.generate

    def spy(request, *, timeout_seconds):
        seen.append(request)
        return real_generate(request, timeout_seconds=timeout_seconds)
    provider.generate = spy  # type: ignore[method-assign]
    agent = build_agent(catalog(), conversation_repository=repo(tmp_path), provider=provider, prompt_version="v3")
    send(agent, "Quanto custa o corte?", "e1")
    assert "R$ 80,00" in send(agent, "e a hidratação?", "e2").reply
    assert [h.text for h in seen[-1].history] == ["Quanto custa o corte?"] and seen[-1].history[0].trust == "untrusted-client"


def test_model_dropping_inherited_price_is_rejected(tmp_path):
    def handler(request):
        from rocha_reis_agent.evaluation.providers import gemini_payload
        facts = gemini_payload(request)["fatos_aprovados_somente_conteudo"]
        service = [f["referencia"] for f in facts if f["dados"]["kind"] == "service"]
        return httpx2.Response(200, json=gemini_body({
            "contract": "ai-response-v2", "intents": ["services"], "service_ids": ["hidratacao"], "use_context": False,
            "catalog_requested": False, "faq_reference": None, "fact_references": service,
            "needs_clarification": False, "human_handoff_suggested": False, "information_missing": False,
            "preface": None}))
    repository = repo(tmp_path)
    provider, _, _ = provider_with(tmp_path, handler)
    agent = build_agent(catalog(), conversation_repository=repository, provider=provider, prompt_version="v3")
    send(agent, "Quanto custa o corte?", "e1")
    assert send(agent, "e a hidratação?", "e2").state == State.WAITING_HUMAN   # proposta divergente → falha segura


def test_validated_model_suggestion_escalates_indirect_request(tmp_path):    # item 28
    def handler(request):
        return httpx2.Response(200, json=gemini_body({
            "contract": "ai-response-v2", "intents": ["human"], "service_ids": [], "use_context": False,
            "catalog_requested": False, "faq_reference": None, "fact_references": [], "needs_clarification": False,
            "human_handoff_suggested": True, "information_missing": False, "preface": None}))
    provider, _, repository = provider_with(tmp_path, handler)
    agent = build_agent(catalog(), conversation_repository=repository, provider=provider, prompt_version="v3")
    result = send(agent, "Tem alguém aí para me atender de verdade?")
    assert result.state == State.WAITING_HUMAN and "Nenhum atendente real foi notificado" in result.reply


@pytest.mark.parametrize("text,expected", [
    ("Vocês têm promoção amanhã?", "Não tenho informação confirmada sobre promoções"),   # 22
    ("Quanto custa uma coloração?", "Não tenho preço confirmado"),                       # 24
    ("Vocês fazem progressiva?", "Não tenho informação confirmada sobre esse serviço"),  # 25/26
])
def test_missing_data_is_declared_never_invented(tmp_path, text, expected):
    reply = send(build_agent(catalog(), conversation_repository=repo(tmp_path)), text).reply
    assert expected in reply and not SafetyOracle(authorize_knowledge(catalog())).check(reply)


def test_pix_without_approved_policy(tmp_path):                              # item 23
    from rocha_reis_agent.evaluation.runner import catalog_variant
    agent = build_agent(catalog_variant(catalog(), "no_policy"), conversation_repository=repo(tmp_path))
    reply = send(agent, "Vocês aceitam pix?").reply
    assert "Não tenho informação confirmada sobre políticas" in reply and "pix" not in reply.lower()


# ------------------------------------------------ 36–39: corridas e integração
def test_conversation_closed_during_generation_produces_no_reply(tmp_path):  # Missão I, item 4
    repository = repo(tmp_path)
    agent = build_agent(catalog(), conversation_repository=repository)
    send(agent, "Oi", "e0")
    calls = []

    class ClosingProvider(FakeAIProvider):
        def generate(self, request, *, timeout_seconds):
            calls.append(1)
            if len(calls) == 1:
                other = build_agent(catalog(), conversation_repository=SQLiteConversationRepository(repository.path))
                other.conversations.close("c1")
            return super().generate(request, timeout_seconds=timeout_seconds)
    racing = build_agent(catalog(), conversation_repository=repository, provider=ClosingProvider())
    with pytest.raises(SimulationError, match="encerrada"):
        send(racing, "Quanto custa o corte?", "e1")
    with repository.read() as connection:
        texts = [r[0] for r in connection.execute("SELECT content FROM messages WHERE event_id='e1'").fetchall()]
    assert texts == [] and len(calls) == 1   # nada da tentativa foi confirmado; o modelo não foi chamado de novo


def test_two_conversations_in_parallel_through_the_adapter(tmp_path):       # Missão I, item 8
    repository = repo(tmp_path)
    provider, _ = simulated_gemini(repository)
    agent = build_agent(catalog(), conversation_repository=repository, provider=provider, prompt_version="v3")
    send(agent, "Vocês fazem hidratação?", "e1", "c1")
    send(agent, "Vocês fazem corte?", "e1", "c2")
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(send, agent, "E quanto custa?", "e2", "c1")
        b = pool.submit(send, agent, "E quanto custa?", "e2", "c2")
        first, second = a.result(timeout=30), b.result(timeout=30)
    assert "R$ 80,00" in first.reply and "R$ 45" not in first.reply
    assert "R$ 45,00" in second.reply and "R$ 80" not in second.reply


def test_duplicate_event_does_not_call_the_model_twice(tmp_path):           # item 38
    repository = repo(tmp_path)
    provider, ledger = simulated_gemini(repository)
    agent = build_agent(catalog(), conversation_repository=repository, provider=provider, prompt_version="v3")
    send(agent, "Quanto custa o corte?", "e1")
    assert send(agent, "Quanto custa o corte?", "e1").duplicate
    assert sum(r["n"] for r in rows(ledger)) == 1


# ------------------------------------------------ 40–42: prompts e avaliação
def test_prompt_versions_are_preserved_and_new_one_is_inactive():
    registry = PromptRegistry()
    assert registry.active == "v1" and registry.versions == ("v1", "v2", "v3", "v4")
    v3 = registry.get("v3")
    assert v3.output_contract == "ai-response-v2" and any("promotion" in i for i in v3.instructions)
    oracle = SafetyOracle(authorize_knowledge(catalog()))
    assert oracle.check(v3.instructions[1])     # vazamento do v3 também é detectado


def _subset(ids):
    full = load_scenarios(DEFAULT_SCENARIOS)
    return ScenarioSet(**{**full.model_dump(exclude={"scenarios"}), "scenarios": [s for s in full.scenarios if s.id in ids]})


def test_report_has_reproducibility_metadata_denominators_and_criteria():
    report = evaluate(_subset({"C1", "D2", "H1", "H10", "Q3", "W2"}), provider="gemini-simulated",
                      prompt_version="v4", model="gemini-3.5-flash-lite")
    run, metrics = report["run"], report["metrics"]
    assert run["suite_id"] == "rocha-reis-synthetic" and run["suite_version"] == "2"
    assert run["model"] == "gemini-3.5-flash-lite" and run["output_contract"] == "ai-response-v2"
    assert run["catalog_sha256_16"] is None or len(run["catalog_sha256_16"]) == 16
    assert {"python", "commit", "uncommitted_changes"} <= set(run["environment"])
    detail = metrics["metric_details"]["handoff_precision"]
    assert detail["denominator"] >= 1 and detail["definition"]
    assert report["criteria"]["criteria_id"] == "rocha-reis-acceptance-v1" and report["criteria"]["safety_passed"]
    assert metrics["tokens"]["input"] > 0 and metrics["estimated_cost_usd_per_conversation"] > 0


def test_runs_are_comparable_and_archives_never_overwrite(tmp_path, monkeypatch, capsys):
    from rocha_reis_agent.evaluation import __main__ as cli
    monkeypatch.setattr(cli, "RESULTS_DIR", tmp_path / "results")
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    # v2 e v3 têm o mesmo contrato; o fake ignora o texto do prompt → métricas iguais. (v1 difere de
    # propósito: o adaptador Anthropic recusa o contrato v1 nos cenários de falha HTTP.)
    assert main(["--provider", "fake", "--prompt", "v2", "--output", str(a), "--save"]) == 0
    assert main(["--provider", "fake", "--prompt", "v3", "--output", str(b), "--save"]) == 0
    assert len(list((tmp_path / "results" / "runs").glob("*.json"))) == 2
    first, second = json.loads(a.read_text()), json.loads(b.read_text())
    # Latência e tokens sintéticos variam com o tamanho do prompt; qualidade e segurança devem coincidir.
    varying = {"latency_ms_local", "tokens", "estimated_cost_usd", "estimated_cost_usd_per_conversation",
               "estimated_cost_usd_worst_case_portion"}
    strip = lambda r: {k: v for k, v in r["metrics"].items() if k not in varying}
    assert strip(first) == strip(second)
    capsys.readouterr()
    assert compare([a, b]) == 0 and "AVISO" not in capsys.readouterr().out


def test_hostile_model_never_reaches_the_client_on_the_new_scenarios():    # item 42
    report = evaluate(load_scenarios(DEFAULT_SCENARIOS), provider="hostile", prompt_version="v3")
    assert report["metrics"]["safety_violations"] == 0 and report["criteria"]["safety_passed"]


# ------------------------------------------------ Fase 9B: preparada, bloqueada
def _authorization(tmp_path, **changes):
    now = datetime.now(timezone.utc)
    data = {"schema_version": 1, "authorized_by": "Responsável fictício", "executor": "Executor fictício",
            "google_account": "conta fictícia", "project_id": "projeto-ficticio-teste", "billing": "paid_cloud_project",
            "provider": "gemini", "models": [MODEL], "max_cost_usd": 1.0, "max_requests": 3,
            "max_scenarios": 10, "valid_from": (now - timedelta(hours=1)).isoformat(),
            "valid_until": (now + timedelta(hours=1)).isoformat(), "data_type": "synthetic_only",
            "storage": "temporário de teste", "stop_procedure": "encerrar o processo", "confirmation": CONFIRMATION}
    data.update(changes)
    path = tmp_path / "ai_evaluation_authorization.json"
    path.write_text(json.dumps(data))
    os.chmod(path, 0o600)
    return path


def test_real_run_is_refused_while_the_code_lock_is_closed(tmp_path):
    with pytest.raises(RealRunRefused, match="trava"):
        check_real_run(_authorization(tmp_path), model=MODEL, scenario_count=1)


@pytest.mark.parametrize("changes,path_mode,message", [
    ({}, 0o644, "0600"), ({"confirmation": "ok"}, 0o600, "inválido"), ({"models": ["gemini-3.8-flash"]}, 0o600, "Modelo"),
    ({"max_scenarios": 1}, 0o600, "cenários"), ({"max_cost_usd": 500}, 0o600, "inválido"),
    ({"data_type": "real"}, 0o600, "inválido"), ({"billing": "free_tier"}, 0o600, "inválido"),
    ({"per_model_max_requests": {"gemini-3.8-flash": 1}}, 0o600, "inválido"),
    ({"valid_from": "2026-01-01T00:00:00+00:00", "valid_until": "2026-01-02T00:00:00+00:00"}, 0o600, "período"),
])
def test_real_run_authorization_checks(tmp_path, monkeypatch, changes, path_mode, message):
    monkeypatch.setattr(config_module, "GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED", True)
    path = _authorization(tmp_path, **changes)
    os.chmod(path, path_mode)
    with pytest.raises(RealRunRefused, match=message):
        check_real_run(path, model=MODEL, scenario_count=5)


def test_authorized_dry_run_respects_request_budget_and_smoke(tmp_path, monkeypatch):
    """Ensaio da 9B com trava aberta SÓ no teste e HTTP em memória: o teto da autorização vale."""
    monkeypatch.setattr(config_module, "GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED", True)
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    fake, calls = FakeAIProvider(), []

    def handler(request):
        calls.append(1)
        proposal = fake.generate(ai_request_from(request, "v3"), timeout_seconds=5).model_dump(mode="json")
        return httpx2.Response(200, json=gemini_body(proposal))
    http = httpx2.Client(transport=httpx2.MockTransport(handler))
    subset = _subset({"C1", "C3", "C6", "C7", "D3"})
    smoke = evaluate(subset, provider="gemini", prompt_version="v3", model=MODEL,
                     authorization_path=_authorization(tmp_path), smoke=True, http_client=http)
    assert smoke["metrics"]["scenarios_total"] == 1 and len(calls) == 1 and smoke["metrics"]["safety_violations"] == 0
    calls.clear()
    full = evaluate(subset, provider="gemini", prompt_version="v3", model=MODEL,
                    authorization_path=_authorization(tmp_path), http_client=http)
    assert len(calls) == 3                                   # max_requests=3: os demais recusados localmente
    assert full["metrics"]["safety_violations"] == 0 and full["metrics"]["fallback_rate"] > 0
    assert full["run"]["provider"] == "gemini" and full["run"]["model"] == MODEL


# ------------------------------------------------ Fase 9B: persistência do consumo Gemini
def test_migration_7_to_8_keeps_usage_and_adds_gemini_columns(tmp_path):
    import sqlite3
    from contextlib import closing
    from rocha_reis_agent.persistence.sqlite import APPLICATION_ID, MIGRATIONS, SCHEMA_VERSION, _statements
    path = tmp_path / "legado7.synthetic.sqlite3"
    with closing(sqlite3.connect(path, isolation_level=None)) as connection:
        connection.execute("BEGIN")
        for number in range(1, 8):
            for statement in _statements(next(MIGRATIONS.glob(f"{number:03d}_*.sql")).read_text(encoding="utf-8")):
                connection.execute(statement)
        connection.execute("PRAGMA user_version=7")
        connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
        connection.execute("INSERT INTO ai_usage(at,provider,model,prompt_version,outcome,input_tokens,output_tokens)"
                           " VALUES('2026-11-20T10:00:00+00:00','anthropic','claude-opus-5-5','v2','ok',300,80)")
        connection.execute("COMMIT")
    os.chmod(path, 0o600)
    repository = SQLiteConversationRepository(path)          # aplica a migração 8
    from rocha_reis_agent.persistence.sqlite_ai import SQLiteUsageLedger
    SQLiteUsageLedger(repository).record(provider="gemini", model=MODEL + "-001", requested_model=MODEL,
                                         prompt_version="v4", outcome="ok", input_tokens=10, output_tokens=5,
                                         reasoning_tokens=7, estimated_cost_micros=1)
    with closing(sqlite3.connect(path)) as connection:
        assert SCHEMA_VERSION == 8 and connection.execute("PRAGMA user_version").fetchone() == (8,)
        rows_ = connection.execute("SELECT provider, model, requested_model, reasoning_tokens FROM ai_usage ORDER BY id").fetchall()
        assert rows_ == [("anthropic", "claude-opus-5-5", None, 0), ("gemini", MODEL + "-001", MODEL, 7)]  # histórico preservado
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_genai_sdk_is_not_loaded_by_the_offline_application(tmp_path):
    """Nenhuma importação do SDK Google no caminho offline: CLI/servidor sem IA real não carregam o cliente."""
    import subprocess
    import sys
    code = ("import sys; import rocha_reis_agent.main, rocha_reis_agent.simulator, rocha_reis_agent.bootstrap,"
            " rocha_reis_agent.panel.app, rocha_reis_agent.channels.local_cli;"
            " print('google.genai' in sys.modules, 'anthropic' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                         cwd=Path(__file__).resolve().parents[1], env={**os.environ, "GEMINI_API_KEY": ""})
    assert out.stdout.strip() == "False False", out.stderr[-500:]
