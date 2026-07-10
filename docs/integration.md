# Drop-in OpenAI-compatible integration

Nimmakai speaks the **OpenAI Chat Completions API**. Any app that can set a custom base URL + API key can use it — Cursor, OpenCode, Continue, Cline, LibreChat, Open WebUI, LangChain, LlamaIndex, the official OpenAI SDKs, etc.

## 60-second setup

1. Run Nimmakai locally (or on your server):

```bash
uv sync
cp .env.example .env   # set NIM_API_KEYS + PROXY_API_KEYS
uv run nimmakai
```

2. Point your client at:

| Setting | Value |
|--------|--------|
| **Base URL** | `http://HOST:8080/v1` |
| **API Key** | any key from `PROXY_API_KEYS` |
| **Model** | `nimmakai/auto` (recommended) or `gpt-4o` / real `org/model` |

That’s it. No vendor SDK required beyond OpenAI-compatible clients.

## Official OpenAI Python SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8080/v1",
    api_key="sk-nimmakai-local-dev",
)

r = client.chat.completions.create(
    model="nimmakai/auto",
    messages=[{"role": "user", "content": "ping"}],
    tools=[...],  # optional — coding ladder preferred automatically
)
```

## Node / TypeScript

```ts
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "http://localhost:8080/v1",
  apiKey: "sk-nimmakai-local-dev",
});

await client.chat.completions.create({
  model: "nimmakai/auto",
  messages: [{ role: "user", content: "ping" }],
});
```

## curl

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer sk-nimmakai-local-dev" \
  -H "Content-Type: application/json" \
  -d '{"model":"nimmakai/auto","messages":[{"role":"user","content":"hi"}]}'
```

## What the engine does for you

1. **Analyzes** the request (tools / agent prompts / length / vision) → intent  
2. **Scores** live NVIDIA NIM models for that intent (docs + size/tier + online learning)  
3. **Routes** to the strongest available model; **ladders** to the next on error/unavailable  
4. **Balances** across your `nvapi-` keys (RPM, sticky, quarantine)

Response headers for debugging: `X-Nimmakai-Model`, `X-Nimmakai-Intent`, `X-Nimmakai-Fallback-Index`.

Inspect ladders: `GET /ladder`  
Force passthrough: header `X-Nimmakai-Disable-Route: 1`

## Compatibility surface

| Endpoint | Supported |
|----------|-----------|
| `POST /v1/chat/completions` (stream + tools) | ✅ |
| `POST /v1/completions` | ✅ |
| `POST /v1/embeddings` | ✅ |
| `POST /v1/responses` | ✅ passthrough |
| `GET /v1/models` (+ synthetic `nimmakai/auto`) | ✅ |

## Production tips

- Put Nimmakai behind HTTPS (Caddy/nginx) and set strong `PROXY_API_KEYS`
- Keep `ROUTING_ENABLED=true`; use `nimmakai/auto` in clients
- Tune `PROBE_BUDGET_PER_HOUR` low on free tier
- Learning state lives in `.nimmakai/learning.json` (survives restarts)
