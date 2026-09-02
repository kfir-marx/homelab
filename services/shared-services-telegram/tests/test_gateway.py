from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import SecretStr

from shared_services_telegram.clients import DefiniteTelegramError, UncertainTelegramError
from shared_services_telegram.config import Settings
from shared_services_telegram.gateway import Gateway


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.answered: list[str] = []
        self.downloads = 0
        self.uncertain = False
        self.definite = False

    async def answer_callback(self, callback_id: str) -> None:
        self.answered.append(callback_id)

    async def send_message(self, chat_id: int, text: str, buttons: list[Any]) -> None:
        if self.uncertain:
            raise UncertainTelegramError("timeout")
        if self.definite:
            raise DefiniteTelegramError("rejected")
        self.sent.append((chat_id, text))

    async def download(self, file_id: str, maximum: int) -> tuple[bytes, str]:
        self.downloads += 1
        return b"%PDF-valid", "documents/cv.pdf"


class FakeBackend:
    def __init__(self) -> None:
        self.authorized = True
        self.is_pending = False
        self.updates: list[dict[str, Any]] = []
        self.documents = 0
        self.events: list[dict[str, Any]] = []
        self.outcomes: list[tuple[str, str]] = []

    async def authorize(self, user_id: int, chat_id: int) -> bool:
        return self.authorized

    async def pending(self, user_id: int, chat_id: int) -> bool:
        return self.is_pending

    async def update(self, update: dict[str, Any]) -> list[dict[str, Any]]:
        self.updates.append(update)
        return [{"chat_id": 123, "text": "ok", "buttons": []}]

    async def document(
        self, update: dict[str, Any], content: bytes, filename: str, mime_type: str
    ) -> list[dict[str, Any]]:
        self.documents += 1
        return []

    async def notifications(self) -> list[dict[str, Any]]:
        return self.events

    async def notification_outcome(self, event_id: str, outcome: str) -> None:
        self.outcomes.append((event_id, outcome))


def settings() -> Settings:
    return Settings(
        telegram_token=SecretStr("test"),
        job_assistant_api_token=SecretStr("update"),
        job_assistant_notification_token=SecretStr("notify"),
    )


def message(user_id: int = 123, chat_id: int = 123, chat_type: str = "private") -> dict[str, Any]:
    return {
        "update_id": 1,
        "message": {
            "from": {"id": user_id},
            "chat": {"id": chat_id, "type": chat_type},
            "text": "/job_help",
        },
    }


@pytest.mark.parametrize(
    "update",
    [
        message(chat_id=-1, chat_type="group"),
        message(chat_id=-1, chat_type="channel"),
        message(chat_id=456),
        {"update_id": 1},
    ],
)
def test_non_private_and_impersonated_updates_are_silent(update: dict[str, Any]) -> None:
    telegram, backend = FakeTelegram(), FakeBackend()
    asyncio.run(Gateway(settings(), telegram, backend).process(update))  # type: ignore[arg-type]
    assert telegram.sent == []
    assert backend.updates == []


@pytest.mark.parametrize("marker", ["forward_origin", "forward_from", "sender_chat"])
def test_forwarded_or_sender_chat_identity_is_silent(marker: str) -> None:
    update = message()
    update["message"][marker] = {"id": 456}
    telegram, backend = FakeTelegram(), FakeBackend()
    asyncio.run(Gateway(settings(), telegram, backend).process(update))  # type: ignore[arg-type]
    assert telegram.sent == []
    assert backend.updates == []


def test_unknown_or_revoked_user_is_silent() -> None:
    telegram, backend = FakeTelegram(), FakeBackend()
    backend.authorized = False
    asyncio.run(Gateway(settings(), telegram, backend).process(message()))  # type: ignore[arg-type]
    assert telegram.sent == []
    assert backend.updates == []


def test_callback_is_acknowledged_and_typed_route_is_forwarded() -> None:
    telegram, backend = FakeTelegram(), FakeBackend()
    update = {
        "update_id": 2,
        "callback_query": {
            "id": "callback-1",
            "from": {"id": 123},
            "data": "apply:11111111-1111-1111-1111-111111111111",
            "message": {"chat": {"id": 123, "type": "private"}},
        },
    }
    asyncio.run(Gateway(settings(), telegram, backend).process(update))  # type: ignore[arg-type]
    assert telegram.answered == ["callback-1"]
    assert backend.updates == [update]


def test_invalid_callback_is_acknowledged_but_not_forwarded() -> None:
    telegram, backend = FakeTelegram(), FakeBackend()
    update = {
        "update_id": 2,
        "callback_query": {
            "id": "callback-invalid",
            "from": {"id": 123},
            "data": "arbitrary-service:root",
            "message": {"chat": {"id": 123, "type": "private"}},
        },
    }
    asyncio.run(Gateway(settings(), telegram, backend).process(update))  # type: ignore[arg-type]
    assert telegram.answered == ["callback-invalid"]
    assert backend.updates == []


def test_document_is_downloaded_only_during_expected_upload() -> None:
    telegram, backend = FakeTelegram(), FakeBackend()
    update = message()
    update["message"].pop("text")
    update["message"]["document"] = {
        "file_id": "opaque",
        "file_name": "cv.pdf",
        "mime_type": "application/pdf",
        "file_size": 100,
    }
    gateway = Gateway(settings(), telegram, backend)  # type: ignore[arg-type]
    asyncio.run(gateway.process(update))
    assert telegram.downloads == 0
    backend.is_pending = True
    update["update_id"] = 2
    asyncio.run(gateway.process(update))
    assert telegram.downloads == 1
    assert backend.documents == 1


@pytest.mark.parametrize(
    ("mime_type", "file_size"),
    [
        ("image/jpeg", 100),
        ("application/pdf", 10_000_001),
        ("application/pdf", 0),
    ],
)
def test_invalid_or_oversized_document_is_rejected_before_download(
    mime_type: str, file_size: int
) -> None:
    telegram, backend = FakeTelegram(), FakeBackend()
    backend.is_pending = True
    update = message()
    update["message"].pop("text")
    update["message"]["document"] = {
        "file_id": "opaque",
        "file_name": "cv.pdf",
        "mime_type": mime_type,
        "file_size": file_size,
    }
    asyncio.run(Gateway(settings(), telegram, backend).process(update))  # type: ignore[arg-type]
    assert telegram.downloads == 0
    assert backend.documents == 0
    assert telegram.sent == [(123, "Upload a PDF or DOCX document no larger than 10 MB.")]


def test_uncertain_notification_is_suppressed_not_acked() -> None:
    telegram, backend = FakeTelegram(), FakeBackend()
    telegram.uncertain = True
    backend.events = [{"id": "event", "chat_id": 123, "text": "ready", "buttons": []}]
    asyncio.run(Gateway(settings(), telegram, backend).deliver_notifications())  # type: ignore[arg-type]
    assert backend.outcomes == [("event", "uncertain")]


@pytest.mark.parametrize(
    ("definite", "expected"),
    [(False, "ack"), (True, "retry")],
)
def test_notification_delivery_reports_persistent_outcome(definite: bool, expected: str) -> None:
    telegram, backend = FakeTelegram(), FakeBackend()
    telegram.definite = definite
    backend.events = [{"id": "event", "chat_id": 123, "text": "ready", "buttons": []}]
    asyncio.run(Gateway(settings(), telegram, backend).deliver_notifications())  # type: ignore[arg-type]
    assert backend.outcomes == [("event", expected)]
