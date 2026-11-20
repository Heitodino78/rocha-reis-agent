"""Detector de regras substituível; não representa compreensão generativa."""

import re

from rocha_reis_agent.domain.contracts import Detection, Intent, normalize_for_matching as normalized
from rocha_reis_agent.knowledge import KnowledgeBase


def contains(text: str, phrase: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(normalized(phrase)) + r"(?!\w)", text) is not None


class RuleIntentDetector:
    def detect(self, text: str, knowledge: KnowledgeBase) -> Detection:
        value = normalized(text)
        intents: list[Intent] = []
        service_ids = tuple(
            service.id for service in knowledge.services
            if any(contains(value, alias) for alias in (service.name, *service.aliases))
        )
        patterns = {
            Intent.GREETING: r"^(oi|ola|bom dia|boa tarde|boa noite|oie)\b",
            Intent.PRICE: r"\b(precos?|valor(es)?|custa|custam|custo)\b|\bquanto (e|fica|sai)\b",
            Intent.BOOKING: (
                r"\b(agendar|agendamento|marcar|marcou|marcado|reservar|reserva|reservado|"
                r"remarcar|cancelar|disponibilidade|vagas?)\b|\bhorarios? disponiveis\b|"
                r"\btem horario\b|\b(quero|preciso|gostaria).{0,40}\bhorario\b|"
                # Pedido de confirmação de agenda nunca é atendido; vira orientação ao Booksy.
                r"\bconfirm\w*\b.{0,40}\b(horario|reserva|agendamento|marcacao)\b"
            ),
            Intent.HOURS: r"\b(funcionamento|abrem|abre|fecham|fecha)\b|\bque horas\b",
            Intent.LOCATION: r"\b(endereco|localizacao)\b|\bonde (fica|ficam)\b|\bcomo chegar\b",
            Intent.PROFESSIONALS: r"\b(profissionais|profissional|cabeleireiros?)\b|\bquem atende\b",
            Intent.POLICY: r"\b(politicas?|cancelamento|pagamento|tolerancia)\b",
            Intent.CANCELLATION_POLICY: r"\bcancelamento\b",
            Intent.SERVICES: r"\b(servicos?|fazem|oferecem)\b|\b(informacoes|informacao) sobre\b",
            Intent.HUMAN: (
                r"\b(atendente|humano|pessoa|recepcionista|reclamacao|reclamar)\b|"
                # Reclamação explícita escala para humano (ARQUITETURA §6).
                r"\b(insatisfeit[oa]s?|pessim[oa]s?|horrivel|absurdo)\b|"
                r"\b(falar|conversar) com alguem\b|\bchama\w* alguem\b|"
                r"\bnao (quero|vou|gosto de) (falar|conversar) com (um |o |uma |a )?(robo|bot|maquina)\b"
            ),
        }
        for intent, pattern in patterns.items():
            if re.search(pattern, value):
                intents.append(intent)
        if Intent.CANCELLATION_POLICY in intents and not re.search(r"\b(pagamento|tolerancia)\b", value):
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
            if value in {normalized(question) for question in faq.questions}
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
        return Detection(
            tuple(intents) or (Intent.UNKNOWN,), service_ids,
            use_context=follow_up and not service_ids, catalog_requested=catalog,
            faq_index=faq_index,
        )
