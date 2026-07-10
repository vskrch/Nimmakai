"""Application settings loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _split_csv(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if v and str(v).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Client-facing — comma-separated in env (NoDecode skips JSON parsing)
    proxy_api_keys: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # Upstream NIM
    nim_base_url: str = "https://integrate.api.nvidia.com/v1"
    nim_api_keys: Annotated[list[str], NoDecode] = Field(default_factory=list)
    nim_rpm_limit: int = 40
    nim_rpm_safety_factor: float = 0.9
    nim_cooldown_seconds: float = 60.0

    # Server
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "info"
    upstream_timeout: float = 300.0
    default_model: str | None = None

    @field_validator("proxy_api_keys", "nim_api_keys", mode="before")
    @classmethod
    def parse_csv(cls, v: object) -> list[str]:
        return _split_csv(v)  # type: ignore[arg-type]

    @property
    def effective_rpm(self) -> float:
        """RPM we schedule against (slightly under hard limit)."""
        return max(1.0, self.nim_rpm_limit * self.nim_rpm_safety_factor)

    @property
    def accept_any_proxy_key(self) -> bool:
        return len(self.proxy_api_keys) == 0


@lru_cache
def get_settings() -> Settings:
    return Settings()
