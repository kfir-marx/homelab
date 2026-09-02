from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from job_assistant.artifacts import FilesystemArtifactStorage
from job_assistant.config import Settings
from job_assistant.domain import (
    create_application,
    ingest_job,
    transition_application,
    transition_job,
)
from job_assistant.interfaces import NormalizedJob
from job_assistant.models import (
    Application,
    Artifact,
    Base,
    JobScore,
    OutboxEvent,
    SearchFeedback,
    TelegramConversation,
    User,
    UserSearchProfile,
)
from job_assistant.reminders import queue_due_reminders
from job_assistant.states import ApplicationStatus, InvalidTransition, JobStatus
from job_assistant.telegram import SETUP_FIELDS, TelegramUpdateHandler


@pytest.fixture
def session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.connect() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        yield value


def _user(identifier: int) -> User:
    prefix = f"{identifier:08d}-0000-0000-0000-000000000000"
    return User(
        telegram_user_id=identifier,
        storage_prefix=prefix,
        career_inventory_key=f"{prefix}/private/career-inventory.yaml",
    )


def _message(update_id: int, user_id: int, text_value: str) -> dict[str, object]:
    return {
        "update_id": update_id,
        "message": {
            "from": {"id": user_id},
            "chat": {"id": user_id, "type": "private"},
            "text": text_value,
        },
    }


def _callback(update_id: int, user_id: int, data: str) -> dict[str, object]:
    return {
        "update_id": update_id,
        "callback_query": {
            "from": {"id": user_id},
            "data": data,
            "message": {"chat": {"id": user_id, "type": "private"}},
        },
    }


def test_setup_is_resumable_confirmed_and_owner_scoped(session: Session, tmp_path: Path) -> None:
    owner, other = _user(100), _user(200)
    session.add_all([owner, other])
    session.flush()
    criteria_path = Path(__file__).parents[1] / "config/search-criteria.example.yaml"
    handler = TelegramUpdateHandler(
        Settings(artifact_root=tmp_path, search_criteria_path=criteria_path),
        FilesystemArtifactStorage(tmp_path),
    )
    assert "Step 1" in handler.process(session, _message(1, 100, "/job_setup"))[0].text
    for index in range(len(SETUP_FIELDS)):
        reply = handler.process(session, _callback(index + 2, 100, "setup-keep"))
        assert reply
    assert session.get(UserSearchProfile, owner.id) is None
    summary = handler.process(session, _callback(100, 100, "setup-view"))[0]
    assert "not saved" in summary.text
    handler.process(session, _callback(101, 100, "setup-confirm"))
    assert session.get(UserSearchProfile, owner.id) is not None
    assert session.get(UserSearchProfile, other.id) is None


def test_setup_expiry_and_cancel_do_not_save(session: Session, tmp_path: Path) -> None:
    user = _user(300)
    session.add(user)
    session.flush()
    criteria_path = Path(__file__).parents[1] / "config/search-criteria.example.yaml"
    handler = TelegramUpdateHandler(
        Settings(artifact_root=tmp_path, search_criteria_path=criteria_path),
        FilesystemArtifactStorage(tmp_path),
    )
    handler.process(session, _message(1, 300, "/job_setup"))
    conversation = session.scalar(
        select(TelegramConversation).where(TelegramConversation.user_id == user.id)
    )
    assert conversation
    conversation.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert "expired" in handler.process(session, _message(2, 300, "value"))[0].text
    handler.process(session, _message(3, 300, "/job_setup"))
    assert "cancelled" in handler.process(session, _callback(4, 300, "setup-cancel"))[0].text
    assert session.get(UserSearchProfile, user.id) is None


def test_application_outcome_transitions_are_explicit() -> None:
    application = Application(status=ApplicationStatus.SUBMITTED.value)
    application.user_id = None  # type: ignore[assignment]
    with pytest.raises(InvalidTransition):
        transition_application(Session(), application, ApplicationStatus.REVIEW_READY, "test")


def test_reminders_are_deduplicated_and_can_be_disabled(session: Session) -> None:
    user = _user(400)
    session.add(user)
    job, _ = ingest_job(
        session,
        NormalizedJob(
            source="test",
            external_job_id="reminder",
            original_url="https://example.com/jobs/1",
            canonical_url="https://example.com/jobs/1",
            company="Acme",
            title="Platform Engineer",
        ),
    )
    session.flush()
    application, _ = create_application(session, user, job, "test")
    application.status = ApplicationStatus.REVIEW_READY.value
    application.updated_at = datetime.now(UTC) - timedelta(days=2)
    now = datetime.now(UTC)
    assert queue_due_reminders(session, now) == 1
    assert queue_due_reminders(session, now) == 0
    assert session.scalar(select(func.count(OutboxEvent.id))) == 1
    application.reminders_disabled = True
    assert queue_due_reminders(session, now + timedelta(days=1)) == 0


def test_today_navigation_and_feedback_callbacks_are_idempotent(
    session: Session, tmp_path: Path
) -> None:
    user = _user(500)
    session.add(user)
    job, _ = ingest_job(
        session,
        NormalizedJob(
            source="test",
            external_job_id="today",
            original_url="https://example.com/jobs/today",
            canonical_url="https://example.com/jobs/today",
            company="Acme",
            title="Platform Engineer",
            location="Tel Aviv",
            workplace_type="hybrid",
        ),
    )
    session.flush()
    transition_job(session, user, job, JobStatus.SHORTLISTED, "test")
    session.add(
        JobScore(
            user_id=user.id,
            job_id=job.id,
            criteria_version="v1",
            score=0.9,
            passed_hard_filters=True,
            explanation="title 100%",
            gaps=[],
            components={"title": 1.0},
        )
    )
    session.flush()
    handler = TelegramUpdateHandler(
        Settings(artifact_root=tmp_path), FilesystemArtifactStorage(tmp_path)
    )
    card = handler.process(session, _message(1, 500, "/job_today"))[0]
    assert "Match 90%" in card.text
    assert {label for label, _ in card.buttons} == {"Apply", "Skip", "Snooze", "Why", "Next"}
    cursor = session.scalar(
        select(TelegramConversation).where(TelegramConversation.user_id == user.id)
    )
    assert cursor and cursor.state == "today" and cursor.data["job_id"] == str(job.id)
    assert "Skipped" in handler.process(session, _callback(2, 500, f"skip:{job.id}"))[0].text
    assert (
        "Already skipped" in handler.process(session, _callback(3, 500, f"skip:{job.id}"))[0].text
    )
    assert session.scalar(select(func.count(SearchFeedback.id))) == 1


def test_review_wizard_shows_exact_final_approval(session: Session, tmp_path: Path) -> None:
    user = _user(600)
    session.add(user)
    job, _ = ingest_job(
        session,
        NormalizedJob(
            source="test",
            external_job_id="review",
            original_url="https://example.com/jobs/review",
            canonical_url="https://example.com/jobs/review",
            company="Acme",
            title="Platform Engineer",
        ),
    )
    session.flush()
    assert job.company
    job.company.domain = "acme.com"
    application, _ = create_application(session, user, job, "test")
    application.status = ApplicationStatus.REVIEW_READY.value
    application.draft_message = "Hello Recruiter"
    application.final_subject = "Platform Engineer application"
    artifact = Artifact(
        user_id=user.id,
        application_id=application.id,
        kind="generated_cv_pdf",
        version=1,
        storage_key=f"{user.storage_prefix}/applications/{application.id}/draft.pdf",
        sha256="0" * 64,
        size_bytes=100,
        mime_type="application/pdf",
    )
    session.add(artifact)
    session.flush()
    handler = TelegramUpdateHandler(
        Settings(artifact_root=tmp_path), FilesystemArtifactStorage(tmp_path)
    )
    handler.process(session, _callback(1, 600, f"accept-draft:{application.id}"))
    handler.process(session, _callback(2, 600, f"accept-message:{application.id}"))
    handler.process(session, _callback(3, 600, f"add-contact:{application.id}"))
    handler.process(session, _message(4, 600, "Alice Recruiter | alice@acme.com"))
    approval = handler.process(session, _callback(5, 600, f"verify-contact:{application.id}"))[0]
    assert "Alice Recruiter <alice@acme.com>" in approval.text
    assert "Subject: Platform Engineer application" in approval.text
    assert "Attachment: generated CV v1.pdf" in approval.text
    assert "Message:\nHello Recruiter" in approval.text
    assert application.status == ApplicationStatus.FINAL_MATERIAL_RECEIVED.value


def test_dashboard_outcome_callbacks_follow_valid_transitions(
    session: Session, tmp_path: Path
) -> None:
    user = _user(700)
    session.add(user)
    job, _ = ingest_job(
        session,
        NormalizedJob(
            source="test",
            external_job_id="outcome",
            original_url="https://example.com/jobs/outcome",
            canonical_url="https://example.com/jobs/outcome",
            company="Acme",
            title="Platform Engineer",
        ),
    )
    session.flush()
    application, _ = create_application(session, user, job, "test")
    application.status = ApplicationStatus.APPROVED.value
    session.flush()
    handler = TelegramUpdateHandler(
        Settings(artifact_root=tmp_path), FilesystemArtifactStorage(tmp_path)
    )
    assert (
        "submitted"
        in handler.process(session, _callback(1, 700, f"submitted:{application.id}"))[0].text
    )
    assert (
        "interview"
        in handler.process(session, _callback(2, 700, f"interview:{application.id}"))[0].text
    )
    assert "offer" in handler.process(session, _callback(3, 700, f"offer:{application.id}"))[0].text
    assert application.status == ApplicationStatus.OFFER.value
