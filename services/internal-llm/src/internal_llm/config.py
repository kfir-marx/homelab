from __future__ import annotations

from urllib.parse import urlparse

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="INTERNAL_LLM_", env_file=None, case_sensitive=False
    )

    api_key: SecretStr = SecretStr("")
    rabbitmq_url: SecretStr = SecretStr("")
    request_queue: str = "internal-llm.requests"
    request_timeout_seconds: float = Field(default=300, gt=0, le=900)
    maximum_request_bytes: int = Field(default=1_048_576, ge=1024, le=16_777_216)
    inference_base_url: str = ""
    inference_api_key: SecretStr = SecretStr("")
    worker_prefetch: int = Field(default=1, ge=1, le=16)

    def require_worker_dependencies(self) -> None:
        missing = []
        rabbitmq_url = self.rabbitmq_url.get_secret_value().strip()
        if not rabbitmq_url:
            missing.append("INTERNAL_LLM_RABBITMQ_URL")
        if not self.inference_base_url.strip():
            missing.append("INTERNAL_LLM_INFERENCE_BASE_URL")
        if missing:
            raise ValueError("worker configuration is missing: " + ", ".join(missing))
        if urlparse(rabbitmq_url).scheme not in {"amqp", "amqps"}:
            raise ValueError("INTERNAL_LLM_RABBITMQ_URL must use the amqp or amqps scheme")
        if urlparse(self.inference_base_url).scheme not in {"http", "https"}:
            raise ValueError("INTERNAL_LLM_INFERENCE_BASE_URL must use HTTP(S)")
