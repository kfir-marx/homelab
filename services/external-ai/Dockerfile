FROM python:3.12.10-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.6 /uv /uvx /bin/
ENV UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=0
WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.12.10-slim-bookworm
ARG CODEX_CLI_VERSION=0.143.0
ARG CODEX_CLI_SHA256=d9dc731dc66e227b175b13c071be9ea126cc7672fcac8a87823da50b0d2b2ffe
RUN apt-get update \
 && apt-get install --no-install-recommends -y ca-certificates curl tini \
 && curl -fsSL -o /tmp/codex.tar.gz \
    "https://github.com/openai/codex/releases/download/rust-v${CODEX_CLI_VERSION}/codex-x86_64-unknown-linux-musl.tar.gz" \
 && echo "${CODEX_CLI_SHA256}  /tmp/codex.tar.gz" | sha256sum -c - \
 && tar -xzf /tmp/codex.tar.gz -C /usr/local/bin \
 && mv /usr/local/bin/codex-x86_64-unknown-linux-musl /usr/local/bin/codex \
 && chmod 0755 /usr/local/bin/codex \
 && rm /tmp/codex.tar.gz \
 && apt-get purge -y curl \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 10001 externalai \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /app externalai
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
RUN mkdir -p /var/lib/codex /work \
 && chown -R 10001:10001 /var/lib/codex /work /app
USER 10001:10001
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["/usr/bin/tini", "--", "external-ai"]
CMD ["api"]
