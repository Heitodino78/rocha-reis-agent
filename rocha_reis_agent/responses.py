"""Respostas determinísticas a partir de dados locais; nunca inferem fatos."""

from rocha_reis_agent.domain.contracts import Detection, Intent
from rocha_reis_agent.knowledge import KnowledgeBase, Service

HELP = "Se quiser, peça atendimento humano para registrarmos o pedido nesta simulação."
SYNTHETIC_LABEL = "[SIMULAÇÃO — DADOS FICTÍCIOS] "


class ResponseGenerator:
    def render(self, detection: Detection, knowledge: KnowledgeBase) -> str:
        if Intent.HUMAN in detection.intents:
            reply = (
                "Registrei o pedido nesta simulação e pausei as respostas automáticas. "
                "Nenhum atendente real foi notificado."
            )
        else:
            # Agendamento tem prioridade; demais intenções mantêm ordem estável.
            selected = [i for i in detection.intents if i != Intent.GREETING]
            if Intent.BOOKING in selected:
                selected.remove(Intent.BOOKING)
                selected.insert(0, Intent.BOOKING)
            if not selected:
                selected = [Intent.GREETING]
            fragments = [self._answer(i, detection, knowledge) for i in selected[:3]]
            corrected = [s.name for s in knowledge.services if s.id in detection.service_ids]
            if detection.spelling_corrected and corrected:
                # Transparência: o serviço foi reconhecido por correção ortográfica.
                fragments.insert(0, f"Entendi que você se refere a: {', '.join(corrected[:3])}.")
            if len(selected) > 3:
                fragments.append("Vamos detalhar as demais dúvidas uma por vez.")
            reply = " ".join(fragments)
        return (SYNTHETIC_LABEL if knowledge.synthetic else "[SIMULAÇÃO LOCAL] ") + reply

    def _answer(self, intent: Intent, detection: Detection, knowledge: KnowledgeBase) -> str:
        services = [s for s in knowledge.services if s.id in detection.service_ids]
        if intent == Intent.GREETING:
            name = f" de {knowledge.name}" if knowledge.name else ""
            return f"Olá! Sou o assistente virtual{name}, em simulação determinística. Como posso ajudar?"
        if intent == Intent.BOOKING:
            link = knowledge.booking_link()
            if link:
                return (
                    f"O agendamento deve ser realizado por você no Booksy: {link} . "
                    "Não consultei disponibilidade nem realizei ou confirmei reserva."
                )
            return (
                "O agendamento online ainda não está disponível pelo agente: não há link "
                f"Booksy válido configurado. {HELP}"
            )
        if intent == Intent.SERVICES:
            if services:
                return " ".join(
                    f"{s.name}: {s.description or 'sem descrição cadastrada.'}" for s in services[:3]
                )
            if detection.catalog_requested and knowledge.services:
                names = ", ".join(s.name for s in knowledge.services[:5])
                return f"Serviços cadastrados nesta base: {names}. Qual deseja consultar?"
            return f"Não tenho informação confirmada sobre esse serviço. {HELP}"
        if intent == Intent.PRICE:
            if services:
                return " ".join(self._price(service) for service in services[:3])
            if detection.use_context:
                return "De qual serviço você quer saber o preço? Preciso que indique um serviço."
            return f"Não tenho preço confirmado para o serviço informado. Qual serviço deseja consultar? {HELP}"
        if intent == Intent.HOURS:
            return self._known(knowledge.opening_hours, "horário de funcionamento")
        if intent == Intent.LOCATION:
            return self._known(knowledge.address, "endereço")
        if intent == Intent.PROFESSIONALS:
            return self._known(", ".join(knowledge.professionals[:3]), "profissionais")
        if intent == Intent.POLICY:
            return self._known(" ".join(knowledge.policies[:3]), "políticas de atendimento")
        if intent == Intent.CANCELLATION_POLICY:
            return self._known(" ".join(knowledge.cancellation_policies[:3]), "política de cancelamento")
        if intent == Intent.PROMOTION:
            # Não há registro aprovado de promoções: nunca afirmar nem negar oferta.
            return f"Não tenho informação confirmada sobre promoções. {HELP}"
        if intent == Intent.FAQ and detection.faq_index is not None:
            return knowledge.faqs[detection.faq_index].answer
        return f"Não compreendi com segurança. Pode reformular sua dúvida? {HELP}"

    @staticmethod
    def _known(value: str | None, label: str) -> str:
        return value if value and value.strip() else f"Não tenho informação confirmada sobre {label}. {HELP}"

    @staticmethod
    def _price(service: Service) -> str:
        if service.price is None:
            return f"Não tenho preço confirmado para {service.name}. {HELP}"
        price = f"{service.price:.2f}".replace(".", ",")
        note = f" {service.price_note}" if service.price_note else ""
        return f"{service.name}: R$ {price}.{note}"
