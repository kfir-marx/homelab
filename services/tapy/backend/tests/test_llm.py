from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import SecretStr

from tapy.config import Settings
from tapy.llm import SYSTEM_PROMPT, BookingExtractor, RpcResponse
from tapy.models import EmailForAnalysis


class FakeEndpoint:
    def __init__(self, responses: list[RpcResponse | Exception]) -> None:
        self.responses = responses
        self.requests: list[dict[str, Any]] = []

    @property
    def ready(self) -> bool:
        return True

    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def request(self, body: dict[str, Any]) -> RpcResponse:
        self.requests.append(body)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def email() -> EmailForAnalysis:
    return EmailForAnalysis(
        message_id="abc123",
        subject="Reservation",
        sender="airline@example.com",
        body_text="Two passenger tickets share PNR X.",
    )


def valid() -> RpcResponse:
    content = {
        "hotel_booking": {
            "is_hotel_booking_confirmation": False,
            "booking_status": "unknown",
            "hotel_name": None,
            "city": None,
            "country": None,
            "check_in_date": None,
            "check_out_date": None,
            "guest_name": None,
            "confirmation_number": None,
        },
        "flight_booking": {
            "is_flight_booking_confirmation": True,
            "booking_status": "confirmed",
            "booking_reference": "BOOK-1",
            "people": [
                {
                    "source_id": "p1",
                    "display_name": "Ada Lovelace",
                    "given_name": "Ada",
                    "family_name": "Lovelace",
                    "roles": [],
                    "contacts": [
                        {"channel": "email", "value": "ada@example.com", "is_primary": True}
                    ],
                }
            ],
            "reservations": [
                {
                    "source_id": "r1",
                    "pnr": "SHARED-PNR",
                    "status": "confirmed",
                    "segments": [
                        {
                            "source_id": "s1",
                            "airline": "El Al",
                            "flight_number": "LY315",
                            "origin_code": "TLV",
                            "destination_code": "LHR",
                            "departure_at": "2026-10-12T08:00:00+03:00",
                            "arrival_at": "2026-10-12T12:00:00+01:00",
                        }
                    ],
                }
            ],
            "tickets": [
                {
                    "ticket_number": "001",
                    "reservation_source_id": "r1",
                    "passenger_source_id": "p1",
                    "segment_source_ids": ["s1"],
                    "amount": "500.25",
                    "currency": "EUR",
                }
            ],
        },
    }
    return RpcResponse(200, {"choices": [{"message": {"content": json.dumps(content)}}]})


@pytest.mark.asyncio
async def test_strict_fact_schema_and_fallback_order() -> None:
    internal = FakeEndpoint([TimeoutError()])
    external = FakeEndpoint([valid()])
    extractor = BookingExtractor(
        ("internal-llm", "external-ai"),
        {"internal-llm": internal, "external-ai": external},
        {"internal-llm": "local", "external-ai": "qwen"},
    )
    result = await extractor.extract(email())
    assert result.flight_booking.reservations[0].pnr == "SHARED-PNR"
    assert result.flight_booking.tickets[0].currency == "EUR"
    schema = external.requests[0]["response_format"]["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert "leader" in SYSTEM_PROMPT
    assert "never infer" in SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_invalid_output_uses_next_backend() -> None:
    invalid = RpcResponse(200, {"choices": [{"message": {"content": "no"}}]})
    extractor = BookingExtractor(
        ("external-ai", "internal-llm"),
        {"external-ai": FakeEndpoint([invalid]), "internal-llm": FakeEndpoint([valid()])},
        {"external-ai": "qwen", "internal-llm": "local"},
    )
    assert (await extractor.extract(email())).flight_booking.tickets[0].ticket_number == "001"


def test_external_only_configuration_builds_one_rpc_endpoint() -> None:
    configured = Settings(
        rabbitmq_url=SecretStr("amqps://example.invalid/vhost"),
        llm_order=("external-ai",),
        external_ai_queue="production.external-ai.requests",
        external_ai_model="alibaba:qwen-plus",
    )
    assert BookingExtractor.from_settings(configured).readiness == {"external-ai": "unavailable"}
