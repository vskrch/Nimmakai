# Potato 🥔 — Enterprise LLM Gateway & Adaptive Contextual RL Auto-Router

[![Build Status](https://img.shields.io/badge/build-passing-brightgreen)](https://github.com/vskrch/potato-gateway)
[![Python Version](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://python.org)
[![RL Engine](https://img.shields.io/badge/RL-LinUCB%20Contextual%20Bandit-orange)](file:///Users/venkatasai/CascadeProjects/Potato/src/potato/routing/rl_engine.py)
[![UI Dashboard](https://img.shields.io/badge/ui-React%2018%20%2B%20Tailwind-purple)](file:///Users/venkatasai/CascadeProjects/Potato/frontend)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Potato** is a high-performance, enterprise-grade LLM API Gateway and adaptive auto-router. Designed for modern AI engineering and agentic workflows, Potato unifies multi-provider inference pools (**NVIDIA NIM**, **Groq**, **Ollama**, **OpenCode Zen & Go**, **OpenRouter**, **Cerebras**, **Together**, **SambaNova**, **DeepSeek**, and custom endpoints) into a single, bulletproof API compatible with both **OpenAI** and **Anthropic Messages** protocols.

Potato features a production-tested **LinUCB Contextual Reinforcement Learning engine** that adapts routing weights online using real-time execution feedback (TTFB latency, function calling validity, error codes, and immediate client retries).

---

## 🏛️ System Architecture

```mermaid
flowchart TD
    subgraph Client ["Client Harnesses & SDKs"]
        Cursor["Cursor Agent / Composer"]
        Claude["Claude Code CLI"]
        Cline["Cline VS Code"]
        OpenCode["OpenCode CLI"]
        SDK["OpenAI / Anthropic SDKs"]
    end

    subgraph Ingress ["Potato API Gateway Ingress"]
        Auth["Account Guard & Auth"]
        Classifier["Dynamic Intent Blending (Rules + Neural Boost)"]
        TinyRouter["TinyRouter (~10K Parameter CPU Neural Head)"]
        Extractor["12-D Feature Extractor (x)"]
    end

    subgraph Routing ["Dual-Stage Adaptive Routing"]
        Ladder["Stage 1: Multi-Criteria Quality Ladder"]
        LinUCB["LinUCB Contextual Bandit (Sherman-Morrison O(d²))"]
        Optimizer["Stage 2: Cobb-Douglas Live Optimizer"]
    end

    subgraph Pool ["Upstream Provider Infrastructure"]
        NIM["NVIDIA NIM"]
        Groq["Groq Ultra-Fast"]
        OpenCodeGo["OpenCode Zen & Go"]
        Ollama["Ollama / Local"]
        Custom["OpenAI Compatible Endpoints"]
    end

    subgraph Feedback ["Closed-Loop Multi-Signal Feedback & Persistence"]
        Telemetry["TTFB, Tool Syntax, Error 429/503"]
        DB[(SQLite Policy Store & JSON Weights)]
    end

    Client --> Auth
    Auth --> Classifier
    Classifier -->|Rule Confidence < 70%| TinyRouter
    Classifier -->|Rule Confidence >= 70%| Extractor
    TinyRouter --> Extractor
    Extractor --> Ladder
    Ladder --> LinUCB
    LinUCB --> Optimizer
    Optimizer --> Pool
    Pool --> Telemetry
    Telemetry --> DB
    DB --> LinUCB
    DB --> TinyRouter
```

---

## 🚀 Key Features

* **🔬 TinyRouter (~10K Parameter CPU Neural Head)**: Ultra-fast $< 0.5\text{ms}$ neural intent classification using 64-D semantic n-gram hashing and 12-D RL structural feature vectors without LLM overhead.
* **⚖️ Dynamic Intent Blending Mode**: Automatically combines deterministic regex fast-paths ($\ge 70\%$ confidence) with neural boosting for ambiguous prompts via header control (`x-potato-classify-mode: dynamic`).
* **🧠 LinUCB Contextual Bandit Engine**: Dynamically weights routing choices per request using a 12-dimensional feature vector ($X$) and Sherman-Morrison rank-1 matrix inverse updates in under 1 microsecond.
* **⚡ Dual-Stage Intelligent Routing**:
  - **Stage 1 (Ladder)**: Precomputed composite score combining benchmark quality, intent affinity, parameter sizes, and provider capabilities.
  - **Stage 2 (Cobb-Douglas Optimizer)**: Real-time request adaptation balancing quality, latency, key pool availability, and provider health.
* **🔌 Universal Protocol Compatibility & Auto-Tool Recovery**: Native Anthropic Messages API (`tool_choice` translation) and OpenAI Responses API support with intelligent XML/JSON parser recovery (`_extract_raw_tool_calls_from_text`) to prevent agentic loops in Cursor, Claude Code, and Cascade.
* **💾 Persistent Self-Healing Telemetry Loop**: Automatic 60-second asynchronous persistence of LinUCB $12 \times 12$ covariance matrices ($A^{-1}, \theta, b$) to SQLite (`potato.db`) and neural weights to `config/tinyrouter_weights.json`.
* **🎯 Granular Model Pool & Intent Gating**: Surgically restrict high-cost frontier models to specialized endpoints (e.g., `potato/coding` or `potato/best`) while excluding them from general auto-routing (`potato/auto`) to prevent token waste.
* **🔑 4-Pillar Multi-Tenant Architecture & BYOK**: Dedicated tenant isolation where standard users manage their own encrypted API keys at rest (AES-256-GCM) and view private analytics, with Admin shared fallbacks and cross-tenant oversight (`multi-tenant` branch).
* **🛡️ Zero-Downtime Fallback & Circuit Breakers**: Automatic fallbacks across providers. If a model encounters a 504 gateway timeout, rate limit (429), or 5xx server error, Potato immediately advances down the intelligence ladder without failing client requests.
* **🤖 First-Class Agent Tooling**: Native drop-in compatibility for **Cursor IDE**, **Claude Code CLI**, **Cline**, **Windsurf**, and **OpenCode CLI**.
* **📊 360° Request Explorer & Glassmorphic Dashboard**: Built with React 18 + Tailwind CSS, featuring live SSE request streams, latency waterfalls, token expenditure analytics, granular Model/Intent/Status filtering, and interactive **Adaptive RL Policy** telemetry.

---

## 🎯 Virtual Routing Models

Pass any of the following virtual model identifiers in your API requests:

| Model ID | Description | Primary Intent Target |
| :--- | :--- | :--- |
| **`potato/auto`** | Default intelligent router — dynamically picks the best model for any prompt | Dynamic auto-selection |
| **`potato/coding`** | Optimized for code generation, agentic tool execution, and multi-turn refactoring | `coding_agentic` |
| **`potato/best`** | Frontier reasoning & complex mathematical theorem proving | `reasoning` / `long_horizon` |
| **`potato/auto-fast`** | Latency-first routing prioritized for low TTFT and high TPS | `chat_fast` |
| **`potato/auto-cheap`** | Cost-optimized routing prioritizing lightweight high-efficiency models | `cheap` |

---

## 🛠️ Tool Integration Guides

### 1. Cursor IDE Setup (Agentic Custom OpenAI Base URL)

1. Open **Cursor** $\rightarrow$ **Settings** $\rightarrow$ **Models**.
2. Under **OpenAI API Key / Base URL**:
   - **OpenAI API Base URL**: `https://your-potato-domain.com/v1`
   - **API Key**: `sk-potato-YOUR-KEY`
3. Add Model: **`potato/auto`** (or `potato/coding`).
4. Select `potato/auto` as your primary active model in Cursor Agent / Composer.

---

### 2. Claude Code CLI Setup (`claude` Terminal Tool)

Potato natively serves Anthropic-compatible endpoints (`/v1/messages` and `/chat`) with full streaming support.

Add the following environment variables to your shell configuration (`~/.zshrc` or `~/.bashrc`):

```bash
export ANTHROPIC_BASE_URL="https://your-potato-domain.com/v1"
export ANTHROPIC_API_KEY="sk-potato-YOUR-KEY"
```

Then run `claude` directly in your terminal:
```bash
claude
```

---

### 3. Cline (VS Code Extension)

1. Open **Cline Settings** in VS Code.
2. Select **API Provider**: `OpenAI Compatible` (or `Anthropic Compatible`).
3. Set **Base URL**: `https://your-potato-domain.com/v1`
4. Set **API Key**: `sk-potato-YOUR-KEY`
5. Set **Model ID**: `potato/auto`

Potato automatically advertises context window sizes (`128,000` tokens) and tool capabilities to Cline via `GET /v1/models`.

---

### 4. OpenCode CLI Setup (Zen & Go Integration)

Potato natively aggregates both **OpenCode Zen** and **OpenCode Go** backends:

- **OpenCode Zen** (`https://opencode.ai/zen/v1`): Free coding agent models (`opencode/zen-free`, `mimo-v2.5-free`).
- **OpenCode Go** (`https://opencode.ai/zen/go/v1`): High-performance Go endpoints (`opencode-go/grok-4.5`, `opencode-go/glm-5.2`, `opencode-go/kimi-k3`, `opencode-go/deepseek-v4-pro`).

Configure `opencode`:

```yaml
# ~/.config/opencode/config.yaml
provider:
  name: custom
  api_base: https://your-potato-domain.com/v1
  api_key: sk-potato-YOUR-KEY
  model: opencode-go/kimi-k3  # Or potato/auto, potato/coding
```

---

## 🌐 API Protocols & Endpoints

Potato exposes three primary protocol surfaces:

| Endpoint | Protocol | Purpose |
| :--- | :--- | :--- |
| `POST /v1/chat/completions` | OpenAI Chat API | Standard OpenAI streaming & non-streaming completions |
| `POST /v1/messages` | Anthropic Messages API | Native Claude Code CLI / Anthropic SDK streaming & tool calls |
| `POST /v1/responses` | OpenAI Responses API | Native OpenAI Responses API mapping to multi-provider upstreams |
| `POST /chat` | Unified Chat API | Dual-protocol endpoint for web clients & Open WebUI |
| `GET /v1/models` | OpenAI Models API | Enriched catalog listing with context windows and capabilities |
| `GET /admin/model-pools` | Admin Control | Granular per-model intent gating & auto-router pool rules |
| `GET /v1/account/provider-keys` | BYOK Key Store | Manage tenant upstream API keys encrypted with AES-256-GCM |
| `GET /admin/rl/stats` | Admin Telemetry | Live LinUCB bandit feature weights ($\theta$) & reward statistics |
| `POST /admin/rl/reset` | Admin Action | Reset online RL policy per model or globally |
| `GET /dashboard` | Web SaaS Portal | User & Admin dashboard, telemetry, routing rules, and provider settings |

---

## 📦 Production & Local Deployment Guide

### ⚡ 1-Line Automatic Deployment & Networking

Run the following commands on any fresh Debian PC, Ubuntu server, local Linux machine, or homelab to provision Docker, generate credentials, and launch Potato Gateway out of the box:

#### Step 1: Deploy Potato Gateway (Build & Docker Setup)

```bash
# Download and execute core deployment script
curl -sSL -o deploy.sh https://raw.githubusercontent.com/vskrch/potato-gateway/main/deploy.sh && sudo bash deploy.sh
```

#### Step 2: Configure Networking & GoDaddy Domain (Tailscale Funnel — Zero 499s)

```bash
# Setup Tailscale Funnel for zero-config HTTPS & GoDaddy custom domain
sudo bash scripts/setup-tunnel.sh --domain=api.yourdomain.com
```

---

### 🐧 Local Debian / Linux Setup & First-Time Admin Key

When deploying locally on Debian or Ubuntu:

1. **Automatic Credential Generation**:
   On the first run, `deploy.sh` automatically generates `ADMIN_PASSWORD` (used to log into `/dashboard`) and `PROXY_API_KEYS` (used by clients/SDKs), saving them securely into `.env` with `chmod 600`.
2. **First-Time Boot & Degraded Mode**:
   If no provider API keys are present in `.env` before running the script, Potato boots into **Degraded Mode**. The deployment script will display a yellow completion banner with your generated **`ADMIN_PASSWORD`** and **`PROXY_API_KEYS`**.
3. **Adding Provider Keys via Dashboard**:
   - Open `http://localhost:8080/dashboard` (or `http://127.0.0.1:8080/dashboard`).
   - Log in using your generated **`ADMIN_PASSWORD`**.
   - Navigate to **Providers** and add your upstream provider API keys (Groq, NVIDIA NIM, OpenRouter, DeepSeek, Cerebras, etc.).
4. **Retrieving Your Admin Password Anytime**:
   If you ever need to view your generated admin password on the host machine:
   ```bash
   sudo grep ADMIN_PASSWORD /opt/potato/.env
   # Or inside local repo:
   grep ADMIN_PASSWORD .env
   ```

---

### 🌐 Zero-Config Internet Tunneling & GoDaddy Domain Setup

To expose your homelab deployment to the public internet securely without a static IP address or router port forwarding:

#### Method 1: Tailscale Funnel — RECOMMENDED (Zero 499 Timeouts + Custom Domain)

Tailscale Funnel passes raw HTTPS streams directly to your server without intermediate proxy buffering or Cloudflare's 100-second HTTP response timeout limit.

##### 1. Automatic Automated Setup Script:
```bash
sudo bash scripts/setup-tunnel.sh --domain=api.yourdomain.com
```

##### 2. Manual CLI Step-by-Step:
```bash
# Install Tailscale
curl -fsSL https://tailscale.com/install.sh | sh

# Authenticate server
sudo tailscale up

# Enable HTTPS Funnel on port 8080
sudo tailscale funnel 8080
```

##### 3. GoDaddy CNAME DNS Setup:
1. Log into **GoDaddy** → **Domain Portfolio** → Select your domain (`potatolabs.cloud`) → **DNS Records**.
2. Add a **CNAME Record**:
   - **Type**: `CNAME`
   - **Name**: `api` *(or custom subdomain)*
   - **Value**: `admin-potato.your-tailnet.ts.net` *(your Tailscale domain)*
   - **TTL**: `1 Hour`

---

### 🧰 CLI Management Commands Reference

Potato Gateway includes single-command utilities to manage, monitor, and troubleshoot your deployment:

#### Core Gateway Management (`deploy.sh`)
```bash
# View live container state, memory/CPU stats, and health probe
sudo bash deploy.sh --status

# Tail live application logs in real-time
sudo bash deploy.sh --logs

# Safely restart the Potato Gateway container
sudo bash deploy.sh --restart

# Manually trigger a timestamped SQLite database snapshot
sudo bash deploy.sh --backup

# Stop container and clean volume state
sudo bash deploy.sh --clean
```

#### Networking & Tunnel Management (`scripts/setup-tunnel.sh`)
```bash
# Verify Tailscale status, active domain, and GoDaddy DNS propagation
sudo bash scripts/setup-tunnel.sh --status

# Test end-to-end public HTTPS health & measure latency (ms)
sudo bash scripts/setup-tunnel.sh --test

# Disable Tailscale Funnel
sudo bash scripts/setup-tunnel.sh --stop
```

---

### ❓ Troubleshooting & FAQ

#### Q1: Why was I seeing HTTP 499 errors mid-work before?
**Cause:** Cloudflare's HTTP proxy (orange cloud) enforces a strict **100-second HTTP response timeout** and buffers SSE responses (`text/event-stream`). When long reasoning models (Claude 3.7, O3, DeepSeek R1) stream output for >100s, Cloudflare drops the TCP connection, causing your IDE client to disconnect and logging `HTTP 499`.  
**Solution:** Using **Tailscale Funnel** (`scripts/setup-tunnel.sh`) passes raw HTTPS streams directly to your server without Cloudflare proxy timeouts or response buffering.

#### Q2: How do I verify if my GoDaddy DNS record has propagated?
Run the built-in DNS propagation checker:
```bash
sudo bash scripts/setup-tunnel.sh --status
```
It will query public DNS servers and confirm when `api.potatolabs.cloud` resolves to your Tailscale node. (GoDaddy DNS propagation usually takes 1–2 minutes).

#### Q3: What if Tailscale HTTPS says "HTTPS disabled"?
1. Log into your **Tailscale Admin Console** at [login.tailscale.com/admin/settings/dns](https://login.tailscale.com/admin/settings/dns).
2. Scroll to **HTTPS Certificates** and click **Enable HTTPS Certificates**.
3. Re-run `sudo bash scripts/setup-tunnel.sh --domain=api.potatolabs.cloud`.

#### Q4: How do I retrieve my Admin Password or API Keys?
Your generated credentials are saved securely in `/opt/potato/.env`:
```bash
# View Admin Password
sudo grep ADMIN_PASSWORD /opt/potato/.env

# View Direct API Key
sudo grep PROXY_API_KEYS /opt/potato/.env
```

#### Q5: How do I backup or restore my SQLite database?
Automated backups are stored under `/opt/potato/backups/`:
```bash
# List all database snapshots
ls -la /opt/potato/backups/

# Manual backup snapshot
sudo bash deploy.sh --backup
```

---

### Option A: Manual Docker Compose Deployment

#### 1. Clone & Configure Environment
```bash
git clone https://github.com/vskrch/potato-gateway.git /opt/potato
cd /opt/potato

cat <<EOF > .env
PROXY_API_KEYS=sk-potato-YOUR-PRODUCTION-SECRET-KEY
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
# Edit /etc/caddy/Caddyfile
api.yourdomain.com {
    reverse_proxy 127.0.0.1:8080
}

sudo systemctl reload caddy
```

---

## 💻 Local Development & Testing

### 1. Build the React Dashboard
```bash
cd frontend && npm run build
```

### 2. Run the Python Backend
```bash
python3 -m uvicorn potato.main:app --port 8080 --reload
```

### 3. Run Automated Test Suite
```bash
python3 -m pytest -v
```

---

## 🏆 Credits & Acknowledgements

Potato Gateway is built on the shoulders of giants. We extend our heartfelt thanks and generous credits to the research, open-source projects, and engineering breakthroughs that made this system possible:

### 🧠 Core Algorithms & Research
* **TinyRouter Neural Head**: Inspired by lightweight evolution strategies (**sep-CMA-ES**) and fast n-gram semantic hashing. This enables sub-millisecond CPU inference (~10K parameters) without ever calling an external LLM for classification.
* **LinUCB Contextual Bandit Engine**: Built upon **Lihong Li et al.'s** landmark research on contextual bandits for web content recommendation, adapted here with **Sherman-Morrison rank-1 matrix updates** for $O(d^2)$ real-time reinforcement learning across multi-provider LLM pools.
* **Cobb-Douglas Live Optimizer**: Leveraging microeconomic utility functions to balance multi-criteria constraints (quality, TTFT latency, TPS throughput, token cost, and API rate limits) in real time.

### 🌐 Upstream Providers & Model Ecosystems
* **NVIDIA NIM**, **Groq**, **Cerebras**, **Together**, **SambaNova**, and **DeepSeek** for providing ultra-high-speed inference endpoints and frontier open-weights models.
* **OpenCode (Zen & Go)** and **Ollama** for enabling democratized local and zero-cost agentic coding pools.

### 🤖 Agent Harnesses & SDK Compatibility
* **Cursor IDE**, **Anthropic Claude Code CLI**, **Cline VS Code**, **Windsurf**, and **Continue.dev** for inspiring our protocol translation layer, robust XML/JSON tool recovery (`_extract_raw_tool_calls_from_text`), and strict schema compliance.

### 🛠️ Open-Source Frameworks & Tech Stack
* **Python Backend**: [FastAPI](https://fastapi.tiangolo.com/), [Pydantic v2](https://docs.pydantic.dev/), [Uvicorn](https://www.uvicorn.org/), and [SQLite](https://sqlite.org/) for blazing-fast asynchronous networking and zero-maintenance embedded storage.
* **Modern Frontend**: [React 18](https://react.dev/), [Tailwind CSS](https://tailwindcss.com/), [Vite](https://vitejs.dev/), [Recharts](https://recharts.org/), and [Lucide Icons](https://lucide.dev/) for our glassmorphic, real-time SSE telemetry dashboard.

---

## 📄 License

MIT © Potato Team.
