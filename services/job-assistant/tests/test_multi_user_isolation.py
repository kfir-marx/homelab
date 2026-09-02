from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from job_assistant.api import lease_telegram_notifications
from job_assistant.artifacts import FilesystemArtifactStorage, user_storage_key
from job_assistant.config import Settings
from job_assistant.domain import create_application, get_application_by_code, ingest_job
from job_assistant.interfaces import NormalizedJob
from job_assistant.models import (
    Application,
    ApplicationContact,
    Artifact,
    Base,
    Contact,
    OutboxEvent,
    TelegramConversation,
    User,
    WorkItem,
)
from job_assistant.queue import put_outbox
from job_assistant.security import UnsafeInput
from job_assistant.telegram import TelegramUpdateHandler


@pytest.fixture
def session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.connect() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        yield value


def user(telegram_id: int, prefix: str, *, generation: bool = False) -> User:
    return User(
        telegram_user_id=telegram_id,
        storage_prefix=prefix,
        career_inventory_key=f"{prefix}/private/career-inventory.yaml",
        generation_enabled=generation,
    )


def job(session: Session):  # type: ignore[no-untyped-def]
    value, _ = ingest_job(
        session,
        NormalizedJob(
            source="manual",
            external_job_id="shared-job",
            original_url="https://example.com/jobs/shared",
            canonical_url="https://example.com/jobs/shared",
            company="Acme",
            title="Platform Engineer",
        ),
    )
    return value


def test_same_job_is_independent_and_codes_are_owner_scoped(session: Session) -> None:
    owner = user(100, "10000000-0000-0000-0000-000000000000")
    friend = user(200, "20000000-0000-0000-0000-000000000000")
    session.add_all([owner, friend])
    shared_job = job(session)
    session.flush()
    owner_app, _ = create_application(session, owner, shared_job, "test")
    friend_app, _ = create_application(session, friend, shared_job, "test")
    session.flush()
    assert owner_app.id != friend_app.id
    assert session.scalar(select(func.count(Application.id))) == 2
    assert get_application_by_code(session, owner, friend_app.human_code) is None
    assert get_application_by_code(session, friend, owner_app.human_code) is None


def test_database_rejects_cross_user_application_contact(session: Session) -> None:
    owner = user(100, "10000000-0000-0000-0000-000000000000")
    friend = user(200, "20000000-0000-0000-0000-000000000000")
    session.add_all([owner, friend])
    shared_job = job(session)
    session.flush()
    owner_app, _ = create_application(session, owner, shared_job, "test")
    friend_contact = Contact(
        user_id=friend.id,
        name="Friend contact",
        source="test",
        confidence="high",
        verification_status="verified",
        evidence="test fixture",
    )
    session.add(friend_contact)
    session.flush()
    session.add(
        ApplicationContact(
            user_id=owner.id,
            application_id=owner_app.id,
            contact_id=friend_contact.id,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_cross_user_uuid_callback_and_conversation_are_denied(
    session: Session, tmp_path: Path
) -> None:
    owner = user(100, "10000000-0000-0000-0000-000000000000")
    friend = user(200, "20000000-0000-0000-0000-000000000000")
    session.add_all([owner, friend])
    shared_job = job(session)
    session.flush()
    owner_app, _ = create_application(session, owner, shared_job, "test")
    session.add(
        TelegramConversation(
            user_id=owner.id,
            telegram_user_id=owner.telegram_user_id,
            chat_id=owner.telegram_user_id,
            state="awaiting_final_cv",
            application_id=owner_app.id,
        )
    )
    session.flush()
    handler = TelegramUpdateHandler(
        Settings(artifact_root=tmp_path), FilesystemArtifactStorage(tmp_path)
    )
    attack = {
        "update_id": 1,
        "callback_query": {
            "from": {"id": friend.telegram_user_id},
            "data": f"confirm:{owner_app.id}",
            "message": {"chat": {"id": friend.telegram_user_id, "type": "private"}},
        },
    }
    reply = handler.process(session, attack)
    assert reply and "no longer exists" in reply[0].text
    assert (
        session.scalar(
            select(TelegramConversation).where(TelegramConversation.user_id == friend.id)
        )
        is None
    )
    upload_attack = {
        "update_id": 2,
        "message": {
            "from": {"id": friend.telegram_user_id},
            "chat": {"id": friend.telegram_user_id, "type": "private"},
            "document": {
                "file_name": "cv.pdf",
                "mime_type": "application/pdf",
            },
            "_file_bytes": b"%PDF-friend",
        },
    }
    handler.process(session, upload_attack)
    session.flush()
    assert session.scalar(select(func.count(Artifact.id))) == 0


def test_artifact_prefix_cannot_cross_users(tmp_path: Path) -> None:
    storage = FilesystemArtifactStorage(tmp_path)
    owner_prefix = "10000000-0000-0000-0000-000000000000"
    friend_prefix = "20000000-0000-0000-0000-000000000000"
    key = user_storage_key(owner_prefix, "applications", "app", "cv.pdf")
    storage.put(key, b"%PDF-owner", "application/pdf")
    assert storage.get_for_user(owner_prefix, key) == b"%PDF-owner"
    with pytest.raises(UnsafeInput):
        storage.get_for_user(friend_prefix, key)


def test_generation_defaults_off_for_invited_user(session: Session, tmp_path: Path) -> None:
    friend = user(200, "20000000-0000-0000-0000-000000000000")
    session.add(friend)
    shared_job = job(session)
    session.flush()
    handler = TelegramUpdateHandler(
        Settings(artifact_root=tmp_path), FilesystemArtifactStorage(tmp_path)
    )
    update = {
        "update_id": 2,
        "callback_query": {
            "from": {"id": friend.telegram_user_id},
            "data": f"apply:{shared_job.id}",
            "message": {"chat": {"id": friend.telegram_user_id, "type": "private"}},
        },
    }
    reply = handler.process(session, update)
    assert reply and "disabled" in reply[0].text
    assert session.scalar(select(func.count(WorkItem.id))) == 0
    application = session.scalar(select(Application).where(Application.user_id == friend.id))
    assert application is not None
    assert not friend.automated_delivery_enabled
    assert (
        "disabled"
        in handler._confirm_send(session, friend, application, friend.telegram_user_id).text
    )


def test_revoked_user_is_silently_ignored(session: Session, tmp_path: Path) -> None:
    revoked = user(300, "30000000-0000-0000-0000-000000000000")
    revoked.active = False
    session.add(revoked)
    session.flush()
    handler = TelegramUpdateHandler(
        Settings(artifact_root=tmp_path), FilesystemArtifactStorage(tmp_path)
    )
    update = {
        "update_id": 3,
        "message": {
            "from": {"id": revoked.telegram_user_id},
            "chat": {"id": revoked.telegram_user_id, "type": "private"},
            "text": "/job_help",
        },
    }
    assert handler.process(session, update) == []


def test_one_users_inventory_cannot_enable_another_users_generation(
    session: Session, tmp_path: Path
) -> None:
    owner = user(100, "10000000-0000-0000-0000-000000000000", generation=True)
    friend = user(200, "20000000-0000-0000-0000-000000000000", generation=True)
    session.add_all([owner, friend])
    shared_job = job(session)
    session.flush()
    owner_inventory = tmp_path / owner.career_inventory_key
    owner_inventory.parent.mkdir(parents=True)
    owner_inventory.write_bytes(
        (Path(__file__).parents[1] / "config/career-inventory.example.yaml").read_bytes()
    )
    handler = TelegramUpdateHandler(
        Settings(artifact_root=tmp_path), FilesystemArtifactStorage(tmp_path)
    )
    update = {
        "update_id": 4,
        "callback_query": {
            "from": {"id": friend.telegram_user_id},
            "data": f"apply:{shared_job.id}",
            "message": {"chat": {"id": friend.telegram_user_id, "type": "private"}},
        },
    }
    reply = handler.process(session, update)
    assert reply and "blocked" in reply[0].text
    assert session.scalar(select(func.count(WorkItem.id))) == 0
    assert not friend.inventory_valid


def test_notification_recipients_are_owner_scoped(session: Session) -> None:
    owner = user(100, "10000000-0000-0000-0000-000000000000")
    friend = user(200, "20000000-0000-0000-0000-000000000000")
    session.add_all([owner, friend])
    session.flush()
    expected = put_outbox(
        session,
        "telegram",
        "recommendation",
        str(owner.telegram_user_id),
        {"text": "owner only"},
        "owner-notification",
        user_id=owner.id,
    )
    forged = put_outbox(
        session,
        "telegram",
        "recommendation",
        str(friend.telegram_user_id),
        {"text": "must not cross"},
        "cross-user-notification",
        user_id=owner.id,
    )
    session.flush()

    leased = lease_telegram_notifications(session, datetime.now(UTC))

    assert [item["id"] for item in leased] == [str(expected.id)]
    leased_event = session.get(OutboxEvent, expected.id)
    assert leased_event and leased_event.status == "leased"
    rejected = session.get(OutboxEvent, forged.id)
    assert rejected and rejected.status == "dead"


def test_telegram_document_notification_cannot_cross_artifact_owner(
    session: Session, tmp_path: Path
) -> None:
    owner = user(100, "10000000-0000-0000-0000-000000000000")
    friend = user(200, "20000000-0000-0000-0000-000000000000")
    session.add_all([owner, friend])
    shared_job = job(session)
    session.flush()
    owner_app, _ = create_application(session, owner, shared_job, "test")
    artifact = Artifact(
        user_id=owner.id,
        application_id=owner_app.id,
        kind="generated_cv_pdf",
        version=1,
        storage_key=f"{owner.storage_prefix}/applications/cv.pdf",
        sha256="0" * 64,
        size_bytes=100,
        mime_type="application/pdf",
    )
    session.add(artifact)
    session.flush()
    forged = put_outbox(
        session,
        "telegram",
        "generation_ready_document",
        str(friend.telegram_user_id),
        {
            "text": "must not deliver",
            "document": {
                "artifact_id": str(artifact.id),
                "filename": "cv.pdf",
                "mime_type": "application/pdf",
                "size_bytes": 100,
            },
        },
        "cross-owner-document",
        user_id=friend.id,
    )
    session.flush()

    assert lease_telegram_notifications(session, datetime.now(UTC)) == []
    assert forged.status == "dead"
