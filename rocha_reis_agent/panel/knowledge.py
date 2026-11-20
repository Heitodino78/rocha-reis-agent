"""Gestão da base de conhecimento pelo painel (Fase 8). Somente ADMIN.

Fluxo: rascunho (DRAFT) → aprovação (APPROVED, passa a ser usado no atendimento)
→ revogação (ARCHIVED, deixa de ser usado). Rascunho pode ser rejeitado
(REJECTED). Cada passo é uma NOVA revisão imutável; o histórico nunca é apagado.

Conservador (ADR-018): um rascunho novo sobre um dado aprovado SUSPENDE o uso do
dado até a nova aprovação — preço em revisão nunca é informado pelo valor antigo.
A IA nunca chama este módulo; ele é acionado apenas por pessoas autenticadas.
"""

from decimal import Decimal, InvalidOperation
import re
from typing import Any

from pydantic import ValidationError

from rocha_reis_agent.domain.contracts import Detection, StorageError
from rocha_reis_agent.intents import RuleIntentDetector
from rocha_reis_agent.knowledge import FAQ, KnowledgeBase, Service
from rocha_reis_agent.knowledge_catalog import (
    Approval, BooksyFact, FAQFact, KnowledgeScope, ListFact, PriceFact, ServiceFact, TextFact,
)
from rocha_reis_agent.knowledge_projection import authorize_knowledge
from rocha_reis_agent.panel.security import PanelError
from rocha_reis_agent.panel.store import PanelUser, SQLitePanelStore
from rocha_reis_agent.persistence.sqlite_knowledge import KnowledgeConflict, SQLiteKnowledgeRepository
from rocha_reis_agent.responses import ResponseGenerator

SINGLETONS = ("identity", "location", "hours", "professionals", "policy", "cancellation", "booksy")
KINDS = (*SINGLETONS, "service", "price", "faq")
KEY = re.compile(r"[a-z][a-z0-9_-]{0,63}")
LABELS = {
    "identity": "Identidade do salão", "location": "Endereço", "hours": "Horário de funcionamento",
    "service": "Serviço", "price": "Preço", "professionals": "Profissionais", "policy": "Políticas de atendimento",
    "cancellation": "Política de cancelamento", "faq": "Pergunta frequente", "booksy": "Link Booksy",
}


def _text(fields: dict[str, Any], name: str, *, required: bool = True) -> str | None:
    value = fields.get(name)
    value = value.strip() if isinstance(value, str) else ""
    if required and not value:
        raise PanelError(422, f"Preencha o campo '{name}'.")
    return value or None


def _lines(fields: dict[str, Any], name: str) -> tuple[str, ...]:
    value = fields.get(name)
    items = value if isinstance(value, list) else (value.splitlines() if isinstance(value, str) else [])
    result = tuple(item.strip() for item in items if isinstance(item, str) and item.strip())
    if not result:
        raise PanelError(422, f"Informe ao menos um item em '{name}' (um por linha).")
    return result


class KnowledgeService:
    def __init__(self, repository: SQLiteKnowledgeRepository, audit: SQLitePanelStore, *,
                 require_distinct_approver: bool = False) -> None:
        self.repository = repository
        self.audit = audit
        self.require_distinct_approver = require_distinct_approver

    # ----------------------------------------------------------------- leitura
    def overview(self, user: PanelUser) -> dict[str, Any]:
        self._admin(user)
        scope = self.repository.scope()
        if scope is None:
            raise PanelError(409, "Base de conhecimento não inicializada (admin_cli knowledge-init).")
        return {"scope": scope.value, "records": self.repository.records(), "pending": self.pending(),
                "labels": LABELS, "kinds": list(KINDS)}

    def pending(self) -> list[dict[str, str]]:
        """Categorias sem dado aprovado em uso — o que ainda precisa ser coletado/aprovado."""
        active = authorize_knowledge(self.repository.get())
        kinds = {fact.data.kind for fact in active.facts}
        missing = [{"kind": kind, "label": LABELS[kind], "detail": "Nenhum registro aprovado em uso."}
                   for kind in ("identity", "location", "hours", "service", "professionals", "policy",
                                "cancellation", "booksy", "faq") if kind not in kinds]
        missing += [{"kind": "price", "label": LABELS["price"], "detail": f"Sem preço aprovado: {service.name}."}
                    for service in active.base.services if service.price is None]
        return missing

    def history(self, user: PanelUser, fact_id: str) -> list[dict[str, Any]]:
        self._admin(user)
        rows = self.repository.history(fact_id)
        if not rows:
            raise PanelError(404, "Registro não encontrado.")
        return rows

    # ---------------------------------------------------------------- escrita
    def draft(self, user: PanelUser, *, kind: object, key: object, fields: object, source: object,
              expected_version: object) -> dict[str, Any]:
        self._admin(user)
        if kind not in KINDS or not isinstance(fields, dict):
            raise PanelError(422, "Tipo de informação inválido.")
        scope = self.repository.scope()
        if scope is None:
            raise PanelError(409, "Base de conhecimento não inicializada.")
        fact_id, data = self._build(str(kind), key, fields, scope)
        origin = _text({"source": source}, "source")
        if len(origin) > 240:  # type: ignore[arg-type]
            raise PanelError(422, "A origem deve ter até 240 caracteres.")
        revision = self._append(user, "knowledge_drafted", fact_id=fact_id, expected=expected_version,
                                status=Approval.DRAFT, data=data, source=origin, reviewed_by=None)
        return {"fact_id": revision.id, "version": revision.version, "status": revision.status.value}

    def approve(self, user: PanelUser, fact_id: str, version: object) -> dict[str, Any]:
        latest = self._latest(user, fact_id, version, Approval.DRAFT, "Só rascunhos podem ser aprovados.")
        if self.require_distinct_approver and self.repository.author_of(fact_id, latest.version) == user.id:
            self.audit.audit(user.id, "knowledge_approved", "knowledge", fact_id, "denied")
            raise PanelError(403, "A aprovação exige um administrador diferente de quem escreveu o rascunho.")
        revision = self._append(user, "knowledge_approved", fact_id=fact_id, expected=latest.version,
                                status=Approval.APPROVED, data=latest.data, source=latest.source,
                                reviewed_by=f"{user.display_name} (painel)")
        return {"fact_id": revision.id, "version": revision.version, "status": revision.status.value}

    def reject(self, user: PanelUser, fact_id: str, version: object) -> dict[str, Any]:
        latest = self._latest(user, fact_id, version, Approval.DRAFT, "Só rascunhos podem ser rejeitados.")
        revision = self._append(user, "knowledge_rejected", fact_id=fact_id, expected=latest.version,
                                status=Approval.REJECTED, data=latest.data, source=latest.source, reviewed_by=None)
        return {"fact_id": revision.id, "version": revision.version, "status": revision.status.value}

    def revoke(self, user: PanelUser, fact_id: str, version: object) -> dict[str, Any]:
        latest = self._latest(user, fact_id, version, Approval.APPROVED, "Só registros aprovados podem ser revogados.")
        revision = self._append(user, "knowledge_revoked", fact_id=fact_id, expected=latest.version,
                                status=Approval.ARCHIVED, data=latest.data, source=latest.source, reviewed_by=None)
        return {"fact_id": revision.id, "version": revision.version, "status": revision.status.value}

    def preview(self, user: PanelUser, question: object) -> dict[str, Any]:
        """Prévia do trecho factual que o atendimento usaria hoje (sem conversa, sem IA, sem envio)."""
        self._admin(user)
        if not isinstance(question, str) or not question.strip() or len(question) > 500:
            raise PanelError(422, "Escreva uma pergunta de até 500 caracteres.")
        knowledge = authorize_knowledge(self.repository.get())
        detection: Detection = RuleIntentDetector().detect(question, knowledge.base)
        return {"intents": [i.value for i in detection.intents],
                "reply": ResponseGenerator().render(detection, knowledge.base),
                "fact_references": [ref.key for ref in knowledge.references_for(detection)]}

    # ---------------------------------------------------------------- internos
    def _latest(self, user: PanelUser, fact_id: str, version: object, status: Approval, message: str):  # type: ignore[no-untyped-def]
        self._admin(user)
        latest = self.repository.latest(fact_id)
        if latest is None:
            raise PanelError(404, "Registro não encontrado.")
        if type(version) is not int or latest.version != version:
            raise PanelError(409, "O registro mudou desde a leitura; atualize e tente novamente.")
        if latest.status != status:
            raise PanelError(409, message)
        return latest

    def _append(self, user: PanelUser, action: str, *, fact_id: str, expected: object, status: Approval,
                data: Any, source: str | None, reviewed_by: str | None):  # type: ignore[no-untyped-def]
        if expected is not None and type(expected) is not int:
            raise PanelError(422, "Versão esperada inválida.")
        try:
            revision = self.repository.append(fact_id=fact_id, expected_version=expected, status=status, data=data,  # type: ignore[arg-type]
                                              source=source or "", reviewed_by=reviewed_by, author=user.id)
        except KnowledgeConflict as error:
            self.audit.audit(user.id, action, "knowledge", fact_id, "failed")
            raise PanelError(409, str(error)) from None
        except StorageError:
            self.audit.audit(user.id, action, "knowledge", fact_id, "failed")
            raise PanelError(409, "Não foi possível gravar; tente novamente.") from None
        self.audit.audit(user.id, action, "knowledge", fact_id, "success")
        return revision

    def _build(self, kind: str, key: object, fields: dict[str, Any], scope: KnowledgeScope):  # type: ignore[no-untyped-def]
        try:
            if kind in ("identity", "location", "hours"):
                return kind, TextFact(kind=kind, text=_text(fields, "text"))  # type: ignore[arg-type]
            if kind in ("professionals", "policy", "cancellation"):
                return kind, ListFact(kind=kind, items=_lines(fields, "items"))  # type: ignore[arg-type]
            if kind == "booksy":
                url = _text(fields, "url")
                probe = KnowledgeBase(synthetic=scope == KnowledgeScope.SYNTHETIC, booksy_url=url)
                if probe.booking_link() is None:
                    raise PanelError(422, "Link Booksy inválido: use https e o domínio booksy.com (sem fragmento).")
                return "booksy", BooksyFact(url=url)  # type: ignore[arg-type]
            slug = key.strip().lower() if isinstance(key, str) else ""
            if KEY.fullmatch(slug) is None:
                raise PanelError(422, "Identificador inválido: letras minúsculas, números, hífen ou sublinhado.")
            if kind == "service":
                aliases = tuple(a.strip() for a in (fields.get("aliases") or "").split(",") if a.strip())
                service = Service(id=slug, name=_text(fields, "name"), aliases=aliases,  # type: ignore[arg-type]
                                  description=_text(fields, "description", required=False))
                return f"service:{slug}", ServiceFact(service=service)
            if kind == "price":
                if self.repository.latest(f"service:{slug}") is None:
                    raise PanelError(422, "Cadastre o serviço antes do preço.")
                raw = _text(fields, "amount").replace("R$", "").replace(" ", "").replace(",", ".")  # type: ignore[union-attr]
                try:
                    amount = Decimal(raw)
                except InvalidOperation:
                    raise PanelError(422, "Valor inválido.") from None
                return f"price:{slug}", PriceFact(service_id=slug, amount=amount,
                                                  note=_text(fields, "note", required=False))
            return f"faq:{slug}", FAQFact(faq=FAQ(questions=_lines(fields, "questions"), answer=_text(fields, "answer")))  # type: ignore[arg-type]
        except ValidationError:
            raise PanelError(422, "Dados inválidos para este tipo de informação (tamanho ou formato).") from None

    @staticmethod
    def _admin(user: PanelUser) -> None:
        if user.role != "ADMIN":
            raise PanelError(403, "Gestão do conhecimento reservada a administradores.")
