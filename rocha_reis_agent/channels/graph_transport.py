"""Adaptador HTTP oficial da WhatsApp Cloud API (Graph API). DESATIVADO nesta fase.

Nada neste módulo é instanciado pela aplicação sem a trava de ativação do
bootstrap (`GRAPH_TRANSPORT_ACTIVATION_AUTHORIZED`), que é falsa no código. Os
testes usam `httpx2.MockTransport`: nenhuma conexão é aberta.

Referências consultadas em 19/11/2026 (revalidar antes de ativar):
- POST https://graph.facebook.com/<VERSÃO>/<PHONE_NUMBER_ID>/messages, Bearer token;
  text.body até 4096; `recipient` (BSUID) no lugar de `to`; resposta messages[].id.
- Códigos de erro: tratar por `error.code` (error_subcode descontinuado desde v16).
- Versões: v26.0 é a mais recente listada como suportada; v20.0 e anteriores expiradas.
"""

from dataclasses import dataclass, field
from enum import StrEnum
import logging
import re
from typing import Any

import httpx2

from rocha_reis_agent.channels.contracts import (
    OutboundText, SendAccepted, SendOutcome, SendRejected, SendUncertain,
)
from rocha_reis_agent.channels.meta import PROVIDER_ID, build_text_payload

logger = logging.getLogger("rocha_reis_agent")

# Destino controlado pela aplicação: nunca derivado de configuração livre ou de cliente.
GRAPH_BASE_URL = "https://graph.facebook.com"
VERSION = re.compile(r"v[1-9][0-9]{0,2}\.[0-9]")
NUMERIC_ID = re.compile(r"[0-9]{1,32}")
MAX_RESPONSE_BYTES = 65536


class ErrorCategory(StrEnum):
    AUTHENTICATION = "authentication"
    PERMISSION = "permission"
    RECIPIENT = "recipient"
    SERVICE_WINDOW = "service_window"
    RATE_LIMIT = "rate_limit"
    TEMPORARY = "temporary"
    POLICY = "policy"
    PARAMETER = "parameter"
    UNCLASSIFIED = "unclassified"


@dataclass(frozen=True, slots=True)
class _Rule:
    category: ErrorCategory
    retryable: bool = False   # True somente quando a Meta indica tentativa recusada/não enviada
    uncertain: bool = False   # True quando não há evidência de que a mensagem NÃO foi aceita


# Classificação por error.code, conforme a página oficial de códigos de erro.
# Retry automático apenas onde a documentação diz "try again"/"wait and retry"
# para uma tentativa recusada ou "failed to send". Dúvida → incerto, nunca retry.
ERROR_RULES: dict[int, _Rule] = {
    0: _Rule(ErrorCategory.AUTHENTICATION), 190: _Rule(ErrorCategory.AUTHENTICATION),
    3: _Rule(ErrorCategory.PERMISSION), 10: _Rule(ErrorCategory.PERMISSION),
    131005: _Rule(ErrorCategory.PERMISSION),
    4: _Rule(ErrorCategory.RATE_LIMIT, retryable=True),
    80007: _Rule(ErrorCategory.RATE_LIMIT, retryable=True),
    130429: _Rule(ErrorCategory.RATE_LIMIT, retryable=True),
    131056: _Rule(ErrorCategory.RATE_LIMIT, retryable=True),
    131048: _Rule(ErrorCategory.POLICY),           # restrição por qualidade: sem instrução de retry
    131049: _Rule(ErrorCategory.POLICY),           # aguardar ≥24h: não reenviar automaticamente
    131000: _Rule(ErrorCategory.TEMPORARY, retryable=True),  # "Message failed to send... Try again"
    2494100: _Rule(ErrorCategory.TEMPORARY, retryable=True),  # manutenção: "Try again in a few minutes"
    1: _Rule(ErrorCategory.TEMPORARY, uncertain=True),       # "Invalid request or possible server error"
    2: _Rule(ErrorCategory.TEMPORARY, uncertain=True),       # indisponibilidade: sem prova de não envio
    131016: _Rule(ErrorCategory.TEMPORARY, uncertain=True),
    131057: _Rule(ErrorCategory.TEMPORARY),        # conta em manutenção
    131026: _Rule(ErrorCategory.RECIPIENT), 131021: _Rule(ErrorCategory.RECIPIENT),
    130403: _Rule(ErrorCategory.RECIPIENT), 131050: _Rule(ErrorCategory.RECIPIENT),
    133010: _Rule(ErrorCategory.RECIPIENT),
    131047: _Rule(ErrorCategory.SERVICE_WINDOW),
    368: _Rule(ErrorCategory.POLICY), 130497: _Rule(ErrorCategory.POLICY),
    131031: _Rule(ErrorCategory.POLICY), 130472: _Rule(ErrorCategory.POLICY),
    100: _Rule(ErrorCategory.PARAMETER), 131008: _Rule(ErrorCategory.PARAMETER),
    131009: _Rule(ErrorCategory.PARAMETER), 131051: _Rule(ErrorCategory.PARAMETER),
    135000: _Rule(ErrorCategory.PARAMETER), 131037: _Rule(ErrorCategory.PARAMETER),
    131045: _Rule(ErrorCategory.PARAMETER),
}


def classify_error(http_status: int, code: int | None) -> SendOutcome:
    """Mapear erro HTTP/Meta para REJECTED (com ou sem retry) ou UNKNOWN."""
    if code is not None:
        rule = ERROR_RULES.get(code) or (
            _Rule(ErrorCategory.PERMISSION) if 200 <= code <= 299 else None
        )
        if rule is not None:
            label = f"{rule.category.value}:{code}"
            return SendUncertain(label) if rule.uncertain else SendRejected(label, retryable=rule.retryable)
    if http_status == 429:
        return SendRejected(f"{ErrorCategory.RATE_LIMIT.value}:http_429", retryable=True)
    if http_status in (401, 403):
        return SendRejected(f"{ErrorCategory.AUTHENTICATION.value}:http_{http_status}", retryable=False)
    if 400 <= http_status < 500:
        # Erro de cliente sem código conhecido: tentativa recusada; não repetir às cegas.
        suffix = code if code is not None else f"http_{http_status}"
        return SendRejected(f"{ErrorCategory.UNCLASSIFIED.value}:{suffix}", retryable=False)
    # 5xx ou status inesperado: o servidor pode ter processado o envio.
    suffix = code if code is not None else f"http_{http_status}"
    return SendUncertain(f"{ErrorCategory.UNCLASSIFIED.value}:{suffix}")


@dataclass(frozen=True)
class GraphTransportConfig:
    access_token: str = field(repr=False)
    phone_number_id: str
    api_version: str
    connect_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if VERSION.fullmatch(self.api_version) is None:
            raise ValueError("Versão da Graph API inválida.")
        if NUMERIC_ID.fullmatch(self.phone_number_id) is None:
            raise ValueError("Identificador do número comercial inválido.")
        token = self.access_token
        if not 32 <= len(token) <= 4096 or not token.isascii() or any(c.isspace() or not c.isprintable() for c in token):
            raise ValueError("Token de acesso ausente ou malformado.")
        if not (0 < self.connect_timeout_seconds <= 30 and 0 < self.read_timeout_seconds <= 60):
            raise ValueError("Timeouts inválidos.")

    @property
    def endpoint(self) -> str:
        return f"{GRAPH_BASE_URL}/{self.api_version}/{self.phone_number_id}/messages"


def build_client(transport: httpx2.BaseTransport | None = None) -> httpx2.Client:
    """Cliente endurecido: sem proxies/certificados do ambiente, sem redirecionamento, TLS verificado."""
    return httpx2.Client(
        transport=transport, trust_env=False, follow_redirects=False, verify=True,
        headers={"User-Agent": "rocha-reis-agent/0.5"},
    )


class GraphAPITransport:
    """Implementa WhatsAppTransport. Nunca lança exceção: classifica tudo em SendOutcome."""

    def __init__(self, config: GraphTransportConfig, client: httpx2.Client) -> None:
        if client.follow_redirects or client.trust_env:
            raise ValueError("Cliente HTTP inseguro para a Graph API.")
        self.config = config
        self.client = client

    def __repr__(self) -> str:
        return f"GraphAPITransport(version={self.config.api_version!r})"

    def send_text(self, message: OutboundText, *, timeout_seconds: float) -> SendOutcome:
        if message.business_number_id != self.config.phone_number_id:
            # Configuração divergente: não transmitir para outro número comercial.
            return SendRejected("parameter:business_number_mismatch", retryable=False)
        if not message.body or len(message.body) > 4096:
            return SendRejected("parameter:body_length", retryable=False)
        timeout = httpx2.Timeout(
            min(timeout_seconds, self.config.read_timeout_seconds),
            connect=min(timeout_seconds, self.config.connect_timeout_seconds),
        )
        try:
            response = self.client.post(
                self.config.endpoint, json=build_text_payload(message), timeout=timeout,
                headers={"Authorization": f"Bearer {self.config.access_token}", "Accept": "application/json"},
            )
        except (httpx2.ConnectTimeout, httpx2.ConnectError, httpx2.PoolTimeout):
            # Conexão não estabelecida: a requisição não foi transmitida.
            outcome: SendOutcome = SendRejected("temporary:connect_failed", retryable=True)
        except httpx2.TimeoutException:
            # Leitura/escrita esgotada após conectar: a Meta pode ter aceitado.
            outcome = SendUncertain("temporary:timeout_after_connect")
        except httpx2.TransportError:
            outcome = SendUncertain("temporary:transport_error_after_connect")
        else:
            outcome = self._interpret(response)
        logger.info("graph_send_classified", extra={"outcome": type(outcome).__name__,
                                                    "code": getattr(outcome, "code", "accepted")})
        return outcome

    def _interpret(self, response: httpx2.Response) -> SendOutcome:
        status = response.status_code
        if 300 <= status < 400:
            # Redirecionamento não é seguido (evita vazar o token); resultado não conclusivo.
            return SendUncertain(f"unclassified:redirect_{status}")
        data = self._json(response)
        if 200 <= status < 300:
            messages = data.get("messages") if isinstance(data, dict) else None
            first = messages[0] if isinstance(messages, list) and messages and isinstance(messages[0], dict) else {}
            provider_id = first.get("id")
            if isinstance(provider_id, str) and PROVIDER_ID.fullmatch(provider_id):
                detail = first.get("message_status")
                return SendAccepted(provider_id, detail if isinstance(detail, str) and len(detail) <= 40 else "accepted")
            # 2xx sem ID utilizável: a Meta pode ter aceitado; não reenviar.
            return SendUncertain("unclassified:success_without_id")
        error = data.get("error") if isinstance(data, dict) else None
        code = error.get("code") if isinstance(error, dict) and type(error.get("code")) is int else None
        return classify_error(status, code)

    @staticmethod
    def _json(response: httpx2.Response) -> Any:
        if len(response.content) > MAX_RESPONSE_BYTES:
            return None
        try:
            return response.json()
        except (ValueError, UnicodeError, RecursionError):
            return None
