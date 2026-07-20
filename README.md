# Nimmakai

**Self-hosted OpenRouter-style multi-provider gateway** with intelligent model routing.  
Drop-in OpenAI-compatible proxy for NVIDIA NIM, Groq, Cerebras, Gemini, OpenRouter, and any other OpenAI-compatible API.

> *Fully vibe coded using Grok 4.5 and OpenCode MiMo v2.5 free.*

---

## Table of Contents

- [Why Nimmakai?](#why-nimmakai)
- [Features](#features)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Multi-Provider Setup](#multi-provider-setup)
- [Web Dashboard](#web-dashboard)
- [Routing & Intelligent Ladder](#routing--intelligent-ladder)
- [Per-Intent User Preferences](#per-intent-user-preferences)
- [OpenAI-Compatible Integration](#openai-compatible-integration)
- [Configuration Reference](#configuration-reference)
- [API Endpoints](#api-endpoints)
- [Caveats & Limitations](#caveats--limitations)
- [Troubleshooting](#troubleshooting)
- [Project Layout](#project-layout)
- [Development](#development)
- [Deploy on DigitalOcean](#deploy-on-digitalocean)
- [License](#license)

---

## Why Nimmakai?

Running coding agents (Cursor, OpenCode, Cline) on free-tier LLM APIs means juggling multiple accounts, rate limits, and model availability. Nimmakai solves this by:

1. **Multi-account key pooling** — distribute RPM across accounts, not hammer one
2. **Multi-provider gateway** — use NIM, Groq, Cerebras, Gemini, and more from a single endpoint
3. **Intelligent routing** — automatically picks the strongest available model for each task (coding, chat, reasoning, vision)
4. **Smart fallback** — when a model fails or is rate-limited, walk down a quality-ordered ladder instead of crashing
5. **Self-hosted** — your keys, your infrastructure, no third-party proxy

---

## Features

| Feature | Status |
|---------|--------|
| OpenAI `/v1/chat/completions` (stream + tools) | ✅ |
| `/v1/models`, `/v1/embeddings`, `/v1/completions`, `/v1/responses` | ✅ |
| Multi-provider hub (NIM + any OpenAI-compatible backend) | ✅ |
| Admin API + Web dashboard for provider management | ✅ |
| Per-intent model pinning (user preferences) | ✅ |
| Intelligent strength ladder (auto-scoring across all providers) | ✅ |
| Ordered fallback across models and providers | ✅ |
| Live catalog refresh (`GET /v1/models` from each provider) | ✅ |
| Per-provider key pools (RPM, RPD, cooldown, quarantine) | ✅ |
| Dynamic per-model context window discovery | ✅ |
| Exponential backoff on 429 / 5xx / transport errors | ✅ |
| Retry-After header respect | ✅ |
| Sticky sessions (opt-in), jitter, concurrency gates | ✅ |
| Diagnostic headers (`X-Nimmakai-*`) | ✅ |
| Intent classification (rules-based, optional LLM-assisted) | ✅ |
| Online learning (adjusts scores from real outcomes) | ✅ |
| Capability probes + health tracking | ✅ |
| Disk snapshot for cold-start resilience | ✅ |
| `ROUTING_ENABLED=false` bootstrap passthrough | ✅ |

---

## Architecture

```
  Cursor / OpenCode / agents / any OpenAI SDK
            │
            │  Base URL: http://localhost:8080/v1
            │  API key:  PROXY_API_KEYS
            │  Model:    nimmakai/auto  (or provider/model-id)
            ▼
       ┌──────────────────────────────────────────┐
       │  Nimmakai                                │
       │                                          │
       │  1. Auth (PROXY_API_KEYS)                │
       │  2. Classify (rules → intent)            │
       │  3. Select (preferences ?: ladder)       │
       │  4. Execute (try chain, fallback)        │
       │  5. Key pool (RPM + RPD + quarantine)    │
       │  6. Learn (record outcome → adjust)      │
       └─────────────┬────────────────────────────┘
                     │
     ┌───────────────┼───────────────┐
     ▼               ▼               ▼
  NVIDIA NIM       Groq           Cerebras
  (built-in)    (add via API)   (add via API)
```

### Core Components

| Component | File | Responsibility |
|-----------|------|----------------|
| **ProviderStore** | `catalog/providers.py` | Provider config (YAML + JSON overlay), API keys, namespaced model IDs |
| **ProviderHub** | `catalog/hub.py` | Per-provider `KeyPool` + `UpstreamClient` lifecycle, model→client routing |
| **ModelRegistry** | `catalog/registry.py` | Live catalog, context window discovery, snapshot persistence |
| **LadderService** | `catalog/ladder.py` | Scores every live model for each intent, builds strength-ordered ladders |
| **IntentClassifier** | `routing/classifier.py` | Request analysis → intent (coding, chat, reasoning, vision, etc.) |
| **ModelSelector** | `routing/selector.py` | Client model field → route decision (auto, alias, passthrough, user pref) |
| **FallbackExecutor** | `routing/fallback.py` | Ordered model attempts, soft-fail detection, cross-provider fallback |
| **AccountGuard** | `safety/guard.py` | Jitter, sticky, concurrency gate |
| **KeyPool** | `balancer.py` | Sliding-window RPM, EWMA scoring, 429 cooldown, auth quarantine |
| **UpstreamClient** | `upstream.py` | httpx forwarder with key rotation, Retry-After, exponential backoff |
| **LearningStore** | `catalog/learning.py` | Per-model outcome tracking, score delta computation |

### Data Flow (Request)

```
Client POST /v1/chat/completions
  → require_proxy_auth (PROXY_API_KEYS)
  → AccountGuard.before_request (jitter + sticky preference)
  → IntentClassifier.classify (rules → coding_agentic / chat_fast / ...)
  → ModelSelector.resolve (nimmakai/auto → chain of namespaced model IDs)
  → FallbackExecutor.execute_json/stream:
      for each model in chain:
        Hub.client_for_model(model) → (UpstreamClient, upstream_model_id)
        Send request with upstream model name
        If success → return (with X-Nimmakai-* headers)
        If retryable error → fallback to next model
      If all fail → 503
  → AccountGuard.after_request
  → LearningStore.record (success/failure/empty/tool_ok)
```

---

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (recommended)
- At least one NVIDIA NIM API key from [build.nvidia.com](https://build.nvidia.com/)

### 1. Install

```bash
git clone https://github.com/vskrch/Nimmakai
cd Nimmakai
uv sync
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your keys
```

Required settings:

```bash
# At least one NVIDIA NIM key
NIM_API_KEYS=nvapi-your-key-here

# Client-facing key (used in Authorization header)
PROXY_API_KEYS=sk-nimmakai-local-dev
```

Other settings have sensible defaults. See [Configuration Reference](#configuration-reference).

### 3. Run

```bash
uv run nimmakai
```

### 4. Verify

```bash
curl http://localhost:8080/health

curl http://localhost:8080/v1/models \
  -H "Authorization: Bearer sk-nimmakai-local-dev"

curl http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer sk-nimmakai-local-dev" \
  -H "Content-Type: application/json" \
  -d '{"model":"nimmakai/auto","messages":[{"role":"user","content":"Hello!"}]}'
```

### 5. Open the Dashboard

Navigate to **http://localhost:8080/dashboard**. Sign up with email, verify (stub backend returns the link), wait for admin approval, then use your `sk-nk-…` API key. Legacy `PROXY_API_KEYS` still work as admin break-glass.

---

## Multi-Provider Setup

Nimmakai can route through any OpenAI-compatible API. Built-in `nim` uses `NIM_*` env vars. Add more providers via the web dashboard, API, or `config/providers.yaml`.

### Via Web Dashboard

1. Open http://localhost:8080/dashboard
2. Go to **Providers** tab
3. Fill in: Provider ID (e.g. `groq`), Base URL, API Key(s)
4. Click **Add Provider**
5. Models appear instantly in the **Models** tab

### Via API

```bash
curl -X POST http://localhost:8080/admin/providers \
  -H "Authorization: Bearer sk-nimmakai-local-dev" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "groq",
    "name": "Groq Free Tier",
    "base_url": "https://api.groq.com/openai/v1",
    "api_keys": ["gsk-your-groq-key"],
    "rpm_limit": 30,
    "rpd_limit": 14400,
    "enabled": true
  }'
```

### Via YAML (config/providers.yaml)

```yaml
providers:
  - id: groq
    name: Groq
    base_url: https://api.groq.com/openai/v1
    api_keys_env: GROQ_API_KEYS
    enabled: true
    rpm_limit: 30
    rpd_limit: 14400
```

Then set `GROQ_API_KEYS=gsk-...` in `.env`.

### Provider Configuration Options

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Unique lowercase identifier (used in model names: `groq/llama-...`) |
| `name` | No | Display name for the dashboard |
| `base_url` | Yes | Full URL to the OpenAI-compatible API (must end in `/v1`) |
| `api_keys` | No | Array of API key strings |
| `api_keys_env` | No | Name of env var containing comma-separated keys |
| `enabled` | No | Default `true` |
| `rpm_limit` | No | Per-key requests per minute limit |
| `rpd_limit` | No | Per-key requests per day limit |
| `max_in_flight_per_key` | No | Default `3` |
| `api_style` | No | Default `openai` (only `openai` supported in phase 1) |

### Recommended Providers

| Provider | ID | Base URL | Free Tier Notes |
|----------|----|----------|-----------------|
| NVIDIA NIM | `nim` | `https://integrate.api.nvidia.com/v1` | ~40 RPM/key, built-in |
| Groq | `groq` | `https://api.groq.com/openai/v1` | 1K–14.4K req/day free |
| Cerebras | `cerebras` | `https://api.cerebras.ai/v1` | Fast inference, generous free tier |
| SiliconFlow | `silicon` | `https://api.siliconflow.cn/v1` | 1K RPM, 50K TPM free |
| Z.AI | `zai` | `https://api.z.ai/v1` | Generous free quota |
| DeepInfra | `deepinfra` | `https://api.deepinfra.com/v1/inference` | 200 concurrent free |
| OpenRouter | `openrouter` | `https://openrouter.ai/api/v1` | 50 req/day free |
| Hyperbolic | `hyperbolic` | `https://api.hyperbolic.xyz/v1` | $1 trial, then paid |

---

## Web Dashboard

The React dashboard (served from `/` or `/dashboard`) includes:

### Overview / Analytics
- KPI cards: requests, latency (avg/p95), tokens, estimated cost, success rate
- Request volume chart, intent distribution, top models / providers
- Time range presets: 1h / 6h / 24h / 7d

### Request Explorer
- Filterable, paginated trace table (intent, status, search)
- Langfuse-style span waterfall (classify → route → upstream / fallback)
- CSV / JSONL export via `/analytics/export/traces`

### Live Feed
- Real-time SSE stream from `/analytics/events?token=…`
- Pause/resume buffering, fallback badges, error highlighting

### Intents / Cost
- Intent confidence aggregates, fallback index distribution, top errors
- Cost by model / API key, editable per-model $/M rate overrides

### Providers / Models / Routing / Playground
- Provider CRUD, health, live catalog, ladder editor, chat playground

---

## Analytics API

Persistent request traces (SQLite WAL) with async batch writes — zero request-path blocking.

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/analytics/summary` | Yes | KPI snapshot (cached ~10s) |
| GET | `/analytics/traces` | Yes | Paginated / filterable traces |
| GET | `/analytics/traces/{id}` | Yes | Trace + spans waterfall |
| GET | `/analytics/timeseries/{metric}` | Yes | `requests`, `latency`, `tokens`, `cost`, `ttft` |
| GET | `/analytics/breakdown/{dim}` | Yes | `models`, `providers`, `intents`, `api_keys`, `errors`, `fallbacks` |
| GET | `/analytics/events` | Yes (`?token=`) | SSE live feed |
| GET | `/analytics/export/traces` | Yes | CSV or JSONL export |
| GET/PUT/DELETE | `/analytics/cost/rates` | Yes | Cost rate defaults + overrides |
| POST | `/analytics/retention/run` | Yes | Trigger retention/rollup cycle |

Config (env): `ANALYTICS_ENABLED`, `ANALYTICS_RETENTION_DAYS` (7), `ANALYTICS_ROLLUP_RETENTION_DAYS` (90), `ANALYTICS_BATCH_SIZE`, `ANALYTICS_FLUSH_INTERVAL`, `ANALYTICS_WEBHOOK_URL`, `ANALYTICS_OTLP_ENDPOINT` (optional `pip install nimmakai[otel]`).

---

## Routing & Intelligent Ladder

### How It Works

1. **Classify**: Analyze the request (tools → `coding_agentic`, short Q&A → `chat_fast`, long context → `long_horizon`, etc.) — same idea as OpenRouter Auto Router prompt analysis and Kilo Auto Model task classification.
2. **Resolve**: Map the client's `model` field to a routing decision:
   - `nimmakai/auto` / `openrouter/auto` / `kilo/auto` → intelligent ladder (drop-in auto-router)
   - `kilo-auto/frontier|balanced|efficient|free` → tiered auto (quality / default / cost / free-only)
   - `nimmakai/auto-coding` / `nimmakai/best` → force coding ladder
   - `nimmakai/auto-fast` / `nimmakai/auto-cheap` → speed or cost variant
   - `groq/llama-3.3-70b` → explicit model (with optional fallback)
   - `gpt-4o` → alias (maps to `chain:coding_agentic`)
3. **Ladder**: Score every live model across all providers for the detected intent:
   - Family affinity (Qwen → coding, Nemotron → chat)
   - Parameter size (397b > 70b > 8b)
   - Version / tier (3.5 > 3.0, ultra > nano)
   - Doc description keywords
   - Online learning (past failures, tool support, empty replies)
   - Capability probes
   - Continuous intelligence × speed × health ranking
4. **Execute**: Try the strongest model first. On error/unavailable/empty, walk down. On 429/5xx, exponential backoff + key rotate.
5. **Response `model` field**: rewritten to the **actual** upstream model used (OpenRouter Auto Router behavior). Requested id is in `X-Nimmakai-Requested-Model`.
6. **Session stickiness**: pins model + key for multi-turn chats via `session_id`, `X-Session-Id` / `X-Nimmakai-Session`, Cursor chat id, or implicit first-system+first-user fingerprint (like OpenRouter).

### OpenRouter / Kilo Auto Router Drop-in

Point Cursor / Kilo / any OpenAI client at Nimmakai and use the same model strings:

| Model id | Behavior |
|----------|----------|
| `openrouter/auto` | Prompt-aware best model (balanced) |
| `kilo/auto` | Same as balanced auto |
| `kilo-auto/frontier` | Max capability / coding-heavy |
| `kilo-auto/balanced` | Strong default |
| `kilo-auto/efficient` | Cost-aware (cheapest capable) |
| `kilo-auto/free` | Free-tier pool only |
| `nimmakai/auto` | Same as openrouter/auto |
| `nimmakai/best` / `nimmakai/auto-coding` | Best coding models |

OpenRouter-style request body options:

```json
{
  "model": "openrouter/auto",
  "session_id": "my-conversation-123",
  "plugins": [{
    "id": "auto-router",
    "allowed_models": ["zen/*", "nim/*"],
    "cost_quality_tradeoff": 3
  }],
  "messages": [{"role": "user", "content": "Explain quantum entanglement"}]
}
```

- `cost_quality_tradeoff`: 0 = pure quality … 10 = maximize cost savings (default maps to balanced/efficient).
- `allowed_models`: glob patterns (`anthropic/*`, `*/claude-*`).
- Response JSON `model` is the concrete model that answered.

### Intent Types

| Intent | Default Primary | When Selected |
|--------|----------------|---------------|
| `coding_agentic` | Qwen | Tools, agent prompts, multi-file |
| `chat_fast` | Nemotron | Plain Q&A, short messages |
| `reasoning` | Nemotron | Math, logic, deep reasoning |
| `long_horizon` | Qwen | Long context, planning |
| `vision` | Qwen | Image + text |
| `embeddings` | Nemotron | Embedding requests |

### Scoring Factors

Models are scored on a 100+ point scale across:
- **Modality gates**: exclude non-text for coding, non-vision for vision
- **Parameter size**: up to ~31 points for 397b models
- **Version/tier**: up to ~30 points
- **Family affinity**: up to 40 points for primary family
- **Doc keywords**: up to ~20 points
- **Online learning**: up to ±25 points from real outcomes
- **Capability probes**: up to 10 points for confirmed tool support

---

## Per-Intent User Preferences

You can override the intelligent ladder by pinning specific models for each intent.

### Via Web Dashboard

1. Go to **Routing** tab
2. For each intent, add models in priority order
3. Toggle **Strict** to skip ladder fallback
4. Click **Save**

### Via API

```bash
# Pin coding_agentic to specific models
curl -X POST http://localhost:8080/preferences \
  -H "Authorization: Bearer sk-nimmakai-local-dev" \
  -H "Content-Type: application/json" \
  -d '{
    "intent": "coding_agentic",
    "chain": ["groq/qwen-3.5-397b", "nim/qwen/qwen3.5-397b-a17b", "groq/deepseek-r1-distill-qwen-32b"],
    "strict": false
  }'

# Reset to intelligent routing
curl -X DELETE http://localhost:8080/preferences/coding_agentic \
  -H "Authorization: Bearer sk-nimmakai-local-dev"
```

Preferences are stored in `.nimmakai/user_preferences.json` and survive restarts.

---

## OpenAI-Compatible Integration

### Cursor

```json
{
  "baseUrl": "http://localhost:8080/v1",
  "apiKey": "sk-nimmakai-local-dev",
  "models": {
    "default": "openrouter/auto",
    "reasoning": "nimmakai/best"
  }
}
```

Any of `openrouter/auto`, `kilo/auto`, `kilo-auto/*`, or `nimmakai/auto` work — they all hit the same intelligent auto-router.

### OpenAI Python SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8080/v1",
    api_key="sk-nimmakai-local-dev",
)

# Let Nimmakai pick the best model
r = client.chat.completions.create(
    model="nimmakai/auto",
    messages=[{"role": "user", "content": "Hello"}],
)

# Or pin a specific provider/model
r = client.chat.completions.create(
    model="groq/llama-3.3-70b-versatile",
    messages=[{"role": "user", "content": "Hello"}],
)
```

### Node.js / TypeScript

```ts
import OpenAI from "openai";
const client = new OpenAI({
  baseURL: "http://localhost:8080/v1",
  apiKey: "sk-nimmakai-local-dev",
});
await client.chat.completions.create({
  model: "nimmakai/auto",
  messages: [{ role: "user", content: "Hello" }],
});
```

### curl

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer sk-nimmakai-local-dev" \
  -H "Content-Type: application/json" \
  -d '{"model":"nimmakai/auto","messages":[{"role":"user","content":"hi"}]}'
```

### Routing Headers (Response)

| Header | Description |
|--------|-------------|
| `X-Nimmakai-Model` | Actual upstream model used |
| `X-Nimmakai-Intent` | Detected intent (coding_agentic, chat_fast, etc.) |
| `X-Nimmakai-Key-Id` | Which pool key served the request |
| `X-Nimmakai-Route-Mode` | auto / alias / alias_model / passthrough / user_pref |
| `X-Nimmakai-Fallback-Index` | 0 unless a later chain model was used |
| `X-Nimmakai-Provider` | Which provider handled the request (nim, groq, etc.) |
| `X-Nimmakai-Context-Length` | Discovered context window of the model used |
| `X-Nimmakai-Requested-Model` | Original model field from the client |
| `X-Nimmakai-Auto-Tier` | balanced / frontier / efficient / free / fast / coding |
| `X-Nimmakai-Sticky-Model` | Session-pinned model (if any) |

### Custom Request Headers

| Header | Effect |
|--------|--------|
| `X-Nimmakai-Session` / `X-Session-Id` | Sticky session affinity (model + key) |
| `X-Nimmakai-Disable-Route: 1` | Force passthrough, no routing |
| `X-Nimmakai-Intent: reasoning` | Override intent classification |

Body field `session_id` is also accepted (OpenRouter-compatible).

---

## Configuration Reference

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PROXY_API_KEYS` | `[]` | Legacy admin break-glass keys (comma-separated) |
| `ALLOW_INSECURE_AUTH` | `false` | Accept any Bearer when PROXY empty |
| `ADMIN_EMAILS` | `[]` | Emails that become admin after verify |
| `EMAIL_BACKEND` | `stub` | `stub` (active) or `smtp` (implemented, not wired yet) |
| `PUBLIC_BASE_URL` | — | Base URL for verify links |
| `SESSION_COOKIE_NAME` | `nk_session` | Dashboard session cookie |
| `SESSION_SECURE_COOKIE` | `false` | Set `true` behind HTTPS |
| `SMTP_HOST` | — | SMTP server (see [docs/email-smtp.md](docs/email-smtp.md)) |
| `SMTP_PORT` | `587` | SMTP port (`465` + `SMTP_USE_SSL=true` for SSL) |
| `SMTP_USERNAME` | — | SMTP auth user |
| `SMTP_PASSWORD` | — | SMTP auth password |
| `SMTP_FROM` | — | From address (verified sender) |
| `SMTP_FROM_NAME` | `Nimmakai` | From display name |
| `SMTP_USE_TLS` | `true` | STARTTLS |
| `SMTP_USE_SSL` | `false` | Implicit SSL |
| `NIM_API_KEYS` | `[]` | NVIDIA NIM API keys |
| `NIM_BASE_URL` | `https://integrate.api.nvidia.com/v1` | NIM API base URL |
| `NIM_RPM_LIMIT` | `40` | Per-key requests per minute |
| `NIM_RPM_SAFETY_FACTOR` | `0.9` | Safety buffer (0–1.0) |
| `NIM_COOLDOWN_SECONDS` | `60` | Cooldown after 429 |
| `NIM_RPD_LIMIT` | `2000` | Daily requests per key |
| `NIM_MAX_IN_FLIGHT_PER_KEY` | `3` | Max concurrent requests per key |
| `GLOBAL_MAX_IN_FLIGHT` | `0` | Global concurrency cap (0 = auto) |
| `ROUTING_ENABLED` | `true` | Enable intelligent routing |
| `MODELS_CONFIG_PATH` | `config/models.yaml` | Model catalog path |
| `CLASSIFY_MODE` | `rules_only` | `rules_only` or `rules_then_llm` |
| `ENABLE_FALLBACK_ON_EXPLICIT` | `true` | Fall back when explicit model fails |
| `MAX_MODEL_FALLBACKS` | `6` | Max model attempts per request |
| `CATALOG_REFRESH_SECONDS` | `300` | Catalog refresh interval |
| `CATALOG_FETCH_DOCS` | `true` | Fetch NVIDIA model docs |
| `CATALOG_RUN_PROBES` | `true` | Run capability probes |
| `PROBE_BUDGET_PER_HOUR` | `8` | Probe calls per hour |
| `PROBE_EVERY_N_REFRESHES` | `6` | Probe every N refresh cycles |
| `INJECT_AUTO_MODEL` | `true` | Add nimmakai/auto to /v1/models |
| `SAFETY_JITTER_ENABLED` | `true` | Request jitter |
| `AUTH_FAIL_THRESHOLD` | `2` | 401/403 failures before quarantine |
| `AUTH_QUARANTINE_SECONDS` | `3600` | Quarantine duration |
| `STICKY_SESSIONS_ENABLED` | `true` | Session-to-key affinity |
| `STICKY_SESSION_TTL_SECONDS` | `1800` | Sticky session TTL |
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8080` | Server port |
| `LOG_LEVEL` | `info` | Logging level |
| `UPSTREAM_TIMEOUT` | `300` | Upstream request timeout |
| `CORS_ALLOW_ORIGINS` | `*` | CORS origins (comma-separated) |

---

## API Endpoints

### OpenAI-Compatible

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/chat/completions` | Chat (stream + tools supported) |
| POST | `/v1/completions` | Text completions |
| POST | `/v1/embeddings` | Embeddings |
| POST | `/v1/responses` | Responses (passthrough) |
| GET | `/v1/models` | List all models (namespaced across providers) |
| GET | `/v1/models/{id}` | Model detail |

### Admin

| Method | Path | Auth Required | Description |
|--------|------|---------------|-------------|
| GET | `/health` | No | Health check |
| GET | `/stats` | Yes | Per-key stats, routing, catalog |
| GET | `/ladder` | Yes | Live intelligent ladders |
| GET | `/catalog` | Yes | Full catalog snapshot |
| POST | `/admin/catalog/refresh` | Yes | Force catalog refresh |
| GET | `/admin/providers` | Yes | List providers (masked) |
| POST | `/admin/providers` | Yes | Add/update provider |
| DELETE | `/admin/providers/{id}` | Yes | Remove/disable provider |
| POST | `/admin/providers/{id}/refresh` | Yes | Refresh one provider |
| GET | `/preferences` | Yes | List user preferences |
| POST | `/preferences` | Yes | Set/modify preference |
| DELETE | `/preferences/{intent}` | Yes | Clear inten preference |
| DELETE | `/preferences` | Yes | Clear all preferences |
| GET | `/dashboard` | No | Web dashboard |

### Analytics

| Method | Path | Auth Required | Description |
|--------|------|---------------|-------------|
| GET | `/analytics/summary` | Yes | Dashboard KPIs |
| GET | `/analytics/traces` | Yes | Request explorer list |
| GET | `/analytics/traces/{id}` | Yes | Trace detail + spans |
| GET | `/analytics/timeseries/*` | Yes | Time-bucketed metrics |
| GET | `/analytics/breakdown/*` | Yes | Dimension aggregates |
| GET | `/analytics/events` | Yes (`?token=`) | SSE live feed |
| GET | `/analytics/export/traces` | Yes | CSV / JSONL export |

---

## Caveats & Limitations

### 1. Scoring Heuristics Are Approximate
The intelligent ladder uses rule-based scoring (family, size, version, doc keywords). It is not a ground-truth benchmark. Models from non-NIM providers get weaker scoring because NVIDIA docs don't cover them. Online learning helps, but the initial ranking may be wrong.

### 2. No Preemptive Context Checking
We advertise the discovered `context_length` per model, but we do not preemptively check if a prompt exceeds a model's window. If a prompt is too large, the upstream returns an error and Nimmakai falls back to the next model. No history rewriting or trimming is done.

### 3. Token Counting Is Approximate
Prompt size is estimated by character count, not token count. This is sufficient for intent classification but not for precise context fitting.

### 4. Provider Catalog Merging Is Best-Effort
Each provider's `/models` endpoint is fetched independently. If a provider is down, it is skipped — other providers continue serving. There is no cross-provider deduplication (same model on different providers appears as separate entries).

### 5. Learning State Is Local
Online learning is stored in `.nimmakai/learning.json` and is lost if the file is deleted. No multi-instance sharing (no Redis/DB backend).

### 6. Accounts + Admin Auth
End users: signup → email verify → **admin approve** → `sk-nk-…` API key. Dashboard uses an HTTP-only session cookie; `/v1/*` uses Bearer keys. Set `ADMIN_EMAILS` for auto-admin after verify. Legacy `PROXY_API_KEYS` remain break-glass admins. Analytics for non-admins are scoped to their `user_id`.

Email delivery defaults to **stub** (logs + returns `verify_url`). An **SMTP** sender and verify/OTP message builders are implemented but not wired into routes yet — see [docs/email-smtp.md](docs/email-smtp.md).

### 7. Phase 1 Only Supports OpenAI-Compatible APIs
Providers using native APIs (Anthropic Messages, Google Vertex, etc.) are not supported. They must be accessed through an OpenAI-compatible adapter.

### 8. No Billing / Spend Tracking
Nimmakai does not track or enforce spend limits. Use `RPD_LIMIT` and `RPM_LIMIT` at the provider config level.

---

## Troubleshooting

### 401 Unauthorized

```
{"error":{"message":"Invalid API key.","code":"invalid_api_key"}}
```

**Fix:** Set `PROXY_API_KEYS` in `.env` and use that key in your `Authorization` header.

If you want to accept any key for local development:
```
ALLOW_INSECURE_AUTH=true
```

### No NIM API Keys Configured

```
SystemExit: NIM_API_KEYS is required
```

**Fix:** Add at least one key to `NIM_API_KEYS` in `.env`, or configure another provider (the app will warn but still start):

```
NIM_API_KEYS=nvapi-your-key
```

### No Models Appear on /v1/models

**Possible causes:**
- No API keys configured for any provider
- Provider's `/models` endpoint returned an error
- Refresh hasn't run yet (first refresh happens at startup)

**Fix:**
```bash
# Check if provider is configured
curl http://localhost:8080/admin/providers \
  -H "Authorization: Bearer sk-nimmakai-local-dev"

# Force refresh
curl -X POST http://localhost:8080/admin/catalog/refresh \
  -H "Authorization: Bearer sk-nimmakai-local-dev"
```

### 429 Too Many Requests

**Causes:**
- Key exhausted its RPM budget
- Key exceeded daily RPD limit
- Key is cooling down after a previous 429

**Automatic handling:** Nimmakai automatically:
- Rotates to another key in the same provider's pool
- Applies exponential backoff (0.5s → 1s → 2s → 4s → 8s → 16s)
- Respects `Retry-After` headers from upstream
- Falls back to the next model in the chain

If all keys and all models are exhausted, you get a 503 with `nimmakai_pool_exhausted`.

**Tuning:**
```
NIM_RPM_LIMIT=40       # Lower if you hit 429s
NIM_RPM_SAFETY_FACTOR=0.9  # Increase safety margin (lower = safer)
NIM_COOLDOWN_SECONDS=120  # Wait longer after 429
```

### "pool exhausted" on /admin/providers

**Cause:** The pool has no available keys and the acquisition timed out.

**Fix:** Add more keys or check if existing keys are quarantined/cooldown:
```
GET /stats  (requires auth)
```

### Stream Hangs or Drops Mid-Response

Nimmakai streams byte-for-byte. If the upstream drops, the connection drops. Check:
- `UPSTREAM_TIMEOUT` (default 300s)
- Provider's stream reliability
- Network proxy/NAT settings

### Model Returns Empty Reply or No Tool Calls

The fallback executor detects:
- Empty `choices` array → soft-fail, try next model
- No `tool_calls` when tools were requested → soft-fail, try next model
- Clear "tool not supported" error → mark model as `tools_unsupported` in capability registry

If you see a model repeatedly returning empty or tool-less responses, check `/stats` for learning data or add it to the exclusion list via user preferences.

### Learning Data Lost

**Fix:** Nimmakai automatically creates `.nimmakai/learning.json` after each outcome. If the file is deleted, learning starts fresh. This is normal — scores will re-adjust as requests flow.

### Dashboard Shows "Not ready" or 503

**Causes:**
- Provider hub failed to start (check logs)
- Registry not loaded (no `models.yaml`)
- No providers with keys configured

**Check:**
```bash
curl http://localhost:8080/health
# Look for catalog_ok and providers fields
```

### How to Reset Everything to Defaults

```bash
# Remove runtime state
rm -rf .nimmakai/

# Reset providers to built-in NIM only
rm -f config/providers.yaml
# Recreate with just nim:
echo 'providers:
  - id: nim
    name: NVIDIA NIM
    base_url: https://integrate.api.nvidia.com/v1
    api_keys_env: NIM_API_KEYS
    enabled: true
    builtin: true
    rpm_limit: 40
    rpd_limit: 2000' > config/providers.yaml

# Restart
uv run nimmakai
```

---

## Project Layout

```
.
├── config/
│   ├── models.yaml              # Model aliases + intent chains + family policy
│   └── providers.yaml           # Provider definitions
├── docs/
│   ├── digitalocean.md          # App Platform + Droplet one-click deploy
│   ├── integration.md           # OpenAI drop-in integration guide
│   └── design-intelligent-router.md  # Full design document
├── src/nimmakai/
│   ├── __init__.py              # Version
│   ├── main.py                  # FastAPI app + lifespan
│   ├── config.py                # Settings (env → pydantic)
│   ├── auth.py                  # Client Bearer/key auth
│   ├── upstream.py              # httpx forwarder + backoff
│   ├── balancer.py              # KeyPool (RPM, RPD, EWMA, quarantine)
│   ├── catalog/
│   │   ├── __init__.py
│   │   ├── aliases.py           # Model name normalization
│   │   ├── context.py           # Dynamic context window extraction
│   │   ├── docs_fetcher.py      # NVIDIA build.nvidia.com/models.md parser
│   │   ├── families.py          # Family matchers + version resolution
│   │   ├── health.py            # Per-model error tracking + cooldown
│   │   ├── hub.py               # ProviderHub — multi-provider runtime
│   │   ├── ladder.py            # LadderService — intelligent scoring
│   │   ├── learning.py          # Online learning store (disk-backed)
│   │   ├── preferences.py       # Per-intent user preferences
│   │   ├── prober.py            # RPM-safe capability probes
│   │   ├── providers.py         # Provider config + store
│   │   ├── registry.py          # ModelRegistry — live catalog + refresh
│   │   └── schema.py            # Pydantic models for models.yaml
│   ├── data/
│   │   └── models.yaml          # Packaged default (for pip install)
│   ├── routes/
│   │   ├── openai.py            # /v1/* endpoints
│   │   └── admin.py             # /admin/*, /stats, /ladder, /preferences
│   ├── routing/
│   │   ├── __init__.py
│   │   ├── classifier.py        # IntentClassifier (rules + optional LLM)
│   │   ├── fallback.py          # FallbackExecutor (model chains)
│   │   ├── intents.py           # Intent enum
│   │   └── selector.py          # ModelSelector (model → route)
│   ├── safety/
│   │   ├── __init__.py
│   │   ├── backoff.py           # Exponential backoff
│   │   ├── budgets.py           # Daily budget helpers
│   │   ├── circuit.py           # Auth quarantine helpers
│   │   ├── concurrency.py       # Global concurrency gate
│   │   ├── guard.py             # Jitter + sticky + concurrency
│   │   ├── jitter.py            # Request jitter
│   │   └── sticky.py            # Session-to-key affinity
│   └── static/
│       └── index.html           # Web dashboard
├── tests/
│   ├── test_backoff.py
│   ├── test_balancer.py
│   ├── test_catalog.py
│   ├── test_classifier.py
│   ├── test_context.py
│   ├── test_egress_proxy.py
│   ├── test_fallback.py
│   ├── test_ladder.py
│   ├── test_learning.py
│   ├── test_providers.py
│   ├── test_safety.py
│   └── test_selector.py
├── scripts/
│   ├── generate-do-userdata.sh  # Interactive → DO Droplet User data (one-click)
│   └── do-smoke.sh              # Local Docker smoke for DO artifacts
├── .env.example                 # Example environment
├── docker-compose.do.yml        # Droplet Compose (port 80 + persistent volume)
├── pyproject.toml               # Dependencies + project metadata
└── LICENSE                      # MIT
```

---

## Development

```bash
# Sync + install dev dependencies
uv sync --group dev

# Run tests
uv run pytest

# Lint
uv run ruff check src tests

# Type check (if installed)
# uv run mypy src tests
```

### Adding a New Provider Type

Providers must be OpenAI-compatible (`/v1/chat/completions`, `/v1/models`). To add:

1. Register via `POST /admin/providers` or add to `config/providers.yaml`
2. The hub auto-discovers models via `GET {base_url}/models`
3. Models appear as `{provider_id}/{upstream_model_id}`

Native non-OpenAI APIs (Anthropic, Google Gemini native) are not supported in phase 1.

### Testing with Multiple Providers

```bash
# Start with a mock second provider using NIM's URL and a different key
# Or use a local vLLM instance
```

---

## Deploy to Heroku

Nimmakai runs on Heroku with zero configuration beyond setting environment variables.

### Prerequisites

- [Heroku CLI](https://devcenter.heroku.com/articles/heroku-cli)
- Git

### Steps

```bash
# Login to Heroku
heroku login

# Create the app (auto-detects Python buildpack)
heroku create your-nimmakai-name

# Set required environment variables (no .env file on Heroku)
heroku config:set PROXY_API_KEYS=sk-your-secret-key
heroku config:set NIM_API_KEYS=nvapi-your-nim-key
heroku config:set ALLOW_INSECURE_AUTH=false

# Optional: add more providers via env
heroku config:set GROQ_API_KEYS=gsk-your-groq-key

# Optional: restrict CORS to your app domain
heroku config:set CORS_ALLOW_ORIGINS=https://your-nimmakai-name.herokuapp.com

# Deploy
git push heroku main

# Open the app
heroku open
```

### Verify

```bash
curl https://your-nimmakai-name.herokuapp.com/health

curl https://your-nimmakai-name.herokuapp.com/v1/models \
  -H "Authorization: Bearer sk-your-secret-key"
```

The dashboard is at `https://your-nimmakai-name.herokuapp.com/dashboard`.

### Heroku-specific notes

- Python version is set via `.python-version` (uv buildpack) or auto-detected from `requirements.txt` (classic buildpack) — both provided
- All config goes through environment variables (no `.env` file on Heroku)
- Runs via `gunicorn` with `uvicorn` workers for production concurrency
- Persistent state (`.nimmakai/`) lives on the ephemeral filesystem and resets on each dyno restart — learning data resets are normal
- For production use with persistent state across restarts, consider [Heroku Postgres](https://elements.heroku.com/addons/heroku-postgresql) or [Heroku Redis](https://elements.heroku.com/addons/heroku-redis)

---

## Deploy on DigitalOcean

Full guide: **[docs/digitalocean.md](docs/digitalocean.md)**

| Path | Cost | Persistence | Best for |
|------|------|-------------|----------|
| **Droplet + one-click userdata** | ~$6/mo | Durable SQLite volume | Analytics, dashboard providers, simplest “paste & go” |
| App Platform (Heroku-style) | ~$10/mo | Ephemeral disk | Push-to-`main` auto-deploy |

Redeem [GitHub Student Pack](https://education.github.com/pack) DigitalOcean credits first when available.

### One-click Droplet (recommended for persistence)

On your laptop, run the interactive generator. It prompts for keys, then writes a **single User data script** you paste into droplet creation — the droplet clones this repo, builds Docker, and serves on port 80.

```bash
chmod +x scripts/generate-do-userdata.sh
./scripts/generate-do-userdata.sh
# → writes ./nimmakai-droplet-userdata.sh  (gitignored; contains secrets)
```

Then in DigitalOcean:

1. [Create Droplet](https://cloud.digitalocean.com/droplets/new)
2. **Image**: Marketplace → **Docker on Ubuntu**
3. **Size**: Basic **s-1vcpu-1gb** (~$6)
4. **SSH key** auth
5. **Advanced** → **User data** → paste the **entire** contents of `nimmakai-droplet-userdata.sh`
6. Create → wait **5–10 minutes** for the first image build
7. Open `http://YOUR_DROPLET_IP/health` or SSH and `cat /root/NIMMAKAI-READY.txt`

Cursor / agents:

```
Base URL:  http://YOUR_DROPLET_IP/v1
API Key:   <PROXY_API_KEYS from the generator output>
Model:     nimmakai/auto
```

Compose file used on the droplet: [`docker-compose.do.yml`](docker-compose.do.yml) (host **80→8080**, volume `nimmakai-data`).

Updates later:

```bash
ssh root@YOUR_DROPLET_IP
cd /opt/nimmakai && git pull && docker compose -f docker-compose.do.yml up -d --build
```

⚠ User data embeds secrets (visible via DO API/metadata). Keep the generated file out of git (already gitignored). Rotate keys if it leaks.

### App Platform (push-to-deploy)

1. [Create App](https://cloud.digitalocean.com/apps/new) → connect this GitHub repo → **Dockerfile**.
2. Size: **1 vCPU / 1 GiB fixed** (~$10/mo). HTTP port **8080**.
3. Encrypted env: `PROXY_API_KEYS`, provider `*_API_KEYS`, `ALLOW_INSECURE_AUTH=false`.
4. Push to `main` for auto-redeploy. Optional: `.github/workflows/deploy-digitalocean.yml` + `DIGITALOCEAN_*` secrets.

App spec: [`.do/app.yaml`](.do/app.yaml)

```bash
# Local image smoke test
docker build -t nimmakai:local .
docker run --rm -p 8080:8080 -e PROXY_API_KEYS=sk-test -e ALLOW_INSECURE_AUTH=false nimmakai:local
```

---

## License

MIT

---

*Built with Grok 4.5 and OpenCode MiMo v2.5 free — fully vibe coded.*
