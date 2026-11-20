"""Transições explícitas; autorização é apenas local, sem autenticação remota."""

from enum import StrEnum

from rocha_reis_agent.domain.contracts import ConversationState as State, SimulationError
from rocha_reis_agent.domain.conversations import Conversation, StateChange, utc_now


class HandoffAction(StrEnum):
    ASSUME = "assume"
    RESOLVE = "resolve"
    RESUME = "resume"


class OperatorAssurance(StrEnum):
    """Grau de confiança na identidade do operador.

    Só existe SIMULATED_LOCAL: quem tem acesso ao terminal local é tratado como
    operador. NÃO é autenticação. Uma futura interface operacional deverá
    introduzir um nível verificado (ex.: VERIFIED_SSO) com autenticação forte,
    autorização por papel e auditoria por pessoa; até lá nenhuma rota de rede
    pode chamar estas ações.
    """

    SIMULATED_LOCAL = "simulated_local"


LOCAL_SIMULATED_OPERATOR = "operador-local"


class HumanHandoffManager:
    _transitions = {
        (State.WAITING_HUMAN, HandoffAction.ASSUME): State.HUMAN_ACTIVE,
        (State.WAITING_HUMAN, HandoffAction.RESOLVE): State.RESOLVED,
        (State.HUMAN_ACTIVE, HandoffAction.RESOLVE): State.RESOLVED,
        (State.RESOLVED, HandoffAction.RESUME): State.AUTO,
    }

    def __init__(self, operators: frozenset[str] = frozenset({LOCAL_SIMULATED_OPERATOR})) -> None:
        self.operators = operators
        # Identidades aceitas aqui são SIMULADAS; nunca promovidas a verificadas.
        self.assurance = OperatorAssurance.SIMULATED_LOCAL

    def authorize(self, operator_id: str) -> OperatorAssurance:
        if not isinstance(operator_id, str) or operator_id not in self.operators:
            raise SimulationError("Operação reservada ao operador local autorizado.")
        return self.assurance

    def request(self, conversation: Conversation, *, reason: str = "client_request") -> None:
        if conversation.state != State.AUTO:
            raise SimulationError("Encaminhamento indisponível no estado atual.")
        self._change(conversation, State.WAITING_HUMAN, reason, None)

    def act(
        self, conversation: Conversation, action: HandoffAction, operator_id: str
    ) -> None:
        self.authorize(operator_id)
        if conversation.closed:
            raise SimulationError("Sessão encerrada; use /reset para iniciar outra.")
        target = self._transitions.get((conversation.state, action))
        if target is None:
            raise SimulationError("Transição inválida para o estado atual.")
        self._change(conversation, target, action.value, operator_id)

    @staticmethod
    def _change(
        conversation: Conversation, target: State, action: str, operator_id: str | None
    ) -> None:
        conversation.transitions.append(StateChange(
            conversation.state, target, action, operator_id, utc_now()
        ))
        conversation.state = target
