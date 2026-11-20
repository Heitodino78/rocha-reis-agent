"""Orquestração local: domínio, contexto, deduplicação e pausa humana."""

from collections.abc import Callable
from dataclasses import replace
from hashlib import sha256
import logging

from rocha_reis_agent.ai.context import build_request
from rocha_reis_agent.ai.contracts import AIProvider, AIProviderError, AIProviderTimeout, AIValidationError
from rocha_reis_agent.ai.fake import FakeAIProvider
from rocha_reis_agent.ai.prompts import PromptRegistry
from rocha_reis_agent.ai.validation import (
    compose_reply, parse_response, parse_response_v2, response_detection, sanitize_preface, validate_candidate,
    validate_structure_v2,
)
from rocha_reis_agent.application.conversations import ConversationManager
from rocha_reis_agent.application.handoff import HandoffAction, HumanHandoffManager
from rocha_reis_agent.application.ports import IntentDetector, KnowledgeRepository
from rocha_reis_agent.application.retry import retry_conflicts
from rocha_reis_agent.domain.contracts import (
    AgentResult, ConversationState, Detection, InboundMessage, Intent,
    SimulationError, normalize_message,
)
from rocha_reis_agent.domain.conversations import Conversation, HistoryEntry, utc_now
from rocha_reis_agent.responses import SYNTHETIC_LABEL, ResponseGenerator
from rocha_reis_agent.knowledge_projection import authorize_knowledge

logger = logging.getLogger("rocha_reis_agent")


class AgentService:
    def __init__(
        self, conversations: ConversationManager, knowledge: KnowledgeRepository,
        detector: IntentDetector, responses: ResponseGenerator,
        handoff: HumanHandoffManager,
        provider: AIProvider | None = None, prompt_registry: PromptRegistry | None = None,
        prompt_version: str | None = None, ai_history_limit: int = 4,
        ai_timeout_seconds: float = 2.0,
    ) -> None:
        self.conversations = conversations
        self.knowledge = knowledge
        self.detector = detector
        self.responses = responses
        self.handoff = handoff
        self.provider = provider
        self.prompt = (prompt_registry or PromptRegistry()).get(prompt_version)
        if not 0 <= ai_history_limit <= 10 or not 0 < ai_timeout_seconds <= 30:
            raise ValueError("Limites de IA inválidos.")
        self.ai_history_limit = ai_history_limit
        self.ai_timeout_seconds = ai_timeout_seconds

    @retry_conflicts
    def handle(
        self, message: InboundMessage, *,
        effects: Callable[[AgentResult, str], None] | None = None,
    ) -> AgentResult:
        """Processar mensagem. effects(result, session_id) roda DENTRO da unidade de
        trabalho, antes do commit: adaptadores de canal encenam inbox/outbox ali, e
        conflito/falha descarta tudo junto. Nunca executar I/O externo em effects."""
        normalize_message(message.text, self.conversations.limits.max_message_chars)
        digest = sha256(message.text.encode("utf-8")).hexdigest()
        with self.conversations.edit(message.conversation_id, create=True) as conversation:
            previous = conversation.processed_events.get(message.event_id)
            original = conversation.event_results.get(message.event_id)
            if previous is None:
                # Evento antigo fora da janela do snapshot: consulta durável, sem mutar a conversa.
                known = self.conversations.known_event(message.conversation_id, message.event_id)
                if known is not None:
                    previous, original = known[0], known[1]  # type: ignore[assignment]
            if previous is not None:
                if previous != digest:
                    raise SimulationError("ID de evento reutilizado com conteúdo diferente.")
                duplicate = (
                    replace(original, reply=None, duplicate=True) if original is not None else
                    AgentResult(message.conversation_id, message.event_id, conversation.state, duplicate=True)
                )
                if effects is not None:
                    effects(duplicate, conversation.session_id)
                return duplicate
            if conversation.closed:
                raise SimulationError("Sessão encerrada; use /reset para iniciar outra.")
            if (not getattr(self.conversations.repository, "rolling_event_window", False)
                    and len(conversation.processed_events) >= self.conversations.limits.max_events_per_session):
                raise SimulationError("Limite de eventos da sessão atingido; use /reset.")
            conversation.history.append(HistoryEntry(
                "client", message.text, message.received_at, message.event_id
            ))
            reply: str | None = None
            detection = Detection(())
            fact_keys: tuple[str, ...] = ()
            used_prompt: str | None = None
            if conversation.state == ConversationState.AUTO:
                context_before = (conversation.service_context, conversation.context_age)
                try:
                    knowledge = authorize_knowledge(self.knowledge.get())
                    control = self.detector.detect(message.text, knowledge.base)
                    if Intent.HUMAN in control.intents:
                        detection = self._context(conversation, control)
                        self.handoff.request(conversation)
                        reply = self.responses.render(detection, knowledge.base)
                    else:
                        request = build_request(
                            message, conversation, knowledge, self.prompt,
                            control=control,
                            context_turns=self.conversations.limits.context_turns,
                            history_limit=self.ai_history_limit,
                        )
                        provider = self.provider if self.provider is not None else FakeAIProvider(self.detector)
                        raw = provider.generate(request, timeout_seconds=self.ai_timeout_seconds)
                        v2 = request.output_contract == "ai-response-v2"
                        response = parse_response_v2(raw) if v2 else parse_response(raw)
                        available = {fact.reference.key for fact in request.facts}
                        cited = {ref.key for ref in response.fact_references}
                        if not cited <= available or (
                            response.faq_reference is not None and response.faq_reference.key not in available
                        ):
                            raise AIValidationError("Referência fora do contexto recuperado.")
                        detection = response_detection(response, knowledge)
                        # Entidades/contexto extraídos pelo backend não podem ser trocados pelo modelo.
                        if (
                            detection.service_ids != control.service_ids
                            or detection.use_context != control.use_context
                            or detection.catalog_requested != control.catalog_requested
                            or (Intent.BOOKING in control.intents and Intent.BOOKING not in detection.intents)
                        ):
                            raise AIValidationError("Proposta diverge do escopo autorizado da consulta.")
                        detection = self._context(conversation, detection)
                        reply = self.responses.render(detection, knowledge.base)
                        if v2:
                            # Fatos SEMPRE do backend; só a abertura cordial pode vir do modelo.
                            validate_structure_v2(response, detection, knowledge)  # type: ignore[arg-type]
                            preface, rejected = sanitize_preface(response.preface, knowledge)  # type: ignore[union-attr]
                            if rejected:
                                logger.warning("ai_preface_rejected", extra={"failure_kind": rejected})
                            label = SYNTHETIC_LABEL if knowledge.base.synthetic else "[SIMULAÇÃO LOCAL] "
                            reply = compose_reply(reply, preface, label)
                        else:
                            validate_candidate(response, detection, knowledge, reply)  # type: ignore[arg-type]
                        if authorize_knowledge(self.knowledge.get()).facts != knowledge.facts:
                            raise AIValidationError("Conhecimento mudou durante a consulta.")
                        if response.human_handoff_suggested:
                            self.handoff.request(conversation, reason="validated_ai_suggestion")
                        fact_keys = tuple(ref.key for ref in response.fact_references)
                        used_prompt = request.prompt_version
                except Exception as error:
                    # Não incluir exceção, texto, identificador ou conhecimento no log.
                    failure_kind = (
                        "ai_timeout" if isinstance(error, AIProviderTimeout) else
                        "ai_provider" if isinstance(error, AIProviderError) else
                        "ai_validation" if isinstance(error, AIValidationError) else "processing_error"
                    )
                    logger.error("simulation_processing_failed", extra={"failure_kind": failure_kind})
                    conversation.service_context, conversation.context_age = context_before
                    detection = Detection(())
                    fact_keys, used_prompt = (), None
                    if conversation.state == ConversationState.AUTO:
                        self.handoff.request(conversation, reason="processing_failure")
                    reply = (
                        "[SIMULAÇÃO LOCAL] Não consegui processar a consulta. "
                        "Registrei o pedido de ajuda nesta simulação e pausei o automático. "
                        "Nenhum atendente real foi notificado."
                    )
                conversation.history.append(HistoryEntry(
                    "assistant", reply, utc_now(), message.event_id, fact_keys, used_prompt
                ))
            conversation.processed_events[message.event_id] = digest
            result = AgentResult(
                message.conversation_id, message.event_id, conversation.state,
                reply=reply, intents=detection.intents,
                fact_references=fact_keys, prompt_version=used_prompt,
            )
            conversation.event_results[message.event_id] = result
            if effects is not None:
                effects(result, conversation.session_id)
            return result

    def _context(self, conversation: Conversation, detection: Detection) -> Detection:
        if detection.service_ids:
            conversation.service_context = detection.service_ids
            conversation.context_age = 0
        elif detection.use_context:
            if (
                len(conversation.service_context) == 1
                and conversation.context_age < self.conversations.limits.context_turns
            ):
                detection = replace(detection, service_ids=conversation.service_context)
            conversation.context_age += 1
        else:
            conversation.context_age += 1
            if Intent.PRICE in detection.intents or (
                Intent.SERVICES in detection.intents and not detection.catalog_requested
            ):
                # Serviço explícito desconhecido não pode herdar o preço do anterior.
                conversation.service_context = ()
        return detection

    @retry_conflicts
    def operator_action(
        self, conversation_id: str, action: HandoffAction, *, operator_id: str
    ) -> ConversationState:
        with self.conversations.edit(conversation_id) as conversation:
            self.handoff.act(conversation, action, operator_id)
            return conversation.state

    @retry_conflicts
    def operator_reply(self, conversation_id: str, text: str, *, operator_id: str) -> None:
        self.handoff.authorize(operator_id)
        text = normalize_message(text, self.conversations.limits.max_message_chars)
        with self.conversations.edit(conversation_id) as conversation:
            if conversation.closed or conversation.state != ConversationState.HUMAN_ACTIVE:
                raise SimulationError("O operador só pode responder durante HUMAN_ACTIVE.")
            conversation.history.append(HistoryEntry("operator", text, utc_now()))
