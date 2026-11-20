"""Base de conhecimento durável: revisões imutáveis no mesmo SQLite.

Toda inclusão reconstrói o KnowledgeCatalog completo e passa pelos MESMOS
validadores da Fase 2 (versões sem lacuna, uma revisão ativa por dado, FAQs sem
pergunta repetida, escopo único). Uma revisão que tornaria o catálogo
inconsistente é recusada antes do commit.

O escopo do banco (SYNTHETIC ou OFFICIAL) é fixado uma vez e nunca muda:
fixtures sintéticas jamais entram num banco oficial, e vice-versa.
"""

from datetime import datetime
import json
import sqlite3
from typing import Any

from pydantic import ValidationError

from rocha_reis_agent.domain.contracts import StorageError
from rocha_reis_agent.domain.conversations import utc_now
from rocha_reis_agent.knowledge_catalog import Approval, FactData, FactRevision, KnowledgeCatalog, KnowledgeScope
from rocha_reis_agent.persistence.sqlite import SQLiteConversationRepository


class KnowledgeConflict(StorageError):
    """Revisão recusada por inconsistência (versão concorrente, conflito ou escopo)."""


class SQLiteKnowledgeRepository:
    """Implementa KnowledgeRepository.get() a partir das revisões persistidas."""

    def __init__(self, repository: SQLiteConversationRepository) -> None:
        self.repository = repository
        self._cache: tuple[tuple[int, int], KnowledgeCatalog] | None = None

    # ------------------------------------------------------------------ escopo
    def scope(self) -> KnowledgeScope | None:
        with self.repository.read() as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key='knowledge_scope'").fetchone()
        return KnowledgeScope(row[0]) if row else None

    def initialize(self, scope: KnowledgeScope) -> None:
        """Fixa o escopo do banco na primeira vez; repetir com o mesmo escopo é inofensivo."""
        with self.repository.write_transaction() as connection:
            current = connection.execute("SELECT value FROM metadata WHERE key='knowledge_scope'").fetchone()
            if current is not None and current[0] != scope.value:
                raise KnowledgeConflict("O escopo da base de conhecimento já foi definido e não pode mudar.")
            connection.execute("INSERT OR IGNORE INTO metadata VALUES('knowledge_scope',?)", (scope.value,))

    # ------------------------------------------------------------------ leitura
    def get(self) -> KnowledgeCatalog:
        scope = self.scope()
        if scope is None:
            raise StorageError("Base de conhecimento não inicializada.")
        with self.repository.read() as connection:
            marker = tuple(connection.execute("SELECT count(*), coalesce(max(id),0) FROM knowledge_revisions").fetchone())
            if self._cache is not None and self._cache[0] == marker:
                return self._cache[1]
            documents = [json.loads(row[0]) for row in connection.execute(
                "SELECT document FROM knowledge_revisions ORDER BY id")]
        catalog = KnowledgeCatalog.model_validate({"scope": scope, "revisions": documents})
        self._cache = (marker, catalog)  # type: ignore[assignment]
        return catalog

    def records(self) -> list[dict[str, Any]]:
        """Última revisão de cada dado, com indicação de uso no atendimento."""
        catalog = self.get()
        active = {r.id for r in catalog.active()}
        latest: dict[str, FactRevision] = {}
        for revision in catalog.revisions:
            if revision.id not in latest or revision.version > latest[revision.id].version:
                latest[revision.id] = revision
        with self.repository.read() as connection:
            authors = {(r["fact_id"], r["version"]): (r["author"], r["created_at"]) for r in connection.execute(
                "SELECT fact_id,version,author,created_at FROM knowledge_revisions")}
        return [{
            "fact_id": r.id, "kind": r.data.kind, "version": r.version, "status": r.status.value,
            "in_use": r.id in active, "source": r.source, "reviewed_by": r.reviewed_by,
            "updated_at": r.updated_at.isoformat(), "author": authors.get((r.id, r.version), ("", ""))[0],
            "data": r.data.model_dump(mode="json"),
        } for r in sorted(latest.values(), key=lambda item: (item.data.kind, item.id))]

    def history(self, fact_id: str) -> list[dict[str, Any]]:
        with self.repository.read() as connection:
            rows = connection.execute("SELECT document,author,created_at FROM knowledge_revisions WHERE fact_id=? "
                                      "ORDER BY version", (fact_id,)).fetchall()
        return [{**json.loads(row[0]), "author": row[1], "recorded_at": row[2]} for row in rows]

    def revision(self, fact_id: str, version: int) -> FactRevision | None:
        with self.repository.read() as connection:
            row = connection.execute("SELECT document FROM knowledge_revisions WHERE fact_id=? AND version=?",
                                     (fact_id, version)).fetchone()
        return FactRevision.model_validate_json(row[0]) if row else None

    def latest(self, fact_id: str) -> FactRevision | None:
        with self.repository.read() as connection:
            row = connection.execute("SELECT document FROM knowledge_revisions WHERE fact_id=? ORDER BY version DESC "
                                     "LIMIT 1", (fact_id,)).fetchone()
        return FactRevision.model_validate_json(row[0]) if row else None

    def author_of(self, fact_id: str, version: int) -> str | None:
        with self.repository.read() as connection:
            row = connection.execute("SELECT author FROM knowledge_revisions WHERE fact_id=? AND version=?",
                                     (fact_id, version)).fetchone()
        return row[0] if row else None

    # ------------------------------------------------------------------ escrita
    def append(self, *, fact_id: str, expected_version: int | None, status: Approval, data: FactData | dict[str, Any],
               source: str, reviewed_by: str | None, author: str, now: datetime | None = None) -> FactRevision:
        """Acrescenta a revisão seguinte. expected_version protege contra edição concorrente."""
        scope = self.scope()
        if scope is None:
            raise StorageError("Base de conhecimento não inicializada.")
        with self.repository.write_transaction() as connection:
            current = connection.execute("SELECT coalesce(max(version),0) FROM knowledge_revisions WHERE fact_id=?",
                                         (fact_id,)).fetchone()[0]
            if (expected_version or 0) != current:
                raise KnowledgeConflict("O registro mudou desde a leitura; atualize e tente novamente.")
            documents = [json.loads(row[0]) for row in connection.execute(
                "SELECT document FROM knowledge_revisions ORDER BY id")]
            try:
                revision = FactRevision.model_validate({
                    "id": fact_id, "version": current + 1, "replaces": current or None, "scope": scope,
                    "status": status, "source": source, "updated_at": now or utc_now(),
                    "reviewed_by": reviewed_by, "data": data,
                })
                KnowledgeCatalog.model_validate({"scope": scope, "revisions": [*documents, revision.model_dump(mode="json")]})
            except ValidationError as error:
                raise KnowledgeConflict(_explain(error)) from None
            try:
                connection.execute(
                    "INSERT INTO knowledge_revisions(fact_id,version,status,scope,document,author,created_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (fact_id, revision.version, revision.status.value, scope.value, revision.model_dump_json(),
                     author[:80], utc_now().isoformat()))
            except sqlite3.IntegrityError:
                raise KnowledgeConflict("Versão concorrente gravada; atualize e tente novamente.") from None
        self._cache = None
        return revision

    def import_catalog(self, catalog: KnowledgeCatalog, *, author: str) -> int:
        """Importação inicial (ex.: fixture SINTÉTICA) somente em base vazia do mesmo escopo."""
        scope = self.scope()
        if scope is None or catalog.scope != scope:
            raise KnowledgeConflict("Catálogo de escopo diferente do banco; importação recusada.")
        with self.repository.write_transaction() as connection:
            if connection.execute("SELECT count(*) FROM knowledge_revisions").fetchone()[0]:
                raise KnowledgeConflict("Importação só é permitida numa base de conhecimento vazia.")
            for revision in sorted(catalog.revisions, key=lambda r: (r.id, r.version)):
                connection.execute(
                    "INSERT INTO knowledge_revisions(fact_id,version,status,scope,document,author,created_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (revision.id, revision.version, revision.status.value, scope.value, revision.model_dump_json(),
                     author[:80], utc_now().isoformat()))
        self._cache = None
        return len(catalog.revisions)


def _explain(error: ValidationError) -> str:
    """Mensagem segura (sem valores) para a interface."""
    messages = {str(item.get("msg", "")) for item in error.errors()}
    joined = " ".join(m.removeprefix("Value error, ") for m in messages if m)[:300]
    return joined or "Revisão inválida."
