from __future__ import annotations

import hmac

from cryptography.fernet import Fernet, InvalidToken


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
