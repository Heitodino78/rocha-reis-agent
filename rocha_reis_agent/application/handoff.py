"""Transições explícitas; autorização é apenas local, sem autenticação remota."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

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
    # Fase 7: funcionário autenticado no painel (sessão + senha Argon2id + RBAC).
    VERIFIED_PANEL = "verified_panel"


@dataclass(frozen=True, slots=True)
class VerifiedOperator:
    """Identidade construída SOMENTE pela camada de autenticação do painel.

    Não é derivada de texto de cliente nem de parâmetro livre de requisição.
    """

    user_id: str
    role: Literal["ADMIN", "OPERATOR"]

    @property
    def audit_id(self) -> str:
        return f"panel:{self.user_id}"


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

    def act_verified(self, conversation: Conversation, action: HandoffAction, operator: VerifiedOperator) -> None:
        """Mesmas transições do domínio, com identidade verificada (painel)."""
        if not isinstance(operator, VerifiedOperator):
            raise SimulationError("Operação exige identidade verificada.")
        if conversation.closed:
            raise SimulationError("Sessão encerrada; ação indisponível.")
        target = self._transitions.get((conversation.state, action))
        if target is None:
            raise SimulationError("Transição inválida para o estado atual.")
        self._change(conversation, target, action.value, operator.audit_id)

    def request_by_operator(self, conversation: Conversation, operator: VerifiedOperator) -> None:
        """Transferir do automático para a fila humana (AUTO → WAITING_HUMAN)."""
        if not isinstance(operator, VerifiedOperator) or conversation.closed:
            raise SimulationError("Operação indisponível.")
        if conversation.state != State.AUTO:
            raise SimulationError("Transição inválida para o estado atual.")
        self._change(conversation, State.WAITING_HUMAN, "operator_request", operator.audit_id)

    def release_to_queue(self, conversation: Conversation, operator: VerifiedOperator) -> None:
        """Responsável desativado (HUMAN_ACTIVE → WAITING_HUMAN): o cliente volta à fila para outra
        pessoa assumir. A automação continua suspensa; a mudança é versionada e auditada."""
        if not isinstance(operator, VerifiedOperator) or conversation.closed:
            raise SimulationError("Operação indisponível.")
        if conversation.state != State.HUMAN_ACTIVE:
            raise SimulationError("Transição inválida para o estado atual.")
        self._change(conversation, State.WAITING_HUMAN, "owner_deactivated", operator.audit_id)

    def record_transfer(self, conversation: Conversation, operator: VerifiedOperator) -> None:
        """Transferência entre funcionários: sem mudança de estado, mas versionada e auditada."""
        if conversation.state != State.HUMAN_ACTIVE or conversation.closed:
            raise SimulationError("Transferência só durante atendimento humano ativo.")
        self._change(conversation, State.HUMAN_ACTIVE, "transfer", operator.audit_id)

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
