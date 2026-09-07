from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlparse

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LlmBackend = Literal["internal-llm", "external-ai"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MATCHER_", extra="ignore")

    database_url: SecretStr = SecretStr("sqlite+pysqlite:///./tapy.db")
    rabbitmq_url: SecretStr = SecretStr("")
    internal_llm_queue: str = "internal-llm.requests"
    internal_llm_model: str = "local-llm"
    external_ai_queue: str = "external-ai.requests"
    external_ai_model: str = "alibaba:qwen-plus"
    llm_order: Annotated[tuple[LlmBackend, ...], NoDecode] = (
        "external-ai",
        "internal-llm",
    )
    flights_config_path: Path = Path("/config/flights.json")
    request_timeout_seconds: float = Field(default=120, gt=0, le=300)
    match_threshold: float = Field(default=0.90, ge=0, le=1)
    maximum_messages_per_scan: int = Field(default=20, ge=1, le=100)
    gmail_query: str = Field(default="newer_than:365d", max_length=500)
    public_base_url: str = "http://localhost:8080"
    google_oauth_redirect_uri: str = ""
    microsoft_oauth_redirect_uri: str = ""
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

    @field_validator(
        "internal_llm_queue",
        "external_ai_queue",
        "internal_llm_model",
        "external_ai_model",
        "microsoft_tenant",
    )
    @classmethod
    def non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be empty")
        return value.strip()

    @field_validator("public_base_url", "google_oauth_redirect_uri", "microsoft_oauth_redirect_uri")
    @classmethod
    def http_url(cls, value: str) -> str:
        if not value:
            return value
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("value must be an absolute HTTP(S) URL")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_oauth_pairs(self) -> Settings:
        providers = (
            (
                "Google",
                self.google_oauth_client_id,
                self.google_oauth_client_secret.get_secret_value(),
            ),
            (
                "Microsoft",
                self.microsoft_oauth_client_id,
                self.microsoft_oauth_client_secret.get_secret_value(),
            ),
        )
        partially_configured = [
            name for name, client_id, secret in providers if bool(client_id) != bool(secret)
        ]
        if partially_configured:
            raise ValueError(
                "OAuth client ID and secret must be configured together for "
                + ", ".join(partially_configured)
            )
        if any(client_id for _, client_id, _ in providers):
            try:
                Fernet(self.oauth_token_encryption_key.get_secret_value().encode())
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "oauth_token_encryption_key must be a valid Fernet key when OAuth is enabled"
                ) from exc
        return self

    def oauth_redirect_uri(self, provider: Literal["gmail", "outlook"]) -> str:
        if provider == "gmail" and self.google_oauth_redirect_uri:
            return self.google_oauth_redirect_uri
        if provider == "outlook" and self.microsoft_oauth_redirect_uri:
            return self.microsoft_oauth_redirect_uri
        return f"{self.public_base_url}/v1/oauth/{provider}/callback"

    def require_rabbitmq(self) -> None:
        value = self.rabbitmq_url.get_secret_value().strip()
        if not value:
            raise ValueError("MATCHER_RABBITMQ_URL is required when the RabbitMQ extractor is used")
        if urlparse(value).scheme not in {"amqp", "amqps"}:
            raise ValueError("MATCHER_RABBITMQ_URL must use the amqp or amqps scheme")
