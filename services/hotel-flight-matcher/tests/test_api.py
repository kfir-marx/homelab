import json
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr

from hotel_flight_matcher.api import create_app
from hotel_flight_matcher.config import Settings
from hotel_flight_matcher.models import EmailForAnalysis, HotelBooking


class FakeExtractor:
    def __init__(self, booking: HotelBooking | None = None) -> None:
        self.booking = booking or HotelBooking(is_hotel_booking_confirmation=False)

    @property
    def ready(self) -> bool:
        return True

    async def extract(self, email: EmailForAnalysis) -> HotelBooking:
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
        second = client.post("/v1/scans", json={"provider": "gmail"}, headers=headers)
        assert second.json()["messages_skipped"] == 1
    assert token_route.call_count == 3
    assert "found hotel booking" in capsys.readouterr().out
