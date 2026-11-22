"""Roteiro guiado da RC1 Offline (Fase 14): executa as jornadas da demonstração de ponta a ponta,
num perfil de demonstração NOVO e isolado, e informa o resultado de cada uma.

Usa os componentes reais: webhook assinado (em processo), inbox, núcleo, conhecimento aprovado,
outbox, transporte FAKE e a API HTTP do painel com as contas fictícias da demonstração.
Nada sai da máquina; não toca em nenhum outro perfil. Não substitui a validação com pessoas.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import tempfile
from typing import Callable
from uuid import uuid4

from rocha_reis_agent.ops.backup import BackupError


@dataclass
class Step:
    id: str
    title: str
    ok: bool = False
    detail: str = ""
    evidence: list[str] = field(default_factory=list)


class _Panel:
    def __init__(self, app, origin: str) -> None:  # type: ignore[no-untyped-def]
        from fastapi.testclient import TestClient
        self.client = TestClient(app, base_url=origin)
        self.origin = origin
        self.csrf = ""

    def login(self, login: str, password: str):  # type: ignore[no-untyped-def]
        response = self.client.post("/api/panel/auth/login", json={"login": login, "password": password},
                                    headers={"Origin": self.origin})
        if response.status_code == 200:
            self.csrf = response.json().get("csrf_token", "")
        return response

    def get(self, path: str):  # type: ignore[no-untyped-def]
        return self.client.get("/api/panel" + path)

    def post(self, path: str, body: dict | None = None):  # type: ignore[no-untyped-def]
        response = self.client.post("/api/panel" + path, json=body or {},
                                    headers={"Origin": self.origin, "X-CSRF-Token": self.csrf})
        if response.status_code == 200 and isinstance(response.json(), dict) and response.json().get("csrf_token"):
            self.csrf = response.json()["csrf_token"]
        return response


def run_rehearsal(workspace: Path | None = None) -> dict[str, object]:
    from rocha_reis_agent import RELEASE_NAME, __version__
    from rocha_reis_agent import config as config_module
    from rocha_reis_agent.bootstrap import (
        build_agent, configured_dispatcher, configured_knowledge, configured_limits, configured_repository, queue_policy,
    )
    from rocha_reis_agent.channels.fake_transport import FakeWhatsAppTransport
    from rocha_reis_agent.channels.service import InboundProcessor, drain
    from rocha_reis_agent.ops.demo import build_demo, profile_settings, send_fictitious
    from rocha_reis_agent.panel import mfa
    from rocha_reis_agent.panel.app import create_panel_app
    from rocha_reis_agent.persistence.sqlite_channel import SQLiteChannelStore

    if any(config_module.activation_locks().values()):  # as cinco (fonte única em config.py)
        raise BackupError("Alguma trava de integração real está aberta: o roteiro se recusa a executar.")
    base = Path(workspace) if workspace else Path(tempfile.mkdtemp(prefix="rr-roteiro-"))
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(base, 0o700)
    profile = base / "demo-roteiro"
    build_demo(profile)
    settings = profile_settings(profile)
    credentials = {line.split("\t")[0]: line.split("\t")[2]
                   for line in (profile / "credenciais-demo.txt").read_text(encoding="utf-8").splitlines()[1:]}
    repository = configured_repository(settings)
    channel = SQLiteChannelStore(repository)
    agent = build_agent(None, configured_limits(settings), conversation_repository=repository,
                        knowledge_repository=configured_knowledge(settings, repository))
    inbound = InboundProcessor(channel, agent, queue_policy(settings))
    transport = FakeWhatsAppTransport()
    outbound = configured_dispatcher(settings, channel, transport, run_id=uuid4().hex)
    app = create_panel_app(settings)
    panels: dict[str, _Panel] = {}

    def panel(login: str) -> _Panel:
        if login not in panels:
            panels[login] = _Panel(app, settings.panel_origin)
            if panels[login].login(login, credentials[login]).status_code != 200:
                raise BackupError(f"Login fictício {login} falhou.")
        return panels[login]

    def customer(number: str, text: str) -> str:
        send_fictitious(profile, text, sender=number)
        drain(inbound, outbound, "roteiro")
        return transport.deliveries[-1].body if transport.deliveries else ""

    def conversation_of(viewer: _Panel, filter_key: str, contains: str) -> str:
        for item in viewer.get(f"/conversations?filter={filter_key}").json()["items"]:
            detail = viewer.get(f"/conversations/{item['id']}").json()
            if any(contains in m["text"] for m in detail["messages"]):
                return str(item["id"])
        raise BackupError(f"Conversa com '{contains}' não encontrada em '{filter_key}'.")

    steps: list[Step] = []

    def flow(identifier: str, title: str, body: Callable[[Step], None]) -> None:
        step = Step(identifier, title)
        try:
            body(step)
            step.ok = True
        except (AssertionError, BackupError, KeyError, ValueError) as error:
            step.detail = str(error) or type(error).__name__
        steps.append(step)

    def f1(step: Step) -> None:
        reply = customer("5511900000501", "Quanto custa um corte?")
        assert "Corte de teste: R$ 45,00" in reply, reply
        step.evidence.append(reply)

    def f2(step: Step) -> None:
        reply = customer("5511900000502", "Quanto custa a coloração?")
        assert "a partir de R$ 120,00" in reply and "pode variar" in reply, reply
        step.evidence.append(reply)

    def f3(step: Step) -> None:
        reply = customer("5511900000503", "Quanto custa a escova?")
        assert "Não tenho preço confirmado" in reply and "R$" not in reply, reply
        reply2 = customer("5511900000503", "Vocês têm promoção de black friday?")
        assert "Não tenho informação confirmada sobre promoções" in reply2, reply2
        step.evidence += [reply, reply2]

    def f4(step: Step) -> None:
        reply = customer("5511900000504", "Quero marcar um horário para sábado")
        assert "https://example.invalid/booksy-sintetico" in reply and "Não consultei disponibilidade" in reply, reply
        assert not re.search(r"(?i)\b(agendado para|está confirmado|temos horário)\b", reply), reply
        step.evidence.append(reply)

    state: dict[str, str] = {}

    def f5(step: Step) -> None:
        customer("5511900000505", "Quero falar com uma pessoa, por favor")
        ana = panel("ana.demo")
        cid = conversation_of(ana, "waiting", "Quero falar com uma pessoa, por favor")
        assert ana.post(f"/conversations/{cid}/assume").status_code == 204
        detail = ana.get(f"/conversations/{cid}").json()
        assert detail["state"] == "HUMAN_ACTIVE" and detail["assigned"]["name"] == "Ana (demo)"
        state["cid"] = cid

    def f6(step: Step) -> None:
        ana = panel("ana.demo")
        cid = state["cid"]
        response = ana.post(f"/conversations/{cid}/reply", {"text": "Olá! Aqui é a Ana (demonstração).",
                                                            "idempotency_key": uuid4().hex})
        assert response.status_code == 200, response.text
        drain(inbound, outbound, "roteiro")
        before = len(transport.deliveries)
        customer("5511900000505", "Quanto custa um corte?")  # agente suspenso: não responde sozinho
        assert len(transport.deliveries) == before, "o agente respondeu durante o atendimento humano"
        assert ana.get(f"/conversations/{cid}").json()["messages"][-2]["delivery"]["key"] == "accepted"

    def f7(step: Step) -> None:
        ana, bia = panel("ana.demo"), panel("bia.demo")
        cid = state["cid"]
        bia_id = next(u["id"] for u in ana.get("/operators").json() if u["display_name"] == "Bia (demo)")
        assert ana.post(f"/conversations/{cid}/transfer", {"target_user_id": bia_id}).status_code == 204
        assert ana.get(f"/conversations/{cid}").status_code == 404
        assert bia.get(f"/conversations/{cid}").json()["assigned"]["name"] == "Bia (demo)"
        assert "assigned_to_me" in [n["type"] for n in bia.get("/notifications").json()["items"]]

    def f8(step: Step) -> None:
        bia = panel("bia.demo")
        cid = state["cid"]
        assert bia.post(f"/conversations/{cid}/resolve").status_code == 204
        detail = bia.get(f"/conversations/{cid}").json()
        assert detail["state"] == "RESOLVED"
        assert bia.post(f"/conversations/{cid}/resume").status_code == 403  # só a administração retoma

    def f9(step: Step) -> None:
        admin = panel("admin.demo")
        history = admin.get("/knowledge/history?fact_id=price:escova").json()
        assert history[-1]["status"] == "DRAFT"
        assert "Não tenho preço confirmado" in admin.post("/knowledge/preview", {"question": "Quanto custa a escova?"}).json()["reply"]
        assert admin.post("/knowledge/approve", {"fact_id": "price:escova", "version": history[-1]["version"]}).status_code == 200
        preview = admin.post("/knowledge/preview", {"question": "Quanto custa a escova?"}).json()
        assert preview["simulated"] is True and "R$ 60,00" in preview["reply"], preview
        step.evidence.append(preview["reply"])

    def f10(step: Step) -> None:
        admin = panel("admin.demo")
        cid = conversation_of(admin, "all", "Quero falar com alguém.")
        messages = admin.get(f"/conversations/{cid}").json()["messages"]
        assert messages[-1]["delivery"]["key"] == "unknown"
        sent = len(transport.deliveries)
        drain(inbound, outbound, "roteiro")
        assert len(transport.deliveries) == sent  # nunca reenviado sozinho
        assert "send_unknown" in [n["type"] for n in admin.get("/notifications").json()["items"]]

    def f11(step: Step) -> None:
        ana = panel("ana.demo")
        attempts = [ana.get("/users"), ana.get("/audit"), ana.get("/system"), ana.get("/knowledge"),
                    ana.post("/knowledge/approve", {"fact_id": "price:corte", "version": 1}),
                    ana.post("/dispatch/halt", {"reason": "teste"}), ana.post("/settings", {})]
        codes = [r.status_code for r in attempts]
        assert codes[:6] == [403] * 6 and codes[6] in (403, 405), codes

    def f12(step: Step) -> None:
        admin = panel("admin.demo")
        before = admin.get(f"/conversations/{state['cid']}").json()
        restarted = create_panel_app(profile_settings(profile))  # novo processo do painel, mesmo banco
        again = _Panel(restarted, settings.panel_origin)
        assert again.login("admin.demo", credentials["admin.demo"]).status_code == 200
        after = again.get(f"/conversations/{state['cid']}").json()
        assert after["state"] == before["state"] and after["messages"] == before["messages"]
        assert len(again.get("/audit").json()) >= 10

    def f13(step: Step) -> None:
        admin = _Panel(app, settings.panel_origin)
        assert admin.login("admin.demo", credentials["admin.demo"]).status_code == 200
        setup = admin.post("/auth/mfa/setup", {"password": credentials["admin.demo"]}).json()
        raw = setup["secret"].replace(" ", "")
        secret = base64.b32decode(raw + "=" * (-len(raw) % 8))
        activated = admin.post("/auth/mfa/activate", {"code": mfa.code_for(secret)})
        assert activated.status_code == 200 and len(activated.json()["recovery_codes"]) == 10
        fresh = _Panel(app, settings.panel_origin)
        assert fresh.login("admin.demo", credentials["admin.demo"]).json() == {"mfa_required": True}
        assert fresh.get("/overview").status_code == 401  # etapa intermediária sem acesso
        recovery = fresh.client.post("/api/panel/auth/mfa", json={"recovery_code": activated.json()["recovery_codes"][0]},
                                     headers={"Origin": settings.panel_origin})
        assert recovery.status_code == 200

    for identifier, title, body in (
        ("F1", "Cliente pergunta preço (conhecimento aprovado)", f1),
        ("F2", "Preço variável responde 'a partir de'", f2),
        ("F3", "Informação ausente não é inventada", f3),
        ("F4", "Agendamento só com o link Booksy fictício, sem confirmar horário", f4),
        ("F5", "Pedido de pessoa entra na fila e a atendente assume", f5),
        ("F6", "Resposta da equipe e agente suspenso", f6),
        ("F7", "Transferência para outra atendente", f7),
        ("F8", "Encerramento; retomar a automação é só da administração", f8),
        ("F9", "Administração aprova rascunho e confere a prévia simulada", f9),
        ("F10", "Envio incerto mostrado e nunca reenviado sozinho", f10),
        ("F11", "Atendente não alcança funções da administração", f11),
        ("F12", "Reinício preserva histórico, estados e auditoria", f12),
        ("F13", "Verificação em duas etapas: cadastro, etapa intermediária sem acesso e recuperação", f13),
    ):
        flow(identifier, title, body)
    passed = sum(step.ok for step in steps)
    return {"versao": f"{RELEASE_NAME} ({__version__})", "aprovados": passed, "total": len(steps),
            "resultado": "OK" if passed == len(steps) else "FALHOU",
            "fluxos": [{"id": s.id, "titulo": s.title, "ok": s.ok, **({"detalhe": s.detail} if s.detail else {})}
                       for s in steps],
            "perfil_isolado": str(profile),
            "aviso": "Roteiro automático com dados fictícios e transporte simulado. Não é validação pelo salão."}
