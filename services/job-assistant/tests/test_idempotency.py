from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from job_assistant.artifacts import FilesystemArtifactStorage
from job_assistant.config import Settings
from job_assistant.domain import ingest_job
from job_assistant.interfaces import NormalizedJob
from job_assistant.models import (
    Application,
    Base,
    OutboxEvent,
    TelegramConversation,
    TelegramUpdate,
    WorkItem,
)
from job_assistant.queue import (
    enqueue_work,
    put_outbox,
    recover_stale_outbox,
    recover_stale_work,
)
from job_assistant.telegram import TelegramUpdateHandler


@pytest.fixture
def session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        yield value


def test_work_and_outbox_idempotency(session: Session) -> None:
    first = enqueue_work(session, "generation", "generate", {}, "same")
    second = enqueue_work(session, "generation", "generate", {}, "same")
    assert first.id == second.id
    first_outbox = put_outbox(session, "email", "review", "me@example.com", {}, "same-email")
    second_outbox = put_outbox(session, "email", "review", "me@example.com", {}, "same-email")
    assert first_outbox.id == second_outbox.id


def test_stale_lease_recovery(session: Session) -> None:
    item = enqueue_work(session, "generation", "generate", {}, "stale")
    item.status = "leased"
    item.lease_owner = "dead-worker"
    item.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.flush()
    assert recover_stale_work(session) == 1
    session.refresh(item)
    assert item.status == "retry"


def test_stale_outbox_lease_recovery(session: Session) -> None:
    event = put_outbox(session, "telegram", "notify", "123", {}, "stale-outbox")
    event.status = "leased"
    event.lease_owner = "dead-worker"
    event.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.flush()
    assert recover_stale_outbox(session) == 1
    recovered = session.get(OutboxEvent, event.id)
    assert recovered and recovered.status == "retry"


def test_duplicate_telegram_apply_creates_one_application(session: Session, tmp_path: Path) -> None:
    candidate = NormalizedJob(
        source="manual",
        external_job_id="one",
        original_url="https://example.com/jobs/1",
        canonical_url="https://example.com/jobs/1",
        company="Acme",
        title="DevOps Engineer",
    )
    job, _ = ingest_job(session, candidate)
    session.flush()
    handler = TelegramUpdateHandler(
        Settings(
            telegram_allowed_user_ids=frozenset({123}),
            artifact_root=tmp_path,
            career_inventory_path=(
                Path(__file__).parents[1] / "config/career-inventory.example.yaml"
            ),
        ),
        FilesystemArtifactStorage(tmp_path),
    )
    update = {
        "update_id": 99,
        "callback_query": {
            "from": {"id": 123},
            "data": f"apply:{job.id}",
            "message": {"chat": {"id": 123}},
        },
    }
    assert handler.process(session, update)
    session.flush()
    assert handler.process(session, update) == []
    assert session.scalar(select(func.count(Application.id))) == 1
    assert session.scalar(select(func.count(WorkItem.id))) == 1
    assert session.scalar(select(func.count(TelegramUpdate.update_id))) == 1


def test_invalid_telegram_user_is_silently_ignored(session: Session, tmp_path: Path) -> None:
    handler = TelegramUpdateHandler(
        Settings(telegram_allowed_user_ids=frozenset({123}), artifact_root=tmp_path),
        FilesystemArtifactStorage(tmp_path),
    )
    update = {
        "update_id": 1,
        "message": {"from": {"id": 999}, "chat": {"id": 999}, "text": "/help"},
    }
    assert handler.process(session, update) == []


def test_manual_metadata_conversation_survives_and_completes(
    session: Session, tmp_path: Path
) -> None:
    candidate = NormalizedJob(
        source="manual",
        external_job_id="manual-incomplete",
        original_url="https://example.com/jobs/manual",
        canonical_url="https://example.com/jobs/manual",
        company="Unknown company",
        title="Title requires manual completion",
    )
    job, _ = ingest_job(session, candidate)
    conversation = TelegramConversation(
        chat_id=123,
        user_id=123,
        state="awaiting_job_metadata",
        data={"job_id": str(job.id)},
    )
    session.add(conversation)
    session.flush()
    handler = TelegramUpdateHandler(
        Settings(telegram_allowed_user_ids=frozenset({123}), artifact_root=tmp_path),
        FilesystemArtifactStorage(tmp_path),
    )
    reply = handler._continue_conversation(
        session,
        conversation,
        {"text": "Acme | Platform Engineer | Tel Aviv | Kubernetes and Linux"},
    )
    assert reply and reply.buttons
    assert job.title == "Platform Engineer"
    assert job.company and job.company.name == "Acme"
    assert job.description_hash
