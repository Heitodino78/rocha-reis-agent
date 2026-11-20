"""Contexto mínimo, isolamento e barreira de estado antes de chamar o provedor."""

from io import StringIO

from rocha_reis_agent.application.handoff import HandoffAction as Action
from rocha_reis_agent.domain.contracts import ConversationState as State
from rocha_reis_agent.domain.conversations import SimulationLimits
from rocha_reis_agent.simulator import Simulator, main

OPERATOR="operador-local"


def test_provider_never_runs_during_human_pause_or_before_explicit_resume(agent,send,provider_spy):
    agent.provider=provider_spy
    send("Quero um humano.")
    assert not provider_spy.requests
    assert send("Quanto custa o corte?").reply is None
    agent.operator_action("cliente-a",Action.ASSUME,operator_id=OPERATOR)
    assert send("/operator resume").reply is None
    agent.operator_action("cliente-a",Action.RESOLVE,operator_id=OPERATOR)
    assert send("Pode retomar?").reply is None
    assert not provider_spy.requests
    agent.operator_action("cliente-a",Action.RESUME,operator_id=OPERATOR)
    assert "R$ 45,00" in send("Quanto custa o corte?").reply
    assert len(provider_spy.requests)==1


def test_provider_history_is_bounded_related_and_excludes_operator_and_other_customer(agent,send,provider_spy):
    agent.provider=provider_spy
    for _ in range(7): send("Informações sobre corte "+"x"*700,"cliente-a")
    send("PRIVATE_CLIENT_B","cliente-b")
    send("Quero um humano.","cliente-a")
    agent.operator_action("cliente-a",Action.ASSUME,operator_id=OPERATOR)
    agent.operator_reply("cliente-a","PRIVATE_OPERATOR_MESSAGE",operator_id=OPERATOR)
    agent.operator_action("cliente-a",Action.RESOLVE,operator_id=OPERATOR)
    agent.operator_action("cliente-a",Action.RESUME,operator_id=OPERATOR)
    send("E quanto custa?","cliente-a")
    request=provider_spy.requests[-1]
    serialized=request.model_dump_json()
    assert len(request.history)==4
    assert all(len(h.text)<=500 and h.role=="client" and h.trust=="untrusted-client" for h in request.history)
    assert "PRIVATE_CLIENT_B" not in serialized
    assert "PRIVATE_OPERATOR_MESSAGE" not in serialized
    assert "cliente-a" not in serialized and "auto-evento" not in serialized
    assert "reviewed_by" not in serialized and "updated_at" not in serialized
    assert request.service_context==("corte",)
    assert "Informações" not in repr(request)


def test_expired_context_does_not_reappear_via_history(agent,send,provider_spy):
    agent.conversations.limits=SimulationLimits(context_turns=1)
    agent.provider=provider_spy
    send("Informações sobre corte")
    send("Olá")
    result=send("E quanto custa?")
    request=provider_spy.requests[-1]
    assert not request.service_context and not request.history
    assert "De qual serviço" in result.reply


def test_provider_can_suggest_only_a_valid_backend_handoff(agent,send,provider_spy):
    from rocha_reis_agent.domain.contracts import Detection,Intent
    from rocha_reis_agent.knowledge_projection import authorize_knowledge
    agent.provider=provider_spy
    def suggest(value,request):
        value.update(intents=["human"],human_handoff_suggested=True,fact_references=[])
        value["candidate_reply"]=agent.responses.render(Detection((Intent.HUMAN,)),authorize_knowledge(agent.knowledge.get()).base)
        return value
    provider_spy.transform=suggest
    result=send("Olá")
    assert result.state==State.WAITING_HUMAN
    assert "Nenhum atendente real" in result.reply
    assert agent.conversations.snapshot("cliente-a").transitions[0].action=="validated_ai_suggestion"


def test_cli_shows_only_fact_references_and_prompt_version(agent):
    output=StringIO()
    Simulator(agent,output).run(StringIO("/start cliente-a\n/send e1 Quanto custa o corte?\n/provenance\n/quit\n"))
    text=output.getvalue()
    assert "prompt=v1" in text and "price:corte@1" in text
    assert agent.prompt.instructions[0] not in text


def test_cli_reads_versioned_synthetic_catalog_without_credentials(monkeypatch,capsys):
    from pathlib import Path
    path=Path(__file__).resolve().parents[1]/"fixtures"/"salon.versioned.synthetic.json"
    monkeypatch.setattr("sys.stdin",StringIO("/start cliente-a\n/send e1 Quanto custa o corte?\n/provenance\n/quit\n"))
    assert main(["--catalog",str(path)])==0
    text=capsys.readouterr().out
    assert "DADOS FICTÍCIOS" in text and "R$ 45,00" in text and "prompt=v1" in text


def test_provider_receives_only_facts_relevant_to_the_request(agent,send,provider_spy):
    agent.provider=provider_spy
    send("Quanto custa o corte?")
    assert {f.reference.id for f in provider_spy.requests[-1].facts}=={"service:corte","price:corte"}
    send("Onde fica o salão?")
    request=provider_spy.requests[-1]
    assert {f.data.kind for f in request.facts}=={"location"}
    assert not request.history and not request.service_context
