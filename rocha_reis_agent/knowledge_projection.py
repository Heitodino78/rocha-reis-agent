"""Projeção autorizada para manter detector e templates da Fase 1."""

from dataclasses import dataclass
from datetime import datetime, timezone

from rocha_reis_agent.domain.contracts import Detection, Intent, SimulationError
from rocha_reis_agent.knowledge import KnowledgeBase
from rocha_reis_agent.knowledge_catalog import (
    Approval, BooksyFact, FAQFact, FactReference, FactRevision, KnowledgeCatalog,
    KnowledgeScope, ListFact, PriceFact, ServiceFact, TextFact,
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
    values["services"] = tuple(
        service.model_copy(update={"price": prices[key].amount, "price_note": prices[key].note})
        if key in prices else service for key, service in services.items()
    )
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
