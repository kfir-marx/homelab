from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass

import httpx
from fastapi import HTTPException, status

GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"


@dataclass(frozen=True)
class AuthenticatedUser:
    subject: str
    email: str | None


class GoogleTokenValidator:
    def __init__(self, client: httpx.AsyncClient, tokeninfo_url: str, client_id: str) -> None:
        self._client = client
        self._tokeninfo_url = tokeninfo_url
        self._client_id = client_id

    async def validate(self, token: str) -> AuthenticatedUser:
        try:
            response = await self._client.get(
                self._tokeninfo_url,
                params={"access_token": token},
                timeout=10,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "invalid Google access token"
            ) from exc
        scopes = set(str(payload.get("scope", "")).split())
        audience = str(payload.get("aud", payload.get("audience", "")))
        subject = str(payload.get("sub", payload.get("user_id", "")))
        try:
            expires_in = int(payload.get("expires_in", 0))
        except (TypeError, ValueError):
            expires_in = 0
        if (
            audience != self._client_id
            or GMAIL_READONLY not in scopes
            or not subject
            or expires_in <= 0
        ):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "token is not valid for this extension"
            )
        email = payload.get("email")
        return AuthenticatedUser(subject=subject, email=str(email) if email else None)


class PerUserRateLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self._limit = limit
        self._window_seconds = window_seconds
        self._requests: defaultdict[str, deque[float]] = defaultdict(deque)

    def check(self, subject: str) -> None:
        now = time.monotonic()
        cutoff = now - self._window_seconds
        entries = self._requests[subject]
        while entries and entries[0] < cutoff:
            entries.popleft()
        if len(entries) >= self._limit:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "per-user rate limit exceeded")
        entries.append(now)
