"""Contrato de provedor e prompts versionados sem rede ou credenciais."""

import json
from pathlib import Path

import pytest

from rocha_reis_agent.ai.contracts import AIResponse, AIValidationError
from rocha_reis_agent.ai.fake import FakeAIProvider
from rocha_reis_agent.ai.prompts import PromptRegistry
from rocha_reis_agent.ai.validation import parse_response
from rocha_reis_agent.domain.contracts import SimulationError


def test_fake_is_deterministic_and_response_matches_typed_contract(agent, send, provider_spy):
    agent.provider=provider_spy
    result=send("Quanto custa o corte?")
    request=provider_spy.requests[0]
    fake=FakeAIProvider()
    first=fake.generate(request,timeout_seconds=2)
    assert first == fake.generate(request,timeout_seconds=2)
    assert parse_response(first.model_dump_json())==first
    assert parse_response(first.model_dump(mode="json"))==first
    assert result.prompt_version=="v1"
    assert set(result.fact_references)=={"price:corte@1","service:corte@1"}


@pytest.mark.parametrize("field",["intents","candidate_reply","fact_references"])
def test_missing_required_output_field_is_rejected(agent, send, provider_spy, field):
    agent.provider=provider_spy
    def remove(value,request):
        del value[field]
        return value
    provider_spy.transform=remove
    result=send("Quanto custa o corte?")
    assert result.state.value=="WAITING_HUMAN"
    assert "Não consegui processar" in result.reply
    assert not result.fact_references


def test_unvalidated_pydantic_copies_are_revalidated(agent, send, provider_spy):
    agent.provider=provider_spy
    send("Olá")
    response=FakeAIProvider().generate(provider_spy.requests[0],timeout_seconds=2)
    with pytest.raises(AIValidationError):
        parse_response(response.model_copy(update={"intents":("nonexistent-intent",)}))
    with pytest.raises(AIValidationError):
        parse_response(response.model_copy(update={"admin_action":"resume"}))


def test_new_prompt_can_be_selected_without_erasing_previous_version(tmp_path: Path):
    original=PromptRegistry().get()
    (tmp_path/"v1.json").write_text(original.model_dump_json(),encoding="utf-8")
    second=original.model_copy(update={"version":"v2","changes":("Revisão fictícia de teste.",)})
    (tmp_path/"v2.json").write_text(second.model_dump_json(),encoding="utf-8")
    (tmp_path/"registry.json").write_text(json.dumps({"active":"v2","versions":["v1","v2"]}))
    registry=PromptRegistry(tmp_path)
    assert registry.get().version=="v2"
    assert registry.get("v1")==original
    with pytest.raises(SimulationError,match="não registrada"):
        registry.get("v9")


def test_prompt_manifest_cannot_traverse_directories(tmp_path: Path):
    (tmp_path/"registry.json").write_text(json.dumps({"active":"v1","versions":["../private"]}))
    with pytest.raises(SimulationError,match="Registro de prompts inválido"):
        PromptRegistry(tmp_path)


def test_unknown_active_prompt_is_configuration_error(tmp_path: Path):
    (tmp_path/"registry.json").write_text('{"active":"v2","versions":["v1"]}')
    (tmp_path/"v1.json").write_text(PromptRegistry().get().model_dump_json())
    with pytest.raises(SimulationError):
        PromptRegistry(tmp_path)


def test_request_rejects_excessive_history_and_unexpected_fields(agent,send,provider_spy):
    from pydantic import ValidationError
    from rocha_reis_agent.ai.contracts import AIRequest
    agent.provider=provider_spy
    send("Olá")
    values=provider_spy.requests[0].model_dump()
    values["history"]=[{"text":"dado fictício"}]*11
    with pytest.raises(ValidationError): AIRequest.model_validate(values)
    values["history"]=[]
    values["other_customer_history"]="PRIVATE_OTHER_CUSTOMER"
    with pytest.raises(ValidationError): AIRequest.model_validate(values)
