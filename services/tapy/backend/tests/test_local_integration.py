"""Opt-in tests for disposable local PostgreSQL/RabbitMQ; never use a live database."""

import asyncio
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from test_reconciliation import flight

from tapy.config import Settings
from tapy.database import (
    BackgroundJob,
    MailboxConnection,
    UpsellOpportunity,
    make_engine,
    make_factory,
    new_agent,
)
from tapy.jobs import JobQueue
from tapy.migrations import upgrade_database
from tapy.models import EmailExtraction, EmailForAnalysis, HotelBooking
from tapy.reconciliation import record_email


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("TAPY_TEST_DATABASE_URL") or not os.environ.get("TAPY_TEST_RABBITMQ_URL"),
    reason="requires disposable PostgreSQL and RabbitMQ",
)
async def test_postgres_concurrency_and_rabbit_outbox() -> None:
    settings = Settings(
        database_url=SecretStr(os.environ["TAPY_TEST_DATABASE_URL"]),
        rabbitmq_url=SecretStr(os.environ["TAPY_TEST_RABBITMQ_URL"]),
        jobs_queue=f"tapy.test.{uuid.uuid4().hex}",
    )
    engine = make_engine(settings)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: upgrade_database(engine), range(2)))
    factory = make_factory(engine)
    with factory.begin() as session:
        user, _ = new_agent(session)
        mailbox = MailboxConnection(
            user_id=user.id,
            organization_id=user.active_organization_id,
            provider="gmail",
            provider_account_id=uuid.uuid4().hex,
            email_address=user.email,
            refresh_token="inert",  # noqa: S106 - local fixture
            scopes="readonly",
        )  # noqa: S106
        session.add(mailbox)
        session.flush()
        assert user.active_organization_id
        organization_id, user_id, mailbox_id = user.active_organization_id, user.id, mailbox.id

    def ingest(_: int) -> list[str]:
        with factory.begin() as session:
            return record_email(
                session,
                organization_id=organization_id,
                agent_id=user_id,
                mailbox_id=mailbox_id,
                provider="gmail",
                email=EmailForAnalysis(message_id="same", body_text="inert"),
                extraction=EmailExtraction(
                    flight_booking=flight(),
                    hotel_booking=HotelBooking(is_hotel_booking_confirmation=False),
                ),
            )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(ingest, range(4)))
    with factory() as session:
        assert (
            session.scalar(
                select(func.count(UpsellOpportunity.id)).where(
                    UpsellOpportunity.organization_id == organization_id
                )
            )
            == 1
        )
    assert sum(len(r) for r in results) == 1
    queue = JobQueue(settings, factory)
    job = queue.enqueue(
        user_id=user_id, organization_id=organization_id, kind="scan", payload={}, key="outbox"
    )
    queued_id = job.id
    await queue.connect()
    executions = 0
    done = asyncio.Event()

    async def handler(job: BackgroundJob) -> dict[str, object]:
        nonlocal executions
        if job.id == queued_id:
            executions += 1
            done.set()
        return {"ok": True}

    task = asyncio.create_task(queue.consume(handler))
    try:
        await asyncio.wait_for(done.wait(), 20)
        await queue.publish(job.id)
        await queue.publish(job.id)
        await asyncio.sleep(0.5)
        with factory() as session:
            result = session.get(BackgroundJob, job.id)
            assert result and result.status == "completed" and result.attempts == 1
        assert executions == 1
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        assert queue.queue
        await queue.queue.delete(if_unused=False, if_empty=False)
        await queue.close()
        engine.dispose()
