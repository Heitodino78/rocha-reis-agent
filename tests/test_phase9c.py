"""Fase 9C-A — preparação OFFLINE da homologação real com Google Gemini.

Cobre: guarda da avaliação real (Meta desligada, experimento pré-registrado por hash,
etapa autorizada, arquivo de parada, livro-razão persistente entre execuções, interrupção
por falha crítica), contexto oferecido pelo backend ao modelo, classificação contraditória,
cenários prioritários, fluxo de ponta a ponta do canal com FakeWhatsAppTransport, plano de
orçamento e o patch de ativação (verificado, NUNCA aplicado). Sockets reais bloqueados;
nenhuma chamada externa. Nada aqui mede o Gemini real.
"""

import json
import os
from pathlib import Path
import socket
import subprocess
from datetime import date, datetime, timedelta, timezone

import httpx2
import pytest

from rocha_reis_agent import config as config_module
from rocha_reis_agent.ai.budget import BudgetExceeded, RunBudget
from rocha_reis_agent.ai.fake import FakeAIProvider
from rocha_reis_agent.bootstrap import build_agent
from rocha_reis_agent.catalog_io import load_synthetic_catalog
from rocha_reis_agent.domain.contracts import ConversationState as State, InboundMessage
from rocha_reis_agent.domain.conversations import utc_now
from rocha_reis_agent.evaluation import real_run
from rocha_reis_agent.evaluation.budget_plan import measure_stage, price
from rocha_reis_agent.evaluation.e2e import FLOW, run_e2e
from rocha_reis_agent.evaluation.providers import gemini_payload, simulated_gemini
from rocha_reis_agent.evaluation.real_run import CONFIRMATION, RealRunRefused, check_real_run, verify_experiment
from rocha_reis_agent.evaluation.runner import DEFAULT_CATALOG, DEFAULT_SCENARIOS, EXPERIMENT, evaluate, select_stage
from rocha_reis_agent.evaluation.scenarios import ScenarioSet, load_scenarios
from rocha_reis_agent.persistence.sqlite import SQLiteConversationRepository
from gemini_support import MODEL, ai_request_from, body as gemini_body, make_provider, make_request

ROOT = Path(__file__).resolve().parents[1]
PRIORITY = ("H12", "L4", "J7", "F7", "K6", "E8", "D8", "D9", "X4")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("Tentativa de conexão de rede na Fase 9C-A.")
    monkeypatch.setattr(socket.socket, "connect", refuse)


def catalog():
    return load_synthetic_catalog(DEFAULT_CATALOG)


def send(agent, text, event, client="c1"):
    return agent.handle(InboundMessage(event, client, text, utc_now()))


def authorization(tmp_path, **changes):
    now = datetime.now(timezone.utc)
    (tmp_path / "ledger").mkdir(mode=0o700, exist_ok=True)
    data = {"schema_version": 3,
            "confirmed_pricing": {MODEL: {"input": 0.25, "output": 1.50}}, "meta_remains_disabled": True,
            "google_billing_controls": "orçamento de teste com alerta (fictício)", "residual_billing_risk_accepted": True, "experiment_id": "rocha-reis-9c-gemini-v1", "stages": ["smoke", "pilot", "full"],
            "usage_ledger": str(tmp_path / "ledger" / "uso.ai-homolog.synthetic.sqlite3"),
            "stop_file": str(tmp_path / "PARAR"), "authorized_by": "Responsável fictício",
            "executor": "Executor fictício", "google_account": "conta fictícia", "project_id": "projeto-ficticio-teste",
            "billing": "paid_cloud_project", "provider": "gemini", "models": [MODEL], "max_cost_usd": 1.0,
            "max_requests": 4, "max_scenarios": 200, "valid_from": (now - timedelta(hours=1)).isoformat(),
            "valid_until": (now + timedelta(hours=1)).isoformat(), "data_type": "synthetic_only",
            "storage": "temporário de teste", "stop_procedure": "arquivo de parada", "confirmation": CONFIRMATION}
    data.update(changes)
    path = tmp_path / "ai_evaluation_authorization.json"
    path.write_text(json.dumps(data))
    os.chmod(path, 0o600)
    return path


def fake_model(calls):
    def handler(request):
        calls.append(1)
        proposal = FakeAIProvider().generate(ai_request_from(request, "v4"), timeout_seconds=5).model_dump(mode="json")
        return httpx2.Response(200, json=gemini_body(proposal))
    return httpx2.Client(transport=httpx2.MockTransport(handler))


def subset(ids):
    full = load_scenarios(DEFAULT_SCENARIOS)
    return ScenarioSet(**{**full.model_dump(exclude={"scenarios"}), "scenarios": [s for s in full.scenarios if s.id in ids]})


# ------------------------------------------------ guarda da execução real (9C-B bloqueada)
def test_real_run_stays_blocked_and_cli_refuses_every_stage(tmp_path):
    assert config_module.GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED is False
    for stage in ("smoke", "pilot", "full"):
        with pytest.raises(RealRunRefused, match="trava"):
            check_real_run(authorization(tmp_path), model=MODEL, scenario_count=1, stage=stage)
    from rocha_reis_agent.evaluation.__main__ import main
    for stage in ("smoke", "pilot", "e2e", "full", "repeat"):
        assert main(["--provider", "gemini", "--prompt", "v4", "--stage", stage]) == 2


def test_gemini_run_refused_if_any_meta_lock_is_open(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED", True)
    for lock in ("GRAPH_TRANSPORT_ACTIVATION_AUTHORIZED", "HOMOLOGATION_TRANSPORT_ACTIVATION_AUTHORIZED"):
        monkeypatch.setattr(config_module, lock, True)
        with pytest.raises(RealRunRefused, match="Meta"):
            check_real_run(authorization(tmp_path), model=MODEL, scenario_count=1)
        monkeypatch.setattr(config_module, lock, False)


@pytest.mark.parametrize("changes,message", [
    ({"stages": ["smoke"]}, "Etapa"),
    ({"experiment_id": "outro-experimento"}, "Experimento"),
    ({"usage_ledger": "relativo.synthetic.sqlite3"}, "inválido"),
    ({"stop_file": str(ROOT / "PARAR")}, "inválido"),                  # dentro do repositório
    ({"usage_ledger": "/tmp/uso.sqlite3"}, "inválido"),                # sem sufixo sintético
    ({"schema_version": 2}, "inválido"),
    ({"meta_remains_disabled": False}, "inválido"),
    ({"residual_billing_risk_accepted": False}, "inválido"),
    ({"confirmed_pricing": {}}, "inválido"),
    ({"confirmed_pricing": {MODEL: {"input": 0.25, "output": 1.40}}}, "Divergência de preços"),
])
def test_authorization_schema_2_checks(tmp_path, monkeypatch, changes, message):
    monkeypatch.setattr(config_module, "GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED", True)
    with pytest.raises(RealRunRefused, match=message):
        check_real_run(authorization(tmp_path, **changes), model=MODEL, scenario_count=1, stage="pilot")


def test_experiment_is_pinned_by_hash_and_prompt(tmp_path, monkeypatch):
    manifest = verify_experiment("rocha-reis-9c-gemini-v1", prompt_version="v4", model=MODEL)
    assert set(manifest["pinned_files"]) == {"evals/scenarios.synthetic.json", "evals/catalog.synthetic.json",
                                             "evals/criteria.json", "prompts/v4.json"}
    with pytest.raises(RealRunRefused, match="prompt"):
        verify_experiment("rocha-reis-9c-gemini-v1", prompt_version="v3", model=MODEL)
    with pytest.raises(RealRunRefused, match="Modelo"):
        verify_experiment("rocha-reis-9c-gemini-v1", prompt_version="v4", model="gemini-2.5-flash")
    altered = json.loads(EXPERIMENT.read_text())
    altered["pinned_files"]["evals/criteria.json"] = "0" * 16          # critérios "mudados" após o pré-registro
    fake = tmp_path / "experiment.json"
    fake.write_text(json.dumps(altered))
    with pytest.raises(RealRunRefused, match="mudaram"):
        verify_experiment("rocha-reis-9c-gemini-v1", prompt_version="v4", model=MODEL, manifest_path=fake)


def test_stop_file_blocks_before_and_during_a_run(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED", True)
    monkeypatch.setenv("GEMINI_API_KEY", "AIza" + "x" * 35)
    path = authorization(tmp_path)
    (tmp_path / "PARAR").touch()
    with pytest.raises(RealRunRefused, match="Interrupção"):
        check_real_run(path, model=MODEL, scenario_count=1, stage="pilot")
    (tmp_path / "PARAR").unlink()
    calls = []

    class Stopping:
        def __call__(self, request):
            calls.append(1)
            (tmp_path / "PARAR").touch()                 # operador aciona a parada após a 1ª chamada
            proposal = FakeAIProvider().generate(ai_request_from(request, "v4"), timeout_seconds=5).model_dump(mode="json")
            return httpx2.Response(200, json=gemini_body(proposal))
    report = evaluate(load_scenarios(DEFAULT_SCENARIOS), provider="gemini", prompt_version="v4", model=MODEL,
                      authorization_path=path, stage="pilot",
                      http_client=httpx2.Client(transport=httpx2.MockTransport(Stopping())))
    assert len(calls) == 1 and "interrupção" in report["run"]["aborted"]


def test_persistent_ledger_caps_all_runs_of_the_authorization(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED", True)
    monkeypatch.setenv("GEMINI_API_KEY", "AIza" + "y" * 35)
    path, calls = authorization(tmp_path, max_requests=3), []
    for _ in range(3):   # três execuções da mesma autorização; teto TOTAL = 3 requisições
        evaluate(subset({"A1", "C6"}), provider="gemini", prompt_version="v4", model=MODEL, authorization_path=path,
                 stage="full", http_client=fake_model(calls))
    assert len(calls) == 3


def test_critical_failure_aborts_the_real_run(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED", True)
    monkeypatch.setenv("GEMINI_API_KEY", "AIza" + "z" * 35)
    calls = []

    def broken(request):
        calls.append(1)
        return httpx2.Response(200, json=gemini_body(text="não é json"))
    report = evaluate(subset({"C1", "C3", "C5"}), provider="gemini", prompt_version="v4", model=MODEL,
                      authorization_path=authorization(tmp_path), stage="full",
                      http_client=httpx2.Client(transport=httpx2.MockTransport(broken)))
    assert len(calls) == 1 and report["run"]["aborted"].startswith("falha crítica")


# ------------------------------------------------ contexto oferecido pelo backend
def _context_model(*, use_context=True, service_ids=()):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:   # 1ª mensagem: classificação normal
            proposal = FakeAIProvider().generate(ai_request_from(request, "v4"), timeout_seconds=5).model_dump(mode="json")
            return httpx2.Response(200, json=gemini_body(proposal))
        payload = gemini_payload(request)
        refs = [f["referencia"] for f in payload["fatos_aprovados_somente_conteudo"]]
        return httpx2.Response(200, json=gemini_body({
            "contract": "ai-response-v2", "intents": ["price"], "service_ids": list(service_ids),
            "use_context": use_context, "catalog_requested": False, "faq_reference": None, "fact_references": refs,
            "needs_clarification": False, "human_handoff_suggested": False, "information_missing": False,
            "preface": None}))
    return handler


def test_model_can_resolve_a_followup_the_rules_miss_but_backend_fixes_the_service(tmp_path):
    provider, repository = make_provider(tmp_path, _context_model())
    agent = build_agent(catalog(), conversation_repository=repository, provider=provider, prompt_version="v4")
    send(agent, "Quanto custa a hidratação?", "e1")
    reply = send(agent, "E nessa, quanto vou gastar?", "e2").reply   # regras não reconhecem; modelo sim
    assert "R$ 80,00" in reply


@pytest.mark.parametrize("first,service_ids", [
    ("Oi", ()),                               # nenhum serviço em contexto: nada é oferecido
    ("Quanto custa a hidratação?", ("corte",)),   # modelo tenta trocar o serviço do contexto
])
def test_context_cannot_be_invented_or_swapped_by_the_model(tmp_path, first, service_ids):
    provider, repository = make_provider(tmp_path, _context_model(service_ids=service_ids))
    agent = build_agent(catalog(), conversation_repository=repository, provider=provider, prompt_version="v4")
    send(agent, first, "e1")
    result = send(agent, "E nessa, quanto vou gastar?", "e2")
    assert result.state == State.WAITING_HUMAN and "R$" not in result.reply


def test_contradictory_classification_fails_safe(tmp_path):
    def handler(request):
        return httpx2.Response(200, json=gemini_body({
            "contract": "ai-response-v2", "intents": ["human"], "service_ids": [], "use_context": False,
            "catalog_requested": False, "faq_reference": None, "fact_references": [], "needs_clarification": False,
            "human_handoff_suggested": False, "information_missing": False, "preface": None}))
    provider, repository = make_provider(tmp_path, handler)
    agent = build_agent(catalog(), conversation_repository=repository, provider=provider, prompt_version="v4")
    result = send(agent, "Não tem alguém que consiga me explicar melhor?", "e1")
    assert result.state == State.WAITING_HUMAN and "Não consegui processar" in result.reply


# ------------------------------------------------ cenários prioritários e etapas
def test_priority_scenarios_are_present_and_stages_are_pre_registered():
    scenarios = load_scenarios(DEFAULT_SCENARIOS)
    ids = {s.id for s in scenarios.scenarios}
    assert len(scenarios.scenarios) == 129 and scenarios.version == "3" and set(PRIORITY) <= ids
    assert [s.id for s in select_stage(scenarios, "smoke")] == ["A1"]
    pilot = [s.id for s in select_stage(scenarios, "pilot")]
    assert len(pilot) == 15 and set(PRIORITY) <= set(pilot)
    assert len(select_stage(scenarios, "full")) == 129


@pytest.mark.parametrize("text", [
    "Meu couro cabeludo está irritado. Posso fazer química?", "Estou com coceira na cabeça, posso pintar?",
])
def test_health_messages_escalate_without_advice(tmp_path, text):
    agent = build_agent(catalog(), conversation_repository=SQLiteConversationRepository(tmp_path / "h.synthetic.sqlite3"))
    result = send(agent, text, "e1")
    assert result.state == State.WAITING_HUMAN and "pode fazer" not in result.reply.lower()


# ------------------------------------------------ ponta a ponta do canal (Meta nunca usada)
@pytest.mark.parametrize("kind", ["fake", "gemini-simulated"])
def test_end_to_end_channel_flow(tmp_path, kind):
    factory = None if kind == "fake" else (lambda repo: simulated_gemini(repo)[0])
    report = run_e2e(factory, catalog_path=DEFAULT_CATALOG, directory=tmp_path)
    assert report["passed"], [s for s in report["steps"] if not s["passed"]]
    assert report["meta_transport"].startswith("fake") and report["outbox_not_final"] == 0
    assert report["deliveries"] == sum(1 for s in FLOW if s.delivered)


def test_end_to_end_flow_survives_provider_failure(tmp_path):
    report = run_e2e(lambda repo: simulated_gemini(repo, http_mode="http_server_error")[0],
                     catalog_path=DEFAULT_CATALOG, directory=tmp_path)
    # Provedor indisponível: falha segura → atendimento humano; nada é inventado, nada sai na pausa.
    first = report["steps"][0]
    assert not first["passed"] and all("oráculo" not in f for s in report["steps"] for f in s["failures"])
    assert report["outbox_not_final"] == 0


# ------------------------------------------------ orçamento proposto e patch de ativação
def test_budget_plan_is_derived_from_real_request_sizes():
    sizes = measure_stage("smoke")
    assert len(sizes) == 1 and 1000 < sizes[0] < 6000
    planned = price(sizes, MODEL, max_output_tokens=2048, max_retries=1)
    tokens_worst = -(-sizes[0] // 2)
    assert planned["usd_ceiling_all_retries_full_output"] == round((tokens_worst * 0.25 + 2048 * 1.50) / 1e6 * 2, 4)
    flash_now = price(sizes, "gemini-3.8-flash", day=date(2026, 12, 31))["usd_pessimistic"]
    flash_2027 = price(sizes, "gemini-3.8-flash", day=date(2027, 1, 1))["usd_pessimistic"]
    assert flash_2027 == pytest.approx(2 * flash_now, rel=0.01)       # reajuste publicado


def test_halted_budget_refuses_before_sending(tmp_path):
    stop = tmp_path / "PARAR"
    stop.touch()
    calls = []
    provider, _ = make_provider(tmp_path, lambda r: calls.append(1), run_budget=RunBudget(halt_file=stop))
    with pytest.raises(BudgetExceeded, match="Interrupção"):
        provider.generate(make_request(), timeout_seconds=5)
    assert calls == []


def test_activation_patch_is_minimal_reversible_and_not_applied():
    patch = ROOT / "docs" / "homolog" / "ativacao_gemini_9c.patch"
    text = patch.read_text(encoding="utf-8")
    removed = [l for l in text.splitlines() if l.startswith("-") and not l.startswith("---")]
    added = [l for l in text.splitlines() if l.startswith("+") and not l.startswith("+++")]
    assert removed == ["-GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED: Final = False"]
    assert added[-1] == "+GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED: Final = True"
    assert all(l.startswith("+#") for l in added[:-1])                 # só comentários além da trava
    assert config_module.GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED is False  # NÃO aplicado
    check = subprocess.run(["git", "apply", "--check", str(patch)], cwd=ROOT, capture_output=True, timeout=30)
    assert check.returncode == 0, check.stderr


@pytest.mark.parametrize("text,offered", [
    ("E nessa, quanto vou gastar?", True),     # assunto não reconhecido: contexto oferecido
    ("Quanto tá saindo?", True),               # preço sem serviço: contexto oferecido
    ("Onde fica o salão?", False),             # outro assunto: NADA do serviço anterior vai ao modelo
    ("Quero marcar para sexta", False),
])
def test_context_offer_is_minimized(tmp_path, text, offered):
    seen = []

    class Spy(FakeAIProvider):
        def generate(self, request, *, timeout_seconds):
            seen.append(request)
            return super().generate(request, timeout_seconds=timeout_seconds)
    agent = build_agent(catalog(), conversation_repository=SQLiteConversationRepository(tmp_path / "m.synthetic.sqlite3"),
                        provider=Spy(), prompt_version="v4")
    send(agent, "Quanto custa a hidratação?", "e1")
    send(agent, text, "e2")
    request = seen[-1]
    assert bool(request.service_context) is offered
    assert any(f.reference.id == "price:hidratacao" for f in request.facts) is offered


def test_ledger_is_cumulative_across_successive_authorizations(tmp_path, monkeypatch):
    """Etapa 1 (piloto) e Etapa 2 (ampliada) com autorizações diferentes: contadores NÃO reiniciam."""
    monkeypatch.setattr(config_module, "GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED", True)
    monkeypatch.setenv("GEMINI_API_KEY", "AIza" + "w" * 35)
    calls = []
    first = authorization(tmp_path, max_requests=2)
    evaluate(subset({"A1", "C6"}), provider="gemini", prompt_version="v4", model=MODEL, authorization_path=first,
             stage="full", http_client=fake_model(calls))
    assert len(calls) == 2
    later = datetime.now(timezone.utc) + timedelta(minutes=1)
    second = authorization(tmp_path, max_requests=3, valid_from=(later - timedelta(minutes=2)).isoformat())
    evaluate(subset({"A1", "C6", "C7"}), provider="gemini", prompt_version="v4", model=MODEL,
             authorization_path=second, stage="full", http_client=fake_model(calls))
    assert len(calls) == 3   # teto cumulativo 3: a nova autorização só libera mais 1


def test_handoff_breakdown_is_reported_with_causes():
    report = evaluate(load_scenarios(DEFAULT_SCENARIOS), provider="fake", prompt_version="v4")
    metrics = report["metrics"]
    assert sorted(metrics["missed_handoffs"]) == ["H12", "H9", "L3", "L4"]   # pedidos/reclamações indiretos
    assert metrics["unnecessary_handoffs"] == []
    assert metrics["handoff_causes"]["health_safety_rule"] == 2 and metrics["handoff_causes"]["rule_request"] >= 10
    hostile = evaluate(load_scenarios(DEFAULT_SCENARIOS), provider="hostile", prompt_version="v4")["metrics"]
    # Seguro porém operacionalmente inadequado: encaminhamentos desnecessários por falha.
    assert len(hostile["unnecessary_handoffs"]) > 40 and hostile["handoff_rate"] > 0.4
