# Deploy Nimmakai on DigitalOcean (~$12/mo budget)

This guide sets up **push-to-deploy** CI/CD similar to Heroku: push to `main` → DigitalOcean builds the Docker image → live URL updates.

GitHub Student Pack often includes **DigitalOcean credits** (redeem at [Education](https://education.github.com/pack) → DigitalOcean). Apply credits in the DO billing panel before creating the app.

---

## Budget pick (under $12/mo)

| Option | Monthly | Persistence | Best for |
|--------|---------|-------------|----------|
| **App Platform** `apps-s-1vcpu-1gb-fixed` | **~$10** | Ephemeral disk (keys via env) | Heroku-like one-click, recommended |
| App Platform `apps-s-1vcpu-1gb` | **$12** | Ephemeral | Same + manual scaling |
| Droplet `s-1vcpu-1gb` + Docker | **~$6** | Persistent volume | Durable SQLite / analytics |
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

1. Create Droplet **Basic $6** (`s-1vcpu-1gb`), image **Docker on Ubuntu**.
2. SSH in, clone repo, copy `.env.example` → `.env`, fill keys.
3. `docker compose -f docker-compose.do.yml up -d --build`
4. Optional: attach a domain + Caddy/nginx TLS.
5. CI/CD: add a GitHub Action that SSHs and runs `git pull && docker compose ... up -d --build` (or use [Watchtower](https://containrrr.dev/watchtower/)).

Data lives in Docker volume `nimmakai-data`.

---

## Verify deploy

```bash
curl -s https://YOUR-APP.ondigitalocean.app/health | jq .
curl -s -H "Authorization: Bearer $PROXY_KEY" \
  https://YOUR-APP.ondigitalocean.app/analytics/summary | jq .
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
| Providers empty after deploy | Set provider `*_API_KEYS` env vars (SQLite is wiped on App Platform) |
| OOM / restarts | Bump to `apps-s-1vcpu-1gb` ($12) or Droplet 1 GiB |
| Student credits not applying | Billing → redeem promo; confirm account email matches GitHub Education |

---

## Cost control

- Start on **$10 fixed** instance; stay at 1 instance.
- No managed DB, no dedicated egress IP ($25).
- Watch **Bandwidth** in the app Insights tab (allowance comes with the instance).
- Destroy unused preview apps / old droplets.
