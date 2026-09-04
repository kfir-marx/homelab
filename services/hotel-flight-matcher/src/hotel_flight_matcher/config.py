from pathlib import Path

from pydantic import Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MATCHER_", extra="ignore")

    google_oauth_client_id: str = Field(min_length=20)
    google_tokeninfo_url: HttpUrl = HttpUrl("https://oauth2.googleapis.com/tokeninfo")
    llm_base_url: HttpUrl = HttpUrl(
        "http://internal-llm.homelab-assistant.svc.cluster.local:8080/v1"
    )
    llm_api_key: str = Field(min_length=20)
    llm_model: str = "local-llm"
    flights_config_path: Path = Path("/config/flights.json")
    request_timeout_seconds: float = Field(default=120, gt=0, le=300)
    per_user_requests_per_minute: int = Field(default=20, ge=1, le=1000)
    related_threshold: float = Field(default=0.65, ge=0, le=1)
