"""CLI local do canal (Fase 4): webhook simulado, worker e envio FAKE. Sem rede.

post   FIXTURE  assina com o App Secret configurado e entrega ao webhook em processo
                (ASGI em memória, sem socket). "timestamp": "NOW" vira o horário atual.
work            processa inbox e outbox até esvaziar, com FakeWhatsAppTransport
status          contagem por estado (sem conteúdo)
resend-uncertain OPERACAO RESEND_UNCERTAIN:OPERACAO   decisão humana de reenvio incerto
"""

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any

from rocha_reis_agent.bootstrap import build_agent, configured_channel, configured_repository, configured_limits, queue_policy
from rocha_reis_agent.catalog_io import load_synthetic_catalog
from rocha_reis_agent.channels.fake_transport import FakeWhatsAppTransport
from rocha_reis_agent.channels.meta import sign
from rocha_reis_agent.channels.service import InboundProcessor, OutboxDispatcher, drain
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
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
        if not settings.whatsapp_enabled:
            raise SimulationError("Defina WHATSAPP_ENABLED=true com segredos locais (veja .env.example).")
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
            knowledge = (load_synthetic_catalog(root / "fixtures" / "salon.versioned.synthetic.json")
                         if args.fixture else KnowledgeBase())
            agent = build_agent(knowledge, configured_limits(settings), prompt_version=settings.ai_prompt_version,
                                ai_history_limit=settings.ai_history_limit,
                                ai_timeout_seconds=settings.ai_timeout_seconds, conversation_repository=repository)
            transport = FakeWhatsAppTransport(args.behavior)
            policy = queue_policy(settings)
            steps = drain(InboundProcessor(store, agent, policy), OutboxDispatcher(store, transport, policy), "local-cli")
            print("etapas: " + (", ".join(steps) or "nada pendente"))
            for delivery in transport.deliveries:
                print(f"[ENVIO SIMULADO — nada saiu desta máquina] comportamento={delivery.behavior}: {delivery.body}")
        elif args.command == "status":
            print(json.dumps(store.counts(), sort_keys=True))
        else:
            store.requeue_unknown(args.operation, confirmation=args.confirmation)
            print("Reenvio incerto reagendado por decisão humana local.")
    except (SimulationError, ConfigurationError, OSError, ValueError) as error:
        message = str(error) if isinstance(error, (SimulationError, ConfigurationError)) else "entrada inválida."
        print(f"Erro: {message}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
