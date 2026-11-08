import base64

import httpx
import pytest
import respx

from tapy.config import Settings
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
        messages = await GmailReader(client, Settings().gmail_query).messages("access", 5)
    assert listed.calls[0].request.url.params["q"] == "newer_than:3m"
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


@pytest.mark.asyncio
@respx.mock
async def test_gmail_paginates_instead_of_repeating_newest_page() -> None:
    from tapy.mailboxes import GmailReader

    listing = respx.get("https://gmail.googleapis.com/gmail/v1/users/me/messages").mock(
        side_effect=[
            httpx.Response(200, json={"messages": [{"id": "first"}], "nextPageToken": "page2"}),
            httpx.Response(200, json={"messages": [{"id": "second"}]}),
        ]
    )
    for message_id in ("first", "second"):
        respx.get(f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": message_id,
                    "payload": {
                        "mimeType": "text/plain",
                        "body": {"data": "aGVsbG8="},
                        "headers": [],
                    },
                },
            )
        )
    async with httpx.AsyncClient() as client:
        messages = await GmailReader(client, "").messages("inert", 200)
    assert [m.message_id for m in messages] == ["first", "second"]
    assert listing.calls[1].request.url.params["pageToken"] == "page2"


@pytest.mark.asyncio
@respx.mock
async def test_gmail_history_returns_unique_added_inbox_message_ids() -> None:
    listing = respx.get("https://gmail.googleapis.com/gmail/v1/users/me/history").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "history": [
                        {
                            "id": "101",
                            "messagesAdded": [
                                {"message": {"id": "gmail-1", "threadId": "thread-1"}}
                            ],
                        }
                    ],
                    "nextPageToken": "page2",
                    "historyId": "101",
                },
            ),
            httpx.Response(
                200,
                json={
                    "history": [
                        {
                            "id": "102",
                            "messagesAdded": [
                                {"message": {"id": "gmail-1"}},
                                {"message": {"id": "gmail-2"}},
                            ],
                        }
                    ],
                    "historyId": "103",
                },
            ),
        ]
    )
    async with httpx.AsyncClient() as client:
        message_ids, history_id = await GmailReader(client, "").history("access", "100")
    assert message_ids == ["gmail-1", "gmail-2"]
    assert history_id == "103"
    assert listing.calls[0].request.url.params["startHistoryId"] == "100"
    assert listing.calls[0].request.url.params["historyTypes"] == "messageAdded"
    assert listing.calls[0].request.url.params["labelId"] == "INBOX"
    assert listing.calls[1].request.url.params["pageToken"] == "page2"
