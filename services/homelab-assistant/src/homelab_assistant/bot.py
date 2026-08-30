"""Private Telegram router for local Codex App Server and fixed homelab operations."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from .app_server import (
    AppServerError,
    CodexAppServer,
    ProgressCallback,
    ThreadPage,
    TurnResult,
    redact_telegram,
)
from .bridge_state import BridgeState
from .config import Settings
from .switching import Mode, SwitchCoordinator

LOG = logging.getLogger("homelab_assistant.bot")

HELP = """Telegram controls:
/tg help — show this guide
/tg sessions — list recent Codex threads
/tg current — show the selected thread
/tg new [title] — create and select a Codex thread
/tg switch — choose a thread
/tg stop — interrupt the selected thread's active turn
/tg rename <title> — rename the selected thread
/tg unlock — open the short-lived administrator lease
/tg lock — immediately close the administrator lease

Deterministic operations:
/ops gaming — safely transition gpu-2/VM 402 to Windows VM 502
/ops k8s — safely transition Windows VM 502 to gpu-2/VM 402

Protocol-backed Codex commands:
/status, /compact, /fork, /model, /review

All other root slash commands belong to Codex. Commands this client cannot map to a
stable App Server operation fail explicitly and are never sent as ordinary prompts."""


@dataclass(frozen=True)
class Reply:
    chat_id: int
    text: str
    buttons: tuple[tuple[str, str], ...] = ()


class CodexClient(Protocol):
    def list_threads(self, cursor: str | None = None, limit: int = 8) -> ThreadPage: ...

    def read_thread(self, thread_id: str) -> dict[str, Any]: ...

    def start_thread(self, title: str | None = None) -> dict[str, Any]: ...

    def resume_thread(self, thread_id: str, model: str | None = None) -> dict[str, Any]: ...

    def rename_thread(self, thread_id: str, title: str) -> None: ...

    def fork_thread(self, thread_id: str) -> dict[str, Any]: ...

    def compact_thread(self, thread_id: str, progress: ProgressCallback | None = None) -> None: ...

    def status(self, thread_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]: ...

    def list_models(self) -> tuple[dict[str, Any], ...]: ...

    def set_model(self, thread_id: str, model: str) -> None: ...

    def interrupt(self, thread_id: str) -> bool: ...

    def run_review(
        self, thread_id: str, progress: ProgressCallback | None = None
    ) -> TurnResult: ...

    def run_text(
        self, thread_id: str, text: str, progress: ProgressCallback | None = None
    ) -> TurnResult: ...


class AdministratorLease:
    """In-memory by design: every service restart returns to locked state."""

    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds
        self._expires: dict[int, float] = {}
        self._lock = threading.Lock()

    def unlock(self, user_id: int) -> int:
        with self._lock:
            self._expires[user_id] = time.monotonic() + self.ttl_seconds
        return self.ttl_seconds

    def lock(self, user_id: int) -> None:
        with self._lock:
            self._expires.pop(user_id, None)

    def remaining(self, user_id: int) -> int:
        with self._lock:
            expiry = self._expires.get(user_id, 0.0)
            remaining = max(0, int(expiry - time.monotonic()))
            if not remaining:
                self._expires.pop(user_id, None)
            return remaining


class AssistantBot:
    def __init__(
        self,
        settings: Settings,
        codex: CodexClient,
        state: BridgeState,
        switcher: SwitchCoordinator | None = None,
    ) -> None:
        self.settings = settings
        self.codex = codex
        self.state = state
        self.switcher = switcher
        self.lease = AdministratorLease(settings.administrator_lease_seconds)
        self.codex_commands: dict[
            str, Callable[[int, int, str, ProgressCallback | None], Reply]
        ] = {
            "/status": self._codex_status,
            "/compact": self._codex_compact,
            "/fork": self._codex_fork,
            "/model": self._codex_model,
            "/review": self._codex_review,
        }

    def _identity(self, update: dict[str, Any]) -> tuple[int, int, dict[str, Any]] | None:
        callback = update.get("callback_query")
        message = update.get("message")
        actor = callback.get("from") if isinstance(callback, dict) else None
        if actor is None and isinstance(message, dict):
            actor = message.get("from")
        chat = callback.get("message", {}).get("chat") if isinstance(callback, dict) else None
        if chat is None and isinstance(message, dict):
            chat = message.get("chat")
        if not isinstance(actor, dict) or not isinstance(chat, dict):
            return None
        if actor.get("id") != self.settings.telegram_allowed_user_id:
            return None
        if (
            chat.get("id") != self.settings.telegram_allowed_chat_id
            or chat.get("type") != "private"
        ):
            return None
        if isinstance(message, dict) and any(
            key in message
            for key in (
                "forward_origin",
                "forward_from",
                "forward_from_chat",
                "is_automatic_forward",
            )
        ):
            return None
        return int(actor["id"]), int(chat["id"]), message if isinstance(message, dict) else {}

    def process(
        self, update: dict[str, Any], progress: ProgressCallback | None = None
    ) -> list[Reply]:
        identity = self._identity(update)
        if not identity:
            return []
        user_id, chat_id, message = identity
        callback = update.get("callback_query")
        try:
            if isinstance(callback, dict):
                return [self._callback(user_id, chat_id, str(callback.get("data", "")), progress)]
            text = message.get("text")
            if not isinstance(text, str) or not text.strip():
                return [Reply(chat_id, "Text messages only. Use /tg help.")]
            text = text.strip()
            if len(text) > self.settings.max_input_chars:
                return [
                    Reply(chat_id, f"Message exceeds {self.settings.max_input_chars} characters.")
                ]
            command_token, _, argument = text.partition(" ")
            command = command_token.split("@", 1)[0].casefold()
            if command == "/tg":
                return [self._telegram_command(user_id, chat_id, argument, progress)]
            if command == "/ops":
                return [self._ops_command(user_id, chat_id, argument)]
            if command.startswith("/"):
                handler = self.codex_commands.get(command)
                if handler:
                    return [handler(user_id, chat_id, argument, progress)]
                return [
                    Reply(
                        chat_id,
                        f"Codex command {command} is not supported by this Telegram Codex client; "
                        "it was not sent as a prompt.",
                    )
                ]
            return [self._ordinary_message(user_id, chat_id, text, progress)]
        except AppServerError:
            LOG.warning("Codex App Server operation failed")
            return [Reply(chat_id, "Codex App Server is unavailable or rejected the operation.")]

    def _telegram_command(
        self, user_id: int, chat_id: int, argument: str, progress: ProgressCallback | None
    ) -> Reply:
        subcommand, _, rest = argument.strip().partition(" ")
        subcommand = subcommand.casefold() or "help"
        if subcommand == "help":
            return Reply(chat_id, HELP)
        if subcommand in {"sessions", "switch"}:
            if rest.strip():
                return Reply(chat_id, f"Usage: /tg {subcommand}")
            return self._sessions(user_id, chat_id, None)
        if subcommand == "current":
            thread_id = self.state.selected_thread(user_id)
            if not thread_id:
                return Reply(chat_id, "No Codex thread is selected. Use /tg sessions or /tg new.")
            return Reply(chat_id, self._format_current(self.codex.read_thread(thread_id), user_id))
        if subcommand == "unlock":
            if rest.strip():
                return Reply(chat_id, "Usage: /tg unlock")
            seconds = self.lease.unlock(user_id)
            self.state.audit(user_id, "lease", "unlocked")
            return Reply(chat_id, f"Administrator lease unlocked for {seconds // 60} minutes.")
        if subcommand == "lock":
            self.lease.lock(user_id)
            self.state.audit(user_id, "lease", "locked")
            return Reply(chat_id, "Administrator lease locked.")
        if subcommand == "new":
            locked = self._require_lease(user_id, chat_id)
            if locked:
                return locked
            title = _clean_title(rest) if rest.strip() else None
            thread = self.codex.start_thread(title)
            thread_id = str(thread["id"])
            self.state.select_thread(user_id, thread_id)
            self.state.audit(user_id, "thread-new", "completed", thread_id)
            return Reply(chat_id, "Created and selected " + _thread_identity(thread))
        if subcommand == "rename":
            locked = self._require_lease(user_id, chat_id)
            if locked:
                return locked
            title = _clean_title(rest)
            if not title:
                return Reply(chat_id, "Usage: /tg rename <title>")
            thread_id = self._selected_or_none(user_id)
            if not thread_id:
                return Reply(chat_id, "No Codex thread is selected. Use /tg sessions or /tg new.")
            self.codex.rename_thread(thread_id, title)
            self.state.audit(user_id, "thread-rename", "completed", thread_id)
            return Reply(chat_id, f"Renamed selected thread {thread_id} to “{title}”.")
        if subcommand == "stop":
            if rest.strip():
                return Reply(chat_id, "Usage: /tg stop")
            thread_id = self._selected_or_none(user_id)
            if not thread_id:
                return Reply(chat_id, "No Codex thread is selected. Use /tg sessions or /tg new.")
            stopped = self.codex.interrupt(thread_id)
            outcome = "requested" if stopped else "idle"
            self.state.audit(user_id, "turn-stop", outcome, thread_id)
            if stopped:
                return Reply(chat_id, "Interrupt requested for the selected Codex turn.")
            return Reply(chat_id, "The selected thread has no active turn.")
        return Reply(chat_id, "Unknown /tg command. Use /tg help.")

    def _ops_command(self, user_id: int, chat_id: int, argument: str) -> Reply:
        operation, _, extra = argument.strip().partition(" ")
        if operation not in {"gaming", "k8s"} or extra.strip():
            return Reply(chat_id, "Usage: /ops gaming or /ops k8s")
        locked = self._require_lease(user_id, chat_id)
        if locked:
            return locked
        mode: Mode = "gaming" if operation == "gaming" else "kubernetes"
        return self._prepare_switch(user_id, chat_id, mode)

    def _callback(
        self,
        user_id: int,
        chat_id: int,
        data: str,
        progress: ProgressCallback | None,
    ) -> Reply:
        namespace, separator, nonce = data.partition(":")
        if not separator or namespace not in {"tg", "ops"}:
            return Reply(chat_id, "Invalid or expired callback.")
        consumed = self.state.consume_callback(nonce, user_id, chat_id)
        if not consumed:
            return Reply(chat_id, "That callback expired or was already used.")
        action, payload = consumed
        if (namespace == "ops") != action.startswith("ops-"):
            return Reply(chat_id, "Invalid or expired callback.")
        if action == "thread-select":
            thread_id = str(payload.get("thread_id", ""))
            thread = self.codex.resume_thread(thread_id)
            self.state.select_thread(user_id, thread_id)
            self.state.audit(user_id, "thread-select", "completed", thread_id)
            return Reply(chat_id, "Selected " + _thread_identity(thread))
        if action == "thread-page":
            return self._sessions(user_id, chat_id, str(payload.get("cursor") or "") or None)
        if action == "model-select":
            locked = self._require_lease(user_id, chat_id)
            if locked:
                return locked
            selected_thread_id = self._selected_or_none(user_id)
            if not selected_thread_id:
                return Reply(chat_id, "No Codex thread is selected.")
            model = str(payload.get("model", ""))
            self.codex.set_model(selected_thread_id, model)
            self.state.audit(user_id, "model-select", "completed", selected_thread_id)
            return Reply(chat_id, f"Selected Codex model {model} for thread {selected_thread_id}.")
        if action == "ops-cancel":
            self.state.audit(user_id, "ops", "cancelled")
            return Reply(chat_id, "Cancelled; no switching action was performed.")
        if action in {"ops-gaming", "ops-kubernetes"}:
            locked = self._require_lease(user_id, chat_id)
            if locked:
                return locked
            return self._execute_switch(
                user_id, chat_id, "gaming" if action == "ops-gaming" else "kubernetes"
            )
        return Reply(chat_id, "Invalid or expired callback.")

    def _sessions(self, user_id: int, chat_id: int, cursor: str | None) -> Reply:
        page = self.codex.list_threads(cursor, self.settings.session_page_size)
        selected = self.state.selected_thread(user_id)
        if not page.threads:
            return Reply(chat_id, "No Codex threads were found for the homelab repository.")
        lines = ["Recent Codex threads:"]
        buttons: list[tuple[str, str]] = []
        for index, thread in enumerate(page.threads, start=1):
            thread_id = str(thread.get("id", ""))
            marker = "●" if thread_id == selected else "○"
            lines.append(f"{index}. {marker} {_thread_metadata(thread)}")
            nonce = self.state.issue_callback(
                user_id,
                chat_id,
                "thread-select",
                {"thread_id": thread_id},
                self.settings.callback_ttl_seconds,
            )
            title = _thread_title(thread)[:38]
            buttons.append((f"{marker} {index}. {title}", f"tg:{nonce}"))
        if page.next_cursor:
            nonce = self.state.issue_callback(
                user_id,
                chat_id,
                "thread-page",
                {"cursor": page.next_cursor},
                self.settings.callback_ttl_seconds,
            )
            buttons.append(("Next page →", f"tg:{nonce}"))
        if cursor:
            nonce = self.state.issue_callback(
                user_id,
                chat_id,
                "thread-page",
                {"cursor": ""},
                self.settings.callback_ttl_seconds,
            )
            buttons.append(("← First page", f"tg:{nonce}"))
        return Reply(chat_id, "\n".join(lines), tuple(buttons))

    def _ordinary_message(
        self, user_id: int, chat_id: int, text: str, progress: ProgressCallback | None
    ) -> Reply:
        locked = self._require_lease(user_id, chat_id)
        if locked:
            return locked
        thread_id = self._selected_or_none(user_id)
        if not thread_id:
            return Reply(chat_id, "No Codex thread is selected. Use /tg sessions or /tg new.")
        result = self.codex.run_text(thread_id, text, progress)
        self.state.audit(user_id, "turn", result.status[:24], thread_id)
        return Reply(chat_id, redact_telegram(result.text))

    def _codex_status(
        self, user_id: int, chat_id: int, argument: str, progress: ProgressCallback | None
    ) -> Reply:
        del progress
        if argument.strip():
            return Reply(chat_id, "Usage: /status")
        thread_id = self._selected_or_none(user_id)
        if not thread_id:
            return Reply(chat_id, "No Codex thread is selected. Use /tg sessions or /tg new.")
        thread, usage, limits = self.codex.status(thread_id)
        return Reply(chat_id, _format_status(thread, usage, limits))

    def _codex_compact(
        self, user_id: int, chat_id: int, argument: str, progress: ProgressCallback | None
    ) -> Reply:
        if argument.strip():
            return Reply(chat_id, "Usage: /compact")
        locked = self._require_lease(user_id, chat_id)
        if locked:
            return locked
        thread_id = self._selected_or_none(user_id)
        if not thread_id:
            return Reply(chat_id, "No Codex thread is selected. Use /tg sessions or /tg new.")
        self.codex.compact_thread(thread_id, progress)
        self.state.audit(user_id, "compact", "completed", thread_id)
        return Reply(chat_id, f"Codex context compaction completed for {thread_id}.")

    def _codex_fork(
        self, user_id: int, chat_id: int, argument: str, progress: ProgressCallback | None
    ) -> Reply:
        del progress
        if argument.strip():
            return Reply(chat_id, "Usage: /fork")
        locked = self._require_lease(user_id, chat_id)
        if locked:
            return locked
        source = self._selected_or_none(user_id)
        if not source:
            return Reply(chat_id, "No Codex thread is selected. Use /tg sessions or /tg new.")
        thread = self.codex.fork_thread(source)
        thread_id = str(thread["id"])
        self.state.select_thread(user_id, thread_id)
        self.state.audit(user_id, "fork", "completed", thread_id)
        return Reply(chat_id, "Forked and selected " + _thread_identity(thread))

    def _codex_model(
        self, user_id: int, chat_id: int, argument: str, progress: ProgressCallback | None
    ) -> Reply:
        del progress
        if argument.strip():
            return Reply(chat_id, "Use /model without arguments and choose from the picker.")
        locked = self._require_lease(user_id, chat_id)
        if locked:
            return locked
        if not self._selected_or_none(user_id):
            return Reply(chat_id, "No Codex thread is selected. Use /tg sessions or /tg new.")
        buttons: list[tuple[str, str]] = []
        lines = ["Choose a Codex model:"]
        for model in self.codex.list_models():
            model_id = str(model.get("model") or model.get("id") or "")
            if not model_id:
                continue
            display = str(model.get("displayName") or model_id)
            nonce = self.state.issue_callback(
                user_id,
                chat_id,
                "model-select",
                {"model": model_id},
                self.settings.callback_ttl_seconds,
            )
            buttons.append((display[:48], f"tg:{nonce}"))
        if not buttons:
            return Reply(chat_id, "Codex App Server returned no picker-visible models.")
        return Reply(chat_id, "\n".join(lines), tuple(buttons))

    def _codex_review(
        self, user_id: int, chat_id: int, argument: str, progress: ProgressCallback | None
    ) -> Reply:
        if argument.strip():
            return Reply(
                chat_id,
                "This client currently supports /review only for uncommitted changes.",
            )
        locked = self._require_lease(user_id, chat_id)
        if locked:
            return locked
        thread_id = self._selected_or_none(user_id)
        if not thread_id:
            return Reply(chat_id, "No Codex thread is selected. Use /tg sessions or /tg new.")
        result = self.codex.run_review(thread_id, progress)
        self.state.audit(user_id, "review", result.status[:24], thread_id)
        return Reply(chat_id, redact_telegram(result.text))

    def _prepare_switch(self, user_id: int, chat_id: int, mode: Mode) -> Reply:
        if not self.switcher:
            return Reply(chat_id, "Switching is not configured; no action was taken.")
        try:
            current = self.switcher.status()
        except OSError, RuntimeError, ValueError:
            self.state.audit(user_id, f"ops-{mode}", "preview-failed")
            return Reply(chat_id, "Current state is unsafe or unavailable; no action was taken.")
        if mode == "gaming":
            transition = (
                "cordon gpu-2 → PDB-aware eviction of non-DaemonSet workloads → fixed actuator "
                "switch-to-gaming → graceful VM 402 shutdown → VM 502 start"
            )
        else:
            transition = (
                "fixed actuator switch-to-kubernetes → graceful VM 502 shutdown → VM 402 start → "
                "wait for gpu-2 Ready → uncordon gpu-2"
            )
        confirm = self.state.issue_callback(
            user_id,
            chat_id,
            f"ops-{mode}",
            {},
            self.settings.switch_confirmation_ttl_seconds,
        )
        cancel = self.state.issue_callback(
            user_id,
            chat_id,
            "ops-cancel",
            {},
            self.settings.switch_confirmation_ttl_seconds,
        )
        self.state.audit(user_id, f"ops-{mode}", "previewed")
        return Reply(
            chat_id,
            f"Current state: {current}\n\nExact transition: {transition}\n\n"
            f"Confirmation expires in {self.settings.switch_confirmation_ttl_seconds} seconds.",
            (("Confirm transition", f"ops:{confirm}"), ("Cancel", f"ops:{cancel}")),
        )

    def _execute_switch(self, user_id: int, chat_id: int, mode: Mode) -> Reply:
        if not self.switcher:
            return Reply(chat_id, "Switching is not configured; no action was taken.")
        self.state.audit(user_id, f"ops-{mode}", "started")
        result = self.switcher.switch(mode)
        outcome = "completed" if result.ok else "failed-safe"
        self.state.audit(user_id, f"ops-{mode}", outcome)
        LOG.info("deterministic %s transition %s", mode, outcome)
        return Reply(chat_id, result.detail)

    def _require_lease(self, user_id: int, chat_id: int) -> Reply | None:
        if self.lease.remaining(user_id):
            return None
        return Reply(chat_id, "Administrator lease is locked. Run /tg unlock first.")

    def _selected_or_none(self, user_id: int) -> str | None:
        return self.state.selected_thread(user_id)

    def _format_current(self, thread: dict[str, Any], user_id: int) -> str:
        lease = self.lease.remaining(user_id)
        lease_text = f"unlocked ({lease}s remaining)" if lease else "locked"
        return f"{_thread_metadata(thread, include_id=True)}\nAdministrator lease: {lease_text}"


def _clean_title(value: str) -> str:
    return " ".join(redact_telegram(value).split())[:120]


def _thread_title(thread: dict[str, Any]) -> str:
    candidate = thread.get("name") or thread.get("preview") or "Untitled"
    return " ".join(redact_telegram(str(candidate)).split())[:80] or "Untitled"


def _source_kind(thread: dict[str, Any]) -> str:
    raw = thread.get("sourceKind") or thread.get("source") or "unknown"
    if isinstance(raw, dict):
        raw = raw.get("type") or raw.get("kind") or "unknown"
    source = str(raw)
    return {"vscode": "vscode", "cli": "cli", "appServer": "Telegram/App Server"}.get(
        source, "unknown"
    )


def _thread_state(thread: dict[str, Any]) -> str:
    status = thread.get("status", {})
    state = status.get("type") if isinstance(status, dict) else status
    return "active" if state == "active" else "idle"


def _thread_branch(thread: dict[str, Any]) -> str:
    git_info = thread.get("gitInfo", {})
    branch = git_info.get("branch") if isinstance(git_info, dict) else None
    return " ".join(str(branch).split())[:80] if branch else "branch unknown"


def _last_activity(thread: dict[str, Any]) -> str:
    value = thread.get("updatedAt") or thread.get("createdAt")
    if not isinstance(value, int | float):
        return "time unknown"
    return datetime.fromtimestamp(value, UTC).strftime("%Y-%m-%d %H:%M UTC")


def _thread_metadata(thread: dict[str, Any], include_id: bool = False) -> str:
    identity = f"id={thread.get('id', 'unknown')} · " if include_id else ""
    return (
        f"{identity}{_thread_title(thread)} · {_source_kind(thread)} · {_thread_state(thread)} · "
        f"{_thread_branch(thread)} · {_last_activity(thread)}"
    )


def _thread_identity(thread: dict[str, Any]) -> str:
    return (
        f"thread {thread.get('id', 'unknown')} — {_thread_title(thread)} ({_source_kind(thread)})"
    )


def _format_status(thread: dict[str, Any], usage: dict[str, Any], limits: dict[str, Any]) -> str:
    status = thread.get("status", {})
    active_flags = status.get("activeFlags", []) if isinstance(status, dict) else []
    model = thread.get("model") or thread.get("modelId") or "not reported"
    cwd = thread.get("cwd") or "not reported"
    token_usage = usage.get("tokenUsage", usage)
    if not isinstance(token_usage, dict):
        token_usage = {}
    total_usage = token_usage.get("total", token_usage)
    if not isinstance(total_usage, dict):
        total_usage = {}
    used = total_usage.get("totalTokens") or total_usage.get("inputTokens") or "not reported"
    window = (
        token_usage.get("modelContextWindow") or token_usage.get("contextWindow") or "not reported"
    )
    rate = limits.get("rateLimits", {})
    rate_lines: list[str] = []
    if isinstance(rate, dict):
        for label in ("primary", "secondary"):
            bucket = rate.get(label)
            if isinstance(bucket, dict):
                percent = bucket.get("usedPercent", "?")
                reset = bucket.get("resetsAt")
                reset_text = "unknown"
                if isinstance(reset, int | float):
                    reset_text = datetime.fromtimestamp(reset, UTC).strftime("%Y-%m-%d %H:%M UTC")
                rate_lines.append(f"{label}: {percent}% used; resets {reset_text}")
    if not rate_lines:
        rate_lines.append("not reported")
    runtime = _thread_state(thread)
    active = ", ".join(str(item) for item in active_flags) if active_flags else runtime
    return redact_telegram(
        "\n".join(
            [
                f"Thread ID: {thread.get('id', 'unknown')}",
                f"Name: {_thread_title(thread)}",
                f"Cwd: {cwd}",
                f"Origin: {_source_kind(thread)}",
                f"Model: {model}",
                f"Runtime: {runtime}",
                f"Active turn state: {active}",
                f"Context usage: {used}/{window}",
                "Rate limits: " + "; ".join(rate_lines),
            ]
        )
    )


def as_codex_client(client: CodexAppServer) -> CodexClient:
    """Keep the concrete type visible to static checkers at the construction boundary."""
    return cast(CodexClient, client)
