from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from pydantic import SecretStr

from internal_llm.api import create_app
from internal_llm.config import Settings
from internal_llm.rpc import RpcResponse
from internal_llm.worker import parse_request


class FakeRpc:
    ready = True

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict[str, Any] | None]] = []

    async def request(self, method: str, path: str, body: dict[str, Any] | None) -> RpcResponse:
        self.requests.append((method, path, body))
        return RpcResponse(200, b'{"id":"queued-response"}', "application/json")


def settings() -> Settings:
    return Settings(api_key=SecretStr("test-token"))  # noqa: S106


def test_gateway_authenticates_and_queues_openai_request() -> None:
    rpc = FakeRpc()
    with TestClient(create_app(settings(), rpc)) as client:
        assert client.get("/v1/models").status_code == 401
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer test-token"},
            json={"model": "local-llm", "messages": [{"role": "user", "content": "hello"}]},
        )
    assert response.status_code == 200
    assert rpc.requests[0][0:2] == ("POST", "/v1/chat/completions")


def test_streaming_is_rejected_before_publish() -> None:
    rpc = FakeRpc()
    with TestClient(create_app(settings(), rpc)) as client:
        response = client.post(
            "/v1/completions",
            headers={"Authorization": "Bearer test-token"},
            json={"model": "local-llm", "prompt": "hello", "stream": True},
        )
    assert response.status_code == 422
    assert not rpc.requests


def test_worker_route_allowlist() -> None:
    assert parse_request(b'{"method":"GET","path":"/v1/models","body":null}') == (
        "GET",
        "/v1/models",
        None,
    )
    try:
        parse_request(b'{"method":"GET","path":"http://example.com","body":null}')
    except ValueError as exc:
        assert "not allowed" in str(exc)
    else:
        raise AssertionError("worker must reject arbitrary URLs")
