# Nimmakai

**OpenAI-compatible multi-provider gateway** (self-hosted OpenRouter-style) for [NVIDIA NIM](https://build.nvidia.com/) plus any third-party OpenAI-compatible API (Groq, Gemini OpenAI endpoint, Cerebras, etc.) with intelligent model routing.

Point Cursor, OpenCode, Pi, Cline, Continue, or any OpenAI SDK client at Nimmakai. It fans out traffic across multiple NIM API keys, picks models by intent (`auto` / Cursor aliases), falls back along quality-ordered chains, and shapes traffic for sustainable personal multi-key use.

```
  Cursor / OpenCode / agents
            │
            │  Base URL: http://localhost:8080/v1
            │  API key:  PROXY_API_KEYS
            │  Model:    nimmakai/auto  (or gpt-4o / org/model)
            ▼
       ┌──────────────────────────────────┐
       │  Nimmakai                        │
       │  intent → chain → key pool       │
       │  RPM + RPD + sticky + quarantine │
       └──────────────┬───────────────────┘
                      │
            integrate.api.nvidia.com/v1
```

## Features

| Feature | Status |
|--------|--------|
| OpenAI `/v1/chat/completions` (stream + tools) | ✅ |
| `/v1/models`, `/v1/embeddings`, `/v1/completions`, `/v1/responses` | ✅ |
| Multi-key RPM window + latency EWMA + 429 cooldown | ✅ |
| Intent-aware routing (`auto`, aliases like `gpt-4o`) | ✅ |
| Multi-provider hub (NIM + any OpenAI-compatible `base_url`) | ✅ |
| Admin API to register providers (`/admin/providers`) | ✅ |
| Strength ladder + ordered fallback (power-first, cross-provider) | ✅ |
| Live catalog refresh + NVIDIA docs + budgeted probes | ✅ |
| Sticky sessions (opt-in headers), jitter, RPD, quarantine | ✅ |
| Diagnostic headers (`X-Nimmakai-*`) | ✅ |
| `ROUTING_ENABLED=false` bootstrap passthrough | ✅ |

## Honest scope

Nimmakai is a solid personal NIM router — not a closed-loop “best engine in the world.” Scoring is heuristic (family, size, docs, online learning); the classifier is mostly rules; probes are budgeted. Expect to tune `config/models.yaml` and watch `/ladder` + learning as NVIDIA’s catalog changes.

## Quick start

### 1. Install

Requires Python 3.11+ and [uv](https://github.com/astral-sh/uv) (recommended).

```bash
cd Nimmakai
uv sync
```

### 2. Configure

```bash
cp .env.example .env
```

Set `NIM_API_KEYS` to your NVIDIA keys from [build.nvidia.com](https://build.nvidia.com/).

Set `PROXY_API_KEYS` to the Bearer token(s) your clients will send. Empty `PROXY_API_KEYS` is rejected unless `ALLOW_INSECURE_AUTH=true` (local/dev only).

### 3. Run

```bash
uv run nimmakai
```

- Docs: http://localhost:8080/docs  
- Health: http://localhost:8080/health  
- Stats / catalog / ladder: auth required (`Authorization: Bearer <PROXY_API_KEYS>`)  

## Add a third-party provider (OpenRouter-style)

Any OpenAI-compatible API works (Groq, OpenRouter, Gemini OpenAI mode, Cerebras, local vLLM, …):

```bash
curl -s http://localhost:8080/admin/providers \
  -H "Authorization: Bearer sk-nimmakai-local-dev" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "groq",
    "name": "Groq",
    "base_url": "https://api.groq.com/openai/v1",
    "api_keys": ["gsk-your-key"],
    "rpm_limit": 30,
    "enabled": true
  }'
```

Models appear on `GET /v1/models` as `groq/<model-id>`. Use `nimmakai/auto` to let the ladder pick across **all** providers, or pin `groq/llama-3.3-70b-versatile`.

Or edit `config/providers.yaml` / set `GROQ_API_KEYS` with `api_keys_env: GROQ_API_KEYS`.

## Use with any OpenAI-compatible app

See **[docs/integration.md](docs/integration.md)** — drop-in `base_url` + `api_key` for Cursor, OpenCode, Continue, SDKs, curl, etc.

Recommended model id: `nimmakai/auto`.


Routing headers on responses:

- `X-Nimmakai-Model` — upstream model used  
- `X-Nimmakai-Intent` — e.g. `coding_agentic`, `chat_fast`  
- `X-Nimmakai-Key-Id` — which pool key served the request  
- `X-Nimmakai-Route-Mode` — `auto` / `alias` / `passthrough` / …  
- `X-Nimmakai-Fallback-Index` — `0` unless a later chain model was used  

Optional request headers:

- `X-Nimmakai-Session` — sticky session id  
- `X-Nimmakai-Disable-Route: 1` — force passthrough of `model`  
- `X-Nimmakai-Intent: reasoning` — force intent  

### OpenAI Python SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8080/v1",
    api_key="sk-nimmakai-local-dev",
)

r = client.chat.completions.create(
    model="nimmakai/auto",
    messages=[{"role": "user", "content": "Hello from Nimmakai"}],
)
print(r.choices[0].message.content)
```

## How routing works

1. **Classify** the request (tools / agent → `coding_agentic`; short Q&A → `chat_fast`; …).
2. **`LadderService` (automatic)** refreshes from NVIDIA docs + live `/v1/models`, scores every available model for that task (family affinity, size/tier, doc keywords), and builds a **strength ladder**.
3. **Always try the strongest available model first**; on unavailable/error, walk to the next strongest on the ladder (not a flaky one-step hop).
4. **Gentle probes** + disk snapshot keep this resilient without clogging RPM.

Inspect live ladders: `GET /ladder` (auth required).

Disable routing with `ROUTING_ENABLED=false`.

## Account safety & responsibility

Multi-account free-tier aggregation may violate NVIDIA’s terms. Nimmakai implements **legitimate traffic shaping** (budgets, jitter, sticky sessions, quarantine) — not ban-evasion tooling (no residential proxy farms, CAPTCHA solving, or identity automation).

For production capacity, prefer NVIDIA AI Enterprise or self-hosted NIM. Optional `NIM_EGRESS_PROXIES` is for corporate egress only; you are responsible for lawful use.

## Project layout

```
config/models.yaml          # aliases + intent chains
src/nimmakai/
  catalog/                  # YAML registry + live refresh + health
  routing/                  # classifier, selector, fallback
  safety/                   # jitter, sticky, concurrency, guard
  balancer.py               # KeyPool (RPM + RPD + quarantine)
  upstream.py               # httpx forwarder
  routes/openai.py          # /v1/*
  routes/admin.py           # /health, /stats, /catalog
docs/design-intelligent-router.md
```

## Development

```bash
uv sync --all-extras
uv run pytest
uv run ruff check src tests
```

## License

MIT
