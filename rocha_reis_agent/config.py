"""Configuração local tipada, sem credenciais ou detalhes nos erros."""

from typing import Final, Literal
from pathlib import Path
import re

from pydantic import Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.exceptions import SettingsError


# Trava de ativação do transporte HTTP real. Mudar exige alteração de código revisada
# e autorização explícita do responsável; variáveis de ambiente sozinhas NUNCA ativam envio.
GRAPH_TRANSPORT_ACTIVATION_AUTHORIZED: Final = False
# Trava SEPARADA para o ensaio de homologação (Fase 6B). Também falsa: preparar o
# mecanismo não autoriza usá-lo. A trava de produção acima não é afetada por esta.
HOMOLOGATION_TRANSPORT_ACTIVATION_AUTHORIZED: Final = False
HOMOLOG_DB_SUFFIX = ".homolog.synthetic.sqlite3"
# Trava do provedor de IA REAL (Fase 8). Independente das travas da Meta: abrir uma
# nunca abre a outra. Falsa nesta fase; uma chave configurada sozinha não ativa nada.
AI_REAL_PROVIDER_ACTIVATION_AUTHORIZED: Final = False


class ConfigurationError(RuntimeError):
    """Configuração inválida que impede inicializar a aplicação."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    app_env: Literal["local", "test", "homologation"] = "local"
    app_name: str = Field(
        default="rocha-reis-agent", pattern=r"^[a-z][a-z0-9-]{0,63}$"
    )
    sim_max_message_chars: int = Field(default=2000, ge=1, le=2000)
    sim_max_history_entries: int = Field(default=100, ge=1, le=1000)
    sim_max_events_per_session: int = Field(default=1000, ge=1, le=10000)
    sim_max_conversations: int = Field(default=100, ge=1, le=1000)
    sim_context_turns: int = Field(default=5, ge=1, le=100)
    ai_provider: Literal["fake", "anthropic"] = "fake"
    ai_model: str = Field(default="claude-opus-5-5", pattern=r"^claude-[a-z0-9-]{1,60}$")
    ai_api_key: SecretStr | None = None
    # Inclui tokens de raciocínio (thinking sempre ligado no Claude Opus 5.5).
    ai_max_output_tokens: int = Field(default=4096, ge=256, le=16000)
    ai_effort: Literal["low", "medium", "high"] = "medium"
    ai_max_retries: int = Field(default=1, ge=0, le=3)
    ai_refusal_fallback: Literal["default", "off"] = "default"
    ai_max_concurrency: int = Field(default=2, ge=1, le=16)
    ai_max_requests_per_day: int = Field(default=500, ge=1, le=100000)
    ai_max_cost_usd_per_day: float = Field(default=5.0, gt=0, le=1000)
    ai_max_input_chars: int = Field(default=6000, ge=1000, le=50000)
    # Fase 8: conhecimento em arquivo (fixtures, padrão) ou no banco (gestão pelo painel).
    knowledge_source: Literal["file", "database"] = "file"
    knowledge_require_distinct_approver: bool = False
    ai_prompt_version: str | None = Field(default=None, pattern=r"^v[1-9][0-9]*$")
    ai_history_limit: int = Field(default=4, ge=0, le=10)
    ai_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    storage_backend: Literal["memory", "sqlite"] = "memory"
    storage_path: Path | None = None
    storage_busy_timeout_ms: int = Field(default=1000, ge=1, le=5000)
    storage_max_bytes: int = Field(default=67108864, ge=1048576, le=1073741824)
    storage_retention_days: int = Field(default=7, ge=1, le=365)
    storage_audit_days: int = Field(default=30, ge=1, le=365)
    storage_audit_limit: int = Field(default=10000, ge=1, le=100000)
    # Conversa ociosa há este tempo pode ser arquivada quando o limite de ATIVAS é atingido.
    storage_archive_idle_hours: float = Field(default=24.0, ge=1, le=8760)

    # Canal WhatsApp (Fase 4): desabilitado por padrão; somente transporte fake.
    whatsapp_enabled: bool = False
    whatsapp_transport: Literal["fake", "graph"] = "fake"
    whatsapp_verify_token: SecretStr | None = None
    whatsapp_app_secret: SecretStr | None = None
    whatsapp_conversation_key: SecretStr | None = None
    # Token de acesso da Graph API: somente para o transporte real (bloqueado nesta fase).
    whatsapp_access_token: SecretStr | None = None
    whatsapp_phone_number_id: str | None = Field(default=None, pattern=r"^[0-9]{1,32}$")
    whatsapp_business_account_id: str | None = Field(default=None, pattern=r"^[0-9]{1,32}$")
    # Versão explícita da Graph API (ex.: v26.0, mais recente confirmada em 19/11/2026).
    whatsapp_graph_api_version: str | None = Field(default=None, pattern=r"^v[1-9][0-9]{0,2}\.[0-9]$")
    whatsapp_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    whatsapp_max_body_bytes: int = Field(default=262144, ge=1024, le=1048576)
    whatsapp_send_timeout_seconds: float = Field(default=10.0, gt=0, le=30)
    channel_inbox_lease_seconds: float = Field(default=60.0, ge=1, le=900)
    channel_inbox_max_attempts: int = Field(default=5, ge=1, le=10)
    channel_outbox_lease_seconds: float = Field(default=30.0, ge=1, le=900)
    channel_outbox_max_attempts: int = Field(default=3, ge=1, le=10)
    channel_backoff_base_seconds: float = Field(default=2.0, ge=0.1, le=60)
    channel_backoff_max_seconds: float = Field(default=300.0, ge=1, le=3600)
    channel_service_window_hours: float = Field(default=23.0, gt=0, le=24)
    # Interrupção por configuração: só bloqueia; não desfaz interrupção registrada no banco.
    channel_dispatch_halted: bool = False
    # Log privado (0600) do canal; recomendado e separado por ambiente em homologação.
    channel_log_path: Path | None = None
    # Homologação: arquivo de autorização privado preenchido pelo responsável (fora do repositório).
    homolog_authorization_file: Path | None = None

    # Painel de atendimento humano (Fase 7): desabilitado por padrão; app separada do webhook.
    panel_enabled: bool = False
    panel_origin: str = Field(default="http://127.0.0.1:8000", pattern=r"^https?://[a-z0-9.-]{1,253}(:[0-9]{1,5})?$")
    panel_cookie_secure: bool = True
    panel_session_idle_minutes: int = Field(default=30, ge=5, le=480)
    panel_session_absolute_hours: int = Field(default=12, ge=1, le=24)
    panel_login_max_failures: int = Field(default=5, ge=3, le=20)
    panel_login_window_minutes: int = Field(default=15, ge=1, le=1440)
    panel_page_size: int = Field(default=25, ge=5, le=100)
    panel_sse_poll_seconds: float = Field(default=1.0, ge=0.1, le=10)
    # Stream rotaciona (cliente reconecta com Last-Event-ID): limita conexões presas no desligamento.
    panel_sse_max_seconds: float = Field(default=60.0, ge=1, le=3600)
    # Worker embutido (somente transporte fake) para demonstração local integrada.
    panel_embedded_worker: bool = False

    @model_validator(mode="after")
    def storage_configuration(self) -> "Settings":
        if self.storage_backend == "sqlite":
            if self.storage_path is None or not self.storage_path.name.endswith(".synthetic.sqlite3"):
                raise ValueError("SQLite exige caminho sintético explícito.")
        elif self.storage_path is not None:
            raise ValueError("Caminho exige selecionar SQLite explicitamente.")
        if self.whatsapp_enabled:
            if self.storage_backend != "sqlite":
                raise ValueError("Canal exige inbox/outbox duráveis em SQLite.")
            if self.whatsapp_phone_number_id is None:
                raise ValueError("Canal exige identificador do número comercial.")
            secrets = [self.whatsapp_verify_token, self.whatsapp_app_secret, self.whatsapp_conversation_key]
            values = [secret.get_secret_value() if secret is not None else "" for secret in secrets]
            if any(not _strong_secret(value) for value in values) or len(set(values)) != len(values):
                raise ValueError("Segredos do canal ausentes, fracos, de exemplo ou reutilizados.")
        homolog = self.app_env == "homologation"
        homolog_db = self.storage_path is not None and self.storage_path.name.endswith(HOMOLOG_DB_SUFFIX)
        if homolog:
            # Ambiente isolado: banco próprio, canal habilitado e autorização explícita em arquivo.
            if not homolog_db or not self.whatsapp_enabled or self.homolog_authorization_file is None:
                raise ValueError("Homologação exige banco *.homolog.synthetic.sqlite3, canal e arquivo de autorização.")
        elif homolog_db or self.homolog_authorization_file is not None:
            raise ValueError("Banco ou autorização de homologação só no ambiente de homologação.")
        if self.ai_provider == "anthropic":
            if not AI_REAL_PROVIDER_ACTIVATION_AUTHORIZED:
                raise ValueError("Provedor de IA real não autorizado nesta fase.")
            key = self.ai_api_key.get_secret_value() if self.ai_api_key else ""
            others = {s.get_secret_value() for s in (self.whatsapp_verify_token, self.whatsapp_app_secret,
                                                     self.whatsapp_conversation_key, self.whatsapp_access_token) if s}
            if (len(key) < 20 or not key.isprintable() or any(c.isspace() for c in key) or key in others
                    or self.storage_backend != "sqlite" or self.ai_prompt_version is None):
                raise ValueError("IA real exige chave exclusiva, SQLite (consumo) e versão de prompt explícita.")
            if self.ai_model.startswith("claude-haiku") and self.ai_refusal_fallback != "off":
                # Referência oficial (2026-11): Haiku 5.5 não tem fallback de recusa no servidor.
                raise ValueError("Modelo sem fallback de recusa no servidor: use AI_REFUSAL_FALLBACK=off.")
        elif self.ai_api_key is not None:
            raise ValueError("Chave de IA só é aceita com provedor real explicitamente selecionado.")
        if self.knowledge_source == "database" and self.storage_backend != "sqlite":
            raise ValueError("Conhecimento no banco exige SQLite.")
        if self.panel_enabled:
            if self.storage_backend != "sqlite" or not self.whatsapp_enabled:
                raise ValueError("Painel exige armazenamento SQLite e canal habilitado.")
            loopback = re.match(r"^http://(127\.0\.0\.1|localhost)(:[0-9]+)?$", self.panel_origin) is not None
            if self.panel_origin.startswith("http://") and not loopback:
                raise ValueError("Painel sem HTTPS só é permitido em 127.0.0.1/localhost.")
            if not self.panel_cookie_secure and not loopback:
                raise ValueError("Cookie sem Secure só é permitido em 127.0.0.1/localhost.")
        if self.panel_embedded_worker and (not self.panel_enabled or self.whatsapp_transport != "fake"):
            raise ValueError("Worker embutido do painel só com painel habilitado e transporte fake.")
        if self.whatsapp_transport == "graph":
            unlocked = HOMOLOGATION_TRANSPORT_ACTIVATION_AUTHORIZED if homolog else GRAPH_TRANSPORT_ACTIVATION_AUTHORIZED
            if not unlocked:
                raise ValueError("Transporte real não autorizado nesta fase.")
            token = self.whatsapp_access_token.get_secret_value() if self.whatsapp_access_token else ""
            others = {s.get_secret_value() for s in (self.whatsapp_verify_token, self.whatsapp_app_secret,
                                                     self.whatsapp_conversation_key) if s is not None}
            if (not self.whatsapp_enabled or self.whatsapp_graph_api_version is None
                    or not 32 <= len(token) <= 4096 or token in others or any(c.isspace() for c in token)):
                raise ValueError("Transporte real exige canal habilitado, versão explícita e token válido e exclusivo.")
        elif self.whatsapp_access_token is not None:
            raise ValueError("Token de acesso só é aceito com transporte real explicitamente selecionado.")
        return self


def _strong_secret(value: str) -> bool:
    """Recusa vazio, curto, previsível ou placeholder. Não prova aleatoriedade."""
    lowered = value.lower()
    return (
        32 <= len(value) <= 512 and len(set(value)) >= 12 and value.isprintable()
        and not any(word in lowered for word in ("change", "exemplo", "example", "placeholder", "secret", "token"))
    )


def load_settings() -> Settings:
    """Ler ambiente/.env e falhar sem expor valores ou a exceção original."""
    try:
        return Settings()
    except (ValidationError, SettingsError, OSError, UnicodeError):
        raise ConfigurationError(
            "Configuração inválida. Revise as variáveis APP_, SIM_, AI_, STORAGE_, WHATSAPP_, CHANNEL_ e o arquivo .env; "
            "consulte .env.example."
        ) from None
