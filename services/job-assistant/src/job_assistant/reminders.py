from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Application, OutboxEvent, User
from .profiles import notification_preferences
from .queue import put_outbox
from .states import ApplicationStatus


def queue_due_reminders(session: Session, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    queued = 0
    applications = session.scalars(
        select(Application)
        .join(User, User.id == Application.user_id)
        .where(User.active.is_(True), Application.reminders_disabled.is_(False))
    ).all()
    for application in applications:
        user = session.get(User, application.user_id)
        if user is None or not notification_preferences(session, user).get("reminders", True):
            continue
        reminder_type: str | None = None
        if application.follow_up_at and application.follow_up_at <= now:
            reminder_type = "scheduled_follow_up"
        elif application.follow_up_at and application.follow_up_at > now:
            continue
        elif (
            application.status == ApplicationStatus.REVIEW_READY.value
            and application.updated_at <= now - timedelta(days=1)
        ):
            reminder_type = "draft_awaiting_review"
        elif application.status in {
            ApplicationStatus.FINAL_MATERIAL_RECEIVED.value,
            ApplicationStatus.APPROVED.value,
        } and application.updated_at <= now - timedelta(days=1):
            reminder_type = "ready_not_submitted"
        elif application.status == ApplicationStatus.SUBMITTED.value and (
            application.submitted_at or application.updated_at
        ) <= now - timedelta(days=14):
            reminder_type = "submitted_no_update"
        elif (
            application.status == ApplicationStatus.MANUAL_REQUIRED.value
            and application.updated_at <= now - timedelta(days=3)
        ):
            reminder_type = "manual_action_required"
        if reminder_type is None:
            continue
        company = application.job.company.name if application.job.company else "Unknown company"
        idempotency_key = (
            f"reminder:{application.user_id}:{application.id}:{reminder_type}:"
            f"{now.date().isoformat()}"
        )
        existing = session.scalar(
            select(OutboxEvent.id).where(OutboxEvent.idempotency_key == idempotency_key)
        )
        put_outbox(
            session,
            "telegram",
            "application_reminder",
            str(user.telegram_user_id),
            {
                "text": (
                    f"Reminder: {company} — {application.job.title}\n"
                    f"{reminder_type.replace('_', ' ')}. Reminders never contact recruiters "
                    "or submit applications."
                ),
                "buttons": [
                    ["View", f"detail:{application.id}"],
                    ["Snooze 7 days", f"remind-snooze:{application.id}"],
                    ["Disable", f"reminder-off:{application.id}"],
                ],
            },
            idempotency_key,
            user_id=application.user_id,
        )
        if existing is None:
            queued += 1
    return queued
