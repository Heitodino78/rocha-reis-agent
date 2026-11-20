"""Ensaio ponta a ponta OFFLINE em ambiente de homologação, com reinício real a cada etapa.

Cada comando é um processo novo da CLI (`channels.local_cli`): webhook assinado em
processo (ASGI, sem socket), worker com FakeWhatsAppTransport e guarda obrigatória.
Isto NÃO valida a integração real com a Meta.
"""

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from homolog_support import AUTH_ID, authorization_document, write_authorization
from whatsapp_support import APP_SECRET, CLIENT_A, CLIENT_B, CONVERSATION_KEY, PHONE_ID, VERIFY, WABA_ID, envelope, status, text_message

ROOT = Path(__file__).resolve().parents[1]


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        self.dir = tmp_path
        self.db = tmp_path / "data" / "ensaio.homolog.synthetic.sqlite3"
        self.log = tmp_path / "logs" / "canal-homolog.log"
        self.auth = write_authorization(tmp_path, authorization_document(limits={
            "max_messages_total": 20, "max_messages_per_recipient": 10,
            "max_messages_per_run": 5, "max_messages_per_minute": 10}))
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("APP_", "SIM_", "AI_", "STORAGE_", "WHATSAPP_", "CHANNEL_", "HOMOLOG_"))}
        self.env.update({
            "PYTHONPATH": str(ROOT), "APP_ENV": "homologation", "STORAGE_BACKEND": "sqlite",
            "STORAGE_PATH": str(self.db), "WHATSAPP_ENABLED": "true", "WHATSAPP_VERIFY_TOKEN": VERIFY,
            "WHATSAPP_APP_SECRET": APP_SECRET, "WHATSAPP_CONVERSATION_KEY": CONVERSATION_KEY,
            "WHATSAPP_PHONE_NUMBER_ID": PHONE_ID, "WHATSAPP_BUSINESS_ACCOUNT_ID": WABA_ID,
            "HOMOLOG_AUTHORIZATION_FILE": str(self.auth), "CHANNEL_LOG_PATH": str(self.log),
        })
        self.count = 0

    def cli(self, *args: str) -> str:
        result = subprocess.run([sys.executable, "-m", "rocha_reis_agent.channels.local_cli", *args],
                                capture_output=True, text=True, cwd=self.dir, env=self.env, timeout=60)
        assert result.returncode == 0, result.stderr
        return result.stdout

    def post(self, payload: dict) -> str:
        self.count += 1
        path = self.dir / f"evento-{self.count}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return self.cli("post", str(path))

    def query(self, sql: str):
        with closing(sqlite3.connect(self.db)) as connection:
            return connection.execute(sql).fetchall()


def test_offline_homologation_end_to_end_with_process_restarts(tmp_path):
    rig = Rig(tmp_path)
    check = json.loads(rig.cli("homolog-check"))
    assert check["authorization_id"] == AUTH_ID and check["active_now"] and check["allowlist_size"] == 2
    assert check["real_transport_code_lock"] is False and check["real_send_possible_from_this_cli"] is False
    assert CLIENT_A not in json.dumps(check)

    # Recebimento: válida, duplicada, remetente não autorizado.
    assert "HTTP 200" in rig.post(envelope(text_message("wamid.E1", "Quero informações sobre corte.")))
    assert "HTTP 200" in rig.post(envelope(text_message("wamid.E1", "Quero informações sobre corte.")))
    assert "HTTP 200" in rig.post(envelope(text_message("wamid.X1", "Olá", CLIENT_B)))
    first = rig.cli("work", "--fixture")
    assert "etapas: replied, ACCEPTED" in first and first.count("[ENVIO SIMULADO") == 1

    # Contexto após reinício.
    rig.post(envelope(text_message("wamid.E2", "E quanto custa?")))
    assert "R$ 45,00" in rig.cli("work", "--fixture")

    # Status: progressão, duplicata, fora de ordem e sem correlação.
    provider = rig.query("SELECT provider_message_id FROM channel_outbox ORDER BY rowid LIMIT 1")[0][0]
    rig.post(envelope(status(provider, "read")))                       # chega primeiro (fora de ordem)
    rig.post(envelope(status(provider, "read")))                       # duplicata: deduplicada na inbox
    rig.post(envelope(status(provider, "sent"), status(provider, "delivered"),
                      status("wamid.NAO_CORRELACIONADO", "delivered")))
    assert rig.cli("work", "--fixture").startswith(
        "etapas: status_read, status_out_of_order_ignored, status_out_of_order_ignored, status_unmatched")
    assert rig.query("SELECT delivery_status FROM channel_outbox ORDER BY rowid LIMIT 1") == [("read",)]

    # Atendimento humano preservado entre processos.
    rig.post(envelope(text_message("wamid.E3", "Quero falar com uma pessoa")))
    assert "Nenhum atendente real foi notificado" in rig.cli("work", "--fixture")
    rig.post(envelope(text_message("wamid.E4", "Quanto custa?")))
    assert "recorded_without_reply" in rig.cli("work", "--fixture")

    # Interrupção emergencial: resposta fica na outbox; retomada explícita envia uma vez.
    assert "interrompidos" in rig.cli("halt", "Ensaio de interrupção")
    rig.post(envelope(text_message("wamid.E5", "Olá", None, user_id="BR.99887766")))
    halted = rig.cli("work", "--fixture")
    assert "etapas: replied" in halted and "[ENVIO SIMULADO" not in halted
    assert rig.query("SELECT status FROM channel_outbox ORDER BY rowid DESC LIMIT 1") == [("PENDING",)]
    rig.cli("resume-dispatch", "RESUME_DISPATCH")
    resumed = rig.cli("work", "--fixture")
    assert "ACCEPTED" in resumed and resumed.count("[ENVIO SIMULADO") == 1

    # Métricas e rastreamento por identificadores técnicos, sem dados pessoais.
    metrics_text = rig.cli("metrics")
    metrics = json.loads(metrics_text)
    counters = metrics["counters"]
    assert counters["webhook_events_duplicate"] >= 2 and counters["webhook_ignored_not_allowlisted"] == 1
    assert counters["dispatch_blocked_halt"] == 1 and counters["dispatch_halts"] == 1
    assert counters["send_attempts"] == 4 and "dispatch_blocked_homolog_rate_limit" not in counters
    assert metrics["outbox_status"]["ACCEPTED"] == 4 and metrics["status_anomalies"] == 2
    assert metrics["human_transitions"] == {"state_client_request": 1}
    trace_text = rig.cli("trace", "1")
    trace = json.loads(trace_text)
    assert trace["outbox"][0]["delivery_status"] == "read" and trace["outbox"][0]["has_provider_id"] == 1
    assert [s["outcome"] for s in trace["statuses"]] == [
        "status_read", "status_out_of_order_ignored", "status_out_of_order_ignored"]
    assert trace["send_ledger"][0]["authorization_id"] == AUTH_ID
    log = rig.log.read_text(encoding="utf-8")
    for private in (CLIENT_A, CLIENT_B, "BR.99887766", "wamid.", "R$", "corte", VERIFY, APP_SECRET):
        assert private not in metrics_text and private not in trace_text and private not in log
    assert os.stat(rig.log).st_mode & 0o777 == 0o600 and os.stat(rig.db).st_mode & 0o777 == 0o600

    # Dados de teste isolados: nenhum banco no diretório do projeto.
    assert not list(ROOT.glob("**/*.sqlite3"))
