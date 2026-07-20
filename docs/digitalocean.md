# Deploy Nimmakai on DigitalOcean (~$12/mo budget)

This guide sets up **push-to-deploy** CI/CD similar to Heroku: push to `main` → DigitalOcean builds the Docker image → live URL updates.

GitHub Student Pack often includes **DigitalOcean credits** (redeem at [Education](https://education.github.com/pack) → DigitalOcean). Apply credits in the DO billing panel before creating the app.

---

## Budget pick (under $12/mo)

| Option | Monthly | Persistence | Best for |
|--------|---------|-------------|----------|
| **App Platform** `apps-s-1vcpu-1gb-fixed` | **~$10** | Ephemeral disk (keys via env) | Heroku-like one-click, recommended |
| App Platform `apps-s-1vcpu-1gb` | **$12** | Ephemeral | Same + manual scaling |
| **Droplet** `s-1vcpu-1gb` + Docker | **~$6** | Persistent volume | Durable SQLite / analytics — use **one-click userdata** (`scripts/generate-do-userdata.sh`) |
| App Platform 512 MiB | **$5** | Ephemeral | Tight budget (may OOM under load) |

**Do not** add a Managed Postgres ($7+) on the $12 plan — Nimmakai uses SQLite. App Platform **does not support volumes**, so SQLite is wiped on every redeploy. Put all provider keys in encrypted env vars so the app rehydrates cleanly.

---

## Path A — App Platform (recommended, Heroku-style)

### 1. Redeem credits & create a token

1. Redeem GitHub Student DigitalOcean offer and link the account.
2. DigitalOcean → **API** → Generate personal access token (write).
3. Install CLI (optional): `brew install doctl && doctl auth init`

### 2. Push this repo to GitHub

```bash
git remote -v   # ensure origin points at your GitHub repo
git push -u origin main
```

### 3. One-time create (Control Panel — easiest)

1. [Create App](https://cloud.digitalocean.com/apps/new) → **GitHub** → authorize → select **Nimmakai** repo / `main`.
2. Detect **Dockerfile** (root). Keep HTTP port **8080**.
3. Instance size: **1 vCPU / 1 GiB fixed** (~$10/mo). Region: closest to you.
4. Add **encrypted** runtime env vars (see table below).
5. Create Resources → create app. Wait for first build (~3–6 min).
6. Open the `*.ondigitalocean.app` URL → `/health` should return JSON → `/dashboard` for UI.

Auto-deploy is on by default for GitHub apps: **every push to `main` redeploys**.

### 3b. Or create from app spec (CLI)

Edit `.do/app.yaml`:

- Set `github.repo` to `your-user/Nimmakai`
- Replace `PROXY_API_KEYS` placeholder (or leave and set secrets in UI)

```bash
doctl apps create --spec .do/app.yaml
doctl apps list   # copy the App ID
```

Then set secrets in Control Panel → App → Settings → App-Level / Component env vars → **Encrypt**.

### 4. Required environment variables

| Key | Encrypted? | Notes |
|-----|------------|-------|
| `PROXY_API_KEYS` | Yes | Client Bearer keys (Cursor / agents) |
| `NIM_API_KEYS` | Yes | Optional if you only use other providers |
| `OPENCODE_ZEN_API_KEYS` / `GROQ_API_KEYS` / … | Yes | Free-tier providers |
| `ALLOW_INSECURE_AUTH` | No | Must be `false` in production |
| `SQLITE_SEED_FREE_PRESETS` | No | `true` so presets return after redeploy |
| `ANALYTICS_ENABLED` | No | `true` (traces reset on redeploy) |

`PORT` is injected by App Platform — do not hardcode it.

### 5. Optional GitHub Actions force-deploy

Repo secrets:

- `DIGITALOCEAN_ACCESS_TOKEN`
- `DIGITALOCEAN_APP_ID` (`doctl apps list`)

Workflow: `.github/workflows/deploy-digitalocean.yml`  
Runs tests → builds Docker → `doctl apps create-deployment` on push to `main`.

Even without this workflow, App Platform still deploys on push when GitHub is connected.

### 6. Point Cursor / agents at production

```
Base URL:  https://YOUR-APP.ondigitalocean.app/v1
API Key:   <one of PROXY_API_KEYS>
Model:     nimmakai/auto
```

---

## Path B — Droplet + Docker Compose (persistent SQLite)

Use when you need analytics / dashboard-added providers to survive redeploys.
Compose file: `docker-compose.do.yml` (maps host **80 → 8080**, volume `nimmakai-data` → `/data`).

### B0. One-click User data (fastest)

Generate a paste-ready bootstrap script on your laptop (prompts for keys, embeds them as base64):

```bash
./scripts/generate-do-userdata.sh
# writes ./nimmakai-droplet-userdata.sh  (gitignored)
```

Then [Create Droplet](https://cloud.digitalocean.com/droplets/new):

| Setting | Value |
|---------|--------|
| Image | Marketplace → **Docker on Ubuntu** |
| Size | Basic **s-1vcpu-1gb** (~$6) |
| Auth | SSH key |
| User data | Paste **entire** `nimmakai-droplet-userdata.sh` |

Wait 5–10 minutes → `http://YOUR_IP/health` or `cat /root/NIMMAKAI-READY.txt` over SSH.

Non-interactive (CI / scripting):

```bash
NONINTERACTIVE=1 \
  PROXY_API_KEYS=sk-your-key \
  NIM_API_KEYS=nvapi-... \
  GROQ_API_KEYS=gsk-... \
  ./scripts/generate-do-userdata.sh -o /tmp/nimmakai-userdata.sh
```

Manual SSH steps below (B1+) are the same outcome without user data.

### B1. Redeem credits & create the Droplet

1. Redeem GitHub Student DigitalOcean credits (if any) in **Billing**.
2. [Create Droplet](https://cloud.digitalocean.com/droplets/new):
   - **Region**: closest to you
   - **Image**: Marketplace → **Docker on Ubuntu** (or Ubuntu + install Docker yourself)
   - **Size**: Basic **`$6`** — `s-1vcpu-1gb` (1 vCPU / 1 GiB)
   - **Authentication**: SSH key (recommended) or one-time password
   - Hostname: e.g. `nimmakai`
3. Create → wait until status is **Active**. Copy the public IPv4.

CLI alternative:

```bash
doctl compute ssh-key list   # note KEY_ID
doctl compute droplet create nimmakai \
  --size s-1vcpu-1gb \
  --image docker-20-04 \
  --region sfo3 \
  --ssh-keys KEY_ID \
  --wait
doctl compute droplet list
```

### B2. Open the firewall (optional but recommended)

DigitalOcean → **Networking** → **Firewalls** → create one attached to this droplet:

| Type | Protocol | Port | Sources |
|------|----------|------|---------|
| SSH | TCP | 22 | Your IP (or `0.0.0.0/0` if you must) |
| HTTP | TCP | 80 | `0.0.0.0/0` |
| HTTPS | TCP | 443 | `0.0.0.0/0` (needed once you add TLS) |

### B3. SSH in and clone the repo

```bash
ssh root@YOUR_DROPLET_IP
# (or: ssh -i ~/.ssh/id_ed25519 root@YOUR_DROPLET_IP)

mkdir -p /opt && cd /opt
git clone https://github.com/YOUR_USER/Nimmakai.git
cd Nimmakai
```

Private repo: use a deploy key or `git clone git@github.com:YOUR_USER/Nimmakai.git` after adding the droplet’s SSH key to GitHub.

Confirm Docker is available:

```bash
docker --version
docker compose version
```

### B4. Configure secrets (`.env`)

```bash
cp .env.example .env
nano .env   # or vim / micro
```

Minimum for production:

| Key | Value |
|-----|--------|
| `PROXY_API_KEYS` | Strong random key(s), comma-separated — clients use these as Bearer |
| `ALLOW_INSECURE_AUTH` | Must stay `false` (compose also forces this) |
| Provider keys | e.g. `NIM_API_KEYS`, `OPENCODE_ZEN_API_KEYS`, `GROQ_API_KEYS`, … |

Compose already sets `SQLITE_PATH=/data/nimmakai.db` and seeds free presets. Do **not** set `ALLOW_INSECURE_AUTH=true` on the droplet.

Generate a proxy key if needed:

```bash
openssl rand -hex 24
# paste into PROXY_API_KEYS=sk-...
```

### B5. Build and start

```bash
cd /opt/Nimmakai
docker compose -f docker-compose.do.yml up -d --build
docker compose -f docker-compose.do.yml ps
docker compose -f docker-compose.do.yml logs -f --tail=80
```

First build can take several minutes (frontend `npm ci` + Python image).

### B6. Verify on the Droplet IP

```bash
# from your laptop
curl -s http://YOUR_DROPLET_IP/health | jq .
curl -s -H "Authorization: Bearer YOUR_PROXY_KEY" \
  http://YOUR_DROPLET_IP/analytics/summary | jq .
```

Browser: `http://YOUR_DROPLET_IP/dashboard` → Auth modal → paste a `PROXY_API_KEYS` value.

### B7. (Optional) Domain + HTTPS with Caddy

1. Point an A record: `nimmakai.example.com` → Droplet IP.
2. On the droplet, install Caddy and reverse-proxy to the container. Simplest pattern: change compose to publish **`127.0.0.1:8080:8080`** only, then Caddy listens on 80/443:

```bash
# /etc/caddy/Caddyfile
nimmakai.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

```bash
systemctl reload caddy
```

Then use `https://nimmakai.example.com` in Cursor.

### B8. Point Cursor / agents at the Droplet

```
Base URL:  http://YOUR_DROPLET_IP/v1
           # or https://nimmakai.example.com/v1 after TLS
API Key:   <one of PROXY_API_KEYS>
Model:     nimmakai/auto
```

### B9. Redeploy / updates

```bash
ssh root@YOUR_DROPLET_IP
cd /opt/Nimmakai
git pull
docker compose -f docker-compose.do.yml up -d --build
```

SQLite + catalog live in Docker volume **`nimmakai-data`** — they survive rebuilds. To wipe data: `docker volume rm nimmakai_nimmakai-data` (destructive).

Optional auto-update: [Watchtower](https://containrrr.dev/watchtower/) watching `nimmakai:latest`, or a GitHub Action that SSHs and runs the commands above.

### B10. Useful ops commands

```bash
docker compose -f docker-compose.do.yml logs -f
docker compose -f docker-compose.do.yml restart
docker compose -f docker-compose.do.yml down          # stop (keeps volume)
docker volume ls | grep nimmakai
df -h                                                 # disk pressure on 1 GiB
```

---

## Verify deploy

**App Platform:**

```bash
curl -s https://YOUR-APP.ondigitalocean.app/health | jq .
curl -s -H "Authorization: Bearer $PROXY_KEY" \
  https://YOUR-APP.ondigitalocean.app/analytics/summary | jq .
```

**Droplet:**

```bash
curl -s http://YOUR_DROPLET_IP/health | jq .
curl -s -H "Authorization: Bearer $PROXY_KEY" \
  http://YOUR_DROPLET_IP/analytics/summary | jq .
```

Local Docker smoke test before pushing:

```bash
docker build -t nimmakai:local .
docker run --rm -p 8080:8080 \
  -e PROXY_API_KEYS=sk-test \
  -e ALLOW_INSECURE_AUTH=false \
  -e NIM_API_KEYS= \
  nimmakai:local
# then: curl localhost:8080/health
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Build fails on `npm ci` | Ensure `frontend/package-lock.json` is committed |
| App unhealthy | Check `/health`; raise `initial_delay_seconds`; watch Runtime Logs |
| 401 on dashboard | Set `PROXY_API_KEYS` and use that key in the Auth modal |
| Providers empty after deploy | Set provider `*_API_KEYS` env vars (App Platform wipes SQLite; Droplet keeps volume) |
| OOM / restarts | Bump to `apps-s-1vcpu-1gb` ($12) or Droplet `s-1vcpu-2gb` |
| Droplet: connection refused on :80 | `docker compose … ps` — container not up; check logs / firewall allows 80 |
| Droplet: build OOM killed | Temporarily resize droplet up, rebuild, or build on CI and `docker pull` |
| Student credits not applying | Billing → redeem promo; confirm account email matches GitHub Education |

---

## Cost control

- **Droplet**: stay on `s-1vcpu-1gb` (~$6); destroy when unused.
- **App Platform**: start on **$10 fixed**; stay at 1 instance.
- No managed DB, no dedicated egress IP ($25).
- Watch bandwidth (Droplet Networking / App Insights).
- Destroy unused preview apps / old droplets.
