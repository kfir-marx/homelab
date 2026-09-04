from __future__ import annotations

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
    inference_base_url: str = "http://llm-inference.homelab-assistant.svc.cluster.local:8000/v1"
    inference_api_key: SecretStr = SecretStr("")
    worker_prefetch: int = Field(default=1, ge=1, le=16)
