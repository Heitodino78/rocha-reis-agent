"""Detector de regras substituível; não representa compreensão generativa."""

import re

from rocha_reis_agent.domain.contracts import Detection, Intent, normalize_for_matching as normalized
from rocha_reis_agent.knowledge import KnowledgeBase


def contains(text: str, phrase: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(normalized(phrase)) + r"(?!\w)", text) is not None


# Fase 9 — vocabulário FECHADO do domínio usado na correção ortográfica. Não é um
# dicionário de português: só palavras-chave das regras abaixo (e, em tempo de
# execução, os nomes/aliases do catálogo aprovado). Palavras curtas não são corrigidas.
_DOMAIN_LEXICON = frozenset((
    "atendente", "atendentes", "recepcionista", "reclamacao", "reclamar", "agendar", "agendamento",
    "reservar", "remarcar", "cancelar", "disponibilidade", "horario", "horarios", "funcionamento",
    "endereco", "localizacao", "profissionais", "profissional", "cabeleireiro", "cabeleireiros",
    "cancelamento", "pagamento", "tolerancia", "servico", "servicos", "informacoes", "informacao",
    "promocao", "promocoes", "desconto", "descontos", "gerente",
))
_MIN_CORRECTION_LENGTH = 7
_ELLIPSIS = re.compile(r"e (?:(?:o|a|os|as|no|na|do|da|pro|pra|para|pela|pelo) )?(?P<rest>.+?)(?: entao)?")


def _within_one_edit(a: str, b: str) -> bool:
    """Distância de Damerau-Levenshtein ≤ 1 (inserção, remoção, troca ou transposição adjacente)."""
    if a == b or abs(len(a) - len(b)) > 1:
        return a == b
    if len(a) == len(b):
        diff = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1
                                  and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    i = 0
    while i < len(short) and short[i] == long_[i]:
        i += 1
    return short[i:] == long_[i + 1:]


def correct_spelling(value: str, extra_lexicon: frozenset[str] = frozenset()) -> str:
    """Corrige tokens a UMA edição de exatamente uma palavra do vocabulário fechado.

    Conservador: tokens com menos de 7 letras, palavras já conhecidas e correções
    ambíguas (duas candidatas) permanecem inalterados.
    """
    lexicon = _DOMAIN_LEXICON | extra_lexicon
    words = value.split()
    for index, word in enumerate(words):
        if len(word) < _MIN_CORRECTION_LENGTH or word in lexicon or not word.isalpha():
            continue
        candidates = {term for term in lexicon if _within_one_edit(word, term)}
        if len(candidates) == 1:
            words[index] = candidates.pop()
    return " ".join(words)


def _service_lexicon(knowledge: KnowledgeBase) -> frozenset[str]:
    return frozenset(
        token for service in knowledge.services for alias in (service.name, *service.aliases)
        for token in normalized(alias).split() if len(token) >= _MIN_CORRECTION_LENGTH and token.isalpha()
    )


def _services_in(value: str, knowledge: KnowledgeBase) -> tuple[str, ...]:
    return tuple(
        service.id for service in knowledge.services
        if any(contains(value, alias) for alias in (service.name, *service.aliases))
    )


class RuleIntentDetector:
    def detect(self, text: str, knowledge: KnowledgeBase) -> Detection:
        original = normalized(text)
        exact_services = _services_in(original, knowledge)
        value = correct_spelling(original, _service_lexicon(knowledge))
        intents: list[Intent] = []
        service_ids = _services_in(value, knowledge)
        spelling_corrected = bool(set(service_ids) - set(exact_services))
        patterns = {
            Intent.GREETING: r"^(oi|ola|bom dia|boa tarde|boa noite|oie)\b",
            Intent.PRICE: r"\b(precos?|valor(es)?|custa|custam|custo)\b|\bquanto (e|fica|sai)\b",
            Intent.BOOKING: (
                # Inclui passado em 1ª pessoa ("já reservei"): o agente nunca confirma; orienta ao Booksy.
                r"\b(agendar|agendamento|agendei|marcar|marcou|marcado|marquei|reservar|reserva|reservado|reservei|"
                r"remarcar|cancelar|disponibilidade|vagas?)\b|\bhorarios? disponiveis\b|"
                r"\btem horario\b|\b(quero|preciso|gostaria).{0,40}\bhorario\b|"
                # Pedido de confirmação de agenda nunca é atendido; vira orientação ao Booksy.
                r"\bconfirm\w*\b.{0,40}\b(horario|reserva|agendamento|marcacao)\b"
            ),
            Intent.HOURS: (
                r"\b(funcionamento|abrem|abre|fecham|fecha)\b|\bque horas\b|\bhorarios? de atendimento\b"
            ),
            Intent.LOCATION: r"\b(endereco|localizacao)\b|\bonde (fica|ficam)\b|\bcomo chegar\b",
            Intent.PROFESSIONALS: r"\b(profissionais|profissional|cabeleireiros?)\b|\bquem atende\b",
            # Meios de pagamento são assunto da política aprovada (DADOS_PENDENTES: "formas de pagamento").
            Intent.POLICY: (
                r"\b(politicas?|cancelamento|pagamento|tolerancia|pix|cartao|cartoes|credito|debito|"
                r"dinheiro|boleto|parcel\w*)\b"
            ),
            Intent.CANCELLATION_POLICY: r"\bcancelamento\b",
            Intent.SERVICES: r"\b(servicos?|fazem|oferecem)\b|\b(informacoes|informacao) sobre\b",
            Intent.PROMOTION: r"\b(promocao|promocoes|descontos?|cupom|cupons|liquidacao|black friday|ofertas?)\b",
            Intent.HUMAN: (
                r"\b(atendente|humano|pessoa|recepcionista|reclamacao|reclamar|gerente)\b|"
                # Reclamação explícita escala para humano (ARQUITETURA §6).
                r"\b(insatisfeit[oa]s?|pessim[oa]s?|horrivel|absurdo)\b|"
                r"\b(falar|conversar) com alguem\b|\bchama\w* alguem\b|"
                # Fase 9B — SEGURANÇA, não compreensão: saúde/gravidez/alergia vão para a equipe
                # (o assistente não orienta sobre saúde; termos do Gemini vedam conselho médico).
                r"\b(gravida|gestante|gravidez|amamentando|amamentacao|alergia|alergica|alergico|"
                r"medicamento|medicamentos|remedio|remedios|quimioterapia|dermatite|queimadura)\b|"
                r"\bnao (quero|vou|gosto de) (falar|conversar) com (um |o |uma |a )?(robo|bot|maquina)\b"
            ),
        }
        for intent, pattern in patterns.items():
            if re.search(pattern, value):
                intents.append(intent)
        if Intent.CANCELLATION_POLICY in intents and not re.search(
                r"\b(pagamento|tolerancia|pix|cartao|credito|debito|dinheiro|boleto)\b", value):
            intents.remove(Intent.POLICY)
        # Negação só anula o pedido quando nega DIRETAMENTE a pessoa ("não preciso de atendente").
        # "Não quero falar com robô, quero uma pessoa" continua sendo pedido de atendimento humano.
        if Intent.HUMAN in intents and re.search(
            r"\bnao (quero|preciso|desejo)( falar com| de| ser atendid[oa] por)?( um| uma| o| a)? "
            r"(humano|atendente|pessoa)\b", value
        ) and not re.search(r"\b(quero|prefiro|preciso)( falar com)?( um| uma)? (humano|atendente|pessoa)\b",
                            re.sub(r"\bnao (quero|preciso|desejo)\b", "", value)):
            intents.remove(Intent.HUMAN)
        if service_ids and not any(i in intents for i in (Intent.SERVICES, Intent.PRICE, Intent.BOOKING)):
            intents.append(Intent.SERVICES)
        faq_index = next((
            index for index, faq in enumerate(knowledge.faqs)
            if {original, value} & {normalized(question) for question in faq.questions}
        ), None)
        if faq_index is not None:
            intents.append(Intent.FAQ)
        follow_up = re.fullmatch(
            r"(?:e )?(?:quanto (?:custa|e|fica|sai)|qual (?:o )?(?:preco|valor)|"
            r"(?:o )?(?:preco|valor))(?: (?:dele|dela|disso|desse servico))?", value
        ) is not None
        catalog = re.fullmatch(
            r"(?:quais (?:sao )?(?:os )?)?(?:seus )?servicos(?: disponiveis)?"
            r"(?: voces (?:tem|oferecem|fazem))?", value
        ) is not None
        # Pergunta elíptica: só conectivo + serviço ("e a escova?", "e pro corte?"). A intenção
        # vem da pergunta anterior do cliente; a decisão é do backend (AgentService).
        ellipsis = _ELLIPSIS.fullmatch(value)
        elliptical = len(service_ids) == 1 and ellipsis is not None and any(
            normalized(alias) == ellipsis.group("rest")
            for service in knowledge.services if service.id == service_ids[0]
            for alias in (service.name, *service.aliases)
        )
        return Detection(
            tuple(intents) or (Intent.UNKNOWN,), service_ids,
            use_context=follow_up and not service_ids, catalog_requested=catalog,
            faq_index=faq_index, inherit_intents=elliptical, spelling_corrected=spelling_corrected,
        )
