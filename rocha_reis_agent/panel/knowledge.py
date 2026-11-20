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

from rocha_reis_agent.domain.contracts import Detection, StorageError, normalize_for_matching
from rocha_reis_agent.intents import RuleIntentDetector
from rocha_reis_agent.knowledge import FAQ, Category, Duration, KnowledgeBase, Professional, Service
from rocha_reis_agent.knowledge_catalog import (
    Approval, BooksyFact, CategoryFact, FAQFact, KnowledgeScope, ListFact, PriceFact, ProfessionalFact, ServiceFact,
    TextFact,
)
from rocha_reis_agent.knowledge_projection import authorize_knowledge
from rocha_reis_agent.panel.security import PanelError
from rocha_reis_agent.panel.store import PanelUser, SQLitePanelStore
from rocha_reis_agent.persistence.sqlite_knowledge import KnowledgeConflict, SQLiteKnowledgeRepository
from rocha_reis_agent.responses import ResponseGenerator

SINGLETONS = ("identity", "location", "hours", "professionals", "policy", "cancellation", "booksy")
KINDS = (*SINGLETONS, "category", "professional", "service", "price", "faq")
PRICINGS = {"fixed": "Preço fixo", "starting": "A partir de", "range": "Faixa de preço",
            "on_evaluation": "Mediante avaliação presencial"}
KEY = re.compile(r"[a-z][a-z0-9_-]{0,63}")
LABELS = {
    "identity": "Identidade do salão", "location": "Endereço", "hours": "Horário de funcionamento",
    "service": "Serviço", "price": "Preço", "professionals": "Profissionais", "policy": "Políticas de atendimento",
    "cancellation": "Política de cancelamento", "faq": "Pergunta frequente", "booksy": "Link Booksy",
    "category": "Categoria de serviço", "professional": "Profissional (quem realiza serviços)",
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


def _minutes(fields: dict[str, Any], name: str) -> int | None:
    raw = fields.get(name)
    raw = raw.strip() if isinstance(raw, str) else ""
    if not raw:
        return None
    if not raw.isdigit():
        raise PanelError(422, "Duração em minutos inteiros (ex.: 45).")
    return int(raw)


def _flag(fields: dict[str, Any], name: str) -> bool:
    value = fields.get(name, "true")
    if value not in ("true", "false", True, False):
        raise PanelError(422, f"Valor inválido em '{name}'.")
    return value in ("true", True)


def _terms(data: Any) -> tuple[str, set[str]] | None:
    """Termos que o atendimento usa para reconhecer o registro (nome/apelidos ou perguntas)."""
    if isinstance(data, CategoryFact):
        return "category", {normalize_for_matching(data.category.name)}
    if isinstance(data, ProfessionalFact):
        return "professional", {normalize_for_matching(data.professional.name)}
    if isinstance(data, ServiceFact):
        return "service", {normalize_for_matching(t) for t in (data.service.name, *data.service.aliases)}
    if isinstance(data, FAQFact):
        return "faq", {normalize_for_matching(q) for q in data.faq.questions}
    return None


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
            raise PanelError(409, "A base de informações do salão ainda não foi preparada. Peça ao suporte técnico "
                                 "para inicializá-la (comando knowledge-init).")
        return {"scope": scope.value, "records": self.repository.records(), "pending": self.pending(),
                "labels": LABELS, "kinds": list(KINDS), "pricings": PRICINGS}

    def pending(self) -> list[dict[str, str]]:
        """Categorias sem dado aprovado em uso — o que ainda precisa ser coletado/aprovado."""
        active = authorize_knowledge(self.repository.get())
        kinds = {fact.data.kind for fact in active.facts}
        missing = [{"kind": kind, "label": LABELS[kind], "detail": "Nenhum registro aprovado em uso."}
                   for kind in ("identity", "location", "hours", "service", "professionals", "policy",
                                "cancellation", "booksy", "faq") if kind not in kinds]
        missing += [{"kind": "price", "label": LABELS["price"], "detail": f"Sem preço aprovado: {service.name}."}
                    for service in active.base.services if service.price is None and service.price_kind is None]
        categories = {c.id: c for c in active.base.categories}
        team = {p.id: p for p in active.base.team}
        for fact in active.facts:
            if not isinstance(fact.data, ServiceFact):
                continue
            service = fact.data.service
            if service.category_id and not (service.category_id in categories and categories[service.category_id].active):
                missing.append({"kind": "category", "label": LABELS["category"],
                                "detail": f"{service.name}: categoria '{service.category_id}' inativa ou não aprovada (não é exibida)."})
            for member in service.professional_ids:
                if not (member in team and team[member].active):
                    missing.append({"kind": "professional", "label": LABELS["professional"],
                                    "detail": f"{service.name}: profissional '{member}' inativo ou não aprovado (não é exibido)."})
        seen: dict[tuple[str, str], str] = {}
        for fact in active.facts:
            found = _terms(fact.data)
            for term in (found[1] if found else ()):
                other = seen.setdefault((found[0], term), fact.id)  # type: ignore[index]
                if other != fact.id:
                    missing.append({"kind": found[0], "label": LABELS[found[0]],  # type: ignore[index]
                                    "detail": f"Conflito: '{term}' aparece em {other} e {fact.id}."})
        return missing

    def _unresolved(self, data: Any) -> str | None:
        """Serviço só é aprovado com categoria e profissionais APROVADOS e ATIVOS."""
        if not isinstance(data, ServiceFact):
            return None
        base = authorize_knowledge(self.repository.get()).base
        service = data.service
        if service.category_id and not any(c.id == service.category_id and c.active for c in base.categories):
            return f"a categoria '{service.category_id}' não está aprovada e ativa"
        active = {p.id for p in base.team if p.active}
        missing = [p for p in service.professional_ids if p not in active]
        if missing:
            return f"profissional não aprovado ou inativo: {', '.join(missing)}"
        return None

    def _conflict(self, fact_id: str, data: Any) -> str | None:
        """Nome/apelido de serviço ou pergunta frequente já usados por OUTRO registro aprovado."""
        mine = _terms(data)
        if mine is None:
            return None
        for fact in authorize_knowledge(self.repository.get()).facts:
            other = _terms(fact.data)
            if fact.id != fact_id and other and other[0] == mine[0] and mine[1] & other[1]:
                return f"'{sorted(mine[1] & other[1])[0]}' já é usado por {fact.id}"
        return None

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
        conflict = self._conflict(fact_id, latest.data) or self._unresolved(latest.data)
        if conflict:
            # Dois registros respondendo ao mesmo termo deixariam a resposta ambígua.
            self.audit.audit(user.id, "knowledge_approved", "knowledge", fact_id, "denied")
            prefix = "Pendência" if conflict.startswith(("a categoria", "profissional")) else "Conflito"
            raise PanelError(409, f"{prefix}: {conflict}. Ajuste o rascunho ou resolva o outro registro antes.")
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
        return {"intents": [i.value for i in detection.intents], "simulated": True,
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
            if kind == "category":
                return f"category:{slug}", CategoryFact(category=Category(
                    id=slug, name=_text(fields, "name"), active=_flag(fields, "active")))  # type: ignore[arg-type]
            if kind == "professional":
                return f"professional:{slug}", ProfessionalFact(professional=Professional(
                    id=slug, name=_text(fields, "name"), active=_flag(fields, "active")))  # type: ignore[arg-type]
            if kind == "service":
                aliases = tuple(a.strip() for a in (fields.get("aliases") or "").split(",") if a.strip())
                category = (fields.get("category") or "").strip() or None
                if category and self.repository.latest(f"category:{category}") is None:
                    raise PanelError(422, "Categoria inexistente: cadastre a categoria antes.")
                team = tuple(dict.fromkeys(p.strip() for p in (fields.get("professionals") or "").split(",") if p.strip()))
                unknown = [p for p in team if self.repository.latest(f"professional:{p}") is None]
                if unknown:
                    raise PanelError(422, f"Profissional inexistente: {', '.join(unknown)}.")
                low, high = _minutes(fields, "duration_min"), _minutes(fields, "duration_max")
                if high is not None and low is None:
                    raise PanelError(422, "Informe a duração mínima (ou só ela, para duração aproximada).")
                try:
                    duration = Duration(min_minutes=low, max_minutes=high) if low is not None else None
                except ValidationError:
                    raise PanelError(422, "Duração inválida: use de 5 a 720 minutos; o máximo deve ser maior "
                                          "que o mínimo.") from None
                service = Service(id=slug, name=_text(fields, "name"), aliases=aliases,  # type: ignore[arg-type]
                                  description=_text(fields, "description", required=False),
                                  category_id=category, duration=duration, professional_ids=team)
                return f"service:{slug}", ServiceFact(service=service)
            if kind == "price":
                if self.repository.latest(f"service:{slug}") is None:
                    raise PanelError(422, "Cadastre o serviço antes do preço.")
                pricing = fields.get("pricing") or "fixed"
                if pricing not in PRICINGS:
                    raise PanelError(422, "Tipo de preço inválido.")

                def money(name: str, required: bool) -> Decimal | None:
                    raw = _text(fields, name, required=required)
                    if raw is None:
                        return None
                    try:
                        return Decimal(raw.replace("R$", "").replace(" ", "").replace(",", "."))
                    except InvalidOperation:
                        raise PanelError(422, "Valor inválido.") from None

                evaluation = pricing == "on_evaluation"
                try:
                    fact = PriceFact(service_id=slug, pricing=pricing,  # type: ignore[arg-type]
                                     amount=None if evaluation else money("amount", True),
                                     amount_max=money("amount_max", True) if pricing == "range" else None,
                                     note=_text(fields, "note", required=False))
                except ValidationError:
                    raise PanelError(422, "Preço inconsistente: na faixa o máximo deve ser maior que o mínimo; "
                                          "valores de até 999.999,99.") from None
                return f"price:{slug}", fact
            return f"faq:{slug}", FAQFact(faq=FAQ(questions=_lines(fields, "questions"), answer=_text(fields, "answer")))  # type: ignore[arg-type]
        except ValidationError:
            raise PanelError(422, "Dados inválidos para este tipo de informação (tamanho ou formato).") from None

    @staticmethod
    def _admin(user: PanelUser) -> None:
        if user.role != "ADMIN":
            raise PanelError(403, "Gestão do conhecimento reservada a administradores.")
