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
from .database import AuthIdentity, MailboxConnection, OAuthState, User
from .models import AuthProvider, Provider

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GOOGLE_LOGIN_SCOPES = "openid email profile"
MICROSOFT_MAIL_SCOPES = "offline_access User.Read Mail.Read"
MICROSOFT_LOGIN_SCOPES = "openid email profile User.Read"


class OAuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class OAuthTokens:
    access_token: str
    refresh_token: str | None
    scopes: str


@dataclass(frozen=True)
class LoginCompletion:
    user: User
    created: bool


def _state_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _auth_to_mail_provider(provider: AuthProvider) -> Provider:
    return "gmail" if provider == "google" else "outlook"


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
        if provider == "gmail":
            return bool(
                self._settings.google_oauth_client_id
                and self._settings.google_oauth_client_secret.get_secret_value()
            )
        return bool(
            self._settings.microsoft_oauth_client_id
            and self._settings.microsoft_oauth_client_secret.get_secret_value()
        )

    def authorization_url(self, user_id: str, provider: Provider) -> str:
        return self._authorization_url(provider, "mailbox", user_id)

    def login_url(self, provider: AuthProvider) -> str:
        return self._authorization_url(_auth_to_mail_provider(provider), "login", None)

    def _authorization_url(self, provider: Provider, purpose: str, user_id: str | None) -> str:
        if not self._configured(provider):
            raise OAuthError(f"{provider} OAuth is not configured")
        if purpose == "mailbox":
            try:
                TokenCipher(self._settings.oauth_token_encryption_key.get_secret_value())
            except (TypeError, ValueError) as exc:
                raise OAuthError("OAuth token encryption is not configured") from exc
        state = secrets.token_urlsafe(32)
        expires = datetime.now(UTC) + timedelta(seconds=self._settings.oauth_state_ttl_seconds)
        with self._factory.begin() as session:
            session.execute(delete(OAuthState).where(OAuthState.expires_at < datetime.now(UTC)))
            session.add(
                OAuthState(
                    state_hash=_state_hash(state),
                    user_id=user_id,
                    provider=provider,
                    purpose=purpose,
                    expires_at=expires,
                )
            )
        redirect_uri = self._settings.oauth_redirect_uri(provider)
        if provider == "gmail":
            params = {
                "client_id": self._settings.google_oauth_client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": GMAIL_SCOPE if purpose == "mailbox" else GOOGLE_LOGIN_SCOPES,
                "state": state,
            }
            if purpose == "mailbox":
                params.update(
                    access_type="offline", prompt="consent", include_granted_scopes="true"
                )
            return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
        scopes = MICROSOFT_MAIL_SCOPES if purpose == "mailbox" else MICROSOFT_LOGIN_SCOPES
        query = urlencode(
            {
                "client_id": self._settings.microsoft_oauth_client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "response_mode": "query",
                "scope": scopes,
                "state": state,
            }
        )
        return (
            f"https://login.microsoftonline.com/{self._settings.microsoft_tenant}"
            f"/oauth2/v2.0/authorize?{query}"
        )

    def _consume_state(self, provider: Provider, state: str, purpose: str) -> str | None:
        with self._factory.begin() as session:
            record = session.get(OAuthState, _state_hash(state))
            if (
                not record
                or record.provider != provider
                or record.purpose != purpose
                or _as_utc(record.expires_at) < datetime.now(UTC)
            ):
                raise OAuthError("OAuth state is invalid or expired")
            user_id = record.user_id
            session.delete(record)
        return user_id

    def state_purpose(self, provider: Provider, state: str) -> str:
        with self._factory() as session:
            record = session.get(OAuthState, _state_hash(state))
            if (
                not record
                or record.provider != provider
                or _as_utc(record.expires_at) < datetime.now(UTC)
            ):
                raise OAuthError("OAuth state is invalid or expired")
            return record.purpose

    async def complete(self, provider: Provider, state: str, code: str) -> MailboxConnection:
        user_id = self._consume_state(provider, state, "mailbox")
        if not user_id:
            raise OAuthError("OAuth state is not associated with a user")
        tokens = await self._exchange(provider, code, "mailbox")
        if not tokens.refresh_token:
            raise OAuthError("provider did not return an offline refresh token")
        account_id, email, _ = await self._profile(provider, tokens.access_token, "mailbox")
        cipher = TokenCipher(self._settings.oauth_token_encryption_key.get_secret_value())
        with self._factory.begin() as session:
            account_mailbox = session.scalar(
                select(MailboxConnection).where(
                    MailboxConnection.provider == provider,
                    MailboxConnection.provider_account_id == account_id,
                )
            )
            if account_mailbox and account_mailbox.user_id != user_id:
                raise OAuthError("mailbox is already connected to another user")
            mailbox = account_mailbox or session.scalar(
                select(MailboxConnection).where(
                    MailboxConnection.user_id == user_id,
                    MailboxConnection.provider == provider,
                )
            )
            if mailbox is None:
                mailbox = MailboxConnection(
                    user_id=user_id,
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

    async def complete_login(
        self, provider: AuthProvider, state: str, code: str
    ) -> LoginCompletion:
        mail_provider = _auth_to_mail_provider(provider)
        self._consume_state(mail_provider, state, "login")
        tokens = await self._exchange(mail_provider, code, "login")
        subject, email, name = await self._profile(mail_provider, tokens.access_token, "login")
        normalized_email = email.strip().casefold()
        with self._factory.begin() as session:
            identity = session.scalar(
                select(AuthIdentity).where(
                    AuthIdentity.provider == provider, AuthIdentity.subject == subject
                )
            )
            if identity:
                user = session.get(User, identity.user_id)
                if not user:
                    raise OAuthError("login identity has no user")
                return LoginCompletion(user, False)
            user = session.scalar(select(User).where(User.email == normalized_email))
            created = user is None
            if user is None:
                user = User(email=normalized_email, name=name or normalized_email.split("@")[0])
                session.add(user)
                session.flush()
            session.add(AuthIdentity(user_id=user.id, provider=provider, subject=subject))
            session.flush()
            return LoginCompletion(user, created)

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
                "scope": MICROSOFT_MAIL_SCOPES,
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

    async def _exchange(self, provider: Provider, code: str, purpose: str) -> OAuthTokens:
        redirect_uri = self._settings.oauth_redirect_uri(provider)
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
                "scope": MICROSOFT_MAIL_SCOPES if purpose == "mailbox" else MICROSOFT_LOGIN_SCOPES,
            }
        try:
            response = await self._client.post(url, data=data)
            response.raise_for_status()
            payload = response.json()
            access = payload["access_token"]
            refresh = payload.get("refresh_token")
            if not isinstance(access, str) or (
                refresh is not None and not isinstance(refresh, str)
            ):
                raise TypeError
            return OAuthTokens(access, refresh, str(payload.get("scope", "")))
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise OAuthError(f"could not exchange {provider} authorization code") from exc

    async def _profile(
        self, provider: Provider, access_token: str, purpose: str
    ) -> tuple[str, str, str]:
        headers = {"Authorization": f"Bearer {access_token}"}
        url = (
            "https://openidconnect.googleapis.com/v1/userinfo"
            if provider == "gmail" and purpose == "login"
            else "https://gmail.googleapis.com/gmail/v1/users/me/profile"
            if provider == "gmail"
            else "https://graph.microsoft.com/v1.0/me?$select=id,displayName,mail,userPrincipalName"
        )
        try:
            response = await self._client.get(url, headers=headers)
            response.raise_for_status()
            payload = response.json()
            if provider == "gmail":
                if purpose == "login" and payload.get("email_verified") is not True:
                    raise OAuthError("Google account email is not verified")
                email = payload.get("email") or payload["emailAddress"]
                account_id = payload.get("sub") or email
                name = payload.get("name") or ""
            else:
                account_id = payload["id"]
                email = payload.get("mail") or payload["userPrincipalName"]
                name = payload.get("displayName") or ""
            if not isinstance(account_id, str) or not isinstance(email, str):
                raise TypeError
            return account_id, email, str(name)
        except OAuthError:
            raise
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise OAuthError(f"could not read {provider} profile") from exc
