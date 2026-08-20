FROM python:3.12.10-slim-bookworm AS builder
WORKDIR /build
COPY pyproject.toml ./
COPY src ./src
RUN python -m pip install --no-cache-dir --prefix=/install .

FROM python:3.12.10-slim-bookworm
RUN groupadd --gid 10001 assistant \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /app assistant
COPY --from=builder /install /usr/local
WORKDIR /app
USER 10001:10001
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["homelab-assistant"]

