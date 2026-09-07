import base64

import httpx
import pytest
import respx

from tapy.mailboxes import GmailReader, OutlookReader


def encoded(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


@pytest.mark.asyncio
@respx.mock
async def test_gmail_reader_lists_then_fetches_bounded_body() -> None:
    listed = respx.get("https://gmail.googleapis.com/gmail/v1/users/me/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "gmail-1"}]})
    )
    respx.get("https://gmail.googleapis.com/gmail/v1/users/me/messages/gmail-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "gmail-1",
                "threadId": "thread-1",
                "payload": {
                    "mimeType": "text/plain",
                    "headers": [
                        {"name": "Subject", "value": "Confirmed"},
                        {"name": "From", "value": "hotel@example.com"},
                    ],
                    "body": {"data": encoded("Hotel confirmation body")},
                },
            },
        )
    )
    async with httpx.AsyncClient() as client:
        messages = await GmailReader(client, "newer_than:30d").messages("access", 5)
    assert listed.calls[0].request.url.params["q"] == "newer_than:30d"
    assert messages[0].body_text == "Hotel confirmation body"
    assert messages[0].subject == "Confirmed"


@pytest.mark.asyncio
@respx.mock
async def test_outlook_reader_uses_delegated_me_messages() -> None:
    route = respx.get("https://graph.microsoft.com/v1.0/me/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "outlook-1",
                        "conversationId": "conversation-1",
                        "subject": "Reservation",
                        "from": {
                            "emailAddress": {
                                "name": "Hotel",
                                "address": "hotel@example.com",
                            }
                        },
                        "receivedDateTime": "2026-09-01T10:00:00Z",
                        "body": {"contentType": "html", "content": "<b>Confirmed</b> stay"},
                    }
                ]
            },
        )
    )
    async with httpx.AsyncClient() as client:
        messages = await OutlookReader(client).messages("access", 10)
    assert route.calls[0].request.headers["prefer"] == 'outlook.body-content-type="text"'
    assert messages[0].body_text == "Confirmed stay"
    assert messages[0].sender == "Hotel <hotel@example.com>"
