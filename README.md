# Nimmakai (🍋) — Production-Grade LLM API Gateway & Intelligent Auto-Router

[![Build Status](https://img.shields.io/badge/build-passing-brightgreen)](https://github.com/vskrch/Nimmakai)
[![Python Version](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://python.org)
[![React Dashboard](https://img.shields.io/badge/ui-React%2018%20%2B%20Tailwind-purple)](file:///Users/venkatasai/CascadeProjects/Nimmakai/frontend)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Nimmakai** is a high-availability, low-latency LLM API Gateway and dynamic auto-router designed for production AI engineering and agentic workflows. It aggregates multi-provider inference pools (OpenCode, Ollama Cloud, NVIDIA NIM, Groq, OpenRouter, Cerebras, Together, Fireworks, SambaNova, DeepSeek, and custom endpoints) into a single, bulletproof API endpoint compatible with **OpenAI** and **Anthropic Messages** protocols.

---

## 🚀 Key Features

* **⚡ Intelligent Pre-Routing (`nimmakai/auto`)**: Automatically analyzes incoming prompts and routes coding, reasoning, long-context, and vision tasks to top-ranked models dynamically—reserving expensive frontier models for hard tasks while keeping simple queries fast and cheap.
* **🛡️ Zero-Downtime Fallback & Circuit Breakers**: Automatic model fallbacks across providers. If a model encounters a 504 gateway timeout, rate limit (429), or 5xx server error, Nimmakai immediately advances down the intelligence ladder without failing client requests.
* **🤖 Native Developer Tooling Integrations**: Full drop-in compatibility for **Cursor IDE**, **Claude Code CLI**, **Cline**, and **OpenCode CLI**.
* **📊 SaaS Telemetry & Admin Dashboard**: Sleek React + Tailwind glassmorphic dashboard featuring live SSE feed traces, latency waterfalls, token expenditure analytics, provider health controls, and an interactive prompt workbench.
* **🔌 Dynamic Benchmark Intelligence Scoring**: Periodically aggregates model capabilities and benchmark scores from OpenRouter and evaluation leaderboards to recompute optimal routing ladders in memory.

---

## 🛠️ Tool Integration Guides

### 1. Cursor IDE Setup (Agentic Custom OpenAI Base URL)

Cursor can use Nimmakai as its custom OpenAI-compatible language model backend:

1. Open **Cursor** $\rightarrow$ **Settings** $\rightarrow$ **Models**.
2. Under **OpenAI API Key / Base URL**:
   - **OpenAI API Base URL**: `https://your-nimmakai-domain.com/v1`
   - **API Key**: `sk-nimmakai-YOUR-KEY`
3. Add Model: **`nimmakai/auto`** (or `nimmakai/coding`).
4. Select `nimmakai/auto` as your primary active model in Cursor Agent / Composer.

---

### 2. Claude Code CLI Setup (`claude` Terminal Tool)

Claude Code CLI uses the Anthropic Messages API. Nimmakai natively serves Anthropic-compatible endpoints (`/v1/messages` and `/chat`) with full streaming support.

Add the following environment variables to your `~/.zshrc` or `~/.bashrc`:

```bash
export ANTHROPIC_BASE_URL="https://your-nimmakai-domain.com/v1"
export ANTHROPIC_API_KEY="sk-nimmakai-YOUR-KEY"
```

Then run `claude` directly in your terminal:
```bash
claude
```

---

### 3. Cline (VS Code Extension)

Cline supports both OpenAI and Anthropic compatible API providers:

1. Open **Cline Settings** in VS Code.
2. Select **API Provider**: `OpenAI Compatible` (or `Anthropic Compatible`).
3. Set **Base URL**: `https://your-nimmakai-domain.com/v1`
4. Set **API Key**: `sk-nimmakai-YOUR-KEY`
5. Set **Model ID**: `nimmakai/auto`

Nimmakai automatically advertises context window sizes (`128,000` tokens) and tool capabilities to Cline via `GET /v1/models`.

---

### 4. OpenCode CLI Setup (Zen & Go Integration)

Nimmakai natively aggregates both **OpenCode Zen** and **OpenCode Go** backends:

- **OpenCode Zen** (`https://opencode.ai/zen/v1`): Free & freemium coding agent models (`opencode/zen-free`, `mimo-v2.5-free`, `deepseek-v4-flash-free`).
- **OpenCode Go** (`https://opencode.ai/zen/go/v1`): High-performance Go endpoints (`opencode-go/grok-4.5`, `opencode-go/glm-5.2`, `opencode-go/kimi-k3`, `opencode-go/deepseek-v4-pro`, `opencode-go/mimo-v2.5-pro`, `opencode-go/minimax-m3`, `opencode-go/qwen3.7-max`, `opencode-go/hy3`).

Configure `opencode` to route through Nimmakai:

```yaml
# ~/.config/opencode/config.yaml
provider:
  name: custom
  api_base: https://your-nimmakai-domain.com/v1
  api_key: sk-nimmakai-YOUR-KEY
  model: opencode-go/kimi-k3  # Or opencode-go/grok-4.5, opencode-go/deepseek-v4-pro, nimmakai/auto
```

---

## 🌐 API Protocols & Endpoints

Nimmakai exposes three primary protocol surfaces:

| Endpoint | Protocol | Purpose |
| :--- | :--- | :--- |
| `POST /v1/chat/completions` | OpenAI Chat API | Standard OpenAI streaming & non-streaming completions |
| `POST /v1/messages` | Anthropic Messages API | Native Claude Code CLI / Anthropic SDK streaming & tool calls |
| `POST /chat` | Unified Chat API | Dual-protocol endpoint for web clients & Open WebUI |
| `GET /v1/models` | OpenAI Models API | Enriched catalog listing with context windows and capabilities |
| `GET /dashboard` | Web SaaS Portal | Admin dashboard, telemetry, routing rules, and provider settings |

---

## 📦 Production Deployment Guide (DigitalOcean)

### ⚡ 1-Line Automatic Deployment

Run this single command on any fresh Ubuntu / DigitalOcean Droplet to install Docker, setup secrets, launch containers, configure UFW firewall rules, and verify health automatically:

```bash
curl -fsSL https://raw.githubusercontent.com/vskrch/Nimmakai/main/deploy.sh | sudo bash
```

Or pass your domain for automatic SSL HTTPS configuration:

```bash
DOMAIN_NAME="api.yourdomain.com" curl -fsSL https://raw.githubusercontent.com/vskrch/Nimmakai/main/deploy.sh | sudo bash
```

---

### Option A: Manual Docker + Caddy Deployment

Run Nimmakai on a DigitalOcean Droplet ($6–$12/mo) with persistent SQLite storage and automatic Let's Encrypt HTTPS using Caddy.

#### 1. Clone & Configure Environment
```bash
git clone https://github.com/vskrch/Nimmakai.git /opt/nimmakai
cd /opt/nimmakai

cat <<EOF > .env
PROXY_API_KEYS=sk-nimmakai-YOUR-PRODUCTION-SECRET-KEY
ALLOW_INSECURE_AUTH=false
SQLITE_SEED_FREE_PRESETS=true
ANALYTICS_ENABLED=true
ROUTING_ENABLED=true
ADMIN_PASSWORD=your-secure-admin-password
NIM_API_KEYS=nvapi-your-nvidia-key
GROQ_API_KEY=gsk_your_groq_key
EOF

chmod 600 .env
```

#### 2. Start Application
```bash
docker compose -f docker-compose.do.yml up -d --build
```

#### 3. Setup Caddy SSL Reverse Proxy
```bash
# Install Caddy
sudo apt install -y caddy

# Edit /etc/caddy/Caddyfile
api.yourdomain.com {
    reverse_proxy 127.0.0.1:8080
}

# Reload Caddy
sudo systemctl reload caddy
```

---

### Option B: DigitalOcean App Platform (Managed Container)

1. Connect your GitHub repository to **DigitalOcean App Platform**.
2. Select **Dockerfile** as the build source.
3. Configure HTTP Port: `8080`.
4. Add Secret Environment Variables: `PROXY_API_KEYS`, `ALLOW_INSECURE_AUTH=false`, `ADMIN_PASSWORD`.
5. Attach your Custom Domain for automated TLS management.

---

## 💻 Local Development & Testing

### Building the SaaS Frontend
```bash
./build-frontend.sh
```

### Running the Python Backend
```bash
uv run uvicorn src.nimmakai.main:app --port 8080 --reload
```

### Running Automated Test Suite
```bash
pytest tests/ -v
```

---

## 📄 License

MIT © Nimmakai Team.
