from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LlmBackend = Literal["internal-llm", "external-ai"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MATCHER_", extra="ignore")

    database_url: SecretStr = SecretStr("sqlite+pysqlite:///./hotel-flight-matcher.db")
    rabbitmq_url: SecretStr = SecretStr("")
    internal_llm_queue: str = "internal-llm.requests"
    internal_llm_model: str = "local-llm"
    external_ai_queue: str = "external-ai.requests"
    external_ai_model: str = "alibaba:qwen-plus"
    llm_order: tuple[LlmBackend, ...] = ("internal-llm", "external-ai")
    flights_config_path: Path = Path("/config/flights.json")
    request_timeout_seconds: float = Field(default=120, gt=0, le=300)
    match_threshold: float = Field(default=0.90, ge=0, le=1)
    maximum_messages_per_scan: int = Field(default=20, ge=1, le=100)
    gmail_query: str = Field(default="newer_than:365d", max_length=500)
    public_base_url: str = "https://staymatch.547600.xyz"
    oauth_token_encryption_key: SecretStr = SecretStr("")
    google_oauth_client_id: str = ""
    google_oauth_client_secret: SecretStr = SecretStr("")
    microsoft_oauth_client_id: str = ""
    microsoft_oauth_client_secret: SecretStr = SecretStr("")
    microsoft_tenant: str = "common"
    oauth_state_ttl_seconds: int = Field(default=600, ge=60, le=3600)

    @field_validator("llm_order", mode="before")
    @classmethod
    def parse_llm_order(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        return value

    @field_validator("llm_order")
    @classmethod
    def validate_llm_order(cls, value: tuple[LlmBackend, ...]) -> tuple[LlmBackend, ...]:
        if not value or len(value) != len(set(value)):
            raise ValueError("llm_order must contain one or more unique backends")
        return value
