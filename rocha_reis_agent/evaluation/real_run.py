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
ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_FILE = ROOT / "evals" / "experiment_9c.json"
MAX_PERIOD = timedelta(days=7)
# Tetos rígidos: uma autorização acima disso é inválida (exige nova revisão do código).
HARD_MAX_COST_USD = 50.0
HARD_MAX_REQUESTS = 2000


class RealRunRefused(RuntimeError):
    """Avaliação real recusada; a mensagem nunca contém segredos."""


class RealRunAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    # Fase 9C-B: schema 3 — experimento pré-registrado, livro-razão persistente DEDICADO (cumulativo
    # entre etapas e autorizações), interrupção, preços confirmados e declarações explícitas.
    schema_version: Literal[3]
    experiment_id: str = Field(pattern=r"^[a-z0-9-]{3,64}$")
    stages: tuple[Literal["smoke", "pilot", "e2e", "full", "repeat"], ...] = Field(min_length=1)
    # Consumo de TODAS as execuções do período (fora do repositório, *.synthetic.sqlite3, 0600 em dir 0700).
    usage_ledger: Path
    # Existindo este arquivo, nenhuma chamada nova sai (interrupção emergencial).
    stop_file: Path
    # Preços conferidos pelo responsável na data da autorização: {modelo: {"input": x, "output": y}}.
    # Divergência com o registro verificado (ai/models.json) na data da execução → recusa.
    confirmed_pricing: dict[str, dict[str, float]]
    meta_remains_disabled: Literal[True]
    # Controles de faturamento do Google configurados (ex.: orçamento/alerta) — descrição livre.
    google_billing_controls: str = Field(min_length=3, max_length=240)
    # Alertas de faturamento NÃO interrompem gastos: aceitação explícita do risco residual.
    residual_billing_risk_accepted: Literal[True]
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
        for path in (self.usage_ledger, self.stop_file):
            if not path.is_absolute() or ROOT in path.resolve().parents:
                raise ValueError("Livro-razão e arquivo de interrupção ficam FORA do repositório, com caminho absoluto.")
        if not self.usage_ledger.name.endswith(".synthetic.sqlite3"):
            raise ValueError("Livro-razão exige nome *.synthetic.sqlite3.")
        if set(self.confirmed_pricing) != set(self.models) or any(
                set(p) != {"input", "output"} for p in self.confirmed_pricing.values()):
            raise ValueError("Preços confirmados devem cobrir exatamente os modelos autorizados.")
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


def verify_experiment(experiment_id: str, *, prompt_version: str, model: str,
                      manifest_path: Path = EXPERIMENT_FILE) -> dict[str, object]:
    """Fase 9C: o experimento pré-registrado precisa bater (arquivos fixados por hash)."""
    import hashlib
    import json
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise RealRunRefused("Manifesto do experimento ausente ou inválido.") from None
    if manifest.get("experiment_id") != experiment_id:
        raise RealRunRefused("Experimento autorizado difere do manifesto pré-registrado.")
    for relative, expected in manifest.get("pinned_files", {}).items():
        try:
            digest = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()[:16]
        except OSError:
            raise RealRunRefused("Arquivo fixado pelo experimento ausente.") from None
        if digest != expected:
            raise RealRunRefused("Cenários, catálogo, critérios ou prompt mudaram após o pré-registro.")
    if manifest.get("prompt_version") != prompt_version:
        raise RealRunRefused("Versão de prompt diferente da pré-registrada.")
    if model not in {m["model"] for m in manifest.get("models_in_order", [])}:
        raise RealRunRefused("Modelo fora do experimento pré-registrado.")
    return manifest


def check_real_run(path: Path | None, *, model: str, scenario_count: int, prompt_version: str = "v4",
                   stage: str = "full", now: datetime | None = None) -> RealRunAuthorization:
    """Todas as condições da avaliação real, na ordem; a primeira que falhar recusa a execução."""
    if not config_module.GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED:
        raise RealRunRefused("Provedor de IA real (Gemini) bloqueado pela trava de código (Fase 9C-B não autorizada).")
    # A ativação do Gemini nunca pode coincidir com a Meta real: homologação não alcança clientes.
    if config_module.GRAPH_TRANSPORT_ACTIVATION_AUTHORIZED or config_module.HOMOLOGATION_TRANSPORT_ACTIVATION_AUTHORIZED:
        raise RealRunRefused("Transporte real da Meta habilitado: avaliação de IA recusada (exige Meta desligada).")
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
    if stage not in authorization.stages:
        raise RealRunRefused("Etapa não autorizada.")
    if authorization.stop_file.exists():
        raise RealRunRefused("Interrupção emergencial ativa (arquivo de parada presente).")
    rates = load_registry().rates(model, moment.date())
    confirmed = authorization.confirmed_pricing[model]
    if rates is None or (confirmed["input"], confirmed["output"]) != (rates.input, rates.output):
        raise RealRunRefused("Divergência de preços entre a autorização e o registro verificado: execução interrompida.")
    verify_experiment(authorization.experiment_id, prompt_version=prompt_version, model=model)
    return authorization
