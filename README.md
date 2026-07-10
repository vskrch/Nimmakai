# Nimmakai

**OpenAI-compatible multi-key proxy for [NVIDIA NIM](https://build.nvidia.com/)** with intelligent model routing.

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
| Ordered model fallback (quality chains in `config/models.yaml`) | ✅ |
| Live catalog refresh (`GET /v1/models`) | ✅ |
| Sticky sessions, jitter, daily RPD, 401/403 quarantine | ✅ |
| Diagnostic headers (`X-Nimmakai-*`) | ✅ |
| `ROUTING_ENABLED=false` bootstrap passthrough | ✅ |

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

### 3. Run

```bash
uv run nimmakai
```

- Docs: http://localhost:8080/docs  
- Health: http://localhost:8080/health  
- Stats: http://localhost:8080/stats  
- Catalog: http://localhost:8080/catalog  

## Use with coding agents

| Setting | Value |
|--------|--------|
| **Base URL** | `http://localhost:8080/v1` |
| **API Key** | value from `PROXY_API_KEYS` |
| **Model** | `nimmakai/auto` (or `auto`, `gpt-4o`, or a real `org/model` id) |

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

1. **Classify** the request (tools / agent fingerprints → `coding_agentic`; short Q&A → `chat_fast`; images → `vision`; …). Rule path is CPU-only; optional `CLASSIFY_MODE=rules_then_llm` for low-confidence cases.
2. **Select** a quality-ordered chain from `config/models.yaml`, intersected with live `GET /v1/models`.
3. **Execute** with key rotation on 429; on model 404/5xx advance to the next chain entry (never mid-stream).
4. **Shape** traffic: soft RPD, jitter, sticky key bias, auth quarantine, Retry-After.

Disable routing entirely with `ROUTING_ENABLED=false`.

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
