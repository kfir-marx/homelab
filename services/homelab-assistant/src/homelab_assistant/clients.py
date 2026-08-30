from __future__ import annotations

import json
import subprocess
from typing import Any, Literal

import httpx


class TelegramClient:
    def __init__(self, token: str, timeout: float = 40.0) -> None:
        self._token = token
        self._client = httpx.Client(
            base_url=f"https://api.telegram.org/bot{token}", timeout=timeout
        )

    def get_me_id(self) -> int:
        response = self._client.get("/getMe")
        response.raise_for_status()
        bot_id = response.json().get("result", {}).get("id")
        if not isinstance(bot_id, int):
            raise ValueError("Telegram getMe returned no numeric bot identity")
        return bot_id

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
    ) -> int | None:
        chunks = _telegram_chunks(text)
        last_message_id: int | None = None
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
            response = self._client.post("/sendMessage", json=body)
            response.raise_for_status()
            message_id = response.json().get("result", {}).get("message_id")
            if isinstance(message_id, int):
                last_message_id = message_id
        return last_message_id

    def edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        response = self._client.post(
            "/editMessageText",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text[:4000],
                "disable_web_page_preview": True,
            },
        )
        if response.status_code == 400 and "message is not modified" in response.text:
            return
        response.raise_for_status()

    def answer_callback(self, callback_query_id: str) -> None:
        response = self._client.post(
            "/answerCallbackQuery", json={"callback_query_id": callback_query_id}
        )
        response.raise_for_status()


ActuatorOperation = Literal["status", "switch-to-gaming", "switch-to-kubernetes"]


class SshActuatorClient:
    """Invoke the forced-command actuator with a fixed, non-shell SSH vector."""

    def __init__(
        self,
        host: str,
        user: str,
        identity_file: str,
        known_hosts_file: str,
        timeout: int,
    ) -> None:
        self._destination = f"{user}@{host}"
        self._identity_file = identity_file
        self._known_hosts_file = known_hosts_file
        self._timeout = timeout

    def execute(self, operation: ActuatorOperation) -> dict[str, Any]:
        if operation not in {"status", "switch-to-gaming", "switch-to-kubernetes"}:
            raise ValueError("unsupported actuator operation")
        command = [
            "/usr/bin/ssh",
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={self._known_hosts_file}",
            "-i",
            self._identity_file,
            self._destination,
            operation,
        ]
        try:
            completed = subprocess.run(  # noqa: S603 - fixed or declarative arguments only
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("actuator fixed operation timed out") from exc
        if completed.returncode != 0:
            raise RuntimeError("actuator rejected or failed the fixed operation")
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ValueError("actuator returned invalid structured state") from exc
        if not isinstance(payload, dict):
            raise ValueError("actuator returned invalid structured state")
        return payload


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
