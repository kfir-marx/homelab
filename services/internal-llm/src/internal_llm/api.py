from __future__ import annotations

import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.middleware.base import RequestResponseEndpoint

from .config import Settings
from .rpc import RabbitRpcClient, RpcResponse

REQUESTS = Counter("internal_llm_requests_total", "Gateway requests", ["path", "status"])
LATENCY = Histogram("internal_llm_request_seconds", "Queued request latency", ["path"])
ALLOWED_PATHS = frozenset(("/v1/models", "/v1/chat/completions", "/v1/completions"))


class Rpc(Protocol):
    @property
    def ready(self) -> bool: ...

    async def request(self, method: str, path: str, body: dict[str, Any] | None) -> RpcResponse: ...


def create_app(settings: Settings, rpc: Rpc | None = None) -> FastAPI:
    client = rpc or RabbitRpcClient(
        settings.rabbitmq_url.get_secret_value(),
        settings.request_queue,
        settings.request_timeout_seconds,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if isinstance(client, RabbitRpcClient):
            await client.connect()
        yield
        if isinstance(client, RabbitRpcClient):
            await client.close()

    app = FastAPI(title="internal-llm", docs_url=None, redoc_url=None, lifespan=lifespan)

    def authorize(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = settings.api_key.get_secret_value()
        supplied = authorization.removeprefix("Bearer ") if authorization else ""
        if not expected or not hmac.compare_digest(supplied, expected):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid bearer token")

    async def dispatch(path: str, body: dict[str, Any] | None) -> Response:
        if body and body.get("stream") is True:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "streaming is not supported")
        try:
            with LATENCY.labels(path=path).time():
                result = await client.request("GET" if body is None else "POST", path, body)
        except TimeoutError as exc:
            REQUESTS.labels(path=path, status="timeout").inc()
            raise HTTPException(
                status.HTTP_504_GATEWAY_TIMEOUT, "inference request timed out"
            ) from exc
        except RuntimeError as exc:
            REQUESTS.labels(path=path, status="unavailable").inc()
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "queue unavailable") from exc
        REQUESTS.labels(path=path, status=str(result.status_code)).inc()
        return Response(result.body, status_code=result.status_code, media_type=result.content_type)

    @app.middleware("http")
    async def limit_request_size(request: Request, call_next: RequestResponseEndpoint) -> Response:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > settings.maximum_request_bytes:
                    return Response(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
            except ValueError:
                return Response(status_code=status.HTTP_400_BAD_REQUEST)
        return await call_next(request)

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready() -> dict[str, str]:
        if not client.ready:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "queue unavailable")
        return {"status": "ok"}

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/v1/models", dependencies=[Depends(authorize)])
    async def models() -> Response:
        return await dispatch("/v1/models", None)

    @app.post("/v1/chat/completions", dependencies=[Depends(authorize)])
    async def chat_completions(body: dict[str, Any]) -> Response:
        return await dispatch("/v1/chat/completions", body)

    @app.post("/v1/completions", dependencies=[Depends(authorize)])
    async def completions(body: dict[str, Any]) -> Response:
        return await dispatch("/v1/completions", body)

    return app
