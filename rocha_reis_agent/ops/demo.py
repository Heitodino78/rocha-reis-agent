"""Perfil de DEMONSTRAÇÃO isolado e reproduzível (Fase 10).

Cria, FORA do repositório e longe de qualquer banco existente, um diretório privado (0700):

    <perfil>/perfil.json            configuração local (segredos SINTÉTICOS gerados aqui; 0600)
    <perfil>/credenciais-demo.txt   senhas geradas dos usuários fictícios (0600; nunca impressas)
    <perfil>/dados/demo.synthetic.sqlite3
    <perfil>/backups/               backups criptografados da demonstração
    <perfil>/chaves/backup.key      chave dos backups (diretório SEPARADO dos backups)
    <perfil>/restaurado-*.synthetic.sqlite3   restauração isolada de conferência

Tudo é fictício: números 55119000002xx, catálogo sintético, transporte FAKE e IA FAKE.
Nenhuma chamada externa; as travas Meta/Gemini não são tocadas.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import shutil
from uuid import uuid4

from rocha_reis_agent.application.conversations import ConversationManager
from rocha_reis_agent.application.handoff import HumanHandoffManager
from rocha_reis_agent.bootstrap import configured_channel, configured_dispatcher, configured_limits, configured_repository
from rocha_reis_agent.channels.contracts import InboundTextMessage, Sender
from rocha_reis_agent.channels.fake_transport import FakeWhatsAppTransport
from rocha_reis_agent.channels.service import conversation_id_for
from rocha_reis_agent.config import Settings
from rocha_reis_agent.domain.conversations import utc_now
from rocha_reis_agent.ops.backup import BackupError, KeyMaterial, create_backup, generate_key, restore_backup
from rocha_reis_agent.panel.security import hash_password
from rocha_reis_agent.panel.service import PanelService
from rocha_reis_agent.panel.store import SQLitePanelStore

MARKER = ".rocha-reis-demo"
PHONE_ID = "100000000000001"     # identificadores numéricos FICTÍCIOS
WABA_ID = "200000000000001"
USERS = (("admin.demo", "Administração (demo)", "ADMIN"), ("ana.demo", "Ana (demo)", "OPERATOR"),
         ("bia.demo", "Bia (demo)", "OPERATOR"))
CLIENTS = {name: f"55119000002{index:02d}" for index, name in enumerate(
    ("auto", "fila", "ana", "transferida", "incerta", "finalizada", "retomada", "excluida",
     "sem-preco", "agenda", "reclamacao", "a-partir-de"), start=1)}


def default_profile() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "rocha-reis-agent" / "demo"


def _private(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def _write_private(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)
    os.chmod(path, 0o600)


def profile_settings(profile: Path, *, port: int = 8000) -> Settings:
    """Settings explícitos do perfil (ignora .env do diretório atual)."""
    data = json.loads((profile / "perfil.json").read_text(encoding="utf-8"))
    values = dict(data["settings"])
    # Fase 12: o perfil declara o ambiente e o arquivo; perfis antigos sem isso são demonstração.
    values["storage_path"] = profile / "dados" / data.get("database", "demo.synthetic.sqlite3")
    values["environment_profile"] = data.get("profile", "demo")
    # Fase 17: no servidor, a origem é o endereço do painel na VPN (atrás do Caddy), declarada no perfil.
    values["panel_origin"] = data.get("panel_origin") or f"{'https' if data.get('tls') else 'http'}://127.0.0.1:{port}"
    volume = data.get("volume") or {}
    if volume.get("id"):  # Fase 18B-0: volume dedicado identificado (ausente = laboratório/demonstração)
        values["storage_volume_mount"] = Path(volume["ponto_montagem"])
        values["storage_volume_id"] = volume["id"]
        values["storage_volume_luks_uuid"] = volume["luks_uuid"]
    if data.get("mfa_key"):  # caminho relativo ao perfil (chave em chaves/, separada de dados/ e backups/)
        values["panel_mfa_key_file"] = profile / data["mfa_key"]
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def _new_profile(profile: Path) -> None:
    _private(profile)
    (profile / MARKER).write_text("perfil de demonstração fictícia\n", encoding="utf-8")
    _private(profile / "dados"); _private(profile / "backups"); _private(profile / "chaves")
    _private(profile / "backups-secundario")  # destino secundário SIMULADO (mesmo disco: não é backup externo)
    config = {"version": 1, "kind": "demo", "profile": "demo", "database": "demo.synthetic.sqlite3",
              "mfa_key": "chaves/mfa.key", "settings": {
        "app_env": "local", "storage_backend": "sqlite", "whatsapp_enabled": True, "whatsapp_transport": "fake",
        "whatsapp_verify_token": secrets.token_urlsafe(24), "whatsapp_app_secret": secrets.token_urlsafe(32),
        "whatsapp_conversation_key": secrets.token_urlsafe(32), "whatsapp_phone_number_id": PHONE_ID,
        "whatsapp_business_account_id": WABA_ID, "panel_enabled": True, "panel_cookie_secure": False,
        "panel_embedded_worker": True, "knowledge_source": "database", "ai_provider": "fake",
        "panel_environment_label": "DEMONSTRAÇÃO",
    }}
    _write_private(profile / "perfil.json", json.dumps(config, indent=2) + "\n")
    generate_key(profile / "chaves" / "mfa.key")  # segundo fator OPCIONAL na demonstração (credenciais fictícias)


def build_demo(profile: Path, *, reset: bool = False) -> dict[str, object]:
    profile = Path(profile).absolute()
    if profile.exists():
        if not (profile / MARKER).is_file():
            raise BackupError("O diretório existe e não é um perfil de demonstração; nada foi alterado.")
        if not reset:
            raise BackupError("Perfil de demonstração já existe. Use --recriar para apagá-lo e criar outro.")
        shutil.rmtree(profile)  # somente o perfil marcado como demonstração
    _new_profile(profile)
    settings = profile_settings(profile)
    repository = configured_repository(settings)
    store = SQLitePanelStore(repository)
    channel, ingestor = configured_channel(settings, repository)

    # 1. Usuários fictícios com senhas geradas (gravadas só no arquivo 0600 do perfil).
    credentials, users = [], {}
    for login, name, role in USERS:
        password = f"Demo-{secrets.token_urlsafe(12)}-9!"
        users[login] = store.create_user(login=login, display_name=name, password_hash=hash_password(password),
                                         role=role, must_change_password=False, actor_id=None)
        credentials.append(f"{login}\t{role}\t{password}")
    _write_private(profile / "credenciais-demo.txt",
                   "# Credenciais FICTÍCIAS da demonstração local (não reutilize em lugar nenhum)\n"
                   + "\n".join(credentials) + "\n")

    # 2. Conhecimento sintético aprovado (fixture), no banco.
    from rocha_reis_agent.bootstrap import configured_knowledge
    from rocha_reis_agent.catalog_io import load_synthetic_catalog
    from rocha_reis_agent.knowledge_catalog import KnowledgeScope
    root = Path(__file__).resolve().parents[2]
    knowledge = configured_knowledge(settings, repository)
    assert knowledge is not None
    knowledge.initialize(KnowledgeScope.SYNTHETIC)
    knowledge.import_catalog(load_synthetic_catalog(root / "fixtures" / "salon.versioned.synthetic.json"),
                             author="fixture-sintetica")

    # 2b. Fase 14: dados ESTRUTURADOS fictícios já aprovados (categoria, profissional, serviço com
    # duração e preço "a partir de"), para a demonstração mostrar o que a Fase 12 permite.
    from rocha_reis_agent.knowledge import Category, Duration, Professional, Service
    from rocha_reis_agent.knowledge_catalog import (
        Approval, CategoryFact, PriceFact, ProfessionalFact, ServiceFact,
    )
    reviewer = "Administração (demonstração)"
    for fact_id, data in (
        ("category:cabelo", CategoryFact(category=Category(id="cabelo", name="Cabelo (fictícia)"))),
        ("professional:pro-a", ProfessionalFact(professional=Professional(id="pro-a", name="Profissional Fictícia A"))),
        ("service:coloracao", ServiceFact(service=Service(
            id="coloracao", name="Coloração de teste", aliases=("coloracao", "coloração", "tintura"),
            description="Serviço fictício com preço inicial.", category_id="cabelo",
            duration=Duration(min_minutes=90, max_minutes=150), professional_ids=("pro-a",)))),
        ("price:coloracao", PriceFact(service_id="coloracao", pricing="starting", amount="120.00",
                                      note="Valor fictício; o final depende do comprimento.")),
    ):
        knowledge.append(fact_id=fact_id, expected_version=None, status=Approval.APPROVED, data=data,
                         source="Dados FICTÍCIOS da demonstração", reviewed_by=reviewer, author=users["admin.demo"].id)

    # 3. Clientes fictícios pelo caminho do webhook (assinatura HMAC verificada em processo).
    from rocha_reis_agent.panel.admin_cli import _envelope, _post, _process
    key = settings.whatsapp_conversation_key.get_secret_value().encode()  # type: ignore[union-attr]

    def say(client: str, text: str) -> str:
        if _post(settings, _envelope(settings, CLIENTS[client], text)) != 200:
            raise BackupError("Webhook interno recusou a mensagem de demonstração.")
        _process(settings, repository, channel)
        event = InboundTextMessage(PHONE_ID, "x", Sender(wa_id=CLIENTS[client]), "x", utc_now())
        return str(conversation_id_for(event, key))

    service = PanelService(conversations=ConversationManager(repository, configured_limits(settings)),  # type: ignore[arg-type]
                           handoff=HumanHandoffManager(), store=store, channel=channel,
                           window_seconds=settings.channel_service_window_hours * 3600)
    admin, ana, bia = (store.user(users[login].id) for login, _, _ in USERS)

    def dispatch(behavior: str) -> None:
        transport = FakeWhatsAppTransport(behavior)  # type: ignore[arg-type]
        configured_dispatcher(settings, channel, transport, run_id=uuid4().hex).dispatch_one("demo")

    say("auto", "Olá! Quanto custa o corte?")
    say("a-partir-de", "Quanto custa a coloração?")          # preço "a partir de" (nunca dito como final)
    say("sem-preco", "Quanto custa a escova?")             # preço não aprovado: não é inventado
    say("agenda", "Quero marcar um corte para sábado")       # só o link Booksy aprovado, sem confirmar horário
    say("reclamacao", "Fiquei insatisfeita com o atendimento de ontem")  # reclamação vai para pessoas
    say("fila", "Quero falar com uma pessoa, por favor.")
    ana_case = say("ana", "Preciso de um atendente.")
    service.assume(ana, ana_case)  # type: ignore[arg-type]
    service.reply(ana, ana_case, "Olá! Sou a Ana (demonstração). Como posso ajudar?", uuid4().hex)  # type: ignore[arg-type]
    dispatch("accept")
    say("ana", "Obrigada! Tenho uma dúvida sobre o atendimento.")  # nova mensagem para a Ana
    moved = say("transferida", "Quero falar com um atendente.")
    service.assume(ana, moved)  # type: ignore[arg-type]
    service.transfer(ana, moved, bia.id)  # type: ignore[arg-type,union-attr]
    service.reply(bia, moved, "Oi, aqui é a Bia (demonstração).", uuid4().hex)  # type: ignore[arg-type]
    dispatch("reject")      # falha de envio visível no painel
    uncertain = say("incerta", "Quero falar com alguém.")
    service.assume(admin, uncertain)  # type: ignore[arg-type]
    service.reply(admin, uncertain, "Olá! Retorno da equipe (demonstração).", uuid4().hex)  # type: ignore[arg-type]
    dispatch("timeout")     # resultado INCERTO: nunca reenviado automaticamente
    done = say("finalizada", "Quero um atendente, por favor.")
    service.assume(admin, done)  # type: ignore[arg-type]
    service.resolve(admin, done)  # type: ignore[arg-type]
    resumed = say("retomada", "Atendente, por favor.")
    service.assume(admin, resumed)  # type: ignore[arg-type]
    service.resolve(admin, resumed)  # type: ignore[arg-type]
    service.resume(admin, resumed)  # type: ignore[arg-type]
    say("retomada", "E quanto custa a escova?")  # atendida de novo pela automação
    retired = say("excluida", "Oi")
    repository.retire(retired, confirmation=f"DELETE_SYNTHETIC:{retired}")
    say("excluida", "Oi de novo")  # política padrão: bloqueado e visível ao ADMIN

    # Rascunho pendente para a administração praticar a aprovação. Fica no preço da ESCOVA (sem valor
    # aprovado): um rascunho sobre um preço aprovado suspenderia o valor (ex.: o do corte) durante a
    # demonstração e o Fluxo 1 do roteiro ("Quanto custa um corte?") deixaria de funcionar (Fase 14).
    knowledge.append(fact_id="price:escova", expected_version=None, status=Approval.DRAFT,
                     data=PriceFact(service_id="escova", amount="60.00", note="Valor fictício em revisão."),
                     source="Tabela fictícia para treino de aprovação", reviewed_by=None, author=users["admin.demo"].id)

    # 4. Backup criptografado + restauração ISOLADA de conferência.
    key_file = generate_key(profile / "chaves" / "backup.key")
    backup = create_backup(settings.storage_path, profile / "backups", KeyMaterial("keyfile", key_file=key_file))  # type: ignore[arg-type]
    restored = profile / f"restaurado-{backup.stem}.synthetic.sqlite3"
    manifest = restore_backup(backup, restored, KeyMaterial("keyfile", key_file=key_file))
    return {"perfil": str(profile), "banco": str(settings.storage_path), "credenciais": str(profile / "credenciais-demo.txt"),
            "backup": str(backup), "restauracao_isolada": str(restored), "contagens": manifest["table_counts"],
            "proximo_passo": f"sh iniciar.sh  (ou: python -m rocha_reis_agent.ops iniciar --perfil {profile})"}


def send_fictitious(profile: Path, text: str, *, sender: str | None = None) -> dict[str, object]:
    """Uma mensagem de cliente FICTÍCIO pelo mesmo caminho do webhook (assinatura verificada).

    Só funciona num perfil marcado como demonstração; o worker embutido do painel (ou o
    próximo `iniciar`) processa e responde com transporte e IA simulados.
    """
    profile = Path(profile).absolute()
    if not (profile / MARKER).is_file():
        raise BackupError("Perfil de demonstração não encontrado; crie com: sh iniciar.sh --nova-demo")
    number = sender or f"551190000{secrets.randbelow(9000) + 1000:04d}"
    if not number.isdigit() or not number.startswith("55119000"):
        raise BackupError("Use somente números fictícios no formato 55119000XXXXX.")
    if not text.strip() or len(text) > 1000:
        raise BackupError("Escreva um texto de 1 a 1000 caracteres.")
    from rocha_reis_agent.panel.admin_cli import _envelope, _post
    settings = profile_settings(profile)
    if _post(settings, _envelope(settings, number, text)) != 200:
        raise BackupError("O webhook interno recusou a mensagem.")
    return {"enviada_de": number, "aviso": "Mensagem FICTÍCIA registrada; o painel aberto a processa em alguns segundos."}
