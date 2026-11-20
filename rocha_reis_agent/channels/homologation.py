"""Homologação controlada (Fase 6): autorização explícita, allowlist e limites.

A autorização é um ARQUIVO privado (0600, do próprio usuário, sem symlink, fora do
repositório) preenchido pelo responsável, não uma variável de ambiente. Mesmo com
um arquivo válido, o envio REAL continua impedido pela trava de código
`HOMOLOGATION_TRANSPORT_ACTIVATION_AUTHORIZED` (config.py), que só pode ser
alterada na Fase 6B, com autorização específica e revisão.

Nada aqui contém números de teste: eles vêm somente do arquivo de autorização.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hmac
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from typing import Literal

from rocha_reis_agent.channels.contracts import ChannelError, Sender
from rocha_reis_agent.channels.meta import BSUID, NUMERIC_ID

SCHEMA = "rocha-reis-homolog-authorization-v1"
OPERATIONS = frozenset({"receive_webhook", "send_text_reply"})
MAX_DURATION = timedelta(hours=72)
MAX_FILE_BYTES = 16384
# Tetos rígidos de código: o arquivo pode pedir menos, nunca mais.
HARD_CAPS = {
    "max_messages_total": 100,
    "max_messages_per_recipient": 30,
    "max_messages_per_run": 20,
    "max_messages_per_minute": 10,
}
AUTH_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
PHONE = re.compile(r"[0-9]{8,15}")
KEYS = {"schema", "authorization_id", "environment", "authorized_by", "executor", "phone_number_id",
        "allowed_recipients", "valid_from", "valid_until", "limits", "operations", "confirmation"}


class HomologationError(ChannelError):
    """Autorização ausente/inválida. Mensagens nunca incluem valores do arquivo."""


@dataclass(frozen=True, slots=True)
class HomologationLimits:
    max_messages_total: int
    max_messages_per_recipient: int
    max_messages_per_run: int
    max_messages_per_minute: int


@dataclass(frozen=True)
class HomologationAuthorization:
    authorization_id: str
    phone_number_id: str
    allowed_recipients: frozenset[str] = field(repr=False)
    valid_from: datetime
    valid_until: datetime
    limits: HomologationLimits
    operations: frozenset[str]

    def allows(self, sender: Sender) -> bool:
        return bool((sender.wa_id and sender.wa_id in self.allowed_recipients)
                    or (sender.user_id and sender.user_id in self.allowed_recipients))

    def accepts_inbound(self, sender: Sender) -> bool:
        return "receive_webhook" in self.operations and self.allows(sender)

    def active(self, now: datetime) -> bool:
        return self.valid_from <= now < self.valid_until


def _private_file(path: Path) -> bytes:
    if not path.is_absolute():
        raise HomologationError("Arquivo de autorização exige caminho absoluto.")
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise HomologationError("Arquivo de autorização não pode usar links simbólicos.")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise HomologationError("Arquivo de autorização ausente ou ilegível.") from None
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o777 != 0o600
                or info.st_uid != os.geteuid() or info.st_size > MAX_FILE_BYTES):
            raise HomologationError("Arquivo de autorização precisa ser regular, 0600, do usuário e pequeno.")
        return os.read(descriptor, MAX_FILE_BYTES + 1)
    finally:
        os.close(descriptor)


def _instant(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise HomologationError("Datas da autorização inválidas.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HomologationError("Datas da autorização inválidas.") from None
    if parsed.utcoffset() is None:
        raise HomologationError("Datas da autorização exigem fuso horário.")
    return parsed


def load_authorization(path: Path, *, expected_phone_number_id: str) -> HomologationAuthorization:
    """Valida o documento inteiro; qualquer falha bloqueia a homologação."""
    raw = _private_file(path)  # fora do try: HomologationError também é ValueError
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        raise HomologationError("Arquivo de autorização não é JSON válido.") from None
    if not isinstance(data, dict) or set(data) != KEYS or data["schema"] != SCHEMA:
        raise HomologationError("Estrutura da autorização inválida.")
    if data["environment"] != "homologation":
        raise HomologationError("Autorização não é de homologação.")
    auth_id = data["authorization_id"]
    if not isinstance(auth_id, str) or AUTH_ID.fullmatch(auth_id) is None:
        raise HomologationError("Identificador da autorização inválido.")
    if data["confirmation"] != f"AUTORIZO HOMOLOGACAO {auth_id}":
        raise HomologationError("Frase de confirmação ausente ou divergente.")
    for name in ("authorized_by", "executor"):
        value = data[name]
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 120 or not value.isprintable():
            raise HomologationError("Responsável e executor devem ser identificados.")
    number = data["phone_number_id"]
    if not isinstance(number, str) or NUMERIC_ID.fullmatch(number) is None or number != expected_phone_number_id:
        raise HomologationError("Número comercial da autorização diverge da configuração.")
    recipients = data["allowed_recipients"]
    if (not isinstance(recipients, list) or not 1 <= len(recipients) <= 10 or len(set(recipients)) != len(recipients)
            or not all(isinstance(r, str) and (PHONE.fullmatch(r) or BSUID.fullmatch(r)) for r in recipients)):
        raise HomologationError("Allowlist inválida (1 a 10 telefones/BSUIDs únicos).")
    operations = data["operations"]
    if not isinstance(operations, list) or not operations or not set(operations) <= OPERATIONS:
        raise HomologationError("Operações autorizadas inválidas.")
    valid_from, valid_until = _instant(data["valid_from"]), _instant(data["valid_until"])
    if not valid_from < valid_until or valid_until - valid_from > MAX_DURATION:
        raise HomologationError("Período de homologação inválido (máximo 72h).")
    limits = data["limits"]
    if not isinstance(limits, dict) or set(limits) != set(HARD_CAPS):
        raise HomologationError("Todos os limites de envio são obrigatórios.")
    for name, cap in HARD_CAPS.items():
        if type(limits[name]) is not int or not 1 <= limits[name] <= cap:
            raise HomologationError("Limite de envio fora do permitido.")
    parsed = HomologationLimits(**limits)
    if parsed.max_messages_per_recipient > parsed.max_messages_total or parsed.max_messages_per_run > parsed.max_messages_total:
        raise HomologationError("Limites de envio inconsistentes.")
    return HomologationAuthorization(auth_id, number, frozenset(recipients), valid_from, valid_until,
                                     parsed, frozenset(operations))


@dataclass(frozen=True, slots=True)
class GuardCounts:
    total: int
    per_recipient: int
    per_run: int
    last_minute: int


@dataclass(frozen=True, slots=True)
class GuardDecision:
    action: Literal["send", "wait", "suppress"]
    reason: str


class HomologationGuard:
    """Decide cada tentativa. 'wait' não altera a outbox; 'suppress' cancela a linha."""

    def __init__(self, authorization: HomologationAuthorization, *, recipient_key: bytes, run_id: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{32}", run_id) or len(recipient_key) < 32:
            raise HomologationError("Execução de homologação inválida.")
        self.authorization = authorization
        self.recipient_key = recipient_key
        self.run_id = run_id

    def recipient_hash(self, recipient: Sender) -> str:
        return hmac.new(self.recipient_key, recipient.identity_key.encode("utf-8"), sha256).hexdigest()

    def evaluate(self, *, now: datetime, business_number_id: str, recipient: Sender, counts: GuardCounts) -> GuardDecision:
        auth = self.authorization
        if not auth.active(now):
            return GuardDecision("wait", "authorization_not_active")
        if "send_text_reply" not in auth.operations:
            return GuardDecision("wait", "operation_not_authorized")
        if business_number_id != auth.phone_number_id:
            return GuardDecision("suppress", "homolog_number_mismatch")
        if not auth.allows(recipient):
            return GuardDecision("suppress", "recipient_not_allowlisted")
        limits = auth.limits
        if counts.total >= limits.max_messages_total:
            return GuardDecision("wait", "homolog_total_limit")
        if counts.per_recipient >= limits.max_messages_per_recipient:
            return GuardDecision("wait", "homolog_recipient_limit")
        if counts.per_run >= limits.max_messages_per_run:
            return GuardDecision("wait", "homolog_run_limit")
        if counts.last_minute >= limits.max_messages_per_minute:
            return GuardDecision("wait", "homolog_rate_limit")
        return GuardDecision("send", "authorized")
