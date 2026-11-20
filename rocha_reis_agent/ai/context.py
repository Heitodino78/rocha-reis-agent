"""Minimização do contexto: nenhuma identidade ou mensagem de outro cliente."""

from datetime import datetime, timezone
from dataclasses import replace

from rocha_reis_agent.ai.contracts import AIRequest, AuthorizedFact, UntrustedText
from rocha_reis_agent.ai.prompts import PromptVersion
from rocha_reis_agent.domain.contracts import Detection, InboundMessage, Intent
from rocha_reis_agent.domain.conversations import Conversation
from rocha_reis_agent.intents import contains, normalized
from rocha_reis_agent.knowledge_catalog import Approval, FactRevision, KnowledgeScope, ServiceFact
from rocha_reis_agent.knowledge_projection import AuthorizedKnowledge, project_active_facts


def build_request(
    message: InboundMessage, conversation: Conversation, knowledge: AuthorizedKnowledge,
    prompt: PromptVersion, *, control: Detection, context_turns: int, history_limit: int = 4,
    inherited_from: str | None = None,
) -> AIRequest:
    known = {s.id for s in knowledge.base.services}
    candidate = tuple(key for key in conversation.service_context if key in known)
    fresh_unique = len(candidate) == 1 and conversation.context_age < context_turns
    # Fase 9C: além do seguimento reconhecido pelas regras, o backend OFERECE ao modelo o
    # serviço em contexto quando ele é único e recente e a mensagem não nomeia outro serviço
    # ("E fica quanto?"). O modelo só pode dizer que a mensagem se refere a ele; quem fixa o
    # serviço é o backend (AgentService._context), nunca o modelo.
    # Minimização: só quando as regras NÃO reconheceram outro assunto (intenção desconhecida ou
    # preço sem serviço). Perguntas de endereço, horário, agendamento etc. não recebem o contexto.
    offered = (fresh_unique and not control.use_context and not control.service_ids
               and not control.catalog_requested and set(control.intents) <= {Intent.UNKNOWN, Intent.PRICE})
    context = candidate if (control.use_context and conversation.context_age < context_turns) or offered else ()
    effective = replace(control, service_ids=context) if control.use_context and len(context) == 1 else control
    selected = {ref.key for ref in knowledge.references_for(effective)}
    if offered:
        # Somente os fatos aprovados do serviço em contexto (definição e preço), nada além.
        selected.update(ref.key for ref in knowledge.references_for(
            replace(control, intents=(Intent.PRICE,), service_ids=context, use_context=False)))
    # Definições das entidades são necessárias para interpretar nomes/aliases,
    # inclusive em agendamento; preços e outros fatos seguem a intenção consultada.
    selected.update(
        fact.reference.key for fact in knowledge.facts if isinstance(fact.data, ServiceFact)
        and fact.data.service.id in (*control.service_ids, *context)
    )
    aliases = tuple(
        alias for service in knowledge.base.services if service.id in context
        for alias in (service.name, *service.aliases)
    )
    # Apenas entradas anteriores do cliente relacionadas ao serviço em contexto.
    history = tuple(
        UntrustedText(text=entry.text[:500]) for entry in conversation.history[:-1]
        if entry.role == "client" and aliases
        and any(contains(normalized(entry.text), alias) for alias in aliases)
    )
    if inherited_from is not None and history_limit:
        # Pergunta anterior do MESMO cliente que dá sentido à pergunta elíptica (não confiável).
        history = (*history, UntrustedText(text=inherited_from[:500]))
    return AIRequest(
        message=UntrustedText(text=message.text), service_context=context,
        history=history[-history_limit:] if history_limit else (),
        facts=tuple(AuthorizedFact(reference=f.reference, data=f.data) for f in knowledge.facts if f.reference.key in selected),
        scope=KnowledgeScope.SYNTHETIC if knowledge.base.synthetic else KnowledgeScope.OFFICIAL,
        prompt_version=prompt.version, objective=prompt.objective,
        system_instructions=prompt.instructions, output_contract=prompt.output_contract,
    )


def request_knowledge(request: AIRequest) -> AuthorizedKnowledge:
    """Reconstrução interna do fake a partir do snapshot já autorizado pelo backend."""
    facts = tuple(FactRevision(
        id=f.reference.id, version=f.reference.version,
        replaces=f.reference.version-1 if f.reference.version>1 else None,
        scope=request.scope, status=Approval.APPROVED, source="Snapshot autorizado do backend",
        reviewed_by="snapshot interno, sem nova aprovação", updated_at=datetime(2026,11,19,tzinfo=timezone.utc),
        data=f.data,
    ) for f in request.facts)
    # Snapshot pode conter apenas a versão ativa: não reconstruir um histórico fictício.
    return project_active_facts(request.scope, facts)
