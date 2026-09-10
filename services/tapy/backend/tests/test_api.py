from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient
from pydantic import SecretStr

from tapy.api import create_app
from tapy.config import Settings
from tapy.models import EmailExtraction, EmailForAnalysis, FlightBooking, HotelBooking


class FakeExtractor:
    ready = True

    async def extract(self, email: EmailForAnalysis) -> EmailExtraction:
        del email
        return EmailExtraction(
            hotel_booking=HotelBooking(is_hotel_booking_confirmation=False),
            flight_booking=FlightBooking(is_flight_booking_confirmation=False),
        )


def settings(tmp_path: Path, *, twilio: bool = False) -> Settings:
    return Settings(
        database_url=SecretStr(f"sqlite+pysqlite:///{tmp_path / 'db.sqlite'}"),
        secure_cookies=False,
        twilio_account_sid="AC123" if twilio else "",
        twilio_auth_token=SecretStr("secret" if twilio else ""),
    )


def register(client: TestClient, name: str, email: str) -> dict[str, Any]:
    import uuid

    from tapy.database import Organization
    from tapy.organizations import issue_invitation

    with cast(Any, client.app).state.factory.begin() as session:
        organization = Organization(name=name, slug=uuid.uuid4().hex)
        session.add(organization)
        session.flush()
        _, token = issue_invitation(session, organization.id, email, "admin", None)
    response = client.post(
        "/v1/invitations/accept",
        json={"token": token, "name": name, "password": "long-password"},
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json()["user"])


def booking_payload(reference: str = "BOOK-1") -> dict[str, Any]:
    return {
        "external_reference": reference,
        "people": [
            {
                "display_name": "Parent One",
                "roles": [{"role": "group_leader", "source_method": "manual"}],
                "contacts": [{"channel": "whatsapp", "value": "+972 50 111 1111"}],
            },
            {
                "display_name": "Parent Two",
                "roles": [{"role": "group_leader", "source_method": "manual"}],
                "contacts": [{"channel": "whatsapp", "value": "+972 50 222 2222"}],
            },
            {
                "display_name": "Child",
                "roles": [{"role": "traveler", "source_method": "manual"}],
                "contacts": [],
            },
        ],
        "reservations": [
            {
                "pnr": "SHARED-PNR",
                "segments": [
                    {
                        "airline": "LY",
                        "flight_number": "LY315",
                        "origin_code": "TLV",
                        "destination_code": "LHR",
                        "departure_at": "2026-10-12T08:00:00+03:00",
                        "arrival_at": "2026-10-12T12:00:00+01:00",
                    }
                ],
            }
        ],
    }


def add_ticket(client: TestClient, booking: dict[str, Any], person: int, number: str) -> str:
    reservation = booking["reservations"][0]
    response = client.post(
        f"/v1/bookings/{booking['id']}/tickets",
        json={
            "reservation_id": reservation["id"],
            "person_id": booking["people"][person]["id"],
            "ticket_number": number,
            "segment_ids": [reservation["segments"][0]["id"]],
            "amount": "500.25",
            "currency": "EUR",
        },
    )
    assert response.status_code == 201, response.text
    return cast(
        str,
        next(
            ticket["id"]
            for ticket in response.json()["reservations"][0]["tickets"]
            if ticket["ticket_number"] == number
        ),
    )


def test_personal_and_organization_authorization(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path), extractor=FakeExtractor())
    with TestClient(app) as client:
        admin = register(client, "Admin", "admin@example.com")
        admin_cookie = client.cookies.get("tapy_session")
        own = client.post("/v1/bookings", json=booking_payload()).json()

        client.cookies.clear()
        agent = register(client, "Agent", "agent@example.com")
        agent_cookie = client.cookies.get("tapy_session")
        other_tenant_booking = client.post(
            "/v1/bookings", json=booking_payload("OTHER-TENANT")
        ).json()

        client.cookies.set("tapy_session", admin_cookie)
        membership = client.post(
            "/v1/organizations/current/invitations",
            json={"email": "agent@example.com", "role": "agent"},
        )
        assert membership.status_code == 201

        client.cookies.set("tapy_session", agent_cookie)
        assert (
            client.post(
                "/v1/invitations/accept",
                json={"token": membership.json()["invitation_path"].split("=")[1]},
            ).status_code
            == 200
        )
        switched = client.patch(
            "/v1/users/me", json={"active_organization_id": admin["active_organization_id"]}
        )
        assert switched.status_code == 200
        assert switched.json()["active_organization_role"] == "agent"
        assert client.get("/v1/bookings?scope=personal").json() == []
        assert client.get("/v1/bookings?scope=organization").status_code == 403
        assert client.get("/v1/metrics?scope=organization").status_code == 403
        assert client.get(f"/v1/bookings/{own['id']}").status_code == 404

        client.cookies.set("tapy_session", admin_cookie)
        organization_bookings = client.get("/v1/bookings?scope=organization").json()
        assert {item["id"] for item in organization_bookings} == {own["id"]}
        assert client.get("/v1/metrics?scope=organization").status_code == 200
        assert client.get(f"/v1/bookings/{other_tenant_booking['id']}").status_code == 404
        assert agent["active_organization_id"] != admin["active_organization_id"]


def test_manual_opportunity_and_success_are_rejected(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path), extractor=FakeExtractor())) as client:
        register(client, "Admin", "admin@example.com")
        booking = client.post("/v1/bookings", json=booking_payload()).json()
        ticket = add_ticket(client, booking, 0, "T1")
        response = client.post(
            "/v1/opportunities", json={"booking_id": booking["id"], "ticket_ids": [ticket]}
        )
        assert response.status_code == 409
        assert (
            client.patch(
                "/v1/opportunities/missing", json={"status": "won", "version": 1}
            ).status_code
            == 422
        )


def test_shared_pnr_is_not_mutated(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path), extractor=FakeExtractor())) as client:
        register(client, "Admin", "admin@example.com")
        booking = client.post("/v1/bookings", json=booking_payload()).json()
        assert [item["pnr"] for item in booking["reservations"]] == ["SHARED-PNR"]
