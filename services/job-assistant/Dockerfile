FROM python:3.14.6-slim-bookworm@sha256:4c92ffcde4dd6f1ff72a24518f49fd4990b27134987dfa31a733badde66df9f8 AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.6 /uv /uvx /bin/
ENV UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=0
WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.14.6-slim-bookworm@sha256:4c92ffcde4dd6f1ff72a24518f49fd4990b27134987dfa31a733badde66df9f8
RUN apt-get update \
 && apt-get install --no-install-recommends -y ca-certificates tini \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 10001 jobassistant \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /app jobassistant
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
RUN mkdir -p /data/artifacts /data/private /tmp/job-assistant \
 && chown -R 10001:10001 /data /tmp/job-assistant /app
COPY --chown=10001:10001 alembic.ini ./alembic.ini
COPY --chown=10001:10001 migrations ./migrations
USER 10001:10001
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    JOB_ASSISTANT_MIGRATION_ROOT=/app \
    PATH="/opt/venv/bin:$PATH"
ENTRYPOINT ["/usr/bin/tini", "--", "job-assistant"]
CMD ["api"]
