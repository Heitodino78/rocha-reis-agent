"""Carregamento offline de catálogo sintético; autoria oficial não é exposta na CLI."""

from pathlib import Path

from pydantic import ValidationError

from rocha_reis_agent.domain.contracts import SimulationError
from rocha_reis_agent.knowledge_catalog import KnowledgeCatalog, KnowledgeScope


def load_synthetic_catalog(path: Path) -> KnowledgeCatalog:
    try:
        if path.stat().st_size > 200_000:
            raise ValueError("Catálogo grande demais.")
        catalog = KnowledgeCatalog.model_validate_json(path.read_text(encoding="utf-8"))
        if catalog.scope != KnowledgeScope.SYNTHETIC:
            raise ValueError("Importação oficial não autorizada nesta CLI.")
        return catalog
    except (OSError, UnicodeError, ValidationError, ValueError):
        raise SimulationError("Use catálogo JSON sintético válido, schema_version=2, até 200 KB.") from None
