"""Multi-provider registry (OpenRouter-style OpenAI-compatible backends)."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_ENV_KEYS = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _resolve_config_path(path: str | Path) -> Path:
    """Resolve config path for local dev, Heroku, and installed wheels."""
    p = Path(path)
    candidates = [
        p,
        Path.cwd() / p,
        Path(__file__).resolve().parents[3] / p,  # repo root when src layout
        Path(__file__).resolve().parents[2] / p,  # nimmakai parent
        Path(__file__).resolve().parent.parent.parent / p,
    ]
    for c in candidates:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return p  # may not exist; caller handles empty


@dataclass
class ProviderConfig:
    id: str
    name: str = ""
    base_url: str = ""
    api_keys: list[str] = field(default_factory=list)
    # Optional: load keys from env var name(s)
    api_keys_env: str | None = None
    enabled: bool = True
    rpm_limit: float = 40.0
    rpd_limit: int = 2000
    max_in_flight_per_key: int = 3
    api_style: str = "openai"  # phase 1: openai only
    # When true, this is the built-in NIM provider (env NIM_* wins)
    builtin: bool = False

    def resolved_keys(self) -> list[str]:
        keys = [k.strip() for k in self.api_keys if k and str(k).strip()]
        if self.api_keys_env:
            raw = os.environ.get(self.api_keys_env, "")
            keys.extend(p.strip() for p in raw.split(",") if p.strip())
        # de-dupe preserve order
        seen: set[str] = set()
        out: list[str] = []
        for k in keys:
            if k not in seen:
                seen.add(k)
                out.append(k)
        return out

    def mask(self) -> dict[str, Any]:
        keys = self.resolved_keys()
        return {
            "id": self.id,
            "name": self.name or self.id,
            "base_url": self.base_url,
            "enabled": self.enabled,
            "rpm_limit": self.rpm_limit,
            "rpd_limit": self.rpd_limit,
            "max_in_flight_per_key": self.max_in_flight_per_key,
            "api_style": self.api_style,
            "builtin": self.builtin,
            "api_keys_env": self.api_keys_env,
            "key_count": len(keys),
            "keys_masked": [_mask_key(k) for k in keys],
        }


def _mask_key(k: str) -> str:
    if len(k) <= 12:
        return "***"
    return f"{k[:6]}...{k[-4:]}"


def namespace_model(provider_id: str, upstream_model_id: str) -> str:
    pid = provider_id.strip().lower()
    mid = upstream_model_id.strip()
    if mid.lower().startswith(f"{pid}/"):
        return mid
    return f"{pid}/{mid}"


def split_provider_model(
    model_id: str,
    provider_ids: set[str],
    *,
    default_provider: str = "nim",
) -> tuple[str, str]:
    """
    Split `provider/upstream...` using known provider ids.
    Bare org/model ids default to `default_provider` (usually nim).
    """
    mid = model_id.strip()
    if not mid:
        return default_provider, mid
    lower = mid.lower()
    # Longest provider id first so `foo-bar` wins over `foo`
    for pid in sorted(provider_ids, key=len, reverse=True):
        prefix = f"{pid.lower()}/"
        if lower.startswith(prefix):
            return pid.lower(), mid[len(prefix) :]
    return default_provider, mid


def scoring_model_id(namespaced: str, provider_ids: set[str]) -> str:
    """Upstream id without provider prefix — used for family/param scoring."""
    _pid, upstream = split_provider_model(namespaced, provider_ids)
    return upstream


@dataclass
class ProviderStore:
    """YAML base + optional runtime overlay (.nimmakai/providers.json)."""

    path: Path
    overlay_path: Path
    providers: dict[str, ProviderConfig] = field(default_factory=dict)

    @classmethod
    def load(
        cls,
        path: str | Path = "config/providers.yaml",
        overlay_path: str | Path = ".nimmakai/providers.json",
        *,
        nim_base_url: str = "https://integrate.api.nvidia.com/v1",
        nim_api_keys: list[str] | None = None,
        nim_rpm: float = 36.0,
        nim_rpd: int = 2000,
        nim_max_in_flight: int = 3,
    ) -> ProviderStore:
        p = _resolve_config_path(path)
        overlay = Path(overlay_path)
        store = cls(path=p, overlay_path=overlay)
        data: dict[str, Any] = {}
        if p.is_file():
            with p.open(encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            logger.info("loaded providers config from %s", p)
        else:
            logger.warning(
                "providers config not found at %s — using NIM env only", path
            )
        for item in data.get("providers") or []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            cfg = _cfg_from_dict(item)
            store.providers[cfg.id] = cfg

        if overlay.is_file():
            try:
                raw = json.loads(overlay.read_text(encoding="utf-8"))
                for item in raw.get("providers") or []:
                    if not isinstance(item, dict) or not item.get("id"):
                        continue
                    cfg = _cfg_from_dict(item)
                    store.providers[cfg.id] = cfg
                logger.info("loaded providers overlay from %s", overlay)
            except Exception:
                logger.exception("failed to load providers overlay")

        # Ensure builtin nim exists / sync from env
        nim = store.providers.get("nim")
        if nim is None:
            nim = ProviderConfig(
                id="nim",
                name="NVIDIA NIM",
                base_url=nim_base_url,
                api_keys=list(nim_api_keys or []),
                enabled=True,
                rpm_limit=nim_rpm,
                rpd_limit=nim_rpd,
                max_in_flight_per_key=nim_max_in_flight,
                builtin=True,
            )
            store.providers["nim"] = nim
        else:
            nim.builtin = True
            nim.base_url = nim_base_url or nim.base_url
            if nim_api_keys:
                nim.api_keys = list(nim_api_keys)
            nim.rpm_limit = nim_rpm
            nim.rpd_limit = nim_rpd
            nim.max_in_flight_per_key = nim_max_in_flight

        # Auto-register free providers when their env keys are present
        store._bootstrap_env_presets()

        return store

    def _bootstrap_env_presets(self) -> None:
        """If GROQ_API_KEYS / etc. are set, register that free provider automatically."""
        from nimmakai.catalog.presets import ENV_PROVIDER_BOOTSTRAP, get_preset

        for env_name, preset_id in ENV_PROVIDER_BOOTSTRAP:
            raw = os.environ.get(env_name, "").strip()
            if not raw:
                continue
            if preset_id in self.providers:
                # Ensure env name is wired so resolved_keys picks it up
                cfg = self.providers[preset_id]
                if not cfg.api_keys_env:
                    cfg.api_keys_env = env_name
                continue
            preset = get_preset(preset_id)
            if not preset or preset.get("custom"):
                continue
            base = str(preset.get("base_url") or "").strip()
            if not base or "{ACCOUNT_ID}" in base:
                continue
            self.providers[preset_id] = ProviderConfig(
                id=preset_id,
                name=str(preset.get("name") or preset_id),
                base_url=base.rstrip("/"),
                api_keys=[],
                api_keys_env=env_name,
                enabled=True,
                rpm_limit=float(preset.get("rpm_limit", 40)),
                rpd_limit=int(preset.get("rpd_limit", 2000)),
                max_in_flight_per_key=int(preset.get("max_in_flight_per_key", 3)),
                api_style="openai",
                builtin=False,
            )
            logger.info(
                "auto-registered free provider %s from env %s", preset_id, env_name
            )

    def enabled_providers(self) -> list[ProviderConfig]:
        return [p for p in self.providers.values() if p.enabled]

    def provider_ids(self) -> set[str]:
        return {p.id.lower() for p in self.providers.values()}

    def upsert(self, cfg: ProviderConfig) -> None:
        if cfg.id.lower() == "nim" and cfg.builtin is False:
            # protect replacing builtin flag incorrectly
            existing = self.providers.get("nim")
            if existing and existing.builtin:
                cfg.builtin = True
        self.providers[cfg.id.lower()] = cfg
        cfg.id = cfg.id.lower()
        self.save_overlay()

    def remove(self, provider_id: str) -> bool:
        pid = provider_id.lower()
        cfg = self.providers.get(pid)
        if cfg is None:
            return False
        if cfg.builtin:
            cfg.enabled = False
            self.save_overlay()
            return True
        del self.providers[pid]
        self.save_overlay()
        return True

    def save_overlay(self) -> None:
        self.overlay_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "providers": [
                {
                    "id": p.id,
                    "name": p.name,
                    "base_url": p.base_url,
                    "api_keys": p.api_keys,
                    "api_keys_env": p.api_keys_env,
                    "enabled": p.enabled,
                    "rpm_limit": p.rpm_limit,
                    "rpd_limit": p.rpd_limit,
                    "max_in_flight_per_key": p.max_in_flight_per_key,
                    "api_style": p.api_style,
                    "builtin": p.builtin,
                }
                for p in self.providers.values()
            ]
        }
        tmp = self.overlay_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.overlay_path)

    def list_masked(self) -> list[dict[str, Any]]:
        return [p.mask() for p in sorted(self.providers.values(), key=lambda x: x.id)]


def _cfg_from_dict(item: dict[str, Any]) -> ProviderConfig:
    pid = str(item["id"]).strip().lower()
    keys = item.get("api_keys") or []
    if isinstance(keys, str):
        keys = [k.strip() for k in keys.split(",") if k.strip()]
    return ProviderConfig(
        id=pid,
        name=str(item.get("name") or pid),
        base_url=str(item.get("base_url") or "").rstrip("/"),
        api_keys=list(keys),
        api_keys_env=item.get("api_keys_env"),
        enabled=bool(item.get("enabled", True)),
        rpm_limit=float(item.get("rpm_limit", 40)),
        rpd_limit=int(item.get("rpd_limit", 2000)),
        max_in_flight_per_key=int(item.get("max_in_flight_per_key", 3)),
        api_style=str(item.get("api_style") or "openai"),
        builtin=bool(item.get("builtin", False)),
    )


def provider_from_request_body(body: dict[str, Any]) -> ProviderConfig:
    if not body.get("id") or not body.get("base_url"):
        raise ValueError("id and base_url are required")
    return _cfg_from_dict(body)
