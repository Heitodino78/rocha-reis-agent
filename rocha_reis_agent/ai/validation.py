"""Validação de propostas; citações sozinhas nunca autorizam texto livre."""

import re
import unicodedata

from pydantic import ValidationError

from rocha_reis_agent.ai.contracts import AIResponse, AIResponseV2, AIValidationError
from rocha_reis_agent.domain.contracts import Detection, Intent, normalize_for_matching
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


def parse_response_v2(value: AIResponseV2 | dict[str, object] | str) -> AIResponseV2:
    """Saída do provedor é dado NÃO confiável: revalidar tudo, recusar campos extras."""
    try:
        if isinstance(value, AIResponseV2):
            value = value.model_dump(mode="python", warnings=False)
        if isinstance(value, str):
            if not value.strip() or len(value) > 32000:
                raise ValueError("Saída vazia ou excessiva.")
            return AIResponseV2.model_validate_json(value)
        if isinstance(value, dict):
            return AIResponseV2.model_validate(value)
        raise TypeError("Tipo de saída inesperado.")
    except (ValidationError, ValueError, TypeError):
        raise AIValidationError("Estrutura do provedor inválida.") from None


def response_detection(response: AIResponse | AIResponseV2, knowledge: AuthorizedKnowledge) -> Detection:
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


def validate_structure_v2(response: AIResponseV2, detection: Detection, knowledge: AuthorizedKnowledge) -> None:
    """As referências citadas devem ser EXATAMENTE as que o backend usará; nada a mais, nada a menos."""
    required = {ref.key for ref in knowledge.references_for(detection)}
    if required != {ref.key for ref in response.fact_references}:
        raise AIValidationError("Referências não correspondem aos dados utilizados.")
    clarification = Intent.UNKNOWN in detection.intents or (detection.use_context and not detection.service_ids)
    if response.needs_clarification != clarification:
        raise AIValidationError("Esclarecimento inconsistente.")


# Abertura: só cordialidade. Qualquer sinal de fato, compromisso, ação ou meta-instrução → descartada.
_FORBIDDEN_STEMS = (
    # compromissos de agenda e disponibilidade
    "agend", "reserv", "confirm", "marquei", "marcad", "disponiv", "vaga", "horario", "amanha", "hoje",
    "segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo",
    # fatos comerciais
    "preco", "valor", "custa", "custo", "reais", "gratis", "gratuit", "desconto", "promoc", "oferta", "pagamento",
    "pix", "cartao", "endereco", "rua", "avenida", "localiz", "funcionamento", "abrimos", "fechamos", "politica",
    "cancelamento", "booksy", "link",
    # alegações de ação do sistema ou da equipe
    "transferi", "encaminhei", "notifiquei", "avisei", "atend", "humano", "equipe", "funcionari", "recepcion",
    "pessoa", "gerente", "profission", "cabeleir", "manicur", "esteticist",
    # meta / injeção / vazamento
    "instruc", "prompt", "sistema", "admin", "regra", "ignore", "ignorar", "modelo", "cliente", "historico",
)
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF]")


def sanitize_preface(text: str | None, knowledge: AuthorizedKnowledge) -> tuple[str | None, str | None]:
    """Retorna (abertura aprovada ou None, motivo da recusa ou None). Nunca levanta exceção."""
    if text is None:
        return None, None
    value = " ".join(text.split())
    if not value:
        return None, None
    if len(value) > 160:
        return None, "too_long"
    if any(unicodedata.category(c).startswith("C") for c in value) or "\n" in text:
        return None, "control_chars"
    if re.search(r"[0-9]", value) or re.search(r"r\$|https?:|www\.|\.com|\.br|@|#|<|>|\{|\}|\[|\]", value.lower()):
        return None, "factual_or_markup"
    if len(_EMOJI.findall(value)) > 1:
        return None, "emoji"
    normalized = normalize_for_matching(value)
    words = normalized.split()
    if any(word.startswith(stem) for word in words for stem in _FORBIDDEN_STEMS):
        return None, "forbidden_term"
    base = knowledge.base
    names = [service.name for service in base.services] + [a for s in base.services for a in s.aliases]
    names += list(base.professionals) + [base.name or "", base.address or ""]
    # Nomes cadastrados podem trazer complementos ("Fulana — cabelos"): compara-se também cada parte.
    parts = [part for name in names for part in re.split(r"[—–,;()/|-]", name)] + names
    for name in parts:
        term = normalize_for_matching(name)
        if len(term) >= 4 and re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", normalized):
            return None, "knowledge_entity"
    return value, None


def compose_reply(rendered: str, preface: str | None, label: str) -> str:
    """Insere a abertura aprovada logo após o rótulo de ambiente, antes dos trechos factuais."""
    if not preface:
        return rendered
    if rendered.startswith(label):
        return f"{label}{preface} {rendered[len(label):]}"
    return f"{preface} {rendered}"
