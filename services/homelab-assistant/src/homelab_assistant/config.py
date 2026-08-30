from __future__ import annotations

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HOMELAB_ASSISTANT_", env_file=None, case_sensitive=False
    )

    telegram_token: SecretStr
    telegram_allowed_user_id: int
    telegram_allowed_chat_id: int
    codex_socket_path: str = "/run/codex-app-server/app-server.sock"
    codex_cwd: str = "/home/kfir/repos/homelab"
    state_database_path: str = "/var/lib/homelab-assistant/bridge.db"
    administrator_lease_seconds: int = Field(default=900, ge=60, le=3600)
    callback_ttl_seconds: int = Field(default=120, ge=30, le=600)
    session_page_size: int = Field(default=8, ge=2, le=12)
    max_input_chars: int = Field(default=12000, ge=100, le=50000)
    app_server_request_timeout_seconds: float = Field(default=30.0, ge=1, le=300)
    codex_turn_timeout_seconds: float = Field(default=3600.0, ge=30, le=86400)
    worker_threads: int = Field(default=8, ge=2, le=32)

    kubernetes_api_url: str = "https://192.168.1.211:6443"
    kubernetes_switch_token_file: str = "/run/secrets/homelab-assistant/kubernetes-switch-token"  # noqa: S105 - credential file path
    kubernetes_ca_file: str = "/run/secrets/homelab-assistant/kubernetes-ca.crt"
    kubernetes_timeout_seconds: float = 15.0
    kubernetes_node_name: str = "gpu-2"
    kubernetes_drain_timeout_seconds: int = 600
    kubernetes_ready_timeout_seconds: int = 600
    switch_confirmation_ttl_seconds: int = 120
    actuator_host: str = "192.168.1.107"
    actuator_user: str = "homelab-actuator"
    actuator_identity_file: str = "/run/secrets/homelab-assistant/actuator-ssh-key"
    actuator_known_hosts_file: str = "/run/secrets/homelab-assistant/actuator-known-hosts"
    actuator_timeout_seconds: int = 660

    @field_validator("kubernetes_node_name")
    @classmethod
    def literal_gpu_2(cls, value: str) -> str:
        if value != "gpu-2":
            raise ValueError("deterministic switching is restricted to gpu-2")
        return value

    @field_validator("telegram_allowed_user_id", "telegram_allowed_chat_id")
    @classmethod
    def positive_telegram_identifier(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Telegram identifiers must be positive numeric IDs")
        return value
