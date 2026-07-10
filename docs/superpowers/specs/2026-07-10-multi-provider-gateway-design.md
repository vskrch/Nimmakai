# Multi-provider OpenRouter-style gateway — design

**Date:** 2026-07-10  
**Status:** approved (user: “do what is best” → Phase 1 = API-first)  
**Goal:** Evolve Nimmakai into a self-hosted OpenRouter-like gateway: NIM + any OpenAI-compatible third-party providers (free or paid), with intelligent routing — without hardcoding vendor lists.

## Context

- [free-ai-tools](https://github.com/ShaikhWarsi/free-ai-tools) is a **curated list**, not an API. Use it as optional **preset templates** later, not as a runtime dependency.
- Today Nimmakai is NIM-centric (`NIM_BASE_URL` + `NIM_API_KEYS`).
- Target: admin registers `base_url` + API key(s) → fetch `/models` → unified catalog + ladder.

## Phasing (best path)

| Phase | Scope |
|-------|--------|
| **1 (now)** | Provider registry (YAML + admin API), multi-upstream, namespaced model ids, unified ladder, route by provider |
| **2** | Simple web admin UI (login + paste keys) |
| **3** | Optional presets seeded from free-ai-tools (Groq, Gemini, Cerebras, …) as one-click templates |

## Phase 1 design

### Provider model

Each provider:

```yaml
id: nim          # stable slug
name: NVIDIA NIM
base_url: https://integrate.api.nvidia.com/v1
api_keys: []     # or env NIM_API_KEYS for built-in nim
enabled: true
rpm_limit: 40
rpd_limit: 2000
# OpenAI-compatible only in phase 1
api_style: openai
```

- Built-in **`nim`** provider maps from existing env (`NIM_*`) for backward compatibility.
- Additional providers in `config/providers.yaml` and/or `POST /admin/providers`.
- Secrets: keys in env preferred (`PROVIDER_<ID>_API_KEYS`); YAML may reference env var names.

### Model identity

- Live models namespaced: `{provider_id}/{upstream_model_id}`  
  e.g. `nim/qwen/qwen3.5-397b-a17b`, `groq/llama-3.3-70b-versatile`
- If upstream id already contains `/`, keep full path after provider: `nim/qwen/qwen3…`
- `nimmakai/auto` still means intelligent pick across **all enabled** providers’ ladders.
- Explicit `provider/model` → pin that provider (passthrough within provider, with fallback chain on that provider first, then optional cross-provider if configured).

### Catalog refresh

- Per enabled provider: `GET {base_url}/models` with that provider’s key pool.
- Merge into global live set + `context_by_model` (reuse existing extractors).
- Provider failure → degrade that provider only; others keep serving.

### Request path

- Resolve model → `(provider_id, upstream_model_id)`.
- Forward to that provider’s `UpstreamClient` (path `/chat/completions` etc. unchanged).
- Fallback ladder may cross providers when in `auto` mode (power-first, same soft-fail / 429 backoff rules).
- Per-provider KeyPool + backoff (reuse existing).

### Admin API (phase 1 “developer interface”)

Auth: same `PROXY_API_KEYS` (or later admin role).

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/admin/providers` | List providers (keys masked) |
| POST | `/admin/providers` | Add/update provider |
| DELETE | `/admin/providers/{id}` | Disable/remove |
| POST | `/admin/providers/{id}/refresh` | Refresh that provider’s models |
| GET | `/v1/models` | Unified list (all providers + auto) |

Persistence: `config/providers.yaml` + optional `.nimmakai/providers.json` overlay for runtime adds (survives restart).

### Non-goals (phase 1)

- Web UI / login pages  
- Scraping free-ai-tools README on a schedule  
- Native Anthropic/Google non-OpenAI protocols  
- Billing / spend tracking  
- Hardcoded free-tier quotas per vendor (user configures RPM/RPD)

## Success criteria

1. Add a second OpenAI-compatible provider via API/YAML; its models appear on `/v1/models`.
2. `nimmakai/auto` can select a non-NIM model when it scores highest / NIM unavailable.
3. Existing NIM-only `.env` setup still works with zero new config.
4. Tests for provider resolve, namespacing, multi-upstream routing.

## Spec self-review

- No dependency on free-ai-tools at runtime  
- Backward compatible with NIM env  
- Scope limited to OpenAI-compatible HTTP APIs  
- UI deferred explicitly  
