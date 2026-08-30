"""Codex App Server client over a filesystem-protected Unix socket."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from websockets.sync.client import ClientConnection, unix_connect

LOG = logging.getLogger("homelab_assistant.app_server")
ProgressCallback = Callable[[str], None]


class AppServerError(RuntimeError):
    pass


class RpcTransport(Protocol):
    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]: ...

    def notification_marker(self) -> int: ...

    def wait_notifications(
        self,
        marker: int,
        predicate: Callable[[dict[str, Any]], bool],
        timeout: float,
    ) -> tuple[int, list[dict[str, Any]]]: ...


class UnixSocketJsonRpc:
    """Concurrent JSON-RPC dispatcher for WebSocket frames on one Unix socket."""

    def __init__(self, socket_path: str, request_timeout: float = 30.0) -> None:
        self.socket_path = socket_path
        self.request_timeout = request_timeout
        self._connection: ClientConnection | None = None
        self._connect_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._pending: dict[int, tuple[threading.Event, dict[str, Any]]] = {}
        self._pending_lock = threading.Lock()
        self._notifications: deque[tuple[int, dict[str, Any]]] = deque(maxlen=4000)
        self._notification_sequence = 0
        self._notification_condition = threading.Condition()
        self._next_id = 1

    def _connect(self) -> None:
        if self._connection is not None:
            return
        with self._connect_lock:
            if self._connection is not None:
                return
            try:
                connection = unix_connect(self.socket_path, uri="ws://localhost/")
            except OSError as exc:
                raise AppServerError("Codex App Server socket is unavailable") from exc
            self._connection = connection
            threading.Thread(target=self._reader, name="codex-app-server", daemon=True).start()
            result = self.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "homelab_assistant",
                        "title": "Homelab Telegram Codex Client",
                        "version": "0.2.0",
                    }
                },
            )
            if not result:
                raise AppServerError("Codex App Server initialization failed")
            self.notify("initialized", {})

    def _reader(self) -> None:
        connection = self._connection
        assert connection is not None
        try:
            for raw in connection:
                message = json.loads(raw)
                if not isinstance(message, dict):
                    continue
                request_id = message.get("id")
                if isinstance(request_id, int) and ("result" in message or "error" in message):
                    with self._pending_lock:
                        pending = self._pending.get(request_id)
                        if pending:
                            pending[1].update(message)
                            pending[0].set()
                    continue
                if isinstance(request_id, int) and isinstance(message.get("method"), str):
                    self._reject_server_request(request_id)
                    continue
                if isinstance(message.get("method"), str):
                    with self._notification_condition:
                        self._notification_sequence += 1
                        self._notifications.append((self._notification_sequence, message))
                        self._notification_condition.notify_all()
        except Exception:  # connection errors are surfaced to pending callers without details
            LOG.warning("Codex App Server socket disconnected")
        finally:
            if self._connection is connection:
                self._connection = None
            with self._pending_lock:
                for event, response in self._pending.values():
                    response["error"] = {"message": "Codex App Server disconnected"}
                    event.set()

    def _reject_server_request(self, request_id: int) -> None:
        self._send(
            {
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": "Interactive server request is unavailable in Telegram",
                },
            }
        )

    def _send(self, message: dict[str, Any]) -> None:
        connection = self._connection
        if connection is None:
            raise AppServerError("Codex App Server is unavailable")
        with self._send_lock:
            connection.send(json.dumps(message, separators=(",", ":")))

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"method": method, "params": params})

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._connection is None and method != "initialize":
            self._connect()
        with self._pending_lock:
            request_id = self._next_id
            self._next_id += 1
            event = threading.Event()
            response: dict[str, Any] = {}
            self._pending[request_id] = (event, response)
        message: dict[str, Any] = {"id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        try:
            self._send(message)
            if not event.wait(self.request_timeout):
                raise AppServerError(f"Codex App Server request timed out: {method}")
            if "error" in response:
                error = response.get("error")
                detail = error.get("message") if isinstance(error, dict) else "request failed"
                raise AppServerError(f"Codex App Server rejected {method}: {detail}")
            result = response.get("result", {})
            return dict(result) if isinstance(result, dict) else {}
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)

    def notification_marker(self) -> int:
        with self._notification_condition:
            return self._notification_sequence

    def wait_notifications(
        self,
        marker: int,
        predicate: Callable[[dict[str, Any]], bool],
        timeout: float,
    ) -> tuple[int, list[dict[str, Any]]]:
        deadline = time.monotonic() + timeout
        with self._notification_condition:
            while True:
                collected = [
                    (sequence, message)
                    for sequence, message in self._notifications
                    if sequence > marker
                ]
                if any(predicate(message) for _, message in collected):
                    return collected[-1][0], [message for _, message in collected]
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AppServerError("Codex App Server event stream timed out")
                self._notification_condition.wait(min(remaining, 1.0))


@dataclass(frozen=True)
class ThreadPage:
    threads: tuple[dict[str, Any], ...]
    next_cursor: str | None


@dataclass(frozen=True)
class TurnResult:
    text: str
    status: str
    turn_id: str


class CodexAppServer:
    """Stable protocol-backed operations used by the Telegram command registry."""

    def __init__(self, rpc: RpcTransport, cwd: str, turn_timeout: float = 3600.0) -> None:
        self.rpc = rpc
        self.cwd = cwd
        self.turn_timeout = turn_timeout
        self._active_turns: dict[str, str] = {}
        self._active_lock = threading.RLock()
        self._thread_locks: dict[str, threading.Lock] = {}
        self._token_usage: dict[str, dict[str, Any]] = {}
        self._thread_models: dict[str, str] = {}

    def _thread_lock(self, thread_id: str) -> threading.Lock:
        with self._active_lock:
            return self._thread_locks.setdefault(thread_id, threading.Lock())

    def list_threads(self, cursor: str | None = None, limit: int = 8) -> ThreadPage:
        params: dict[str, Any] = {
            "limit": limit,
            "sortKey": "updated_at",
            "sortDirection": "desc",
            "sourceKinds": ["cli", "vscode", "appServer"],
            "cwd": self.cwd,
        }
        if cursor:
            params["cursor"] = cursor
        result = self.rpc.request("thread/list", params)
        rows = result.get("data", [])
        threads = tuple(item for item in rows if isinstance(item, dict))
        next_cursor = result.get("nextCursor")
        return ThreadPage(threads, str(next_cursor) if next_cursor else None)

    def read_thread(self, thread_id: str) -> dict[str, Any]:
        result = self.rpc.request("thread/read", {"threadId": thread_id, "includeTurns": False})
        thread = result.get("thread")
        if not isinstance(thread, dict):
            raise AppServerError("Codex App Server returned no thread metadata")
        return thread

    def start_thread(self, title: str | None = None) -> dict[str, Any]:
        result = self.rpc.request(
            "thread/start",
            {
                "cwd": self.cwd,
                "approvalPolicy": "never",
                "sandbox": "danger-full-access",
                "serviceName": "homelab_assistant",
            },
        )
        thread = result.get("thread")
        if not isinstance(thread, dict) or not thread.get("id"):
            raise AppServerError("Codex App Server did not create a thread")
        if title:
            self.rename_thread(str(thread["id"]), title)
            thread["name"] = title
        return thread

    def resume_thread(self, thread_id: str, model: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {
            "threadId": thread_id,
            "cwd": self.cwd,
            "approvalPolicy": "never",
            "sandbox": "danger-full-access",
        }
        if model:
            params["model"] = model
        result = self.rpc.request("thread/resume", params)
        thread = result.get("thread")
        if not isinstance(thread, dict):
            raise AppServerError("Codex App Server could not resume the thread")
        if model:
            self._thread_models[thread_id] = model
        return thread

    def rename_thread(self, thread_id: str, title: str) -> None:
        self.rpc.request("thread/name/set", {"threadId": thread_id, "name": title})

    def fork_thread(self, thread_id: str) -> dict[str, Any]:
        thread_lock = self._thread_lock(thread_id)
        if not thread_lock.acquire(blocking=False):
            raise AppServerError("The selected Codex thread already has an active operation")
        try:
            if self._thread_is_active(self.read_thread(thread_id)):
                raise AppServerError("Stop the active Codex turn before forking this thread")
            result = self.rpc.request("thread/fork", {"threadId": thread_id})
            thread = result.get("thread")
            if not isinstance(thread, dict) or not thread.get("id"):
                raise AppServerError("Codex App Server did not fork the thread")
            return thread
        finally:
            thread_lock.release()

    def compact_thread(self, thread_id: str, progress: ProgressCallback | None = None) -> None:
        thread_lock = self._thread_lock(thread_id)
        if not thread_lock.acquire(blocking=False):
            raise AppServerError("The selected Codex thread already has an active operation")
        try:
            thread = self.read_thread(thread_id)
            if self._thread_is_active(thread):
                raise AppServerError("Stop the active Codex turn before compacting this thread")
            self._ensure_loaded(thread_id, thread)
            marker = self.rpc.notification_marker()
            self.rpc.request("thread/compact/start", {"threadId": thread_id})
            if progress:
                progress("Compacting the selected Codex thread…")

            def completed(message: dict[str, Any]) -> bool:
                params = message.get("params", {})
                if params.get("threadId") != thread_id:
                    return False
                item = params.get("item", {})
                return (
                    message.get("method") == "item/completed"
                    and item.get("type") == "contextCompaction"
                )

            deadline = time.monotonic() + self.turn_timeout
            cursor = marker
            while True:
                cursor, events = self.rpc.wait_notifications(
                    cursor,
                    lambda event: self._thread_event(thread_id, event),
                    max(0.1, deadline - time.monotonic()),
                )
                self._remember_started_turn(thread_id, events)
                self._emit_progress(thread_id, events, progress)
                if any(completed(event) for event in events):
                    return
        finally:
            with self._active_lock:
                self._active_turns.pop(thread_id, None)
            thread_lock.release()

    def status(self, thread_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        thread = dict(self.read_thread(thread_id))
        if "model" not in thread and thread_id in self._thread_models:
            thread["model"] = self._thread_models[thread_id]
        limits = self.rpc.request("account/rateLimits/read")
        return thread, dict(self._token_usage.get(thread_id, {})), limits

    def list_models(self) -> tuple[dict[str, Any], ...]:
        result = self.rpc.request("model/list", {"limit": 40, "includeHidden": False})
        return tuple(item for item in result.get("data", []) if isinstance(item, dict))

    def set_model(self, thread_id: str, model: str) -> None:
        self.resume_thread(thread_id, model=model)

    def interrupt(self, thread_id: str) -> bool:
        with self._active_lock:
            turn_id = self._active_turns.get(thread_id)
        if not turn_id:
            result = self.rpc.request("thread/read", {"threadId": thread_id, "includeTurns": True})
            thread = result.get("thread", {})
            turns = thread.get("turns", []) if isinstance(thread, dict) else []
            if isinstance(turns, list):
                for turn in reversed(turns):
                    if isinstance(turn, dict) and turn.get("status") == "inProgress":
                        candidate = turn.get("id")
                        if candidate:
                            turn_id = str(candidate)
                            break
        if not turn_id:
            return False
        self.rpc.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})
        return True

    def run_review(self, thread_id: str, progress: ProgressCallback | None = None) -> TurnResult:
        thread_lock = self._thread_lock(thread_id)
        if not thread_lock.acquire(blocking=False):
            raise AppServerError("The selected Codex thread already has an active operation")
        try:
            thread = self.read_thread(thread_id)
            if self._thread_is_active(thread):
                raise AppServerError("Stop the active Codex turn before starting review")
            self._ensure_loaded(thread_id, thread)
            marker = self.rpc.notification_marker()
            result = self.rpc.request(
                "review/start",
                {
                    "threadId": thread_id,
                    "delivery": "inline",
                    "target": {"type": "uncommittedChanges"},
                },
            )
            return self._wait_for_turn(thread_id, result, marker, progress)
        finally:
            thread_lock.release()

    def run_text(
        self, thread_id: str, text: str, progress: ProgressCallback | None = None
    ) -> TurnResult:
        with self._active_lock:
            active_turn = self._active_turns.get(thread_id)
        if active_turn:
            self.rpc.request(
                "turn/steer",
                {
                    "threadId": thread_id,
                    "expectedTurnId": active_turn,
                    "input": [{"type": "text", "text": text}],
                },
            )
            return TurnResult("Steered the active Codex turn.", "steered", active_turn)

        thread_lock = self._thread_lock(thread_id)
        if not thread_lock.acquire(blocking=False):
            raise AppServerError("A Telegram turn is already starting for this Codex thread")
        try:
            thread = self.read_thread(thread_id)
            if self._thread_is_active(thread):
                raise AppServerError(
                    "This thread is active in another Codex client; steer or stop it there first"
                )
            self._ensure_loaded(thread_id, thread)
            marker = self.rpc.notification_marker()
            result = self.rpc.request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": text}],
                    "cwd": self.cwd,
                    "approvalPolicy": "never",
                    "sandboxPolicy": {"type": "dangerFullAccess"},
                },
            )
            return self._wait_for_turn(thread_id, result, marker, progress)
        finally:
            thread_lock.release()

    def _ensure_loaded(
        self, thread_id: str, thread: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        metadata = thread or self.read_thread(thread_id)
        status = metadata.get("status", {})
        if isinstance(status, dict) and status.get("type") == "notLoaded":
            return self.resume_thread(thread_id)
        return metadata

    @staticmethod
    def _thread_is_active(thread: dict[str, Any]) -> bool:
        status = thread.get("status", {})
        return isinstance(status, dict) and status.get("type") == "active"

    def _remember_started_turn(self, thread_id: str, events: list[dict[str, Any]]) -> None:
        for event in events:
            if event.get("method") != "turn/started":
                continue
            params = event.get("params", {})
            turn = params.get("turn", {}) if isinstance(params, dict) else {}
            if isinstance(turn, dict) and turn.get("id"):
                with self._active_lock:
                    self._active_turns[thread_id] = str(turn["id"])

    def _wait_for_turn(
        self,
        thread_id: str,
        response: dict[str, Any],
        marker: int,
        progress: ProgressCallback | None,
    ) -> TurnResult:
        turn = response.get("turn", {})
        turn_id = str(turn.get("id", "")) if isinstance(turn, dict) else ""
        if not turn_id:
            raise AppServerError("Codex App Server returned no active turn")
        with self._active_lock:
            self._active_turns[thread_id] = turn_id

        def completed(message: dict[str, Any]) -> bool:
            params = message.get("params", {})
            event_turn = params.get("turn", {})
            return (
                message.get("method") == "turn/completed"
                and params.get("threadId") == thread_id
                and isinstance(event_turn, dict)
                and event_turn.get("id") == turn_id
            )

        try:
            events: list[dict[str, Any]] = []
            deadline = time.monotonic() + self.turn_timeout
            cursor = marker
            while True:
                cursor, batch = self.rpc.wait_notifications(
                    cursor,
                    lambda event: self._thread_event(thread_id, event),
                    max(0.1, deadline - time.monotonic()),
                )
                events.extend(batch)
                self._emit_progress(thread_id, events, progress)
                if any(completed(event) for event in batch):
                    break
            text, status = self._summarize_events(thread_id, turn_id, events, progress)
            return TurnResult(text, status, turn_id)
        finally:
            with self._active_lock:
                self._active_turns.pop(thread_id, None)

    def _summarize_events(
        self,
        thread_id: str,
        turn_id: str,
        events: list[dict[str, Any]],
        progress: ProgressCallback | None,
    ) -> tuple[str, str]:
        del progress
        deltas: list[str] = []
        completed_text: list[str] = []
        status = "completed"
        for event in events:
            method = str(event.get("method", ""))
            params = event.get("params", {})
            if not isinstance(params, dict) or params.get("threadId") != thread_id:
                continue
            if method == "thread/tokenUsage/updated":
                self._token_usage[thread_id] = dict(params)
            if method == "item/agentMessage/delta" and params.get("turnId") == turn_id:
                delta = params.get("delta")
                if isinstance(delta, str):
                    deltas.append(delta)
            item = params.get("item")
            if method == "item/completed" and isinstance(item, dict):
                if item.get("type") == "agentMessage":
                    text = item.get("text")
                    if isinstance(text, str):
                        completed_text.append(text)
            if method == "turn/completed":
                event_turn = params.get("turn", {})
                if isinstance(event_turn, dict) and event_turn.get("id") == turn_id:
                    status = str(event_turn.get("status", "completed"))
        final = "".join(deltas).strip() or "\n\n".join(completed_text).strip()
        if not final:
            final = "Codex turn finished without a user-facing message."
        return redact_telegram(final), status

    def _thread_event(self, thread_id: str, event: dict[str, Any]) -> bool:
        params = event.get("params", {})
        if not isinstance(params, dict):
            return False
        if params.get("threadId") == thread_id:
            return True
        with self._active_lock:
            active_turn = self._active_turns.get(thread_id)
        return bool(active_turn and params.get("turnId") == active_turn)

    def _emit_progress(
        self,
        thread_id: str,
        events: list[dict[str, Any]],
        progress: ProgressCallback | None,
    ) -> None:
        progress_lines: list[str] = []
        agent_chunks: list[str] = []
        for event in events:
            method = str(event.get("method", ""))
            params = event.get("params", {})
            if not isinstance(params, dict) or not self._thread_event(thread_id, event):
                continue
            if method == "thread/tokenUsage/updated":
                self._token_usage[thread_id] = dict(params)
            if method == "model/rerouted" and isinstance(params.get("toModel"), str):
                self._thread_models[thread_id] = str(params["toModel"])
            if method == "item/agentMessage/delta" and isinstance(params.get("delta"), str):
                agent_chunks.append(str(params["delta"]))
            if method == "turn/plan/updated":
                plan = params.get("plan", [])
                count = len(plan) if isinstance(plan, list) else 0
                progress_lines.append(f"Plan updated ({count} steps).")
            if method == "error":
                progress_lines.append("Codex reported a failure.")
            item = params.get("item")
            if isinstance(item, dict):
                summary = _event_summary(method, item)
                if summary and summary not in progress_lines:
                    progress_lines.append(summary)
        if progress:
            if agent_chunks:
                progress(redact_telegram("Codex:\n" + "".join(agent_chunks)[-3000:]))
            elif progress_lines:
                progress("\n".join(progress_lines[-8:]))


_SECRET_ASSIGNMENT = re.compile(
    r"(?im)^\s*[A-Z0-9_.-]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API[-_]KEY|ACCESS[-_]KEY|"
    r"PRIVATE[-_]KEY|CLIENT[-_]KEY|AUTH|CREDENTIAL|COOKIE|CERTIFICATE[-_]DATA)"
    r"[A-Z0-9_.-]*\s*[:=].*$"
)
_SECRET_JSON = re.compile(
    r'(?im)^\s*"[^"]*(?:token|secret|password|passwd|access_key|private_key|client_key|auth|credential|cookie)[^"]*"\s*:\s*.*$'
)
_BEARER = re.compile(r"(?i)\b(?:bearer|token)\s+[A-Za-z0-9._~+/-]{12,}")
_KNOWN_TOKEN = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[opusr]_[A-Za-z0-9]{20,}|"
    r"\d{6,12}:[A-Za-z0-9_-]{30,})\b"
)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.DOTALL
)
_TELEGRAM_URL = re.compile(r"https://api\.telegram\.org/bot[^/\s]+", re.IGNORECASE)


def redact_telegram(text: str, maximum: int = 12000) -> str:
    """Best-effort output boundary; repository safety instructions remain authoritative."""
    redacted = _PRIVATE_KEY.sub("[REDACTED PRIVATE KEY]", text)
    redacted = _SECRET_ASSIGNMENT.sub("[REDACTED SECRET VALUE]", redacted)
    redacted = _SECRET_JSON.sub("[REDACTED SECRET VALUE]", redacted)
    redacted = _BEARER.sub("[REDACTED CREDENTIAL]", redacted)
    redacted = _KNOWN_TOKEN.sub("[REDACTED CREDENTIAL]", redacted)
    redacted = _JWT.sub("[REDACTED CREDENTIAL]", redacted)
    redacted = _TELEGRAM_URL.sub("[REDACTED TELEGRAM URL]", redacted)
    return redacted[:maximum] + ("\n[output truncated]" if len(redacted) > maximum else "")


def _event_summary(method: str, item: dict[str, Any]) -> str | None:
    item_type = str(item.get("type", ""))
    if item_type == "commandExecution":
        status = str(item.get("status", "completed"))
        return f"Command {status}."
    if item_type == "fileChange":
        changes = item.get("changes", [])
        count = len(changes) if isinstance(changes, list) else 1
        return f"File changes prepared: {count}."
    if item_type in {"mcpToolCall", "dynamicToolCall", "collabToolCall"}:
        status = str(item.get("status", "completed"))
        return f"Tool call {status}."
    if item_type == "contextCompaction":
        return "Context compaction completed."
    if item_type in {"plan", "planUpdate"}:
        return "Plan updated."
    if item_type == "webSearch":
        return "Web search completed."
    if item_type == "reasoning":
        return None
    if method == "item/completed" and item_type in {"error", "turnError"}:
        return "Codex reported a failure."
    return None
