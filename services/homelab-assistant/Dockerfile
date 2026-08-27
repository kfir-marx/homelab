FROM python:3.12.10-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.6 /uv /uvx /bin/
ENV UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=0
WORKDIR /build
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.12.10-slim-bookworm
RUN groupadd --gid 10001 assistant \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /app assistant
COPY --from=builder /opt/venv /opt/venv
COPY skills /app/skills
WORKDIR /app
USER 10001:10001
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["homelab-assistant"]
