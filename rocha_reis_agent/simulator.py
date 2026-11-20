"""CLI local; comandos administrativos nunca são despachados a partir do texto do cliente."""

import argparse
from pathlib import Path
import sys
from typing import TextIO

from rocha_reis_agent.application.handoff import HandoffAction
from rocha_reis_agent.application.service import AgentService
from rocha_reis_agent.config import ConfigurationError, load_settings
from rocha_reis_agent.domain.contracts import InboundMessage, SimulationError
from rocha_reis_agent.domain.conversations import utc_now
from rocha_reis_agent.knowledge import KnowledgeBase, load_synthetic_knowledge
from rocha_reis_agent.catalog_io import load_synthetic_catalog
from rocha_reis_agent.bootstrap import build_agent, configured_limits, configured_repository
from rocha_reis_agent.persistence.sqlite import SQLiteConversationRepository
from pydantic import ValidationError

HELP_TEXT = """Comandos locais (use somente clientes e mensagens fictícios):
/start CLIENTE             iniciar sessão e selecionar cliente
/use CLIENTE               alternar para sessão existente
/send EVENTO TEXTO         enviar mensagem de cliente; repetir EVENTO testa duplicata
/history                   consultar histórico limitado
/state                     consultar estado e contexto
/audit                     consultar transições administrativas
/provenance                consultar referências e versão do prompt das respostas
/operator assume           operador local assume a conversa em espera
/operator reply TEXTO      registrar resposta humana simulada
/operator resolve          encerrar atendimento humano (permanece pausado)
/operator resume           retomar explicitamente após RESOLVED
/close                     encerrar sessão
/reset CONFIRMAÇÃO         reiniciar; exige CONFIRM_SYNTHETIC_RESET
/help                      exibir comandos
/storage-audit             consultar auditoria técnica persistente
/delete CONFIRMAÇÃO        excluir conteúdo sintético; DELETE_SYNTHETIC:CLIENTE
/purge PURGE_SYNTHETIC      anonimizar conversas inativas (retenção); não bloqueia o cliente
/quit                      sair; memória é descartada, SQLite permanece"""


class Simulator:
    def __init__(self, agent: AgentService, output: TextIO) -> None:
        self.agent = agent
        self.output = output
        self.current: str | None = None
        # Acesso ao terminal é a fronteira de confiança, sem login remoto nesta fase.
        self.operator_id = "operador-local"

    def emit(self, text: str) -> None:
        print(text, file=self.output, flush=True)

    def selected(self) -> str:
        if self.current is None:
            raise SimulationError("Selecione uma conversa com /start ou /use.")
        return self.current

    def execute(self, line: str) -> bool:
        parts = line.strip().split(maxsplit=1)
        if not parts:
            return True
        command = parts[0]
        argument = parts[1] if len(parts) == 2 else ""
        try:
            if command == "/quit":
                return False
            if command == "/help":
                self.emit(HELP_TEXT)
            elif command == "/start":
                self.agent.conversations.start(argument)
                self.current = argument
                self.emit("Sessão iniciada em AUTO.")
            elif command == "/use":
                self.agent.conversations.snapshot(argument)
                self.current = argument
                self.emit("Conversa selecionada.")
            elif command == "/send":
                message = argument.split(maxsplit=1)
                if len(message) != 2:
                    raise SimulationError("Use /send EVENTO TEXTO.")
                result = self.agent.handle(InboundMessage(
                    message[0], self.selected(), message[1], utc_now()
                ))
                self.emit(result.reply or (
                    "[duplicata ignorada]" if result.duplicate else "[automático pausado; mensagem registrada]"
                ))
            elif command == "/operator":
                self._operator(argument)
            elif command in {"/state", "/history", "/audit", "/provenance"}:
                self._inspect(command)
            elif command in {"/storage-audit", "/delete", "/purge"}:
                self.agent.handoff.authorize(self.operator_id)
                repository = self.agent.conversations.repository
                if not isinstance(repository, SQLiteConversationRepository):
                    raise SimulationError("Comando exige banco sintético SQLite explícito.")
                if command == "/storage-audit":
                    for entry in repository.audit(self.selected()):
                        self.emit(f"{entry['operation']}: {entry['outcome']}; correlação={entry['correlation']}")
                elif command == "/delete":
                    repository.retire(self.selected(), confirmation=argument)
                    self.emit("Conteúdo sintético excluído; identificador e chaves de eventos permanecem bloqueados contra reprocessamento.")
                else:
                    count = repository.purge_expired(confirmation=argument)
                    self.emit(f"Retenção aplicada: {count} conversas sintéticas anonimizadas e arquivadas; podem ser retomadas.")
            elif command in {"/close", "/reset"}:
                self.agent.handoff.authorize(self.operator_id)
                manager = self.agent.conversations
                if command == "/close":
                    manager.close(self.selected())
                    self.emit("Sessão encerrada.")
                else:
                    durable = isinstance(manager.repository, SQLiteConversationRepository)
                    if argument != "CONFIRM_SYNTHETIC_RESET":
                        raise SimulationError("Use /reset CONFIRM_SYNTHETIC_RESET para confirmar a limpeza da sessão sintética.")
                    manager.reset(self.selected())
                    self.emit("Nova sessão em AUTO; histórico e contexto descartados; eventos preservados." if durable else "Nova sessão em AUTO; histórico, contexto e eventos anteriores descartados.")
            else:
                raise SimulationError("Comando desconhecido; use /help. Texto de cliente exige /send.")
        except SimulationError as error:
            self.emit(f"Erro: {error}")
        return True

    def _operator(self, argument: str) -> None:
        parts = argument.split(maxsplit=1)
        if not parts:
            raise SimulationError("Use /operator assume, reply, resolve ou resume.")
        if parts[0] == "reply":
            if len(parts) != 2:
                raise SimulationError("Use /operator reply TEXTO.")
            self.agent.operator_reply(self.selected(), parts[1], operator_id=self.operator_id)
            self.emit("Resposta do operador registrada somente nesta simulação.")
            return
        try:
            action = HandoffAction(parts[0])
        except ValueError:
            raise SimulationError("Ação de operador desconhecida.") from None
        if len(parts) != 1:
            raise SimulationError("A ação de estado não aceita texto adicional.")
        state = self.agent.operator_action(self.selected(), action, operator_id=self.operator_id)
        self.emit(f"Estado: {state.value}")

    def _inspect(self, command: str) -> None:
        conversation = self.agent.conversations.snapshot(self.selected())
        if command == "/state":
            self.emit(
                f"Estado: {conversation.state.value}; encerrada={conversation.closed}; "
                f"contexto={','.join(conversation.service_context) or 'ausente'}; "
                f"histórico={len(conversation.history)}; eventos={len(conversation.processed_events)}"
            )
        elif command == "/history":
            for entry in conversation.history:
                self.emit(f"{entry.role}: {entry.text}")
        elif command == "/audit":
            for entry in conversation.transitions:
                self.emit(f"{entry.action}: {entry.previous.value} -> {entry.current.value}")
        else:
            for entry in conversation.history:
                if entry.role == "assistant":
                    self.emit(
                        f"evento={entry.event_id}; prompt={entry.prompt_version or 'não aplicado'}; "
                        f"fatos={','.join(entry.fact_references) or 'nenhum'}"
                    )

    def run(self, source: TextIO) -> None:
        self.emit("SIMULAÇÃO DETERMINÍSTICA LOCAL — FakeAIProvider, sem IA real, mensagens externas ou atendente real.")
        self.emit("Use somente dados fictícios. /help lista comandos; /quit encerra.")
        while True:
            if source.isatty():
                print("> ", end="", file=self.output, flush=True)
            line = source.readline()
            if not line or not self.execute(line):
                break


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Simulador local determinístico Rocha Reis Agent")
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--fixture", action="store_true", help="usar a demonstração fictícia incluída")
    choice.add_argument("--knowledge", type=Path, help="JSON local obrigatoriamente synthetic=true")
    choice.add_argument("--catalog", type=Path, help="catálogo sintético versionado, schema_version=2")
    parser.add_argument("--database", type=Path, help="banco local *.synthetic.sqlite3; preserva dados ao sair")
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
        if args.database is not None:
            settings = type(settings).model_validate({**settings.model_dump(), "storage_backend": "sqlite", "storage_path": args.database})
        path = args.knowledge
        if args.fixture:
            args.catalog = Path(__file__).resolve().parents[1] / "fixtures" / "salon.versioned.synthetic.json"
        knowledge = (
            load_synthetic_catalog(args.catalog) if args.catalog is not None else
            load_synthetic_knowledge(path) if path is not None else KnowledgeBase()
        )
        limits = configured_limits(settings)
        conversation_repository = configured_repository(settings)
        Simulator(build_agent(
            knowledge, limits, prompt_version=settings.ai_prompt_version,
            ai_history_limit=settings.ai_history_limit, ai_timeout_seconds=settings.ai_timeout_seconds,
            conversation_repository=conversation_repository,
        ), sys.stdout).run(sys.stdin)
    except ValidationError:
        print("Erro: Configuração de armazenamento inválida.", file=sys.stderr)
        return 2
    except (SimulationError, ConfigurationError) as error:
        print(f"Erro: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
