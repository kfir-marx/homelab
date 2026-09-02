from __future__ import annotations

import json
from typing import Any

import httpx

from .config import Settings


class DefiniteTelegramError(RuntimeError):
    pass


class UncertainTelegramError(RuntimeError):
    pass


class TelegramClient:
    def __init__(self, settings: Settings) -> None:
        token = settings.telegram_token.get_secret_value()
        self._request_timeout_seconds = settings.request_timeout_seconds
        self._api = httpx.AsyncClient(
            base_url=f"https://api.telegram.org/bot{token}",
            timeout=httpx.Timeout(settings.request_timeout_seconds),
        )
        self._files = httpx.AsyncClient(
            base_url=f"https://api.telegram.org/file/bot{token}",
            timeout=httpx.Timeout(settings.file_timeout_seconds),
        )

    async def call(
        self,
        method: str,
        payload: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        try:
            request_options: dict[str, Any] = {"json": payload}
            if timeout_seconds is not None:
                request_options["timeout"] = timeout_seconds
            response = await self._api.post(f"/{method}", **request_options)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise UncertainTelegramError(type(exc).__name__) from exc
        if response.status_code >= 500 or response.status_code == 429:
            raise DefiniteTelegramError(f"telegram_{response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise DefiniteTelegramError("telegram_invalid_response") from exc
        if not response.is_success or not body.get("ok"):
            raise DefiniteTelegramError(f"telegram_{response.status_code}")
        result = body.get("result")
        return result if isinstance(result, dict) else {"value": result}

    async def get_updates(self, offset: int, poll_timeout: int) -> list[dict[str, Any]]:
        result = await self.call(
            "getUpdates",
            {
                "offset": offset,
                "timeout": poll_timeout,
                "allowed_updates": ["message", "callback_query"],
            },
            timeout_seconds=poll_timeout + self._request_timeout_seconds,
        )
        value = result.get("value", result)
        return value if isinstance(value, list) else []

    async def prepare_long_polling(self) -> None:
        await self.call("deleteWebhook", {"drop_pending_updates": False})

    async def answer_callback(self, callback_id: str) -> None:
        await self.call("answerCallbackQuery", {"callback_query_id": callback_id})

    async def send_message(
        self, chat_id: int, text: str, buttons: list[list[str]] | list[tuple[str, str]]
    ) -> None:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text[:4096]}
        if buttons:
            keyboard = []
            for button in buttons:
                label, callback = str(button[0]), str(button[1])
                if len(callback.encode()) > 64:
                    continue
                keyboard.append([{"text": label[:64], "callback_data": callback}])
            if keyboard:
                payload["reply_markup"] = {"inline_keyboard": keyboard}
        await self.call("sendMessage", payload)

    async def send_document(
        self,
        chat_id: int,
        content: bytes,
        filename: str,
        mime_type: str,
        caption: str,
        buttons: list[list[str]] | list[tuple[str, str]],
    ) -> None:
        if mime_type not in {
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }:
            raise DefiniteTelegramError("document_type_rejected")
        keyboard = []
        for button in buttons:
            label, callback = str(button[0]), str(button[1])
            if len(callback.encode()) <= 64:
                keyboard.append([{"text": label[:64], "callback_data": callback}])
        data: dict[str, str] = {"chat_id": str(chat_id), "caption": caption[:1024]}
        if keyboard:
            data["reply_markup"] = json.dumps({"inline_keyboard": keyboard})
        try:
            response = await self._api.post(
                "/sendDocument",
                data=data,
                files={"document": (filename[:200], content, mime_type)},
                timeout=self._files.timeout,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise UncertainTelegramError(type(exc).__name__) from exc
        if response.status_code >= 500 or response.status_code == 429:
            raise DefiniteTelegramError(f"telegram_{response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise DefiniteTelegramError("telegram_invalid_response") from exc
        if not response.is_success or not body.get("ok"):
            raise DefiniteTelegramError(f"telegram_{response.status_code}")

    async def download(self, file_id: str, maximum: int) -> tuple[bytes, str]:
        metadata = await self.call("getFile", {"file_id": file_id})
        path = str(metadata.get("file_path", ""))
        size = int(metadata.get("file_size", 0) or 0)
        if not path or size > maximum:
            raise DefiniteTelegramError("file_rejected")
        try:
            async with self._files.stream("GET", f"/{path}") as response:
                if not response.is_success:
                    raise DefiniteTelegramError("file_rejected")
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > maximum:
                        raise DefiniteTelegramError("file_rejected")
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise DefiniteTelegramError(type(exc).__name__) from exc
        return bytes(chunks), path

    async def close(self) -> None:
        await self._api.aclose()
        await self._files.aclose()


class JobAssistantClient:
    def __init__(self, settings: Settings) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.job_assistant_base_url,
            timeout=httpx.Timeout(settings.request_timeout_seconds),
        )
        self._api_headers = {
            "Authorization": f"Bearer {settings.job_assistant_api_token.get_secret_value()}"
        }
        self._notification_headers = {
            "Authorization": (
                f"Bearer {settings.job_assistant_notification_token.get_secret_value()}"
            )
        }

    async def authorize(self, user_id: int, chat_id: int) -> bool:
        response = await self._client.get(
            "/internal/telegram/authorize",
            params={"user_id": user_id, "chat_id": chat_id},
            headers=self._api_headers,
        )
        return response.is_success and bool(response.json().get("authorized"))

    async def ready(self) -> bool:
        try:
            response = await self._client.get("/health/ready")
        except (httpx.TimeoutException, httpx.TransportError):
            return False
        return response.is_success

    async def pending(self, user_id: int, chat_id: int) -> bool:
        response = await self._client.get(
            "/internal/telegram/pending",
            params={"user_id": user_id, "chat_id": chat_id},
            headers=self._api_headers,
        )
        return response.is_success and bool(response.json().get("pending"))

    async def update(self, update: dict[str, Any]) -> list[dict[str, Any]]:
        response = await self._client.post(
            "/internal/telegram/update", json=update, headers=self._api_headers
        )
        response.raise_for_status()
        return list(response.json().get("replies", []))

    async def document(
        self, update: dict[str, Any], content: bytes, filename: str, mime_type: str
    ) -> list[dict[str, Any]]:
        response = await self._client.post(
            "/internal/telegram/document",
            data={"update_json": json.dumps(update)},
            files={"content": (filename, content, mime_type)},
            headers=self._api_headers,
        )
        response.raise_for_status()
        return list(response.json().get("replies", []))

    async def notifications(self) -> list[dict[str, Any]]:
        response = await self._client.get(
            "/internal/telegram/notifications", headers=self._notification_headers
        )
        response.raise_for_status()
        return list(response.json().get("notifications", []))

    async def notification_document(self, event_id: str, maximum: int) -> tuple[bytes, str, str]:
        try:
            async with self._client.stream(
                "GET",
                f"/internal/telegram/notifications/{event_id}/document",
                headers=self._notification_headers,
            ) as response:
                response.raise_for_status()
                mime_type = response.headers.get("content-type", "").partition(";")[0]
                filename = response.headers.get("x-document-filename", "")
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > maximum:
                        raise DefiniteTelegramError("backend_document_too_large")
        except DefiniteTelegramError:
            raise
        except httpx.HTTPError as exc:
            raise DefiniteTelegramError("backend_document_unavailable") from exc
        if (
            mime_type
            not in {
                "application/pdf",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            }
            or not filename
        ):
            raise DefiniteTelegramError("backend_document_rejected")
        return bytes(chunks), mime_type, filename

    async def notification_outcome(self, event_id: str, outcome: str) -> None:
        response = await self._client.post(
            f"/internal/telegram/notifications/{event_id}/{outcome}",
            headers=self._notification_headers,
        )
        response.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()
