from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr

from tapy.config import Settings
from tapy.llm import BookingExtractor, RpcResponse
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
        subject="Your reservation",
        sender="hotel@example.com",
        body_text="Confirmed in London from 2026-10-12 through 2026-10-17.",
    )


def valid() -> RpcResponse:
    return RpcResponse(
        200,
        {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"is_hotel_booking_confirmation":true,'
                            '"booking_status":"confirmed","hotel_name":"Hotel",'
                            '"city":"London","country":"UK",'
                            '"check_in_date":"2026-10-12",'
                            '"check_out_date":"2026-10-17","guest_name":null,'
                            '"confirmation_number":null}'
                        )
                    }
                }
            ]
        },
    )


@pytest.mark.asyncio
async def test_falls_back_in_configured_order() -> None:
    internal = FakeEndpoint([TimeoutError()])
    external = FakeEndpoint([valid()])
    extractor = BookingExtractor(
        ("internal-llm", "external-ai"),
        {"internal-llm": internal, "external-ai": external},
        {"internal-llm": "local-llm", "external-ai": "alibaba:qwen-plus"},
    )
    result = await extractor.extract(email())
    assert result.is_hotel_booking_confirmation
    assert internal.requests[0]["model"] == "local-llm"
    assert external.requests[0]["model"] == "alibaba:qwen-plus"
    assert "json_schema" in external.requests[0]["response_format"]["type"]


@pytest.mark.asyncio
async def test_invalid_first_output_uses_fallback() -> None:
    invalid = RpcResponse(200, {"choices": [{"message": {"content": "no"}}]})
    extractor = BookingExtractor(
        ("external-ai", "internal-llm"),
        {"internal-llm": FakeEndpoint([valid()]), "external-ai": FakeEndpoint([invalid])},
        {"internal-llm": "local-llm", "external-ai": "alibaba:qwen-plus"},
    )
    assert (await extractor.extract(email())).hotel_name == "Hotel"


def test_external_only_configuration_builds_one_rpc_endpoint() -> None:
    configured = Settings(
        rabbitmq_url=SecretStr("amqps://example.invalid/vhost"),
        llm_order=("external-ai",),
        external_ai_queue="production.external-ai.requests",
        external_ai_model="alibaba:qwen-plus",
    )
    extractor = BookingExtractor.from_settings(configured)
    assert extractor.readiness == {"external-ai": "unavailable"}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("external-ai", ("external-ai",)),
        ("internal-llm,external-ai", ("internal-llm", "external-ai")),
    ],
)
def test_llm_order_accepts_environment_value(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
    expected: tuple[str, ...],
) -> None:
    monkeypatch.setenv("MATCHER_LLM_ORDER", value)
    assert Settings().llm_order == expected


def test_rabbitmq_is_required_for_runtime_extractor() -> None:
    with pytest.raises(ValueError, match="MATCHER_RABBITMQ_URL"):
        Settings().require_rabbitmq()
