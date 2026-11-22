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
# Trava do provedor de IA REAL — Google Gemini (Fase 9B; substitui a trava Anthropic da Fase 8).
# Independente das travas da Meta: abrir uma nunca abre a outra. Falsa nesta fase; a
# existência de GEMINI_API_KEY (ou GOOGLE_API_KEY) no ambiente NUNCA ativa nada.
GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED: Final = False


class ConfigurationError(RuntimeError):
    """Configuração inválida que impede inicializar a aplicação."""


# Fase 12 — trava de DADOS REAIS (pessoais), independente das três acima. Falsa: nenhum perfil,
# variável ou arquivo libera dados reais. Abri-la exige revisão de código, autorização escrita do
# responsável pelos dados e os requisitos de docs/SEGURANCA_ARMAZENAMENTO.md §6 cumpridos.
REAL_DATA_ACTIVATION_AUTHORIZED: Final = False
PREPROD_DB_SUFFIX = ".preprod.synthetic.sqlite3"

# Fase 18B-0 — trava do envio REAL de backups ao armazenamento externo (S3 compatível), independente das
# quatro acima. Falsa: com ela fechada o adaptador só conversa com 127.0.0.1 (servidor S3 FALSO dos
# testes); qualquer endpoint externo é recusado ANTES de abrir conexão. Abri-la exige conta contratada,
# credencial restrita e autorização escrita do responsável.
EXTERNAL_BACKUP_ACTIVATION_AUTHORIZED: Final = False


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
    ai_provider: Literal["fake", "gemini"] = "fake"
    # Sem modelo padrão: a escolha depende da homologação (Fase 9B). Só IDs do registro verificado.
    ai_model: str | None = Field(default=None, pattern=r"^gemini-[a-z0-9.-]{1,60}$")
    # Lida de GEMINI_API_KEY. Só aceita com provedor real explicitamente selecionado E trava aberta.
    gemini_api_key: SecretStr | None = None
    # Inclui tokens de raciocínio (cobrados como saída).
    ai_max_output_tokens: int = Field(default=2048, ge=256, le=65536)
    # Ausente = nível padrão do modelo no registro (minimal nos Flash-Lite, medium no 3.8 Flash).
    ai_thinking_level: Literal["minimal", "low", "medium", "high"] | None = None
    ai_max_retries: int = Field(default=1, ge=0, le=3)
    ai_max_concurrency: int = Field(default=2, ge=1, le=16)
    ai_max_requests_per_day: int = Field(default=500, ge=1, le=100000)
    ai_max_cost_usd_per_day: float = Field(default=5.0, gt=0, le=1000)
    ai_max_input_chars: int = Field(default=6000, ge=1000, le=50000)
    # Tokens de entrada ESTIMADOS (caracteres ÷ 2, conservador) recusados antes da chamada.
    ai_max_input_tokens: int = Field(default=4000, ge=500, le=100000)
    # Fase 9: tetos por EXECUÇÃO do processo (além dos diários). Ausente = só os diários.
    ai_max_requests_per_run: int | None = Field(default=None, ge=1, le=100000)
    ai_max_cost_usd_per_run: float | None = Field(default=None, gt=0, le=1000)
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
    # Fase 10: histórico paginado e rótulo do ambiente (ex.: "DEMONSTRAÇÃO") exibido no painel.
    # Fase 13 — segundo fator (TOTP). A chave que cifra os segredos TOTP fica FORA do banco e dos
    # backups (arquivo 0600). Obrigatório em pré-produção. Sem chave, o segundo fator fica indisponível.
    panel_mfa_required: bool = False
    panel_mfa_key_file: Path | None = None
    panel_mfa_issuer: str = Field(default="Rocha Reis (painel)", pattern=r"^[\w À-ÿ().-]{3,40}$")
    # Proxies reversos confiáveis para X-Forwarded-*: vazio = nenhum (o IP real da conexão é usado).
    panel_trusted_proxies: str = Field(default="", pattern=r"^[0-9a-fA-F.:/, ]{0,200}$")
    # Fase 12 — perfil do ambiente gravado no banco (impede abrir o banco de um ambiente em outro)
    # e classificação dos dados. Ausente = comportamento anterior (sem marcação).
    environment_profile: Literal["development", "demo", "test", "preproduction"] | None = None
    data_classification: Literal["synthetic", "real"] = "synthetic"
    # Exige volume cifrado VERIFICADO (dm-crypt) para o banco; sempre exigido em pré-produção.
    storage_require_encrypted_volume: bool = False
    # Fase 18B-0: identidade do volume de dados DEDICADO (do perfil, fora do volume). Quando definida, todo
    # processo confere, ao abrir o banco, que o volume LUKS correto está montado (ver storage_security).
    storage_volume_mount: Path | None = None
    storage_volume_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    storage_volume_luks_uuid: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    panel_history_page_size: int = Field(default=50, ge=10, le=200)
    panel_environment_label: str | None = Field(default=None, max_length=40, pattern=r"^[\w À-ÿ.-]{1,40}$")
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
        if self.ai_provider == "gemini":
            if not GEMINI_REAL_PROVIDER_ACTIVATION_AUTHORIZED:
                raise ValueError("Provedor de IA real (Gemini) não autorizado nesta fase.")
            key = self.gemini_api_key.get_secret_value() if self.gemini_api_key else ""
            others = {s.get_secret_value() for s in (self.whatsapp_verify_token, self.whatsapp_app_secret,
                                                     self.whatsapp_conversation_key, self.whatsapp_access_token) if s}
            if (len(key) < 20 or not key.isprintable() or any(c.isspace() for c in key) or key in others
                    or self.storage_backend != "sqlite" or self.ai_prompt_version is None or self.ai_model is None):
                raise ValueError("IA real exige chave exclusiva, SQLite (consumo), modelo e versão de prompt explícitos.")
            # Modelo do registro verificado (ai/models.json): estável, com saída estruturada,
            # nível de raciocínio e limite de saída aceitos.
            from rocha_reis_agent.ai.model_registry import ModelNotSupported, require_model
            try:
                require_model(self.ai_model, thinking_level=self.ai_thinking_level,
                              max_output_tokens=self.ai_max_output_tokens)
            except ModelNotSupported as error:
                raise ValueError(str(error)) from None
        elif self.gemini_api_key is not None:
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
        self._environment_rules()
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


    def _environment_rules(self) -> None:
        """Isolamento entre ambientes (Fase 12). Erros sem valores de configuração."""
        if self.data_classification == "real" and not REAL_DATA_ACTIVATION_AUTHORIZED:
            raise ValueError("Dados reais bloqueados: trava de código fechada e requisitos de proteção pendentes "
                             "(docs/SEGURANCA_ARMAZENAMENTO.md).")
        profile = self.environment_profile
        path = self.storage_path
        preprod_db = path is not None and path.name.endswith(PREPROD_DB_SUFFIX)
        if profile == "preproduction":
            if (self.app_env != "local" or self.storage_backend != "sqlite" or not preprod_db
                    or self.knowledge_source != "database" or not self.panel_environment_label):
                raise ValueError("Pré-produção exige APP_ENV=local, banco *.preprod.synthetic.sqlite3, conhecimento "
                                 "no banco e rótulo de ambiente.")
            if self.whatsapp_transport != "fake" or self.ai_provider != "fake" or self.panel_embedded_worker:
                raise ValueError("Pré-produção desta fase: transporte e IA simulados, sem worker embutido.")
        elif preprod_db:
            raise ValueError("Banco de pré-produção só pode ser aberto com o perfil de pré-produção.")
        if self.panel_mfa_required and self.panel_mfa_key_file is None:
            raise ValueError("Segundo fator obrigatório exige PANEL_MFA_KEY_FILE.")
        if self.panel_mfa_key_file is not None and path is not None:
            key_dir = self.panel_mfa_key_file.absolute().parent
            if key_dir == path.absolute().parent or path.absolute().parent in key_dir.parents:
                raise ValueError("A chave do segundo fator não pode ficar junto do banco.")
        if profile == "preproduction" and self.panel_enabled:
            # Sem exceção de demonstração: pré-produção sempre com segundo fator e HTTPS.
            if (not self.panel_mfa_required or not self.panel_cookie_secure
                    or not self.panel_origin.startswith("https://") or self.panel_session_idle_minutes > 30):
                raise ValueError("Painel de pré-produção exige segundo fator obrigatório, HTTPS, cookie Secure "
                                 "e inatividade de no máximo 30 minutos.")
        if profile == "demo" and (path is None or not path.name.startswith("demo")):
            raise ValueError("Perfil de demonstração exige banco demo*.synthetic.sqlite3.")
        if profile == "test" and self.app_env != "test":
            raise ValueError("Perfil de testes exige APP_ENV=test.")

    @property
    def requires_encrypted_volume(self) -> bool:
        return self.storage_require_encrypted_volume or self.environment_profile == "preproduction"


def _strong_secret(value: str) -> bool:
    """Recusa vazio, curto, previsível ou placeholder. Não prova aleatoriedade."""
    lowered = value.lower()
    return (
        32 <= len(value) <= 512 and len(set(value)) >= 12 and value.isprintable()
        and not any(word in lowered for word in ("change", "exemplo", "example", "placeholder", "secret", "token"))
    )


def load_settings() -> Settings:
    """Ler ambiente/.env e falhar sem expor valores ou a exceção original.

    Fase 17: com ROCHA_REIS_PROFILE=<diretório> (serviços systemd do servidor), a configuração vem do
    perfil (perfil.json 0600, segredos fora do ambiente e da linha de comando), como `ops iniciar`.
    """
    import os
    profile = os.environ.get("ROCHA_REIS_PROFILE")
    if profile:
        from pathlib import Path
        from rocha_reis_agent.ops.demo import profile_settings
        try:
            return profile_settings(Path(profile))
        except (ValidationError, OSError, ValueError, KeyError, TypeError):
            raise ConfigurationError("Perfil inválido ou ilegível em ROCHA_REIS_PROFILE.") from None
    try:
        return Settings()
    except (ValidationError, SettingsError, OSError, UnicodeError):
        raise ConfigurationError(
            "Configuração inválida. Revise as variáveis APP_, SIM_, AI_, GEMINI_, STORAGE_, WHATSAPP_, CHANNEL_ e o arquivo .env; "
            "consulte .env.example."
        ) from None
