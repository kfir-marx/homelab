from __future__ import annotations

import base64
import re
from collections.abc import Mapping
from html import unescape
from typing import Any, Protocol

import httpx

from .models import EmailForAnalysis, Provider


class MailboxError(RuntimeError):
    pass


class MailboxReader(Protocol):
    async def messages(self, access_token: str, limit: int) -> list[EmailForAnalysis]: ...


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


class GmailReader:
    def __init__(self, client: httpx.AsyncClient, query: str) -> None:
        self._client = client
        self._query = query

    async def messages(self, access_token: str, limit: int) -> list[EmailForAnalysis]:
        headers = {"Authorization": f"Bearer {access_token}"}
        try:
            listed = await self._client.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                headers=headers,
                params={"maxResults": limit, "q": self._query},
            )
            listed.raise_for_status()
            references = listed.json().get("messages", [])[:limit]
            result: list[EmailForAnalysis] = []
            for reference in references:
                message_id = reference["id"]
                response = await self._client.get(
                    f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
                    headers=headers,
                    params={"format": "full"},
                )
                response.raise_for_status()
                payload = response.json()
                metadata = {
                    str(item.get("name", "")).casefold(): str(item.get("value", ""))
                    for item in payload.get("payload", {}).get("headers", [])
                }
                body = _gmail_body(payload.get("payload", {})).strip()[:40_000]
                if body:
                    result.append(
                        EmailForAnalysis(
                            message_id=str(payload["id"]),
                            thread_id=str(payload.get("threadId", "")) or None,
                            subject=metadata.get("subject", "")[:500],
                            sender=metadata.get("from", "")[:500],
                            sent_at=metadata.get("date") or None,
                            body_text=body,
                        )
                    )
            return result
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise MailboxError("Gmail API request failed") from exc


class OutlookReader:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def messages(self, access_token: str, limit: int) -> list[EmailForAnalysis]:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Prefer": 'outlook.body-content-type="text"',
        }
        try:
            response = await self._client.get(
                "https://graph.microsoft.com/v1.0/me/messages",
                headers=headers,
                params={
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
            return result
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise MailboxError("Microsoft Graph request failed") from exc


def readers(client: httpx.AsyncClient, gmail_query: str) -> dict[Provider, MailboxReader]:
    return {"gmail": GmailReader(client, gmail_query), "outlook": OutlookReader(client)}
