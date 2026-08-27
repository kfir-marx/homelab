FROM python:3.12.10-slim-bookworm@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.6@sha256:88bc6eb1ccd4b82efd0e1b530caffabddf50dc2bf612e66c14ea25b8ee8a4d3d /uv /uvx /bin/
ENV UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=0
WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.12.10-slim-bookworm@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db
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
