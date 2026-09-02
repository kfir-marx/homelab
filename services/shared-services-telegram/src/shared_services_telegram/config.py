from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SHARED_SERVICES_TELEGRAM_", env_file=None, case_sensitive=False
    )

    telegram_token: SecretStr
    job_assistant_base_url: str = "http://job-assistant-api.job-assistant.svc.cluster.local:8080"
    job_assistant_api_token: SecretStr
    job_assistant_notification_token: SecretStr
    listen_host: str = "0.0.0.0"  # noqa: S104 - container health listener
    listen_port: int = 8080
    poll_timeout_seconds: int = 45
    request_timeout_seconds: float = 10.0
    file_timeout_seconds: float = 30.0
    max_file_bytes: int = 10_000_000
    notification_interval_seconds: float = 2.0
    per_user_updates_per_minute: int = 30
    global_updates_per_minute: int = 180
