import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from test_api import FakeExtractor, register, settings
from test_reconciliation import Evidence, flight
from test_reconciliation import evidence as evidence

from tapy.api import create_app
from tapy.database import BackgroundJob, MailboxConnection
from tapy.jobs import JobQueue
from tapy.models import EmailExtraction, EmailForAnalysis, HotelBooking
from tapy.reconciliation import record_email


@pytest.fixture
def app_client(tmp_path: Path) -> Iterator[tuple[Any, TestClient]]:
    configured = settings(tmp_path, twilio=True).model_copy(
        update={"hotel_offer_url": "https://partner.example/offer?affiliate=tapy"}
    )
    app = create_app(configured, extractor=FakeExtractor())
    with TestClient(app) as client:
        user = register(client, "Agent", "agent@example.com")
        with app.state.factory.begin() as session:
            mailbox = MailboxConnection(
                user_id=user["id"],
                provider="gmail",
                provider_account_id="a",
                email_address=user["email"],
                refresh_token="inert",  # noqa: S106 - local fixture
                scopes="readonly",
            )  # noqa: S106
            session.add(mailbox)
            session.flush()
            record_email(
                session,
                organization_id=user["active_organization_id"],
                agent_id=user["id"],
                mailbox_id=mailbox.id,
                provider="gmail",
                email=EmailForAnalysis(message_id="1", body_text="not stored"),
                extraction=EmailExtraction(
                    flight_booking=flight(),
                    hotel_booking=HotelBooking(is_hotel_booking_confirmation=False),
                ),
            )
        yield app, client


def execute(app: Any, client: TestClient, job_id: str) -> None:
    assert client.portal
    client.portal.call(app.state.job_queue.execute, job_id, app.state.run_job)


def test_scan_enqueue_progress_replay_and_tenant_scope(app_client: tuple[Any, TestClient]) -> None:
    app, client = app_client

    class Reader:
        async def pages(self, token: str, limit: int) -> AsyncIterator[list[EmailForAnalysis]]:
            yield [EmailForAnalysis(message_id="new", body_text="newsletter")]

    app.state.mailbox_readers["gmail"] = Reader()
    app.state.oauth.access_token = AsyncMock(return_value="inert")
    response = client.post(
        "/v1/scans", json={"provider": "gmail"}, headers={"Idempotency-Key": "scan1"}
    )
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert app.state.oauth.access_token.await_count == 0
    replay = client.post(
        "/v1/scans", json={"provider": "gmail"}, headers={"Idempotency-Key": "scan1"}
    )
    assert replay.json()["id"] == response.json()["id"]
    execute(app, client, response.json()["id"])
    completed = client.get(f"/v1/jobs/{response.json()['id']}").json()
    assert completed["status"] == "completed"
    assert completed["progress"]["messages_analyzed"] == 1
    client.cookies.clear()
    register(client, "Other", "other@example.com")
    assert client.get(f"/v1/jobs/{completed['id']}").status_code == 404


@respx.mock
def test_send_is_queued_and_idempotent_and_dismissal_is_final(
    app_client: tuple[Any, TestClient],
) -> None:
    app, client = app_client
    route = respx.post("https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json").mock(
        return_value=httpx.Response(201, json={"sid": "SM1"})
    )
    card = client.get("/v1/opportunities").json()[0]
    assert "potential_commission" not in card
    response = client.post(
        f"/v1/opportunities/{card['id']}/send", headers={"Idempotency-Key": "send1"}
    )
    assert response.status_code == 202
    assert route.call_count == 0
    replay = client.post(
        f"/v1/opportunities/{card['id']}/send", headers={"Idempotency-Key": "send1"}
    )
    assert replay.json()["id"] == response.json()["id"]
    execute(app, client, response.json()["id"])
    job = client.get(f"/v1/jobs/{response.json()['id']}").json()
    assert job["status"] == "completed"
    assert route.call_count == 2  # one delivery for each explicitly selected traveler
    execute(app, client, job["id"])
    assert route.call_count == 2
    card = client.get(f"/v1/opportunities/{card['id']}").json()
    assert card["status"] == "contacted"
    assert client.get("/v1/metrics").json()["won_opportunities"] == 0
    assert (
        client.patch(
            f"/v1/opportunities/{card['id']}", json={"status": "won", "version": card["version"]}
        ).status_code
        == 422
    )
    dismissed = client.patch(
        f"/v1/opportunities/{card['id']}", json={"status": "declined", "version": card["version"]}
    )
    assert dismissed.status_code == 200
    assert client.post(f"/v1/opportunities/{card['id']}/send").status_code == 409
    assert (
        client.get(f"/v1/opportunities/{card['id']}/audit").json()[-1]["reason"]
        == "agent_dismissed"
    )


@respx.mock
def test_invalidated_queued_send_never_reaches_provider(app_client: tuple[Any, TestClient]) -> None:
    app, client = app_client
    card = client.get("/v1/opportunities").json()[0]
    job = client.post(f"/v1/opportunities/{card['id']}/send").json()
    current = client.get(f"/v1/opportunities/{card['id']}").json()
    assert (
        client.patch(
            f"/v1/opportunities/{card['id']}",
            json={"status": "closed", "version": current["version"]},
        ).status_code
        == 200
    )
    execute(app, client, job["id"])
    assert client.get(f"/v1/jobs/{job['id']}").json()["status"] == "failed"
    assert not respx.calls


@respx.mock
def test_partial_failure_is_visible_without_automatic_resend(
    app_client: tuple[Any, TestClient],
) -> None:
    app, client = app_client
    route = respx.post("https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json").mock(
        side_effect=[httpx.Response(201, json={"sid": "SM1"}), httpx.Response(500)]
    )
    card = client.get("/v1/opportunities").json()[0]
    job = client.post(f"/v1/opportunities/{card['id']}/send").json()
    execute(app, client, job["id"])
    failed = client.get(f"/v1/jobs/{job['id']}").json()
    assert failed["status"] == "failed"
    assert failed["result"]["status"] == "partial"
    assert {d["status"] for d in failed["result"]["deliveries"]} == {"submitted", "failed"}
    execute(app, client, job["id"])
    assert route.call_count == 2


@pytest.mark.asyncio
async def test_job_retries_leases_failure_and_duplicate_consumers(
    evidence: Evidence, tmp_path: Path
) -> None:
    factory, ids = evidence
    queue = JobQueue(settings(tmp_path), factory)
    job = queue.enqueue(
        user_id=ids["agent_id"],
        organization_id=ids["organization_id"],
        kind="scan",
        payload={},
        key="one",
    )
    calls = 0

    async def fail(record: BackgroundJob) -> dict[str, object]:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        raise RuntimeError("SECRET must never appear")

    for attempt in range(3):
        with factory.begin() as session:
            row = session.get(BackgroundJob, job.id)
            assert row
            row.available_at = datetime.now(UTC) - timedelta(seconds=1)
        await asyncio.gather(queue.execute(job.id, fail), queue.execute(job.id, fail))
        with factory() as session:
            row = session.get(BackgroundJob, job.id)
            assert row
            assert row.attempts == attempt + 1
            assert "SECRET" not in (row.error or "")
            assert row.status == ("failed" if attempt == 2 else "retrying")
    assert calls == 3


def test_stale_dismissal_version_rejected(app_client: tuple[Any, TestClient]) -> None:
    _, client = app_client
    card = client.get("/v1/opportunities").json()[0]
    assert client.patch(
        f"/v1/opportunities/{card['id']}",
        json={"status": "declined", "version": card["version"] - 1},
    ).status_code in {409, 422}
    assert client.get(f"/v1/opportunities/{card['id']}").json()["status"] == "open"


def test_mailbox_scope_cannot_follow_ui_tenant_switch(app_client: tuple[Any, TestClient]) -> None:
    _, client = app_client
    other = client.post("/v1/organizations", json={"name": "Another agency"}).json()
    client.patch("/v1/users/me", json={"active_organization_id": other["organization_id"]})
    assert client.post("/v1/scans", json={"provider": "gmail"}).status_code == 409


@pytest.mark.asyncio
async def test_stale_worker_cannot_overwrite_newer_lease(
    evidence: Evidence, tmp_path: Path
) -> None:
    factory, ids = evidence
    queue = JobQueue(settings(tmp_path), factory)
    job = queue.enqueue(
        user_id=ids["agent_id"],
        organization_id=ids["organization_id"],
        kind="scan",
        payload={},
        key="stale",
    )

    async def superseded(record: BackgroundJob) -> dict[str, object]:
        with factory.begin() as session:
            newer = session.get(BackgroundJob, record.id)
            assert newer
            newer.attempts += 1
            newer.status = "retrying"
        return {"stale": True}

    await queue.execute(job.id, superseded)
    with factory() as session:
        current = session.get(BackgroundJob, job.id)
        assert current and current.status == "retrying" and current.result == {}
