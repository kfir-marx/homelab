from __future__ import annotations

import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .artifacts import FilesystemArtifactStorage
from .config import Settings
from .database import database_ready, make_engine, make_session_factory
from .models import (
    Application,
    Artifact,
    GenerationRun,
    JobScore,
    JobSource,
    JobSourceOccurrence,
    OutboxEvent,
    TelegramConversation,
    User,
    WorkerHeartbeat,
    WorkItem,
)
from .security import UnsafeInput, safe_filename, validate_upload
from .telegram import TelegramUpdateHandler


def lease_telegram_notifications(
    session: Session, now: datetime, maximum_document_bytes: int = 10_000_000
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    events = session.scalars(
        select(OutboxEvent)
        .join(User, User.id == OutboxEvent.user_id)
        .where(
            OutboxEvent.channel == "telegram",
            OutboxEvent.status.in_(["pending", "retry"]),
            OutboxEvent.available_at <= now,
            User.active.is_(True),
        )
        .order_by(OutboxEvent.created_at)
        .with_for_update(skip_locked=True)
        .limit(20)
    ).all()
    for event in events:
        owner = session.get(User, event.user_id)
        if owner is None or event.recipient != str(owner.telegram_user_id):
            event.status = "dead"
            event.last_error = "notification recipient ownership mismatch"
            continue
        document = event.payload.get("document")
        document_output: dict[str, Any] | None = None
        if document is not None:
            if not isinstance(document, dict):
                event.status = "dead"
                event.last_error = "invalid typed document payload"
                continue
            try:
                artifact_id = uuid.UUID(str(document["artifact_id"]))
            except (KeyError, ValueError):
                event.status = "dead"
                event.last_error = "invalid document artifact identifier"
                continue
            artifact = session.scalar(
                select(Artifact).where(
                    Artifact.id == artifact_id,
                    Artifact.user_id == event.user_id,
                    Artifact.size_bytes > 0,
                    Artifact.mime_type.in_(
                        [
                            "application/pdf",
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        ]
                    ),
                    Artifact.size_bytes <= maximum_document_bytes,
                )
            )
            try:
                filename = safe_filename(str(document.get("filename", "")))
            except UnsafeInput:
                filename = ""
            expected_suffix = (
                ".pdf" if artifact and artifact.mime_type == "application/pdf" else ".docx"
            )
            if (
                artifact is None
                or int(document.get("size_bytes", -1)) != artifact.size_bytes
                or not filename.casefold().endswith(expected_suffix)
            ):
                event.status = "dead"
                event.last_error = "document ownership, type, or size validation failed"
                continue
            document_output = {
                "filename": filename[:200],
                "mime_type": artifact.mime_type,
                "size_bytes": artifact.size_bytes,
            }
        event.status = "leased"
        event.lease_owner = "telegram-gateway"
        event.lease_expires_at = now + timedelta(minutes=2)
        event.attempts += 1
        notification: dict[str, Any] = {
            "id": str(event.id),
            "chat_id": int(event.recipient),
            "text": str(event.payload["text"]),
            "buttons": list(event.payload.get("buttons", [])),
        }
        if document_output:
            notification["document"] = document_output
        output.append(notification)
    return output


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = make_engine(settings)
    factory = make_session_factory(engine)
    application = FastAPI(
        title="Homelab Job Assistant",
        version="0.1.0",
        docs_url=None if settings.environment == "production" else "/docs",
        redoc_url=None,
    )
    telegram_handler = TelegramUpdateHandler(
        settings, FilesystemArtifactStorage(settings.artifact_root), trusted_gateway=True
    )
    artifact_storage = FilesystemArtifactStorage(settings.artifact_root)

    def gateway_auth(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = settings.shared_gateway_api_token.get_secret_value()
        if (
            not expected
            or not authorization
            or not authorization.startswith("Bearer ")
            or not hmac.compare_digest(authorization.removeprefix("Bearer "), expected)
        ):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED)

    def gateway_notification_auth(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        expected = settings.shared_gateway_notification_token.get_secret_value()
        if (
            not expected
            or not authorization
            or not authorization.startswith("Bearer ")
            or not hmac.compare_digest(authorization.removeprefix("Bearer "), expected)
        ):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED)

    def typed_replies(replies: list[Any]) -> dict[str, list[dict[str, Any]]]:
        return {
            "replies": [
                {"chat_id": reply.chat_id, "text": reply.text, "buttons": list(reply.buttons)}
                for reply in replies
            ]
        }

    @application.get("/health/live")
    def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/health/ready")
    def readiness(response: Response) -> dict[str, object]:
        ready = database_ready(engine)
        if not ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "ready" if ready else "not-ready", "database": ready}

    @application.get("/health/workers")
    def worker_health(response: Response) -> dict[str, object]:
        now = datetime.now(UTC)
        with factory() as session:
            heartbeats = session.scalars(select(WorkerHeartbeat)).all()
        current = {
            item.role: item.last_seen_at
            >= now
            - timedelta(
                seconds=(
                    settings.generation_timeout_seconds + 120 if item.role == "generation" else 120
                )
            )
            for item in heartbeats
        }
        healthy = current.get("worker", False) and current.get("generation", False)
        if not healthy:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "ok" if healthy else "degraded", "workers": current}

    @application.get("/metrics")
    def metrics() -> Response:
        from .metrics import (
            APPLICATIONS,
            GENERATION_RUNS,
            JOB_SCORE_OUTCOMES,
            JOBS_BY_SOURCE,
            OUTBOX_EVENTS,
            OUTREACH,
            QUEUE_DEPTH,
            QUEUE_OLDEST,
            SOURCE_CONSECUTIVE_FAILURES,
        )

        now = datetime.now(UTC)
        with factory() as session:
            for metric in (
                APPLICATIONS,
                OUTREACH,
                QUEUE_DEPTH,
                QUEUE_OLDEST,
                JOBS_BY_SOURCE,
                JOB_SCORE_OUTCOMES,
                GENERATION_RUNS,
                OUTBOX_EVENTS,
                SOURCE_CONSECUTIVE_FAILURES,
            ):
                metric.clear()
            queue_rows = session.execute(
                select(WorkItem.queue, func.count(WorkItem.id), func.min(WorkItem.created_at))
                .where(WorkItem.status.in_(["pending", "retry"]))
                .group_by(WorkItem.queue)
            ).all()
            for queue, depth, oldest in queue_rows:
                QUEUE_DEPTH.labels(queue=queue).set(depth)
                QUEUE_OLDEST.labels(queue=queue).set(max(0, (now - oldest).total_seconds()))
            outbox_depth = (
                session.scalar(
                    select(func.count(OutboxEvent.id)).where(
                        OutboxEvent.status.in_(["pending", "retry"])
                    )
                )
                or 0
            )
            QUEUE_DEPTH.labels(queue="outbox").set(outbox_depth)
            for app_status, count in session.execute(
                select(Application.status, func.count(Application.id)).group_by(Application.status)
            ):
                APPLICATIONS.labels(status=app_status).set(count)
            for outreach_status, count in session.execute(
                select(Application.outreach_status, func.count(Application.id)).group_by(
                    Application.outreach_status
                )
            ):
                OUTREACH.labels(status=outreach_status).set(count)
            for source_name, count in session.execute(
                select(JobSource.name, func.count(JobSourceOccurrence.id))
                .join(JobSourceOccurrence, JobSourceOccurrence.source_id == JobSource.id)
                .group_by(JobSource.name)
            ):
                JOBS_BY_SOURCE.labels(source=source_name).set(count)
            for passed, count in session.execute(
                select(JobScore.passed_hard_filters, func.count(JobScore.id)).group_by(
                    JobScore.passed_hard_filters
                )
            ):
                JOB_SCORE_OUTCOMES.labels(outcome="shortlisted" if passed else "filtered").set(
                    count
                )
            for run_status, error_code, count in session.execute(
                select(
                    GenerationRun.status,
                    GenerationRun.error_code,
                    func.count(GenerationRun.id),
                ).group_by(GenerationRun.status, GenerationRun.error_code)
            ):
                GENERATION_RUNS.labels(status=run_status, error_code=error_code or "none").set(
                    count
                )
            for event_status, channel, count in session.execute(
                select(
                    OutboxEvent.status, OutboxEvent.channel, func.count(OutboxEvent.id)
                ).group_by(OutboxEvent.status, OutboxEvent.channel)
            ):
                OUTBOX_EVENTS.labels(status=event_status, channel=channel).set(count)
            for source_name, failures in session.execute(
                select(JobSource.name, JobSource.consecutive_failures)
            ):
                SOURCE_CONSECUTIVE_FAILURES.labels(source=source_name).set(failures)
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @application.get("/internal/telegram/pending", dependencies=[Depends(gateway_auth)])
    def telegram_pending(user_id: int, chat_id: int) -> dict[str, bool]:
        with factory() as session:
            user = session.scalar(
                select(User).where(
                    User.telegram_user_id == user_id,
                    User.active.is_(True),
                )
            )
            if user is None or user_id != chat_id:
                return {"pending": False}
            pending = session.scalar(
                select(TelegramConversation.id).where(
                    TelegramConversation.user_id == user.id,
                    TelegramConversation.chat_id == chat_id,
                    (
                        TelegramConversation.state.like("setup:%")
                        | TelegramConversation.state.in_(
                            [
                                "awaiting_job_metadata",
                                "awaiting_final_cv",
                                "awaiting_final_message",
                                "awaiting_contact",
                                "awaiting_follow_up",
                            ]
                        )
                    ),
                    TelegramConversation.expires_at > datetime.now(UTC),
                )
            )
        return {"pending": pending is not None}

    @application.get("/internal/telegram/authorize", dependencies=[Depends(gateway_auth)])
    def telegram_authorize(user_id: int, chat_id: int) -> dict[str, bool]:
        if user_id != chat_id:
            return {"authorized": False}
        with factory() as session:
            authorized = session.scalar(
                select(User.id).where(User.telegram_user_id == user_id, User.active.is_(True))
            )
        return {"authorized": authorized is not None}

    @application.post("/internal/telegram/update", dependencies=[Depends(gateway_auth)])
    def telegram_update(update: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        with factory.begin() as session:
            replies = telegram_handler.process(session, update)
        return typed_replies(replies)

    @application.post("/internal/telegram/document", dependencies=[Depends(gateway_auth)])
    async def telegram_document(
        update_json: Annotated[str, Form()], content: Annotated[UploadFile, File()]
    ) -> dict[str, list[dict[str, Any]]]:
        update = json.loads(update_json)
        raw = await content.read(settings.max_upload_bytes + 1)
        if len(raw) > settings.max_upload_bytes:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        update["message"]["_file_bytes"] = raw
        with factory.begin() as session:
            replies = telegram_handler.process(session, update)
        return typed_replies(replies)

    @application.get(
        "/internal/telegram/notifications",
        dependencies=[Depends(gateway_notification_auth)],
    )
    def telegram_notifications() -> dict[str, list[dict[str, Any]]]:
        now = datetime.now(UTC)
        with factory.begin() as session:
            output = lease_telegram_notifications(session, now, settings.max_upload_bytes)
        return {"notifications": output}

    @application.get(
        "/internal/telegram/notifications/{event_id}/document",
        dependencies=[Depends(gateway_notification_auth)],
    )
    def telegram_notification_document(event_id: str) -> Response:
        try:
            identifier = uuid.UUID(event_id)
        except ValueError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND) from exc
        with factory() as session:
            event = session.scalar(
                select(OutboxEvent).where(
                    OutboxEvent.id == identifier,
                    OutboxEvent.channel == "telegram",
                    OutboxEvent.status == "leased",
                )
            )
            if event is None or not isinstance(event.payload.get("document"), dict):
                raise HTTPException(status.HTTP_404_NOT_FOUND)
            owner = session.get(User, event.user_id)
            document = event.payload["document"]
            try:
                artifact_id = uuid.UUID(str(document["artifact_id"]))
            except (KeyError, ValueError) as exc:
                raise HTTPException(status.HTTP_404_NOT_FOUND) from exc
            artifact = session.scalar(
                select(Artifact).where(
                    Artifact.id == artifact_id,
                    Artifact.user_id == event.user_id,
                    Artifact.size_bytes > 0,
                    Artifact.mime_type.in_(
                        [
                            "application/pdf",
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        ]
                    ),
                    Artifact.size_bytes <= settings.max_upload_bytes,
                )
            )
            if owner is None or artifact is None or event.recipient != str(owner.telegram_user_id):
                raise HTTPException(status.HTTP_404_NOT_FOUND)
            content = artifact_storage.get_for_user(owner.storage_prefix, artifact.storage_key)
            try:
                filename = validate_upload(
                    str(document.get("filename", "")),
                    artifact.mime_type,
                    len(content),
                    settings.max_upload_bytes,
                    content,
                )
            except UnsafeInput as exc:
                raise HTTPException(status.HTTP_404_NOT_FOUND) from exc
            if len(content) != artifact.size_bytes:
                raise HTTPException(status.HTTP_404_NOT_FOUND)
            return Response(
                content=content,
                media_type=artifact.mime_type,
                headers={
                    "Content-Length": str(len(content)),
                    "X-Document-Filename": filename[:200],
                },
            )

    @application.post(
        "/internal/telegram/notifications/{event_id}/ack",
        dependencies=[Depends(gateway_notification_auth)],
    )
    def telegram_notification_ack(event_id: str) -> dict[str, str]:
        with factory.begin() as session:
            try:
                identifier = uuid.UUID(event_id)
            except ValueError as exc:
                raise HTTPException(status.HTTP_404_NOT_FOUND) from exc
            event = session.get(OutboxEvent, identifier)
            if (
                not event
                or event.channel != "telegram"
                or event.status not in {"leased", "delivered"}
            ):
                raise HTTPException(status.HTTP_404_NOT_FOUND)
            if event.status == "delivered":
                return {"status": "delivered"}
            event.status = "delivered"
            event.delivered_at = datetime.now(UTC)
            event.lease_owner = None
            event.lease_expires_at = None
        return {"status": "delivered"}

    @application.post(
        "/internal/telegram/notifications/{event_id}/uncertain",
        dependencies=[Depends(gateway_notification_auth)],
    )
    def telegram_notification_uncertain(event_id: str) -> dict[str, str]:
        with factory.begin() as session:
            try:
                identifier = uuid.UUID(event_id)
            except ValueError as exc:
                raise HTTPException(status.HTTP_404_NOT_FOUND) from exc
            event = session.get(OutboxEvent, identifier)
            if not event or event.channel != "telegram" or event.status != "leased":
                raise HTTPException(status.HTTP_404_NOT_FOUND)
            event.status = "uncertain"
            event.last_error = "Telegram send outcome uncertain; automatic retry suppressed"
            event.lease_owner = None
            event.lease_expires_at = None
        return {"status": "uncertain"}

    @application.post(
        "/internal/telegram/notifications/{event_id}/retry",
        dependencies=[Depends(gateway_notification_auth)],
    )
    def telegram_notification_retry(event_id: str) -> dict[str, str]:
        with factory.begin() as session:
            try:
                identifier = uuid.UUID(event_id)
            except ValueError as exc:
                raise HTTPException(status.HTTP_404_NOT_FOUND) from exc
            event = session.get(OutboxEvent, identifier)
            if not event or event.channel != "telegram" or event.status != "leased":
                raise HTTPException(status.HTTP_404_NOT_FOUND)
            event.lease_owner = None
            event.lease_expires_at = None
            if event.attempts >= event.max_attempts:
                event.status = "dead"
            else:
                event.status = "retry"
                event.available_at = datetime.now(UTC) + timedelta(
                    seconds=min(300, 2**event.attempts * 5)
                )
        return {"status": event.status}

    return application


app = create_app()
