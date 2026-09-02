from __future__ import annotations

import os
import random
import socket
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .artifacts import (
    FilesystemArtifactStorage,
    render_docx,
    render_markdown,
    render_pdf,
    user_storage_key,
)
from .career import CareerInventory
from .config import Settings
from .contact_policy import ContactPolicyInput, automatic_email_allowed
from .domain import transition_application, transition_outreach
from .email_delivery import SmtpDeliveryProvider
from .generation import ExternalAiGenerationProvider, GenerationError, validate_claims
from .interfaces import Delivery, GenerationResult
from .metrics import (
    DELIVERY_FAILURES,
    EXTERNAL_AI_FAILURES,
    GENERATION_DURATION,
    GENERATION_FAILURES,
)
from .models import (
    Application,
    Artifact,
    Contact,
    DeliveryAttempt,
    GenerationRun,
    JobScore,
    OutboxEvent,
    User,
    WorkerHeartbeat,
    WorkItem,
)
from .queue import claim_work, complete_work, fail_work, put_outbox, recover_stale_outbox
from .states import ApplicationStatus, OutreachStatus


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
    provider = ExternalAiGenerationProvider(
        settings.external_ai_base_url,
        settings.external_ai_token.get_secret_value(),
        settings.external_ai_model,
        settings.external_ai_reasoning,
        settings.generation_timeout_seconds,
    )
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
                if item.user_id != application.user_id:
                    raise GenerationError("work item ownership mismatch", "authorization")
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
                        user_id=application.user_id,
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
            with GENERATION_DURATION.labels(provider=provider.name).time():
                submission_key = item.idempotency_key

                def record_external_job(
                    public_id: str, idempotency_key: str = submission_key
                ) -> None:
                    with factory.begin() as submission_session:
                        submitted_run = submission_session.scalar(
                            select(GenerationRun).where(
                                GenerationRun.idempotency_key == idempotency_key
                            )
                        )
                        if submitted_run:
                            submitted_run.external_job_id = public_id

                result = provider.generate(raw_payload, submission_key, record_external_job)
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
                run.external_job_id = provider.last_external_job_id
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
                    user_id=application.user_id,
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
                EXTERNAL_AI_FAILURES.labels(kind=code).inc()
            with factory.begin() as session:
                work = session.get(WorkItem, item.id)
                if work:
                    fail_work(work, code, getattr(exc, "retryable", False))
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
                owner = session.get(User, application.user_id) if application else None
                if (
                    chat_id is not None
                    and owner is not None
                    and int(chat_id) == owner.telegram_user_id
                    and code in {"authentication", "usage_limit"}
                ):
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
                        user_id=application.user_id if application else item.user_id,
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
        .where(
            OutboxEvent.status.in_(["pending", "retry"]),
            OutboxEvent.available_at <= now,
            OutboxEvent.channel != "telegram",
        )
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
                raise RuntimeError("Telegram notifications are delivered by the gateway")
            elif event.event_type == "review_material":
                if not smtp or not event.recipient:
                    raise RuntimeError("review SMTP is not configured")
                application_id = uuid.UUID(str(event.payload["application_id"]))
                with factory() as session:
                    application = session.get(Application, application_id)
                    assert application and application.user_id == event.user_id
                    user = session.get(User, application.user_id)
                    assert user and user.active
                    company_name = (
                        application.job.company.name
                        if application.job.company
                        else "Unknown company"
                    )
                    attachments = tuple(
                        storage.path_for_user(user.storage_prefix, str(key))
                        for key in event.payload["artifact_keys"]
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
                        f"Return the edited CV with /job_final {application.human_code}."
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
                    current.last_error = type(exc).__name__
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
        if (
            not application
            or not run
            or run.user_id != application.user_id
            or event.user_id != application.user_id
            or not run.output_json
        ):
            raise RuntimeError("completed generation output is unavailable")
        user = session.get(User, application.user_id)
        if user is None or not user.active:
            raise RuntimeError("application owner is inactive")
        result = GenerationResult.model_validate(run.output_json)
        preview = render_markdown(result).decode("utf-8")
        rendered = {
            "generated_cv_markdown": (render_markdown(result), "text/markdown", ".md"),
            "generated_cv_docx": (
                render_docx(
                    result,
                    settings.artifact_root / user.cv_template_key if user.cv_template_key else None,
                ),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ".docx",
            ),
            "generated_cv_pdf": (render_pdf(result), "application/pdf", ".pdf"),
        }
        artifact_keys: list[str] = []
        generated_artifacts: list[Artifact] = []
        for kind, (content, mime_type, suffix) in rendered.items():
            key = user_storage_key(
                user.storage_prefix,
                "applications",
                str(application.id),
                f"{kind}-v1{suffix}",
            )
            artifact_keys.append(key)
            existing = session.scalar(
                select(Artifact).where(
                    Artifact.application_id == application.id,
                    Artifact.kind == kind,
                    Artifact.version == 1,
                )
            )
            if existing:
                generated_artifacts.append(existing)
                continue
            stored = storage.put(key, content, mime_type)
            artifact = Artifact(
                user_id=user.id,
                application_id=application.id,
                kind=kind,
                version=1,
                storage_key=stored.key,
                sha256=stored.sha256,
                size_bytes=stored.size_bytes,
                mime_type=stored.mime_type,
            )
            session.add(artifact)
            session.flush()
            generated_artifacts.append(artifact)
        if application.status == ApplicationStatus.GENERATING.value:
            transition_application(session, application, ApplicationStatus.REVIEW_READY, "worker")
        application.draft_message = result.recruiter_message
        application.final_subject = f"Regarding the open role — {application.human_code}"
        score = session.scalar(
            select(JobScore)
            .where(
                JobScore.job_id == application.job_id,
                JobScore.user_id == application.user_id,
            )
            .order_by(JobScore.created_at.desc())
            .limit(1)
        )
        if user.review_email:
            put_outbox(
                session,
                "email",
                "review_material",
                user.review_email,
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
                user_id=user.id,
            )
        chat_id = event.payload.get("notification_chat_id")
        if chat_id is not None and int(chat_id) == user.telegram_user_id:
            email_note = (
                "Review email queued."
                if user.review_email
                else "Review email is not configured; artifacts remain stored."
            )
            explanation = score.explanation if score else "Not scored"
            gaps = ", ".join(item[:100] for item in result.unsupported_requirements[:5])
            warnings = ", ".join(item[:100] for item in result.warnings[:5])
            gaps = gaps or "none identified"
            warnings = warnings or "none"
            review_buttons = [
                ["Accept Draft", f"accept-draft:{application.id}"],
                ["Upload Revision", f"upload-revision:{application.id}"],
                ["Edit Message", f"edit-message:{application.id}"],
                ["Manual", f"manual:{application.id}"],
            ]
            put_outbox(
                session,
                "telegram",
                "generation_ready_summary",
                str(chat_id),
                {
                    "text": (
                        f"Application {application.human_code} is ready for review.\n"
                        f"Match: {explanation[:700]}\nGaps: {gaps}\nWarnings: {warnings}\n"
                        f"Recruiter draft:\n{result.recruiter_message}\n\n{email_note}"
                    ),
                    "buttons": review_buttons,
                },
                f"generation-ready:{application.id}:summary:v1",
                user_id=user.id,
            )
            for artifact in generated_artifacts:
                if artifact.mime_type not in {
                    "application/pdf",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                }:
                    continue
                extension = ".pdf" if artifact.mime_type == "application/pdf" else ".docx"
                put_outbox(
                    session,
                    "telegram",
                    "generation_ready_document",
                    str(chat_id),
                    {
                        "text": (
                            f"Application {application.human_code} "
                            f"{extension[1:].upper()} draft. Review details were sent separately."
                        ),
                        "buttons": review_buttons,
                        "document": {
                            "artifact_id": str(artifact.id),
                            "filename": f"job-{application.human_code}-draft{extension}",
                            "mime_type": artifact.mime_type,
                            "size_bytes": artifact.size_bytes,
                        },
                    },
                    f"generation-ready:{application.id}:{artifact.kind}:v1",
                    user_id=user.id,
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
        if event.user_id != application.user_id:
            raise RuntimeError("delivery event ownership mismatch")
        user = session.get(User, application.user_id)
        if user is None or not user.active or not user.automated_delivery_enabled:
            raise RuntimeError("automated delivery is disabled for this account")
        if not user.smtp_from or user.smtp_from != settings.smtp_from:
            raise RuntimeError("per-user sender configuration is unavailable")
        contact = session.scalar(
            select(Contact).where(
                Contact.id == application.approved_contact_id,
                Contact.user_id == application.user_id,
            )
        )
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
            user_id=application.user_id,
            application_id=application.id,
            contact_id=contact.id,
            channel="email",
            idempotency_key=event.idempotency_key,
            status="sending",
        )
        session.add(created_attempt)
        final_cv = session.scalar(
            select(Artifact).where(
                Artifact.id == application.final_cv_artifact_id,
                Artifact.application_id == application.id,
                Artifact.user_id == application.user_id,
            )
        )
        if not final_cv:
            raise RuntimeError("approved final CV is missing")
        recipient, message, code = contact.email, application.final_message, application.human_code
        subject = application.final_subject or f"Regarding the open role — {code}"
        artifact_path = storage.path_for_user(user.storage_prefix, final_cv.storage_key)
    assert recipient and message
    provider_id = smtp.send(
        Delivery(
            recipient,
            subject,
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
    user = session.get(User, application.user_id)
    if user is not None and user.active:
        put_outbox(
            session,
            "telegram",
            "manual_delivery_required",
            str(user.telegram_user_id),
            {
                "text": (
                    f"Application {application.human_code} needs manual outreach: {reason}. "
                    "No automatic retry will be attempted."
                )
            },
            f"manual-delivery:{application.id}:{user.id}:v1",
            user_id=user.id,
        )
