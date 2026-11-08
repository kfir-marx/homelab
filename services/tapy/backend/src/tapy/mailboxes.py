from __future__ import annotations

import base64
import re
from collections.abc import AsyncIterator, Mapping
from html import unescape
from typing import Any, Protocol

import httpx

from .models import EmailForAnalysis, Provider


class MailboxError(RuntimeError):
    pass


class GmailHistoryExpired(MailboxError):
    """The saved Gmail history cursor is no longer available."""


class MailboxReader(Protocol):
    def pages(self, access_token: str, limit: int) -> AsyncIterator[list[EmailForAnalysis]]: ...
    async def messages(self, access_token: str, limit: int) -> list[EmailForAnalysis]: ...


class GmailIncrementalReader(MailboxReader, Protocol):
    async def history(self, access_token: str, start_history_id: str) -> tuple[list[str], str]: ...
    async def message_ids(self, access_token: str, limit: int) -> list[str]: ...
    async def message(
        self, access_token: str, message_id: str
    ) -> EmailForAnalysis | None: ...


def _plain_html(value: str) -> str:
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    return " ".join(unescape(value).split())


def _decode(value: str) -> str:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding).decode("utf-8", errors="replace")


def _gmail_body(part: Mapping[str, Any]) -> str:
    mime_type = str(part.get("mimeType", ""))
    data = part.get("body", {}).get("data")
    if isinstance(data, str) and mime_type in {"text/plain", "text/html"}:
        decoded = _decode(data)
        return decoded if mime_type == "text/plain" else _plain_html(decoded)
    bodies = [_gmail_body(child) for child in part.get("parts", []) if isinstance(child, dict)]
    plain = [body for body in bodies if body]
    return "\n".join(plain)


class PagedReader:
    async def _page(
        self, access_token: str, limit: int, cursor: str | None
    ) -> tuple[list[EmailForAnalysis], str | None, int]:
        raise NotImplementedError

    async def pages(self, access_token: str, limit: int) -> AsyncIterator[list[EmailForAnalysis]]:
        cursor = None
        seen_cursors = set()
        count = 0
        while count < limit:
            page, cursor, fetched = await self._page(access_token, min(100, limit - count), cursor)
            count += fetched
            yield page
            if not cursor:
                return
            if cursor in seen_cursors:
                raise MailboxError("Mailbox pagination repeated a cursor")
            seen_cursors.add(cursor)
        if cursor:
            raise MailboxError("Scan limit reached; increase the configured scan limit")

    async def messages(self, access_token: str, limit: int) -> list[EmailForAnalysis]:
        return [email async for page in self.pages(access_token, limit) for email in page]


class GmailReader(PagedReader):
    def __init__(self, client: httpx.AsyncClient, query: str) -> None:
        self._client = client
        self._query = query

    @staticmethod
    def _email(payload: Mapping[str, Any]) -> EmailForAnalysis | None:
        metadata = {
            str(item.get("name", "")).casefold(): str(item.get("value", ""))
            for item in payload.get("payload", {}).get("headers", [])
        }
        body = _gmail_body(payload.get("payload", {})).strip()[:40_000]
        if not body:
            return None
        return EmailForAnalysis(
            message_id=str(payload["id"]),
            thread_id=str(payload.get("threadId", "")) or None,
            subject=metadata.get("subject", "")[:500],
            sender=metadata.get("from", "")[:500],
            sent_at=metadata.get("date") or None,
            body_text=body,
        )

    async def message(self, access_token: str, message_id: str) -> EmailForAnalysis | None:
        try:
            response = await self._client.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"format": "full"},
            )
            response.raise_for_status()
            return self._email(response.json())
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise MailboxError("Gmail message request failed") from exc

    async def message_ids(self, access_token: str, limit: int) -> list[str]:
        """List message ids for bounded recovery when Gmail has expired a history cursor."""
        headers = {"Authorization": f"Bearer {access_token}"}
        result: list[str] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        try:
            while len(result) < limit:
                response = await self._client.get(
                    "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                    headers=headers,
                    params={
                        "maxResults": min(500, limit - len(result)),
                        "q": self._query,
                        "labelIds": "INBOX",
                        **({"pageToken": cursor} if cursor else {}),
                    },
                )
                response.raise_for_status()
                body = response.json()
                result.extend(
                    str(item["id"])
                    for item in body.get("messages", [])
                    if isinstance(item, dict) and item.get("id")
                )
                cursor = str(body.get("nextPageToken", "")) or None
                if not cursor:
                    return result[:limit]
                if len(result) >= limit:
                    raise MailboxError(
                        "Gmail recovery limit reached; increase the configured scan limit"
                    )
                if cursor in seen_cursors:
                    raise MailboxError("Gmail pagination repeated a cursor")
                seen_cursors.add(cursor)
            return result[:limit]
        except MailboxError:
            raise
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise MailboxError("Gmail message listing failed") from exc

    async def history(
        self, access_token: str, start_history_id: str
    ) -> tuple[list[str], str]:
        """Return inbox messages added after a Gmail history cursor and the new cursor."""
        headers = {"Authorization": f"Bearer {access_token}"}
        result: list[str] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        history_id = start_history_id
        try:
            while True:
                response = await self._client.get(
                    "https://gmail.googleapis.com/gmail/v1/users/me/history",
                    headers=headers,
                    params={
                        "startHistoryId": start_history_id,
                        "historyTypes": "messageAdded",
                        "labelId": "INBOX",
                        "maxResults": 500,
                        **({"pageToken": cursor} if cursor else {}),
                    },
                )
                if response.status_code == 404:
                    raise GmailHistoryExpired("Gmail history cursor expired")
                response.raise_for_status()
                body = response.json()
                for change in body.get("history", []):
                    if not isinstance(change, dict):
                        continue
                    for added in change.get("messagesAdded", []):
                        message = added.get("message", {}) if isinstance(added, dict) else {}
                        if isinstance(message, dict) and message.get("id"):
                            result.append(str(message["id"]))
                history_id = str(body.get("historyId", history_id))
                cursor = str(body.get("nextPageToken", "")) or None
                if not cursor:
                    return list(dict.fromkeys(result)), history_id
                if cursor in seen_cursors:
                    raise MailboxError("Gmail history pagination repeated a cursor")
                seen_cursors.add(cursor)
        except MailboxError:
            raise
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise MailboxError("Gmail history request failed") from exc

    async def _page(
        self, access_token: str, limit: int, cursor: str | None
    ) -> tuple[list[EmailForAnalysis], str | None, int]:
        headers = {"Authorization": f"Bearer {access_token}"}
        try:
            listed = await self._client.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                headers=headers,
                params={
                    "maxResults": limit,
                    "q": self._query,
                    **({"pageToken": cursor} if cursor else {}),
                },
            )
            listed.raise_for_status()
            references = listed.json().get("messages", [])[:limit]
            result: list[EmailForAnalysis] = []
            for reference in references:
                email = await self.message(access_token, str(reference["id"]))
                if email:
                    result.append(email)
            return result, listed.json().get("nextPageToken"), len(references)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise MailboxError("Gmail API request failed") from exc


class OutlookReader(PagedReader):
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def _page(
        self, access_token: str, limit: int, cursor: str | None
    ) -> tuple[list[EmailForAnalysis], str | None, int]:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Prefer": 'outlook.body-content-type="text"',
        }
        try:
            response = await self._client.get(
                cursor or "https://graph.microsoft.com/v1.0/me/messages",
                headers=headers,
                params=None
                if cursor
                else {
                    "$top": limit,
                    "$orderby": "receivedDateTime desc",
                    "$select": "id,conversationId,subject,from,receivedDateTime,body",
                },
            )
            response.raise_for_status()
            result: list[EmailForAnalysis] = []
            for payload in response.json().get("value", [])[:limit]:
                body_value = str(payload.get("body", {}).get("content", ""))
                if str(payload.get("body", {}).get("contentType", "")).casefold() == "html":
                    body_value = _plain_html(body_value)
                body_value = body_value.strip()[:40_000]
                sender = payload.get("from", {}).get("emailAddress", {})
                sender_text = str(sender.get("address", ""))
                if sender.get("name"):
                    sender_text = f"{sender['name']} <{sender_text}>"
                if body_value:
                    result.append(
                        EmailForAnalysis(
                            message_id=str(payload["id"]),
                            thread_id=str(payload.get("conversationId", "")) or None,
                            subject=str(payload.get("subject", ""))[:500],
                            sender=sender_text[:500],
                            sent_at=str(payload.get("receivedDateTime", "")) or None,
                            body_text=body_value,
                        )
                    )
            next_link = response.json().get("@odata.nextLink")
            if next_link and not next_link.startswith(
                "https://graph.microsoft.com/v1.0/me/messages?"
            ):
                raise MailboxError("Unexpected Outlook pagination URL")
            return result, next_link, len(response.json().get("value", []))
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise MailboxError("Microsoft Graph request failed") from exc


def readers(client: httpx.AsyncClient, gmail_query: str) -> dict[Provider, MailboxReader]:
    return {"gmail": GmailReader(client, gmail_query), "outlook": OutlookReader(client)}
