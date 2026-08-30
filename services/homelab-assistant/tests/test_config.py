from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from homelab_assistant.config import Settings


def test_settings_require_exact_numeric_user_and_chat_ids() -> None:
    configured = Settings(
        telegram_token=SecretStr("placeholder"),
        telegram_allowed_user_id=123,
        telegram_allowed_chat_id=456,
    )
    assert configured.telegram_allowed_user_id == 123
    assert configured.telegram_allowed_chat_id == 456

    with pytest.raises(ValidationError):
        Settings(
            telegram_token=SecretStr("placeholder"),
            telegram_allowed_user_id=0,
            telegram_allowed_chat_id=456,
        )


def test_switch_node_is_literal_gpu_2() -> None:
    with pytest.raises(ValidationError):
        Settings(
            telegram_token=SecretStr("placeholder"),
            telegram_allowed_user_id=123,
            telegram_allowed_chat_id=456,
            kubernetes_node_name="another-node",
        )
