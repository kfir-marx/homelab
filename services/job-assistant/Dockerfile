FROM python:3.12.10-slim-bookworm AS builder
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir --prefix=/install .

FROM python:3.12.10-slim-bookworm
RUN apt-get update \
 && apt-get install --no-install-recommends -y ca-certificates tini \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 10001 jobassistant \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /app jobassistant
COPY --from=builder /install /usr/local
WORKDIR /app
RUN mkdir -p /data/artifacts /data/private /tmp/job-assistant \
 && chown -R 10001:10001 /data /tmp/job-assistant /app
COPY --chown=10001:10001 alembic.ini ./alembic.ini
COPY --chown=10001:10001 migrations ./migrations
USER 10001:10001
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    JOB_ASSISTANT_MIGRATION_ROOT=/app
ENTRYPOINT ["/usr/bin/tini", "--", "job-assistant"]
CMD ["api"]
