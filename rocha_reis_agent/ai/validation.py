"""Validação de propostas; citações sozinhas nunca autorizam texto livre."""

from pydantic import ValidationError

from rocha_reis_agent.ai.contracts import AIResponse, AIValidationError
from rocha_reis_agent.domain.contracts import Detection, Intent
from rocha_reis_agent.knowledge_catalog import FAQFact
from rocha_reis_agent.knowledge_projection import AuthorizedKnowledge


def parse_response(value: AIResponse | dict[str, object] | str) -> AIResponse:
    try:
        if isinstance(value, AIResponse):
            if set(value.__dict__) - AIResponse.model_fields.keys() or value.__pydantic_extra__:
                raise AIValidationError("Campos adicionais na proposta.")
            # Saída não confiável: evitar warnings de serialização com valores privados;
            # todos os campos serão validados novamente logo abaixo.
            value = value.model_dump(mode="python", warnings=False)
        if isinstance(value, str):
            if len(value) > 32000:
                raise ValueError("Saída excessiva.")
            return AIResponse.model_validate_json(value)
        return AIResponse.model_validate(value)
    except (ValidationError, ValueError, TypeError):
        raise AIValidationError("Estrutura do provedor inválida.") from None


def response_detection(response: AIResponse, knowledge: AuthorizedKnowledge) -> Detection:
    known = {s.id for s in knowledge.base.services}
    if not set(response.service_ids) <= known or len(set(response.intents)) != len(response.intents):
        raise AIValidationError("Serviço ou intenções não autorizados.")
    authorized = {fact.reference.key: fact for fact in knowledge.facts}
    refs = [ref.key for ref in response.fact_references]
    if len(refs) != len(set(refs)) or not set(refs) <= authorized.keys():
        raise AIValidationError("Referências não autorizadas.")
    faq_index = None
    if response.faq_reference is not None:
        fact = authorized.get(response.faq_reference.key)
        if fact is None or not isinstance(fact.data, FAQFact):
            raise AIValidationError("FAQ não autorizada.")
        faq_index = knowledge.base.faqs.index(fact.data.faq)
    if (Intent.FAQ in response.intents) != (faq_index is not None):
        raise AIValidationError("FAQ incompatível com a intenção.")
    if (Intent.HUMAN in response.intents) != response.human_handoff_suggested:
        raise AIValidationError("Sugestão de ajuda humana inconsistente.")
    return Detection(
        response.intents, response.service_ids, response.use_context,
        response.catalog_requested, faq_index,
    )


def validate_candidate(
    response: AIResponse, detection: Detection, knowledge: AuthorizedKnowledge,
    authorized_reply: str,
) -> None:
    required = {ref.key for ref in knowledge.references_for(detection)}
    if required != {ref.key for ref in response.fact_references}:
        raise AIValidationError("Referências não correspondem aos dados utilizados.")
    if response.candidate_reply != authorized_reply:
        raise AIValidationError("Resposta candidata não corresponde aos campos e templates autorizados.")
    clarification = Intent.UNKNOWN in detection.intents or (detection.use_context and not detection.service_ids)
    if response.needs_clarification != clarification:
        raise AIValidationError("Esclarecimento inconsistente.")
