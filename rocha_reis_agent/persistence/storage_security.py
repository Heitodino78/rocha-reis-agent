"""Verificação (somente leitura) da proteção em repouso do volume onde fica um arquivo.

Não cifra nada e não implementa criptografia: apenas CONSTATA, pelo que o kernel Linux expõe em
/sys, se o arquivo está num dispositivo device-mapper do tipo dm-crypt (LUKS/plain), direta ou
indiretamente (ex.: LVM sobre LUKS). Sem evidência positiva, o resultado é "não verificado" —
nunca se presume proteção.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path


@dataclass(frozen=True)
class VolumeProtection:
    encrypted: bool
    persistent: bool
    method: str
    chain: tuple[str, ...] = field(default=())

    def describe(self) -> str:
        if not self.persistent:
            return "memória temporária (tmpfs): não persiste após desligar; não serve para o banco"
        if self.encrypted:
            return f"volume cifrado verificado ({self.method}; cadeia: {' → '.join(self.chain)})"
        return f"NÃO cifrado ou não verificável ({self.method})"


def _existing(path: Path) -> Path:
    path = Path(path).absolute()
    while not path.exists():
        path = path.parent
    return path


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="ascii", errors="replace").strip()
    except OSError:
        return ""


def _walk(device: Path, chain: list[str], depth: int = 0) -> str | None:
    """Retorna o tipo de dm-crypt encontrado (ex.: 'LUKS2') ao descer pelos dispositivos."""
    if depth > 8 or not device.exists():
        return None
    uuid = _read(device / "dm" / "uuid")
    name = _read(device / "dm" / "name") or device.name
    chain.append(name)
    if uuid.startswith("CRYPT-"):
        return uuid.split("-")[1] if "-" in uuid else "dm-crypt"
    slaves = device / "slaves"
    if slaves.is_dir():
        for slave in sorted(slaves.iterdir()):
            found = _walk(slave.resolve(), chain, depth + 1)
            if found:
                return found
    return None


def volume_protection(path: Path, *, sys_root: Path = Path("/sys"),
                      device_id: tuple[int, int] | None = None) -> VolumeProtection:
    """`device_id` (major, minor) só para testes; normalmente vem do próprio arquivo."""
    if device_id is None:
        status = os.stat(_existing(path))
        device_id = (os.major(status.st_dev), os.minor(status.st_dev))
    major, minor = device_id
    if major == 0:
        return VolumeProtection(False, False, "sistema de arquivos virtual/tmpfs")
    device = sys_root / "dev" / "block" / f"{major}:{minor}"
    if not device.exists():
        return VolumeProtection(False, True, "dispositivo não identificado em /sys")
    device = device.resolve()
    if (device / "partition").exists() and not (device / "dm").exists():
        # Partição comum: verifica o próprio dispositivo (não há dm-crypt numa partição crua).
        return VolumeProtection(False, True, f"partição sem dm-crypt ({device.name})", (device.name,))
    chain: list[str] = []
    found = _walk(device, chain)
    if found:
        return VolumeProtection(True, True, f"dm-crypt {found}", tuple(chain))
    return VolumeProtection(False, True, "dispositivo sem dm-crypt na cadeia", tuple(chain))
