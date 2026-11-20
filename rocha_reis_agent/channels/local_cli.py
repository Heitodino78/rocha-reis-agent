"""CLI local do canal (Fase 4): webhook simulado, worker e envio FAKE. Sem rede.

post   FIXTURE  assina com o App Secret configurado e entrega ao webhook em processo
                (ASGI em memória, sem socket). "timestamp": "NOW" vira o horário atual.
work            processa inbox e outbox até esvaziar, com FakeWhatsAppTransport
status          contagem por estado (sem conteúdo)
resend-uncertain OPERACAO RESEND_UNCERTAIN:OPERACAO   decisão humana de reenvio incerto
halt "MOTIVO"   interrupção emergencial de envios (preserva a outbox)
resume-dispatch RESUME_DISPATCH   retomada explícita dos envios
metrics         contadores e agregados técnicos (sem dados pessoais)
trace INBOX_ID  fluxo de um evento por identificadores técnicos
homolog-check   verificação de prontidão da homologação (sem exibir allowlist ou segredos)
"""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time
from typing import Any

from uuid import uuid4

from rocha_reis_agent import config as config_module
from rocha_reis_agent.bootstrap import (
    build_agent, build_ai_provider, configured_channel, configured_dispatcher, configured_knowledge, configured_limits,
    configured_repository, homologation_authorization, queue_policy,
)
from rocha_reis_agent.channels.contracts import ChannelError
from rocha_reis_agent.observability import configure_private_log
from rocha_reis_agent.catalog_io import load_synthetic_catalog
from rocha_reis_agent.channels.fake_transport import FakeWhatsAppTransport
from rocha_reis_agent.channels.meta import sign
from rocha_reis_agent.channels.service import InboundProcessor, drain
from rocha_reis_agent.config import ConfigurationError, load_settings
from rocha_reis_agent.domain.contracts import SimulationError
from rocha_reis_agent.knowledge import KnowledgeBase

BEHAVIORS = ("accept", "reject", "throttle", "not_sent", "timeout", "ambiguous", "crash")


def _now(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: (str(int(time.time())) if k == "timestamp" and v == "NOW" else _now(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_now(v) for v in value]
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Canal WhatsApp local — somente simulação offline")
    sub = parser.add_subparsers(dest="command", required=True)
    post = sub.add_parser("post")
    post.add_argument("fixture", type=Path)
    work = sub.add_parser("work")
    work.add_argument("--fixture", action="store_true", help="catálogo sintético incluído")
    work.add_argument("--behavior", choices=BEHAVIORS, default="accept")
    sub.add_parser("status")
    resend = sub.add_parser("resend-uncertain")
    resend.add_argument("operation")
    resend.add_argument("confirmation")
    halt = sub.add_parser("halt")
    halt.add_argument("reason")
    resume = sub.add_parser("resume-dispatch")
    resume.add_argument("confirmation")
    sub.add_parser("metrics")
    trace = sub.add_parser("trace")
    trace.add_argument("inbox_id", type=int)
    sub.add_parser("homolog-check")
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
        if not settings.whatsapp_enabled:
            raise SimulationError("Defina WHATSAPP_ENABLED=true com segredos locais (veja .env.example).")
        if settings.whatsapp_transport != "fake":
            # A CLI de simulação NUNCA usa o transporte real, mesmo se configurado.
            raise SimulationError("A CLI local só opera com WHATSAPP_TRANSPORT=fake.")
        if settings.channel_log_path is not None:
            configure_private_log(settings.channel_log_path)
        repository = configured_repository(settings)
        store, _ = configured_channel(settings, repository)
        if args.command == "post":
            from fastapi.testclient import TestClient  # transporte ASGI em memória
            from rocha_reis_agent.main import create_app

            if args.fixture.stat().st_size > settings.whatsapp_max_body_bytes:
                raise SimulationError("Fixture excede o limite configurado.")
            raw = json.dumps(_now(json.loads(args.fixture.read_text(encoding="utf-8")))).encode("utf-8")
            assert settings.whatsapp_app_secret is not None
            signature = sign(settings.whatsapp_app_secret.get_secret_value().encode("utf-8"), raw)
            with TestClient(create_app(settings)) as client:
                response = client.post("/webhooks/whatsapp", content=raw, headers={"X-Hub-Signature-256": signature})
            print(f"webhook: HTTP {response.status_code}")
        elif args.command == "work":
            root = Path(__file__).resolve().parents[2]
            knowledge_repository = configured_knowledge(settings, repository)
            knowledge = (load_synthetic_catalog(root / "fixtures" / "salon.versioned.synthetic.json")
                         if args.fixture and knowledge_repository is None else KnowledgeBase())
            agent = build_agent(knowledge, configured_limits(settings), prompt_version=settings.ai_prompt_version,
                                ai_history_limit=settings.ai_history_limit,
                                ai_timeout_seconds=settings.ai_timeout_seconds, conversation_repository=repository,
                                knowledge_repository=knowledge_repository,
                                provider=build_ai_provider(settings, repository))
            transport = FakeWhatsAppTransport(args.behavior)
            policy = queue_policy(settings)
            # Em homologação, a guarda (allowlist, limites, período) é obrigatória mesmo no fake.
            dispatcher = configured_dispatcher(settings, store, transport, run_id=uuid4().hex)
            steps = drain(InboundProcessor(store, agent, policy), dispatcher, "local-cli")
            print("etapas: " + (", ".join(steps) or "nada pendente"))
            for delivery in transport.deliveries:
                print(f"[ENVIO SIMULADO — nada saiu desta máquina] comportamento={delivery.behavior}: {delivery.body}")
        elif args.command == "status":
            print(json.dumps(store.counts(), sort_keys=True))
        elif args.command == "halt":
            store.halt(args.reason, set_by="operador-local")
            print("Envios interrompidos. Outbox preservada; nenhuma tentativa ou retry até resume-dispatch.")
        elif args.command == "resume-dispatch":
            store.resume_dispatch(confirmation=args.confirmation, set_by="operador-local")
            print("Envios retomados por decisão humana local.")
        elif args.command == "metrics":
            print(json.dumps(store.metrics(), sort_keys=True, ensure_ascii=False))
        elif args.command == "trace":
            print(json.dumps(store.trace(args.inbox_id), sort_keys=True, ensure_ascii=False))
        elif args.command == "homolog-check":
            authorization = homologation_authorization(settings)
            if authorization is None:
                raise SimulationError("Defina APP_ENV=homologation e HOMOLOG_AUTHORIZATION_FILE.")
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            print(json.dumps({
                "authorization_id": authorization.authorization_id,
                "active_now": authorization.active(now),
                "valid_from": authorization.valid_from.isoformat(), "valid_until": authorization.valid_until.isoformat(),
                "allowlist_size": len(authorization.allowed_recipients),
                "operations": sorted(authorization.operations),
                "limits": asdict(authorization.limits),
                "transport": settings.whatsapp_transport,
                "real_transport_code_lock": config_module.HOMOLOGATION_TRANSPORT_ACTIVATION_AUTHORIZED,
                "dispatch_halted": store.halt_status() is not None or settings.channel_dispatch_halted,
                # Esta CLI nunca envia de verdade; o real depende da Fase 6B e da trava acima.
                "real_send_possible_from_this_cli": False,
            }, sort_keys=True, ensure_ascii=False))
        else:
            store.requeue_unknown(args.operation, confirmation=args.confirmation)
            print("Reenvio incerto reagendado por decisão humana local.")
    except (SimulationError, ConfigurationError, OSError, ValueError) as error:
        message = str(error) if isinstance(error, (SimulationError, ConfigurationError, ChannelError)) else "entrada inválida."
        print(f"Erro: {message}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
