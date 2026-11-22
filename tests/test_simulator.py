"""CLI em memória, incluindo comandos administrativos separados de mensagens."""

from io import StringIO
from pathlib import Path

from rocha_reis_agent.domain.contracts import ConversationState
from rocha_reis_agent.simulator import Simulator, main


def test_complete_cli_flow_with_two_customers_and_human_operator(agent):
    script = StringIO("""/start cliente-a
/send a1 Quero informações sobre corte.
/send a2 E quanto custa?
/send a3 Quero agendar para sábado.
/start cliente-b
/send b1 E quanto custa?
/use cliente-a
/send a4 Quero um humano.
/send a5 /operator resume
/operator assume
/operator reply Atendimento humano fictício.
/send a6 Olá
/operator resolve
/send a7 Pode retomar?
/operator resolve
/operator resume
/send a8 E quanto custa?
/send a8 E quanto custa?
/history
/state
/audit
/quit
""")
    output = StringIO()
    Simulator(agent, output).run(script)
    text = output.getvalue()
    assert "R$ 45,00" in text
    assert "De qual serviço" in text
    assert "https://example.invalid/booksy-sintetico" in text
    assert "Nenhum atendente real foi notificado" in text
    assert "Estado: HUMAN_ACTIVE" in text
    assert "Estado: RESOLVED" in text
    assert "Estado: AUTO" in text
    assert "[duplicata ignorada]" in text
    assert "[automático pausado; mensagem registrada]" in text
    assert "operator: Atendimento humano fictício." in text
    assert "client_request: AUTO -> WAITING_HUMAN" in text
    assert "client_returned: RESOLVED -> WAITING_HUMAN" in text  # ADR-134 (22/11)
    assert agent.conversations.snapshot("cliente-b").service_context == ()


def test_cli_validation_does_not_terminate_session(agent):
    output = StringIO()
    simulator = Simulator(agent, output)
    simulator.run(StringIO("""/send x Olá
/start cliente-a
/send
/send id
/operator unknown
/operator assume extra
/send x Olá
/close
/send y Olá
/reset CONFIRM_SYNTHETIC_RESET
/send x Olá
/quit
"""))
    text = output.getvalue()
    assert "Selecione uma conversa" in text
    assert "Use /send EVENTO TEXTO" in text
    assert "Sessão encerrada" in text
    assert "Nova sessão em AUTO" in text
    assert "Erro:" in text
    assert agent.conversations.snapshot("cliente-a").state == ConversationState.AUTO
    assert not agent.conversations.snapshot("cliente-a").closed


def test_cli_does_not_interpret_bare_text_as_operator_commands(agent):
    output = StringIO()
    Simulator(agent, output).run(StringIO("/start cliente-a\nresume\n/quit\n"))
    assert "Texto de cliente exige /send" in output.getvalue()
    assert not agent.conversations.snapshot("cliente-a").history


def test_main_default_keeps_business_data_pending(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", StringIO("/start cliente-a\n/send e1 Quero agendar\n/quit\n"))
    assert main([]) == 0
    text = capsys.readouterr().out
    assert "não há link Booksy válido" in text
    assert "https://" not in text


def test_main_loads_bundled_fixture_without_network(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", StringIO("/start cliente-a\n/send e1 Quanto custa o corte?\n/quit\n"))
    assert main(["--fixture"]) == 0
    assert "DADOS FICTÍCIOS" in capsys.readouterr().out


def test_main_invalid_knowledge_returns_safe_exit_code(tmp_path: Path, capsys):
    path = tmp_path / "invalid.json"
    path.write_text("PRIVATE_SYNTHETIC_MARKER", encoding="utf-8")
    assert main(["--knowledge", str(path)]) == 2
    captured = capsys.readouterr()
    assert "PRIVATE_SYNTHETIC_MARKER" not in captured.err
    assert "JSON sintético válido" in captured.err


def test_main_applies_limits_from_environment(monkeypatch, capsys):
    monkeypatch.setenv("SIM_MAX_MESSAGE_CHARS", "3")
    monkeypatch.setattr("sys.stdin", StringIO("/start cliente-a\n/send e1 Olá!\n/send e2 Oi\n/quit\n"))
    assert main([]) == 0
    output = capsys.readouterr().out
    assert "até 3 caracteres" in output
    assert "Olá! Sou o assistente" in output
