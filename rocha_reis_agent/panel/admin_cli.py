"""CLI local do painel (nunca exposta em rede).

create-admin --login L --name "Nome"   primeiro administrador; senha digitada (getpass), sem padrão
             [--password-stdin]        lê a senha da entrada padrão (automação local)
seed-demo                              conversas FICTÍCIAS A–E pelo webhook interno + worker fake
knowledge-init --scope synthetic|official --confirm KNOWLEDGE_SCOPE:<ESCOPO>   fixa o escopo da base
knowledge-import-fixture               importa a fixture SINTÉTICA numa base sintética vazia
say --from NUMERO "texto" [--process]  simula mensagem de cliente (assinada pelo caminho do webhook)
"""

import argparse
from getpass import getpass
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

from rocha_reis_agent.application.conversations import ConversationManager
from rocha_reis_agent.application.handoff import HumanHandoffManager
from rocha_reis_agent.bootstrap import (
    build_agent, build_ai_provider, configured_channel, configured_dispatcher, configured_knowledge, configured_limits,
    configured_repository, queue_policy,
)
from rocha_reis_agent.knowledge_catalog import KnowledgeScope
from rocha_reis_agent.catalog_io import load_synthetic_catalog
from rocha_reis_agent.channels.fake_transport import FakeWhatsAppTransport
from rocha_reis_agent.channels.meta import sign
from rocha_reis_agent.channels.service import InboundProcessor, drain
from rocha_reis_agent.config import ConfigurationError, load_settings
from rocha_reis_agent.domain.contracts import SimulationError
from rocha_reis_agent.panel.security import PanelError, hash_password, normalize_login, validate_password
from rocha_reis_agent.panel.service import PanelService
from rocha_reis_agent.panel.store import SQLitePanelStore
from rocha_reis_agent.persistence.sqlite_channel import SQLiteChannelStore

ROOT = Path(__file__).resolve().parents[2]
DEMO = [  # números e conteúdos explicitamente fictícios
    ("5511900000101", "Olá! Quero agendar um corte para sábado."),
    ("5511900000102", "Quanto custa o corte?"),
    ("5511900000103", "Quero falar com uma pessoa, por favor."),
    ("5511900000104", "Quero falar com um atendente."),
    ("5511900000105", "Preciso de um atendente humano."),
]


def _envelope(settings, sender: str, text: str) -> dict:  # type: ignore[no-untyped-def]
    return {"object": "whatsapp_business_account", "entry": [{"id": settings.whatsapp_business_account_id or "0",
            "changes": [{"field": "messages", "value": {
                "messaging_product": "whatsapp",
                "metadata": {"display_phone_number": "15550000000", "phone_number_id": settings.whatsapp_phone_number_id},
                "contacts": [{"wa_id": sender}],
                "messages": [{"from": sender, "id": f"wamid.SIMULADO.{uuid4().hex}", "timestamp": str(int(time.time())),
                              "type": "text", "text": {"body": text}}]}}]}]}


def _post(settings, payload: dict) -> int:  # type: ignore[no-untyped-def]
    """Mesmo caminho do webhook real: assinatura HMAC verificada em processo (sem socket)."""
    from fastapi.testclient import TestClient
    from rocha_reis_agent.main import create_app

    raw = json.dumps(payload).encode("utf-8")
    signature = sign(settings.whatsapp_app_secret.get_secret_value().encode("utf-8"), raw)
    with TestClient(create_app(settings)) as client:
        return client.post("/webhooks/whatsapp", content=raw, headers={"X-Hub-Signature-256": signature}).status_code


def _process(settings, repository, channel) -> list[str]:  # type: ignore[no-untyped-def]
    knowledge_repository = configured_knowledge(settings, repository)
    catalog = None if knowledge_repository is not None else load_synthetic_catalog(
        ROOT / "fixtures" / "salon.versioned.synthetic.json")
    agent = build_agent(catalog, configured_limits(settings), conversation_repository=repository,
                        knowledge_repository=knowledge_repository, prompt_version=settings.ai_prompt_version,
                        provider=build_ai_provider(settings, repository))
    return drain(InboundProcessor(channel, agent, queue_policy(settings)),
                 configured_dispatcher(settings, channel, FakeWhatsAppTransport(), run_id=uuid4().hex), "panel-cli")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Painel Rocha Reis — administração local")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-admin")
    create.add_argument("--login", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--password-stdin", action="store_true")
    sub.add_parser("seed-demo")
    init = sub.add_parser("knowledge-init")
    init.add_argument("--scope", choices=["synthetic", "official"], required=True)
    init.add_argument("--confirm", required=True)
    sub.add_parser("knowledge-import-fixture")
    say = sub.add_parser("say")
    say.add_argument("--from", dest="sender", required=True)
    say.add_argument("text")
    say.add_argument("--process", action="store_true")
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
        if not settings.panel_enabled:
            raise SimulationError("Defina PANEL_ENABLED=true (ver .env.example).")
        repository = configured_repository(settings)
        store = SQLitePanelStore(repository)
        channel: SQLiteChannelStore = configured_channel(settings, repository)[0]
        if args.command == "create-admin":
            if store.count_users() > 0:
                raise SimulationError("Já existem funcionários; crie novos usuários pelo painel (administrador).")
            login = normalize_login(args.login)
            if args.password_stdin:
                password = sys.stdin.readline().rstrip("\n")
            else:
                password = getpass("Senha do administrador (não exibida): ")
                if getpass("Repita a senha: ") != password:
                    raise SimulationError("As senhas não conferem.")
            validate_password(password, login)
            name = args.name.strip()
            if not 1 <= len(name) <= 80:
                raise SimulationError("Nome de exibição inválido.")
            store.create_user(login=login, display_name=name, password_hash=hash_password(password), role="ADMIN",
                              must_change_password=False, actor_id=None)
            print("Administrador criado. Nenhuma senha foi exibida ou gravada em texto.")
        elif args.command == "knowledge-init":
            repo = configured_knowledge(settings, repository)
            scope = KnowledgeScope(args.scope.upper())
            if repo is None:
                raise SimulationError("Defina KNOWLEDGE_SOURCE=database.")
            if args.confirm != f"KNOWLEDGE_SCOPE:{scope.value}":
                raise SimulationError(f"Confirme com --confirm KNOWLEDGE_SCOPE:{scope.value} (o escopo não pode mudar depois).")
            repo.initialize(scope)
            print(f"Base de conhecimento com escopo {scope.value}.")
        elif args.command == "knowledge-import-fixture":
            repo = configured_knowledge(settings, repository)
            if repo is None or repo.scope() != KnowledgeScope.SYNTHETIC:
                raise SimulationError("Importação de fixture só numa base SINTÉTICA (knowledge-init --scope synthetic).")
            count = repo.import_catalog(load_synthetic_catalog(ROOT / "fixtures" / "salon.versioned.synthetic.json"),
                                        author="fixture-sintetica")
            print(f"{count} revisões SINTÉTICAS importadas (não são dados do salão).")
        elif args.command == "seed-demo":
            repo = configured_knowledge(settings, repository)
            if repo is not None and repo.scope() is None:
                repo.initialize(KnowledgeScope.SYNTHETIC)
                repo.import_catalog(load_synthetic_catalog(ROOT / "fixtures" / "salon.versioned.synthetic.json"),
                                    author="fixture-sintetica")
            admins = [u for u in store.users() if u["role"] == "ADMIN" and u["active"]]
            if not admins:
                raise SimulationError("Crie o administrador antes (create-admin).")
            for sender, text in DEMO:
                if _post(settings, _envelope(settings, sender, text)) != 200:
                    raise SimulationError("Webhook interno recusou a mensagem de demonstração.")
            _process(settings, repository, channel)
            # Cliente D: atendimento assumido e finalizado pelo administrador (conversa encerrada).
            admin = store.user(str(admins[0]["id"]))
            assert admin is not None
            from rocha_reis_agent.channels.contracts import InboundTextMessage, Sender
            from rocha_reis_agent.channels.service import conversation_id_for
            from rocha_reis_agent.domain.conversations import utc_now
            assert settings.whatsapp_conversation_key is not None and settings.whatsapp_phone_number_id is not None
            event = InboundTextMessage(settings.whatsapp_phone_number_id, "x", Sender(wa_id=DEMO[3][0]), "x", utc_now())
            key = conversation_id_for(event, settings.whatsapp_conversation_key.get_secret_value().encode())
            service = PanelService(conversations=ConversationManager(repository, configured_limits(settings)),  # type: ignore[arg-type]
                                   handoff=HumanHandoffManager(), store=store, channel=channel,
                                   window_seconds=settings.channel_service_window_hours * 3600)
            service.assume(admin, key)
            service.resolve(admin, key)
            print("Demonstração fictícia criada: A (agendamento), B (preço), C e E (aguardando), D (encerrada).")
        else:
            status = _post(settings, _envelope(settings, args.sender, args.text))
            print(f"webhook: HTTP {status}")
            if args.process:
                print("etapas: " + (", ".join(_process(settings, repository, channel)) or "nada pendente"))
    except (SimulationError, ConfigurationError, PanelError, ValueError) as error:
        message = error.message if isinstance(error, PanelError) else str(error) if isinstance(
            error, (SimulationError, ConfigurationError)) else "entrada inválida."
        print(f"Erro: {message}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
