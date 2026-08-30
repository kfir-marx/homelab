from __future__ import annotations

import httpx

from homelab_assistant.clients import TelegramClient


def test_get_me_returns_only_numeric_bot_identity() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/getMe")
        return httpx.Response(200, json={"ok": True, "result": {"id": 987, "is_bot": True}})

    telegram = TelegramClient("unit-test-placeholder")
    telegram._client = httpx.Client(
        base_url="https://api.telegram.org", transport=httpx.MockTransport(respond)
    )

    assert telegram.get_me_id() == 987
