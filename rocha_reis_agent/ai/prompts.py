"""Registro local de prompts; o cliente não escolhe versão nem instruções."""

from pathlib import Path

from pydantic import Field, ValidationError

from rocha_reis_agent.domain.contracts import SimulationError
from rocha_reis_agent.knowledge import Record, Text


class PromptVersion(Record):
    version: str = Field(pattern=r"^v[1-9][0-9]*$")
    objective: Text
    instructions: tuple[Text, ...] = Field(min_length=1, max_length=16)
    changes: tuple[Text, ...] = Field(min_length=1, max_length=20)
    output_contract: str = Field(pattern=r"^ai-response-v[12]$")
    # Fase 8: data de criação registrada no próprio arquivo (opcional nas versões antigas).
    created: Text | None = None


class PromptManifest(Record):
    active: str = Field(pattern=r"^v[1-9][0-9]*$")
    versions: tuple[str, ...] = Field(min_length=1, max_length=20)


class PromptRegistry:
    def __init__(self, directory: Path | None = None) -> None:
        root = directory or Path(__file__).resolve().parents[2] / "prompts"
        try:
            manifest = PromptManifest.model_validate_json((root / "registry.json").read_text())
            self._versions: dict[str, PromptVersion] = {}
            for version in manifest.versions:
                # Validar o ID antes de usá-lo em um caminho de arquivo.
                if not version.startswith("v") or not version[1:].isdigit() or version[1:].startswith("0"):
                    raise ValueError("ID inválido.")
                prompt = PromptVersion.model_validate_json((root / f"{version}.json").read_text(encoding="utf-8"))
                if prompt.version != version or version in self._versions:
                    raise ValueError("Versão conflitante.")
                self._versions[version] = prompt
            self.active = manifest.active
            if self.active not in self._versions:
                raise ValueError("Versão ativa inexistente.")
        except (OSError, UnicodeError, ValidationError, ValueError):
            raise SimulationError("Registro de prompts inválido ou indisponível.") from None

    def get(self, version: str | None = None) -> PromptVersion:
        selected = version if version is not None else self.active
        if selected not in self._versions:
            raise SimulationError("Versão de prompt não registrada.")
        return self._versions[selected]
