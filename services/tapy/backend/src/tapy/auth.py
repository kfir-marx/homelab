from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete
from sqlalchemy.orm import Session

from .database import UserSession, token_hash


class TokenCipher:
    def __init__(self, key: str) -> None:
        if not key:
            raise ValueError("OAuth token encryption key is not configured")
        self._fernet = Fernet(key.encode())

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("stored OAuth token cannot be decrypted") from exc


def bearer_token(authorization: str | None) -> str:
    if not authorization:
        return ""
    scheme, separator, value = authorization.partition(" ")
    if separator != " " or not hmac.compare_digest(scheme.casefold(), "bearer"):
        return ""
    return value.strip()


def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$" + base64.urlsafe_b64encode(salt + digest).decode()


def password_matches(password: str, encoded: str | None) -> bool:
    if not encoded or not encoded.startswith("scrypt$"):
        return False
    try:
        value = base64.urlsafe_b64decode(encoded.removeprefix("scrypt$").encode())
        salt, expected = value[:16], value[16:]
        actual = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def new_session(session: Session, user_id: str, days: int = 30) -> tuple[str, datetime]:
    now = datetime.now(UTC)
    session.execute(delete(UserSession).where(UserSession.expires_at < now))
    token = "ses_" + secrets.token_urlsafe(32)
    expires_at = now + timedelta(days=days)
    session.add(UserSession(token_hash=token_hash(token), user_id=user_id, expires_at=expires_at))
    return token, expires_at
