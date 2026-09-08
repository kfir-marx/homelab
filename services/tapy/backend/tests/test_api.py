import json
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr

from tapy.api import create_app
from tapy.config import Settings
from tapy.models import (
    EmailExtraction,
    EmailForAnalysis,
    FlightBooking,
    FlightTicket,
    HotelBooking,
)


class FakeExtractor:
    def __init__(self, booking: HotelBooking | EmailExtraction | None = None) -> None:
        self.booking = booking or HotelBooking(is_hotel_booking_confirmation=False)

    @property
    def ready(self) -> bool:
        return True

    async def extract(self, email: EmailForAnalysis) -> HotelBooking | EmailExtraction:
        del email
        return self.booking


class FakeReader:
    async def messages(self, access_token: str, limit: int) -> list[EmailForAnalysis]:
        assert access_token == "mail-access"  # noqa: S105
        assert limit == 20
        return [
            EmailForAnalysis(
                message_id="provider-message-1",
                subject="Hotel confirmed",
                sender="hotel@example.com",
                body_text="London 12-17 October 2026",
            )
        ]


def settings(tmp_path: Path) -> Settings:
    config = tmp_path / "flights.json"
    config.write_text(
        json.dumps(
            {
                "version": 1,
                "flights": [
                    {
                        "id": "test",
                        "label": "Test trip",
                        "arrival_date": "2026-10-12",
                        "departure_date": "2026-10-17",
                        "destination": {"city": "London", "country": "United Kingdom"},
                    }
                ],
            }
        )
    )
    return Settings(
        database_url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'db.sqlite'}"),
        flights_config_path=config,
        secure_cookies=False,
    )


def test_creates_and_authenticates_agent(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path), extractor=FakeExtractor())) as client:
        created = client.post("/v1/agents")
        assert created.status_code == 201
        body = created.json()
        assert body["access_token"].startswith("agt_")
        assert client.get("/v1/agents/me").status_code == 401
        authenticated = client.get(
            "/v1/agents/me", headers={"Authorization": f"Bearer {body['access_token']}"}
        )
        assert authenticated.json() == {
            "agent_id": body["agent_id"],
            "connected_mailboxes": [],
        }


def test_public_pages_describe_server_side_mail_access(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path), extractor=FakeExtractor())) as client:
        privacy = client.get("/privacy")
        assert privacy.status_code == 200
        assert "extension" not in privacy.text.casefold()
        assert "outlook" in privacy.text.casefold()
        assert privacy.headers["cache-control"] == "no-store"


@respx.mock
def test_google_consent_scan_match_and_idempotent_skip(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    configured = settings(tmp_path).model_copy(
        update={
            "google_oauth_client_id": "web-client.apps.googleusercontent.com",
            "google_oauth_client_secret": SecretStr("google-secret"),
            "oauth_token_encryption_key": SecretStr(Fernet.generate_key().decode()),
        }
    )
    token_route = respx.post("https://oauth2.googleapis.com/token").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "access_token": "consent-access",
                    "refresh_token": "refresh-token",
                    "scope": "https://www.googleapis.com/auth/gmail.readonly",
                },
            ),
            httpx.Response(200, json={"access_token": "mail-access"}),
            httpx.Response(200, json={"access_token": "mail-access"}),
        ]
    )
    respx.get("https://gmail.googleapis.com/gmail/v1/users/me/profile").mock(
        return_value=httpx.Response(200, json={"emailAddress": "agent@example.com"})
    )
    booking = HotelBooking(
        is_hotel_booking_confirmation=True,
        booking_status="confirmed",
        hotel_name="Test Hotel",
        city="London",
        country="United Kingdom",
        check_in_date=date(2026, 10, 12),
        check_out_date=date(2026, 10, 17),
    )
    app = create_app(
        configured,
        extractor=FakeExtractor(booking),
        mailbox_readers={"gmail": FakeReader(), "outlook": FakeReader()},
    )
    with TestClient(app) as client:
        created = client.post("/v1/agents").json()
        headers = {"Authorization": f"Bearer {created['access_token']}"}
        authorization = client.post("/v1/mailboxes/gmail/authorization", headers=headers).json()[
            "authorization_url"
        ]
        query = parse_qs(urlparse(authorization).query)
        assert query["access_type"] == ["offline"]
        assert query["scope"] == ["https://www.googleapis.com/auth/gmail.readonly"]
        callback = client.get(
            "/v1/oauth/gmail/callback",
            params={"state": query["state"][0], "code": "authorization-code"},
        )
        assert callback.status_code == 200
        first = client.post("/v1/scans", json={"provider": "gmail"}, headers=headers)
        assert first.status_code == 200
        assert first.json()["matches_found"] == 1
        matched_flight = client.get("/v1/flights", headers=headers).json()[0]
        assert matched_flight["status"] == "past"
        assert matched_flight["closed_reason"] == "hotel_match"
        assert matched_flight["matched_hotel"]["hotel_name"] == "Test Hotel"
        second = client.post("/v1/scans", json={"provider": "gmail"}, headers=headers)
        assert second.json()["messages_skipped"] == 1
    assert token_route.call_count == 3
    assert "found hotel booking" in capsys.readouterr().out


def test_regular_auth_flight_crud_and_backend_metrics(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path), extractor=FakeExtractor())) as client:
        registered = client.post(
            "/v1/auth/register",
            json={"name": "Ari Agent", "email": "ari@example.com", "password": "long-password"},
        )
        assert registered.status_code == 201
        assert registered.json()["user"]["auth_providers"] == ["password"]
        assert client.get("/v1/users/me").json()["email"] == "ari@example.com"

        created = client.post(
            "/v1/flights",
            json={
                "booking_ref": "BOOK-1",
                "passenger_name": "Pat Passenger",
                "party_size": 2,
                "email": "pat@example.com",
                "phone": "+1 555 0100",
                "origin": "JFK",
                "origin_city": "New York",
                "destination_code": "LHR",
                "destination_city": "London",
                "destination_country": "United Kingdom",
                "arrival_date": "2026-10-12",
                "departure_date": "2026-10-17",
                "flight_cost_usd": 1000,
                "hotel_cost_usd": 2000,
            },
        )
        assert created.status_code == 201
        flight_id = created.json()["id"]
        assert client.get("/v1/metrics").json()["potential_profit"] == 20

        closed = client.patch(f"/v1/flights/{flight_id}/status", json={"status": "declined"})
        assert closed.json()["status"] == "declined"
        metrics = client.get("/v1/metrics").json()
        assert metrics["open_count"] == 0
        assert metrics["declined_count"] == 1
        assert metrics["net_profit"] == 0

        assert client.post("/v1/auth/logout").status_code == 204
        assert client.get("/v1/users/me").status_code == 401


def test_manual_flight_deduplication_and_notifications(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path), extractor=FakeExtractor())) as client:
        client.post(
            "/v1/auth/register",
            json={"name": "Ari", "email": "ari@example.com", "password": "long-password"},
        )
        flight = {
            "passenger_name": " Pat Passenger ",
            "origin": "tlv",
            "origin_city": "Tel Aviv",
            "destination_code": "lhr",
            "destination_city": "London",
            "arrival_date": "2026-10-12",
            "departure_date": "2026-10-17",
        }

        assert client.post("/v1/flights", json=flight).status_code == 201
        assert client.post("/v1/flights", json=flight).status_code == 409
        assert len(client.get("/v1/flights").json()) == 1

        notifications = client.get("/v1/notifications").json()
        assert [item["kind"] for item in notifications] == [
            "flight_add_failed",
            "flight_added",
        ]
        assert all(item["read_at"] is None for item in notifications)
        notification_ids = [item["id"] for item in notifications]
        assert (
            client.post(
                "/v1/notifications/read", json={"notification_ids": notification_ids}
            ).status_code
            == 204
        )
        assert all(item["read_at"] for item in client.get("/v1/notifications").json())

        assert client.post("/v1/flights", json={"passenger_name": "Incomplete"}).status_code == 422
        assert client.get("/v1/notifications").json()[0]["kind"] == "flight_add_failed"


@respx.mock
def test_upsell_closes_only_after_twilio_accepts_message(tmp_path: Path) -> None:
    configured = settings(tmp_path).model_copy(
        update={
            "twilio_account_sid": "AC123",
            "twilio_auth_token": SecretStr("twilio-secret"),
        }
    )
    twilio = respx.post("https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json").mock(
        return_value=httpx.Response(201, json={"sid": "SM123"})
    )
    with TestClient(create_app(configured, extractor=FakeExtractor())) as client:
        client.post(
            "/v1/auth/register",
            json={"name": "Ari", "email": "ari@example.com", "password": "long-password"},
        )
        flight = client.post(
            "/v1/flights",
            json={
                "passenger_name": "Pat Passenger",
                "phone": "+972501234567",
                "origin": "TLV",
                "origin_city": "Tel Aviv",
                "destination_code": "LHR",
                "destination_city": "London",
                "arrival_date": "2026-10-12",
                "departure_date": "2026-10-17",
                "hotel_cost_usd": 2000,
            },
        ).json()

        sent = client.post(f"/v1/flights/{flight['id']}/send-upsell")

        assert sent.status_code == 200
        assert sent.json()["message_sid"] == "SM123"
        assert sent.json()["flight"]["status"] == "upsold"
        assert "To=whatsapp%3A%2B972501234567" in twilio.calls[0].request.content.decode()
        assert client.get("/v1/notifications").json()[0]["kind"] == "upsell_sent"
        metrics = client.get("/v1/metrics").json()
        assert metrics["upsold_count"] == 1
        assert metrics["net_profit"] == 20


def test_upsell_failure_keeps_flight_open_and_notifies_user(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path), extractor=FakeExtractor())) as client:
        client.post(
            "/v1/auth/register",
            json={"name": "Ari", "email": "ari@example.com", "password": "long-password"},
        )
        flight = client.post(
            "/v1/flights",
            json={
                "passenger_name": "Pat Passenger",
                "phone": "+972501234567",
                "origin": "TLV",
                "origin_city": "Tel Aviv",
                "destination_code": "LHR",
                "destination_city": "London",
                "arrival_date": "2026-10-12",
                "departure_date": "2026-10-17",
            },
        ).json()

        assert client.post(f"/v1/flights/{flight['id']}/send-upsell").status_code == 503
        assert client.get("/v1/flights").json()[0]["status"] == "open"
        assert client.get("/v1/notifications").json()[0]["kind"] == "whatsapp_failed"


@respx.mock
def test_email_confirmation_creates_one_flight_per_ticket(tmp_path: Path) -> None:
    configured = settings(tmp_path).model_copy(
        update={
            "google_oauth_client_id": "web-client.apps.googleusercontent.com",
            "google_oauth_client_secret": SecretStr("google-secret"),
            "oauth_token_encryption_key": SecretStr(Fernet.generate_key().decode()),
        }
    )
    respx.post("https://oauth2.googleapis.com/token").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "access_token": "consent-access",
                    "refresh_token": "refresh-token",
                    "scope": "https://www.googleapis.com/auth/gmail.readonly",
                },
            ),
            httpx.Response(200, json={"access_token": "mail-access"}),
        ]
    )
    respx.get("https://gmail.googleapis.com/gmail/v1/users/me/profile").mock(
        return_value=httpx.Response(200, json={"emailAddress": "agent@example.com"})
    )
    tickets = [
        FlightTicket(
            booking_ref="SHARED-PNR",
            ticket_number=f"TICKET-{number}",
            passenger_name=name,
            origin_code="TLV",
            origin_city="Tel Aviv",
            destination_code="LHR",
            destination_city="London",
            destination_country="United Kingdom",
            departure_at=datetime(2026, 10, 12, 8, tzinfo=UTC),
            return_at=datetime(2026, 10, 17, 18, tzinfo=UTC),
        )
        for number, name in enumerate(["Ada Lovelace", "Grace Hopper"], start=1)
    ]
    extraction = EmailExtraction(
        hotel_booking=HotelBooking(is_hotel_booking_confirmation=False),
        flight_booking=FlightBooking(
            is_flight_booking_confirmation=True,
            booking_status="confirmed",
            tickets=tickets,
        ),
    )
    app = create_app(
        configured,
        extractor=FakeExtractor(extraction),
        mailbox_readers={"gmail": FakeReader(), "outlook": FakeReader()},
    )
    with TestClient(app) as client:
        client.post(
            "/v1/auth/register",
            json={"name": "Ari", "email": "ari@example.com", "password": "long-password"},
        )
        authorization = client.post("/v1/mailboxes/gmail/authorization").json()
        query = parse_qs(urlparse(authorization["authorization_url"]).query)
        assert (
            client.get(
                "/v1/oauth/gmail/callback",
                params={"state": query["state"][0], "code": "authorization-code"},
            ).status_code
            == 200
        )

        scan = client.post("/v1/scans", json={"provider": "gmail"})

        assert scan.status_code == 200
        assert scan.json()["flight_tickets_found"] == 2
        assert scan.json()["flights_added"] == 2
        flights = client.get("/v1/flights").json()
        assert {flight["passenger_name"] for flight in flights} == {
            "Ada Lovelace",
            "Grace Hopper",
        }
        assert len({flight["booking_ref"] for flight in flights}) == 2
        assert all(flight["status"] == "open" for flight in flights)
        assert [item["kind"] for item in client.get("/v1/notifications").json()] == [
            "flight_added",
            "flight_added",
        ]


def test_gmail_webhook_rejects_wrong_verification_token(tmp_path: Path) -> None:
    configured = settings(tmp_path).model_copy(
        update={"webhook_verification_token": SecretStr("expected-token")}
    )
    with TestClient(create_app(configured, extractor=FakeExtractor())) as client:
        response = client.post(
            "/v1/webhooks/gmail?token=wrong",
            json={"message": {"data": "ignored"}},
        )
        assert response.status_code == 401


def test_flights_are_isolated_per_user(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path), extractor=FakeExtractor())) as client:
        client.post(
            "/v1/auth/register",
            json={"name": "First", "email": "first@example.com", "password": "first-password"},
        )
        created = client.post(
            "/v1/flights",
            json={
                "passenger_name": "Private passenger",
                "origin": "TLV",
                "origin_city": "Tel Aviv",
                "destination_code": "LHR",
                "destination_city": "London",
                "arrival_date": "2026-10-12",
                "departure_date": "2026-10-17",
            },
        ).json()
        client.post("/v1/auth/logout")
        client.post(
            "/v1/auth/register",
            json={"name": "Second", "email": "second@example.com", "password": "second-password"},
        )
        assert client.get("/v1/flights").json() == []
        assert client.get("/v1/notifications").json() == []
        assert (
            client.patch(
                f"/v1/flights/{created['id']}/status", json={"status": "declined"}
            ).status_code
            == 404
        )


@respx.mock
def test_google_social_login_creates_session(tmp_path: Path) -> None:
    configured = settings(tmp_path).model_copy(
        update={
            "google_oauth_client_id": "web-client.apps.googleusercontent.com",
            "google_oauth_client_secret": SecretStr("google-secret"),
            "oauth_token_encryption_key": SecretStr(Fernet.generate_key().decode()),
        }
    )
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(200, json={"access_token": "login-access"})
    )
    respx.get("https://openidconnect.googleapis.com/v1/userinfo").mock(
        return_value=httpx.Response(
            200,
            json={
                "sub": "google-subject",
                "email": "social@example.com",
                "email_verified": True,
                "name": "Social User",
            },
        )
    )
    with TestClient(create_app(configured, extractor=FakeExtractor())) as client:
        authorization = client.get("/v1/auth/google/authorization").json()["authorization_url"]
        query = parse_qs(urlparse(authorization).query)
        assert query["scope"] == ["openid email profile"]
        callback = client.get(
            "/v1/oauth/gmail/callback",
            params={"state": query["state"][0], "code": "authorization-code"},
            follow_redirects=False,
        )
        assert callback.status_code == 303
        me = client.get("/v1/users/me")
        assert me.status_code == 200
        assert me.json()["auth_providers"] == ["google"]
