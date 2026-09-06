from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from .auth import TokenCipher
from .config import Settings
from .database import MailboxConnection, OAuthState
from .models import Provider

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
MICROSOFT_SCOPES = "offline_access User.Read Mail.Read"


class OAuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class OAuthTokens:
    access_token: str
    refresh_token: str
    scopes: str


def _state_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class OAuthService:
    def __init__(
        self,
        settings: Settings,
        factory: sessionmaker[Session],
        client: httpx.AsyncClient,
    ) -> None:
        self._settings = settings
        self._factory = factory
        self._client = client

    def _configured(self, provider: Provider) -> bool:
        try:
            TokenCipher(self._settings.oauth_token_encryption_key.get_secret_value())
        except (TypeError, ValueError):
            return False
        if provider == "gmail":
            return bool(
                self._settings.google_oauth_client_id
                and self._settings.google_oauth_client_secret.get_secret_value()
            )
        return bool(
            self._settings.microsoft_oauth_client_id
            and self._settings.microsoft_oauth_client_secret.get_secret_value()
        )

    def authorization_url(self, agent_id: str, provider: Provider) -> str:
        if not self._configured(provider):
            raise OAuthError(f"{provider} OAuth is not configured")
        state = secrets.token_urlsafe(32)
        expires = datetime.now(UTC) + timedelta(seconds=self._settings.oauth_state_ttl_seconds)
        with self._factory.begin() as session:
            session.execute(delete(OAuthState).where(OAuthState.expires_at < datetime.now(UTC)))
            session.add(
                OAuthState(
                    state_hash=_state_hash(state),
                    agent_id=agent_id,
                    provider=provider,
                    expires_at=expires,
                )
            )
        redirect_uri = f"{self._settings.public_base_url.rstrip('/')}/v1/oauth/{provider}/callback"
        if provider == "gmail":
            query = urlencode(
                {
                    "client_id": self._settings.google_oauth_client_id,
                    "redirect_uri": redirect_uri,
                    "response_type": "code",
                    "scope": GMAIL_SCOPE,
                    "access_type": "offline",
                    "prompt": "consent",
                    "include_granted_scopes": "true",
                    "state": state,
                }
            )
            return f"https://accounts.google.com/o/oauth2/v2/auth?{query}"
        tenant = self._settings.microsoft_tenant
        query = urlencode(
            {
                "client_id": self._settings.microsoft_oauth_client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "response_mode": "query",
                "scope": MICROSOFT_SCOPES,
                "state": state,
            }
        )
        return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize?{query}"

    async def complete(self, provider: Provider, state: str, code: str) -> MailboxConnection:
        with self._factory.begin() as session:
            record = session.get(OAuthState, _state_hash(state))
            if (
                not record
                or record.provider != provider
                or _as_utc(record.expires_at) < datetime.now(UTC)
            ):
                raise OAuthError("OAuth state is invalid or expired")
            agent_id = record.agent_id
            session.delete(record)
        tokens = await self._exchange(provider, code)
        account_id, email = await self._profile(provider, tokens.access_token)
        cipher = TokenCipher(self._settings.oauth_token_encryption_key.get_secret_value())
        with self._factory.begin() as session:
            account_mailbox = session.scalar(
                select(MailboxConnection).where(
                    MailboxConnection.provider == provider,
                    MailboxConnection.provider_account_id == account_id,
                )
            )
            if account_mailbox and account_mailbox.agent_id != agent_id:
                raise OAuthError("mailbox is already connected to another agent")
            mailbox = account_mailbox or session.scalar(
                select(MailboxConnection).where(
                    MailboxConnection.agent_id == agent_id,
                    MailboxConnection.provider == provider,
                )
            )
            if mailbox is None:
                mailbox = MailboxConnection(
                    agent_id=agent_id,
                    provider=provider,
                    provider_account_id=account_id,
                    email_address=email,
                    refresh_token="",
                    scopes=tokens.scopes,
                )
                session.add(mailbox)
            mailbox.provider_account_id = account_id
            mailbox.email_address = email
            mailbox.refresh_token = cipher.encrypt(tokens.refresh_token)
            mailbox.scopes = tokens.scopes
            session.flush()
            return mailbox

    async def access_token(self, mailbox: MailboxConnection) -> str:
        cipher = TokenCipher(self._settings.oauth_token_encryption_key.get_secret_value())
        refresh_token = cipher.decrypt(mailbox.refresh_token)
        if mailbox.provider == "gmail":
            url = "https://oauth2.googleapis.com/token"
            data = {
                "client_id": self._settings.google_oauth_client_id,
                "client_secret": self._settings.google_oauth_client_secret.get_secret_value(),
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }
        else:
            url = (
                f"https://login.microsoftonline.com/{self._settings.microsoft_tenant}"
                "/oauth2/v2.0/token"
            )
            data = {
                "client_id": self._settings.microsoft_oauth_client_id,
                "client_secret": self._settings.microsoft_oauth_client_secret.get_secret_value(),
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
                "scope": MICROSOFT_SCOPES,
            }
        try:
            response = await self._client.post(url, data=data)
            response.raise_for_status()
            payload = response.json()
            token = payload["access_token"]
            if not isinstance(token, str):
                raise TypeError
            rotated = payload.get("refresh_token")
            if isinstance(rotated, str) and rotated and rotated != refresh_token:
                with self._factory.begin() as session:
                    current = session.get(MailboxConnection, mailbox.id)
                    if current:
                        current.refresh_token = cipher.encrypt(rotated)
            return token
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise OAuthError(f"could not refresh {mailbox.provider} authorization") from exc

    async def _exchange(self, provider: Provider, code: str) -> OAuthTokens:
        redirect_uri = f"{self._settings.public_base_url.rstrip('/')}/v1/oauth/{provider}/callback"
        if provider == "gmail":
            url = "https://oauth2.googleapis.com/token"
            data = {
                "client_id": self._settings.google_oauth_client_id,
                "client_secret": self._settings.google_oauth_client_secret.get_secret_value(),
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            }
        else:
            url = (
                f"https://login.microsoftonline.com/{self._settings.microsoft_tenant}"
                "/oauth2/v2.0/token"
            )
            data = {
                "client_id": self._settings.microsoft_oauth_client_id,
                "client_secret": self._settings.microsoft_oauth_client_secret.get_secret_value(),
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "scope": MICROSOFT_SCOPES,
            }
        try:
            response = await self._client.post(url, data=data)
            response.raise_for_status()
            payload = response.json()
            access = payload["access_token"]
            refresh = payload["refresh_token"]
            if not isinstance(access, str) or not isinstance(refresh, str):
                raise TypeError
            return OAuthTokens(access, refresh, str(payload.get("scope", "")))
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise OAuthError(f"could not exchange {provider} authorization code") from exc

    async def _profile(self, provider: Provider, access_token: str) -> tuple[str, str]:
        headers = {"Authorization": f"Bearer {access_token}"}
        url = (
            "https://gmail.googleapis.com/gmail/v1/users/me/profile"
            if provider == "gmail"
            else "https://graph.microsoft.com/v1.0/me?$select=id,mail,userPrincipalName"
        )
        try:
            response = await self._client.get(url, headers=headers)
            response.raise_for_status()
            payload = response.json()
            if provider == "gmail":
                account_id = email = payload["emailAddress"]
            else:
                account_id = payload["id"]
                email = payload.get("mail") or payload["userPrincipalName"]
            if not isinstance(account_id, str) or not isinstance(email, str):
                raise TypeError
            return account_id, email
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise OAuthError(f"could not read {provider} mailbox profile") from exc
