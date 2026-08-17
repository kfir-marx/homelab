from __future__ import annotations

import fcntl
import os
import random
import socket
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .artifacts import FilesystemArtifactStorage, render_docx, render_markdown, render_pdf
from .career import CareerInventory
from .config import Settings
from .contact_policy import ContactPolicyInput, automatic_email_allowed
from .domain import transition_application, transition_outreach
from .email_delivery import SmtpDeliveryProvider
from .generation import (
    CodexCliGenerationProvider,
    GenerationError,
    validate_claims,
)
from .interfaces import Delivery, GenerationResult
from .metrics import CODEX_FAILURES, DELIVERY_FAILURES, GENERATION_DURATION, GENERATION_FAILURES
from .models import (
    Application,
    Artifact,
    Contact,
    DeliveryAttempt,
    GenerationRun,
    JobScore,
    OutboxEvent,
    WorkerHeartbeat,
    WorkItem,
)
from .queue import claim_work, complete_work, fail_work, put_outbox, recover_stale_outbox
from .states import ApplicationStatus, OutreachStatus
from .telegram import TelegramHttpProvider, TelegramReply


def _worker_id(role: str) -> str:
    return f"{role}:{socket.gethostname()}:{os.getpid()}"


def _heartbeat(session: Session, worker_id: str, role: str) -> None:
    heartbeat = session.get(WorkerHeartbeat, worker_id)
    if not heartbeat:
        heartbeat = WorkerHeartbeat(worker_id=worker_id, role=role)
        session.add(heartbeat)
    heartbeat.last_seen_at = datetime.now(UTC)


def run_generation_worker(factory: sessionmaker[Session], settings: Settings) -> None:
    worker_id = _worker_id("generation")
    provider = CodexCliGenerationProvider(
        settings.codex_executable, settings.codex_home, settings.codex_timeout_seconds
    )
    lock_path = settings.codex_home / "generation.lock"
    settings.codex_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    while True:
        with factory.begin() as session:
            _heartbeat(session, worker_id, "generation")
            item = claim_work(session, "generation", worker_id, settings.queue_lease_seconds)
        if not item:
            time.sleep(2)
            continue
        try:
            application_id = uuid.UUID(str(item.payload["application_id"]))
            raw_payload = item.payload.get("generation_payload")
            if not isinstance(raw_payload, dict):
                raise GenerationError("sanitized generation payload is missing", "invalid_input")
            inventory = CareerInventory.model_validate(raw_payload.get("career_inventory"))
            with factory.begin() as session:
                application = session.get(Application, application_id)
                if not application:
                    raise GenerationError("application not found", "not_found")
                if application.status == ApplicationStatus.GENERATION_QUEUED.value:
                    transition_application(
                        session, application, ApplicationStatus.GENERATING, worker_id
                    )
                run = session.scalar(
                    select(GenerationRun).where(
                        GenerationRun.idempotency_key == item.idempotency_key
                    )
                )
                if not run:
                    run = GenerationRun(
                        application_id=application.id,
                        provider=provider.name,
                        idempotency_key=item.idempotency_key,
                        status="running",
                        started_at=datetime.now(UTC),
                    )
                    session.add(run)
                else:
                    run.status = "running"
                    run.error_code = None
                    run.finished_at = None
            with (
                lock_path.open("a+") as lock_handle,
                GENERATION_DURATION.labels(provider=provider.name).time(),
            ):
                fcntl.flock(lock_handle, fcntl.LOCK_EX)
                result = provider.generate(raw_payload)
            validate_claims(result, inventory)
            with factory.begin() as session:
                application = session.get(Application, application_id)
                run = session.scalar(
                    select(GenerationRun).where(
                        GenerationRun.idempotency_key == item.idempotency_key
                    )
                )
                assert application and run
                run.status = "completed"
                run.output_json = result.model_dump(mode="json")
                run.structured_log = provider.last_structured_log
                run.exit_code = provider.last_exit_code
                run.finished_at = datetime.now(UTC)
                put_outbox(
                    session,
                    "internal",
                    "prepare_review",
                    "worker",
                    {
                        "application_id": str(application.id),
                        "generation_run_id": str(run.id),
                        "notification_chat_id": item.payload.get("notification_chat_id"),
                    },
                    f"prepare-review:{application.id}:v1",
                )
                work = session.get(WorkItem, item.id)
                assert work
                work.payload = {
                    "application_id": str(application.id),
                    "sanitized_input_purged": True,
                }
                complete_work(work)
        except (GenerationError, FileNotFoundError, ValueError) as exc:
            code = exc.code if isinstance(exc, GenerationError) else "configuration"
            GENERATION_FAILURES.labels(code=code).inc()
            if code in {"authentication", "usage_limit"}:
                CODEX_FAILURES.labels(kind=code).inc()
            with factory.begin() as session:
                work = session.get(WorkItem, item.id)
                if work:
                    fail_work(work, str(exc), getattr(exc, "retryable", False))
                run = session.scalar(
                    select(GenerationRun).where(
                        GenerationRun.idempotency_key == item.idempotency_key
                    )
                )
                if run:
                    run.status = "failed"
                    run.error_code = code
                    run.exit_code = getattr(exc, "exit_code", provider.last_exit_code)
                    run.structured_log = getattr(
                        exc, "structured_log", provider.last_structured_log
                    )
                    run.finished_at = datetime.now(UTC)
                application = session.get(
                    Application, uuid.UUID(str(item.payload["application_id"]))
                )
                chat_id = item.payload.get("notification_chat_id")
                if chat_id is not None and code in {"authentication", "usage_limit"}:
                    action = (
                        "Codex authentication needs manual recovery; follow the auth runbook."
                        if code == "authentication"
                        else "Codex usage is temporarily limited; generation will retry later."
                    )
                    put_outbox(
                        session,
                        "telegram",
                        "generation_attention",
                        str(chat_id),
                        {"text": f"Application generation paused. {action}"},
                        f"generation-attention:{application_id}:{code}:v1",
                    )
                if (
                    application
                    and work
                    and work.status == "dead"
                    and application.status
                    in {
                        ApplicationStatus.GENERATION_QUEUED.value,
                        ApplicationStatus.GENERATING.value,
                    }
                ):
                    transition_application(
                        session, application, ApplicationStatus.FAILED, worker_id, {"code": code}
                    )


def _claim_outbox(session: Session, worker_id: str, lease_seconds: int) -> OutboxEvent | None:
    now = datetime.now(UTC)
    event = session.scalar(
        select(OutboxEvent)
        .where(OutboxEvent.status.in_(["pending", "retry"]), OutboxEvent.available_at <= now)
        .order_by(OutboxEvent.available_at, OutboxEvent.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if event:
        event.status = "leased"
        event.attempts += 1
        event.lease_owner = worker_id
        event.lease_expires_at = now + timedelta(seconds=lease_seconds)
    return event


def run_general_worker(factory: sessionmaker[Session], settings: Settings) -> None:
    worker_id = _worker_id("worker")
    storage = FilesystemArtifactStorage(settings.artifact_root)
    telegram = (
        TelegramHttpProvider(settings.telegram_token.get_secret_value())
        if settings.telegram_token
        else None
    )
    smtp = None
    if settings.smtp_username and settings.smtp_password and settings.smtp_from:
        smtp = SmtpDeliveryProvider(
            settings.smtp_host,
            settings.smtp_port,
            settings.smtp_username,
            settings.smtp_password.get_secret_value(),
            settings.smtp_from,
        )
    while True:
        with factory.begin() as session:
            _heartbeat(session, worker_id, "worker")
            recover_stale_outbox(session)
            event = _claim_outbox(session, worker_id, settings.queue_lease_seconds)
        if not event:
            time.sleep(2)
            continue
        try:
            if event.event_type == "prepare_review":
                _prepare_review(factory, settings, storage, event)
            elif event.channel == "telegram":
                if not telegram:
                    raise RuntimeError("Telegram provider is not configured")
                buttons = tuple(
                    (str(row[0]), str(row[1])) for row in event.payload.get("buttons", [])
                )
                telegram.send_reply(
                    TelegramReply(int(event.recipient), str(event.payload["text"]), buttons)
                )
            elif event.event_type == "review_material":
                if not smtp or not event.recipient:
                    raise RuntimeError("review SMTP is not configured")
                application_id = uuid.UUID(str(event.payload["application_id"]))
                with factory() as session:
                    application = session.get(Application, application_id)
                    assert application
                    company_name = (
                        application.job.company.name
                        if application.job.company
                        else "Unknown company"
                    )
                    attachments = tuple(
                        settings.artifact_root / str(key) for key in event.payload["artifact_keys"]
                    )
                    body = (
                        f"Application: {application.human_code}\n{application.job.title} at "
                        f"{company_name}\n"
                        f"Job: {application.job.original_url}\n\n"
                        f"Match: {event.payload['match_explanation']}\n"
                        f"Contact: {event.payload['contact_summary']}\n\n"
                        f"Recruiter draft:\n{event.payload['message']}\n\n"
                        f"Unsupported requirements/gaps: {event.payload['gaps']}\n\n"
                        f"Warnings: {event.payload['warnings']}\n\n"
                        f"Plain-text CV preview:\n{event.payload['preview']}\n"
                        f"Return the edited CV with /final {application.human_code}."
                    )
                smtp.send(
                    Delivery(
                        event.recipient,
                        f"Job application {application.human_code} review",
                        body,
                        attachments,
                        event.idempotency_key,
                    )
                )
            elif event.event_type == "recruiter_outreach":
                _send_recruiter_once(factory, settings, storage, smtp, event)
            else:
                raise RuntimeError(f"unsupported outbox event {event.event_type}")
            with factory.begin() as session:
                current = session.get(OutboxEvent, event.id)
                if current:
                    current.status = "delivered"
                    current.delivered_at = datetime.now(UTC)
                    current.lease_owner = None
                    current.lease_expires_at = None
                    current.last_error = None
        except Exception as exc:
            DELIVERY_FAILURES.labels(channel=event.channel).inc()
            with factory.begin() as session:
                current = session.get(OutboxEvent, event.id)
                if current:
                    current.last_error = str(exc)[:4_000]
                    current.lease_owner = None
                    current.lease_expires_at = None
                    if current.attempts >= current.max_attempts:
                        current.status = "dead"
                    else:
                        current.status = "retry"
                        current.available_at = datetime.now(UTC) + timedelta(
                            seconds=min(3600, 2**current.attempts * 10) + random.uniform(0, 5)  # noqa: S311 - retry jitter
                        )


def _prepare_review(
    factory: sessionmaker[Session],
    settings: Settings,
    storage: FilesystemArtifactStorage,
    event: OutboxEvent,
) -> None:
    application_id = uuid.UUID(str(event.payload["application_id"]))
    run_id = uuid.UUID(str(event.payload["generation_run_id"]))
    with factory.begin() as session:
        application = session.get(Application, application_id)
        run = session.get(GenerationRun, run_id)
        if not application or not run or not run.output_json:
            raise RuntimeError("completed generation output is unavailable")
        result = GenerationResult.model_validate(run.output_json)
        preview = render_markdown(result).decode("utf-8")
        rendered = {
            "generated_cv_markdown": (render_markdown(result), "text/markdown", ".md"),
            "generated_cv_docx": (
                render_docx(result, settings.cv_template_path),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ".docx",
            ),
            "generated_cv_pdf": (render_pdf(result), "application/pdf", ".pdf"),
        }
        artifact_keys: list[str] = []
        for kind, (content, mime_type, suffix) in rendered.items():
            key = f"{application.human_code}/{kind}-v1{suffix}"
            artifact_keys.append(key)
            existing = session.scalar(
                select(Artifact).where(
                    Artifact.application_id == application.id,
                    Artifact.kind == kind,
                    Artifact.version == 1,
                )
            )
            if existing:
                continue
            stored = storage.put(key, content, mime_type)
            session.add(
                Artifact(
                    application_id=application.id,
                    kind=kind,
                    version=1,
                    storage_key=stored.key,
                    sha256=stored.sha256,
                    size_bytes=stored.size_bytes,
                    mime_type=stored.mime_type,
                )
            )
        if application.status == ApplicationStatus.GENERATING.value:
            transition_application(session, application, ApplicationStatus.REVIEW_READY, "worker")
        score = session.scalar(
            select(JobScore)
            .where(JobScore.job_id == application.job_id)
            .order_by(JobScore.created_at.desc())
            .limit(1)
        )
        if settings.review_email:
            put_outbox(
                session,
                "email",
                "review_material",
                settings.review_email,
                {
                    "application_id": str(application.id),
                    "message": result.recruiter_message,
                    "gaps": result.unsupported_requirements,
                    "warnings": result.warnings,
                    "match_explanation": score.explanation if score else "Not scored",
                    "contact_summary": "No verified contact selected yet",
                    "preview": preview,
                    "artifact_keys": artifact_keys,
                },
                f"review:{application.id}:v1",
            )
        chat_id = event.payload.get("notification_chat_id")
        if chat_id is not None:
            email_note = (
                "Review email queued."
                if settings.review_email
                else "Review email is not configured; artifacts remain stored."
            )
            put_outbox(
                session,
                "telegram",
                "generation_ready",
                str(chat_id),
                {
                    "text": (
                        f"Application {application.human_code} is ready for review. "
                        f"{email_note} Use /final {application.human_code} when ready."
                    )
                },
                f"generation-ready:{application.id}:v1",
            )


def _send_recruiter_once(
    factory: sessionmaker[Session],
    settings: Settings,
    storage: FilesystemArtifactStorage,
    smtp: SmtpDeliveryProvider | None,
    event: OutboxEvent,
) -> None:
    if not smtp:
        raise RuntimeError("SMTP is not configured")
    application_id = uuid.UUID(str(event.payload["application_id"]))
    with factory.begin() as session:
        application = session.get(Application, application_id)
        assert application and application.approved_contact_id
        contact = session.get(Contact, application.approved_contact_id)
        assert contact
        existing_attempt = session.scalar(
            select(DeliveryAttempt).where(DeliveryAttempt.idempotency_key == event.idempotency_key)
        )
        if existing_attempt:
            if existing_attempt.status in {"sent", "delivered"}:
                return
            if existing_attempt.status == "sending":
                existing_attempt.status = "unknown"
                existing_attempt.response_metadata = {
                    "reason": (
                        "worker restarted with SMTP outcome unknown; automatic retry suppressed"
                    )
                }
                if application.status == ApplicationStatus.APPROVED.value:
                    transition_application(
                        session,
                        application,
                        ApplicationStatus.MANUAL_REQUIRED,
                        "delivery-worker",
                        {"reason": "SMTP outcome unknown"},
                    )
                if application.outreach_status == OutreachStatus.QUEUED.value:
                    transition_outreach(
                        session,
                        application,
                        OutreachStatus.MANUAL_REQUIRED,
                        "delivery-worker",
                        {"reason": "SMTP outcome unknown"},
                    )
                _queue_manual_delivery_notice(
                    session, settings, application, "SMTP outcome unknown"
                )
                return
            raise RuntimeError("prior delivery attempt requires manual review")
        allowed, reason = automatic_email_allowed(
            ContactPolicyInput(
                contact.email,
                application.job.company.domain if application.job.company else None,
                contact.confidence,
                contact.verification_status == "verified",
                application.status == ApplicationStatus.APPROVED.value,
                False,
            )
        )
        if not allowed:
            transition_application(
                session,
                application,
                ApplicationStatus.MANUAL_REQUIRED,
                "delivery-worker",
                {"reason": reason},
            )
            if application.outreach_status == OutreachStatus.QUEUED.value:
                transition_outreach(
                    session,
                    application,
                    OutreachStatus.MANUAL_REQUIRED,
                    "delivery-worker",
                    {"reason": reason},
                )
            _queue_manual_delivery_notice(session, settings, application, reason)
            return
        created_attempt = DeliveryAttempt(
            application_id=application.id,
            contact_id=contact.id,
            channel="email",
            idempotency_key=event.idempotency_key,
            status="sending",
        )
        session.add(created_attempt)
        final_cv = session.scalar(
            select(Artifact)
            .where(Artifact.application_id == application.id, Artifact.kind == "final_cv")
            .order_by(Artifact.version.desc())
            .limit(1)
        )
        if not final_cv:
            raise RuntimeError("approved final CV is missing")
        recipient, message, code = contact.email, application.final_message, application.human_code
        artifact_path = settings.artifact_root / final_cv.storage_key
    assert recipient and message
    provider_id = smtp.send(
        Delivery(
            recipient,
            f"Regarding the open role — {code}",
            message,
            (artifact_path,),
            event.idempotency_key,
        )
    )
    with factory.begin() as session:
        completed_attempt = session.scalar(
            select(DeliveryAttempt).where(DeliveryAttempt.idempotency_key == event.idempotency_key)
        )
        application = session.get(Application, application_id)
        assert completed_attempt and application
        completed_attempt.status = "sent"
        completed_attempt.provider_message_id = provider_id
        transition_outreach(session, application, OutreachStatus.SENT, "delivery-worker")


def _queue_manual_delivery_notice(
    session: Session, settings: Settings, application: Application, reason: str
) -> None:
    for chat_id in settings.telegram_allowed_user_ids:
        put_outbox(
            session,
            "telegram",
            "manual_delivery_required",
            str(chat_id),
            {
                "text": (
                    f"Application {application.human_code} needs manual outreach: {reason}. "
                    "No automatic retry will be attempted."
                )
            },
            f"manual-delivery:{application.id}:{chat_id}:v1",
        )
