from __future__ import annotations

import asyncio
import json
from typing import Any

import aio_pika
import httpx
from aio_pika import DeliveryMode, Message
from aio_pika.abc import AbstractIncomingMessage

from .config import Settings

ALLOWED_REQUESTS = frozenset(
    (("GET", "/v1/models"), ("POST", "/v1/chat/completions"), ("POST", "/v1/completions"))
)


def parse_request(raw: bytes) -> tuple[str, str, dict[str, Any] | None]:
    payload: dict[str, Any] = json.loads(raw)
    method = str(payload["method"])
    path = str(payload["path"])
    body = payload.get("body")
    if (method, path) not in ALLOWED_REQUESTS:
        raise ValueError("request route is not allowed")
    if body is not None and not isinstance(body, dict):
        raise ValueError("request body must be an object")
    return method, path, body


async def run(settings: Settings) -> None:
    connection = await aio_pika.connect_robust(settings.rabbitmq_url.get_secret_value())
    channel = await connection.channel(publisher_confirms=True)
    await channel.set_qos(prefetch_count=settings.worker_prefetch)
    queue = await channel.declare_queue(settings.request_queue, durable=True)
    headers = {"Authorization": f"Bearer {settings.inference_api_key.get_secret_value()}"}
    timeout = httpx.Timeout(settings.request_timeout_seconds)

    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:

        async def handle(message: AbstractIncomingMessage) -> None:
            async with message.process(ignore_processed=True):
                try:
                    method, path, body = parse_request(message.body)
                    response = await client.request(
                        method,
                        f"{settings.inference_base_url.rstrip('/')}{path.removeprefix('/v1')}",
                        json=body,
                    )
                    try:
                        response_body: object = response.json()
                    except ValueError:
                        response_body = {"error": {"message": "inference returned invalid JSON"}}
                    result = {"status_code": response.status_code, "body": response_body}
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    result = {"status_code": 400, "body": {"error": {"message": "invalid request"}}}
                except httpx.HTTPError:
                    result = {
                        "status_code": 502,
                        "body": {"error": {"message": "inference unavailable"}},
                    }

                if message.reply_to and message.correlation_id:
                    await channel.default_exchange.publish(
                        Message(
                            body=json.dumps(result, separators=(",", ":")).encode(),
                            content_type="application/json",
                            correlation_id=message.correlation_id,
                            delivery_mode=DeliveryMode.NOT_PERSISTENT,
                        ),
                        routing_key=message.reply_to,
                    )

        await queue.consume(handle)
        try:
            await asyncio.Future()
        finally:
            await connection.close()
