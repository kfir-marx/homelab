from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import SecretStr

from homelab_assistant import main
from homelab_assistant.bot import AssistantBot, CodexClient
from homelab_assistant.bridge_state import BridgeState
from homelab_assistant.config import Settings
from homelab_assistant.main import (
    TelegramIdentityConfigurationError,
    handle_update,
    validate_telegram_identity,
)
from homelab_assistant.switching import SwitchCoordinator, SwitchResult


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


class RecordingTelegram:
    def __init__(self, *, fail_acknowledgement: bool = False, fail_delivery: bool = False) -> None:
        self.fail_acknowledgement = fail_acknowledgement
        self.fail_delivery = fail_delivery
        self.acknowledgements: list[str] = []
        self.deliveries: list[str] = []

    def answer_callback(self, callback_query_id: str) -> None:
        self.acknowledgements.append(callback_query_id)
        if self.fail_acknowledgement:
            raise RuntimeError("simulated expired callback")

    def send_message(
        self, chat_id: int, text: str, buttons: tuple[tuple[str, str], ...] = ()
    ) -> int | None:
        del chat_id, buttons
        self.deliveries.append(text)
        if self.fail_delivery:
            raise RuntimeError("simulated delivery failure")
        return 1

    def edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        del chat_id, message_id, text


class SuccessfulSwitcher:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def switch(self, mode: str) -> SwitchResult:
        self.calls.append(mode)
        return SwitchResult(True, f"completed {mode}")


def _confirmed_gaming_update(
    tmp_path: Path,
) -> tuple[AssistantBot, dict[str, Any], SuccessfulSwitcher]:
    configured = Settings(
        telegram_token=SecretStr("unit-test-placeholder"),
        telegram_allowed_user_id=123,
        telegram_allowed_chat_id=456,
        state_database_path=str(tmp_path / "bridge.db"),
    )
    state = BridgeState(configured.state_database_path)
    switcher = SuccessfulSwitcher()
    bot = AssistantBot(
        configured,
        cast(CodexClient, object()),
        state,
        cast(SwitchCoordinator, switcher),
    )
    bot.lease.unlock(123)
    nonce = state.issue_callback(123, 456, "ops-gaming", {}, 120)
    update: dict[str, Any] = {
        "callback_query": {
            "id": "callback-1",
            "from": {"id": 123},
            "data": f"ops:{nonce}",
            "message": {"chat": {"id": 456, "type": "private"}},
        }
    }
    return bot, update, switcher


def test_callback_acknowledgement_failure_does_not_reclassify_completed_switch(
    tmp_path: Path,
) -> None:
    bot, update, switcher = _confirmed_gaming_update(tmp_path)
    telegram = RecordingTelegram(fail_acknowledgement=True)

    handle_update(cast(Any, telegram), bot, update, 456)

    assert telegram.acknowledgements == ["callback-1"]
    assert telegram.deliveries == ["completed gaming"]
    assert switcher.calls == ["gaming"]


def test_final_delivery_failure_is_not_retried_or_replaced_with_generic_error(
    tmp_path: Path,
) -> None:
    bot, update, switcher = _confirmed_gaming_update(tmp_path)
    telegram = RecordingTelegram(fail_delivery=True)

    handle_update(cast(Any, telegram), bot, update, 456)

    assert telegram.acknowledgements == ["callback-1"]
    assert telegram.deliveries == ["completed gaming"]
    assert switcher.calls == ["gaming"]
