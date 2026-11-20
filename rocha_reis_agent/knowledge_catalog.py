"""Revisões imutáveis e aprovação explícita, sem banco ou dados reais embutidos."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from rocha_reis_agent.knowledge import FAQ, Category, Pricing, Professional, Record, Service, Text
from rocha_reis_agent.domain.contracts import normalize_for_matching


class Approval(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ARCHIVED = "ARCHIVED"


class KnowledgeScope(StrEnum):
    SYNTHETIC = "SYNTHETIC"
    OFFICIAL = "OFFICIAL"


class TextFact(Record):
    kind: Literal["identity", "location", "hours"]
    text: Text


class ServiceFact(Record):
    kind: Literal["service"] = "service"
    service: Service

    @model_validator(mode="after")
    def separate_price(self) -> "ServiceFact":
        service = self.service
        if (service.price is not None or service.price_note is not None or service.price_kind is not None
                or service.price_max is not None):
            raise ValueError("Preço exige registro e aprovação próprios.")
        return self


class PriceFact(Record):
    kind: Literal["price"] = "price"
    service_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    # Fase 12: documentos antigos (sem `pricing`) continuam sendo preço FIXO, como antes.
    pricing: Pricing = "fixed"
    amount: Decimal | None = Field(default=None, ge=0, max_digits=8, decimal_places=2)
    amount_max: Decimal | None = Field(default=None, ge=0, max_digits=8, decimal_places=2)
    note: Text | None = None

    @model_validator(mode="after")
    def consistent(self) -> "PriceFact":
        if self.pricing == "on_evaluation":
            if self.amount is not None or self.amount_max is not None:
                raise ValueError("Preço mediante avaliação não tem valor.")
        elif self.amount is None:
            raise ValueError("Informe o valor.")
        elif self.pricing == "range":
            if self.amount_max is None or self.amount_max <= self.amount:
                raise ValueError("Faixa de preço exige valor máximo maior que o mínimo.")
        elif self.amount_max is not None:
            raise ValueError("Valor máximo só existe em faixa de preço.")
        return self


class CategoryFact(Record):
    kind: Literal["category"] = "category"
    category: Category


class ProfessionalFact(Record):
    kind: Literal["professional"] = "professional"
    professional: Professional


class ListFact(Record):
    kind: Literal["professionals", "policy", "cancellation"]
    items: tuple[Text, ...] = Field(min_length=1, max_length=10)


class FAQFact(Record):
    kind: Literal["faq"] = "faq"
    faq: FAQ


class BooksyFact(Record):
    kind: Literal["booksy"] = "booksy"
    url: str = Field(min_length=1, max_length=2048)


FactData = Annotated[
    TextFact | ServiceFact | PriceFact | ListFact | FAQFact | BooksyFact | CategoryFact | ProfessionalFact,
    Field(discriminator="kind"),
]


class FactReference(Record):
    id: str = Field(pattern=r"^[a-z][a-z0-9_.:-]{0,79}$")
    version: int = Field(ge=1, strict=True)

    @property
    def key(self) -> str:
        return f"{self.id}@{self.version}"


class FactRevision(Record):
    id: str = Field(pattern=r"^[a-z][a-z0-9_.:-]{0,79}$")
    version: int = Field(ge=1, strict=True)
    replaces: int | None = Field(default=None, ge=1, strict=True)
    scope: KnowledgeScope
    status: Approval
    source: Text
    updated_at: datetime
    reviewed_by: Text | None = None
    data: FactData

    @field_validator("updated_at")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("Data da revisão deve ter fuso horário.")
        return value

    @model_validator(mode="after")
    def valid_revision(self) -> "FactRevision":
        if self.replaces != (self.version - 1 if self.version > 1 else None):
            raise ValueError("Revisão deve indicar a versão anterior exata.")
        if self.status == Approval.APPROVED and not self.reviewed_by:
            raise ValueError("Aprovação exige identificação de revisão.")
        return self

    @property
    def reference(self) -> FactReference:
        return FactReference(id=self.id, version=self.version)

    @property
    def semantic_key(self) -> tuple[str, str]:
        if isinstance(self.data, ServiceFact):
            return ("service", self.data.service.id)
        if isinstance(self.data, PriceFact):
            return ("price", self.data.service_id)
        if isinstance(self.data, FAQFact):
            return ("faq", self.id)
        if isinstance(self.data, CategoryFact):
            return ("category", self.data.category.id)
        if isinstance(self.data, ProfessionalFact):
            return ("professional", self.data.professional.id)
        return (self.data.kind, "singleton")


class KnowledgeCatalog(Record):
    schema_version: Literal[2] = 2
    scope: KnowledgeScope
    revisions: tuple[FactRevision, ...] = Field(default=(), max_length=1024)

    @model_validator(mode="after")
    def validate_history(self) -> "KnowledgeCatalog":
        grouped: dict[str, list[FactRevision]] = {}
        for revision in self.revisions:
            if revision.scope != self.scope:
                raise ValueError("Não misturar dados sintéticos e oficiais.")
            grouped.setdefault(revision.id, []).append(revision)
        for history in grouped.values():
            history.sort(key=lambda r: r.version)
            if [r.version for r in history] != list(range(1, len(history) + 1)):
                raise ValueError("Versões duplicadas ou lacuna no histórico.")
            if len({r.semantic_key for r in history}) != 1:
                raise ValueError("Um ID não pode mudar de categoria ou dado representado.")
            if any(b.updated_at < a.updated_at for a, b in zip(history, history[1:])):
                raise ValueError("Datas das revisões devem ser monotônicas.")
        active = self.active()
        if len(active) > 128 or len({r.semantic_key for r in active}) != len(active):
            raise ValueError("Conflito entre dados ativos ou excesso de registros ativos.")
        questions = [normalize_for_matching(q) for r in active if isinstance(r.data, FAQFact) for q in r.data.faq.questions]
        if len(questions) != len(set(questions)):
            raise ValueError("FAQs ativas não podem ter a mesma pergunta.")
        return self

    def active(self) -> tuple[FactRevision, ...]:
        latest: dict[str, FactRevision] = {}
        for revision in self.revisions:
            if revision.id not in latest or revision.version > latest[revision.id].version:
                latest[revision.id] = revision
        # Conservador: uma nova revisão pendente também desativa a anterior.
        return tuple(latest[key] for key in sorted(latest) if latest[key].status == Approval.APPROVED)

    def append(self, revision: FactRevision) -> "KnowledgeCatalog":
        """Autoria local confiável: retornar novo catálogo sem apagar revisões."""
        return KnowledgeCatalog.model_validate({
            "scope": self.scope,
            "revisions": [r.model_dump() for r in (*self.revisions, revision)],
        })
