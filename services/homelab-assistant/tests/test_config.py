from __future__ import annotations

import pytest

from homelab_assistant.config import Settings


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("6638039140", frozenset({6638039140})),
        ("6638039140,123", frozenset({6638039140, 123})),
        ("[6638039140, 123]", frozenset({6638039140, 123})),
    ],
)
def test_telegram_user_ids_are_parsed_from_environment(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: frozenset[int]
) -> None:
    monkeypatch.setenv("HOMELAB_ASSISTANT_TELEGRAM_TOKEN", "unit-test-placeholder")
    monkeypatch.setenv("HOMELAB_ASSISTANT_LLM_API_KEY", "unit-test-placeholder")
    monkeypatch.setenv("HOMELAB_ASSISTANT_TELEGRAM_ALLOWED_USER_IDS", value)

    assert Settings().telegram_allowed_user_ids == expected  # type: ignore[call-arg]
