# Nimmakai

**OpenAI-compatible multi-key proxy for [NVIDIA NIM](https://build.nvidia.com/).**

Point Cursor, OpenCode, Pi, Cline, Continue, or any OpenAI SDK client at Nimmakai. It fans out traffic across multiple NIM API keys, respects per-key rate limits (~40 RPM free tier), and balances on live response rates so agentic workloads avoid `429` storms.

```
  Cursor / OpenCode / agents
            │
            │  Base URL: http://localhost:8080/v1
            │  API key:  any key you set (PROXY_API_KEYS)
            ▼
       ┌─────────────┐
       │  Nimmakai   │  ← key shuffle + RPM window + latency EWMA
       └──────┬──────┘
              │
    ┌─────────┼─────────┬─────────┐
    ▼         ▼         ▼         ▼
  NIM key1  key2      key3      key4   →  integrate.api.nvidia.com
```

## Features (bootstrap)

| Feature | Status |
|--------|--------|
| OpenAI `/v1/chat/completions` (stream + tools) | ✅ |
| `/v1/models`, `/v1/embeddings`, `/v1/completions` | ✅ |
| `/v1/responses` passthrough (newer agent SDKs) | ✅ |
| Multi-key rotation + weighted shuffle | ✅ |
| Per-key RPM sliding window (default 40 × 0.9) | ✅ |
| Auto cooldown + retry on upstream `429` | ✅ |
| Response-rate / latency-aware balancing | ✅ |
| Client API key gate (`PROXY_API_KEYS`) | ✅ |
| `/health` + `/stats` observability | ✅ |

## Quick start

### 1. Install

Requires Python 3.11+ and [uv](https://github.com/astral-sh/uv) (recommended).

```bash
cd Nimmakai
uv sync
```

Or with pip:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env`:

```env
# Keys your IDE / agent will send (comma-separated). Empty = accept any key.
PROXY_API_KEYS=sk-nimmakai-local-dev

# Your NVIDIA NIM keys (one per account), comma-separated
NIM_API_KEYS=nvapi-xxxx,nvapi-yyyy,nvapi-zzzz,nvapi-wwww

NIM_RPM_LIMIT=40
NIM_RPM_SAFETY_FACTOR=0.9
HOST=0.0.0.0
PORT=8080
```

Get NIM keys at [build.nvidia.com](https://build.nvidia.com/) → account → API keys (phone verification required for free tier).

### 3. Run

```bash
uv run nimmakai
# or
uv run uvicorn nimmakai.main:app --host 0.0.0.0 --port 8080
```

- API docs: http://localhost:8080/docs  
- Health: http://localhost:8080/health  
- Pool stats: http://localhost:8080/stats  

## Use with coding agents

### Cursor / any OpenAI-compatible client

| Setting | Value |
|--------|--------|
| **Base URL** | `http://localhost:8080/v1` |
| **API Key** | `sk-nimmakai-local-dev` (or whatever you set in `PROXY_API_KEYS`) |
| **Model** | Any NIM model id, e.g. `meta/llama-3.1-70b-instruct`, `deepseek-ai/deepseek-r1` |

### OpenAI Python SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8080/v1",
    api_key="sk-nimmakai-local-dev",
)

r = client.chat.completions.create(
    model="meta/llama-3.1-70b-instruct",
    messages=[{"role": "user", "content": "Hello from Nimmakai"}],
)
print(r.choices[0].message.content)
```

### curl

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer sk-nimmakai-local-dev" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta/llama-3.1-70b-instruct",
    "messages": [{"role": "user", "content": "ping"}]
  }'
```

## How balancing works

1. **RPM window** — each key tracks request timestamps in a 60s sliding window. Effective limit = `NIM_RPM_LIMIT × NIM_RPM_SAFETY_FACTOR` (default **36**/min) so we leave headroom before NVIDIA’s hard 40.
2. **Selection** — among keys under the limit (and not cooling down), score by remaining headroom × inverse EWMA latency × success rate × concurrency penalty, then weighted-random pick from the top half (shuffle + prefer healthy keys).
3. **429 handling** — that key enters cooldown (`NIM_COOLDOWN_SECONDS`, default 60s); the request is retried on another key automatically.
4. **Streaming** — SSE chunks are proxied byte-for-byte so tool calls / agentic streams stay intact.

With **4 keys × ~36 RPM** you get on the order of **~144 RPM** aggregate capacity before the pool waits for window slots.

## Project layout

```
src/nimmakai/
  main.py          # FastAPI app + lifespan
  config.py        # pydantic-settings
  auth.py          # client Bearer validation
  balancer.py      # KeyPool (RPM + EWMA + cooldown)
  upstream.py      # httpx forwarder with retry
  routes/
    openai.py      # /v1/* OpenAI surface
    admin.py       # /health, /stats
tests/
  test_balancer.py
```

## Development

```bash
uv sync --all-extras
uv run pytest
uv run ruff check src tests
```

## Roadmap (next)

- [ ] Model-level routing / fallback aliases (map `gpt-4o` → NIM model)
- [ ] Persistent metrics (Prometheus)
- [ ] Optional Redis shared state for multi-instance deploys
- [ ] Request queue with fair scheduling per client
- [ ] Admin UI for live key health

## License

MIT
