"""Application settings loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from nimmakai import __version__


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

    # Catalog / routing
    models_config_path: str = "config/models.yaml"
    routing_enabled: bool = True
    classify_mode: Literal["rules_only", "rules_then_llm"] = "rules_only"
    enable_fallback_on_explicit: bool = True
    max_model_fallbacks: int = 3
    catalog_refresh_seconds: int = 300
    strict_catalog: bool = False
    inject_auto_model: bool = True
    fallback_on_pool_exhaust: bool = True
    long_context_chars: int = 48000
    short_chat_chars: int = 800
    llm_classify_threshold: float = 0.55
    llm_classify_cache_ttl: float = 600.0
    llm_classify_cache_size: int = 256

    # Account safety
    safety_jitter_enabled: bool = True
    safety_jitter_ms_min: float = 20.0
    safety_jitter_ms_max: float = 120.0
    nim_rpd_limit: int = 2000
    nim_max_in_flight_per_key: int = 3
    global_max_in_flight: int = 0  # 0 = auto (keys * per-key)
    auth_fail_threshold: int = 2
    auth_quarantine_seconds: float = 3600.0
    sticky_sessions_enabled: bool = True
    sticky_session_ttl_seconds: float = 1800.0
    sticky_boost: float = 3.0
    upstream_user_agent: str = f"nimmakai/{__version__} (+https://github.com/nimmakai/nimmakai; OpenAI-compatible proxy)"

    # Optional egress proxies (corporate networking — not for ban evasion)
    nim_egress_proxies: Annotated[list[str], NoDecode] = Field(default_factory=list)
    http_proxy: str | None = None
    https_proxy: str | None = None

    @field_validator("proxy_api_keys", "nim_api_keys", "nim_egress_proxies", mode="before")
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

    def egress_proxy_url(self) -> str | None:
        """First configured egress proxy, if any."""
        if self.nim_egress_proxies:
            return self.nim_egress_proxies[0]
        return self.https_proxy or self.http_proxy


@lru_cache
def get_settings() -> Settings:
    return Settings()
