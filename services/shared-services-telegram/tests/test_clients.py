from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from pydantic import SecretStr

from shared_services_telegram.clients import TelegramClient
from shared_services_telegram.config import Settings


def test_long_poll_request_outlives_telegram_poll_timeout() -> None:
    settings = Settings(
        telegram_token=SecretStr("test"),
        job_assistant_api_token=SecretStr("update"),
        job_assistant_notification_token=SecretStr("notify"),
        poll_timeout_seconds=45,
        request_timeout_seconds=10,
    )
    client = TelegramClient(settings)
    call = AsyncMock(return_value={"value": []})
    client.call = call  # type: ignore[method-assign]

    try:
        assert asyncio.run(client.get_updates(offset=7, poll_timeout=45)) == []
        call.assert_awaited_once_with(
            "getUpdates",
            {
                "offset": 7,
                "timeout": 45,
                "allowed_updates": ["message", "callback_query"],
            },
            timeout_seconds=55,
        )
    finally:
        asyncio.run(client.close())
