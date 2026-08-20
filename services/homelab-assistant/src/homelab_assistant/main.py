from __future__ import annotations

import logging
import time

import httpx

from .bot import AssistantBot
from .clients import ExternalAiClient, JobAssistantClient, LlmClient, TelegramClient
from .config import Settings
from .sessions import SessionStore

LOG = logging.getLogger("homelab_assistant")


def run(settings: Settings) -> None:
    if not settings.telegram_allowed_user_ids:
        raise ValueError("HOMELAB_ASSISTANT_TELEGRAM_ALLOWED_USER_IDS must not be empty")
    telegram = TelegramClient(settings.telegram_token.get_secret_value())
    llm = LlmClient(
        settings.llm_base_url,
        settings.llm_api_key.get_secret_value(),
        settings.llm_model,
        settings.llm_timeout_seconds,
    )
    store = SessionStore(settings.session_database_url.get_secret_value())
    external_ai = (
        ExternalAiClient(
            settings.external_ai_base_url, settings.external_ai_token.get_secret_value()
        )
        if settings.external_ai_token.get_secret_value()
        else None
    )
    job_assistant = (
        JobAssistantClient(
            settings.job_assistant_base_url,
            settings.job_assistant_token.get_secret_value(),
            settings.job_assistant_notification_token.get_secret_value(),
        )
        if settings.job_assistant_token.get_secret_value()
        and settings.job_assistant_notification_token.get_secret_value()
        else None
    )
    bot = AssistantBot(settings, llm, store, external_ai, job_assistant)
    offset: int | None = None
    LOG.info("starting allowlisted Telegram long polling")
    while True:
        try:
            updates = telegram.get_updates(offset)
            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = update_id + 1
                document = (update.get("message") or {}).get("document")
                if document and bot.wants_job_document(update):
                    try:
                        update["message"]["_file_bytes"] = telegram.download_document(
                            str(document["file_id"]), settings.max_job_upload_bytes
                        )
                    except ValueError:
                        telegram.send_message(
                            int(update["message"]["chat"]["id"]),
                            "Document exceeds the 10 MB gateway limit.",
                        )
                        continue
                for reply in bot.process(update):
                    telegram.send_message(reply.chat_id, reply.text, reply.buttons)
            for reply in bot.poll_external():
                telegram.send_message(reply.chat_id, reply.text, reply.buttons)
            if job_assistant:
                for notification in job_assistant.notifications():
                    buttons = tuple(
                        (str(row[0]), "job:" + str(row[1]))
                        for row in notification.get("buttons", [])
                    )
                    telegram.send_message(
                        int(notification["chat_id"]), str(notification["text"]), buttons
                    )
                    job_assistant.acknowledge(str(notification["id"]))
        except (httpx.HTTPError, ValueError):
            # httpx exception strings can contain the Telegram bot-token URL.
            # Keep credentials out of logs even when the upstream request fails.
            LOG.warning("Telegram polling or model request failed; retrying")
            time.sleep(5)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Telegram embeds the bot token in every API URL. httpx logs complete URLs
    # at INFO, so inheriting the application log level would disclose it.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> None:
    configure_logging()
    settings = Settings()  # type: ignore[call-arg]
    run(settings)


if __name__ == "__main__":
    main()
