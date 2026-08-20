from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr
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
    default_timeout_seconds: int = 600
    maximum_timeout_seconds: int = 1200
    maximum_prompt_bytes: int = 512_000
    poll_seconds: float = 2.0


MODEL_ALIASES = {"sol": "gpt-5.6-sol"}
MODEL_REASONING = {"gpt-5.6-sol": frozenset({"none", "low", "medium", "high", "xhigh", "max"})}


def resolve_model(model: str, reasoning: str) -> tuple[str, str]:
    canonical = MODEL_ALIASES.get(model.casefold(), model)
    allowed = MODEL_REASONING.get(canonical)
    if allowed is None:
        raise ValueError("model is not allowlisted")
    normalized_reasoning = reasoning.casefold()
    if normalized_reasoning not in allowed:
        raise ValueError(f"reasoning effort is not supported by {canonical}")
    return canonical, normalized_reasoning
