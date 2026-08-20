from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="JOB_ASSISTANT_", env_file=None, case_sensitive=False
    )

    environment: str = "production"
    database_url: SecretStr = SecretStr("postgresql+psycopg://job_assistant@localhost/jobs")
    generation_database_url: SecretStr | None = None
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"  # noqa: S104 - container listener
    api_port: int = 8080
    gateway_api_token: SecretStr = SecretStr("")
    gateway_notification_token: SecretStr = SecretStr("")
    telegram_allowed_user_ids: frozenset[int] = Field(default_factory=frozenset)
    external_ai_base_url: str = "http://external-ai.external-ai.svc.cluster.local:8080"
    external_ai_token: SecretStr = SecretStr("")
    external_ai_model: str = "sol"
    external_ai_reasoning: str = "high"
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    review_email: str | None = None
    smtp_from: str | None = None
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    imap_username: str | None = None
    imap_password: SecretStr | None = None
    imap_folder: str = "LinkedIn Jobs"
    artifact_root: Path = Path("/data/artifacts")
    career_inventory_path: Path = Path("/data/private/career-inventory.yaml")
    cv_template_path: Path | None = Path("/data/private/cv-template.docx")
    search_criteria_path: Path = Path("/app/config/search-criteria.yaml")
    company_registry_path: Path = Path("/app/config/company-registry.yaml")
    generation_timeout_seconds: int = 600
    http_timeout_seconds: float = 15.0
    max_download_bytes: int = 2_000_000
    max_upload_bytes: int = 10_000_000
    queue_lease_seconds: int = 900
    max_work_attempts: int = 5
    discovery_schedule: str = "17 7 * * *"
    timezone: str = "Asia/Jerusalem"

    @field_validator("telegram_allowed_user_ids", mode="before")
    @classmethod
    def parse_user_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return frozenset(int(item.strip()) for item in value.split(",") if item.strip())
        return value


def get_settings() -> Settings:
    return Settings()
