"""Worker contínuo do canal (Fase 17): inbox → núcleo → outbox → transporte, 24/7, para o systemd.

Antes desta fase só havia o worker EMBUTIDO da demonstração (sempre transporte falso) e o comando
`local_cli work`, que esvazia as filas uma vez. Em servidor, o webhook só grava na inbox: sem este
processo nenhuma mensagem seria respondida.

Garantias preservadas (são das filas, não deste laço): leases com expiração, idempotência por evento,
envio incerto vira UNKNOWN e NUNCA é reenviado às cegas. Parar no meio (SIGTERM, queda, kill -9) é seguro:
o item em andamento volta à fila quando o lease vence ou vira UNKNOWN se já estava sendo enviado.

    python -m rocha_reis_agent.worker              # configuração do ambiente (EnvironmentFile no systemd)

Transporte: o configurado (`build_transport`), que só é real com a trava de código E configuração válida.
Integração com o systemd (sem dependências): READY=1 ao iniciar, WATCHDOG=1 a cada volta do laço
(processo travado é reiniciado pelo WatchdogSec) e STOPPING=1 no encerramento. Batimento em arquivo
opcional (WORKER_HEARTBEAT_FILE) para o diagnóstico e os alertas locais.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import signal
import socket
import sys
from threading import Event
import time
from typing import Callable
from uuid import uuid4

logger = logging.getLogger("rocha_reis_agent")

MAX_CONSECUTIVE_FAILURES = 20  # falha persistente (ex.: banco inacessível): sai com erro e o systemd reinicia


def notify(message: str) -> bool:
    """Protocolo sd_notify mínimo (datagrama no NOTIFY_SOCKET). Sem systemd, não faz nada."""
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return False
    if address.startswith("@"):
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(address)
            sock.sendall(message.encode("ascii"))
        return True
    except OSError:
        return False


def write_heartbeat(path: Path | None, *, processed: int, failures: int) -> bool:
    """Batimento é SINAL de vida, nunca motivo de queda. Fase 18B-0 (achado no laboratório com disco LUKS
    real): se o volume some, a pasta do batimento some junto e a exceção derrubava o worker com código 1
    no meio da parada controlada. Agora registra e segue; o alerta `worker_sem_batimento` vigia a falta."""
    if path is None:
        return True
    temporary = path.with_name(path.name + ".tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "at": time.time(), "processed": processed,
                       "consecutive_failures": failures}, handle)
        os.replace(temporary, path)
        return True
    except OSError:
        logger.warning("worker_heartbeat_failed")  # sem caminho nem detalhe (mesma regra do canal)
        return False


def read_heartbeat(path: Path) -> dict[str, object] | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


class _RecordingFake:
    """Transporte FALSO que registra cada envio aceito num arquivo (ensaios com processo morto: o registro
    sobrevive ao kill -9 e permite provar que nada foi enviado duas vezes). Só existe para o fake."""

    def __init__(self, inner, path: Path) -> None:  # type: ignore[no-untyped-def]
        self.inner = inner
        self.path = path

    def send_text(self, message, *, timeout_seconds: float):  # type: ignore[no-untyped-def]
        outcome = self.inner.send_text(message, timeout_seconds=timeout_seconds)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            os.write(descriptor, (json.dumps({"operation": message.operation_id,
                                              "outcome": type(outcome).__name__}) + "\n").encode())
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return outcome


def build_pipeline(settings, *, record_path: Path | None = None):  # type: ignore[no-untyped-def]
    """Mesma composição do restante do sistema (sem segunda implementação de regras)."""
    from rocha_reis_agent.bootstrap import (
        build_agent, build_ai_provider, build_transport, configured_channel, configured_dispatcher,
        configured_knowledge, configured_limits, configured_repository, queue_policy,
    )
    from rocha_reis_agent.channels.service import InboundProcessor
    from rocha_reis_agent.config import ConfigurationError
    from rocha_reis_agent.knowledge import KnowledgeBase
    from rocha_reis_agent.panel.store import SQLitePanelStore

    if not settings.whatsapp_enabled:
        raise ConfigurationError("Worker exige WHATSAPP_ENABLED=true (canal configurado).")
    if settings.knowledge_source != "database":
        # Em servidor, o conhecimento é o aprovado pelo painel; o modo arquivo é só de desenvolvimento.
        raise ConfigurationError("Worker exige KNOWLEDGE_SOURCE=database (conhecimento aprovado pelo painel).")
    repository = configured_repository(settings)
    store, _ = configured_channel(settings, repository)
    knowledge_repository = configured_knowledge(settings, repository)
    agent = build_agent(KnowledgeBase(), configured_limits(settings), conversation_repository=repository,
                        prompt_version=settings.ai_prompt_version, ai_history_limit=settings.ai_history_limit,
                        ai_timeout_seconds=settings.ai_timeout_seconds, knowledge_repository=knowledge_repository,
                        provider=build_ai_provider(settings, repository))
    transport = build_transport(settings)
    if record_path is not None:
        if settings.whatsapp_transport != "fake":
            raise ConfigurationError("Registro de envios só existe para o transporte FALSO.")
        transport = _RecordingFake(transport, record_path)
    inbound = InboundProcessor(store, agent, queue_policy(settings))
    outbound = configured_dispatcher(settings, store, transport, run_id=uuid4().hex)
    panel = SQLitePanelStore(repository)  # limpeza do feed de mudanças do painel (como o worker embutido)
    return inbound, outbound, panel


def run(settings, stop: Event, *, idle_seconds: float = 1.0, batch: int = 50,  # type: ignore[no-untyped-def]
        heartbeat: Path | None = None, record_path: Path | None = None,
        on_step: Callable[[list[str]], None] | None = None) -> int:
    """Laço principal. Retorna 0 no encerramento pedido; 1 após falhas persistentes."""
    from rocha_reis_agent.channels.service import drain

    inbound, outbound, panel = build_pipeline(settings, record_path=record_path)
    worker_id = f"worker-{os.getpid()}"
    processed = failures = 0
    notify("READY=1\nSTATUS=processando filas")
    logger.info("worker_started")
    while not stop.is_set():
        try:
            steps = drain(inbound, outbound, worker_id, limit=batch)
            panel.prune_changes()
            processed += len(steps)
            failures = 0
            if on_step is not None:
                on_step(steps)
        except Exception:
            # Sem exceção, texto ou identificadores no log (mesma regra do restante do canal).
            failures += 1
            logger.error("worker_cycle_failed", extra={"failure_kind": "processing_error"})
            if failures >= MAX_CONSECUTIVE_FAILURES:
                notify("STATUS=falhas persistentes; saindo para o systemd reiniciar")
                logger.error("worker_giving_up")
                return 1
            steps = []
        notify("WATCHDOG=1")
        write_heartbeat(heartbeat, processed=processed, failures=failures)
        if not steps:
            stop.wait(min(30.0, idle_seconds * (2 ** min(failures, 5))))
    notify("STOPPING=1")
    logger.info("worker_stopped")
    return 0


def main(argv: list[str] | None = None) -> int:
    from rocha_reis_agent.config import ConfigurationError, load_settings

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)
    stop = Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())  # termina a etapa atual e sai (sem cortar transação)
    try:
        settings = load_settings()
        heartbeat = os.environ.get("WORKER_HEARTBEAT_FILE")
        record = os.environ.get("WORKER_FAKE_SEND_LOG")
        return run(settings, stop, heartbeat=Path(heartbeat) if heartbeat else None,
                   record_path=Path(record) if record else None)
    except ConfigurationError as error:
        print(f"Configuração inválida: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
