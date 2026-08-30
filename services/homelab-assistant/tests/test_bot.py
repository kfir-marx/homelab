from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import SecretStr

from homelab_assistant.app_server import AppServerError, ThreadPage, TurnResult
from homelab_assistant.bot import AssistantBot, CodexClient
from homelab_assistant.bridge_state import BridgeState
from homelab_assistant.config import Settings
from homelab_assistant.switching import SwitchCoordinator, SwitchResult


class FakeCodex:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.threads = (
            {
                "id": "thr-vscode",
                "name": "Fix storage",
                "source": "vscode",
                "cwd": "/home/kfir/repos/homelab",
                "gitInfo": {"branch": "main"},
                "updatedAt": 1_800_000_000,
                "status": {"type": "idle"},
                "model": "gpt-test",
            },
            {
                "id": "thr-app",
                "preview": "Telegram session",
                "source": "appServer",
                "cwd": "/home/kfir/repos/homelab",
                "updatedAt": 1_799_000_000,
                "status": {"type": "notLoaded"},
            },
        )

    def list_threads(self, cursor: str | None = None, limit: int = 8) -> ThreadPage:
        self.calls.append(("list_threads", (cursor, limit)))
        return ThreadPage(self.threads, "next-cursor" if cursor is None else None)

    def read_thread(self, thread_id: str) -> dict[str, Any]:
        self.calls.append(("read_thread", thread_id))
        return dict(
            next((item for item in self.threads if item["id"] == thread_id), self.threads[0])
        )

    def start_thread(self, title: str | None = None) -> dict[str, Any]:
        self.calls.append(("start_thread", title))
        return {"id": "thr-new", "name": title or "Untitled", "sourceKind": "appServer"}

    def resume_thread(self, thread_id: str, model: str | None = None) -> dict[str, Any]:
        self.calls.append(("resume_thread", (thread_id, model)))
        return {"id": thread_id, "name": "Selected", "sourceKind": "vscode"}

    def rename_thread(self, thread_id: str, title: str) -> None:
        self.calls.append(("rename_thread", (thread_id, title)))

    def fork_thread(self, thread_id: str) -> dict[str, Any]:
        self.calls.append(("fork_thread", thread_id))
        return {"id": "thr-fork", "name": "Fork", "sourceKind": "appServer"}

    def compact_thread(self, thread_id: str, progress: object = None) -> None:
        self.calls.append(("compact_thread", thread_id))

    def status(self, thread_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        self.calls.append(("status", thread_id))
        return (
            dict(self.threads[0]),
            {"tokenUsage": {"total": {"totalTokens": 12}, "modelContextWindow": 100}},
            {"rateLimits": {"primary": {"usedPercent": 5, "resetsAt": 1_800_000_000}}},
        )

    def list_models(self) -> tuple[dict[str, Any], ...]:
        self.calls.append(("list_models", None))
        return ({"model": "gpt-test", "displayName": "GPT Test"},)

    def set_model(self, thread_id: str, model: str) -> None:
        self.calls.append(("set_model", (thread_id, model)))

    def interrupt(self, thread_id: str) -> bool:
        self.calls.append(("interrupt", thread_id))
        return True

    def run_review(self, thread_id: str, progress: object = None) -> TurnResult:
        self.calls.append(("run_review", thread_id))
        return TurnResult("review", "completed", "turn-review")

    def run_text(self, thread_id: str, text: str, progress: object = None) -> TurnResult:
        self.calls.append(("run_text", (thread_id, text)))
        return TurnResult("answer", "completed", "turn-1")


class FakeSwitcher:
    def __init__(self) -> None:
        self.modes: list[str] = []

    def status(self) -> str:
        return "VM 402=running; VM 502=stopped; gpu-2 Ready=true, cordoned=false"

    def switch(self, mode: str) -> SwitchResult:
        self.modes.append(mode)
        return SwitchResult(True, f"completed {mode}")


def settings(tmp_path: Path) -> Settings:
    return Settings(
        telegram_token=SecretStr("unit-test-placeholder"),
        telegram_allowed_user_id=123,
        telegram_allowed_chat_id=456,
        state_database_path=str(tmp_path / "bridge.db"),
        administrator_lease_seconds=60,
    )


def update(
    text: str, *, user_id: int = 123, chat_id: int = 456, chat_type: str = "private"
) -> dict[str, Any]:
    return {
        "update_id": 1,
        "message": {
            "from": {"id": user_id},
            "chat": {"id": chat_id, "type": chat_type},
            "text": text,
        },
    }


def callback(data: str, *, user_id: int = 123, chat_id: int = 456) -> dict[str, Any]:
    return {
        "update_id": 2,
        "callback_query": {
            "id": "callback-id",
            "from": {"id": user_id},
            "data": data,
            "message": {"chat": {"id": chat_id, "type": "private"}},
        },
    }


def make_bot(
    tmp_path: Path, switcher: FakeSwitcher | None = None
) -> tuple[AssistantBot, FakeCodex]:
    configured = settings(tmp_path)
    fake = FakeCodex()
    bot = AssistantBot(
        configured,
        cast(CodexClient, fake),
        BridgeState(configured.state_database_path),
        cast(SwitchCoordinator, switcher) if switcher else None,
    )
    return bot, fake


def unlock(bot: AssistantBot) -> None:
    assert "unlocked" in bot.process(update("/tg unlock"))[0].text


def test_exact_private_identity_and_forwarded_context_are_rejected(tmp_path: Path) -> None:
    bot, _ = make_bot(tmp_path)
    assert bot.process(update("/tg help", user_id=999)) == []
    assert bot.process(update("/tg help", chat_id=999)) == []
    assert bot.process(update("/tg help", chat_type="group")) == []
    forwarded = update("/tg help")
    forwarded["message"]["forward_origin"] = {"type": "user"}
    assert bot.process(forwarded) == []
    nonce = bot.process(update("/tg sessions"))[0].buttons[0][1]
    assert bot.process(callback(nonce, user_id=999)) == []
    assert bot.process(callback(nonce, chat_id=999)) == []


def test_identity_rejections_log_only_safe_reason_classes(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    bot, _ = make_bot(tmp_path)
    caplog.set_level(logging.WARNING)

    assert bot.process(update("/tg help", user_id=999)) == []
    assert "actor identity mismatch" in caplog.text
    assert "999" not in caplog.text
    assert "123" not in caplog.text
    assert "456" not in caplog.text


def test_tg_current_routes_to_transport_control(tmp_path: Path) -> None:
    bot, fake = make_bot(tmp_path)
    bot.state.select_thread(123, "thr-vscode")
    reply = bot.process(update("/tg current"))[0]
    assert "thr-vscode" in reply.text and "Administrator lease: locked" in reply.text
    assert not any(call[0] == "run_text" for call in fake.calls)


def test_tg_sessions_lists_sanitized_cross_client_threads_and_selects_by_nonce(
    tmp_path: Path,
) -> None:
    bot, fake = make_bot(tmp_path)
    reply = bot.process(update("/tg sessions"))[0]
    assert "vscode" in reply.text and "Telegram/App Server" in reply.text
    assert "main" in reply.text and reply.buttons
    selected = bot.process(callback(reply.buttons[0][1]))[0]
    assert "thr-vscode" in selected.text
    assert bot.state.selected_thread(123) == "thr-vscode"
    assert ("resume_thread", ("thr-vscode", None)) in fake.calls
    assert "already used" in bot.process(callback(reply.buttons[0][1]))[0].text


def test_callback_nonce_expires_and_cannot_cross_namespaces(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    now = [1000.0]
    monkeypatch.setattr("homelab_assistant.bridge_state.time.time", lambda: now[0])
    bot, _ = make_bot(tmp_path)
    button = bot.process(update("/tg sessions"))[0].buttons[0][1]
    _, nonce = button.split(":", 1)
    assert "Invalid" in bot.process(callback(f"ops:{nonce}"))[0].text

    button = bot.process(update("/tg sessions"))[0].buttons[0][1]
    now[0] += 121
    assert "expired" in bot.process(callback(button))[0].text


def test_ops_routes_only_to_deterministic_switching_with_confirmation(tmp_path: Path) -> None:
    switcher = FakeSwitcher()
    bot, fake = make_bot(tmp_path, switcher)
    unlock(bot)
    gaming = bot.process(update("/ops gaming"))[0]
    assert "Exact transition" in gaming.text
    assert bot.process(callback(gaming.buttons[0][1]))[0].text == "completed gaming"
    k8s = bot.process(update("/ops k8s"))[0]
    assert bot.process(callback(k8s.buttons[0][1]))[0].text == "completed kubernetes"
    assert switcher.modes == ["gaming", "kubernetes"]
    assert not any(call[0] == "run_text" for call in fake.calls)


def test_completed_switch_is_reported_when_final_audit_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    switcher = FakeSwitcher()
    bot, _ = make_bot(tmp_path, switcher)
    unlock(bot)
    gaming = bot.process(update("/ops gaming"))[0]
    original_audit = bot.state.audit

    def fail_completed_audit(
        user_id: int, operation: str, outcome: str, thread_id: str | None = None
    ) -> None:
        if outcome == "completed":
            raise OSError("simulated audit write failure")
        original_audit(user_id, operation, outcome, thread_id)

    monkeypatch.setattr(bot.state, "audit", fail_completed_audit)

    replies = bot.process(callback(gaming.buttons[0][1]))

    assert [reply.text for reply in replies] == ["completed gaming"]
    assert switcher.modes == ["gaming"]


def test_status_invokes_codex_status_adapter_not_prompt(tmp_path: Path) -> None:
    bot, fake = make_bot(tmp_path)
    bot.state.select_thread(123, "thr-vscode")
    reply = bot.process(update("/status"))[0]
    assert "Context usage: 12/100" in reply.text and "Rate limits" in reply.text
    assert ("status", "thr-vscode") in fake.calls
    assert not any(call[0] == "run_text" for call in fake.calls)


def test_compact_uses_codex_compaction_adapter(tmp_path: Path) -> None:
    bot, fake = make_bot(tmp_path)
    bot.state.select_thread(123, "thr-vscode")
    unlock(bot)
    reply = bot.process(update("/compact"))[0]
    assert "compaction completed" in reply.text
    assert fake.calls.count(("compact_thread", "thr-vscode")) == 1
    assert not any(call[0] == "run_text" for call in fake.calls)


def test_fork_switches_persistent_selection(tmp_path: Path) -> None:
    bot, fake = make_bot(tmp_path)
    bot.state.select_thread(123, "thr-vscode")
    unlock(bot)
    reply = bot.process(update("/fork"))[0]
    assert "thr-fork" in reply.text
    assert bot.state.selected_thread(123) == "thr-fork"
    assert ("fork_thread", "thr-vscode") in fake.calls


def test_unknown_and_unsupported_root_commands_are_never_prompts(tmp_path: Path) -> None:
    bot, fake = make_bot(tmp_path)
    bot.state.select_thread(123, "thr-vscode")
    unlock(bot)
    reply = bot.process(update("/goal build it"))[0]
    assert "not supported by this Telegram Codex client" in reply.text
    assert not any(call[0] == "run_text" for call in fake.calls)


def test_ordinary_message_uses_selected_codex_thread_and_requires_lease(tmp_path: Path) -> None:
    bot, fake = make_bot(tmp_path)
    assert (
        "No Codex thread" in bot.process(update("hello"))[0].text
        or "locked" in bot.process(update("hello"))[0].text
    )
    bot.state.select_thread(123, "thr-vscode")
    assert "locked" in bot.process(update("hello"))[0].text
    unlock(bot)
    assert bot.process(update("hello"))[0].text == "answer"
    assert ("run_text", ("thr-vscode", "hello")) in fake.calls


def test_service_restart_returns_lease_to_locked_but_keeps_selection(tmp_path: Path) -> None:
    bot, _ = make_bot(tmp_path)
    bot.state.select_thread(123, "thr-vscode")
    unlock(bot)
    restarted, _ = make_bot(tmp_path)
    assert restarted.state.selected_thread(123) == "thr-vscode"
    assert "locked" in restarted.process(update("hello"))[0].text


def test_skill_reference_is_preserved_as_codex_input(tmp_path: Path) -> None:
    bot, fake = make_bot(tmp_path)
    bot.state.select_thread(123, "thr-vscode")
    unlock(bot)

    bot.process(update("$skill-name inspect the manifests"))

    assert ("run_text", ("thr-vscode", "$skill-name inspect the manifests")) in fake.calls


def test_administrator_lease_expires(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    now = [1000.0]
    monkeypatch.setattr("homelab_assistant.bot.time.monotonic", lambda: now[0])
    bot, _ = make_bot(tmp_path)
    bot.state.select_thread(123, "thr-vscode")
    unlock(bot)
    now[0] += 61

    assert "locked" in bot.process(update("hello"))[0].text


def test_secret_output_is_redacted_and_never_persisted_or_logged(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    secret = "sk-" + "abcdefghijklmnopqrstuvwxyz123456"

    class SecretCodex(FakeCodex):
        def run_text(self, thread_id: str, text: str, progress: object = None) -> TurnResult:
            return TurnResult(f"credential={secret}", "completed", "turn-secret")

    configured = settings(tmp_path)
    state = BridgeState(configured.state_database_path)
    fake = SecretCodex()
    bot = AssistantBot(configured, cast(CodexClient, fake), state)
    state.select_thread(123, "thr-vscode")
    unlock(bot)
    reply = bot.process(update("show it"))[0]
    assert secret not in reply.text
    assert secret.encode() not in Path(configured.state_database_path).read_bytes()

    class FailingCodex(SecretCodex):
        def run_text(self, thread_id: str, text: str, progress: object = None) -> TurnResult:
            raise AppServerError(f"transport failed with {secret}")

    failing = AssistantBot(configured, cast(CodexClient, FailingCodex()), state)
    failing.state.select_thread(123, "thr-vscode")
    unlock(failing)
    caplog.set_level(logging.WARNING)
    failing.process(update("fail"))
    assert secret not in caplog.text
