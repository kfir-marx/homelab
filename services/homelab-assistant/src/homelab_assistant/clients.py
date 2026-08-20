from __future__ import annotations

import json
from typing import Any

import httpx


class TelegramClient:
    def __init__(self, token: str, timeout: float = 40.0) -> None:
        self._client = httpx.Client(
            base_url=f"https://api.telegram.org/bot{token}", timeout=timeout
        )

    def get_updates(self, offset: int | None) -> list[dict[str, Any]]:
        response = self._client.get(
            "/getUpdates",
            params={
                "offset": offset,
                "timeout": 30,
                "allowed_updates": json.dumps(["message"]),
            },
        )
        response.raise_for_status()
        result = response.json().get("result", [])
        return list(result) if isinstance(result, list) else []

    def send_message(self, chat_id: int, text: str) -> None:
        for chunk in _telegram_chunks(text):
            response = self._client.post(
                "/sendMessage",
                json={"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True},
            )
            response.raise_for_status()


class LlmClient:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float) -> None:
        self.model = model
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    def complete(self, messages: list[dict[str, str]], max_tokens: int) -> str:
        response = self._client.post(
            "/chat/completions",
            json={
                "model": self.model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.4,
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
        response.raise_for_status()
        payload = response.json()
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("LLM returned an invalid chat-completion response") from exc
        if not isinstance(content, str) or not content.strip():
            raise ValueError("LLM returned an empty response")
        return content.strip()

    def ready(self) -> bool:
        try:
            response = self._client.get("/models", timeout=10.0)
            response.raise_for_status()
            return True
        except httpx.HTTPError:
            return False


def _telegram_chunks(text: str, maximum: int = 4000) -> list[str]:
    if len(text) <= maximum:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        split_at = remaining.rfind("\n", 0, maximum + 1)
        if split_at < maximum // 2:
            split_at = maximum
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    return chunks
