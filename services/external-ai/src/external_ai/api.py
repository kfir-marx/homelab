from __future__ import annotations

import hmac
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text

from .broker import submit
from .config import Settings, resolve_model
from .database import initialize, make_engine, make_factory
from .models import Job

SUBMISSIONS = Counter("external_ai_submissions_total", "Submitted jobs", ["requester"])
QUEUE = Gauge("external_ai_queue_depth", "Queued jobs", ["requester", "status"])
QUEUE_OLDEST = Gauge(
    "external_ai_oldest_queued_job_seconds", "Age of the oldest queued job", ["requester"]
)


class SubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requester: str
    idempotency_key: str = Field(min_length=8, max_length=200)
    prompt: str = Field(min_length=1)
    model: str
    reasoning_effort: str
    output_schema: dict[str, Any] | None = None
    timeout_seconds: int | None = None
    correlation: dict[str, str | int | bool] = Field(default_factory=dict)


class JobResponse(BaseModel):
    job_id: str
    status: str
    requester: str
    model: str
    reasoning_effort: str
    result: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = make_engine(settings)
    initialize(engine)
    factory = make_factory(engine)
    app = FastAPI(title="external-ai", docs_url=None, redoc_url=None)

    def requester(authorization: Annotated[str | None, Header()] = None) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED)
        supplied = authorization.removeprefix("Bearer ")
        candidates = {
            "homelab-assistant": settings.homelab_assistant_token.get_secret_value(),
            "job-assistant": settings.job_assistant_token.get_secret_value(),
        }
        for name, expected in candidates.items():
            if expected and hmac.compare_digest(supplied, expected):
                return name
        raise HTTPException(status.HTTP_401_UNAUTHORIZED)

    def response_for(job: Job, include_result: bool = True) -> JobResponse:
        return JobResponse(
            job_id=job.public_id,
            status=job.status,
            requester=job.requester,
            model=job.model,
            reasoning_effort=job.reasoning,
            result=job.result if include_result and job.status == "completed" else None,
            usage=job.usage,
            error_code=job.error_code,
        )

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready(response: Response) -> dict[str, str]:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "not-ready"}
        return {"status": "ready"}

    @app.post("/v1/jobs", response_model=JobResponse, status_code=202)
    def create_job(body: SubmitRequest, actor: str = Depends(requester)) -> JobResponse:
        if body.requester != actor:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "requester does not match credential")
        if len(body.prompt.encode()) > settings.maximum_prompt_bytes:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "prompt too large")
        try:
            model, reasoning = resolve_model(body.model, body.reasoning_effort)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        timeout = body.timeout_seconds or settings.default_timeout_seconds
        if timeout < 30 or timeout > settings.maximum_timeout_seconds:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "timeout outside policy")
        with factory.begin() as session:
            try:
                job = submit(
                    session,
                    requester=actor,
                    idempotency_key=body.idempotency_key,
                    prompt=body.prompt,
                    model=model,
                    reasoning=reasoning,
                    output_schema=body.output_schema,
                    timeout_seconds=timeout,
                    correlation=body.correlation,
                )
            except ValueError as exc:
                raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
            result = response_for(job, include_result=False)
        SUBMISSIONS.labels(requester=actor).inc()
        return result

    @app.get("/v1/jobs/{job_id}", response_model=JobResponse)
    def get_job(job_id: str, actor: str = Depends(requester)) -> JobResponse:
        with factory() as session:
            job = session.scalar(select(Job).where(Job.public_id == job_id, Job.requester == actor))
            if not job:
                raise HTTPException(status.HTTP_404_NOT_FOUND)
            return response_for(job)

    @app.post("/v1/jobs/{job_id}/cancel", response_model=JobResponse)
    def cancel_job(job_id: str, actor: str = Depends(requester)) -> JobResponse:
        with factory.begin() as session:
            job = session.scalar(select(Job).where(Job.public_id == job_id, Job.requester == actor))
            if not job:
                raise HTTPException(status.HTTP_404_NOT_FOUND)
            if job.status == "queued":
                job.status = "cancelled"
            elif job.status == "running":
                job.status = "cancel_requested"
            return response_for(job, include_result=False)

    @app.get("/metrics")
    def metrics() -> Response:
        QUEUE.clear()
        QUEUE_OLDEST.clear()
        with factory() as session:
            for actor, job_status, count in session.execute(
                select(Job.requester, Job.status, func.count(Job.id)).group_by(
                    Job.requester, Job.status
                )
            ):
                QUEUE.labels(requester=actor, status=job_status).set(count)
            for actor, oldest in session.execute(
                select(Job.requester, func.min(Job.created_at))
                .where(Job.status == "queued")
                .group_by(Job.requester)
            ):
                if oldest.tzinfo is None:
                    oldest = oldest.replace(tzinfo=UTC)
                QUEUE_OLDEST.labels(requester=actor).set(
                    max(0, (datetime.now(UTC) - oldest).total_seconds())
                )
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app
