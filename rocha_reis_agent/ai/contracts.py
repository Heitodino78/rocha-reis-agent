"""Fronteira tipada: propostas do provedor não são comandos da aplicação."""

from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rocha_reis_agent.domain.contracts import Intent
from rocha_reis_agent.knowledge_catalog import FactData, FactReference, KnowledgeScope


class AIProviderError(RuntimeError):
    """Falha de provedor; detalhes nunca devem ser expostos ao cliente."""


class AIProviderTimeout(AIProviderError, TimeoutError):
    pass


class AIValidationError(ValueError):
    """Proposta sem autorização estrutural, factual ou de política."""


class AIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class UntrustedText(AIModel):
    role: Literal["client"] = "client"
    text: str = Field(min_length=1, max_length=2000, repr=False)
    trust: Literal["untrusted-client"] = "untrusted-client"


class AuthorizedFact(AIModel):
    reference: FactReference
    data: FactData = Field(repr=False)
    authority: Literal["content-only"] = "content-only"


class AIRequest(AIModel):
    message: UntrustedText = Field(repr=False)
    service_context: tuple[str, ...] = Field(max_length=20)
    history: tuple[UntrustedText, ...] = Field(max_length=10, repr=False)
    facts: tuple[AuthorizedFact, ...] = Field(max_length=128, repr=False)
    scope: KnowledgeScope
    prompt_version: str = Field(pattern=r"^v[1-9][0-9]*$")
    objective: str = Field(max_length=240, repr=False)
    system_instructions: tuple[str, ...] = Field(min_length=1, max_length=16, repr=False)
    output_contract: Literal["ai-response-v1"] = "ai-response-v1"
    backend_policy: Literal["backend-v1"] = "backend-v1"

    @model_validator(mode="after")
    def bounded_request(self) -> "AIRequest":
        if len(self.model_dump_json()) > 64000 or any(len(h.text) > 500 for h in self.history):
            raise ValueError("Contexto excede o limite autorizado.")
        keys = [fact.reference.key for fact in self.facts]
        if len(keys) != len(set(keys)):
            raise ValueError("Referências repetidas no contexto.")
        return self


class AIResponse(AIModel):
    intents: tuple[Intent, ...] = Field(min_length=1, max_length=12)
    service_ids: tuple[Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")], ...] = Field(max_length=20)
    use_context: bool = Field(strict=True)
    catalog_requested: bool = Field(strict=True)
    faq_reference: FactReference | None
    fact_references: tuple[FactReference, ...] = Field(max_length=128)
    candidate_reply: str = Field(min_length=1, max_length=8192, repr=False)
    needs_clarification: bool = Field(strict=True)
    human_handoff_suggested: bool = Field(strict=True)


class AIProvider(Protocol):
    def generate(
        self, request: AIRequest, *, timeout_seconds: float
    ) -> AIResponse | dict[str, object] | str:
        """Retornar proposta ou falhar; adaptadores reais devem impor o timeout."""
        ...
