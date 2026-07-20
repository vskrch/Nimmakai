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
    # Streaming: short TTFT = fail-fast to next model if not responding;
    # long idle once first token arrives (Cursor/agent safe).
    stream_ttft_timeout_seconds: float = 12.0
    stream_idle_timeout_seconds: float = 180.0
    request_log_size: int = 200
    # Adaptive: always prefer currently responding models at request time
    adaptive_routing: bool = True

    # Catalog / routing
    models_config_path: str = "config/models.yaml"
    routing_enabled: bool = True
    classify_mode: Literal["rules_only", "rules_then_llm"] = "rules_only"
    enable_fallback_on_explicit: bool = True
    max_model_fallbacks: int = 10  # deeper ladder for resilient coding agents
    # Extra fallbacks for coding_agentic / Cursor tool loops
    coding_max_fallbacks: int = 12
    # Self-heal catalog/providers every N seconds (0 = only with catalog refresh)
    self_heal_seconds: int = 120
    catalog_refresh_seconds: int = 300
    strict_catalog: bool = False
    inject_auto_model: bool = True
    fallback_on_pool_exhaust: bool = True
    catalog_docs_url: str = "https://build.nvidia.com/models.md"
    catalog_snapshot_path: str = ".nimmakai/catalog_snapshot.json"
    probe_budget_per_hour: int = 8
    catalog_fetch_docs: bool = True
    catalog_run_probes: bool = True
    long_context_chars: int = 48000
    short_chat_chars: int = 800
    llm_classify_threshold: float = 0.55
    llm_classify_cache_ttl: float = 600.0
    llm_classify_cache_size: int = 256
    providers_config_path: str = "config/providers.yaml"
    providers_overlay_path: str = ".nimmakai/providers.json"
    # Durable store for providers + preferences (stdlib sqlite3)
    sqlite_path: str = ".nimmakai/nimmakai.db"
    # One-time: seed free-provider templates (no keys) into SQLite on first boot
    sqlite_seed_free_presets: bool = True

    # Account safety — jitter off by default for Cursor "no delay"
    safety_jitter_enabled: bool = False
    safety_jitter_ms_min: float = 0.0
    safety_jitter_ms_max: float = 0.0
    nim_rpd_limit: int = 2000
    nim_max_in_flight_per_key: int = 3
    global_max_in_flight: int = 0  # 0 = auto (keys * per-key)
    auth_fail_threshold: int = 2
    auth_quarantine_seconds: float = 3600.0
    sticky_sessions_enabled: bool = True
    sticky_session_ttl_seconds: float = 1800.0
    sticky_boost: float = 3.0
    allow_insecure_auth: bool = False  # must be true to accept any Bearer when PROXY empty
    request_deadline_seconds: float = 180.0
    probe_every_n_refreshes: int = 6
    # Backoff only for 429 / transport / 5xx — not for model-not-found ladder steps
    retry_backoff_base_seconds: float = 0.5
    retry_backoff_cap_seconds: float = 16.0
    cors_allow_origins: str = "*"
    upstream_user_agent: str = (
        f"nimmakai/{__version__} (OpenAI-compatible NIM proxy)"
    )

    # Analytics (persistent traces + dashboard)
    analytics_enabled: bool = True
    analytics_retention_days: int = 7
    analytics_rollup_retention_days: int = 90
    analytics_batch_size: int = 50
    analytics_flush_interval: float = 1.0
    analytics_webhook_url: str | None = None
    analytics_otlp_endpoint: str | None = None

    # Multi-tenant accounts
    admin_emails: Annotated[list[str], NoDecode] = Field(default_factory=list)
    email_backend: str = "stub"  # stub | resend (resend later)
    public_base_url: str | None = None  # e.g. https://app.example.com for verify links
    session_cookie_name: str = "nk_session"
    session_secure_cookie: bool = False  # True behind HTTPS in production

    # Optional egress proxies (corporate networking — not for ban evasion)
    nim_egress_proxies: Annotated[list[str], NoDecode] = Field(default_factory=list)
    http_proxy: str | None = None
    https_proxy: str | None = None

    @field_validator(
        "proxy_api_keys",
        "nim_api_keys",
        "nim_egress_proxies",
        "admin_emails",
        mode="before",
    )
    @classmethod
    def parse_csv(cls, v: object) -> list[str]:
        return _split_csv(v)  # type: ignore[arg-type]

    @property
    def effective_rpm(self) -> float:
        """RPM we schedule against (slightly under hard limit)."""
        return max(1.0, self.nim_rpm_limit * self.nim_rpm_safety_factor)

    @property
    def accept_any_proxy_key(self) -> bool:
        return self.allow_insecure_auth

    def egress_proxy_url(self) -> str | None:
        """First configured egress proxy, if any."""
        if self.nim_egress_proxies:
            return self.nim_egress_proxies[0]
        return self.https_proxy or self.http_proxy


@lru_cache
def get_settings() -> Settings:
    return Settings()
