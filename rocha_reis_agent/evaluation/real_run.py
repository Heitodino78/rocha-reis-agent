"""Fase 9B — guarda da avaliação com Google Gemini REAL. PREPARADA, NÃO AUTORIZADA.

Uma avaliação paga só pode começar quando TODAS as condições abaixo valem:

1. Trava de código `GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED = True` (config.py), em commit
   revisado. Hoje é False: `check_real_run` recusa antes de ler qualquer outra coisa.
2. Arquivo de autorização privado (0600, sem link simbólico, fora do repositório) com
   responsável, executor, conta Google e projeto, provedor, modelos, orçamento, nº máximo
   de requisições (total e por modelo) e de
   cenários, período (≤ 7 dias), tipo de dado (só sintético), armazenamento e
   procedimento de interrupção, mais a frase de confirmação.
3. Modelo pedido consta da autorização E do registro verificado (ai/models.json).
4. Chave GEMINI_API_KEY fornecida pelo ambiente/gerenciador de segredos — nunca por chat
   ou Git. A conta deve ser PAGA (projeto com faturamento): no nível gratuito o conteúdo é
   usado para melhorar produtos do Google.
   A existência de uma chave NÃO é autorização.

O resultado é um `RunBudget` com os tetos autorizados, compartilhado por todas as
chamadas da execução: atingido o teto, as chamadas seguintes são recusadas ANTES de
sair da máquina (BudgetExceeded) e a conversa vai para a falha segura.
"""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import stat
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from rocha_reis_agent import config as config_module
from rocha_reis_agent.ai.model_registry import ModelNotSupported, load_registry

CONFIRMATION = "AUTORIZO AVALIACAO SINTETICA COM GEMINI REAL DENTRO DOS LIMITES DESTE ARQUIVO"
MAX_PERIOD = timedelta(days=7)
# Tetos rígidos: uma autorização acima disso é inválida (exige nova revisão do código).
HARD_MAX_COST_USD = 50.0
HARD_MAX_REQUESTS = 2000


class RealRunRefused(RuntimeError):
    """Avaliação real recusada; a mensagem nunca contém segredos."""


class RealRunAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    schema_version: Literal[1]
    authorized_by: str = Field(min_length=3, max_length=120)
    executor: str = Field(min_length=3, max_length=120)
    provider: Literal["gemini"]
    google_account: str = Field(min_length=3, max_length=120)
    project_id: str = Field(pattern=r"^[a-z][a-z0-9-]{4,61}[a-z0-9]$")
    billing: Literal["paid_cloud_project"]
    models: tuple[str, ...] = Field(min_length=1, max_length=5)
    per_model_max_requests: dict[str, int] = Field(default_factory=dict)
    max_cost_usd: float = Field(gt=0, le=HARD_MAX_COST_USD)
    max_requests: int = Field(ge=1, le=HARD_MAX_REQUESTS)
    max_scenarios: int = Field(ge=1, le=1000)
    valid_from: datetime
    valid_until: datetime
    data_type: Literal["synthetic_only"]
    storage: str = Field(min_length=3, max_length=240)
    stop_procedure: str = Field(min_length=3, max_length=240)
    confirmation: str

    @model_validator(mode="after")
    def coherent(self) -> "RealRunAuthorization":
        if self.confirmation != CONFIRMATION:
            raise ValueError("Frase de confirmação ausente ou diferente.")
        if self.valid_from.utcoffset() is None or self.valid_until.utcoffset() is None:
            raise ValueError("Período exige fuso horário.")
        if not timedelta(0) < self.valid_until - self.valid_from <= MAX_PERIOD:
            raise ValueError("Período inválido ou maior que 7 dias.")
        if not set(self.per_model_max_requests) <= set(self.models) or any(
                not 1 <= n <= self.max_requests for n in self.per_model_max_requests.values()):
            raise ValueError("Limites por modelo incoerentes com a autorização.")
        return self


def load_authorization(path: Path) -> RealRunAuthorization:
    try:
        info = os.lstat(path)
    except OSError:
        raise RealRunRefused("Arquivo de autorização inexistente ou inacessível.") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RealRunRefused("Autorização deve ser arquivo regular, sem link simbólico.")
    if info.st_mode & 0o077:
        raise RealRunRefused("Autorização deve ter permissão 0600.")
    try:
        return RealRunAuthorization.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError):
        raise RealRunRefused("Arquivo de autorização inválido.") from None


def check_real_run(path: Path | None, *, model: str, scenario_count: int,
                   now: datetime | None = None) -> RealRunAuthorization:
    """Todas as condições da Fase 9B, na ordem; a primeira que falhar recusa a execução."""
    if not config_module.GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED:
        raise RealRunRefused("Provedor de IA real (Gemini) bloqueado pela trava de código (Fase 9B não autorizada).")
    if path is None:
        raise RealRunRefused("Avaliação real exige --authorization com arquivo privado.")
    authorization = load_authorization(path)
    moment = now or datetime.now(timezone.utc)
    if not authorization.valid_from <= moment <= authorization.valid_until:
        raise RealRunRefused("Fora do período autorizado.")
    if model not in authorization.models:
        raise RealRunRefused("Modelo não autorizado para esta avaliação.")
    try:
        spec = load_registry().spec(model)
    except ModelNotSupported:
        spec = None
    if spec is None or spec.lifecycle != "stable" or not spec.structured_outputs:
        raise RealRunRefused("Modelo fora do registro verificado ou incompatível com o contrato.")
    if scenario_count > authorization.max_scenarios:
        raise RealRunRefused("Quantidade de cenários acima do autorizado.")
    return authorization
