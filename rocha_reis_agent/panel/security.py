"""Senhas (Argon2id via argon2-cffi), política de senha e tokens de sessão."""

from hashlib import sha256
import hmac
import re
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from rocha_reis_agent.domain.contracts import SimulationError

# Parâmetros padrão da biblioteca: Argon2id, perfil RFC 9106 de baixa memória.
HASHER = PasswordHasher()
LOGIN = re.compile(r"[a-z0-9._-]{3,64}")


class PanelError(SimulationError):
    """Erro de painel com status HTTP e mensagem segura (sem dados privados).

    Herda de SimulationError para atravessar a unidade de trabalho sem ser
    convertido em erro genérico de armazenamento (e causar rollback completo).
    """

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def normalize_login(value: object) -> str:
    login = value.strip().lower() if isinstance(value, str) else ""
    if LOGIN.fullmatch(login) is None:
        raise PanelError(422, "Login deve ter 3 a 64 letras minúsculas, números, ponto, hífen ou sublinhado.")
    return login


def validate_password(password: object, login: str) -> str:
    if not isinstance(password, str) or not 12 <= len(password) <= 128 or not password.isprintable():
        raise PanelError(422, "A senha precisa ter de 12 a 128 caracteres imprimíveis.")
    if login and login in password.lower():
        raise PanelError(422, "A senha não pode conter o login.")
    if len(set(password)) < 6:
        raise PanelError(422, "A senha é previsível demais; use caracteres variados.")
    return password


def hash_password(password: str) -> str:
    return HASHER.hash(password)


_DUMMY_HASH = HASHER.hash(secrets.token_urlsafe(24))


def verify_password(stored_hash: str | None, password: str) -> bool:
    """Tempo semelhante para usuário inexistente (hash fictício) — reduz enumeração."""
    try:
        return HASHER.verify(stored_hash or _DUMMY_HASH, password) and stored_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def csrf_for(token: str) -> str:
    """Token CSRF derivado do token de sessão (HMAC): nunca armazenado em texto."""
    return hmac.new(token.encode("utf-8"), b"rocha-reis-panel-csrf-v1", sha256).hexdigest()
