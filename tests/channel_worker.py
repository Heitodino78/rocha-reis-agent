"""Worker do canal executado em OUTRO processo; rede bloqueada."""

import json
import os
from pathlib import Path
import socket
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def blocked(*args, **kwargs):
    raise AssertionError("Network forbidden in channel worker")


socket.socket.connect = socket.socket.connect_ex = socket.socket.sendto = blocked
socket.getaddrinfo = socket.create_connection = blocked

from fastapi.testclient import TestClient  # noqa: E402

from rocha_reis_agent.channels.fake_transport import FakeWhatsAppTransport  # noqa: E402
from rocha_reis_agent.channels.service import drain  # noqa: E402
from rocha_reis_agent.main import create_app  # noqa: E402
from whatsapp_support import Clock, envelope, post, settings, stack, text_message  # noqa: E402

directory = Path(sys.argv[1])
mode = sys.argv[2]
payload = envelope(text_message("wamid.PROC", "Quanto custa o corte?"))

if mode == "post":
    # Requisição HTTP em processo (ASGI, sem socket), igual à que a Meta faria.
    with TestClient(create_app(settings(directory))) as client:
        print(json.dumps({"status": post(client, payload).status_code}), flush=True)
elif mode == "process":
    s = stack(directory)
    print(json.dumps({"inbound": s.inbound.process_one("p")}), flush=True)
elif mode == "crash_during_send":
    def crash(message):
        os._exit(75)  # morte abrupta depois de SENDING confirmado, durante o "envio"
    s = stack(directory, transport=FakeWhatsAppTransport(on_send=crash))
    s.outbound.dispatch_one("crasher")
elif mode == "recover":
    clock = Clock()
    clock.advance(3600)  # lease do worker morto já venceu
    s = stack(directory, clock=clock)
    steps = drain(s.inbound, s.outbound, "recover")
    rows = s.store.outbox_rows()
    print(json.dumps({"steps": steps, "sent": len(s.transport.deliveries),
                      "outbox": [r["status"] for r in rows], "outcome": [r["outcome"] for r in rows]}), flush=True)
elif mode == "dispatch":
    s = stack(directory)
    steps = drain(s.inbound, s.outbound, "d")
    print(json.dumps({"steps": steps, "sent": len(s.transport.deliveries),
                      "bodies": [d.body for d in s.transport.deliveries]}), flush=True)
else:
    raise AssertionError("unknown mode")
