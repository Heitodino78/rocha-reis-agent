"""Esquema de conhecimento e leitura local exclusiva de fixtures sintéticas."""

from decimal import Decimal
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from rocha_reis_agent.domain.contracts import SimulationError

Text = Annotated[str, Field(min_length=1, max_length=240)]


class Record(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, hide_input_in_errors=True, str_strip_whitespace=True
    )

    @field_validator("*", mode="after")
    @classmethod
    def printable_values(cls, value: object) -> object:
        items = value if isinstance(value, tuple) else (value,)
        for item in items:
            if isinstance(item, str) and any(unicodedata.category(c).startswith("C") for c in item):
                raise ValueError("Conhecimento contém caracteres de controle não permitidos.")
        return value


class Service(Record):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    name: Text
    aliases: tuple[Text, ...] = Field(default=(), max_length=10)
    description: Text | None = None
    price: Decimal | None = Field(default=None, ge=0, max_digits=8, decimal_places=2)
    price_note: Text | None = None


class FAQ(Record):
    questions: tuple[Text, ...] = Field(min_length=1, max_length=10)
    answer: Text


class KnowledgeBase(Record):
    schema_version: int = Field(default=1, strict=True)
    synthetic: bool = Field(default=False, strict=True)
    name: Text | None = None
    address: Text | None = None
    opening_hours: Text | None = None
    services: tuple[Service, ...] = Field(default=(), max_length=20)
    professionals: tuple[Text, ...] = Field(default=(), max_length=10)
    policies: tuple[Text, ...] = Field(default=(), max_length=10)
    cancellation_policies: tuple[Text, ...] = Field(default=(), max_length=10)
    booksy_url: str | None = Field(default=None, max_length=2048)
    faqs: tuple[FAQ, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def unique_services(self) -> "KnowledgeBase":
        if self.schema_version != 1 or len({s.id for s in self.services}) != len(self.services):
            raise ValueError("Versão de esquema ou identificadores de serviços inválidos.")
        return self

    def booking_link(self) -> str | None:
        """Validar formato/domínio sem fazer requisição ou validar o perfil real."""
        value = self.booksy_url
        if not value or any(not c.isprintable() or c.isspace() or c in "\\<>\"'" for c in value):
            return None
        try:
            parsed = urlsplit(value)
            host = parsed.hostname or ""
            allowed = host == "booksy.com" or host.endswith(".booksy.com")
            if self.synthetic:
                allowed = allowed or host == "example.invalid" or host.endswith(".example.invalid")
            if (
                parsed.scheme != "https" or not allowed or parsed.username or parsed.password
                or parsed.port not in (None, 443) or not parsed.path.strip("/")
                or parsed.fragment
            ):
                return None
        except ValueError:
            return None
        return value


def load_synthetic_knowledge(path: Path) -> KnowledgeBase:
    try:
        if path.stat().st_size > 100_000:
            raise ValueError("Fixture muito grande.")
        knowledge = KnowledgeBase.model_validate_json(path.read_text(encoding="utf-8"))
        if not knowledge.synthetic:
            raise ValueError("Somente fixtures sintéticas estão autorizadas nesta fase.")
        return knowledge
    except (OSError, UnicodeError, ValidationError, ValueError):
        raise SimulationError(
            "Não foi possível carregar a base: use JSON sintético válido, schema_version=1, "
            "synthetic=true e até 100 KB."
        ) from None
