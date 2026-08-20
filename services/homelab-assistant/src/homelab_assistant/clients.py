from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class Completion:
    content: str
    prompt_tokens: int
    completion_tokens: int


class TelegramClient:
    def __init__(self, token: str, timeout: float = 40.0) -> None:
        self._token = token
        self._client = httpx.Client(
            base_url=f"https://api.telegram.org/bot{token}", timeout=timeout
        )

    def get_updates(self, offset: int | None) -> list[dict[str, Any]]:
        response = self._client.get(
            "/getUpdates",
            params={
                "offset": offset,
                "timeout": 30,
                "allowed_updates": json.dumps(["message", "callback_query"]),
            },
        )
        response.raise_for_status()
        result = response.json().get("result", [])
        return list(result) if isinstance(result, list) else []

    def send_message(
        self, chat_id: int, text: str, buttons: tuple[tuple[str, str], ...] = ()
    ) -> None:
        chunks = _telegram_chunks(text)
        for index, chunk in enumerate(chunks):
            body: dict[str, Any] = {
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            }
            if buttons and index == len(chunks) - 1:
                body["reply_markup"] = {
                    "inline_keyboard": [
                        [{"text": label, "callback_data": data}] for label, data in buttons
                    ]
                }
            response = self._client.post(
                "/sendMessage",
                json=body,
            )
            response.raise_for_status()

    def download_document(self, file_id: str, maximum: int) -> bytes:
        metadata = self._client.get("/getFile", params={"file_id": file_id})
        metadata.raise_for_status()
        file_path = str(metadata.json()["result"]["file_path"])
        content = bytearray()
        with httpx.stream(
            "GET",
            f"https://api.telegram.org/file/bot{self._token}/{file_path}",
            timeout=self._client.timeout,
        ) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                if len(content) + len(chunk) > maximum:
                    raise ValueError("Telegram document exceeds maximum size")
                content.extend(chunk)
        return bytes(content)


class LlmClient:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float) -> None:
        self.model = model
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    def complete(self, messages: list[dict[str, str]], max_tokens: int) -> Completion:
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
        usage = payload.get("usage", {})
        return Completion(
            content.strip(),
            int(usage.get("prompt_tokens", 0)),
            int(usage.get("completion_tokens", 0)),
        )

    def ready(self) -> bool:
        try:
            response = self._client.get("/models", timeout=10.0)
            response.raise_for_status()
            return True
        except httpx.HTTPError:
            return False


class ExternalAiClient:
    def __init__(self, base_url: str, token: str, timeout: float = 20.0) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )

    def submit(
        self, prompt: str, model: str, reasoning: str, idempotency_key: str
    ) -> dict[str, Any]:
        response = self._client.post(
            "/v1/jobs",
            json={
                "requester": "homelab-assistant",
                "idempotency_key": idempotency_key,
                "prompt": prompt,
                "model": model,
                "reasoning_effort": reasoning,
                "correlation": {"source": "telegram-handover"},
            },
        )
        response.raise_for_status()
        return dict(response.json())

    def get(self, job_id: str) -> dict[str, Any]:
        response = self._client.get(f"/v1/jobs/{job_id}")
        response.raise_for_status()
        return dict(response.json())


class JobAssistantClient:
    def __init__(
        self, base_url: str, token: str, notification_token: str, timeout: float = 40.0
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        self._notification_client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {notification_token}"},
            timeout=timeout,
        )

    def has_pending(self, user_id: int, chat_id: int) -> bool:
        response = self._client.get(
            "/internal/telegram/pending", params={"user_id": user_id, "chat_id": chat_id}
        )
        response.raise_for_status()
        return bool(response.json().get("pending"))

    def route(self, update: dict[str, Any]) -> list[dict[str, Any]]:
        message = update.get("message")
        raw = message.pop("_file_bytes", None) if isinstance(message, dict) else None
        if isinstance(raw, bytes):
            response = self._client.post(
                "/internal/telegram/document",
                data={"update_json": json.dumps(update)},
                files={"content": ("telegram-upload", raw, "application/octet-stream")},
            )
        else:
            response = self._client.post("/internal/telegram/update", json=update)
        response.raise_for_status()
        result = response.json().get("replies", [])
        return list(result) if isinstance(result, list) else []

    def notifications(self) -> list[dict[str, Any]]:
        response = self._notification_client.get("/internal/telegram/notifications")
        response.raise_for_status()
        result = response.json().get("notifications", [])
        return list(result) if isinstance(result, list) else []

    def acknowledge(self, event_id: str) -> None:
        response = self._notification_client.post(
            f"/internal/telegram/notifications/{event_id}/ack"
        )
        response.raise_for_status()


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
