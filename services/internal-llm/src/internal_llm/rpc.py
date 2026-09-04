from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Any

import aio_pika
from aio_pika import DeliveryMode, Message
from aio_pika.abc import AbstractIncomingMessage, AbstractQueue, AbstractRobustConnection


@dataclass(frozen=True)
class RpcResponse:
    status_code: int
    body: bytes
    content_type: str


class RabbitRpcClient:
    def __init__(self, url: str, queue_name: str, timeout_seconds: float) -> None:
        self._url = url
        self._queue_name = queue_name
        self._timeout_seconds = timeout_seconds
        self._connection: AbstractRobustConnection | None = None
        self._callback_queue: AbstractQueue | None = None
        self._futures: dict[str, asyncio.Future[RpcResponse]] = {}

    @property
    def ready(self) -> bool:
        return self._connection is not None and not self._connection.is_closed

    async def connect(self) -> None:
        self._connection = await aio_pika.connect_robust(self._url)
        channel = await self._connection.channel(publisher_confirms=True)
        await channel.declare_queue(self._queue_name, durable=True)
        callback_queue = await channel.declare_queue(exclusive=True, auto_delete=True)
        self._callback_queue = callback_queue
        await callback_queue.consume(self._on_response, no_ack=True)

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
        for future in self._futures.values():
            if not future.done():
                future.cancel()
        self._futures.clear()

    async def _on_response(self, message: AbstractIncomingMessage) -> None:
        correlation_id = message.correlation_id
        if not correlation_id:
            return
        future = self._futures.pop(correlation_id, None)
        if future is None or future.done():
            return
        try:
            payload: dict[str, Any] = json.loads(message.body)
            future.set_result(
                RpcResponse(
                    status_code=int(payload["status_code"]),
                    body=json.dumps(payload["body"], separators=(",", ":")).encode(),
                    content_type="application/json",
                )
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            future.set_exception(RuntimeError("invalid worker response"))

    async def request(self, method: str, path: str, body: dict[str, Any] | None) -> RpcResponse:
        if self._connection is None or self._callback_queue is None:
            raise RuntimeError("RabbitMQ is not connected")
        channel = await self._connection.channel(publisher_confirms=True)
        correlation_id = uuid.uuid4().hex
        future: asyncio.Future[RpcResponse] = asyncio.get_running_loop().create_future()
        self._futures[correlation_id] = future
        envelope = {"method": method, "path": path, "body": body}
        try:
            await channel.default_exchange.publish(
                Message(
                    body=json.dumps(envelope, separators=(",", ":")).encode(),
                    content_type="application/json",
                    correlation_id=correlation_id,
                    reply_to=self._callback_queue.name,
                    delivery_mode=DeliveryMode.PERSISTENT,
                    expiration=int(self._timeout_seconds * 1000),
                ),
                routing_key=self._queue_name,
                mandatory=True,
            )
            async with asyncio.timeout(self._timeout_seconds):
                return await future
        finally:
            self._futures.pop(correlation_id, None)
            await channel.close()
