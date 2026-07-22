# Nimmakai

**Self-hosted OpenRouter-style multi-provider gateway** with intelligent model routing, session-aware context tracking, streaming backpressure, and cancel-safe concurrency for production agentic workloads.

Drop-in OpenAI-compatible proxy for NVIDIA NIM, Groq, Cerebras, Gemini, DeepSeek, SambaNova, OpenRouter, and any OpenAI-compatible API.

> Built for Cursor, OpenCode, Cline, Claude Code, Kiro, and any agentic coding tool that needs resilient multi-provider routing.

---

## Why Nimmakai?

Running coding agents on LLM APIs means juggling multiple accounts, rate limits, model availability, and the brutal failure modes of long agentic loops — context overflows, rate-limit cascades, stream disconnects, tool-call malformations. Nimmakai solves this by:

1. **Multi-account key pooling** — distribute RPM/RPD across accounts, not hammer one
2. **Multi-provider gateway** — NIM, Groq, Cerebras, Gemini, DeepSeek, SambaNova, OpenRouter, and more from a single endpoint
3. **Intelligent routing** — automatically picks the strongest available model for each task (coding, chat, reasoning, vision, long-horizon)
4. **Session-aware context tracking** — knows how many tokens you've consumed across turns, routes to larger-context models before overflow
5. **Smart fallback** — when a model fails or rate-limits, walk down a quality-ordered ladder; pre-filters models known to lack tool support
6. **Cancel-safe concurrency** — no key in_flight leaks, no gate slot leaks under client disconnects (CancelledError-safe)
7. **Streaming backpressure** — bounded memory for slow consumers under concurrent multi-tenant load
8. **Self-hosted** — your keys, your infrastructure, no third-party proxy

---

## Features

| Feature | Status |
|---------|--------|
| OpenAI `/v1/chat/completions` (stream + tools + parallel tool calls) | ✅ |
| `/v1/models`, `/v1/embeddings`, `/v1/completions`, `/v1/responses` | ✅ |
| Multi-provider hub (NIM + any OpenAI-compatible backend) | ✅ |
| Admin API + Web dashboard for provider management | ✅ |
| Per-intent model pinning (user preferences) | ✅ |
| Intelligent strength ladder (auto-scoring across all providers) | ✅ |
| Ordered fallback across models and providers | ✅ |
| **Tool capability pre-filtering** (skip models that can't handle tools) | ✅ |
| **Session-level context tracking** (cumulative token budget across turns) | ✅ |
| **Streaming backpressure** (bounded queue — no OOM on slow clients) | ✅ |
| **Per-model max_tokens capping** (prevent 400 errors) | ✅ |
| **Per-model temperature recommendations** (coding → 0, reasoning → 1) | ✅ |
| **Retryable provider 400 detection** ("upstream request failed" → fallback) | ✅ |
| Cancel-safe key release (CancelledError-safe) | ✅ |
| Cancel-safe gate release (no concurrency slot leaks) | ✅ |
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
| Rejected admin auto-reactivation prevention | ✅ |
| Dict-mutation-safe catalog refresh | ✅ |
| Race-free learning persistence (epoch counter) | ✅ |
| Cost override cache (non-blocking) | ✅ |
| Writer re-enqueue on transient DB failure | ✅ |
| Guarded admin JSON parsing (400 instead of 500) | ✅ |
| Mixed-case provider model round-trip (SambaNova) | ✅ |
| Dynamic cost pricing from models.dev + bulk import | ✅ |

---

## Architecture

```
  Cursor / OpenCode / Cline / Kiro / any OpenAI SDK
            │
            │  Base URL: http://localhost:8080/v1
            │  API key:  PROXY_API_KEYS or sk-nk-...
            │  Model:    nimmakai/auto  (or provider/model-id)
            ▼
       ┌──────────────────────────────────────────────────┐
       │  Nimmakai                                        │
       │                                                  │
       │  1. Auth (PROXY_API_KEYS | sk-nk-... sessions)   │
       │  2. Guard.before_request (jitter + sticky)       │
       │  3. Classify (rules → intent)                    │
       │     - tools/functions present → coding_agentic   │
       │     - User-Agent / X-Client headers → coding     │
       │     - Agent fingerprints (Cursor, Cline, Kiro)   │
       │  4. Session context lookup (cumulative tokens)   │
       │  5. Select (preferences ?: ladder)               │
       │     - Filter by context budget across turns      │
       │     - Filter by tool capability                  │
       │  6. Execute (try chain, cancel-safe fallback)    │
       │     - Per-model max_tokens capping               │
       │     - Pre-filter non-tool-supporting models      │
       │     - On failure: record + advance               │
       │     - On transport error: circuit + advance       │
       │  7. Key pool (RPM + RPD + quarantine)            │
       │  8. Update session context (token count)         │
       │  9. Learn (record outcome → adjust)              │
       └─────────────┬────────────────────────────────────┘
                     │
     ┌───────────────┼───────────────┬───────────────┐
     ▼               ▼               ▼               ▼
  NVIDIA NIM       Groq           Cerebras       DeepSeek
  (built-in)    (add via API)   (add via API)  (add via API)
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
| **SessionContext** | `safety/sticky.py` | Per-session cumulative token tracking for multi-turn context budget |
| **CostCache** | `analytics/store.py` | 60s write-through cache for cost overrides (non-blocking) |

### Data Flow (Request)

```
Client POST /v1/chat/completions
  → require_proxy_auth (PROXY_API_KEYS)
  → AccountGuard.before_request (jitter + sticky preference + concurrency gate)
  → IntentClassifier.classify (rules → coding_agentic / chat_fast / ...)
    → Header-based agent detection (User-Agent, X-Client)
    → Body-based detection (tools, tool_choice, tool role)
    → Text analysis (fingerprints, code fences, reasoning keywords)
  → ModelSelector.resolve (nimmakai/auto → chain of namespaced model IDs)
    → Session context lookup: cumulative tokens from previous turns
    → estimated_tokens = session.total_prompt_tokens + current_estimate
    → Filter models by context capacity
    → Filter models by tool capability (when tools present)
  → FallbackExecutor.execute_json/stream:
      chain = _chain(decision, had_tools=bool(tools))
      for each model in chain:
        rec = ladder.model_recommendations(model)
        cap max_tokens to model limit if needed
        Hub.client_for_model(model) → (UpstreamClient, upstream_model_id)
        Send request with upstream model name
        If 2xx with content → return (with X-Nimmakai-* headers)
        If empty reply / no tool_calls → soft-fail, advance
        If 400 with "upstream request failed" → reclassify as retryable, advance
        If 404/429/5xx → backoff + key rotate + advance
        If httpx transport error → circuit_breaker.fail + advance
      If all fail → 503 OpenAI error envelope
  → AccountGuard.after_request (gate release + sticky pin)
  → StickySessionStore.update_session_context (cumulative tokens)
  → LearningStore.record (success/failure/empty/tool_ok)
```

---

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (recommended)
- At least one API key from any supported provider

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

Minimum required settings:

```bash
# Client-facing key (used in Authorization header)
PROXY_API_KEYS=sk-nimmakai-local-dev

# Add providers via env vars (or dashboard after startup)
NVIDIA NIM: NIM_API_KEYS=nvapi-your-key-here
DeepSeek:   DEEPSEEK_API_KEYS=sk-your-deepseek-key
Groq:       GROQ_API_KEYS=gsk-your-groq-key
```

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

Nimmakai can route through any OpenAI-compatible API. Built-in `nim` uses `NIM_*` env vars. Add more providers via the web dashboard, API, `config/providers.yaml`, or env vars.

### Supported Providers

The following providers have built-in presets (auto-configurable via env var):

| Provider | Env Var | Base URL | Free Tier |
|----------|---------|----------|-----------|
| NVIDIA NIM | `NIM_API_KEYS` | `https://integrate.api.nvidia.com/v1` | ~40 RPM/key |
| OpenCode Zen | `OPENCODE_ZEN_API_KEYS` | `https://opencode.ai/zen/v1` | Free coding models |
| Groq | `GROQ_API_KEYS` | `https://api.groq.com/openai/v1` | 1K–14.4K req/day |
| Cerebras | `CEREBRAS_API_KEYS` | `https://api.cerebras.ai/v1` | Fast free tier |
| DeepSeek | `DEEPSEEK_API_KEYS` | `https://api.deepseek.com/v1` | Free credits on signup |
| SambaNova | `SAMBANOVA_API_KEYS` | `https://api.sambanova.ai/v1` | Free ultra-fast |
| Google Gemini | `GEMINI_API_KEYS` | `https://generativelanguage.googleapis.com/v1beta/openai` | Free tier |
| OpenRouter | `OPENROUTER_API_KEYS` | `https://openrouter.ai/api/v1` | 50 req/day |
| Together AI | `TOGETHER_API_KEYS` | `https://api.together.xyz/v1` | Free credits |
| Fireworks AI | `FIREWORKS_API_KEYS` | `https://api.fireworks.ai/inference/v1` | Free credits |
| DeepInfra | `DEEPINFRA_API_KEYS` | `https://api.deepinfra.com/v1/openai` | Free credits |
| Mistral AI | `MISTRAL_API_KEYS` | `https://api.mistral.ai/v1` | Free experimental |
| Hyperbolic | `HYPERBOLIC_API_KEYS` | `https://api.hyperbolic.xyz/v1` | Free credits |
| GitHub Models | `GITHUB_MODELS_API_KEYS` | `https://models.inference.ai.azure.com` | Free |
| Cloudflare Workers AI | `CLOUDFLARE_API_KEYS` | `https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/v1` | Free tier |

### Via Env Var (Auto-Registration)

```bash
# Add to .env — provider is auto-registered at boot
DEEPSEEK_API_KEYS=sk-your-deepseek-key
GROQ_API_KEYS=gsk-your-groq-key
```

### Via Web Dashboard

1. Open http://localhost:8080/dashboard
2. Go to **Providers** tab
3. Click **Add Provider** → pick from presets or enter custom URL
4. Paste API key(s)
5. Models appear instantly in the **Models** tab

### Via API

```bash
curl -X POST http://localhost:8080/admin/providers \
  -H "Authorization: Bearer sk-nimmakai-local-dev" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "deepseek",
    "name": "DeepSeek",
    "base_url": "https://api.deepseek.com/v1",
    "api_keys": ["sk-your-deepseek-key"],
    "rpm_limit": 60,
    "rpd_limit": 10000,
    "enabled": true
  }'
```

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
| `model_whitelist` | No | Only import these model IDs (glob patterns) |
| `model_blacklist` | No | Exclude these model IDs (glob patterns) |
| `api_style` | No | Default `openai` (only `openai` supported) |

---

## Routing & Intelligent Ladder

### How It Works

1. **Classify**: Analyze the request using a multi-step pipeline:
   - **Header detection**: User-Agent or X-Client headers from agent tools → `coding_agentic`
   - **Tool detection**: `tools`/`functions` present, `tool_choice` not `none`, `tool` role messages → `coding_agentic` (0.98 confidence)
   - **Fingerprints**: System prompts containing agent signatures (Cursor, OpenCode, Cline, Kiro, Continue, Codeium, Windsurf) → `coding_agentic` (0.92)
   - **Code keywords**: `import`, `def`, `function`, `class`, `async`, etc. → `coding_agentic` (0.70)
   - **Reasoning**: "prove", "theorem", "step-by-step" → `reasoning`
   - **Vision**: Image parts in messages → `vision`
   - **Long horizon**: Character length > 48000 → `long_horizon` or `coding_agentic`
   - **Short chat**: < 800 chars, no tools, no fences → `chat_fast`

2. **Resolve**: Map the client's `model` field to a routing decision:
   - `nimmakai/auto` / `openrouter/auto` / `kilo/auto` → intelligent ladder (drop-in auto-router)
   - `kilo-auto/frontier|balanced|efficient|free` → tiered auto
   - `nimmakai/auto-coding` / `nimmakai/best` → force coding ladder
   - `nimmakai/auto-fast` / `nimmakai/auto-cheap` → speed/cost variant
   - `groq/llama-3.3-70b` → explicit model (with optional fallback to siblings)
   - `gpt-4o` → alias (maps to `chain:coding_agentic`)

3. **Session context**: Look up cumulative token usage from previous turns in this session. Use it to improve context window estimation — prevents routing to models that will overflow mid-loop.

4. **Ladder**: Score every live model across all providers for the detected intent:
   - Family affinity (Qwen → coding, Nemotron → chat)
   - Parameter size (397b > 70b > 8b)
   - Version / tier (3.5 > 3.0, ultra > nano)
   - Doc description keywords
   - Online learning (past failures, tool support, empty replies)
   - Capability probes
   - Continuous intelligence × speed × health ranking

5. **Execute**: Try the strongest model first. On error/unavailable/empty, walk down. On 429/5xx, exponential backoff + key rotate.

6. **Response `model` field**: rewritten to the **actual** upstream model used. Requested id is in `X-Nimmakai-Requested-Model`.

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

- `cost_quality_tradeoff`: 0 = pure quality … 10 = maximize cost savings
- `allowed_models`: glob patterns (`anthropic/*`, `*/claude-*`)
- `session_id`: body-level session ID for sticky routing (also accepts `X-Session-Id`, `x-cursor-chat-id`, `x-opencode-session`, `x-kiro-session`, etc.)

### Intent Types

| Intent | When Selected | Default Primary |
|--------|---------------|-----------------|
| `coding_agentic` | Tools, agent prompts, multi-file edits, code fences | Highest-scoring coder |
| `chat_fast` | Plain Q&A, short messages | Speed-optimized model |
| `reasoning` | Math, logic, deep reasoning, proofs | Reasoning-capable model |
| `long_horizon` | Long context, planning, multi-step tasks | Largest-context model |
| `vision` | Image + text inputs | Vision-capable model |
| `embeddings` | Embedding requests | Embedding model |

### Scoring Factors

Models are scored on a 100+ point scale across:
- **Modality gates**: exclude non-text for coding, non-vision for vision
- **Parameter size**: up to ~31 points for 397b models
- **Version/tier**: up to ~30 points
- **Family affinity**: up to 40 points for primary family
- **Doc keywords**: up to ~20 points
- **Online learning**: up to ±25 points from real outcomes
- **Capability probes**: up to 10 points for confirmed tool support
- **Tool capability**: +15% for confirmed tool support, -90% for confirmed no tools
- **Vision capability**: +10% for confirmed vision support, 0% for confirmed no vision
- **Reasoning capability**: +20% for reasoning models on reasoning tasks

---

## Session-Aware Agentic Improvements

### Session-Level Context Tracking

Nimmakai tracks cumulative token usage per session across multi-turn agentic loops. This enables smarter routing:

```
Turn 1: prompt=5K tokens, completion=1K tokens  → routed to 16K model
Turn 2: prompt=10K tokens (5K history + 5K new) → session=6K tokens
Turn 3: prompt=15K tokens → session=11K tokens, routed to 128K model (overflow detected)
```

**How it works:**
- After each successful request, `StickySessionStore.update_session_context()` accumulates `total_prompt_tokens` and `total_completion_tokens`
- `_prepare_routed()` checks `guard.sticky.get_session_context(session_id)` → if cumulative tokens exist, `estimated_tokens = session.total_prompt_tokens + current_estimate`
- `_chain()` drops models whose `context_length < estimated_tokens` (unknown-context models kept as fallback)

**Benefit:** Prevents context overflow in agentic loops that grow past a model's limit. Instead of wasting a fallback slot on a predictable 400, the proxy routes directly to a larger-context model.

### Tool Capability Pre-Checking

When a request contains `tools`/`functions`, `_chain()` pre-filters models with `supports_tools=False`:

```python
if had_tools and hasattr(self.registry, "ladder"):
    caps = getattr(self.registry.ladder, "capabilities", {})
    for m in raw:
        cap = caps.get(m) or {}
        if cap.get("supports_tools") is False:
            continue  # skip — known to not support tools
        filtered.append(m)
```

**Benefit:** Avoids wasting a fallback slot and 5–30 seconds of latency on a model that will predictably return "tools not supported". Tool capability is learned reactively from 400 errors and tracked in the ladder's `capabilities` dict.

### Streaming Backpressure

`robust_iter` uses a bounded `asyncio.Queue(maxsize=32)` between the upstream iterator and the downstream response:

```
upstream → producer task → [queue: max 32 chunks] → consumer → client
```

When the client is slow (bad network, large tool response processing), the queue fills and the producer blocks — backpropagating pressure to the upstream.

**Benefit:** Prevents unbounded memory growth under concurrent slow consumers. Without this, 20 agentic sessions each buffering a 5MB stream would consume 100MB+ of memory.

### Retryable Provider 400 Detection

Some providers wrap server-side errors in HTTP 400 responses (e.g., `"Upstream request failed"`). Nimmakai now treats these as retryable:

```python
_retryable_phrases = (
    "upstream request failed",
    "error from provider",
    "internal error",
    "service unavailable",
    "request failed",
    "bad gateway",
    "gateway timeout",
)
```

When a 400 body contains any of these phrases, the fallback executor advances to the next model instead of returning the error to the client.

**Benefit:** Turns a hard-failure into a transparent fallback — the client sees a working response from the next model in the chain.

### Per-Model Max Tokens Capping

`model_recommendations()` returns per-model `max_tokens_limit`. The fallback executor caps `max_tokens` in the upstream request:

```python
rec = self.registry.ladder.model_recommendations(model)
max_limit = rec.get("max_tokens_limit")
if max_limit:
    attempt_body["max_tokens"] = min(
        attempt_body.get("max_tokens", max_limit), max_limit
    )
```

Known limits:
- GPT-4o / GPT-4.1: 16,384
- Claude (Sonnet/Opus): 8,192
- DeepSeek: 8,192
- Gemini: 8,192

**Benefit:** Prevents 400 errors from requesting more tokens than a model supports. When the client doesn't set `max_tokens`, the proxy uses the model's limit as the default.

### Cancel-Safe Resource Release

All resource acquisitions use `try/finally` and catch `BaseException` to prevent leaks on `CancelledError`:

- **Key pool slots**: `upstream.py` wraps acquire→release in `try/finally` with a `released` flag
- **Global concurrency gate**: `guard.py` catches `BaseException` to release on cancellation
- **Stream byte_iter**: catches `BaseException` to mark failure and release key
- **Gate on error paths**: `openai.py` releases gate on `ValueError("model_disabled")`

**Benefit:** No permanent resource leaks when client disconnects mid-request. Before this fix, 3 cancellations on a key would block it forever. Now the key is always released.

### Writer Durability

When a trace batch write fails (e.g., SQLite transient error), the writer re-enqueues the batch instead of silently dropping it:

```python
except Exception:
    logger.exception("analytics batch write failed (n=%s)", len(batch))
    for trace in batch:
        try:
            self._queue.put_nowait(trace)
        except asyncio.QueueFull:
            self._dropped += 1
```

**Benefit:** Zero trace loss on transient database errors. Previously up to 50 traces per batch were silently dropped.

### Race-Free Learning Persistence

The learning store uses an epoch counter to avoid lost updates during concurrent save+record:

```python
# In record():
self._epoch += 1

# In save():
if self._epoch == epoch_at_start:
    self._dirty = False  # only clear if no new records arrived
```

**Benefit:** No lost learning signals under concurrent request load. Previously, if a `record()` happened during `save()`, the `_dirty` flag was incorrectly cleared.

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
    "chain": ["deepseek/deepseek-chat", "nim/qwen/qwen3.5-397b-a17b", "groq/deepseek-r1-distill-qwen-32b"],
    "strict": false
  }'

# Reset to intelligent routing
curl -X DELETE http://localhost:8080/preferences/coding_agentic \
  -H "Authorization: Bearer sk-nimmakai-local-dev"
```

Preferences are stored in SQLite and survive restarts.

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

### OpenCode

```json
{
  "baseUrl": "http://localhost:8080/v1",
  "apiKey": "sk-nimmakai-local-dev"
}
```

### Cline

```json
{
  "apiProvider": "openai",
  "openAiBaseUrl": "http://localhost:8080/v1",
  "openAiApiKey": "sk-nimmakai-local-dev",
  "openAiModelId": "nimmakai/auto"
}
```

### Kiro

```
Base URL: http://localhost:8080/v1
API Key:  sk-nimmakai-local-dev
Model:    nimmakai/auto
```

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
| `X-Nimmakai-Rule-Id` | Classification rule that matched |
| `X-Nimmakai-Key-Id` | Which pool key served the request |
| `X-Nimmakai-Route-Mode` | auto / alias / alias_model / passthrough / user_pref |
| `X-Nimmakai-Fallback-Index` | 0 unless a later chain model was used |
| `X-Nimmakai-Provider` | Which provider handled the request (nim, groq, deepseek) |
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
| `X-Cursor-Chat-Id` | Cursor session ID for sticky routing |
| `X-OpenCode-Session` | OpenCode session ID for sticky routing |
| `X-Cline-Session` | Cline session ID for sticky routing |
| `X-Kiro-Session` | Kiro session ID for sticky routing |

Body field `session_id` is also accepted (OpenRouter-compatible).

---

## Web Dashboard

The React dashboard (served from `/` or `/dashboard`) includes:

### Overview / Analytics
- KPI cards: requests, latency (avg/p95), tokens, estimated cost, success rate
- Request volume chart, intent distribution, top models / providers
- Time range presets: 1h / 6h / 24h / 7d

### Request Explorer
- Filterable, paginated trace table (intent, status, search, date range)
- Langfuse-style span waterfall (classify → route → upstream / fallback)
- CSV / JSONL export via `/analytics/export/traces`

### Live Feed
- Real-time SSE stream from `/analytics/events?token=…`
- Pause/resume buffering, fallback badges, error highlighting

### Cost Center
- Cost by model / API key breakdown
- Editable per-model $/M rate overrides
- **Auto-fill from models.dev** - bulk import pricing for all live models
- Manual override input for any model

### Intents / Models / Providers / Routing / Playground
- Intent confidence aggregates, fallback distribution, top errors
- Model pool enable/disable per model, bulk provider actions
- Provider CRUD, health, live catalog
- Ladder viewer, chat playground

---

## Analytics API

Persistent request traces (SQLite WAL) with async batch writes — zero request-path blocking. 60s write-through cache for cost overrides.

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/analytics/summary` | Yes | KPI snapshot (cached ~10s) |
| GET | `/analytics/traces` | Yes | Paginated / filterable traces |
| GET | `/analytics/traces/{id}` | Yes | Trace + spans waterfall |
| GET | `/analytics/timeseries/{metric}` | Yes | `requests`, `latency`, `tokens`, `cost`, `ttft` |
| GET | `/analytics/breakdown/{dim}` | Yes | `models`, `providers`, `intents`, `api_keys`, `errors`, `fallbacks` |
| GET | `/analytics/events` | Yes (`?token=`) | SSE live feed |
| GET | `/analytics/export/traces` | Yes | CSV or JSONL export |
| GET | `/analytics/cost/rates` | Yes | All rates (defaults + overrides) |
| PUT | `/analytics/cost/rates/{model_id}` | Yes | Set cost override ($/M tokens) |
| DELETE | `/analytics/cost/rates/{model_id}` | Yes | Delete cost override |
| POST | `/analytics/cost/rates/import` | Yes | Bulk-import rates from models.dev |
| POST | `/analytics/retention/run` | Yes | Trigger retention/rollup cycle |

Config (env): `ANALYTICS_ENABLED`, `ANALYTICS_RETENTION_DAYS` (7), `ANALYTICS_ROLLUP_RETENTION_DAYS` (90), `ANALYTICS_BATCH_SIZE`, `ANALYTICS_FLUSH_INTERVAL`, `ANALYTICS_WEBHOOK_URL`, `ANALYTICS_OTLP_ENDPOINT`.

### Cost Rate Management

**Manual per-model:**
```bash
curl -X PUT http://localhost:8080/analytics/cost/rates/nim/deepseek-v4-flash \
  -H "Authorization: Bearer sk-nimmakai-local-dev" \
  -H "Content-Type: application/json" \
  -d '{"input_per_m": 0.50, "output_per_m": 0.80}'
```

**Bulk import from models.dev (auto-fill all live models):**
```bash
# Fill gaps only (skip models with existing overrides)
curl -X POST http://localhost:8080/analytics/cost/rates/import \
  -H "Authorization: Bearer sk-nimmakai-local-dev" \
  -H "Content-Type: application/json" \
  -d '{"overwrite": false}'

# Overwrite all with fresh dynamic data
curl -X POST http://localhost:8080/analytics/cost/rates/import \
  -H "Authorization: Bearer sk-nimmakai-local-dev" \
  -H "Content-Type: application/json" \
  -d '{"overwrite": true}'
```

Cost matching resolution order:
1. Explicit overrides (from admin API)
2. Free-tier patterns (groq, cerebras, zen, etc. → $0)
3. Dynamic pricing from models.dev (namespaced ID match)
4. Hardcoded fallback rates
5. $0 for unknown models

---

## Configuration Reference

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| **Auth** | | |
| `PROXY_API_KEYS` | `[]` | Legacy admin break-glass keys (comma-separated) |
| `ALLOW_INSECURE_AUTH` | `false` | Accept any Bearer when PROXY empty |
| `ADMIN_EMAILS` | `[]` | Emails that become admin after verify |
| `EMAIL_BACKEND` | `stub` | `stub` or `smtp` |
| `PUBLIC_BASE_URL` | — | Base URL for verify links |
| `SESSION_COOKIE_NAME` | `nk_session` | Dashboard session cookie |
| `SESSION_SECURE_COOKIE` | `false` | Set `true` behind HTTPS |
| **SMTP** | | |
| `SMTP_HOST` | — | SMTP server |
| `SMTP_PORT` | `587` | SMTP port (`465` + `SMTP_USE_SSL=true` for SSL) |
| `SMTP_USERNAME` | — | SMTP auth user |
| `SMTP_PASSWORD` | — | SMTP auth password |
| `SMTP_FROM` | — | From address |
| `SMTP_FROM_NAME` | `Nimmakai` | From display name |
| `SMTP_USE_TLS` | `true` | STARTTLS |
| `SMTP_USE_SSL` | `false` | Implicit SSL |
| **Providers** | | |
| `NIM_API_KEYS` | `[]` | NVIDIA NIM API keys |
| `NIM_BASE_URL` | `https://integrate.api.nvidia.com/v1` | NIM API base URL |
| `NIM_RPM_LIMIT` | `40` | Per-key requests per minute |
| `NIM_RPM_SAFETY_FACTOR` | `0.9` | Safety buffer (0–1.0) |
| `NIM_COOLDOWN_SECONDS` | `60` | Cooldown after 429 |
| `NIM_RPD_LIMIT` | `2000` | Daily requests per key |
| `NIM_MAX_IN_FLIGHT_PER_KEY` | `3` | Max concurrent requests per key |
| `GLOBAL_MAX_IN_FLIGHT` | `0` | Global concurrency cap (0 = auto from all providers) |
| `NIM_EGRESS_PROXIES` | `[]` | Corporate egress proxies |
| `HTTP_PROXY` / `HTTPS_PROXY` | — | System egress proxy |
| **Routing** | | |
| `ROUTING_ENABLED` | `true` | Enable intelligent routing |
| `MODELS_CONFIG_PATH` | `config/models.yaml` | Model catalog path |
| `CLASSIFY_MODE` | `rules_only` | `rules_only` or `rules_then_llm` |
| `ENABLE_FALLBACK_ON_EXPLICIT` | `true` | Fall back when explicit model fails |
| `MAX_MODEL_FALLBACKS` | `10` | Max model attempts per request |
| `CODING_MAX_FALLBACKS` | `12` | Extra fallbacks for coding_agentic |
| `CATALOG_REFRESH_SECONDS` | `300` | Catalog refresh interval |
| `SELF_HEAL_SECONDS` | `120` | Self-heal loop interval |
| `CATALOG_FETCH_DOCS` | `true` | Fetch NVIDIA model docs |
| `CATALOG_RUN_PROBES` | `true` | Run capability probes |
| `PROBE_BUDGET_PER_HOUR` | `8` | Probe calls per hour |
| `PROBE_EVERY_N_REFRESHES` | `6` | Probe every N refresh cycles |
| `INJECT_AUTO_MODEL` | `true` | Add nimmakai/auto to /v1/models |
| `STRICT_CATALOG` | `false` | Error on YAML parse failure |
| `FALLBACK_ON_POOL_EXHAUST` | `true` | Advance chain on key exhaustion |
| `ADAPTIVE_ROUTING` | `true` | Per-request optimizer (health × speed × capability) |
| `REQUEST_DEADLINE_SECONDS` | `180` | End-to-end request deadline (threaded through chain) |
| `RETRY_BACKOFF_BASE_SECONDS` | `0.5` | Exponential backoff base |
| `RETRY_BACKOFF_CAP_SECONDS` | `16.0` | Exponential backoff cap |
| `UPSTREAM_TIMEOUT` | `300` | Upstream request timeout |
| `STREAM_TTFT_TIMEOUT_SECONDS` | `12` | Time to first stream token |
| `STREAM_IDLE_TIMEOUT_SECONDS` | `180` | Stream idle timeout (per chunk) |
| `LONG_CONTEXT_CHARS` | `48000` | Long-horizon classification threshold |
| `SHORT_CHAT_CHARS` | `800` | Short-chat classification threshold |
| `LLM_CLASSIFY_THRESHOLD` | `0.55` | LLM classify confidence threshold |
| **Safety** | | |
| `SAFETY_JITTER_ENABLED` | `false` | Request jitter (off by default for Cursor) |
| `SAFETY_JITTER_MS_MIN` | `0` | Jitter minimum |
| `SAFETY_JITTER_MS_MAX` | `0` | Jitter maximum |
| `AUTH_FAIL_THRESHOLD` | `2` | 401/403 failures before quarantine |
| `AUTH_QUARANTINE_SECONDS` | `3600` | Quarantine duration |
| `STICKY_SESSIONS_ENABLED` | `true` | Session-to-key/model affinity |
| `STICKY_SESSION_TTL_SECONDS` | `1800` | Sticky session TTL |
| `STICKY_BOOST` | `3.0` | Sticky key score multiplier |
| **Server** | | |
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8080` | Server port |
| `LOG_LEVEL` | `info` | Logging level |
| `CORS_ALLOW_ORIGINS` | `*` | CORS origins (comma-separated) |
| `SQLITE_PATH` | `.nimmakai/nimmakai.db` | Durable store path |
| `SQLITE_SEED_FREE_PRESETS` | `true` | Seed free provider templates |
| **Analytics** | | |
| `ANALYTICS_ENABLED` | `true` | Persistent request traces |
| `ANALYTICS_RETENTION_DAYS` | `7` | Trace retention |
| `ANALYTICS_ROLLUP_RETENTION_DAYS` | `90` | Rollup retention |
| `ANALYTICS_BATCH_SIZE` | `50` | Writer batch size |
| `ANALYTICS_FLUSH_INTERVAL` | `1.0` | Writer flush interval (seconds) |
| `ANALYTICS_WEBHOOK_URL` | — | Webhook for trace batches |
| `ANALYTICS_OTLP_ENDPOINT` | — | OpenTelemetry endpoint |

---

## API Endpoints

### OpenAI-Compatible

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/chat/completions` | Chat (stream + tools + parallel tool calls) |
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
| DELETE | `/admin/providers/{id}` | Yes | Remove/disable |
| POST | `/admin/providers/{id}/refresh` | Yes | Refresh one provider |
| POST | `/admin/providers/test` | Yes | Test provider without saving |
| GET | `/admin/providers/presets` | No | List free presets |
| GET | `/admin/models/set-enabled` | Yes | Enable/disable model in pool |
| GET | `/admin/models/bulk-enabled` | Yes | Bulk enable/disable |
| POST | `/admin/models/register` | Yes | Register custom model |
| GET | `/preferences` | Yes | List user preferences |
| POST | `/preferences` | Yes | Set/modify preference |
| DELETE | `/preferences/{intent}` | Yes | Clear intent preference |
| DELETE | `/preferences` | Yes | Clear all preferences |
| GET | `/dashboard` | No | Web dashboard |

### Analytics

| Method | Path | Auth Required | Description |
|--------|------|---------------|-------------|
| GET | `/analytics/summary` | Yes | Dashboard KPIs |
| GET | `/analytics/traces` | Yes | Request explorer list |
| GET | `/analytics/traces/{id}` | Yes | Trace detail + spans |
| GET | `/analytics/timeseries/{metric}` | Yes | Time-bucketed metrics |
| GET | `/analytics/breakdown/{dim}` | Yes | Dimension aggregates |
| GET | `/analytics/events` | Yes (`?token=`) | SSE live feed |
| GET | `/analytics/export/traces` | Yes | CSV / JSONL export |
| GET | `/analytics/cost/rates` | Yes | List all cost rates |
| PUT | `/analytics/cost/rates/{model_id}` | Yes | Set cost override |
| DELETE | `/analytics/cost/rates/{model_id}` | Yes | Delete cost override |
| POST | `/analytics/cost/rates/import` | Yes | Bulk import from models.dev |
| POST | `/analytics/retention/run` | Yes | Trigger cleanup |

---

## Agentic Coding Best Practices

### Configuration for Agent Tools

For the best experience with agentic coding tools (Cursor, OpenCode, Cline, Kiro), use these settings:

```bash
# Relax upstream timeout for long tool-call sequences
UPSTREAM_TIMEOUT=300

# Increase fallback depth for agentic coding (more models = more resilience)
MAX_MODEL_FALLBACKS=12
CODING_MAX_FALLBACKS=15

# Keep sticky sessions for multi-turn affinity
STICKY_SESSIONS_ENABLED=true
STICKY_SESSION_TTL_SECONDS=3600

# Enable adaptive routing for per-request optimization
ADAPTIVE_ROUTING=true

# Set an end-to-end deadline to prevent infinite retries
REQUEST_DEADLINE_SECONDS=180

# Safety jitter (off by default for agentic — reduces latency)
SAFETY_JITTER_ENABLED=false
```

### Session Stickiness for Multi-Turn Agentic Loops

Nimmakai supports session stickiness through multiple mechanisms:

| Mechanism | Header / Field | Client |
|-----------|---------------|--------|
| Explicit session ID | `X-Nimmakai-Session` or `session_id` (body) | Any |
| Chat ID | `X-Cursor-Chat-Id` | Cursor |
| OpenCode session | `X-OpenCode-Session` | OpenCode |
| Cline session | `X-Cline-Session` | Cline |
| Kiro session | `X-Kiro-Session` | Kiro |
| Codeium session | `X-Codeium-Session` | Codeium / Windsurf |
| Cascade session | `X-Cascade-Session` | Cascade |
| Implicit fingerprint | First system + first user message hash | Any (no header needed) |

When stickiness is active, the same model AND API key are reused across turns — maximizing provider-side KV cache hits for faster multi-turn responses.

---

## Troubleshooting

### 401 Unauthorized

```
{"error":{"message":"Invalid API key.","code":"invalid_api_key"}}
```

**Fix:** Set `PROXY_API_KEYS` in `.env` and use that key in your `Authorization` header. Or allow insecure auth:
```
ALLOW_INSECURE_AUTH=true
```

### Provider Returns "Upstream Request Failed"

```
Provider returned error: {"error":{"message":"Error from provider (Console): Upstream request failed",...}}
```

**Nimmakai now handles this automatically** — this 400 message is detected as `retryable_400` and the proxy falls back to the next model in the chain. Check logs for `retryable 400 detected`.

### No Models Appear on /v1/models

**Possible causes:**
- No API keys configured for any provider
- Provider's `/models` endpoint returned an error
- Refresh hasn't run yet (first refresh happens at startup)

**Fix:**
```bash
curl http://localhost:8080/admin/providers \
  -H "Authorization: Bearer sk-nimmakai-local-dev"

curl -X POST http://localhost:8080/admin/catalog/refresh \
  -H "Authorization: Bearer sk-nimmakai-local-dev"
```

### 429 Too Many Requests

Nimmakai automatically:
- Rotates to another key in the same provider's pool
- Applies exponential backoff (0.5s → 1s → 2s → 4s → 8s → 16s)
- Respects `Retry-After` headers from upstream
- Falls back to the next model in the chain when all keys are exhausted

If all keys and all models are exhausted, you get a 503:
```
{"error":{"code":"nimmakai_pool_exhausted"}}
```

### Stream Hangs or Drops Mid-Response

Check:
- `UPSTREAM_TIMEOUT` (default 300s)
- `STREAM_TTFT_TIMEOUT_SECONDS` (default 12s) — if first token doesn't arrive, falls back to next model
- `STREAM_IDLE_TIMEOUT_SECONDS` (default 180s) — if no chunk arrives in this window, stream ends cleanly
- Provider network reliability

### Context Overflow in Agentic Loop

If you see:
```
large request body: model=... size=XXXKB messages=N
```

The agentic conversation has grown large. Nimmakai:
1. Tracks cumulative tokens via `SessionContext`
2. Uses session context to estimate total context across turns
3. Filters models whose context is too small
4. Falls back to models with larger context windows

**To improve:**
- Add providers with larger context models (e.g., Gemini 2.5 Pro with 1M context, Qwen with 128K)
- Ensure sticky sessions are enabled so the session context is tracked
- Check the `X-Nimmakai-Context-Length` response header to see what context the model supports

### Empty Reply or No Tool Calls

The fallback executor detects:
- Empty `choices` array → soft-fail, try next model
- No `tool_calls` when tools were requested → soft-fail, try next model
- Clear "tool not supported" error → mark model as `supports_tools=False` in capability registry

**Nimmakai now pre-filters** models with `supports_tools=False` when tools are present, so the first attempt goes to a tool-capable model.

### Dashboard Shows "Not ready" or 503

```bash
curl http://localhost:8080/health
# Look for catalog_ok and providers fields
```

**Causes:**
- Provider hub failed to start (check logs)
- Registry not loaded (no models.yaml)
- No providers with keys configured

### How to Reset Everything to Defaults

```bash
# Remove runtime state
rm -rf .nimmakai/

# Reset providers to built-in NIM only
rm -f config/providers.yaml
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

## Security & Auth

### Auth Flow

1. **Dashboard users**: signup → email verify → **admin approve** → `sk-nk-…` API key
2. **Legacy break-glass**: `PROXY_API_KEYS` env var (always admin)
3. **Session cookie**: HTTP-only session cookie for dashboard
4. **`/v1/*` endpoints**: Bearer token (`sk-nk-…` or proxy key)
5. **Admin endpoints**: `require_admin()` — calls same auth check

### Security Features
- API keys stored as SHA-256 hashes only (no plaintext)
- All SQL parameterized (no injection)
- Admin auto-approval via `ADMIN_EMAILS` env list
- Rejected admin reactivation is blocked (verify link consumed, status check enforces)
- Session cookie `httponly` + `samesite=lax`
- Optional `SESSION_SECURE_COOKIE=true` behind HTTPS

---

## Performance & Production

### Concurrent Multi-Tenant Tuning

```bash
# Per-key concurrency
NIM_MAX_IN_FLIGHT_PER_KEY=6

# Global concurrency (0 = auto-sum from all providers)
GLOBAL_MAX_IN_FLIGHT=0

# Global gate timeout
GLOBAL_GATE_TIMEOUT=30.0

# Analytics batch size (larger = less I/O, more memory)
ANALYTICS_BATCH_SIZE=100
ANALYTICS_FLUSH_INTERVAL=2.0

# Egress proxy for corporate networking
NIM_EGRESS_PROXIES=http://proxy.company.com:8080
```

### Resource Management

- **Key pools**: Per-provider RPM/RPD windows, sliding-window counters
- **Concurrency gate**: Global in-flight limiter, auto-sized from all provider pools
- **CancelledError safety**: All resource acquisitions are `CancelledError`-safe — no permanent leaks
- **Backpressure**: Bounded stream queue (max 32 chunks) prevents OOM under slow clients
- **Cost cache**: 60s write-through cache avoids SQLite reads on every request
- **Learning persistence**: Epoch-counter safe — no lost updates under concurrent load
- **Catalog refresh**: Dict-mutation-safe iteration, partial failure retains existing models
- **Writer durability**: Re-enqueues on transient DB errors, no trace loss

### Monitoring

- `/health` endpoint for load balancers
- `/stats` for per-key, per-model, routing diagnostics
- `/analytics/events` SSE stream for real-time monitoring
- Request traces with span waterfall (classify → route → upstream → fallback)
- Cost estimation with dynamic pricing from models.dev

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
├── frontend/
│   └── src/                     # React dashboard
├── src/nimmakai/
│   ├── main.py                  # FastAPI app + lifespan
│   ├── config.py                # Settings (env → pydantic)
│   ├── auth.py                  # Client Bearer/key auth
│   ├── upstream.py              # httpx forwarder + backoff
│   ├── balancer.py              # KeyPool (RPM, RPD, EWMA, quarantine)
│   ├── accounts/                # Multi-tenant user/account SQLite store
│   ├── analytics/               # Trace persistence, cost, webhook, OTLP
│   ├── catalog/
│   │   ├── providers.py         # Provider config + store
│   │   ├── registry.py          # ModelRegistry — live catalog + refresh
│   │   ├── hub.py               # ProviderHub — multi-provider runtime
│   │   ├── ladder.py            # LadderService — intelligent scoring
│   │   ├── learning.py          # Online learning store (disk-backed)
│   │   ├── health.py            # Per-model error tracking + cooldown
│   │   ├── context.py           # Dynamic context window extraction
│   │   └── ...                  # Aliases, families, preferences, prober, docs, presets
│   ├── routes/
│   │   ├── openai.py            # /v1/* endpoints
│   │   ├── admin.py             # /admin/*, /stats, /ladder, /preferences
│   │   ├── accounts.py          # /auth/* signup/login/verify
│   │   └── analytics.py         # /analytics/* traces, cost rates
│   ├── routing/
│   │   ├── classifier.py        # IntentClassifier (rules + optional LLM + headers)
│   │   ├── fallback.py          # FallbackExecutor (model chains + backpressure)
│   │   ├── selector.py          # ModelSelector (model → route)
│   │   ├── intents.py           # Intent enum
│   │   ├── optimizer.py         # Continuous intelligence × speed optimizer
│   │   └── auto_router.py       # OpenRouter/Kilo auto-router surface
│   └── safety/
│       ├── guard.py              # Jitter + sticky + concurrency
│       ├── sticky.py             # Session-to-key/model affinity + context tracking
│       ├── concurrency.py        # Global concurrency gate
│       ├── backoff.py            # Exponential backoff
│       └── ...                  # Circuit breaker, budgets, jitter
├── tests/                       # 250+ tests
├── scripts/                     # Deployment helpers
├── .env.example                 # Example environment
├── Dockerfile
├── docker-compose.do.yml        # Droplet Compose
├── Procfile                     # Heroku
└── pyproject.toml               # Dependencies + project metadata
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

# Type check
uv run mypy src tests
```

### Testing

```bash
# Run all tests
uv run pytest -q

# Run specific module tests
uv run pytest tests/test_fallback.py -q
uv run pytest tests/test_classifier.py -q
uv run pytest tests/test_model_pool_toggle.py -q

# Lint changed files
uv run ruff check src/nimmakai/routing/ src/nimmakai/compat.py -q
```

### Architecture Decisions

- **Single-process asyncio** — no Redis/dependency requirement, one uvicorn worker for production
- **SQLite WAL with RLock** — thread-safe shared connection, check_same_thread=False
- **Write-behind caching** — cost overrides: 60s TTL, learning: epoch-counter safe
- **Pressure-reactive queues** — writer: bounded at 5000, stream: bounded at 32, event bus: bounded per subscriber
- **CancelledError discipline** — all resource acquisitions use `try/finally` with `BaseException` catching

---

## Deploy on DigitalOcean

Full guide: **[docs/digitalocean.md](docs/digitalocean.md)**

| Path | Cost | Persistence | Best for |
|------|------|-------------|----------|
| **Droplet + one-click userdata** | ~$6/mo | Durable SQLite volume | Analytics, dashboard, simplest |
| App Platform (Heroku-style) | ~$10/mo | Ephemeral disk | Push-to-main auto-deploy |

### One-click Droplet

```bash
chmod +x scripts/generate-do-userdata.sh
./scripts/generate-do-userdata.sh
# → writes ./nimmakai-droplet-userdata.sh (gitignored; contains secrets)
```

Then paste the script into DigitalOcean Droplet **User data** during creation.

---

## License

MIT

---

*Built for production agentic coding workloads — Cursor, OpenCode, Cline, Kiro, and beyond.*
