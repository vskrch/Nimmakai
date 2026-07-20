#!/usr/bin/env bash
# Interactive generator: prompts for secrets → writes a one-shot DigitalOcean
# Droplet "User data" script. Paste that into Create Droplet → Advanced → User data.
#
# Usage:
#   ./scripts/generate-do-userdata.sh
#   ./scripts/generate-do-userdata.sh -o /tmp/nimmakai-userdata.sh
#   NONINTERACTIVE=1 REPO_URL=... PROXY_API_KEYS=sk-... ./scripts/generate-do-userdata.sh -o out.sh
#
# Security: the generated script embeds your keys (base64). Anyone with DO
# account/API access can read user data. Destroy unused droplets; rotate keys
# if the script leaks.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_FILE="${ROOT}/nimmakai-droplet-userdata.sh"
DEFAULT_REPO="https://github.com/vskrch/Nimmakai.git"
DEFAULT_BRANCH="main"

usage() {
  cat <<'EOF'
generate-do-userdata.sh — build a paste-into-DO Droplet user-data bootstrap

Options:
  -o FILE   Output path (default: ./nimmakai-droplet-userdata.sh)
  -h        Help

Env (non-interactive when NONINTERACTIVE=1):
  REPO_URL, BRANCH, PROXY_API_KEYS, GIT_TOKEN
  NIM_API_KEYS, OPENCODE_ZEN_API_KEYS, OPENCODE_API_KEYS, GROQ_API_KEYS,
  CEREBRAS_API_KEYS, OPENROUTER_API_KEYS, GEMINI_API_KEYS,
  TOGETHER_API_KEYS, FIREWORKS_API_KEYS, MISTRAL_API_KEYS,
  GITHUB_MODELS_API_KEYS, EXTRA_ENV_LINES
EOF
}

while getopts "o:h" opt; do
  case "$opt" in
    o) OUT_FILE="$OPTARG" ;;
    h) usage; exit 0 ;;
    *) usage; exit 1 ;;
  esac
done

prompt() {
  # prompt VAR "Question" "default"
  local var="$1" q="$2" def="${3-}"
  local ans
  if [[ -n "$def" ]]; then
    read -r -p "$q [$def]: " ans || true
    ans="${ans:-$def}"
  else
    read -r -p "$q: " ans || true
  fi
  printf -v "$var" '%s' "$ans"
}

prompt_secret() {
  # prompt_secret VAR "Question" — empty allowed; hide input when tty
  local var="$1" q="$2"
  local ans
  if [[ -t 0 ]]; then
    read -r -s -p "$q (empty=skip): " ans || true
    echo
  else
    read -r -p "$q (empty=skip): " ans || true
  fi
  printf -v "$var" '%s' "$ans"
}

append_env() {
  local key="$1" val="$2"
  [[ -z "$val" ]] && return 0
  # Escape newlines in values (keys should be single-line)
  val="${val//$'\n'/}"
  ENV_BODY+="${key}=${val}"$'\n'
}

echo
echo "═══════════════════════════════════════════════════════════"
echo "  Nimmakai — DigitalOcean Droplet one-click userdata"
echo "═══════════════════════════════════════════════════════════"
echo "  Answer the prompts. You'll get one shell script to paste"
echo "  into Create Droplet → Advanced options → User data."
echo "  Use image: Marketplace → Docker on Ubuntu  (or Ubuntu 22.04+)."
echo "  Size: s-1vcpu-1gb (~\$6/mo). Open ports 22 + 80 (+443 later)."
echo "═══════════════════════════════════════════════════════════"
echo

REPO_URL="${REPO_URL:-}"
BRANCH="${BRANCH:-}"
PROXY_API_KEYS="${PROXY_API_KEYS:-}"
GIT_TOKEN="${GIT_TOKEN:-}"
ENV_BODY=""

if [[ "${NONINTERACTIVE:-0}" != "1" ]]; then
  prompt REPO_URL "Git clone URL" "$DEFAULT_REPO"
  prompt BRANCH "Git branch" "$DEFAULT_BRANCH"

  echo
  echo "Client auth — Cursor / agents use this as Bearer API key."
  prompt PROXY_API_KEYS "PROXY_API_KEYS (empty = auto-generate)" ""
  if [[ -z "$PROXY_API_KEYS" ]]; then
    PROXY_API_KEYS="sk-nimmakai-$(openssl rand -hex 16)"
    echo "  → generated: $PROXY_API_KEYS"
  fi

  echo
  echo "Optional: GitHub PAT if the repo is private (empty = public clone)."
  prompt_secret GIT_TOKEN "GIT_TOKEN / fine-grained PAT"

  echo
  echo "Provider API keys (Enter to skip any)."
  prompt_secret NIM_API_KEYS "NIM_API_KEYS"
  prompt_secret OPENCODE_ZEN_API_KEYS "OPENCODE_ZEN_API_KEYS"
  prompt_secret OPENCODE_API_KEYS "OPENCODE_API_KEYS"
  prompt_secret GROQ_API_KEYS "GROQ_API_KEYS"
  prompt_secret CEREBRAS_API_KEYS "CEREBRAS_API_KEYS"
  prompt_secret OPENROUTER_API_KEYS "OPENROUTER_API_KEYS"
  prompt_secret GEMINI_API_KEYS "GEMINI_API_KEYS"
  prompt_secret TOGETHER_API_KEYS "TOGETHER_API_KEYS"
  prompt_secret FIREWORKS_API_KEYS "FIREWORKS_API_KEYS"
  prompt_secret MISTRAL_API_KEYS "MISTRAL_API_KEYS"
  prompt_secret GITHUB_MODELS_API_KEYS "GITHUB_MODELS_API_KEYS"

  echo
  echo "Extra .env lines (KEY=value), blank line to finish:"
  EXTRA_ENV_LINES=""
  while true; do
    read -r -p "  > " line || true
    [[ -z "${line:-}" ]] && break
    EXTRA_ENV_LINES+="${line}"$'\n'
  done

  prompt OUT_FILE "Write userdata script to" "$OUT_FILE"
else
  REPO_URL="${REPO_URL:-$DEFAULT_REPO}"
  BRANCH="${BRANCH:-$DEFAULT_BRANCH}"
  if [[ -z "$PROXY_API_KEYS" ]]; then
    echo "NONINTERACTIVE=1 requires PROXY_API_KEYS" >&2
    exit 1
  fi
fi

# Build .env body
append_env "PROXY_API_KEYS" "$PROXY_API_KEYS"
append_env "ALLOW_INSECURE_AUTH" "false"
append_env "SQLITE_SEED_FREE_PRESETS" "true"
append_env "ANALYTICS_ENABLED" "true"
append_env "ROUTING_ENABLED" "true"
append_env "NIM_API_KEYS" "${NIM_API_KEYS:-}"
append_env "OPENCODE_ZEN_API_KEYS" "${OPENCODE_ZEN_API_KEYS:-}"
append_env "OPENCODE_API_KEYS" "${OPENCODE_API_KEYS:-}"
append_env "GROQ_API_KEYS" "${GROQ_API_KEYS:-}"
append_env "CEREBRAS_API_KEYS" "${CEREBRAS_API_KEYS:-}"
append_env "OPENROUTER_API_KEYS" "${OPENROUTER_API_KEYS:-}"
append_env "GEMINI_API_KEYS" "${GEMINI_API_KEYS:-}"
append_env "TOGETHER_API_KEYS" "${TOGETHER_API_KEYS:-}"
append_env "FIREWORKS_API_KEYS" "${FIREWORKS_API_KEYS:-}"
append_env "MISTRAL_API_KEYS" "${MISTRAL_API_KEYS:-}"
append_env "GITHUB_MODELS_API_KEYS" "${GITHUB_MODELS_API_KEYS:-}"
if [[ -n "${EXTRA_ENV_LINES:-}" ]]; then
  ENV_BODY+="${EXTRA_ENV_LINES}"
fi

ENV_B64="$(printf '%s' "$ENV_BODY" | base64 | tr -d '\n')"
REPO_B64="$(printf '%s' "$REPO_URL" | base64 | tr -d '\n')"
BRANCH_B64="$(printf '%s' "$BRANCH" | base64 | tr -d '\n')"
TOKEN_B64=""
if [[ -n "${GIT_TOKEN:-}" ]]; then
  TOKEN_B64="$(printf '%s' "$GIT_TOKEN" | base64 | tr -d '\n')"
fi

# shellcheck disable=SC2016
cat >"$OUT_FILE" <<EOF
#!/bin/bash
# Nimmakai Droplet bootstrap — generated $(date -u +%Y-%m-%dT%H:%MZ)
# Paste into DigitalOcean → Create Droplet → Advanced → User data
# Image: Docker on Ubuntu  |  Size: s-1vcpu-1gb  |  Firewall: 22,80
set -euxo pipefail
exec > >(tee -a /var/log/nimmakai-bootstrap.log) 2>&1

export DEBIAN_FRONTEND=noninteractive
INSTALL_DIR=/opt/nimmakai
READY_FILE=/root/NIMMAKAI-READY.txt

echo "==> waiting for cloud-init / apt"
cloud-init status --wait 2>/dev/null || true
for i in \$(seq 1 60); do
  if ! fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 \\
    && ! fuser /var/lib/apt/lists/lock >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo "==> packages"
apt-get update -y
apt-get install -y --no-install-recommends ca-certificates curl git jq openssl

if ! command -v docker >/dev/null 2>&1; then
  echo "==> installing Docker Engine"
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
fi

# Compose v2 plugin (Marketplace image usually has it)
if ! docker compose version >/dev/null 2>&1; then
  apt-get install -y docker-compose-plugin || true
fi
docker --version
docker compose version

REPO_URL=\$(printf '%s' '${REPO_B64}' | base64 -d)
BRANCH=\$(printf '%s' '${BRANCH_B64}' | base64 -d)
GIT_TOKEN=""
if [[ -n '${TOKEN_B64}' ]]; then
  GIT_TOKEN=\$(printf '%s' '${TOKEN_B64}' | base64 -d)
fi

echo "==> clone \$REPO_URL (\$BRANCH)"
rm -rf "\$INSTALL_DIR"
if [[ -n "\$GIT_TOKEN" ]]; then
  # https://USER:TOKEN@host/path  or inject token for github.com
  case "\$REPO_URL" in
    https://github.com/*)
      CLONE_URL="https://x-access-token:\${GIT_TOKEN}@\${REPO_URL#https://}"
      ;;
    https://*)
      CLONE_URL="https://x-access-token:\${GIT_TOKEN}@\${REPO_URL#https://}"
      ;;
    *)
      CLONE_URL="\$REPO_URL"
      ;;
  esac
  git clone --depth 1 --branch "\$BRANCH" "\$CLONE_URL" "\$INSTALL_DIR"
else
  git clone --depth 1 --branch "\$BRANCH" "\$REPO_URL" "\$INSTALL_DIR"
fi

cd "\$INSTALL_DIR"
test -f docker-compose.do.yml

echo "==> writing .env"
printf '%s' '${ENV_B64}' | base64 -d > .env
chmod 600 .env

echo "==> docker compose up"
docker compose -f docker-compose.do.yml up -d --build

echo "==> wait for /health"
ok=0
for i in \$(seq 1 90); do
  if curl -fsS http://127.0.0.1/health >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 2
done

IP=\$(curl -fsS http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address 2>/dev/null || true)
IP=\${IP:-UNKNOWN}

PROXY_KEY=\$(grep -E '^PROXY_API_KEYS=' .env | head -1 | cut -d= -f2- | cut -d, -f1)

{
  echo "Nimmakai is live (bootstrap finished: \$(date -u +%Y-%m-%dT%H:%MZ))"
  echo
  if [[ "\$ok" == "1" ]]; then
    echo "Health: OK"
  else
    echo "Health: NOT READY YET — check: docker compose -f \$INSTALL_DIR/docker-compose.do.yml logs"
  fi
  echo
  echo "Dashboard:  http://\${IP}/dashboard"
  echo "API base:   http://\${IP}/v1"
  echo "Health:     http://\${IP}/health"
  echo
  echo "Cursor / OpenAI-compatible clients:"
  echo "  Base URL:  http://\${IP}/v1"
  echo "  API Key:   \${PROXY_KEY}"
  echo "  Model:     nimmakai/auto"
  echo
  echo "Logs:        /var/log/nimmakai-bootstrap.log"
  echo "App dir:     \$INSTALL_DIR"
  echo "Update later:"
  echo "  cd \$INSTALL_DIR && git pull && docker compose -f docker-compose.do.yml up -d --build"
} | tee "\$READY_FILE"
cp "\$READY_FILE" /etc/motd 2>/dev/null || true

echo "==> bootstrap done"
EOF

chmod 600 "$OUT_FILE"

BYTES=$(wc -c <"$OUT_FILE" | tr -d ' ')
echo
echo "═══════════════════════════════════════════════════════════"
echo "  Wrote: $OUT_FILE  (${BYTES} bytes)"
echo "═══════════════════════════════════════════════════════════"
echo
echo "Next steps (one-time):"
echo "  1. DigitalOcean → Create Droplet"
echo "  2. Image: Marketplace → Docker on Ubuntu"
echo "  3. Size: Basic s-1vcpu-1gb (~\$6)"
echo "  4. Auth: your SSH key"
echo "  5. Advanced → User data → paste the ENTIRE file contents"
echo "  6. Create → wait 5–10 min for first Docker build"
echo "  7. SSH in:  cat /root/NIMMAKAI-READY.txt"
echo "     or open: http://YOUR_DROPLET_IP/health"
echo
echo "Your PROXY_API_KEYS (save now):"
echo "  $PROXY_API_KEYS"
echo
echo "Cursor:"
echo "  Base URL: http://YOUR_DROPLET_IP/v1"
echo "  API Key:  $PROXY_API_KEYS"
echo "  Model:    nimmakai/auto"
echo
echo "⚠  User data embeds secrets (readable via DO API/metadata). Rotate if leaked."
echo "   File is mode 600. Do not commit $OUT_FILE to git."
echo
