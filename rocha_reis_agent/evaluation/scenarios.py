"""Formato dos cenários de avaliação (JSON versionado em evals/)."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from rocha_reis_agent.domain.contracts import Intent, SimulationError

CATEGORIES = {
    "A": "Saudações", "B": "Perguntas frequentes", "C": "Consultas de preço", "D": "Informações ausentes",
    "E": "Agendamentos", "F": "Contexto entre mensagens", "G": "Múltiplas intenções",
    "H": "Solicitação de atendimento humano", "I": "Mensagens ambíguas", "J": "Linguagem informal",
    "K": "Erros de digitação", "L": "Reclamações", "M": "Mensagens adversariais",
    "N": "Tentativas de vazamento de dados", "O": "Conversas longas", "P": "Isolamento entre clientes",
    "Q": "Falhas de IA", "R": "Saídas inválidas", "S": "Falhas durante o processamento",
    "T": "Corridas com atendimento humano",
}

# Modos que forçam um comportamento do provedor no cenário inteiro (Q e R).
ProviderMode = Literal[
    "normal", "failure", "timeout", "invalid_json", "empty", "extra_field", "unknown_reference",
    "swap_service", "fake_price_preface", "promise_preface", "leak_preface",
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Expect(_Model):
    """Expectativas de um turno. Campos ausentes não são verificados."""

    intents_all: tuple[Intent, ...] = ()
    intents_any: tuple[Intent, ...] = ()
    state: Literal["AUTO", "WAITING_HUMAN", "HUMAN_ACTIVE", "RESOLVED"] | None = None
    reply: Literal["present", "none"] | None = None
    contains: tuple[str, ...] = ()
    # Conteúdo proibido: verificação de SEGURANÇA (vale inclusive com modelo hostil).
    not_contains: tuple[str, ...] = ()
    booksy_link: bool | None = None
    missing_info: bool | None = None
    duplicate: bool | None = None


class Turn(_Model):
    client: str = Field(default="c1", pattern=r"^[a-z0-9-]{1,32}$")
    text: str | None = Field(default=None, min_length=1, max_length=2000)
    # Ação administrativa simulada ANTES da mensagem (CLI local; não é autenticação).
    operator: Literal["assume", "resolve", "resume"] | None = None
    # Reenvia o mesmo evento do turno anterior deste cliente (deduplicação).
    repeat_previous_event: bool = False
    # Corridas: executadas DURANTE a chamada ao provedor, por outro repositório/processo lógico.
    takeover_during_generation: bool = False
    knowledge_change_during_generation: bool = False
    expect: Expect = Expect()

    @model_validator(mode="after")
    def one_action(self) -> "Turn":
        if (self.text is None) == (self.operator is None) and not self.repeat_previous_event:
            raise ValueError("Cada turno tem exatamente uma mensagem OU uma ação de operador.")
        return self


class Scenario(_Model):
    id: str = Field(pattern=r"^[A-T][0-9]{1,3}$")
    category: str = Field(pattern=r"^[A-T]$")
    title: str = Field(min_length=1, max_length=160)
    # Crítico: segurança ou regra de negócio; exige zero violações.
    critical: bool = False
    catalog: Literal["default", "no_booksy"] = "default"
    provider_mode: ProviderMode = "normal"
    turns: tuple[Turn, ...] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def category_matches(self) -> "Scenario":
        if self.id[0] != self.category:
            raise ValueError("ID deve começar pela categoria.")
        return self


class ScenarioSet(_Model):
    schema_version: Literal[1] = 1
    synthetic: Literal[True] = True
    description: str
    scenarios: tuple[Scenario, ...] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_ids(self) -> "ScenarioSet":
        ids = [s.id for s in self.scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("IDs de cenário repetidos.")
        return self


def load_scenarios(path: Path) -> ScenarioSet:
    try:
        return ScenarioSet.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError):
        raise SimulationError("Arquivo de cenários inválido ou indisponível.") from None
