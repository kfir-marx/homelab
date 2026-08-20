from __future__ import annotations

from collections import defaultdict
from typing import Any

from .clients import LlmClient
from .config import Settings

HELP = (
    "Send a message to chat with the private homelab LLM.\n\n"
    "/new — clear this chat's in-memory context\n"
    "/status — check whether the model endpoint is ready\n"
    "/help — show this message\n\n"
    "This bot cannot run commands or change the cluster."
)


class AssistantBot:
    def __init__(self, settings: Settings, llm: LlmClient) -> None:
        self.settings = settings
        self.llm = llm
        self._history: dict[int, list[dict[str, str]]] = defaultdict(list)

    def process(self, update: dict[str, Any]) -> tuple[int, str] | None:
        message = update.get("message")
        if not isinstance(message, dict):
            return None
        sender = message.get("from")
        chat = message.get("chat")
        if not isinstance(sender, dict) or not isinstance(chat, dict):
            return None
        user_id = sender.get("id")
        chat_id = chat.get("id")
        if not isinstance(user_id, int) or user_id not in self.settings.telegram_allowed_user_ids:
            return None
        # Refuse groups even if the owner is a member: other participants could
        # inject prompts or read replies from the private assistant.
        if chat.get("type") != "private" or chat_id != user_id:
            return None
        text = message.get("text")
        if not isinstance(text, str) or not text.strip():
            return chat_id, "Text messages only. Use /help for available commands."
        text = text.strip()
        command = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
        if command in {"/start", "/help"}:
            return chat_id, HELP
        if command == "/new":
            self._history.pop(chat_id, None)
            return chat_id, "Context cleared."
        if command == "/status":
            state = "ready" if self.llm.ready() else "unavailable or still loading"
            return chat_id, f"Model: {self.settings.llm_model}\nStatus: {state}"
        if command.startswith("/"):
            return chat_id, "Unknown command. Use /help."
        if len(text) > self.settings.max_input_chars:
            return (
                chat_id,
                f"Message is too long (maximum {self.settings.max_input_chars} characters).",
            )

        messages = [
            {"role": "system", "content": self.settings.system_prompt},
            *self._history[chat_id],
            {"role": "user", "content": text},
        ]
        answer = self.llm.complete(messages, self.settings.max_output_tokens)
        history = self._history[chat_id]
        history.extend(
            [
                {"role": "user", "content": text},
                {"role": "assistant", "content": answer},
            ]
        )
        if len(history) > self.settings.max_history_messages:
            del history[: len(history) - self.settings.max_history_messages]
        return chat_id, answer
