"""Segundo fator (TOTP, RFC 6238) do painel — Fase 13.

- Códigos gerados/verificados pela biblioteca consolidada `cryptography` (TOTP SHA-1, 6 dígitos,
  passo de 30 s — o padrão aceito pelos aplicativos autenticadores). Nada de geração própria.
- O segredo TOTP precisa ser recuperável pelo servidor: é guardado CIFRADO com AES-256-GCM, chave
  em arquivo 0600 FORA do banco e dos backups; o id do usuário vai como dado associado (um segredo
  copiado para outra conta não decifra).
- Tolerância de relógio: ±1 passo (±30 s). Código já usado (mesmo passo ou anterior) é recusado.
- Códigos de recuperação: 10 por emissão, 60 bits de entropia cada, guardados só como SHA-256
  (entropia alta torna força bruta inviável), uso único.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import hmac
from pathlib import Path
import secrets
import time

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA1
from cryptography.hazmat.primitives.twofactor import InvalidToken
from cryptography.hazmat.primitives.twofactor.totp import TOTP

from rocha_reis_agent.panel.security import PanelError, new_token, token_hash

STEP_SECONDS = 30
DIGITS = 6
WINDOW = 1                      # passos aceitos antes/depois do atual
CHALLENGE_MINUTES = 5
CHALLENGE_ATTEMPTS = 5
RECOVERY_CODES = 10
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # sem 0/O, 1/I/L: menos erro de digitação


_now = time.time  # ponto de troca do relógio para testes (nunca alterado em execução normal)


class MfaKeyError(Exception):
    """Chave de proteção ausente, inválida ou diferente da usada para cifrar."""


@dataclass(frozen=True)
class MfaCipher:
    key: bytes

    @classmethod
    def from_file(cls, path: Path) -> "MfaCipher":
        from rocha_reis_agent.ops.backup import BackupError, load_key
        try:
            return cls(load_key(path))
        except BackupError as error:
            raise MfaKeyError(f"Chave do segundo fator indisponível: {error}") from None

    @property
    def key_id(self) -> str:
        return sha256(b"rr-mfa-key-id:" + self.key).hexdigest()[:16]

    def encrypt(self, user_id: str, secret: bytes) -> str:
        nonce = secrets.token_bytes(12)
        sealed = AESGCM(self.key).encrypt(nonce, secret, b"rr-mfa-v1:" + user_id.encode())
        return "v1." + base64.urlsafe_b64encode(nonce + sealed).decode("ascii")

    def decrypt(self, user_id: str, blob: str, key_id: str) -> bytes:
        if key_id != self.key_id:
            raise MfaKeyError("O segredo foi cifrado com outra chave (rotação incompleta ou chave errada).")
        try:
            raw = base64.urlsafe_b64decode(blob.removeprefix("v1.").encode("ascii"))
            return AESGCM(self.key).decrypt(raw[:12], raw[12:], b"rr-mfa-v1:" + user_id.encode())
        except (InvalidTag, ValueError):
            raise MfaKeyError("Segredo do segundo fator ilegível com esta chave.") from None


def new_secret() -> bytes:
    return secrets.token_bytes(20)  # 160 bits, recomendado pela RFC 4226


def display_secret(secret: bytes) -> str:
    text = base64.b32encode(secret).decode("ascii").rstrip("=")
    return " ".join(text[i:i + 4] for i in range(0, len(text), 4))


def _totp(secret: bytes) -> TOTP:
    return TOTP(secret, DIGITS, SHA1(), STEP_SECONDS, enforce_key_length=True)


def provisioning_uri(secret: bytes, *, account: str, issuer: str) -> str:
    return _totp(secret).get_provisioning_uri(account, issuer)


def matching_step(secret: bytes, code: str, *, now: float | None = None) -> int | None:
    """Passo TOTP cujo código confere (janela ±WINDOW), ou None. Verificação pela biblioteca."""
    code = "".join(code.split())
    if len(code) != DIGITS or not code.isdigit():
        return None
    moment = _now() if now is None else now
    current = int(moment // STEP_SECONDS)
    totp = _totp(secret)
    for step in sorted(range(current - WINDOW, current + WINDOW + 1), key=lambda s: abs(s - current)):
        try:
            totp.verify(code.encode("ascii"), step * STEP_SECONDS)
            return step
        except InvalidToken:
            continue
    return None


def code_for(secret: bytes, *, now: float | None = None) -> str:
    """Código atual (usado só por testes e pelo ensaio local; nunca exposto pela API)."""
    moment = _now() if now is None else now
    return _totp(secret).generate(int(moment)).decode("ascii")


def new_recovery_codes() -> list[str]:
    codes = []
    for _ in range(RECOVERY_CODES):
        raw = "".join(secrets.choice(_ALPHABET) for _ in range(12))
        codes.append(f"{raw[:4]}-{raw[4:8]}-{raw[8:]}")
    return codes


def recovery_hash(user_id: str, code: str) -> str:
    normalized = "".join(c for c in code.upper() if c.isalnum())
    return hmac.new(user_id.encode(), b"rr-recovery-v1:" + normalized.encode(), sha256).hexdigest()


def new_challenge() -> tuple[str, str]:
    token = new_token()
    return token, token_hash(token)


def challenge_expiry(now: datetime) -> datetime:
    return now + timedelta(minutes=CHALLENGE_MINUTES)


def require_cipher(cipher: MfaCipher | None) -> MfaCipher:
    if cipher is None:
        raise PanelError(409, "Segundo fator indisponível neste ambiente: a chave de proteção não foi configurada.")
    return cipher


def rotate_key(store, old: MfaCipher, new: MfaCipher) -> int:  # type: ignore[no-untyped-def]
    """Recifra TODOS os segredos com a chave nova numa transação. Falha (chave antiga errada) = nada muda."""
    rows = []
    for row in store.mfa_all():
        secret = old.decrypt(row["user_id"], row["secret_blob"], row["key_id"])
        rows.append((row["user_id"], new.encrypt(row["user_id"], secret), new.key_id))
    return store.mfa_reencrypt(rows)
