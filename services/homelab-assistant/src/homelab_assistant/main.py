from __future__ import annotations

import logging
import time

import httpx

from .bot import AssistantBot
from .clients import LlmClient, TelegramClient
from .config import Settings

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
    bot = AssistantBot(settings, llm)
    offset: int | None = None
    LOG.info("starting allowlisted Telegram long polling")
    while True:
        try:
            updates = telegram.get_updates(offset)
            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = update_id + 1
                reply = bot.process(update)
                if reply:
                    telegram.send_message(*reply)
        except (httpx.HTTPError, ValueError):
            # httpx exception strings can contain the Telegram bot-token URL.
            # Keep credentials out of logs even when the upstream request fails.
            LOG.warning("Telegram polling or model request failed; retrying")
            time.sleep(5)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings()  # type: ignore[call-arg]
    run(settings)


if __name__ == "__main__":
    main()
