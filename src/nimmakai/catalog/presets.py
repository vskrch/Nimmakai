"""Free / popular OpenAI-compatible provider presets for the admin UI.

Users paste API keys; models are namespaced and merged into the global pool
for intelligent best+fast routing across all backends.
"""

from __future__ import annotations

from typing import Any

# Curated free-tier / freemium OpenAI-compatible gateways (2026).
# base_url must end at the OpenAI-compatible root (…/v1).
PROVIDER_PRESETS: list[dict[str, Any]] = [
    {
        "id": "zen",
        "name": "OpenCode Zen",
        "base_url": "https://opencode.ai/zen/v1",
        "api_keys_env": "OPENCODE_ZEN_API_KEYS",
        "rpm_limit": 60,
        "rpd_limit": 50000,
        "max_in_flight_per_key": 6,
        "free_tier": True,
        "speed_tier": "fast",
        "signup_url": "https://opencode.ai/auth",
        "description": (
            "OpenCode Zen — curated coding agents. Free: mimo-v2.5-free, "
            "deepseek-v4-flash-free, north-mini-code-free, big-pickle, nemotron-3-ultra-free."
        ),
        "tags": ["free", "coding", "opencode", "openai-compatible", "best"],
        "coding_priority": True,
    },
    {
        "id": "groq",
        "name": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "api_keys_env": "GROQ_API_KEYS",
        "rpm_limit": 30,
        "rpd_limit": 14400,
        "max_in_flight_per_key": 4,
        "free_tier": True,
        "speed_tier": "ultra",  # very high TPS free tier
        "signup_url": "https://console.groq.com/keys",
        "description": "Free ultra-fast inference (Llama, Qwen, Gemma, etc.).",
        "tags": ["free", "fast", "openai-compatible"],
    },
    {
        "id": "cerebras",
        "name": "Cerebras",
        "base_url": "https://api.cerebras.ai/v1",
        "api_keys_env": "CEREBRAS_API_KEYS",
        "rpm_limit": 30,
        "rpd_limit": 14400,
        "max_in_flight_per_key": 3,
        "free_tier": True,
        "speed_tier": "ultra",
        "signup_url": "https://cloud.cerebras.ai/",
        "description": "Free wafer-scale chips — extreme tokens/sec on Llama.",
        "tags": ["free", "fast", "openai-compatible"],
    },
    {
        "id": "openrouter",
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_keys_env": "OPENROUTER_API_KEYS",
        "rpm_limit": 20,
        "rpd_limit": 5000,
        "max_in_flight_per_key": 3,
        "free_tier": True,
        "speed_tier": "medium",
        "signup_url": "https://openrouter.ai/keys",
        "description": "Many free models via :free suffix; also paid catalog.",
        "tags": ["free", "multi-model", "openai-compatible"],
    },
    {
        "id": "gemini",
        "name": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "api_keys_env": "GEMINI_API_KEYS",
        "rpm_limit": 15,
        "rpd_limit": 1500,
        "max_in_flight_per_key": 2,
        "free_tier": True,
        "speed_tier": "fast",
        "signup_url": "https://aistudio.google.com/apikey",
        "description": "Gemini OpenAI-compatible endpoint (free tier available).",
        "tags": ["free", "vision", "openai-compatible"],
    },
    {
        "id": "together",
        "name": "Together AI",
        "base_url": "https://api.together.xyz/v1",
        "api_keys_env": "TOGETHER_API_KEYS",
        "rpm_limit": 60,
        "rpd_limit": 10000,
        "max_in_flight_per_key": 4,
        "free_tier": True,
        "speed_tier": "fast",
        "signup_url": "https://api.together.xyz/",
        "description": "Open models with free credits; strong open-weight catalog.",
        "tags": ["free-credits", "openai-compatible"],
    },
    {
        "id": "fireworks",
        "name": "Fireworks AI",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "api_keys_env": "FIREWORKS_API_KEYS",
        "rpm_limit": 60,
        "rpd_limit": 10000,
        "max_in_flight_per_key": 4,
        "free_tier": True,
        "speed_tier": "fast",
        "signup_url": "https://fireworks.ai/",
        "description": "Fast open models; free credits on signup.",
        "tags": ["free-credits", "fast", "openai-compatible"],
    },
    {
        "id": "sambanova",
        "name": "SambaNova",
        "base_url": "https://api.sambanova.ai/v1",
        "api_keys_env": "SAMBANOVA_API_KEYS",
        "rpm_limit": 30,
        "rpd_limit": 5000,
        "max_in_flight_per_key": 3,
        "free_tier": True,
        "speed_tier": "ultra",
        "signup_url": "https://cloud.sambanova.ai/",
        "description": "Free-tier high-speed Llama and open models.",
        "tags": ["free", "fast", "openai-compatible"],
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "api_keys_env": "DEEPSEEK_API_KEYS",
        "rpm_limit": 60,
        "rpd_limit": 10000,
        "max_in_flight_per_key": 4,
        "free_tier": True,
        "speed_tier": "fast",
        "signup_url": "https://platform.deepseek.com/",
        "description": (
            "DeepSeek V3 / R1 and newer models via OpenAI-compatible API "
            "(free credits on signup)."
        ),
        "tags": ["free-credits", "coding", "reasoning", "openai-compatible"],
    },
    {
        "id": "deepinfra",
        "name": "DeepInfra",
        "base_url": "https://api.deepinfra.com/v1/openai",
        "api_keys_env": "DEEPINFRA_API_KEYS",
        "rpm_limit": 60,
        "rpd_limit": 10000,
        "max_in_flight_per_key": 4,
        "free_tier": True,
        "speed_tier": "fast",
        "signup_url": "https://deepinfra.com/",
        "description": "Pay-as-you-go + free credits; broad open model set.",
        "tags": ["free-credits", "openai-compatible"],
    },
    {
        "id": "github",
        "name": "GitHub Models",
        "base_url": "https://models.inference.ai.azure.com",
        "api_keys_env": "GITHUB_MODELS_API_KEYS",
        "rpm_limit": 15,
        "rpd_limit": 1500,
        "max_in_flight_per_key": 2,
        "free_tier": True,
        "speed_tier": "medium",
        "signup_url": "https://github.com/marketplace/models",
        "description": "Free GitHub Models (PAT as API key) OpenAI-compatible.",
        "tags": ["free", "openai-compatible"],
    },
    {
        "id": "cloudflare",
        "name": "Cloudflare Workers AI",
        "base_url": "https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/v1",
        "api_keys_env": "CLOUDFLARE_API_KEYS",
        "rpm_limit": 60,
        "rpd_limit": 10000,
        "max_in_flight_per_key": 3,
        "free_tier": True,
        "speed_tier": "medium",
        "signup_url": "https://developers.cloudflare.com/workers-ai/",
        "description": "Replace {ACCOUNT_ID} in base URL; free tier available.",
        "tags": ["free", "openai-compatible"],
        "needs_url_edit": True,
    },
    {
        "id": "mistral",
        "name": "Mistral AI",
        "base_url": "https://api.mistral.ai/v1",
        "api_keys_env": "MISTRAL_API_KEYS",
        "rpm_limit": 30,
        "rpd_limit": 5000,
        "max_in_flight_per_key": 3,
        "free_tier": True,
        "speed_tier": "fast",
        "signup_url": "https://console.mistral.ai/",
        "description": "Free experimental tier + paid; OpenAI-compatible chat.",
        "tags": ["free-credits", "openai-compatible"],
    },
    {
        "id": "hyperbolic",
        "name": "Hyperbolic",
        "base_url": "https://api.hyperbolic.xyz/v1",
        "api_keys_env": "HYPERBOLIC_API_KEYS",
        "rpm_limit": 60,
        "rpd_limit": 10000,
        "max_in_flight_per_key": 4,
        "free_tier": True,
        "speed_tier": "fast",
        "signup_url": "https://app.hyperbolic.xyz/",
        "description": "Affordable / free-credit open models, OpenAI API.",
        "tags": ["free-credits", "openai-compatible"],
    },
    {
        "id": "nim",
        "name": "NVIDIA NIM",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "api_keys_env": "NIM_API_KEYS",
        "rpm_limit": 40,
        "rpd_limit": 2000,
        "max_in_flight_per_key": 3,
        "free_tier": True,
        "speed_tier": "medium",
        "signup_url": "https://build.nvidia.com/",
        "description": "Built-in. Free-tier NVIDIA hosted models (configure via env or keys).",
        "tags": ["free", "builtin", "openai-compatible"],
        "builtin": True,
    },
    {
        "id": "custom",
        "name": "Custom OpenAI-compatible",
        "base_url": "",
        "api_keys_env": None,
        "rpm_limit": 40,
        "rpd_limit": 2000,
        "max_in_flight_per_key": 3,
        "free_tier": True,
        "speed_tier": "medium",
        "signup_url": "",
        "description": "Any OpenAI-compatible base URL (LiteLLM, vLLM, LocalAI, Ollama /v1, etc.).",
        "tags": ["custom", "openai-compatible"],
        "custom": True,
    },
]

# Known provider speed priors (multiplicative) used by the ladder for
# best+fast combined ranking across free backends.
PROVIDER_SPEED_PRIOR: dict[str, float] = {
    "zen": 1.28,  # OpenCode Zen free coding hosts — high priority
    "groq": 1.35,
    "cerebras": 1.40,
    "sambanova": 1.30,
    "deepseek": 1.25,
    "fireworks": 1.20,
    "together": 1.15,
    "deepinfra": 1.12,
    "hyperbolic": 1.12,
    "mistral": 1.10,
    "gemini": 1.08,
    "openrouter": 1.05,
    "github": 1.00,
    "cloudflare": 1.00,
    "nim": 1.05,  # slight bump — deepseek-v4 / qwen live here often
}

# Env var → preset id for auto-registration at boot
ENV_PROVIDER_BOOTSTRAP: list[tuple[str, str]] = [
    ("OPENCODE_ZEN_API_KEYS", "zen"),
    ("OPENCODE_API_KEYS", "zen"),  # alias
    ("ZEN_API_KEYS", "zen"),
    ("GROQ_API_KEYS", "groq"),
    ("CEREBRAS_API_KEYS", "cerebras"),
    ("OPENROUTER_API_KEYS", "openrouter"),
    ("GEMINI_API_KEYS", "gemini"),
    ("TOGETHER_API_KEYS", "together"),
    ("FIREWORKS_API_KEYS", "fireworks"),
    ("SAMBANOVA_API_KEYS", "sambanova"),
    ("DEEPSEEK_API_KEYS", "deepseek"),
    ("DEEPINFRA_API_KEYS", "deepinfra"),
    ("GITHUB_MODELS_API_KEYS", "github"),
    ("MISTRAL_API_KEYS", "mistral"),
    ("HYPERBOLIC_API_KEYS", "hyperbolic"),
]

# Extra env names accepted by resolved_keys() per provider id
_PROVIDER_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "zen": ("OPENCODE_ZEN_API_KEYS", "OPENCODE_API_KEYS", "ZEN_API_KEYS"),
}


def env_aliases_for_provider(provider_id: str) -> tuple[str, ...]:
    return _PROVIDER_ENV_ALIASES.get(provider_id.strip().lower(), ())

# Free OpenCode Zen coding model ids (bare, before namespacing)
ZEN_FREE_CODING_MODELS: tuple[str, ...] = (
    "mimo-v2.5-free",
    "deepseek-v4-flash-free",
    "north-mini-code-free",
    "nemotron-3-ultra-free",
    "big-pickle",
    "qwen3.6-plus-free",
    "minimax-m3-free",
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "mimo-v2.5",
    "kimi-k2.6",
    "kimi-k2.7-code",
    "minimax-m3",
    "glm-5.2",
)


def list_presets(*, free_only: bool = False) -> list[dict[str, Any]]:
    out = []
    for p in PROVIDER_PRESETS:
        if free_only and not p.get("free_tier"):
            continue
        out.append(dict(p))
    return out


def get_preset(provider_id: str) -> dict[str, Any] | None:
    pid = provider_id.strip().lower()
    for p in PROVIDER_PRESETS:
        if p["id"] == pid:
            return dict(p)
    return None


def speed_prior_for_provider(provider_id: str) -> float:
    return PROVIDER_SPEED_PRIOR.get(provider_id.lower(), 1.0)
