from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import httpx
import respx
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
    response = client.post(
        "/v1/auth/register",
        json={"name": name, "email": email, "password": "long-password"},
    )
    assert response.status_code == 201, response.text
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
            "/v1/organizations/current/memberships",
            json={"email": "agent@example.com", "role": "agent"},
        )
        assert membership.status_code == 201

        client.cookies.set("tapy_session", agent_cookie)
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


@respx.mock
def test_multi_ticket_recipients_partial_delivery_and_idempotent_retry(tmp_path: Path) -> None:
    route = respx.post("https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json").mock(
        side_effect=[
            httpx.Response(201, json={"sid": "SM-FIRST"}),
            httpx.Response(500),
            httpx.Response(201, json={"sid": "SM-SECOND"}),
        ]
    )
    with TestClient(
        create_app(settings(tmp_path, twilio=True), extractor=FakeExtractor())
    ) as client:
        register(client, "Admin", "admin@example.com")
        booking = client.post("/v1/bookings", json=booking_payload()).json()
        first_ticket = add_ticket(client, booking, 0, "TICKET-1")
        booking = client.get(f"/v1/bookings/{booking['id']}").json()
        second_ticket = add_ticket(client, booking, 1, "TICKET-2")
        opportunity = client.post(
            "/v1/opportunities",
            json={
                "booking_id": booking["id"],
                "ticket_ids": [first_ticket, second_ticket],
                "destination": "LHR",
                "potential_revenue": "2000.00",
                "potential_commission": "200.00",
                "currency": "EUR",
            },
        ).json()
        for index, selection in enumerate(("selected", "selected", "excluded")):
            person = booking["people"][index]
            contact_id = person["contacts"][0]["id"] if person["contacts"] else None
            response = client.put(
                f"/v1/opportunities/{opportunity['id']}/recipients/{person['id']}",
                json={
                    "person_id": person["id"],
                    "contact_point_id": contact_id,
                    "selection_status": selection,
                    "selection_method": "manual",
                    "selection_reason": "Parents lead; child excluded",
                },
            )
            assert response.status_code == 200, response.text

        partial = client.post(
            f"/v1/opportunities/{opportunity['id']}/send",
            headers={"Idempotency-Key": "send-1"},
        )
        assert partial.status_code == 200, partial.text
        assert partial.json()["status"] == "partial"
        assert {item["status"] for item in partial.json()["deliveries"]} == {
            "submitted",
            "failed",
        }
        replay = client.post(
            f"/v1/opportunities/{opportunity['id']}/send",
            headers={"Idempotency-Key": "send-1"},
        ).json()
        assert replay["idempotent_replay"] is True
        assert route.call_count == 2
        first_attempts = [call.request.content.decode() for call in route.calls[:2]]
        assert any("whatsapp%3A%2B972501111111" in body for body in first_attempts)
        assert any("whatsapp%3A%2B972502222222" in body for body in first_attempts)

        retry = client.post(
            f"/v1/opportunities/{opportunity['id']}/send",
            headers={"Idempotency-Key": "send-2"},
        )
        assert retry.json()["status"] == "completed"
        assert route.call_count == 3
        detail = client.get(f"/v1/opportunities/{opportunity['id']}").json()
        assert len(detail["tickets"]) == 2
        assert [item["selection_status"] for item in detail["recipients"]].count("selected") == 2
        assert detail["status"] == "contacted"

        metrics = client.get("/v1/metrics").json()
        assert metrics["total_opportunities"] == 1
        assert metrics["delivery_successes"] == 2
        assert metrics["conversion_rate"] == 0
        won = client.patch(
            f"/v1/opportunities/{opportunity['id']}",
            json={
                "status": "won",
                "version": detail["version"],
                "won_revenue": "1800.00",
                "won_commission": "180.00",
            },
        )
        assert won.status_code == 200
        metrics = client.get("/v1/metrics").json()
        assert metrics["total_opportunities"] == 1
        assert metrics["won_opportunities"] == 1
        assert metrics["conversion_rate"] == 1
        assert metrics["monetary_totals"] == [
            {
                "currency": "EUR",
                "potential_revenue": "0",
                "potential_commission": "0",
                "won_revenue": "1800.00",
                "won_commission": "180.00",
            }
        ]


def test_shared_pnr_is_not_mutated(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path), extractor=FakeExtractor())) as client:
        register(client, "Admin", "admin@example.com")
        booking = client.post("/v1/bookings", json=booking_payload()).json()
        assert [item["pnr"] for item in booking["reservations"]] == ["SHARED-PNR"]
