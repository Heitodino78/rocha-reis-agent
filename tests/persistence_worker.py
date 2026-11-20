"""Worker de integração executado em outro processo; nunca contata rede."""

import json
import os
from pathlib import Path
import socket
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rocha_reis_agent.bootstrap import build_agent
from rocha_reis_agent.catalog_io import load_synthetic_catalog
from rocha_reis_agent.domain.contracts import InboundMessage, StorageError
from rocha_reis_agent.domain.conversations import utc_now
from rocha_reis_agent.persistence.sqlite import SQLiteConversationRepository
from rocha_reis_agent.ai.fake import FakeAIProvider


def blocked(*args, **kwargs):
    raise AssertionError("Network forbidden in persistence worker")


socket.socket.connect = socket.socket.connect_ex = socket.socket.sendto = blocked
socket.getaddrinfo = socket.create_connection = blocked
path = Path(sys.argv[1])
mode = sys.argv[2]
repository = SQLiteConversationRepository(path)
catalog = load_synthetic_catalog(Path(__file__).resolve().parents[1] / "fixtures/salon.versioned.synthetic.json")
agent = build_agent(catalog, conversation_repository=repository)
if mode == "crash":
    def crash(stage):
        if stage == "after_events": os._exit(73)
    repository.fault_hook = crash
    agent.handle(InboundMessage("interrupted", "cliente-a", "Quero um humano", utc_now()))
elif mode == "race":
    class Gate:
        entered = False
        def generate(self, request, *, timeout_seconds):
            if not self.entered:
                self.entered = True
                print("READY", flush=True)
                if sys.stdin.readline().strip() != "COMMIT": raise AssertionError("Missing release")
            return FakeAIProvider().generate(request, timeout_seconds=timeout_seconds)
    agent.provider = Gate()
    result = agent.handle(InboundMessage("shared", "cliente-a", "Quanto custa o corte?", utc_now()))
    print(json.dumps({"duplicate": result.duplicate, "reply": bool(result.reply)}), flush=True)
elif mode == "inspect":
    conversation = agent.conversations.snapshot("cliente-a")
    print(json.dumps({"state": conversation.state, "context": conversation.service_context,
                      "events": sorted(conversation.processed_events), "history": len(conversation.history),
                      "audit": [row["operation"] for row in repository.audit("cliente-a")]}))
elif mode == "initialize":
    print("INITIALIZED", flush=True)
else:
    raise AssertionError("Unknown worker operation")
