from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import SecretStr

from homelab_assistant import sessions as sessions_module
from homelab_assistant.bot import AssistantBot, _namespace_job_update, effective_prompt_budget
from homelab_assistant.clients import (
    Completion,
    ExternalAiClient,
    JobAssistantClient,
    LlmClient,
    _telegram_chunks,
)
from homelab_assistant.config import Settings
from homelab_assistant.sessions import SessionStore
from homelab_assistant.tools import HandoffRequest


class FakeLlm:
    def __init__(self, prompt_tokens: int = 20) -> None:
        self.requests: list[list[dict[str, str]]] = []
        self.prompt_tokens = prompt_tokens

    def complete(self, messages: list[dict[str, str]], max_tokens: int) -> Completion:
        self.requests.append(messages)
        if "exactly these headings" in messages[-1]["content"]:
            content = "\n".join(
                f"## {name}\nvalue"
                for name in (
                    "Objective",
                    "Verified facts",
                    "Decisions made",
                    "Current state",
                    "Pending work",
                    "Safety constraints",
                    "Important identifiers",
                    "Uncertainties",
                )
            )
        else:
            content = f"answer-{len(self.requests)}"
        return Completion(content, self.prompt_tokens, 5)

    def ready(self) -> bool:
        return True


class FailingSummaryLlm(FakeLlm):
    def complete(self, messages: list[dict[str, str]], max_tokens: int) -> Completion:
        if "exactly these headings" in messages[-1]["content"]:
            raise ValueError("summary failed")
        return super().complete(messages, max_tokens)


class HandoffLlm(FakeLlm):
    def complete_with_tools(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        current_user_text: str,
    ) -> Completion:
        self.requests.append(messages)
        return Completion(
            "Preparing an external preview.",
            self.prompt_tokens,
            5,
            HandoffRequest("gpt-5.6-sol", "high"),
        )


class FakeJobAssistant:
    def __init__(self, pending: bool) -> None:
        self.pending = pending
        self.updates: list[dict[str, object]] = []

    def has_pending(self, user_id: int, chat_id: int) -> bool:
        return self.pending

    def route(self, routed: dict[str, object]) -> list[dict[str, object]]:
        self.updates.append(routed)
        return [{"chat_id": 123, "text": "job reply", "buttons": [["Apply", "apply:id"]]}]


class FakeExternalAi:
    def submit(
        self, prompt: str, model: str, reasoning: str, idempotency_key: str
    ) -> dict[str, object]:
        return {
            "job_id": "X1234567",
            "model": model,
            "reasoning_effort": reasoning,
            "status": "queued",
        }

    def get(self, job_id: str) -> dict[str, object]:
        return {
            "job_id": job_id,
            "model": "gpt-5.6-sol",
            "reasoning_effort": "high",
            "status": "completed",
            "result": "external result",
            "usage": {"input_tokens": 10, "output_tokens": 4},
        }


def settings(tmp_path: Path) -> Settings:
    return Settings(
        telegram_token=SecretStr("unit-test-placeholder"),  # noqa: S106
        telegram_allowed_user_ids=frozenset({123}),
        llm_api_key=SecretStr("unit-test-placeholder"),
        session_database_url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'sessions.db'}"),
    )


def update(user_id: int, text: str, chat_type: str = "private") -> dict[str, object]:
    return {
        "update_id": 1,
        "message": {
            "from": {"id": user_id},
            "chat": {"id": user_id, "type": chat_type},
            "text": text,
        },
    }


def callback(user_id: int, data: str) -> dict[str, object]:
    return {
        "update_id": 2,
        "callback_query": {
            "from": {"id": user_id},
            "data": data,
            "message": {"chat": {"id": user_id, "type": "private"}},
        },
    }


def make_bot(tmp_path: Path, fake: FakeLlm | None = None) -> AssistantBot:
    configured = settings(tmp_path)
    return AssistantBot(
        configured,
        cast(LlmClient, fake or FakeLlm()),
        SessionStore(configured.session_database_url.get_secret_value()),
    )


def test_unauthorized_and_group_updates_are_silently_ignored(tmp_path: Path) -> None:
    bot = make_bot(tmp_path)
    assert bot.process(update(999, "hello")) == []
    assert bot.process(update(123, "hello", "group")) == []


def test_sessions_persist_restart_and_are_owner_scoped(tmp_path: Path) -> None:
    bot = make_bot(tmp_path)
    first = bot.process(update(123, "/new Persistent topic"))[0]
    session_id = first.text.split()[3].rstrip(":")
    assert len(session_id) == 6
    assert bot.process(update(123, "first"))[0].text.startswith("answer")
    restarted = make_bot(tmp_path)
    assert session_id in restarted.process(update(123, "/history"))[0].text
    try:
        restarted.store.get(session_id, 999)
    except KeyError:
        pass
    else:
        raise AssertionError("session leaked across owners")


def test_session_ids_retry_collisions_and_are_case_insensitive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    characters = iter("000000000000ABCDEF")
    monkeypatch.setattr(sessions_module.secrets, "choice", lambda _alphabet: next(characters))
    store = SessionStore(f"sqlite+pysqlite:///{tmp_path / 'collision.db'}")
    first = store.create(123)
    second = store.create(123)
    assert first.human_id == "000000" and second.human_id == "ABCDEF"
    assert store.get("abcdef", 123).id == second.id


def test_continue_is_sticky_and_rename_archive_delete_are_confirmed(tmp_path: Path) -> None:
    bot = make_bot(tmp_path)
    first = bot.store.create(123, "First")
    second = bot.store.create(123, "Second")
    bot.process(update(123, f"/continue {first.human_id}"))
    bot.process(update(123, "/rename Renamed"))
    assert bot.store.active(123).id == first.id  # type: ignore[union-attr]
    bot.process(update(123, "ordinary turn"))
    assert len(bot.store.messages(first.id)) == 2
    bot.process(update(123, f"/archive {second.human_id}"))
    delete_reply = bot.process(update(123, f"/delete {second.human_id}"))[0]
    assert delete_reply.buttons
    bot.process(callback(123, "session:delete:confirm"))
    assert bot.store.get(second.id, 123).status == "deleted"


def test_context_thresholds_use_effective_token_budget(tmp_path: Path) -> None:
    fake = FakeLlm(prompt_tokens=6000)
    bot = make_bot(tmp_path, fake)
    budget = effective_prompt_budget(bot.settings)
    assert budget == 6656
    warned = bot.process(update(123, "hello"))[0]
    assert "above 80%" in warned.text
    active = bot.store.active(123)
    assert active
    blocked = bot.process(update(123, "more"))[0]
    assert "90% safety limit" in blocked.text


def test_context_trimming_keeps_complete_turns(tmp_path: Path) -> None:
    configured = settings(tmp_path).model_copy(
        update={
            "model_context_tokens": 900,
            "max_output_tokens": 100,
            "fixed_prompt_overhead_tokens": 100,
        }
    )
    store = SessionStore(configured.session_database_url.get_secret_value())
    session = store.create(123)
    for index in range(5):
        store.append(
            session.id,
            "user",
            f"user-{index}-" + "u" * 220,
            provider="telegram",
            model="human",
        )
        store.append(
            session.id,
            "assistant",
            f"assistant-{index}-" + "a" * 220,
            provider="vllm",
            model="local",
        )
    bot = AssistantBot(configured, cast(LlmClient, FakeLlm()), store)
    context = bot._context(session, "next")
    roles = [item["role"] for item in context[1:-1]]
    assert roles and len(roles) % 2 == 0
    assert all(
        roles[index : index + 2] == ["user", "assistant"] for index in range(0, len(roles), 2)
    )
    assert "user-0" not in "\n".join(item["content"] for item in context)


def test_compaction_creates_linked_child_only_after_accept(tmp_path: Path) -> None:
    bot = make_bot(tmp_path)
    bot.process(update(123, "Discuss storage safety"))
    parent = bot.store.active(123)
    assert parent
    preview = bot.process(update(123, "/compact"))[0]
    assert "Compaction preview" in preview.text
    assert bot.store.active(123).id == parent.id  # type: ignore[union-attr]
    accepted = bot.process(callback(123, "session:compact:accept"))[0]
    child = bot.store.active(123)
    assert child and child.id != parent.id and child.parent_session_id == parent.id
    assert bot.store.get(parent.id, 123).status == "compacted"
    assert "Activated" in accepted.text


def test_compaction_retry_cancel_and_generation_failure_leave_source_active(
    tmp_path: Path,
) -> None:
    bot = make_bot(tmp_path)
    source = bot.store.create(123, "Source")
    bot.store.append(source.id, "user", "context", provider="telegram", model="human")
    bot.process(update(123, "/compact"))
    retried = bot.process(callback(123, "session:compact:retry"))[0]
    assert "Compaction preview" in retried.text
    bot.process(callback(123, "session:cancel"))
    assert bot.store.active(123).id == source.id  # type: ignore[union-attr]

    failed = make_bot(tmp_path, FailingSummaryLlm())
    response = failed.process(update(123, "/compact"))[0]
    assert "failed" in response.text
    assert failed.store.active(123).id == source.id  # type: ignore[union-attr]


def test_handover_validation_preview_and_cancel(tmp_path: Path) -> None:
    bot = make_bot(tmp_path)
    assert "Unknown model" in bot.process(update(123, "/handover arbitrary high"))[0].text
    preview = bot.process(update(123, "/handover sol max"))[0]
    assert "gpt-5.6-sol" in preview.text and "Approximate size" in preview.text
    cancelled = bot.process(callback(123, "session:cancel"))[0]
    assert "no state" in cancelled.text


def test_prompt_authorized_model_handoff_uses_existing_preview_flow(tmp_path: Path) -> None:
    bot = make_bot(tmp_path, HandoffLlm())
    preview = bot.process(update(123, "Please hand this session over to external AI"))[0]
    assert "External handover preview" in preview.text
    assert preview.buttons == (("Confirm", "handover:confirm"), ("Cancel", "session:cancel"))
    pending = bot.store.pending(123)
    assert pending and pending[0] == "handover"


def test_confirmed_handover_completes_asynchronously_with_provenance(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    store = SessionStore(configured.session_database_url.get_secret_value())
    bot = AssistantBot(
        configured,
        cast(LlmClient, FakeLlm()),
        store,
        cast(ExternalAiClient, FakeExternalAi()),
    )
    bot.process(update(123, "Plan safely"))
    active = store.active(123)
    assert active
    bot.process(update(123, "/handover sol high"))
    queued = bot.process(callback(123, "handover:confirm"))[0]
    assert "X1234567" in queued.text
    completed = bot.poll_external()[0]
    assert "external result" in completed.text
    message = store.messages(active.id)[-1]
    assert (message.provider, message.model, message.reasoning, message.job_id) == (
        "codex",
        "gpt-5.6-sol",
        "high",
        "X1234567",
    )


def test_pending_job_conversation_precedes_general_chat_and_callbacks_are_namespaced(
    tmp_path: Path,
) -> None:
    configured = settings(tmp_path)
    fake_llm = FakeLlm()
    fake_job = FakeJobAssistant(pending=True)
    bot = AssistantBot(
        configured,
        cast(LlmClient, fake_llm),
        SessionStore(configured.session_database_url.get_secret_value()),
        job_assistant=cast(JobAssistantClient, fake_job),
    )
    reply = bot.process(update(123, "pending contact answer"))[0]
    assert reply.text == "job reply" and reply.buttons[0][1] == "job:apply:id"
    assert fake_llm.requests == []
    bot.process(callback(123, "job:apply:id"))
    assert fake_job.updates[-1]["callback_query"]["data"] == "apply:id"  # type: ignore[index]


def test_unrelated_text_stays_in_general_session(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    fake_llm = FakeLlm()
    fake_job = FakeJobAssistant(pending=False)
    bot = AssistantBot(
        configured,
        cast(LlmClient, fake_llm),
        SessionStore(configured.session_database_url.get_secret_value()),
        job_assistant=cast(JobAssistantClient, fake_job),
    )
    assert bot.process(update(123, "general chat"))[0].text.startswith("answer")
    assert fake_llm.requests


def test_document_download_is_authorized_and_pending_only(tmp_path: Path) -> None:
    configured = settings(tmp_path)
    fake_job = FakeJobAssistant(pending=True)
    bot = AssistantBot(
        configured,
        cast(LlmClient, FakeLlm()),
        SessionStore(configured.session_database_url.get_secret_value()),
        job_assistant=cast(JobAssistantClient, fake_job),
    )
    document = cast(dict[str, object], update(123, "unused"))
    document_message = cast(dict[str, object], document["message"])
    document_message.pop("text")
    document_message["document"] = {"file_id": "bounded"}
    assert bot.wants_job_document(document)
    unauthorized = cast(dict[str, object], update(999, "unused"))
    unauthorized_message = cast(dict[str, object], unauthorized["message"])
    unauthorized_message.pop("text")
    unauthorized_message["document"] = {"file_id": "do-not-fetch"}
    assert not bot.wants_job_document(unauthorized)
    fake_job.pending = False
    assert not bot.wants_job_document(document)


def test_job_commands_and_callbacks_are_namespaced() -> None:
    routed = _namespace_job_update(cast(dict[str, object], update(123, "/job_status ABC123")))
    assert routed["message"]["text"] == "/status ABC123"  # type: ignore[index]
    routed_callback = _namespace_job_update(cast(dict[str, object], callback(123, "job:apply:id")))
    assert routed_callback["callback_query"]["data"] == "apply:id"  # type: ignore[index]


def test_long_replies_are_split_within_telegram_limit() -> None:
    chunks = _telegram_chunks("word " * 2000)
    assert len(chunks) > 1
    assert all(len(chunk) <= 4000 for chunk in chunks)
