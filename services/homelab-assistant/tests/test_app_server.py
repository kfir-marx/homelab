from __future__ import annotations

import threading
from typing import Any

from homelab_assistant.app_server import CodexAppServer, redact_telegram


class FakeRpc:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.events: list[dict[str, Any]] = []

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((method, params))
        if method == "thread/read":
            return {"thread": {"id": "thr-1", "status": {"type": "notLoaded"}}}
        if method == "thread/list":
            return {
                "data": [
                    {
                        "id": "thr-1",
                        "name": "Live-compatible thread",
                        "source": "appServer",
                        "status": {"type": "idle"},
                    }
                ],
                "nextCursor": "cursor-2",
            }
        if method == "thread/start":
            return {"thread": {"id": "thr-new", "status": {"type": "idle"}}}
        if method == "thread/resume":
            return {"thread": {"id": "thr-1", "status": {"type": "idle"}}}
        if method == "thread/compact/start":
            if not self.events:
                self.events = [
                    {
                        "method": "item/completed",
                        "params": {
                            "threadId": "thr-1",
                            "item": {"type": "contextCompaction"},
                        },
                    }
                ]
            return {}
        if method == "turn/start":
            self.events = [
                {
                    "method": "item/agentMessage/delta",
                    "params": {"threadId": "thr-1", "turnId": "turn-1", "delta": "done"},
                },
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "thr-1",
                        "turn": {"id": "turn-1", "status": "completed"},
                    },
                },
            ]
            return {"turn": {"id": "turn-1", "status": "inProgress"}}
        return {}

    def notification_marker(self) -> int:
        return 0

    def wait_notifications(
        self, marker: int, predicate: object, timeout: float
    ) -> tuple[int, list[dict[str, Any]]]:
        return marker + len(self.events), self.events


def test_compact_invokes_exact_stable_protocol_method_once() -> None:
    rpc = FakeRpc()
    codex = CodexAppServer(rpc, "/home/kfir/repos/homelab")
    codex.compact_thread("thr-1")
    compact = [call for call in rpc.calls if call[0] == "thread/compact/start"]
    assert compact == [("thread/compact/start", {"threadId": "thr-1"})]


def test_turn_uses_never_approval_and_danger_full_access() -> None:
    rpc = FakeRpc()
    codex = CodexAppServer(rpc, "/home/kfir/repos/homelab")
    result = codex.run_text("thr-1", "run tests")
    assert result.text == "done"
    turn_call = next(call for call in rpc.calls if call[0] == "turn/start")
    assert turn_call[1] is not None
    assert turn_call[1]["approvalPolicy"] == "never"
    assert turn_call[1]["sandboxPolicy"] == {"type": "dangerFullAccess"}


def test_thread_start_and_resume_use_current_sandbox_mode_spelling() -> None:
    rpc = FakeRpc()
    codex = CodexAppServer(rpc, "/home/kfir/repos/homelab")

    codex.start_thread()
    codex.resume_thread("thr-1")

    start = next(params for method, params in rpc.calls if method == "thread/start")
    resume = next(params for method, params in rpc.calls if method == "thread/resume")
    assert start is not None and start["sandbox"] == "danger-full-access"
    assert resume is not None and resume["sandbox"] == "danger-full-access"


def test_thread_list_uses_current_schema_and_parses_live_response_shape() -> None:
    rpc = FakeRpc()
    codex = CodexAppServer(rpc, "/home/kfir/repos/homelab")

    page = codex.list_threads(limit=8)

    assert page.threads[0]["id"] == "thr-1"
    assert page.next_cursor == "cursor-2"
    params = next(params for method, params in rpc.calls if method == "thread/list")
    assert params == {
        "limit": 8,
        "sortKey": "updated_at",
        "sortDirection": "desc",
        "sourceKinds": ["cli", "vscode", "appServer"],
        "cwd": "/home/kfir/repos/homelab",
    }


def test_thread_creation_parses_live_response_and_uses_name_set() -> None:
    rpc = FakeRpc()
    codex = CodexAppServer(rpc, "/home/kfir/repos/homelab")

    thread = codex.start_thread("test")

    assert thread == {"id": "thr-new", "status": {"type": "idle"}, "name": "test"}
    assert ("thread/name/set", {"threadId": "thr-new", "name": "test"}) in rpc.calls


def test_output_redaction_removes_secret_assignments_and_private_keys() -> None:
    text = "API_TOKEN=supersecretvalue\n-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"
    redacted = redact_telegram(text)
    assert "supersecretvalue" not in redacted
    assert "BEGIN PRIVATE KEY" not in redacted


def test_output_is_bounded_and_known_tokens_are_redacted() -> None:
    secret = "sk-" + "abcdefghijklmnopqrstuvwxyz123456"
    redacted = redact_telegram(secret + "\n" + ("x" * 200), maximum=80)
    assert secret not in redacted
    assert len(redacted) <= 99
    assert redacted.endswith("[output truncated]")


def test_compaction_progress_caches_token_usage_for_status() -> None:
    rpc = FakeRpc()
    rpc.events = [
        {
            "method": "thread/tokenUsage/updated",
            "params": {
                "threadId": "thr-1",
                "tokenUsage": {"total": {"totalTokens": 42}},
            },
        },
        {
            "method": "item/completed",
            "params": {"threadId": "thr-1", "item": {"type": "contextCompaction"}},
        },
    ]
    progress: list[str] = []
    codex = CodexAppServer(rpc, "/home/kfir/repos/homelab")
    codex.compact_thread("thr-1", progress.append)
    _, usage, _ = codex.status("thr-1")
    assert usage["tokenUsage"]["total"]["totalTokens"] == 42
    assert any("compaction completed" in item.casefold() for item in progress)


class ConcurrentRpc(FakeRpc):
    def __init__(self) -> None:
        super().__init__()
        self.turn_started = threading.Event()
        self.release = threading.Event()

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if method == "thread/read":
            self.calls.append((method, params))
            return {"thread": {"id": "thr-1", "status": {"type": "idle"}}}
        if method == "turn/start":
            self.calls.append((method, params))
            self.turn_started.set()
            return {"turn": {"id": "turn-1", "status": "inProgress"}}
        if method == "turn/steer":
            self.calls.append((method, params))
            return {}
        return super().request(method, params)

    def wait_notifications(
        self, marker: int, predicate: object, timeout: float
    ) -> tuple[int, list[dict[str, Any]]]:
        assert self.release.wait(2)
        return (
            marker + 1,
            [
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "thr-1",
                        "turn": {"id": "turn-1", "status": "completed"},
                    },
                },
            ],
        )


def test_simultaneous_input_steers_one_active_turn_instead_of_starting_another() -> None:
    rpc = ConcurrentRpc()
    codex = CodexAppServer(rpc, "/home/kfir/repos/homelab")
    worker = threading.Thread(target=codex.run_text, args=("thr-1", "first"))
    worker.start()
    assert rpc.turn_started.wait(1)

    result = codex.run_text("thr-1", "second")
    rpc.release.set()
    worker.join(2)

    assert result.status == "steered"
    assert [method for method, _ in rpc.calls].count("turn/start") == 1
    assert [method for method, _ in rpc.calls].count("turn/steer") == 1


def test_stop_resolves_an_active_turn_after_bridge_restart() -> None:
    class ActiveRpc(FakeRpc):
        def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
            self.calls.append((method, params))
            if method == "thread/read":
                return {
                    "thread": {
                        "id": "thr-1",
                        "turns": [
                            {"id": "turn-old", "status": "completed"},
                            {"id": "turn-active", "status": "inProgress"},
                        ],
                    }
                }
            return {}

    rpc = ActiveRpc()
    codex = CodexAppServer(rpc, "/home/kfir/repos/homelab")

    assert codex.interrupt("thr-1")
    assert (
        "turn/interrupt",
        {"threadId": "thr-1", "turnId": "turn-active"},
    ) in rpc.calls
