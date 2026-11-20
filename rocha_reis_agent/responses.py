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
                    f"{s.name}: {s.description or 'sem descrição cadastrada.'}{self._details(s, knowledge)}"
                    for s in services[:3]
                )
            if detection.catalog_requested and knowledge.services:
                return f"Serviços cadastrados nesta base: {self._catalog(knowledge)}. Qual deseja consultar?"
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
            with_team = [s for s in services if s.professional_ids]
            if with_team:
                return " ".join(f"{s.name} é realizado por: {self._team(s, knowledge)}." for s in with_team[:3])
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
    def _money(value: object) -> str:
        return "R$ " + f"{value:.2f}".replace(".", ",")

    @classmethod
    def _price(cls, service: Service) -> str:
        note = f" {service.price_note}" if service.price_note else ""
        if service.price_kind == "on_evaluation":
            return f"{service.name}: o valor é definido após avaliação presencial.{note}"
        if service.price is None:
            return f"Não tenho preço confirmado para {service.name}. {HELP}"
        if service.price_kind == "starting":
            return f"{service.name}: a partir de {cls._money(service.price)} (o valor final pode variar).{note}"
        if service.price_kind == "range" and service.price_max is not None:
            return f"{service.name}: de {cls._money(service.price)} a {cls._money(service.price_max)}.{note}"
        return f"{service.name}: {cls._money(service.price)}.{note}"

    @staticmethod
    def _minutes(value: int) -> str:
        hours, minutes = divmod(value, 60)
        if not hours:
            return f"{minutes} min"
        return f"{hours}h{minutes:02d}" if minutes else f"{hours}h"

    @staticmethod
    def _team(service: Service, knowledge: KnowledgeBase) -> str:
        names = {p.id: p.name for p in knowledge.team if p.active}
        return ", ".join(names[p] for p in service.professional_ids if p in names)

    @classmethod
    def _details(cls, service: Service, knowledge: KnowledgeBase) -> str:
        """Complementos aprovados; vazio quando o serviço não os tem (texto antigo preservado)."""
        parts = []
        category = next((c.name for c in knowledge.categories if c.id == service.category_id and c.active), None)
        if category:
            parts.append(f"Categoria: {category}.")
        if service.duration is not None:
            low, high = service.duration.min_minutes, service.duration.max_minutes
            span = (f"entre {cls._minutes(low)} e {cls._minutes(high)}" if high
                    else f"cerca de {cls._minutes(low)}")
            parts.append(f"Duração estimada: {span} (pode variar).")
        team = cls._team(service, knowledge)
        if team:
            parts.append(f"Realizado por: {team}.")
        return (" " + " ".join(parts)) if parts else ""

    @staticmethod
    def _catalog(knowledge: KnowledgeBase) -> str:
        active = {c.id: c.name for c in knowledge.categories if c.active}
        if not any(s.category_id in active for s in knowledge.services):
            return ", ".join(s.name for s in knowledge.services[:5])
        groups: dict[str, list[str]] = {}
        for service in knowledge.services[:12]:
            groups.setdefault(active.get(service.category_id or "", "Outros serviços"), []).append(service.name)
        return "; ".join(f"{label}: {', '.join(names)}" for label, names in groups.items())
