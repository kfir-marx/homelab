from __future__ import annotations

import logging

import pytest
from pydantic import SecretStr

from homelab_assistant import main
from homelab_assistant.config import Settings
from homelab_assistant.main import (
    TelegramIdentityConfigurationError,
    validate_telegram_identity,
)


def test_main_suppresses_token_bearing_httpx_request_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(logging, "basicConfig", lambda **_kwargs: None)
    httpx_logger = logging.getLogger("httpx")
    original_level = httpx_logger.level
    httpx_logger.setLevel(logging.NOTSET)
    try:
        main.configure_logging()
        assert not httpx_logger.isEnabledFor(logging.INFO)
    finally:
        httpx_logger.setLevel(original_level)


def test_bot_account_cannot_be_configured_as_allowed_private_identity() -> None:
    configured = Settings(
        telegram_token=SecretStr("unit-test-placeholder"),
        telegram_allowed_user_id=123,
        telegram_allowed_chat_id=123,
    )

    with pytest.raises(TelegramIdentityConfigurationError):
        validate_telegram_identity(configured, 123)

    validate_telegram_identity(configured, 999)
