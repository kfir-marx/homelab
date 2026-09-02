from __future__ import annotations

import re
from typing import Any

from prometheus_client import Counter

from .clients import (
    DefiniteTelegramError,
    JobAssistantClient,
    TelegramClient,
    UncertainTelegramError,
)
from .config import Settings
from .rate_limit import RateLimits

UPDATES = Counter(
    "shared_services_telegram_updates_total", "Sanitized update outcomes", ["outcome"]
)
DELIVERIES = Counter(
    "shared_services_telegram_deliveries_total", "Sanitized delivery outcomes", ["outcome"]
)

JOB_COMMANDS = {
    "/job_add",
    "/job_status",
    "/job_contact",
    "/job_final",
    "/job_approve",
    "/job_manual",
    "/job_submitted",
    "/job_reopen",
    "/job_help",
    "/job_setup",
    "/job_today",
    "/job_applications",
}
JOB_CALLBACK = re.compile(
    r"^(?:setup-(?:confirm|back|keep|reset|view|cancel)|"
    r"(?:apply|skip|snooze|why|open|next|detail|verify-contact|confirm|cancel|manual|"
    r"accept-draft|upload-revision|accept-message|edit-message|add-contact|choose-contact|final-review|"
    r"submitted|interview|rejected|offer|withdrawn|follow-up|reminder-off|remind-snooze):"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,36})$"
)
GATEWAY_HELP = (
    "Shared services:\n"
    "• Job Assistant — /job_help\n\n"
    "Only explicitly registered private-chat commands are accepted."
)


class Gateway:
    def __init__(
        self,
        settings: Settings,
        telegram: TelegramClient,
        job_assistant: JobAssistantClient,
    ) -> None:
        self.settings = settings
        self.telegram = telegram
        self.job_assistant = job_assistant
        self.rate_limits = RateLimits(
            settings.per_user_updates_per_minute, settings.global_updates_per_minute
        )

    @staticmethod
    def identity(update: dict[str, Any]) -> tuple[int, int] | None:
        callback = update.get("callback_query")
        message = update.get("message")
        actor = callback.get("from") if isinstance(callback, dict) else None
        if actor is None and isinstance(message, dict):
            actor = message.get("from")
        chat = (
            callback.get("message", {}).get("chat")
            if isinstance(callback, dict)
            else message.get("chat")
            if isinstance(message, dict)
            else None
        )
        if not isinstance(actor, dict) or not isinstance(chat, dict):
            return None
        try:
            user_id, chat_id = int(actor["id"]), int(chat["id"])
        except (KeyError, TypeError, ValueError):
            return None
        if chat.get("type") != "private" or user_id != chat_id:
            return None
        if isinstance(message, dict) and any(
            key in message
            for key in ("forward_origin", "forward_from", "sender_chat", "author_signature")
        ):
            return None
        return user_id, chat_id

    async def process(self, update: dict[str, Any]) -> None:
        identity = self.identity(update)
        if identity is None:
            UPDATES.labels(outcome="ignored_identity").inc()
            return
        user_id, chat_id = identity
        if not self.rate_limits.allow(user_id):
            UPDATES.labels(outcome="rate_limited").inc()
            return
        try:
            if not await self.job_assistant.authorize(user_id, chat_id):
                UPDATES.labels(outcome="ignored_unknown").inc()
                return
        except Exception:
            UPDATES.labels(outcome="backend_unavailable").inc()
            return

        callback = update.get("callback_query")
        if isinstance(callback, dict):
            await self._callback(update, callback, user_id)
            return
        message = update.get("message")
        if not isinstance(message, dict):
            return
        if "photo" in message:
            UPDATES.labels(outcome="ignored_photo").inc()
            return
        document = message.get("document")
        if isinstance(document, dict):
            await self._document(update, document, user_id, chat_id)
            return
        text = str(message.get("text", "")).strip()
        command = text.partition(" ")[0].split("@", 1)[0].casefold()
        if command in {"/start", "/help"}:
            await self._send(chat_id, GATEWAY_HELP, [])
            return
        if command in JOB_COMMANDS:
            category = "url" if command == "/job_add" else "update"
            if not self.rate_limits.allow(user_id, category):
                UPDATES.labels(outcome="rate_limited_category").inc()
                return
            await self._forward(update)
            return
        if text and await self.job_assistant.pending(user_id, chat_id):
            await self._forward(update)
            return
        UPDATES.labels(outcome="ignored_unregistered").inc()

    async def _callback(
        self, update: dict[str, Any], callback: dict[str, Any], user_id: int
    ) -> None:
        callback_id = str(callback.get("id", ""))
        if callback_id:
            try:
                await self.telegram.answer_callback(callback_id)
            except (DefiniteTelegramError, UncertainTelegramError):
                pass
        data = str(callback.get("data", ""))
        if len(data.encode()) > 64 or not JOB_CALLBACK.fullmatch(data):
            UPDATES.labels(outcome="ignored_callback").inc()
            return
        category = "generation" if data.startswith("apply:") else "callback"
        if not self.rate_limits.allow(user_id, category):
            UPDATES.labels(outcome="rate_limited_category").inc()
            return
        await self._forward(update)

    async def _document(
        self,
        update: dict[str, Any],
        document: dict[str, Any],
        user_id: int,
        chat_id: int,
    ) -> None:
        if not self.rate_limits.allow(user_id, "file"):
            return
        if not await self.job_assistant.pending(user_id, chat_id):
            UPDATES.labels(outcome="ignored_unexpected_file").inc()
            return
        size = int(document.get("file_size", 0) or 0)
        mime = str(document.get("mime_type", ""))
        filename = str(document.get("file_name", ""))
        allowed_mime = {
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
        if size <= 0 or size > self.settings.max_file_bytes or mime not in allowed_mime:
            await self._send(chat_id, "Upload a PDF or DOCX document no larger than 10 MB.", [])
            return
        try:
            content, _ = await self.telegram.download(
                str(document.get("file_id", "")), self.settings.max_file_bytes
            )
            replies = await self.job_assistant.document(update, content, filename, mime)
            await self._send_replies(replies)
        except Exception as exc:
            UPDATES.labels(outcome=type(exc).__name__).inc()

    async def _forward(self, update: dict[str, Any]) -> None:
        try:
            await self._send_replies(await self.job_assistant.update(update))
            UPDATES.labels(outcome="forwarded").inc()
        except Exception as exc:
            UPDATES.labels(outcome=type(exc).__name__).inc()

    async def _send_replies(self, replies: list[dict[str, Any]]) -> None:
        for reply in replies[:10]:
            await self._send(
                int(reply["chat_id"]),
                str(reply["text"]),
                list(reply.get("buttons", [])),
            )

    async def _send(self, chat_id: int, text: str, buttons: list[Any]) -> None:
        try:
            await self.telegram.send_message(chat_id, text, buttons)
        except Exception as exc:
            DELIVERIES.labels(outcome=type(exc).__name__).inc()

    async def deliver_notifications(self) -> None:
        try:
            events = await self.job_assistant.notifications()
        except Exception:
            return
        for event in events:
            event_id = str(event["id"])
            try:
                document = event.get("document")
                if isinstance(document, dict):
                    content, mime_type, filename = await self.job_assistant.notification_document(
                        event_id, self.settings.max_file_bytes
                    )
                    if (
                        mime_type != str(document.get("mime_type"))
                        or filename != str(document.get("filename"))
                        or len(content) != int(document.get("size_bytes", -1))
                    ):
                        raise DefiniteTelegramError("typed_document_mismatch")
                    await self.telegram.send_document(
                        int(event["chat_id"]),
                        content,
                        filename,
                        mime_type,
                        str(event["text"]),
                        list(event.get("buttons", [])),
                    )
                else:
                    await self.telegram.send_message(
                        int(event["chat_id"]),
                        str(event["text"]),
                        list(event.get("buttons", [])),
                    )
            except UncertainTelegramError:
                await self.job_assistant.notification_outcome(event_id, "uncertain")
                DELIVERIES.labels(outcome="uncertain").inc()
            except DefiniteTelegramError:
                await self.job_assistant.notification_outcome(event_id, "retry")
                DELIVERIES.labels(outcome="retry").inc()
            else:
                await self.job_assistant.notification_outcome(event_id, "ack")
                DELIVERIES.labels(outcome="delivered").inc()
