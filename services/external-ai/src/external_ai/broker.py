from __future__ import annotations

import secrets
from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import BrokerState, Job

REQUESTERS = ("homelab-assistant", "job-assistant")
PUBLIC_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_public_id() -> str:
    return "X" + "".join(secrets.choice(PUBLIC_ALPHABET) for _ in range(7))


def submit(
    session: Session,
    *,
    requester: str,
    idempotency_key: str,
    prompt: str,
    model: str,
    reasoning: str,
    output_schema: dict[str, object] | None,
    timeout_seconds: int,
    correlation: Mapping[str, object],
) -> Job:
    existing = session.scalar(
        select(Job).where(Job.requester == requester, Job.idempotency_key == idempotency_key)
    )
    if existing:
        if (
            existing.prompt != prompt
            or existing.model != model
            or existing.reasoning != reasoning
            or existing.output_schema != output_schema
            or existing.timeout_seconds != timeout_seconds
            or existing.correlation != dict(correlation)
        ):
            raise ValueError("idempotency key was reused for a different request")
        return existing
    job = Job(
        public_id=new_public_id(),
        requester=requester,
        idempotency_key=idempotency_key,
        prompt=prompt,
        model=model,
        reasoning=reasoning,
        output_schema=output_schema,
        timeout_seconds=timeout_seconds,
        correlation=dict(correlation),
    )
    session.add(job)
    session.flush()
    return job


def claim_fair(session: Session) -> Job | None:
    state = session.get(BrokerState, "last_requester")
    last = state.value if state else REQUESTERS[-1]
    order = REQUESTERS[1:] + REQUESTERS[:1] if last == REQUESTERS[0] else REQUESTERS
    job: Job | None = None
    for requester in order:
        job = session.scalar(
            select(Job)
            .where(Job.status == "queued", Job.requester == requester)
            .order_by(Job.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job:
            if state is None:
                state = BrokerState(key="last_requester", value=requester)
                session.add(state)
            else:
                state.value = requester
            job.status = "running"
            job.attempts += 1
            job.started_at = datetime.now(UTC)
            session.flush()
            return job
    return None
