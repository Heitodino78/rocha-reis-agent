"""Medição LOCAL de desempenho do painel com volume SINTÉTICO (Fase 10).

Gera o volume pelo caminho real (ingestão → núcleo → outbox fake) num diretório
temporário e mede as consultas do painel. Os números dependem da máquina: são
medições desta execução, não garantias. Nada é enviado; nenhuma IA real é chamada.
"""

from __future__ import annotations

import os
from pathlib import Path
import platform
import resource
import threading
import secrets
import statistics
import tempfile
from time import perf_counter
from uuid import uuid4

from rocha_reis_agent.bootstrap import (
    build_agent, configured_channel, configured_dispatcher, configured_limits, configured_repository, queue_policy,
)
from rocha_reis_agent.catalog_io import load_synthetic_catalog
from rocha_reis_agent.channels.fake_transport import FakeWhatsAppTransport
from rocha_reis_agent.channels.service import InboundProcessor, drain
from rocha_reis_agent.config import Settings
from rocha_reis_agent.ops.backup import KeyMaterial, create_backup, generate_key, verify_backup
from rocha_reis_agent.panel.admin_cli import _envelope
from rocha_reis_agent.panel.security import hash_password
from rocha_reis_agent.panel.store import SQLitePanelStore

ROOT = Path(__file__).resolve().parents[2]


def _timed(function, repeat: int) -> dict[str, float]:  # type: ignore[no-untyped-def]
    samples = []
    for _ in range(repeat):
        start = perf_counter()
        function()
        samples.append((perf_counter() - start) * 1000)
    samples.sort()
    return {"mediana_ms": round(statistics.median(samples), 2),
            "p95_ms": round(samples[max(0, int(len(samples) * 0.95) - 1)], 2), "max_ms": round(samples[-1], 2)}


def measure(conversations: int = 1000, long_messages: int = 400, repeat: int = 30) -> dict[str, object]:
    if not 10 <= conversations <= 1000 or not 10 <= long_messages <= 900:
        raise ValueError("Use 10–1000 conversas e 10–900 mensagens na conversa longa.")
    with tempfile.TemporaryDirectory(prefix="rr-perf-") as tmp:
        os.chmod(tmp, 0o700)
        base = Path(tmp)
        settings = Settings(_env_file=None, app_env="test", storage_backend="sqlite",  # type: ignore[call-arg]
                            storage_path=base / "perf.synthetic.sqlite3", storage_max_bytes=512 * 1024 * 1024,
                            sim_max_conversations=1000, sim_max_history_entries=1000,
                            whatsapp_enabled=True, whatsapp_verify_token=secrets.token_urlsafe(24),
                            whatsapp_app_secret=secrets.token_urlsafe(32),
                            whatsapp_conversation_key=secrets.token_urlsafe(32),
                            whatsapp_phone_number_id="100000000000001", panel_enabled=True)
        repository = configured_repository(settings)
        channel, ingestor = configured_channel(settings, repository)
        agent = build_agent(load_synthetic_catalog(ROOT / "fixtures" / "salon.versioned.synthetic.json"),
                            configured_limits(settings), conversation_repository=repository)
        inbound = InboundProcessor(channel, agent, queue_policy(settings))
        outbound = configured_dispatcher(settings, channel, FakeWhatsAppTransport(), run_id=uuid4().hex)
        store = SQLitePanelStore(repository)
        admin = store.create_user(login="admin.perf", display_name="Admin", password_hash=hash_password(
            secrets.token_urlsafe(16)), role="ADMIN", must_change_password=False, actor_id=None)
        operator = store.create_user(login="ana.perf", display_name="Ana", password_hash=hash_password(
            secrets.token_urlsafe(16)), role="OPERATOR", must_change_password=False, actor_id=None)

        started = perf_counter()
        for index in range(conversations - 1):  # +1 conversa longa = total pedido (teto do protótipo: 1000 ativas)
            text = "Quero falar com uma pessoa" if index % 10 == 0 else "Quanto custa o corte?"
            ingestor.ingest(_envelope(settings, f"551190{index:07d}", text))
        long_sender = "5511999999999"
        for index in range(long_messages // 2):  # cada mensagem do cliente gera uma resposta
            ingestor.ingest(_envelope(settings, long_sender, f"Quanto custa o corte? ({index})"))
        drain(inbound, outbound, "perf", limit=10 * (conversations + long_messages))
        generation_s = perf_counter() - started

        long_id = store.list_conversations(admin, filter_key="auto", query="(0)", cursor=None, page_size=5)["items"][0]["id"]
        first_page = store.messages_page(long_id, limit=settings.panel_history_page_size)
        cursor = store.latest_change()
        results = {
            "lista_todas_admin": _timed(lambda: store.list_conversations(
                admin, filter_key="all", query=None, cursor=None, page_size=25), repeat),
            "lista_aguardando_atendente": _timed(lambda: store.list_conversations(
                operator, filter_key="waiting", query=None, cursor=None, page_size=25), repeat),
            "busca_texto_admin": _timed(lambda: store.list_conversations(
                admin, filter_key="all", query="corte", cursor=None, page_size=25), repeat),
            "visao_geral_admin": _timed(lambda: store.overview(admin), repeat),
            "avisos_admin": _timed(lambda: store.notifications(admin), repeat),
            "avisos_atendente": _timed(lambda: store.notifications(operator), repeat),
            "historico_pagina_recente": _timed(lambda: store.messages_page(
                long_id, limit=settings.panel_history_page_size), repeat),
            "historico_pagina_anterior": _timed(lambda: store.messages_page(
                long_id, before=first_page["before"], limit=settings.panel_history_page_size), repeat),
            "historico_completo_legado": _timed(lambda: store.messages(long_id), max(3, repeat // 5)),
            "feed_mudancas": _timed(lambda: store.changes_since(admin, max(0, cursor - 50)), repeat),
            "sistema": _timed(store.system_status, repeat),
        }
        # Fase 12 — conhecimento: prévia e ciclo rascunho→aprovação pelo serviço real do painel.
        from rocha_reis_agent.knowledge_catalog import KnowledgeScope
        from rocha_reis_agent.panel.knowledge import KnowledgeService
        from rocha_reis_agent.persistence.sqlite_knowledge import SQLiteKnowledgeRepository
        knowledge_repo = SQLiteKnowledgeRepository(repository)
        knowledge_repo.initialize(KnowledgeScope.SYNTHETIC)
        knowledge_repo.import_catalog(load_synthetic_catalog(ROOT / "fixtures" / "salon.versioned.synthetic.json"),
                                      author="perf")
        knowledge = KnowledgeService(knowledge_repo, store)
        admin_user = store.user(admin.id)
        results["conhecimento_previa"] = _timed(lambda: knowledge.preview(admin_user, "Quanto custa o corte?"), repeat)
        cycle = iter(range(10_000))

        def approve_cycle() -> None:
            name = f"cat-{next(cycle)}"
            draft = knowledge.draft(admin_user, kind="category", key=name, fields={"name": f"Categoria {name}"},
                                    source="perf sintético", expected_version=None)
            knowledge.approve(admin_user, draft["fact_id"], draft["version"])
        results["conhecimento_rascunho_e_aprovacao"] = _timed(approve_cycle, max(5, repeat // 3))

        # Operadores simultâneos (leituras do painel em paralelo).
        errors: list[str] = []

        def operator_loop() -> None:
            for _ in range(repeat):
                try:
                    store.list_conversations(operator, filter_key="waiting", query=None, cursor=None, page_size=25)
                    store.notifications(operator)
                    store.overview(operator)
                except Exception as error:  # noqa: BLE001 — medido, não escondido
                    errors.append(type(error).__name__)
        started = perf_counter()
        threads = [threading.Thread(target=operator_loop) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        concurrent = {"operadores": 4, "ciclos_por_operador": repeat,
                      "total_s": round(perf_counter() - started, 2), "erros": errors}

        key = generate_key(base / "chaves" / "k.key")
        # Backup DURANTE operação: mensagens chegando enquanto a cópia é feita.
        stop = threading.Event()
        written: list[int] = []

        def writer() -> None:
            index = 0
            while not stop.is_set():
                # clientes JÁ existentes: não esbarra no teto de 1000 conversas ativas do protótipo
                ingestor.ingest(_envelope(settings, f"551190{index % (conversations - 1):07d}", "Quanto custa o corte?"))
                drain(inbound, outbound, "perf-bk", limit=10)
                index += 1
            written.append(index)
        thread = threading.Thread(target=writer)
        thread.start()
        start = perf_counter()
        backup = create_backup(settings.storage_path, base / "backups", KeyMaterial("keyfile", key_file=key))  # type: ignore[arg-type]
        backup_ms = (perf_counter() - start) * 1000
        stop.set()
        thread.join()
        start = perf_counter()
        configured_repository(settings)  # "reinício": abrir, verificar e migrar (nada a migrar)
        reopen_ms = (perf_counter() - start) * 1000
        start = perf_counter()
        verify_backup(backup, KeyMaterial("keyfile", key_file=key))
        verify_ms = (perf_counter() - start) * 1000
        counts = store.system_status()
        return {
            "aviso": "Medição local desta máquina com dados sintéticos; não é garantia de desempenho em produção.",
            "maquina": {"python": platform.python_version(), "sistema": platform.system(), "cpus": os.cpu_count()},
            "volume": {"conversas": counts["counts"]["conversations"], "mensagens": counts["counts"]["messages"],
                       "mensagens_na_conversa_longa": len(store.messages(long_id)),
                       "saidas": counts["counts"]["channel_outbox"], "banco_mb": round(counts["database_bytes"] / 2 ** 20, 2),
                       "geracao_s": round(generation_s, 1)},
            "repeticoes": repeat, "consultas": results,
            "backup": {"criar_ms": round(backup_ms, 1), "verificar_ms": round(verify_ms, 1),
                       "tamanho_mb": round(backup.stat().st_size / 2 ** 20, 2),
                       "mensagens_gravadas_durante_o_backup": written[0] if written else 0},
            "simultaneidade": concurrent,
            "reabrir_banco_ms": round(reopen_ms, 1),
            "memoria_pico_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        }
