from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from .tools import AssistantTools, HandoffRequest


@dataclass(frozen=True)
class Completion:
    content: str
    prompt_tokens: int
    completion_tokens: int
    handoff: HandoffRequest | None = None


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
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float,
        tools: AssistantTools | None = None,
        maximum_tool_rounds: int = 6,
        maximum_tool_context_chars: int = 6_000,
    ) -> None:
        self.model = model
        self.tools = tools
        self.maximum_tool_rounds = maximum_tool_rounds
        self.maximum_tool_context_chars = maximum_tool_context_chars
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    def complete(self, messages: list[dict[str, str]], max_tokens: int) -> Completion:
        return self._request_completion(messages, max_tokens)

    def complete_with_tools(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        current_user_text: str,
    ) -> Completion:
        if not self.tools:
            return self.complete(messages, max_tokens)
        working: list[dict[str, Any]] = [dict(item) for item in messages]
        handoff: HandoffRequest | None = None
        completion_tokens = 0
        tool_context_chars = 0
        for _ in range(self.maximum_tool_rounds):
            payload = self._payload(working, max_tokens)
            payload["tools"] = self.tools.definitions
            payload["tool_choice"] = "auto"
            response = self._client.post("/chat/completions", json=payload)
            response.raise_for_status()
            message, usage = self._response_message(response)
            completion_tokens += int(usage.get("completion_tokens", 0))
            calls = message.get("tool_calls")
            if not isinstance(calls, list) or not calls:
                content = message.get("content")
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("LLM returned an empty response")
                return Completion(
                    content.strip(),
                    int(usage.get("prompt_tokens", 0)),
                    completion_tokens,
                    handoff,
                )
            working.append(
                {
                    "role": "assistant",
                    "content": message.get("content"),
                    "tool_calls": calls,
                }
            )
            for call in calls:
                if not isinstance(call, dict):
                    raise ValueError("LLM returned an invalid tool call")
                function = call.get("function")
                if not isinstance(function, dict):
                    raise ValueError("LLM returned an invalid tool function")
                try:
                    arguments = json.loads(str(function.get("arguments", "{}")))
                except json.JSONDecodeError as exc:
                    raise ValueError("LLM returned invalid tool arguments") from exc
                if not isinstance(arguments, dict):
                    raise ValueError("LLM tool arguments must be an object")
                result = self.tools.execute(
                    str(function.get("name", "")), arguments, current_user_text
                )
                if result.handoff:
                    handoff = result.handoff
                available = max(0, self.maximum_tool_context_chars - tool_context_chars)
                result_content = result.content[:available]
                if len(result.content) > available:
                    result_content += "\n[aggregate tool context budget exhausted]"
                tool_context_chars += len(result_content)
                working.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(call.get("id", "")),
                        "name": str(function.get("name", "")),
                        "content": result_content,
                    }
                )
        raise ValueError("LLM exceeded the tool-call round limit")

    def _request_completion(self, messages: list[dict[str, str]], max_tokens: int) -> Completion:
        response = self._client.post(
            "/chat/completions", json=self._payload([dict(item) for item in messages], max_tokens)
        )
        response.raise_for_status()
        message, usage = self._response_message(response)
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("LLM returned an empty response")
        return Completion(
            content.strip(),
            int(usage.get("prompt_tokens", 0)),
            int(usage.get("completion_tokens", 0)),
        )

    def _payload(self, messages: list[dict[str, Any]], max_tokens: int) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.4,
            "chat_template_kwargs": {"enable_thinking": False},
        }

    @staticmethod
    def _response_message(response: httpx.Response) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = response.json()
        try:
            message = payload["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("LLM returned an invalid chat-completion response") from exc
        if not isinstance(message, dict):
            raise ValueError("LLM returned an invalid chat-completion message")
        usage = payload.get("usage", {})
        return message, usage if isinstance(usage, dict) else {}

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
