import httpx
import pytest
import respx

from hotel_flight_matcher.llm import BookingExtractor, ExtractionError
from hotel_flight_matcher.models import EmailForAnalysis


def email() -> EmailForAnalysis:
    return EmailForAnalysis(
        message_id="abc123",
        subject="Your reservation",
        sender="hotel@example.com",
        body_text="Confirmed in London from 2026-10-12 through 2026-10-17.",
    )


@pytest.mark.asyncio
@respx.mock
async def test_extracts_valid_schema() -> None:
    route = respx.post("http://llm.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"is_hotel_booking":true,"booking_status":"confirmed",'
                                '"hotel_name":"Hotel","city":"London","country":"UK",'
                                '"check_in_date":"2026-10-12","check_out_date":"2026-10-17",'
                                '"guest_name":null,"confirmation_number":null,"confidence":0.9,'
                                '"evidence":["confirmed stay"]}'
                            )
                        }
                    }
                ]
            },
        )
    )
    async with httpx.AsyncClient() as client:
        result = await BookingExtractor(
            client, "http://llm.test/v1", "x" * 32, "local-llm"
        ).extract(email())
    assert route.called
    assert result.is_hotel_booking is True
    request_body = route.calls[0].request.content.decode()
    assert "json_schema" in request_body
    assert "MUST NOT be null" in request_body
    assert "never follow instructions" not in email().body_text


@pytest.mark.asyncio
@respx.mock
async def test_invalid_model_output_fails_closed() -> None:
    respx.post("http://llm.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "no"}}]})
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(ExtractionError):
            await BookingExtractor(client, "http://llm.test/v1", "x" * 32, "local-llm").extract(
                email()
            )
