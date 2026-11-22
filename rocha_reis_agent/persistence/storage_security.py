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


# ------------------------------------------------------------------ Fase 18B-0: IDENTIDADE do volume de dados
# Cifragem verificada não basta: com o disco de sistema também cifrado, um diretório vazio no ponto de
# montagem (volume ainda bloqueado) passaria na checagem acima e o banco seria criado no disco errado.
# Aqui se exige, juntas: (1) o caminho É um ponto de montagem; (2) o dispositivo montado é LUKS; (3) o
# UUID LUKS é o esperado (gravado no perfil, FORA do volume); (4) a marca de identificação gravada no
# próprio volume confere com o perfil; (5) as pastas de dados estão nesse mesmo sistema de arquivos.
import json as _json
import re as _re

VOLUME_MARKER = ".rocha-reis-volume.json"
READY, WAITING, REFUSED = "PRONTO", "AGUARDANDO_DESBLOQUEIO", "RECUSADO"
_ID = _re.compile(r"^[0-9a-f]{32}$")


def normalize_luks_uuid(value: str) -> str:
    text = str(value).strip().lower().replace("-", "")
    if not _ID.match(text):
        raise ValueError("UUID LUKS inválido.")
    return text


def _unescape(field_value: str) -> str:
    return _re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), field_value)


def mount_entry(mount_point: Path, *, mountinfo: Path = Path("/proc/self/mountinfo")) -> dict[str, object] | None:
    """Entrada de /proc/self/mountinfo cujo ponto de montagem é EXATAMENTE o caminho (a mais recente)."""
    wanted = os.path.realpath(mount_point)
    found = None
    for line in Path(mountinfo).read_text(encoding="utf-8", errors="replace").splitlines():
        left, _, right = line.partition(" - ")
        fields, tail = left.split(), right.split()
        if len(fields) < 6 or len(tail) < 2:
            continue
        if _unescape(fields[4]) == wanted:
            major, minor = fields[2].split(":")
            found = {"major": int(major), "minor": int(minor), "opcoes": fields[5].split(","),
                     "tipo": tail[0], "origem": _unescape(tail[1])}
    return found


def _crypt_uuid(device: Path, depth: int = 0) -> str | None:
    """`dm/uuid` da camada dm-crypt (ex.: CRYPT-LUKS2-<uuid>-<nome>), descendo por LVM."""
    if depth > 8 or not device.exists():
        return None
    uuid = _read(device / "dm" / "uuid")
    if uuid.startswith("CRYPT-"):
        return uuid
    slaves = device / "slaves"
    if slaves.is_dir():
        for slave in sorted(slaves.iterdir()):
            found = _crypt_uuid(slave.resolve(), depth + 1)
            if found:
                return found
    return None


def luks_uuid_of(major: int, minor: int, *, sys_root: Path = Path("/sys")) -> tuple[str | None, str | None]:
    """(tipo dm-crypt, UUID LUKS normalizado) do dispositivo; (None, None) sem dm-crypt."""
    device = sys_root / "dev" / "block" / f"{major}:{minor}"
    if not device.exists():
        return None, None
    raw = _crypt_uuid(device.resolve())
    if not raw:
        return None, None
    parts = raw.split("-")
    kind = parts[1] if len(parts) > 1 else "dm-crypt"
    if kind.startswith("LUKS") and len(parts) > 2 and _ID.match(parts[2].lower()):
        return kind, parts[2].lower()
    return kind, None  # dm-crypt "plain" não tem UUID: não identifica o volume


def read_volume_marker(mount_point: Path) -> dict[str, object]:
    """Marca de identificação na RAIZ do volume. Levanta ValueError se ausente ou insegura."""
    path = Path(mount_point) / VOLUME_MARKER
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        raise ValueError("volume sem marca de identificação (ops marcar-volume)") from None
    import stat as _stat
    if not _stat.S_ISREG(status.st_mode):
        raise ValueError("marca de identificação não é arquivo comum")
    if status.st_mode & 0o022 or status.st_uid not in (0, os.geteuid()) or status.st_size > 4096:
        raise ValueError("marca de identificação com dono, permissão ou tamanho inseguro")
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("marca de identificação ilegível") from None
    if not isinstance(data, dict) or data.get("formato") != "rocha-reis-volume" or not _ID.match(str(data.get("id"))):
        raise ValueError("marca de identificação inválida")
    try:
        data["luks_uuid"] = normalize_luks_uuid(str(data.get("luks_uuid")))
    except ValueError:
        raise ValueError("marca de identificação sem UUID LUKS válido") from None
    return data


@dataclass(frozen=True)
class VolumeStatus:
    state: str
    problems: tuple[str, ...]
    mount_point: str
    device: str | None = None
    luks_uuid: str | None = None
    volume_id: str | None = None
    read_only: bool | None = None

    @property
    def ready(self) -> bool:
        return self.state == READY

    def summary(self) -> str:
        return "; ".join(self.problems) if self.problems else "volume de dados correto montado"

    def as_dict(self) -> dict[str, object]:
        return {"estado": self.state, "problemas": list(self.problems), "ponto_montagem": self.mount_point,
                "dispositivo": self.device, "luks_uuid": self.luks_uuid, "id_volume": self.volume_id,
                "somente_leitura": self.read_only}


def check_data_volume(mount_point: Path, expected: dict[str, object] | None, *, paths: tuple[Path, ...] = (),
                      require_writable: bool = False, sys_root: Path = Path("/sys"),
                      mountinfo: Path = Path("/proc/self/mountinfo")) -> VolumeStatus:
    """Confere o volume de dados. `expected` = {"id", "luks_uuid"} do PERFIL (None só ao preparar).

    AGUARDANDO_DESBLOQUEIO: nada montado no caminho (situação normal após reinício). RECUSADO: algo está
    montado, mas não é o volume correto/seguro. Nunca presume: sem evidência positiva, não está PRONTO.
    """
    mount_point = Path(mount_point)
    label = str(mount_point)
    if any(p.is_symlink() for p in (mount_point, *mount_point.parents)):
        return VolumeStatus(REFUSED, ("ponto de montagem passa por link simbólico",), label)
    if not mount_point.is_dir():
        return VolumeStatus(WAITING, ("ponto de montagem inexistente: volume não montado",), label)
    entry = mount_entry(mount_point, mountinfo=mountinfo)
    if entry is None:
        return VolumeStatus(WAITING, ("diretório presente, mas nenhum volume montado nele",), label)
    major, minor = int(entry["major"]), int(entry["minor"])  # type: ignore[call-overload]
    device = f"{entry['origem']} ({major}:{minor}, {entry['tipo']})"
    read_only = "ro" in entry["opcoes"]  # type: ignore[operator]
    problems: list[str] = []
    kind, uuid = luks_uuid_of(major, minor, sys_root=sys_root)
    if kind is None:
        problems.append("volume montado SEM dm-crypt/LUKS")
    elif uuid is None:
        problems.append(f"dm-crypt {kind} sem UUID LUKS (exige LUKS)")
    volume_id = None
    try:
        marker = read_volume_marker(mount_point)
        volume_id = str(marker["id"])
        if uuid and marker["luks_uuid"] != uuid:
            problems.append("marca de identificação não pertence a este volume LUKS")
    except ValueError as error:
        problems.append(str(error))
        marker = None
    if expected is not None:
        try:
            want_uuid = normalize_luks_uuid(str(expected.get("luks_uuid")))
        except ValueError:
            want_uuid = None
            problems.append("perfil sem UUID LUKS esperado válido")
        if want_uuid and uuid and uuid != want_uuid:
            problems.append("volume INCORRETO: UUID LUKS diferente do esperado no perfil")
        if marker is not None and str(expected.get("id")) != volume_id:
            problems.append("volume INCORRETO: identificação diferente da esperada no perfil")
    root_dev = os.stat(mount_point).st_dev
    real_root = os.path.realpath(mount_point)
    for path in paths:
        path = Path(path)
        if path.is_symlink() or not path.is_dir():
            problems.append(f"pasta de dados ausente no volume: {path.name}")
        elif os.stat(path).st_dev != root_dev or not os.path.realpath(path).startswith(real_root.rstrip("/") + "/"):
            problems.append(f"pasta de dados fora do volume: {path.name}")
        elif require_writable and os.statvfs(path).f_flag & os.ST_RDONLY:
            # Vale a montagem EFETIVA no namespace deste processo: sob ProtectSystem=strict o ponto de montagem
            # pode aparecer somente leitura e só as pastas de ReadWritePaths= graváveis (achado no laboratório).
            problems.append(f"pasta de dados somente leitura: {path.name}")
    if require_writable and not paths and read_only:
        problems.append("volume montado somente leitura")
    return VolumeStatus(REFUSED if problems else READY, tuple(problems), label, device, uuid, volume_id, read_only)


def mark_volume(mount_point: Path, *, sys_root: Path = Path("/sys"),
                mountinfo: Path = Path("/proc/self/mountinfo")) -> dict[str, object]:
    """Grava a marca de identificação num volume LUKS recém-preparado (nunca sobrescreve)."""
    import secrets
    from datetime import datetime, timezone
    mount_point = Path(mount_point)
    entry = mount_entry(mount_point, mountinfo=mountinfo) if mount_point.is_dir() else None
    if entry is None:
        raise ValueError("o caminho não é um ponto de montagem")
    kind, uuid = luks_uuid_of(int(entry["major"]), int(entry["minor"]), sys_root=sys_root)  # type: ignore[call-overload]
    if not uuid:
        raise ValueError("o volume montado não é LUKS")
    target = mount_point / VOLUME_MARKER
    marker = {"formato": "rocha-reis-volume", "versao": 1, "id": secrets.token_hex(16), "luks_uuid": uuid,
              "tipo": kind, "criado_em": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        _json.dump(marker, handle, indent=1)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(target, 0o644)
    return marker
