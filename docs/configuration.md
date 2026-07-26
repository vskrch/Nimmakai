# Configuration reference

Every Potato setting is an environment variable (loaded from `.env` via `pydantic-settings`).
Source of truth: `src/potato/config.py`. This doc lists every knob, its default, and what it
actually does — `.env.example` is the template, this is the reference.

> **Load order:** environment variables override `.env`, which overrides code defaults.
> Lists are **comma-separated** (`PROXY_API_KEYS=sk-a,sk-b`). Empty values unset the list.

---

## Quick start (minimum viable `.env`)

```bash
PROXY_API_KEYS=sk-potato-<random>          # clients use this as Bearer
NIM_API_KEYS=nvapi-<your-key>               # or any provider key
ALLOW_INSECURE_AUTH=false
ROUTING_ENABLED=true
```

Everything else has working defaults. Tune the sections below only when something breaks.

---

## Client-facing auth

| Key | Default | Purpose |
|-----|---------|---------|
| `PROXY_API_KEYS` | _(empty)_ | Comma-separated Bearer tokens clients send in `Authorization: Bearer <key>`. **Required** unless `ALLOW_INSECURE_AUTH=true`. Generate with `openssl rand -hex 24`. |
| `ALLOW_INSECURE_AUTH` | `false` | If `true`, accept **any** Bearer (or none). **Local dev only.** Never set true in production. |
| `CORS_ALLOW_ORIGINS` | `*` | Comma-separated CORS origins. Use explicit origins (not `*`) when `allow_credentials` is needed (cookies / dashboard sessions). |

See [Auth model](#auth-model) below for how proxy keys, user keys, and sessions interact.

---

## Upstream providers

### Built-in NIM (`nim` provider)

| Key | Default | Purpose |
|-----|---------|---------|
| `NIM_BASE_URL` | `https://integrate.api.nvidia.com/v1` | NVIDIA NIM OpenAI-compatible endpoint. |
| `NIM_API_KEYS` | _(empty)_ | Comma-separated `nvapi-` keys, one per account. Each gets its own RPM window + cooldown + quarantine. |
| `NIM_RPM_LIMIT` | `40` | Per-key requests-per-minute ceiling (NIM free tier ≈ 40). |
| `NIM_RPM_SAFETY_FACTOR` | `0.9` | Schedule against `RPM × factor` (0.0–1.0) to avoid edge 429s. |
| `NIM_COOLDOWN_SECONDS` | `60` | Seconds a key sits out after a 429 before it is eligible again. |
| `NIM_RPD_LIMIT` | `2000` | Soft daily-request budget per key (UTC calendar day). Keys over budget are skipped, not failed. |
| `NIM_MAX_IN_FLIGHT_PER_KEY` | `3` | Max concurrent in-flight requests per key. |
| `GLOBAL_MAX_IN_FLIGHT` | `0` | Global in-flight cap. `0` = auto (`keys × per-key`). |

### Multi-provider (OpenRouter-style)

Additional providers register from `config/providers.yaml`, the SQLite store (dashboard-added),
or env vars. Every enabled provider with keys merges into one model pool.

| Key | Default | Purpose |
|-----|---------|---------|
| `PROVIDERS_CONFIG_PATH` | `config/providers.yaml` | Built-in provider templates. |
| `PROVIDERS_OVERLAY_PATH` | `.potato/providers.json` | Runtime-added providers (survives restart). On Docker: `/data/providers.json`. |
| `SQLITE_PATH` | `.potato/potato.db` | Durable store for providers, preferences, ladders, analytics. On Docker: `/data/potato.db`. |
| `SQLITE_SEED_FREE_PRESETS` | `true` | On first boot, seed free-provider templates (Groq, Cerebras, OpenRouter, …) without keys. |

Provider key env vars (comma-separated, each registers the provider if non-empty):

```
OPENCODE_ZEN_API_KEYS, OPENCODE_API_KEYS, OPENCODE_GO_API_KEYS,
GROQ_API_KEYS, CEREBRAS_API_KEYS, OPENROUTER_API_KEYS, GEMINI_API_KEYS,
TOGETHER_API_KEYS, FIREWORKS_API_KEYS, SAMBANOVA_API_KEYS, DEEPSEEK_API_KEYS,
DEEPINFRA_API_KEYS, GITHUB_MODELS_API_KEYS, MISTRAL_API_KEYS,
HYPERBOLIC_API_KEYS, OLLAMA_CLOUD_API_KEYS
```

Add custom OpenAI-compatible endpoints via the dashboard (**Providers → Custom Endpoint**) or
`POST /admin/providers`.

---

## Intelligent routing

| Key | Default | Purpose |
|-----|---------|---------|
| `ROUTING_ENABLED` | `true` | `false` = bootstrap passthrough-only (honor client `model` verbatim, no auto/alias/fallback). Instant rollback switch. |
| `MODELS_CONFIG_PATH` | `config/models.yaml` | Versioned catalog: aliases, intent chains, scoring weights. |
| `CLASSIFY_MODE` | `rules_only` | `rules_only` (deterministic, <5ms) \| `rules_then_llm` (LLM reclassify on low confidence — burns RPM). |
| `ENABLE_FALLBACK_ON_EXPLICIT` | `true` | Fall back to next chain model when an explicit `org/model` fails. `false` = strict passthrough. |
| `MAX_MODEL_FALLBACKS` | `10` | Universal cap on ordered model attempts per request. Per-intent overrides in `models.yaml`. |
| `INJECT_AUTO_MODEL` | `true` | Add synthetic `potato/auto` (and `kilo/auto`, `openrouter/auto`) to `GET /v1/models` for picker UIs. |
| `LONG_CONTEXT_CHARS` | `48000` | Concatenated message length that flips intent to `long_horizon`. |
| `SHORT_CHAT_CHARS` | `800` | Short single-turn length that flips intent to `chat_fast`. |
| `LLM_CLASSIFY_THRESHOLD` | `0.55` | Confidence below which `rules_then_llm` calls the LLM classifier. |
| `LLM_CLASSIFY_CACHE_TTL` | `600` | Seconds to cache LLM classify results. |
| `LLM_CLASSIFY_CACHE_SIZE` | `256` | LRU size for LLM classify cache. |
| `STRICT_CATALOG` | `false` | `true` = fail startup if YAML models are missing from the live catalog. `false` = skip missing with a warning. |

### Catalog refresh + probes

| Key | Default | Purpose |
|-----|---------|---------|
| `CATALOG_REFRESH_SECONDS` | `300` | Interval to re-fetch `GET /v1/models` from each provider. |
| `CATALOG_DOCS_URL` | `https://build.nvidia.com/models.md` | NVIDIA docs catalog (used for capability enrichment). |
| `CATALOG_FETCH_DOCS` | `true` | Pull the docs catalog during refresh (slower; off = `/v1/models` only). |
| `CATALOG_RUN_PROBES` | `true` | Run lightweight capability probes (tools/vision) against unknown models. |
| `PROBE_EVERY_N_REFRESHES` | `6` | Run probes every Nth refresh (probes cost RPM). |
| `PROBE_BUDGET_PER_HOUR` | `8` | Max probe requests per hour (avoids clogging free-tier RPM). |
| `CATALOG_SNAPSHOT_PATH` | `.potato/catalog_snapshot.json` | Cached snapshot for fast cold-start. On Docker: `/data/catalog_snapshot.json`. |

### Adaptive timeouts (504 cascade fix — NMK-C104)

**Invariants** (violating these guarantees a 504 cascade):
- `UPSTREAM_TIMEOUT >= PER_ATTEMPT_BUDGET_SECONDS`
- `REQUEST_DEADLINE_SECONDS >= UPSTREAM_TIMEOUT`

| Key | Default | Purpose |
|-----|---------|---------|
| `UPSTREAM_TIMEOUT` | `120` | Total timeout for one upstream request. |
| `REQUEST_DEADLINE_SECONDS` | `120` | Total request deadline across all fallback attempts. |
| `PER_ATTEMPT_BUDGET_SECONDS` | `30` | Budget per single model attempt before advancing. |
| `STREAM_TTFT_TIMEOUT_SECONDS` | `15` | Fail-fast to next model if first token not received in this window. |
| `STREAM_IDLE_TIMEOUT_SECONDS` | `60` | Long idle once streaming has started (Cursor/agent safe). |
| `GATEWAY_TIMEOUT_COOLDOWN_SECONDS` | `30` | Cooldown for a model after a 504. |
| `ADAPTIVE_ROUTING` | `true` | Prefer currently responding models at request time. |
| `SELF_HEAL_SECONDS` | `120` | Reconcile providers + heal stale models every N seconds. |

### Fallback + health tuning (NMK-C101 / NMK-C102)

| Key | Default | Purpose |
|-----|---------|---------|
| `FALLBACK_ON_POOL_EXHAUST` | `true` | Advance to next model when all keys for current model are cooling down. |
| `MIN_QUALITY_RATIO` | `0.6` | Drop models below this × top model quality score. |
| `ERROR_RATE_THRESHOLD` | `0.45` | Error rate over the health window that triggers model cooldown. |
| `MODEL_COOLDOWN_SECONDS` | `45` | Soft cooldown after sustained errors. |
| `HARD_FAIL_COOLDOWN_SECONDS` | `5` | Short cooldown after a hard failure (4xx non-retryable). |
| `MAX_COOLDOWN_SECONDS` | `180` | Cap on any cooldown. |
| `RATE_LIMIT_COOLDOWN_SECONDS` | `15` | Cooldown after 429 (overridden by `Retry-After` if present). |
| `HEALTH_WINDOW_SIZE` | `8` | Sliding window for error-rate calculation. |
| `RECENT_SUCCESS_WINDOW_SECONDS` | `30` | Window for "recently successful" promotion. |

### Ladder / scoring tuning (NMK-C101)

| Key | Default | Purpose |
|-----|---------|---------|
| `UCB_EXPLORATION_C` | `5.0` | LinUCB exploration constant. |
| `DIVERSITY_STREAK_MAX` | `2` | Max consecutive picks from one model before forcing diversity. |
| `DEFAULT_AFFINITY` | `0.85` | Default intent affinity for unknown models. |
| `THOMPSON_SCALE` | `16.0` | Thompson sampling scale. |
| `THOMPSON_BLEND_N` | `12` | Samples before blending Thompson into LinUCB. |

### Dynamic Intelligence Scoring (NMK-I / NMK-S / NMK-G8)

| Key | Default | Purpose |
|-----|---------|---------|
| `ARTIFICIAL_ANALYSIS_API_KEY` | _(empty)_ | Enable [ArtificialAnalysis](https://artificialanalysis.ai) benchmark data source. |
| `INTEL_FETCH_TTL_HOURS` | `6` | Cache TTL for intelligence data. |
| `INTEL_CACHE_PATH` | `.potato/intel_cache.json` | Intelligence cache file. |
| `SCORE_RECOMPUTE_INTERVAL_SECONDS` | `300` | How often to recompute quality scores. |

The scoring weights themselves live in `config/models.yaml` (`scoring:` section) —
edit YAML, not env. See [Catalog files](#catalog-files) below.

---

## Account safety

| Key | Default | Purpose |
|-----|---------|---------|
| `SAFETY_JITTER_ENABLED` | `false` | Pre-request jitter (off by default for Cursor "no delay"). |
| `SAFETY_JITTER_MS_MIN` | `0.0` | Min jitter ms. |
| `SAFETY_JITTER_MS_MAX` | `0.0` | Max jitter ms. |
| `AUTH_FAIL_THRESHOLD` | `2` | 401/403 count before a key is quarantined. |
| `AUTH_QUARANTINE_SECONDS` | `3600` | Quarantine duration after `AUTH_FAIL_THRESHOLD` auth failures. |
| `STICKY_SESSIONS_ENABLED` | `true` | Bias (not pin) a session to one key for cache locality. |
| `STICKY_SESSION_TTL_SECONDS` | `1800` | Sticky map TTL. |
| `STICKY_BOOST` | `3.0` | Weight multiplier for the preferred sticky key. |
| `NIM_EGRESS_PROXIES` | _(empty)_ | Corporate egress proxy URLs. **Not for ban evasion — user responsibility.** |
| `HTTPS_PROXY` / `HTTP_PROXY` | _(empty)_ | Fallback egress proxy. |
| `UPSTREAM_USER_AGENT` | `potato/<version> (…)` | Upstream UA. Don't spoof browser UAs. |

---

## Server + logging

| Key | Default | Purpose |
|-----|---------|---------|
| `HOST` | `0.0.0.0` | Bind host. |
| `PORT` | `8080` | Bind port. DigitalOcean App Platform injects this — don't hardcode. |
| `LOG_LEVEL` | `info` | `debug` \| `info` \| `warning` \| `error`. |
| `UPSTREAM_TIMEOUT` | `120` | Per-request upstream timeout (seconds). |
| `DEFAULT_MODEL` | _(empty)_ | Fallback model if client omits one. Prefer `potato/auto` over this. |
| `REQUEST_LOG_SIZE` | `20000` | In-memory ring for Live Feed (`/admin/logs`). |
| `REQUEST_FILE_LOGGING` | `true` | Rotating file logs next to SQLite. |
| `REQUEST_LOG_MAX_BYTES` | `52428800` | 50 MiB per rotated file. |
| `REQUEST_LOG_RETENTION_DAYS` | `90` | ~3 months on disk. |
| `RETRY_BACKOFF_BASE_SECONDS` | `0.2` | Exponential backoff base for 429 / 5xx. |
| `RETRY_BACKOFF_CAP_SECONDS` | `2.0` | Backoff cap. |

---

## Universal system prompt

| Key | Default | Purpose |
|-----|---------|---------|
| `DEFAULT_SYSTEM_PROMPT` | _(see below)_ | Prepended to (or merged into) every chat request. Enforces response language = user's language and blocks hallucinated CJK output from non-Chinese open models. Set empty (`DEFAULT_SYSTEM_PROMPT=`) to disable injection entirely. |

The default prompt enforces:
- Respond in the user's language (default English if unclear).
- Never emit CJK characters unless explicitly requested.
- Be accurate and grounded (no fabrication).
- Prefer tool calls over guessing when factual data is needed.
- Preserve code, identifiers, and file paths verbatim.

---

## Analytics (persistent traces + dashboard)

| Key | Default | Purpose |
|-----|---------|---------|
| `ANALYTICS_ENABLED` | `true` | Persistent trace writer + retention manager. Live SSE feed still works when `false`. |
| `ANALYTICS_RETENTION_DAYS` | `7` | Trace row retention. |
| `ANALYTICS_ROLLUP_RETENTION_DAYS` | `90` | Rollup (timeseries) retention. |
| `ANALYTICS_BATCH_SIZE` | `50` | Trace flush batch size. |
| `ANALYTICS_FLUSH_INTERVAL` | `1.0` | Flush interval seconds. |
| `ANALYTICS_WEBHOOK_URL` | _(empty)_ | POST each flushed batch as JSON to this URL. |
| `ANALYTICS_OTLP_ENDPOINT` | _(empty)_ | OTLP HTTP trace exporter (e.g. `http://localhost:4318/v1/traces`). |

Optional OTLP deps: `pip install potato[otel]`.

---

## Multi-tenant accounts

| Key | Default | Purpose |
|-----|---------|---------|
| `ADMIN_EMAILS` | _(empty)_ | Comma-separated emails that get `role=admin` after email verify. |
| `EMAIL_BACKEND` | `stub` | `stub` (logs/returns verify URL) \| `smtp` (implemented, **not wired in routes yet** — see `docs/email-smtp.md`). |
| `PUBLIC_BASE_URL` | _(empty)_ | e.g. `https://app.example.com` for verify links. |
| `SESSION_COOKIE_NAME` | `nk_session` | Dashboard session cookie name. |
| `SESSION_SECURE_COOKIE` | `false` | `true` behind HTTPS in production. |

### SMTP (when `EMAIL_BACKEND=smtp` and routes are wired)

| Key | Default | Purpose |
|-----|---------|---------|
| `SMTP_HOST` | _(empty)_ | e.g. `smtp.gmail.com`. |
| `SMTP_PORT` | `587` | 587 = STARTTLS, 465 = implicit SSL. |
| `SMTP_USERNAME` | _(empty)_ | API user or app password. |
| `SMTP_PASSWORD` | _(empty)_ | **Never commit.** |
| `SMTP_FROM` | _(empty)_ | Verified sender address. |
| `SMTP_FROM_NAME` | `Potato` | Display name. |
| `SMTP_USE_TLS` | `true` | STARTTLS (port 587). |
| `SMTP_USE_SSL` | `false` | Implicit SSL (port 465). |
| `SMTP_TIMEOUT` | `30` | Send timeout seconds. |

See `docs/email-smtp.md` for provider examples (Gmail, SES, Mailgun, SendGrid, Postmark).

---

## Auth model

Potato has three auth paths, resolved in this order (`src/potato/auth.py:resolve_auth`):

1. **Explicit Bearer / `x-api-key`** — overrides cookie (break-glass).
   - `sk-nk-…` → user API key (issued after admin approval). Resolves to a `user_id`.
   - Any other Bearer → legacy `PROXY_API_KEYS` (break-glass admin).
2. **Session cookie** (`nk_session`) — dashboard login. Resolves to a `user_id`.
3. **`ALLOW_INSECURE_AUTH=true`** — accept anything (local dev only).

### Roles

| Role | How granted | Powers |
|------|-------------|--------|
| `anonymous` | no credentials | rejected |
| `user` | signup → verify → admin approve → issued `sk-nk-` key | own analytics only |
| `admin` | `ADMIN_EMAILS` match after verify, **or** legacy `PROXY_API_KEYS` | all users + gateway + admin APIs |
| `legacy_admin` | `PROXY_API_KEYS` Bearer | same as admin (break-glass) |

### Account statuses

`unverified` → `pending_approval` → `active` | `rejected` | `suspended`

API keys are issued **only on approval**. Suspended/pending/rejected accounts get
HTTP 403 `account_not_active` on both proxy and dashboard APIs. Legacy `PROXY_API_KEYS`
callers have no account status and are unaffected.

---

## Catalog files

### `config/models.yaml`

Versioned model catalog: aliases, intent chains, scoring weights. **Configuration, not code.**
The runtime intersects chains with live `GET /v1/models` — unavailable ids are skipped, not hard-failed.

Key sections:

- `defaults.auto_mode_model_tokens` — strings treated as "auto" (`auto`, `potato/auto`,
  `openrouter/auto`, `kilo/auto`, `kilo-auto/*`, `""`).
- `families` — soft family policy (`chat_primary`, `coding_primary`, `fallbacks`). Concrete ids
  resolved at runtime against live catalog.
- `aliases` — client conveniences (`gpt-4o → chain:coding_agentic`, `o3 → chain:reasoning`).
- `intents` — six intents (`coding_agentic`, `chat_fast`, `reasoning`, `long_horizon`,
  `vision`, `embeddings`), each with a `primary_family` and dynamic `chain: []`.
- `scoring` — quality signal weights, intent optimizer weights, per-intent fallback counts +
  attempt budgets, provider speed priors, capability affinity deltas, quality-floor keywords.
- `models: {}` — optional per-model metadata overlays (left empty by default; populated at runtime).

Edit this file to retune routing without touching code. The scoring section is the main lever
for "why does intent X pick model Y" questions.

### `config/providers.yaml`

OpenAI-compatible provider templates. Built-in `nim` is always synced from `NIM_*` env.
Free-provider examples (Groq, Cerebras, OpenRouter) are commented out — uncomment + set env,
or use the dashboard.

Each provider:
```yaml
- id: groq
  name: Groq
  base_url: https://api.groq.com/openai/v1
  api_keys_env: GROQ_API_KEYS    # env var to read keys from
  enabled: true
  rpm_limit: 30
  rpd_limit: 14400
```

---

## Virtual routing models

Pass any of these as `model` in API requests. The router expands them to an intent-aligned chain.

| Model ID | Behavior | Target intent |
|:---|:---|:---|
| `potato/auto` | Default intelligent router | Dynamic auto-selection |
| `potato/auto-coding` | Force the coding/agentic ladder | `coding_agentic` |
| `potato/auto-fast` | Latency-first (low TTFT, high TPS) | `chat_fast` |
| `potato/auto-cheap` | Cost-optimized (lightweight high-efficiency models) | efficient tier |
| `potato/best` | Frontier reasoning & complex math | `reasoning` / `long_horizon` |
| `potato/coding` | Code generation, agentic tools, multi-turn refactoring | `coding_agentic` |
| `auto` | Alias of `potato/auto` | Dynamic |
| `openrouter/auto` | OpenRouter-style parity | Dynamic |
| `kilo/auto`, `kilo-auto/*` | Kilo-style parity (e.g. `kilo-auto/frontier`, `kilo-auto/balanced`, `kilo-auto/efficient`, `kilo-auto/free`) | Tiered |
| `gpt-4o`, `gpt-4.1`, `gpt-4`, `claude-*`, `o1`/`o3`/`o4-mini`, `cursor-small` | Alias → chain | Per-alias |
| `org/model` (e.g. `nim/qwen/qwen3.5-397b-a17b`) | Explicit passthrough | Client's choice |

If a client sends an explicit real `org/model` id, Potato **respects it** (with optional
fallback to same-intent peers if `ENABLE_FALLBACK_ON_EXPLICIT=true`).

### Force / debug headers

| Header | Effect |
|--------|--------|
| `X-Potato-Disable-Route: 1` | Force passthrough of the client's `model` field; no auto/alias resolution. |
| `X-Potato-Intent: reasoning` | Force intent (power users). One of the six intent ids. |
| `X-Potato-Session: <id>` | Sticky session id for key affinity. |

### Response headers (debugging)

Every routed response includes:

| Header | Example | Meaning |
|--------|---------|---------|
| `X-Potato-Model` | `minimaxai/minimax-m2.7` | Actual upstream model used. |
| `X-Potato-Intent` | `coding_agentic` | Resolved intent. |
| `X-Potato-Route-Mode` | `auto` \| `alias` \| `passthrough` \| `passthrough_with_fallback` \| `unknown_alias_as_auto` | How the model was chosen. |
| `X-Potato-Fallback-Index` | `0` (or `1`+ if a later chain model was used) | Position in the chain. |
| `X-Potato-Rule-Id` | `tools_present` | Classifier rule that fired. |
| `X-Potato-Key-Id` | `key-2` | Opaque key id (never the key value). |
| `X-Potato-Provider` | `nim` | Provider that served the request. |
| `X-Potato-Requested-Model` | `potato/auto` | What the client asked for. |
| `X-Potato-Context-Length` | `1234` | Token-approx context length. |
| `X-Potato-Auto-Tier` | `balanced` | Auto-router tier (`balanced` / `fast` / `coding` / `efficient` / `free`). |
| `X-Potato-Sticky-Model` | `minimaxai/minimax-m2.7` | Model the sticky cache pinned. |
| `X-Request-Id` | `req_…` | Per-request id (echoed from `x-request-id` if sent). |

The JSON `model` field in the response is rewritten to the **actual** upstream model
(not the request model) — this differs from some proxies.

---

## Health, readiness, stats

Three endpoints, three purposes:

| Endpoint | Auth | Purpose |
|----------|------|---------|
| `GET /health` | none | Liveness — is the process up? Returns `status: ok` even when no keys are configured (`degraded`). Use for Docker/App Platform liveness probes. |
| `GET /ready` | none | Readiness — can it serve real traffic? Requires proxy auth + ≥1 active provider + ≥1 live model + catalog_ok. Returns 503 with `readiness_failures` if not. Use for Kubernetes/DigitalOcean readiness probes and deploy.sh verification. |
| `GET /stats` | proxy auth | Per-key RPM/latency/cooldown snapshot + routing counts + catalog summary. Operational, not health. |

`/health` payload:
```json
{
  "status": "ok" | "degraded",
  "version": "0.5.0",
  "keys_configured": 4,
  "keys_available": 3,
  "active_providers": 1,
  "live_models": 96,
  "catalog_ok": true,
  "proxy_auth_configured": true,
  "providers": [{"id": "nim", "enabled": true, "key_count": 4, "runtime": true}],
  "routing_enabled": true,
  "dashboard": "/dashboard"
}
```

`/ready` adds:
```json
{
  "ready": false,
  "readiness_failures": ["no_live_models", "catalog_unavailable"]
}
```