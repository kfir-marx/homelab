from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .states import ApplicationStatus, JobStatus, OutreachStatus


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("telegram_user_id > 0", name="ck_users_telegram_id_positive"),
        UniqueConstraint("storage_prefix"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(300))
    username: Mapped[str | None] = mapped_column(String(100))
    generation_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    automated_delivery_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    inventory_valid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    storage_prefix: Mapped[str] = mapped_column(String(36), nullable=False)
    career_inventory_key: Mapped[str] = mapped_column(String(200), nullable=False)
    cv_template_key: Mapped[str | None] = mapped_column(String(200))
    review_email: Mapped[str | None] = mapped_column(String(500))
    smtp_from: Mapped[str | None] = mapped_column(String(500))
    search_criteria_key: Mapped[str | None] = mapped_column(String(200))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobSource(Base, TimestampMixin):
    __tablename__ = "job_sources"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class Company(Base, TimestampMixin):
    __tablename__ = "companies"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    domain: Mapped[str | None] = mapped_column(String(255))
    excluded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SourceCompany(Base, TimestampMixin):
    __tablename__ = "source_companies"
    __table_args__ = (UniqueConstraint("source_id", "external_slug"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job_sources.id"), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), nullable=False)
    external_slug: Mapped[str] = mapped_column(String(300), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Job(Base, TimestampMixin):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('discovered','shortlisted','skipped','snoozed','expired','reopened')",
            name="ck_jobs_status",
        ),
        Index("ix_jobs_canonical_url", "canonical_url"),
        Index("ix_jobs_description_hash", "description_hash"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("companies.id"))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    location: Mapped[str | None] = mapped_column(String(500))
    workplace_type: Mapped[str | None] = mapped_column(String(30))
    employment_type: Mapped[str | None] = mapped_column(String(100))
    original_url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    ats_job_id: Mapped[str | None] = mapped_column(String(300))
    description_html: Mapped[str | None] = mapped_column(Text)
    description_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    description_hash: Mapped[str | None] = mapped_column(String(64))
    raw_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), default=JobStatus.DISCOVERED.value, nullable=False
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    company: Mapped[Company | None] = relationship()


class JobSourceOccurrence(Base, TimestampMixin):
    __tablename__ = "job_source_occurrences"
    __table_args__ = (UniqueConstraint("source_id", "external_job_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job_sources.id"), nullable=False)
    external_job_id: Mapped[str] = mapped_column(String(500), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    raw_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class JobDuplicateCandidate(Base):
    __tablename__ = "job_duplicate_candidates"
    __table_args__ = (
        UniqueConstraint("job_id", "candidate_job_id"),
        CheckConstraint("job_id <> candidate_job_id", name="ck_duplicate_distinct_jobs"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    candidate_job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    similarity: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class JobScore(Base):
    __tablename__ = "job_scores"
    __table_args__ = (UniqueConstraint("user_id", "job_id", "criteria_version"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    criteria_version: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    passed_hard_filters: Mapped[bool] = mapped_column(Boolean, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    gaps: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    components: Mapped[dict[str, float]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Application(Base, TimestampMixin):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("user_id", "job_id"),
        UniqueConstraint("id", "user_id"),
        ForeignKeyConstraint(
            ["approved_contact_id", "user_id"], ["contacts.id", "contacts.user_id"]
        ),
        UniqueConstraint("human_code"),
        CheckConstraint(
            "status IN ('selected','generation_queued','generating','review_ready',"
            "'final_material_received','approved','submitted','manual_required','failed','withdrawn')",
            name="ck_applications_status",
        ),
        CheckConstraint(
            "outreach_status IN ("
            "'no_contact','contact_candidate_found','contact_verified','drafted',"
            "'approved','queued','sent','delivered','bounced','manual_required','failed')",
            name="ck_applications_outreach_status",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    human_code: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(
        String(40), default=ApplicationStatus.SELECTED.value, nullable=False
    )
    outreach_status: Mapped[str] = mapped_column(
        String(40), default=OutreachStatus.NO_CONTACT.value, nullable=False
    )
    final_message: Mapped[str | None] = mapped_column(Text)
    approved_contact_id: Mapped[uuid.UUID | None] = mapped_column()
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    job: Mapped[Job] = relationship()
    user: Mapped[User] = relationship()


class ApplicationEvent(Base):
    __tablename__ = "application_events"
    __table_args__ = (
        CheckConstraint(
            "application_id IS NOT NULL OR job_id IS NOT NULL", name="ck_event_has_aggregate_id"
        ),
        ForeignKeyConstraint(
            ["application_id", "user_id"], ["applications.id", "applications.user_id"]
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    application_id: Mapped[uuid.UUID | None] = mapped_column()
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"))
    aggregate: Mapped[str] = mapped_column(String(30), nullable=False)
    from_state: Mapped[str | None] = mapped_column(String(40))
    to_state: Mapped[str] = mapped_column(String(40), nullable=False)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Contact(Base, TimestampMixin):
    __tablename__ = "contacts"
    __table_args__ = (
        CheckConstraint("confidence IN ('low','medium','high')", name="ck_contacts_confidence"),
        CheckConstraint(
            "verification_status IN ('unverified','verified','rejected','stale')",
            name="ck_contacts_verification",
        ),
        UniqueConstraint("id", "user_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    company_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("companies.id"))
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    role: Mapped[str | None] = mapped_column(String(300))
    email: Mapped[str | None] = mapped_column(String(500))
    profile_url: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(200), nullable=False)
    confidence: Mapped[str] = mapped_column(String(20), nullable=False)
    verification_status: Mapped[str] = mapped_column(
        String(20), default="unverified", nullable=False
    )
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApplicationContact(Base):
    __tablename__ = "application_contacts"
    __table_args__ = (
        UniqueConstraint("application_id", "contact_id"),
        ForeignKeyConstraint(
            ["application_id", "user_id"], ["applications.id", "applications.user_id"]
        ),
        ForeignKeyConstraint(["contact_id", "user_id"], ["contacts.id", "contacts.user_id"]),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    application_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    contact_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    selected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("application_id", "kind", "version"),
        ForeignKeyConstraint(
            ["application_id", "user_id"], ["applications.id", "applications.user_id"]
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    application_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(200), nullable=False)
    user_edited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class GenerationRun(Base):
    __tablename__ = "generation_runs"
    __table_args__ = (
        UniqueConstraint("application_id", "idempotency_key"),
        ForeignKeyConstraint(
            ["application_id", "user_id"], ["applications.id", "applications.user_id"]
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    application_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    external_job_id: Mapped[str | None] = mapped_column(String(40), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    structured_log: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(100))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class WorkItem(Base, TimestampMixin):
    __tablename__ = "work_items"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        CheckConstraint(
            "status IN ('pending','leased','completed','retry','dead')", name="ck_work_status"
        ),
        CheckConstraint(
            "queue <> 'generation' OR user_id IS NOT NULL",
            name="ck_generation_work_has_user",
        ),
        Index("ix_work_claim", "queue", "status", "available_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    queue: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    lease_owner: Mapped[str | None] = mapped_column(String(200))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class OutboxEvent(Base, TimestampMixin):
    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        CheckConstraint(
            "channel <> 'telegram' OR user_id IS NOT NULL",
            name="ck_telegram_outbox_has_user",
        ),
        Index("ix_outbox_pending", "status", "available_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    recipient: Mapped[str] = mapped_column(String(500), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(200))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class TelegramConversation(Base, TimestampMixin):
    __tablename__ = "telegram_conversations"
    __table_args__ = (
        UniqueConstraint("user_id"),
        ForeignKeyConstraint(
            ["application_id", "user_id"], ["applications.id", "applications.user_id"]
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    state: Mapped[str] = mapped_column(String(100), nullable=False)
    application_id: Mapped[uuid.UUID | None] = mapped_column()
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TelegramUpdate(Base):
    __tablename__ = "telegram_updates"
    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    payload_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"
    __table_args__ = (
        UniqueConstraint("idempotency_key"),
        ForeignKeyConstraint(
            ["application_id", "user_id"], ["applications.id", "applications.user_id"]
        ),
        ForeignKeyConstraint(["contact_id", "user_id"], ["contacts.id", "contacts.user_id"]),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    application_id: Mapped[uuid.UUID | None] = mapped_column()
    contact_id: Mapped[uuid.UUID | None] = mapped_column()
    channel: Mapped[str] = mapped_column(String(30), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(500))
    response_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class SearchFeedback(Base):
    __tablename__ = "search_feedback"
    __table_args__ = (
        UniqueConstraint("user_id", "job_id", "action"),
        ForeignKeyConstraint(
            ["application_id", "user_id"], ["applications.id", "applications.user_id"]
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    application_id: Mapped[uuid.UUID | None] = mapped_column()
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    worker_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    role: Mapped[str] = mapped_column(String(100), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )


class UserJobState(Base, TimestampMixin):
    __tablename__ = "user_job_states"
    __table_args__ = (
        UniqueConstraint("user_id", "job_id"),
        CheckConstraint(
            "status IN ('discovered','shortlisted','skipped','snoozed','expired','reopened')",
            name="ck_user_job_states_status",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), default=JobStatus.DISCOVERED.value, nullable=False
    )
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
