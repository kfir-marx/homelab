from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .models import OutboxEvent, WorkItem


def enqueue_work(
    session: Session,
    queue: str,
    kind: str,
    payload: dict[str, object],
    idempotency_key: str,
    max_attempts: int = 5,
    user_id: uuid.UUID | None = None,
) -> WorkItem:
    existing = session.scalar(select(WorkItem).where(WorkItem.idempotency_key == idempotency_key))
    if existing:
        return existing
    item = WorkItem(
        queue=queue,
        kind=kind,
        payload=payload,
        idempotency_key=idempotency_key,
        max_attempts=max_attempts,
        user_id=user_id,
    )
    session.add(item)
    session.flush()
    return item


def claim_work(session: Session, queue: str, worker_id: str, lease_seconds: int) -> WorkItem | None:
    now = datetime.now(UTC)
    item = session.scalar(
        select(WorkItem)
        .where(
            WorkItem.queue == queue,
            WorkItem.status.in_(["pending", "retry"]),
            WorkItem.available_at <= now,
        )
        .order_by(WorkItem.available_at, WorkItem.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if item:
        item.status = "leased"
        item.lease_owner = worker_id
        item.lease_expires_at = now + timedelta(seconds=lease_seconds)
        item.attempts += 1
        session.flush()
    return item


def complete_work(item: WorkItem) -> None:
    item.status = "completed"
    item.lease_owner = None
    item.lease_expires_at = None


def fail_work(item: WorkItem, error: str, retryable: bool = True) -> None:
    item.last_error = error[:4_000]
    item.lease_owner = None
    item.lease_expires_at = None
    if retryable and item.attempts < item.max_attempts:
        item.status = "retry"
        seconds = min(3600, (2**item.attempts) * 10) + random.uniform(0, 5)  # noqa: S311
        item.available_at = datetime.now(UTC) + timedelta(seconds=seconds)
    else:
        item.status = "dead"


def recover_stale_work(session: Session) -> int:
    now = datetime.now(UTC)
    result = session.execute(
        update(WorkItem)
        .where(WorkItem.status == "leased", WorkItem.lease_expires_at < now)
        .values(status="retry", lease_owner=None, lease_expires_at=None, available_at=now)
    )
    return int(getattr(result, "rowcount", 0) or 0)


def recover_stale_outbox(session: Session) -> int:
    now = datetime.now(UTC)
    retryable = session.execute(
        update(OutboxEvent)
        .where(
            OutboxEvent.status == "leased",
            OutboxEvent.lease_expires_at < now,
            OutboxEvent.channel != "telegram",
        )
        .values(
            status="retry",
            lease_owner=None,
            lease_expires_at=None,
            available_at=now,
        )
    )
    uncertain = session.execute(
        update(OutboxEvent)
        .where(
            OutboxEvent.status == "leased",
            OutboxEvent.lease_expires_at < now,
            OutboxEvent.channel == "telegram",
        )
        .values(
            status="uncertain",
            lease_owner=None,
            lease_expires_at=None,
            last_error="gateway lease expired; Telegram delivery outcome is uncertain",
        )
    )
    return int(getattr(retryable, "rowcount", 0) or 0) + int(getattr(uncertain, "rowcount", 0) or 0)


def put_outbox(
    session: Session,
    channel: str,
    event_type: str,
    recipient: str,
    payload: dict[str, object],
    idempotency_key: str,
    user_id: uuid.UUID | None = None,
) -> OutboxEvent:
    existing = session.scalar(
        select(OutboxEvent).where(OutboxEvent.idempotency_key == idempotency_key)
    )
    if existing:
        return existing
    event = OutboxEvent(
        channel=channel,
        event_type=event_type,
        recipient=recipient,
        payload=payload,
        idempotency_key=idempotency_key,
        user_id=user_id,
    )
    session.add(event)
    session.flush()
    return event
