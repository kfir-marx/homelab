from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import httpx
import structlog
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .database import MailboxConnection
from .oauth import OAuthError, OAuthService

logger = structlog.get_logger()


class WebhookService:
    """Registers renewable provider watches after delegated mailbox consent."""

    def __init__(
        self,
        settings: Settings,
        factory: sessionmaker[Session],
        oauth: OAuthService,
        client: httpx.AsyncClient,
    ) -> None:
        self._settings = settings
        self._factory = factory
        self._oauth = oauth
        self._client = client

    async def ensure(self, mailbox: MailboxConnection) -> bool:
        if mailbox.provider == "gmail" and not self._settings.gmail_pubsub_topic:
            logger.info("gmail_watch_not_configured", mailbox_id=mailbox.id)
            return False
        try:
            access_token = await self._oauth.access_token(mailbox)
            if mailbox.provider == "gmail":
                return await self._gmail(mailbox, access_token)
            return await self._outlook(mailbox, access_token)
        except (OAuthError, httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "mailbox_webhook_registration_failed",
                provider=mailbox.provider,
                mailbox_id=mailbox.id,
                error=str(exc),
            )
            return False

    async def _gmail(self, mailbox: MailboxConnection, access_token: str) -> bool:
        response = await self._client.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/watch",
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "topicName": self._settings.gmail_pubsub_topic,
                "labelIds": ["INBOX"],
                "labelFilterBehavior": "include",
            },
        )
        response.raise_for_status()
        body = response.json()
        expires_ms = int(body["expiration"])
        with self._factory.begin() as session:
            current = session.get(MailboxConnection, mailbox.id)
            if current:
                current.webhook_subscription_id = mailbox.email_address
                current.webhook_cursor = str(body.get("historyId", "")) or None
                current.webhook_expires_at = datetime.fromtimestamp(expires_ms / 1000, UTC)
        return True

    async def _outlook(self, mailbox: MailboxConnection, access_token: str) -> bool:
        expires = datetime.now(UTC) + timedelta(days=2, hours=20)
        client_state = mailbox.webhook_client_state or secrets.token_urlsafe(32)
        body = {
            "changeType": "created,updated",
            "notificationUrl": self._settings.webhook_url("outlook"),
            "resource": "me/messages",
            "expirationDateTime": expires.isoformat().replace("+00:00", "Z"),
            "clientState": client_state,
        }
        response = await self._client.post(
            "https://graph.microsoft.com/v1.0/subscriptions",
            headers={"Authorization": f"Bearer {access_token}"},
            json=body,
        )
        response.raise_for_status()
        payload = response.json()
        with self._factory.begin() as session:
            current = session.get(MailboxConnection, mailbox.id)
            if current:
                current.webhook_subscription_id = str(payload["id"])
                current.webhook_client_state = client_state
                raw_expiry = str(payload.get("expirationDateTime", body["expirationDateTime"]))
                current.webhook_expires_at = datetime.fromisoformat(
                    raw_expiry.replace("Z", "+00:00")
                )
        return True


def webhook_active(mailbox: MailboxConnection) -> bool:
    expires = mailbox.webhook_expires_at
    if not expires:
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    return expires > datetime.now(UTC)
