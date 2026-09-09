"""Database outbox + RabbitMQ execution, with leases and bounded retry backoff."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta

import aio_pika
from aio_pika.abc import (
    AbstractChannel,
    AbstractIncomingMessage,
    AbstractQueue,
    AbstractRobustConnection,
)
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .database import BackgroundJob
from .llm import ExtractionError
from .mailboxes import MailboxError
from .models import JobView
from .oauth import OAuthError


def job_view(job: BackgroundJob) -> JobView:
    return JobView.model_validate({name: getattr(job, name) for name in JobView.model_fields})


class JobFailure(RuntimeError):
    def __init__(self, message: str, result: dict[str, object]) -> None:
        super().__init__(message)
        self.result = result


class JobQueue:
    def __init__(self, settings: Settings, factory: sessionmaker[Session]) -> None:
        self.settings, self.factory = settings, factory
        self.connection: AbstractRobustConnection | None = None
        self.channel: AbstractChannel | None = None
        self.queue: AbstractQueue | None = None

    @property
    def ready(self) -> bool:
        return self.connection is not None and not self.connection.is_closed

    async def connect(self) -> None:
        self.connection = await aio_pika.connect_robust(
            self.settings.rabbitmq_url.get_secret_value()
        )
        self.channel = await self.connection.channel(publisher_confirms=True)
        await self.channel.set_qos(prefetch_count=1)
        self.queue = await self.channel.declare_queue(self.settings.jobs_queue, durable=True)

    async def close(self) -> None:
        if self.connection:
            await self.connection.close()

    async def publish(self, job_id: str) -> None:
        if not self.channel:
            return  # durable outbox remains queued until a worker can publish it
        await self.channel.default_exchange.publish(
            aio_pika.Message(
                body=json.dumps({"job_id": job_id}).encode(),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key=self.settings.jobs_queue,
            mandatory=True,
        )

    def enqueue(
        self, *, user_id: str, organization_id: str, kind: str, payload: dict[str, object], key: str
    ) -> JobView:
        with self.factory.begin() as session:
            from .database import Organization

            session.scalar(
                select(Organization).where(Organization.id == organization_id).with_for_update()
            )
            job = session.scalar(
                select(BackgroundJob).where(
                    BackgroundJob.user_id == user_id,
                    BackgroundJob.organization_id == organization_id,
                    BackgroundJob.idempotency_key == key,
                )
            )
            if job:
                if job.kind != kind or job.payload != payload:
                    raise ValueError("idempotency key already used for another request")
                return job_view(job)
            job = BackgroundJob(
                user_id=user_id,
                organization_id=organization_id,
                kind=kind,
                payload=payload,
                idempotency_key=key,
            )
            session.add(job)
            session.flush()
            return job_view(job)

    async def recover(self) -> None:
        while True:
            now = datetime.now(UTC)
            with self.factory() as session:
                ids = session.scalars(
                    select(BackgroundJob.id)
                    .where(
                        or_(
                            BackgroundJob.status.in_(["queued", "retrying"]),
                            (BackgroundJob.status == "running") & (BackgroundJob.lease_until < now),
                        ),
                        BackgroundJob.available_at <= now,
                    )
                    .limit(100)
                ).all()
            for job_id in ids:
                with suppress(Exception):
                    await self.publish(job_id)
            await asyncio.sleep(10)

    async def execute(
        self, job_id: str, handler: Callable[[BackgroundJob], Awaitable[dict[str, object]]]
    ) -> None:
        now = datetime.now(UTC)
        with self.factory.begin() as session:
            job = session.scalar(
                select(BackgroundJob).where(BackgroundJob.id == job_id).with_for_update()
            )
            if not job or job.status in {"completed", "failed"}:
                return

            def aware(value: datetime) -> datetime:
                return value.replace(tzinfo=UTC) if value.tzinfo is None else value

            if aware(job.available_at) > now or (
                job.status == "running" and job.lease_until and aware(job.lease_until) > now
            ):
                return
            if job.attempts >= self.settings.job_max_attempts:
                job.status, job.error = "failed", "Worker stopped repeatedly; retry this job."
                return
            job.status, job.error = "running", None
            job.attempts += 1
            job.lease_until = now + timedelta(seconds=90)
            attempt = job.attempts

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(20)
                with self.factory.begin() as session:
                    current = session.get(BackgroundJob, job_id)
                    if current and current.status == "running" and current.attempts == attempt:
                        current.lease_until = datetime.now(UTC) + timedelta(seconds=90)

        pulse = asyncio.create_task(heartbeat())
        try:
            result = await handler(job)
        except Exception as exc:
            with self.factory.begin() as session:
                current = session.get(BackgroundJob, job_id)
                assert current
                if current.status != "running" or current.attempts != attempt:
                    return  # A newer lease owns this job; stale completion cannot overwrite it.
                current.status = (
                    "failed"
                    if job.kind == "send" or attempt >= self.settings.job_max_attempts
                    else "retrying"
                )
                if isinstance(exc, JobFailure):
                    current.result = exc.result
                # Never leak provider credentials or raw email through exceptions.
                current.error = (
                    str(exc)
                    if isinstance(exc, (JobFailure, MailboxError))
                    else "Mailbox access failed. Reconnect this account and retry."
                    if isinstance(exc, OAuthError)
                    else "A message could not be classified. Retry the scan."
                    if isinstance(exc, ExtractionError)
                    else "Delivery could not be confirmed. "
                    "Review delivery results before sending again."
                    if job.kind == "send"
                    else "Background processing failed. "
                    "Retry the job or check the mailbox connection."
                )
                current.available_at = datetime.now(UTC) + timedelta(
                    seconds=min(300, 5 * 2**attempt)
                )
                current.lease_until = None
        else:
            with self.factory.begin() as session:
                current = session.get(BackgroundJob, job_id)
                assert current
                if current.status != "running" or current.attempts != attempt:
                    return  # A newer lease owns this job; stale completion cannot overwrite it.
                current.status, current.result, current.error = "completed", result, None
                current.lease_until = None
        finally:
            pulse.cancel()
            with suppress(asyncio.CancelledError):
                await pulse

    async def consume(
        self, handler: Callable[[BackgroundJob], Awaitable[dict[str, object]]]
    ) -> None:
        async def received(message: AbstractIncomingMessage) -> None:
            async with message.process(requeue=True):
                await self.execute(json.loads(message.body)["job_id"], handler)

        assert self.queue
        await self.queue.consume(received)
        await self.recover()
