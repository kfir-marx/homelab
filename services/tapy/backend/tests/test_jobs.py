import asyncio
import base64
import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from test_api import FakeExtractor, register, settings
from test_reconciliation import Evidence, flight
from test_reconciliation import evidence as evidence

from tapy.api import create_app
from tapy.database import BackgroundJob, MailboxConnection, MessageDelivery, ProcessedMessage
from tapy.jobs import JobQueue
from tapy.models import EmailExtraction, EmailForAnalysis, HotelBooking
from tapy.reconciliation import record_email


@pytest.fixture
def app_client(tmp_path: Path) -> Iterator[tuple[Any, TestClient]]:
    configured = settings(tmp_path, twilio=True).model_copy(
        update={
            "hotel_offer_url": "https://partner.example/offer?affiliate=tapy",
            "webhook_verification_token": SecretStr("webhook-test"),
            "gmail_pubsub_topic": "projects/test/topics/gmail",
        }
    )
    app = create_app(configured, extractor=FakeExtractor())
    with TestClient(app) as client:
        user = register(client, "Agent", "agent@example.com")
        with app.state.factory.begin() as session:
            mailbox = MailboxConnection(
                user_id=user["id"],
                organization_id=user["active_organization_id"],
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


def test_gmail_webhook_creates_one_job_per_incoming_message(
    app_client: tuple[Any, TestClient],
) -> None:
    app, client = app_client

    class Reader:
        def __init__(self) -> None:
            self.history_calls: list[str] = []
            self.message_calls: list[str] = []

        async def history(self, token: str, start_history_id: str) -> tuple[list[str], str]:
            del token
            self.history_calls.append(start_history_id)
            return ["incoming-1", "incoming-1"], "102"

        async def message(self, token: str, message_id: str) -> EmailForAnalysis:
            del token
            self.message_calls.append(message_id)
            return EmailForAnalysis(message_id=message_id, body_text="booking confirmation")

    reader = Reader()
    app.state.mailbox_readers["gmail"] = reader
    app.state.oauth.access_token = AsyncMock(return_value="access")
    with app.state.factory.begin() as session:
        mailbox = session.scalar(select(MailboxConnection))
        assert mailbox
        mailbox.webhook_cursor = "100"
        email_address = mailbox.email_address

    data = base64.urlsafe_b64encode(
        json.dumps({"emailAddress": email_address, "historyId": "102"}).encode()
    ).decode().rstrip("=")
    response = client.post(
        "/v1/webhooks/gmail?token=webhook-test", json={"message": {"data": data}}
    )
    assert response.status_code == 202
    replay = client.post(
        "/v1/webhooks/gmail?token=webhook-test", json={"message": {"data": data}}
    )
    assert replay.status_code == 202

    with app.state.factory() as session:
        history_jobs = session.scalars(
            select(BackgroundJob).where(BackgroundJob.kind == "gmail_history")
        ).all()
        assert len(history_jobs) == 1
        assert not session.scalars(
            select(BackgroundJob).where(BackgroundJob.kind == "scan")
        ).all()
    execute(app, client, history_jobs[0].id)
    assert reader.history_calls == ["100"]

    with app.state.factory() as session:
        incoming_jobs = session.scalars(
            select(BackgroundJob).where(BackgroundJob.kind == "incoming_email")
        ).all()
        assert len(incoming_jobs) == 1
        mailbox = session.scalar(select(MailboxConnection))
        assert mailbox and mailbox.webhook_cursor == "102"
    execute(app, client, incoming_jobs[0].id)
    assert reader.message_calls == ["incoming-1"]
    completed = client.get(f"/v1/jobs/{incoming_jobs[0].id}").json()
    assert completed["status"] == "completed"
    assert completed["result"]["messages_seen"] == 1
    assert completed["result"]["messages_analyzed"] == 1
    with app.state.factory() as session:
        assert session.scalar(
            select(ProcessedMessage).where(
                ProcessedMessage.provider_message_id == "incoming-1"
            )
        )


@respx.mock
def test_gmail_watch_filters_inbox_and_preserves_consumed_cursor(
    app_client: tuple[Any, TestClient],
) -> None:
    app, client = app_client
    route = respx.post("https://gmail.googleapis.com/gmail/v1/users/me/watch").mock(
        return_value=httpx.Response(
            200, json={"historyId": "999", "expiration": "2000000000000"}
        )
    )
    app.state.oauth.access_token = AsyncMock(return_value="access")
    with app.state.factory.begin() as session:
        mailbox = session.scalar(select(MailboxConnection))
        assert mailbox
        mailbox.webhook_cursor = "100"
    assert client.portal
    assert client.portal.call(app.state.webhooks.ensure, mailbox)
    assert json.loads(route.calls[0].request.content) == {
        "topicName": "projects/test/topics/gmail",
        "labelIds": ["INBOX"],
        "labelFilterBehavior": "INCLUDE",
    }
    with app.state.factory() as session:
        current = session.get(MailboxConnection, mailbox.id)
        assert current and current.webhook_cursor == "100"


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
    with app.state.factory() as session:
        rendered_messages = set(session.scalars(select(MessageDelivery.rendered_message)).all())
        assert (
            "Hi Ada Lovelace, here are accommodation options for your upcoming trip: "
            "https://partner.example/offer?affiliate=tapy. "
            "Please contact your travel agent if you have any questions."
        ) in rendered_messages
    form = parse_qs(route.calls[0].request.content.decode())
    assert form["From"] == ["whatsapp:+972553088930"]
    assert form["ContentSid"] == ["HX0123456789abcdef0123456789abcdef"]
    assert json.loads(form["ContentVariables"][0]) == {
        "1": "Ada Lovelace",
        "2": "https://partner.example/offer?affiliate=tapy",
    }
    assert "Body" not in form
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
    app, client = app_client
    from tapy.database import Organization, OrganizationMembership

    user = client.get("/v1/users/me").json()
    with app.state.factory.begin() as session:
        other = Organization(name="Other", slug="other")
        session.add(other)
        session.flush()
        session.add(
            OrganizationMembership(organization_id=other.id, user_id=user["id"], role="admin")
        )
    client.patch("/v1/users/me", json={"active_organization_id": other.id})
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
