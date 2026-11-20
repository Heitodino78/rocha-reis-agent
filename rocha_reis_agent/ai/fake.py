"""Simulador determinístico do contrato de IA, sem inferência generativa."""

from dataclasses import replace
from typing import Literal

from rocha_reis_agent.ai.context import request_knowledge
from rocha_reis_agent.ai.contracts import AIProviderError, AIProviderTimeout, AIRequest, AIResponse
from rocha_reis_agent.application.ports import IntentDetector
from rocha_reis_agent.domain.contracts import Intent
from rocha_reis_agent.intents import RuleIntentDetector
from rocha_reis_agent.knowledge_catalog import FAQFact
from rocha_reis_agent.responses import ResponseGenerator


class FakeAIProvider:
    def __init__(
        self, detector: IntentDetector | None = None,
        mode: Literal["normal", "failure", "timeout"] = "normal",
    ) -> None:
        self.detector = detector or RuleIntentDetector()
        self.mode = mode

    def generate(self, request: AIRequest, *, timeout_seconds: float) -> AIResponse:
        if self.mode == "timeout" or timeout_seconds <= 0:
            raise AIProviderTimeout("Timeout simulado.")
        if self.mode == "failure":
            raise AIProviderError("Falha simulada.")
        knowledge = request_knowledge(request)
        detection = self.detector.detect(request.message.text, knowledge.base)
        effective = detection
        if detection.use_context and len(request.service_context) == 1:
            effective = replace(detection, service_ids=request.service_context)
        faq_ref = next((
            fact.reference for fact in knowledge.facts if isinstance(fact.data, FAQFact)
            and detection.faq_index is not None
            and fact.data.faq == knowledge.base.faqs[detection.faq_index]
        ), None)
        return AIResponse(
            intents=detection.intents, service_ids=detection.service_ids,
            use_context=detection.use_context, catalog_requested=detection.catalog_requested,
            faq_reference=faq_ref, fact_references=knowledge.references_for(effective),
            candidate_reply=ResponseGenerator().render(effective, knowledge.base),
            needs_clarification=Intent.UNKNOWN in detection.intents or (detection.use_context and not effective.service_ids),
            human_handoff_suggested=Intent.HUMAN in detection.intents,
        )
