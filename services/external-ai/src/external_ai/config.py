from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EXTERNAL_AI_", env_file=None, case_sensitive=False
    )

    database_url: SecretStr = SecretStr("sqlite+pysqlite:///./external-ai.db")
    homelab_assistant_token: SecretStr = SecretStr("")
    job_assistant_token: SecretStr = SecretStr("")
    codex_executable: str = "codex"
    codex_home: Path = Path("/var/lib/codex")
    work_root: Path = Path("/work")
    default_timeout_seconds: int = Field(default=600, ge=30, le=3600)
    maximum_timeout_seconds: int = Field(default=1200, ge=30, le=7200)
    maximum_prompt_bytes: int = Field(default=512_000, ge=1024, le=16_777_216)
    poll_seconds: float = Field(default=2.0, gt=0, le=60)
    rabbitmq_url: SecretStr = SecretStr("")
    request_queue: str = "external-ai.requests"
    rpc_timeout_seconds: int = Field(default=300, ge=1, le=3600)
    alibaba_api_key: SecretStr = SecretStr("")
    alibaba_base_url: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

    @field_validator("request_queue", "codex_executable")
    @classmethod
    def non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be empty")
        return value.strip()

    @field_validator("alibaba_base_url")
    @classmethod
    def absolute_https_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("alibaba_base_url must be an absolute HTTPS URL")
        return value.rstrip("/")

    @model_validator(mode="after")
    def timeout_order(self) -> Settings:
        if self.default_timeout_seconds > self.maximum_timeout_seconds:
            raise ValueError("default_timeout_seconds must not exceed maximum_timeout_seconds")
        return self

    def require_worker_dependencies(self) -> None:
        value = self.rabbitmq_url.get_secret_value().strip()
        if not value:
            raise ValueError("EXTERNAL_AI_RABBITMQ_URL is required by the worker")
        if urlparse(value).scheme not in {"amqp", "amqps"}:
            raise ValueError("EXTERNAL_AI_RABBITMQ_URL must use the amqp or amqps scheme")


MODEL_ALIASES = {
    "sol": "gpt-5.6-sol",
    "qwen": "alibaba:qwen-plus",
    "qwen-plus": "alibaba:qwen-plus",
}
MODEL_REASONING = {
    "gpt-5.6-sol": frozenset({"none", "low", "medium", "high", "xhigh", "max"}),
    "alibaba:qwen-plus": frozenset({"none"}),
}


def resolve_model(model: str, reasoning: str) -> tuple[str, str]:
    canonical = MODEL_ALIASES.get(model.casefold(), model)
    allowed = MODEL_REASONING.get(canonical)
    if allowed is None:
        raise ValueError("model is not allowlisted")
    normalized_reasoning = reasoning.casefold()
    if normalized_reasoning not in allowed:
        raise ValueError(f"reasoning effort is not supported by {canonical}")
    return canonical, normalized_reasoning
