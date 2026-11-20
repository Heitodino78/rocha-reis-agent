"""Projeção autorizada para manter detector e templates da Fase 1."""

from dataclasses import dataclass
from datetime import datetime, timezone

from rocha_reis_agent.domain.contracts import Detection, Intent, SimulationError
from rocha_reis_agent.knowledge import KnowledgeBase
from rocha_reis_agent.knowledge_catalog import (
    Approval, BooksyFact, CategoryFact, FAQFact, FactReference, FactRevision, KnowledgeCatalog,
    KnowledgeScope, ListFact, PriceFact, ProfessionalFact, ServiceFact, TextFact,
)


@dataclass(frozen=True)
class AuthorizedKnowledge:
    base: KnowledgeBase
    facts: tuple[FactRevision, ...]

    def references_for(self, detection: Detection) -> tuple[FactReference, ...]:
        if Intent.HUMAN in detection.intents:
            return ()
        requested = {
            Intent.GREETING: {"identity"}, Intent.SERVICES: {"service"},
            Intent.PRICE: {"service", "price"}, Intent.HOURS: {"hours"},
            Intent.LOCATION: {"location"}, Intent.PROFESSIONALS: {"professionals"},
            Intent.POLICY: {"policy"}, Intent.CANCELLATION_POLICY: {"cancellation"},
            Intent.BOOKING: {"booksy"},
            Intent.FAQ: {"faq"},
        }
        intents = tuple(i for i in detection.intents if i != Intent.GREETING or len(detection.intents) == 1)
        kinds = set().union(*(requested.get(i, set()) for i in intents))
        references = []
        for fact in self.facts:
            if fact.data.kind not in kinds:
                continue
            if isinstance(fact.data, (ServiceFact, PriceFact)):
                service_id = fact.data.service.id if isinstance(fact.data, ServiceFact) else fact.data.service_id
                if service_id not in detection.service_ids and not detection.catalog_requested:
                    continue
            if isinstance(fact.data, FAQFact):
                if detection.faq_index is None or fact.data.faq != self.base.faqs[detection.faq_index]:
                    continue
            references.append(fact.reference)
        # Fase 12: categoria e profissionais citados junto do serviço também são proveniência.
        mentioned = [s for s in self.base.services if s.id in detection.service_ids]
        wanted: set[tuple[str, str]] = set()
        if kinds & {"service", "professionals"}:
            wanted |= {("category", s.category_id) for s in mentioned if s.category_id}
            wanted |= {("professional", p) for s in mentioned for p in s.professional_ids}
        if "professionals" in kinds and not mentioned:
            wanted |= {("professional", p.id) for p in self.base.team if p.active}
        for fact in self.facts:
            if isinstance(fact.data, CategoryFact) and ("category", fact.data.category.id) in wanted:
                references.append(fact.reference)
            elif isinstance(fact.data, ProfessionalFact) and ("professional", fact.data.professional.id) in wanted:
                references.append(fact.reference)
        return tuple(references)


def authorize_catalog(catalog: KnowledgeCatalog) -> AuthorizedKnowledge:
    # Revalidar até instâncias copiadas sem validação; nada não aprovado passa.
    catalog = KnowledgeCatalog.model_validate(catalog.model_dump(mode="python"))
    facts = catalog.active()
    return project_active_facts(catalog.scope, facts)


def project_active_facts(
    scope: KnowledgeScope, facts: tuple[FactRevision, ...]
) -> AuthorizedKnowledge:
    """Projeção interna; o chamador deve autorizar o snapshot antes de usar."""
    values: dict[str, object] = {"synthetic": scope == KnowledgeScope.SYNTHETIC}
    services = {}
    prices = {}
    faqs = []
    policies = []
    categories = []
    team = []
    for fact in facts:
        data = fact.data
        if isinstance(data, TextFact):
            values[{"identity": "name", "location": "address", "hours": "opening_hours"}[data.kind]] = data.text
        elif isinstance(data, ServiceFact):
            services[data.service.id] = data.service
        elif isinstance(data, PriceFact):
            prices[data.service_id] = data
        elif isinstance(data, ListFact):
            if data.kind == "professionals":
                values["professionals"] = data.items
            elif data.kind == "cancellation":
                values["cancellation_policies"] = data.items
            else:
                policies.extend(data.items)
        elif isinstance(data, FAQFact):
            faqs.append(data.faq)
        elif isinstance(data, BooksyFact):
            values["booksy_url"] = data.url
        elif isinstance(data, CategoryFact):
            categories.append(data.category)
        elif isinstance(data, ProfessionalFact):
            team.append(data.professional)
    active_team = {p.id for p in team if p.active}
    active_categories = {c.id for c in categories if c.active}

    def visible(service):  # type: ignore[no-untyped-def]
        # Referências a categoria/profissional inativo ou não aprovado não chegam ao atendimento.
        update: dict[str, object] = {
            "category_id": service.category_id if service.category_id in active_categories else None,
            "professional_ids": tuple(p for p in service.professional_ids if p in active_team),
        }
        if service.id in prices:
            price = prices[service.id]
            update.update(price=price.amount, price_note=price.note, price_kind=price.pricing,
                          price_max=price.amount_max)
        return service.model_copy(update=update)

    values["services"] = tuple(visible(service) for service in services.values())
    values["categories"] = tuple(categories)
    values["team"] = tuple(team)
    # Nomes de profissionais ATIVOS também entram na lista simples (usada pela validação da IA
    # para impedir que o modelo cite pessoas, e pelo oráculo de avaliação).
    names = list(values.get("professionals", ()))  # type: ignore[arg-type]
    names += [p.name for p in team if p.active and p.name not in names]
    values["professionals"] = tuple(names)
    values["policies"] = tuple(policies)
    values["faqs"] = tuple(faqs)
    return AuthorizedKnowledge(KnowledgeBase.model_validate(values), facts)


def legacy_synthetic_catalog(base: KnowledgeBase) -> KnowledgeCatalog:
    """Compatibilidade v1 só para fixtures, sem promover dados planos a oficiais."""
    if not base.synthetic:
        if any((base.name, base.address, base.opening_hours, base.services, base.professionals, base.policies, base.cancellation_policies, base.booksy_url, base.faqs)):
            raise SimulationError("Dados oficiais exigem catálogo versionado e aprovação explícita.")
        return KnowledgeCatalog(scope=KnowledgeScope.OFFICIAL)
    entries = []
    for kind, value in (("identity", base.name), ("location", base.address), ("hours", base.opening_hours)):
        if value:
            entries.append((kind, TextFact(kind=kind, text=value)))
    for service in base.services:
        entries.append((f"service:{service.id}", ServiceFact(service=service.model_copy(update={"price": None, "price_note": None}))))
        if service.price is not None:
            entries.append((f"price:{service.id}", PriceFact(service_id=service.id, amount=service.price, note=service.price_note)))
    for kind, items in (("professionals", base.professionals), ("policy", base.policies), ("cancellation", base.cancellation_policies)):
        if items:
            entries.append((kind, ListFact(kind=kind, items=items)))
    if base.booksy_url:
        entries.append(("booksy", BooksyFact(url=base.booksy_url)))
    entries.extend((f"faq:{index}", FAQFact(faq=faq)) for index, faq in enumerate(base.faqs))
    return KnowledgeCatalog(scope=KnowledgeScope.SYNTHETIC, revisions=tuple(
        FactRevision(
            id=key, version=1, scope=KnowledgeScope.SYNTHETIC, status=Approval.APPROVED,
            source="Fixture sintética v1; adaptação local para compatibilidade",
            reviewed_by="revisão fictícia da fixture; não é aprovação do salão",
            updated_at=datetime(2026, 11, 19, tzinfo=timezone.utc), data=data,
        ) for key, data in entries
    ))


def authorize_knowledge(value: KnowledgeBase | KnowledgeCatalog) -> AuthorizedKnowledge:
    return authorize_catalog(value if isinstance(value, KnowledgeCatalog) else legacy_synthetic_catalog(value))
