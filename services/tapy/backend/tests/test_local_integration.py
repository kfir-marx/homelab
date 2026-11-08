"""Opt-in tests for disposable local PostgreSQL/RabbitMQ; never use a live database."""

import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable
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
        other_user, _ = new_agent(session)
        mailbox = MailboxConnection(
            user_id=user.id,
            organization_id=user.active_organization_id,
            provider="gmail",
            provider_account_id=uuid.uuid4().hex,
            email_address=user.email,
            refresh_token="inert",  # noqa: S106 - local fixture
            scopes="readonly",
        )  # noqa: S106
        other_mailbox = MailboxConnection(
            user_id=other_user.id,
            organization_id=other_user.active_organization_id,
            provider="gmail",
            provider_account_id=uuid.uuid4().hex,
            email_address=other_user.email,
            refresh_token="inert",  # noqa: S106 - local fixture
            scopes="readonly",
        )  # noqa: S106
        session.add_all((mailbox, other_mailbox))
        session.flush()
        assert user.active_organization_id and other_user.active_organization_id
        organization_id, user_id, mailbox_id = user.active_organization_id, user.id, mailbox.id
        other_ids = (other_user.active_organization_id, other_user.id, other_mailbox.id)

    def ingest(item: tuple[str, str, str, int]) -> list[str]:
        current_organization, current_user, current_mailbox, _ = item
        with factory.begin() as session:
            return record_email(
                session,
                organization_id=current_organization,
                agent_id=current_user,
                mailbox_id=current_mailbox,
                provider="gmail",
                email=EmailForAnalysis(message_id="same", body_text="inert"),
                extraction=EmailExtraction(
                    flight_booking=flight(),
                    hotel_booking=HotelBooking(is_hotel_booking_confirmation=False),
                ),
            )

    first_items = [(organization_id, user_id, mailbox_id, index) for index in range(4)]
    second_items = [(*other_ids, index) for index in range(4)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(ingest, [*first_items, *second_items]))
    with factory() as session:
        count_rows = session.execute(
            select(UpsellOpportunity.organization_id, func.count(UpsellOpportunity.id)).group_by(
                UpsellOpportunity.organization_id
            )
        ).all()
        counts: dict[str, int] = {
            current_organization: count for current_organization, count in count_rows
        }
        assert counts == {organization_id: 1, other_ids[0]: 1}
    assert sum(len(result) for result in results) == 2

    first_queue = JobQueue(settings, factory)
    second_queue = JobQueue(settings, factory)

    def enqueue_incoming(_: int) -> str:
        return first_queue.enqueue(
            user_id=user_id,
            organization_id=organization_id,
            kind="incoming_email",
            payload={"mailbox_id": mailbox_id, "message_id": "concurrent"},
            key=f"incoming-email:{mailbox_id}:concurrent",
        ).id

    with ThreadPoolExecutor(max_workers=4) as pool:
        incoming_ids = list(pool.map(enqueue_incoming, range(4)))
    assert len(set(incoming_ids)) == 1
    with factory() as session:
        assert (
            session.scalar(
                select(func.count(BackgroundJob.id)).where(
                    BackgroundJob.idempotency_key == f"incoming-email:{mailbox_id}:concurrent"
                )
            )
            == 1
        )

    await first_queue.connect()
    await second_queue.connect()
    parallel_ids: set[str] = set()
    worker_by_job: dict[str, str] = {}
    parallel_started = asyncio.Event()
    release_parallel = asyncio.Event()
    duplicate_id = ""
    duplicate_executions = 0
    duplicate_done = asyncio.Event()

    def handler(label: str) -> Callable[[BackgroundJob], Awaitable[dict[str, object]]]:
        async def run(job: BackgroundJob) -> dict[str, object]:
            nonlocal duplicate_executions
            if job.id in parallel_ids:
                worker_by_job[job.id] = label
                if len(worker_by_job) == 2:
                    parallel_started.set()
                await release_parallel.wait()
            elif job.id == duplicate_id:
                duplicate_executions += 1
                duplicate_done.set()
            return {"ok": True}

        return run

    first_task = asyncio.create_task(first_queue.consume(handler("first")))
    second_task = asyncio.create_task(second_queue.consume(handler("second")))
    try:
        jobs = [
            first_queue.enqueue(
                user_id=user_id,
                organization_id=organization_id,
                kind="scan",
                payload={},
                key=f"parallel-{index}",
            )
            for index in range(2)
        ]
        parallel_ids.update(job.id for job in jobs)
        for job in jobs:
            await first_queue.publish(job.id)
        await asyncio.wait_for(parallel_started.wait(), 20)
        assert set(worker_by_job) == parallel_ids
        assert set(worker_by_job.values()) == {"first", "second"}
        release_parallel.set()

        duplicate = first_queue.enqueue(
            user_id=user_id,
            organization_id=organization_id,
            kind="scan",
            payload={},
            key="duplicate-delivery",
        )
        duplicate_id = duplicate.id
        await first_queue.publish(duplicate.id)
        await asyncio.wait_for(duplicate_done.wait(), 20)
        await first_queue.publish(duplicate.id)
        await first_queue.publish(duplicate.id)
        await asyncio.sleep(0.5)
        with factory() as session:
            result = session.get(BackgroundJob, duplicate.id)
            assert result and result.status == "completed" and result.attempts == 1
            for job in jobs:
                current = session.get(BackgroundJob, job.id)
                assert current and current.status == "completed"
        assert duplicate_executions == 1
    finally:
        first_task.cancel()
        second_task.cancel()
        with suppress(asyncio.CancelledError):
            await first_task
        with suppress(asyncio.CancelledError):
            await second_task
        assert first_queue.queue
        await first_queue.queue.delete(if_unused=False, if_empty=False)
        await first_queue.close()
        await second_queue.close()
        engine.dispose()
