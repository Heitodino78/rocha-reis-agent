"""Regras do painel sobre o núcleo existente (sem segundo sistema de mensagens).

Cada ação altera a conversa pelo ConversationManager (versão otimista) e encena,
no MESMO commit, atribuição, outbox e auditoria. Permissões são verificadas aqui,
no backend; a interface é só uma conveniência.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
import re
from uuid import uuid4

from rocha_reis_agent.application.conversations import ConversationManager
from rocha_reis_agent.application.handoff import HandoffAction, HumanHandoffManager
from rocha_reis_agent.application.retry import retry_conflicts
from rocha_reis_agent.domain.contracts import (
    ConcurrentUpdate, ConversationState as State, InvalidInput, SimulationError, StorageError, normalize_message,
)
from rocha_reis_agent.domain.conversations import HistoryEntry, utc_now
from rocha_reis_agent.panel.security import PanelError, hash_password, normalize_login, validate_password
from rocha_reis_agent.panel.store import PanelUser, SQLitePanelStore
from rocha_reis_agent.persistence.sqlite_channel import AssignmentLost, SQLiteChannelStore

IDEMPOTENCY = re.compile(r"[A-Za-z0-9_-]{16,64}")
MAX_REPLY_CHARS = 2000


class PanelService:
    def __init__(self, *, conversations: ConversationManager, handoff: HumanHandoffManager,
                 store: SQLitePanelStore, channel: SQLiteChannelStore, window_seconds: float,
                 clock: Callable[[], datetime] = utc_now) -> None:
        self.conversations = conversations
        self.handoff = handoff
        self.store = store
        self.channel = channel
        self.window_seconds = window_seconds
        self.clock = clock

    # ---------------------------------------------------------------- acesso
    def require_visible(self, user: PanelUser, conversation_id: str) -> dict[str, object]:
        """IDOR/BOLA: toda ação começa provando que o usuário pode ver a conversa."""
        if not isinstance(conversation_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", conversation_id):
            raise PanelError(404, "Conversa não encontrada.")
        summary = self.store.visible_conversation(user, conversation_id)
        if summary is None:
            raise PanelError(404, "Conversa não encontrada.")  # mesma resposta para inexistente e proibida
        return summary

    def _run(self, user: PanelUser, action: str, conversation_id: str, operation: Callable[[], None]) -> None:
        try:
            operation()
        except AssignmentLost:
            self.store.audit(user.id, action, "conversation", conversation_id, "failed")
            raise PanelError(409, "A responsabilidade pela conversa mudou. Atualize e tente novamente.") from None
        except PanelError:
            self.store.audit(user.id, action, "conversation", conversation_id, "denied")
            raise
        except (InvalidInput, StorageError) as error:
            self.store.audit(user.id, action, "conversation", conversation_id, "failed")
            raise PanelError(409, "Operação não concluída; tente novamente.") from error
        except SimulationError:
            self.store.audit(user.id, action, "conversation", conversation_id, "denied")
            raise PanelError(409, "Ação incompatível com o estado atual da conversa.") from None

    # ------------------------------------------------------------- transições
    def assume(self, user: PanelUser, conversation_id: str) -> None:
        self.require_visible(user, conversation_id)

        @retry_conflicts
        def operation() -> None:
            with self.conversations.edit(conversation_id) as conversation:
                if conversation.state != State.WAITING_HUMAN:
                    raise SimulationError("Conversa não está aguardando atendimento.")
                self.handoff.act_verified(conversation, HandoffAction.ASSUME, user.operator)
                self.store.stage_assign(conversation_id, user.id, expected=None, actor_id=user.id)
                self.store.stage_audit(user.id, "conversation_assumed", "conversation", conversation_id)

        self._run(user, "conversation_assumed", conversation_id, operation)

    def transfer(self, user: PanelUser, conversation_id: str, target_user_id: object) -> None:
        self.require_visible(user, conversation_id)
        target = self.store.user(target_user_id) if isinstance(target_user_id, str) else None
        if target is None or not target.active:
            raise PanelError(422, "Funcionário de destino inválido ou inativo.")

        @retry_conflicts
        def operation() -> None:
            with self.conversations.edit(conversation_id) as conversation:
                current = self.store.assignment(conversation_id)
                if user.role != "ADMIN":
                    if current != user.id:
                        raise PanelError(403, "Só o responsável pode transferir esta conversa.")
                    if not self.store.settings()["operators_can_transfer"]:
                        raise PanelError(403, "Transferência por atendentes está desabilitada.")
                if current == target.id:
                    raise PanelError(409, "A conversa já está com esse funcionário.")
                self.handoff.record_transfer(conversation, user.operator)
                self.store.stage_assign(conversation_id, target.id, expected=current, actor_id=user.id)
                self.store.stage_audit(user.id, "conversation_transferred", "conversation", conversation_id)

        self._run(user, "conversation_transferred", conversation_id, operation)

    def resolve(self, user: PanelUser, conversation_id: str) -> None:
        self.require_visible(user, conversation_id)

        @retry_conflicts
        def operation() -> None:
            with self.conversations.edit(conversation_id) as conversation:
                current = self.store.assignment(conversation_id)
                if user.role != "ADMIN" and current != user.id:
                    raise PanelError(403, "Só o responsável pode finalizar este atendimento.")
                # Finalizar NÃO reativa o agente: RESOLVED mantém a automação suspensa.
                self.handoff.act_verified(conversation, HandoffAction.RESOLVE, user.operator)
                self.store.stage_audit(user.id, "conversation_resolved", "conversation", conversation_id)

        self._run(user, "conversation_resolved", conversation_id, operation)

    def resume(self, user: PanelUser, conversation_id: str) -> None:
        self.require_visible(user, conversation_id)

        @retry_conflicts
        def operation() -> None:
            with self.conversations.edit(conversation_id) as conversation:
                current = self.store.assignment(conversation_id)
                if user.role != "ADMIN":
                    if current != user.id or not self.store.settings()["operators_can_resume"]:
                        raise PanelError(403, "Retomada da automação reservada a administradores.")
                self.handoff.act_verified(conversation, HandoffAction.RESUME, user.operator)
                self.store.stage_unassign(conversation_id)
                self.store.stage_audit(user.id, "automation_resumed", "conversation", conversation_id)

        self._run(user, "automation_resumed", conversation_id, operation)

    def request_human(self, user: PanelUser, conversation_id: str) -> None:
        if user.role != "ADMIN":
            raise PanelError(403, "Somente administradores transferem conversas automáticas para a fila.")
        self.require_visible(user, conversation_id)

        @retry_conflicts
        def operation() -> None:
            with self.conversations.edit(conversation_id) as conversation:
                self.handoff.request_by_operator(conversation, user.operator)
                self.store.stage_audit(user.id, "handoff_requested", "conversation", conversation_id)

        self._run(user, "handoff_requested", conversation_id, operation)

    # ------------------------------------------------------------------ resposta
    def reply(self, user: PanelUser, conversation_id: str, text: object, idempotency_key: object) -> dict[str, object]:
        self.require_visible(user, conversation_id)
        if not isinstance(idempotency_key, str) or IDEMPOTENCY.fullmatch(idempotency_key) is None:
            raise PanelError(422, "Identificador de envio inválido.")
        try:
            body = normalize_message(text if isinstance(text, str) else "", MAX_REPLY_CHARS)  # type: ignore[arg-type]
        except InvalidInput:
            raise PanelError(422, f"Escreva uma mensagem de 1 a {MAX_REPLY_CHARS} caracteres, sem caracteres de controle.") from None
        result: dict[str, object] = {}

        @retry_conflicts
        def operation() -> None:
            existing = self.channel.operator_message(idempotency_key)
            if existing is not None:
                # Clique repetido/reenvio da mesma requisição: nada novo é criado.
                if existing["conversation_id"] != conversation_id or existing["operator_user_id"] != user.id:
                    raise PanelError(409, "Identificador de envio já utilizado.")
                result.update(operation_id=existing["id"], duplicate=True)
                return
            contact = self.channel.contact_window(conversation_id)
            if contact is None:
                raise PanelError(409, "Sem mensagem do cliente pelo WhatsApp; não há como responder por este canal.")
            if self.clock() - contact.last_inbound_at > timedelta(seconds=self.window_seconds):
                raise PanelError(409, "A janela de 24h de atendimento terminou; respostas livres não são permitidas.")
            operation_id = uuid4().hex
            with self.conversations.edit(conversation_id) as conversation:
                if conversation.closed or conversation.state != State.HUMAN_ACTIVE:
                    raise PanelError(409, "Responda somente durante o atendimento humano ativo.")
                if self.store.assignment(conversation_id) != user.id:
                    raise PanelError(403, "Você não é o responsável por esta conversa.")
                conversation.history.append(HistoryEntry("operator", body, utc_now(), operation_id=operation_id))
                self.channel.stage_operator_message(
                    operation_id=operation_id, idempotency_key=idempotency_key, conversation_id=conversation_id,
                    session_id=conversation.session_id, operator_user_id=user.id, body=body, contact=contact,
                )
                self.store.stage_audit(user.id, "operator_message_queued", "conversation", conversation_id, operation_id)
            result.update(operation_id=operation_id, duplicate=False)

        def settle() -> None:
            # Cliques simultâneos podem esgotar as retentativas de conflito do SQLite enquanto
            # outro pedido com a MESMA chave grava a mensagem. Antes de responder erro, confere-se
            # a chave: se já existe, é o mesmo envio (sucesso idempotente, nada novo é criado).
            # Também a violação de unicidade da chave (StorageError): o outro pedido gravou entre a
            # verificação e o commit deste, que foi revertido por inteiro (Fase 11, corrida real).
            try:
                operation()
            except StorageError:  # inclui ConcurrentUpdate
                existing = self.channel.operator_message(idempotency_key)
                if existing is None or existing["conversation_id"] != conversation_id \
                        or existing["operator_user_id"] != user.id:
                    raise
                result.update(operation_id=existing["id"], duplicate=True)

        self._run(user, "operator_message_queued", conversation_id, settle)
        return result

    # ----------------------------------------------------------------- equipe
    def create_user(self, actor: PanelUser, *, login: object, display_name: object, role: object,
                    password: object) -> dict[str, object]:
        self._admin(actor)
        clean = normalize_login(login)
        name = display_name.strip() if isinstance(display_name, str) else ""
        if not 1 <= len(name) <= 80 or not name.isprintable():
            raise PanelError(422, "Informe um nome de exibição de 1 a 80 caracteres.")
        if role not in ("ADMIN", "OPERATOR"):
            raise PanelError(422, "Papel inválido.")
        validate_password(password, clean)
        # Senha inicial definida pelo administrador: troca obrigatória no primeiro acesso.
        return self.store.create_user(login=clean, display_name=name, password_hash=hash_password(password),  # type: ignore[arg-type]
                                      role=role, must_change_password=True, actor_id=actor.id).public()

    def set_active(self, actor: PanelUser, user_id: str, active: bool) -> int:
        """Ativa/desativa. Ao desativar, os atendimentos em andamento da pessoa voltam à fila de espera
        (antes ficavam presos a alguém que não entra mais). Retorna quantos voltaram."""
        self._admin(actor)
        self.store.set_active(user_id, active, actor_id=actor.id)
        if active:
            return 0
        released = 0
        for conversation_id in self.store.active_assignments(user_id):
            @retry_conflicts
            def operation(conversation_id: str = conversation_id) -> bool:
                with self.conversations.edit(conversation_id) as conversation:
                    if conversation.closed or conversation.state != State.HUMAN_ACTIVE \
                            or self.store.assignment(conversation_id) != user_id:
                        return False  # mudou desde a consulta: nada a devolver
                    self.handoff.release_to_queue(conversation, actor.operator)
                    self.store.stage_unassign(conversation_id)
                    self.store.stage_audit(actor.id, "conversation_requeued", "conversation", conversation_id)
                    return True
            try:
                released += int(operation())
            except (SimulationError, StorageError):
                # Fica com o responsável desativado; a Visão geral da administração alerta (orphaned_active).
                self.store.audit(actor.id, "conversation_requeued", "conversation", conversation_id, "failed")
        return released

    def reset_password(self, actor: PanelUser, user_id: str, password: object) -> None:
        self._admin(actor)
        target = self.store.user(user_id)
        if target is None:
            raise PanelError(404, "Funcionário não encontrado.")
        validate_password(password, target.login)
        self.store.set_password(user_id, hash_password(password), must_change=True, actor_id=actor.id)  # type: ignore[arg-type]

    @staticmethod
    def _admin(user: PanelUser) -> None:
        if user.role != "ADMIN":
            raise PanelError(403, "Ação reservada a administradores.")
