from __future__ import annotations

from typing import cast

from pydantic import SecretStr

from homelab_assistant.bot import AssistantBot
from homelab_assistant.clients import LlmClient, _telegram_chunks
from homelab_assistant.config import Settings


class FakeLlm:
    def __init__(self) -> None:
        self.requests: list[list[dict[str, str]]] = []

    def complete(self, messages: list[dict[str, str]], max_tokens: int) -> str:
        self.requests.append(messages)
        return f"answer-{len(self.requests)}"

    def ready(self) -> bool:
        return True


def settings() -> Settings:
    return Settings(
        telegram_token=SecretStr("unit-test-placeholder"),  # noqa: S106
        telegram_allowed_user_ids=frozenset({123}),
        llm_api_key=SecretStr("unit-test-placeholder"),
        max_history_messages=2,
    )


def update(user_id: int, text: str, chat_type: str = "private") -> dict[str, object]:
    return {
        "update_id": 1,
        "message": {
            "from": {"id": user_id},
            "chat": {"id": user_id, "type": chat_type},
            "text": text,
        },
    }


def test_unauthorized_user_is_silently_ignored() -> None:
    fake = FakeLlm()
    bot = AssistantBot(settings(), cast(LlmClient, fake))
    assert bot.process(update(999, "hello")) is None
    assert fake.requests == []


def test_group_message_is_silently_ignored() -> None:
    fake = FakeLlm()
    bot = AssistantBot(settings(), cast(LlmClient, fake))
    assert bot.process(update(123, "hello", "group")) is None


def test_history_is_bounded_and_can_be_cleared() -> None:
    fake = FakeLlm()
    bot = AssistantBot(settings(), cast(LlmClient, fake))
    assert bot.process(update(123, "first")) == (123, "answer-1")
    assert bot.process(update(123, "second")) == (123, "answer-2")
    assert [item["content"] for item in fake.requests[1]] == [
        settings().system_prompt,
        "first",
        "answer-1",
        "second",
    ]
    assert bot.process(update(123, "/new")) == (123, "Context cleared.")
    assert bot.process(update(123, "third")) == (123, "answer-3")
    assert [item["content"] for item in fake.requests[2]] == [settings().system_prompt, "third"]


def test_long_replies_are_split_within_telegram_limit() -> None:
    chunks = _telegram_chunks("word " * 2000)
    assert len(chunks) > 1
    assert all(len(chunk) <= 4000 for chunk in chunks)
