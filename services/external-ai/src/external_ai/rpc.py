from __future__ import annotations

import asyncio
import json
from typing import Any

import aio_pika
import httpx
from aio_pika import DeliveryMode, Message
from aio_pika.abc import AbstractIncomingMessage
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings, resolve_model
from .models import Job
from .worker import ExecutionFailure, execute, process_one


def parse_request(raw: bytes) -> dict[str, Any]:
    payload = json.loads(raw)
    if payload.get("method") != "POST" or payload.get("path") != "/v1/chat/completions":
        raise ValueError("request route is not allowed")
    body = payload.get("body")
    if not isinstance(body, dict) or body.get("stream") is True:
        raise ValueError("request body is invalid")
    return body


def _prompt(messages: object) -> str:
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages are required")
    lines: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("message is invalid")
        role, content = message.get("role"), message.get("content")
        if not isinstance(role, str) or not isinstance(content, str):
            raise ValueError("message is invalid")
        lines.append(f"{role.upper()}: {content}")
    return "\n\n".join(lines)


def execute_rpc(body: dict[str, Any], settings: Settings) -> dict[str, Any]:
    requested_model = body.get("model")
    if not isinstance(requested_model, str):
        raise ValueError("model is required")
    default_reasoning = (
        "none" if requested_model.casefold().startswith(("alibaba:", "qwen")) else "high"
    )
    requested_reasoning = body.get("reasoning_effort", default_reasoning)
    if not isinstance(requested_reasoning, str):
        raise ValueError("reasoning_effort is invalid")
    model, reasoning = resolve_model(requested_model, requested_reasoning)
    if model.startswith("alibaba:"):
        api_key = settings.alibaba_api_key.get_secret_value()
        if not api_key:
            raise ExecutionFailure("authentication", False)
        request = dict(body)
        request["model"] = model.removeprefix("alibaba:")
        request.pop("reasoning_effort", None)
        response_format = request.get("response_format")
        if isinstance(response_format, dict) and response_format.get("type") == "json_schema":
            provider_schema = response_format.get("json_schema", {}).get("schema")
            messages = list(request.get("messages", []))
            messages.insert(
                0,
                {
                    "role": "system",
                    "content": "Return only JSON conforming to this schema: "
                    + json.dumps(provider_schema, separators=(",", ":")),
                },
            )
            request["messages"] = messages
            request["response_format"] = {"type": "json_object"}
        try:
            response = httpx.post(
                f"{settings.alibaba_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=request,
                timeout=settings.rpc_timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise TypeError
            return payload
        except httpx.TimeoutException as exc:
            raise ExecutionFailure("timeout", True) from exc
        except httpx.HTTPStatusError as exc:
            raise ExecutionFailure(f"provider_http_{exc.response.status_code}", False) from exc
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            raise ExecutionFailure("execution_failed", False) from exc
    schema: dict[str, object] | None = None
    response_format = body.get("response_format")
    if isinstance(response_format, dict) and response_format.get("type") == "json_schema":
        candidate = response_format.get("json_schema", {}).get("schema")
        if isinstance(candidate, dict):
            schema = candidate
    job = Job(
        public_id="RPC",
        requester="rabbitmq",
        idempotency_key="rpc",
        prompt=_prompt(body.get("messages")),
        model=model,
        reasoning=reasoning,
        output_schema=schema,
        timeout_seconds=settings.rpc_timeout_seconds,
        correlation={},
    )
    result, usage = execute(job, settings)
    return {
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": result}}],
        "usage": usage,
    }


async def run(factory: sessionmaker[Session], settings: Settings) -> None:
    connection = await aio_pika.connect_robust(settings.rabbitmq_url.get_secret_value())
    channel = await connection.channel(publisher_confirms=True)
    await channel.set_qos(prefetch_count=1)
    queue = await channel.declare_queue(settings.request_queue, durable=True)
    execution_lock = asyncio.Lock()

    async def handle(message: AbstractIncomingMessage) -> None:
        async with message.process(ignore_processed=True):
            try:
                body = parse_request(message.body)
                async with execution_lock:
                    response_body = await asyncio.to_thread(execute_rpc, body, settings)
                result = {"status_code": 200, "body": response_body}
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                result = {"status_code": 400, "body": {"error": {"message": "invalid request"}}}
            except ExecutionFailure as exc:
                result = {
                    "status_code": 502,
                    "body": {"error": {"message": "external model unavailable", "code": exc.code}},
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
        while True:
            async with execution_lock:
                processed = await asyncio.to_thread(process_one, factory, settings)
            if not processed:
                await asyncio.sleep(settings.poll_seconds)
    finally:
        await connection.close()
