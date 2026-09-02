from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .clients import JobAssistantClient, TelegramClient
from .config import Settings
from .gateway import Gateway

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
LOGGER = logging.getLogger("shared-services-telegram")


class Runtime:
    last_poll_success = 0.0
    started = False


async def polling_loop(settings: Settings, gateway: Gateway, telegram: TelegramClient) -> None:
    offset = 0
    while True:
        try:
            await telegram.prepare_long_polling()
            break
        except Exception as exc:
            LOGGER.warning("poll_prepare_failed class=%s", type(exc).__name__)
            await asyncio.sleep(2)
    while True:
        try:
            updates = await telegram.get_updates(offset, settings.poll_timeout_seconds)
            Runtime.last_poll_success = time.monotonic()
            for update in updates:
                update_id = int(update.get("update_id", -1))
                await gateway.process(update)
                offset = max(offset, update_id + 1)
        except Exception as exc:
            LOGGER.warning("poll_failed class=%s", type(exc).__name__)
            await asyncio.sleep(2)


async def notification_loop(settings: Settings, gateway: Gateway) -> None:
    while True:
        await gateway.deliver_notifications()
        await asyncio.sleep(settings.notification_interval_seconds)


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or Settings()
    telegram = TelegramClient(configured)
    job_assistant = JobAssistantClient(configured)
    gateway = Gateway(configured, telegram, job_assistant)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        Runtime.started = True
        tasks = [
            asyncio.create_task(polling_loop(configured, gateway, telegram)),
            asyncio.create_task(notification_loop(configured, gateway)),
        ]
        yield
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await telegram.close()
        await job_assistant.close()

    application = FastAPI(docs_url=None, redoc_url=None, lifespan=lifespan)

    @application.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/health/ready")
    async def ready(response: Response) -> dict[str, str]:
        healthy = (
            Runtime.started
            and Runtime.last_poll_success > 0
            and time.monotonic() - Runtime.last_poll_success < configured.poll_timeout_seconds * 3
            and await job_assistant.ready()
        )
        if not healthy:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "ready" if healthy else "not-ready"}

    @application.get("/metrics")
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return application


def main() -> None:
    settings = Settings()
    uvicorn.run(
        create_app(settings),
        host=settings.listen_host,
        port=settings.listen_port,
        access_log=False,
    )


if __name__ == "__main__":
    main()
