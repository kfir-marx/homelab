from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Literal, cast

import httpx

from .app_server import CodexAppServer, UnixSocketJsonRpc, redact_telegram
from .bot import AssistantBot, as_codex_client
from .bridge_state import BridgeState
from .clients import SshActuatorClient, TelegramClient
from .config import Settings
from .switching import KubernetesSwitcher, SwitchCoordinator

LOG = logging.getLogger("homelab_assistant")


class TelegramIdentityConfigurationError(RuntimeError):
    """Fail closed when the allowed private identity is the bot itself."""


def validate_telegram_identity(settings: Settings, bot_id: int) -> None:
    if bot_id in {
        settings.telegram_allowed_user_id,
        settings.telegram_allowed_chat_id,
    }:
        raise TelegramIdentityConfigurationError(
            "Telegram allowed identity targets the bot account"
        )


class TelegramProgress:
    """Bound progress edits so App Server fragments do not flood Telegram."""

    def __init__(self, telegram: TelegramClient, chat_id: int) -> None:
        self.telegram = telegram
        self.chat_id = chat_id
        self.message_id: int | None = None
        self.last_edit = 0.0
        self.last_text = ""
        self._lock = threading.Lock()

    def __call__(self, text: str) -> None:
        safe = redact_telegram(text, 3500)
        now = time.monotonic()
        with self._lock:
            if safe == self.last_text:
                return
            self.last_text = safe
            if self.message_id is None:
                self.message_id = self.telegram.send_message(self.chat_id, safe)
                self.last_edit = now
                return
            if now - self.last_edit < 1.5:
                return
            self.telegram.edit_message(self.chat_id, self.message_id, safe)
            self.last_edit = now


def build_bot(settings: Settings) -> AssistantBot:
    rpc = UnixSocketJsonRpc(settings.codex_socket_path, settings.app_server_request_timeout_seconds)
    codex = CodexAppServer(rpc, settings.codex_cwd, settings.codex_turn_timeout_seconds)
    switcher = SwitchCoordinator(
        KubernetesSwitcher(
            settings.kubernetes_api_url,
            settings.kubernetes_switch_token_file,
            settings.kubernetes_ca_file,
            cast(Literal["gpu-2"], settings.kubernetes_node_name),
            settings.kubernetes_timeout_seconds,
            settings.kubernetes_drain_timeout_seconds,
            settings.kubernetes_ready_timeout_seconds,
        ),
        SshActuatorClient(
            settings.actuator_host,
            settings.actuator_user,
            settings.actuator_identity_file,
            settings.actuator_known_hosts_file,
            settings.actuator_timeout_seconds,
        ),
    )
    return AssistantBot(
        settings,
        as_codex_client(codex),
        BridgeState(settings.state_database_path),
        switcher,
    )


def run(settings: Settings) -> None:
    telegram = TelegramClient(settings.telegram_token.get_secret_value())
    bot = build_bot(settings)
    executor = ThreadPoolExecutor(
        max_workers=settings.worker_threads, thread_name_prefix="telegram-update"
    )
    active: set[Future[None]] = set()
    offset: int | None = None
    identity_validated = False

    def handle(update: dict[str, object]) -> None:
        message = update.get("message")
        callback = update.get("callback_query")
        chat: object = None
        if isinstance(message, dict):
            chat = message.get("chat")
        elif isinstance(callback, dict):
            callback_message = callback.get("message")
            if isinstance(callback_message, dict):
                chat = callback_message.get("chat")
        chat_id = (
            int(chat["id"])
            if isinstance(chat, dict) and isinstance(chat.get("id"), int)
            else settings.telegram_allowed_chat_id
        )
        progress = TelegramProgress(telegram, chat_id)
        try:
            replies = bot.process(update, progress)
            if replies and isinstance(callback, dict) and isinstance(callback.get("id"), str):
                telegram.answer_callback(str(callback["id"]))
            for reply in replies:
                telegram.send_message(reply.chat_id, reply.text, reply.buttons)
        except httpx.HTTPError, ValueError:
            # Telegram embeds the bot token in request URLs. Never log exception strings.
            LOG.warning("Telegram outbound delivery failed")
        except Exception:  # keep token-bearing transport exceptions out of logs
            LOG.warning("Telegram update worker failed")
            try:
                telegram.send_message(
                    chat_id, "The Telegram bridge could not complete the request."
                )
            except httpx.HTTPError, ValueError:
                LOG.warning("Telegram error reply delivery failed")

    while True:
        try:
            if not identity_validated:
                validate_telegram_identity(settings, telegram.get_me_id())
                identity_validated = True
                LOG.info("starting exact-identity private Telegram long polling in locked state")
            active = {future for future in active if not future.done()}
            updates = telegram.get_updates(offset)
            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = update_id + 1
                active.add(executor.submit(handle, update))
        except TelegramIdentityConfigurationError:
            LOG.error("Telegram identity configuration is invalid; refusing to poll")
            raise
        except httpx.HTTPError, ValueError:
            # Telegram embeds the bot token in request URLs. Never log exception strings.
            LOG.warning("Telegram polling failed; retrying")
            time.sleep(5)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)


def main() -> None:
    configure_logging()
    run(Settings())


if __name__ == "__main__":
    main()
