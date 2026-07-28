#!/usr/bin/env bash
# ==============================================================================
# Potato (🥔) Dedicated Networking & Tunnel Setup Script
# Configures Tailscale Funnel for zero-config HTTPS and 499-free streaming.
#
# Usage:
#   sudo bash scripts/setup-tunnel.sh --domain=api.yourdomain.com
# ==============================================================================
set -euo pipefail

# Visual colors
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/opt/homebrew/bin:${PATH:-}"

log() { echo -e "${CYAN}${BOLD}[Potato Tunnel]${NC} $1"; }
ok()  { echo -e "${GREEN}${BOLD}[SUCCESS]${NC} $1"; }
warn(){ echo -e "${YELLOW}${BOLD}[WARNING]${NC} $1"; }
err() { echo -e "${RED}${BOLD}[ERROR]${NC} $1"; exit 1; }

echo -e "${BOLD}"
echo "=============================================================================="
echo "               🥔 POTATO NETWORKING & TAILSCALE TUNNEL SETUP                  "
echo "=============================================================================="
echo -e "${NC}"

DOMAIN_NAME=""
PORT="8080"
ENV_FILE=""

for arg in "$@"; do
    case $arg in
        --domain=*)
            DOMAIN_NAME="${arg#*=}"
            ;;
        --port=*)
            PORT="${arg#*=}"
            ;;
        --env=*)
            ENV_FILE="${arg#*=}"
            ;;
        --help|-h)
            echo "Usage: sudo bash scripts/setup-tunnel.sh [options]"
            echo ""
            echo "Options:"
            echo "  --domain=api.yourdomain.com   Set custom GoDaddy/DNS domain name"
            echo "  --port=8080                   Port for Potato Gateway (default: 8080)"
            echo "  --env=/path/to/.env           Path to .env file to update"
            exit 0
            ;;
    esac
done

# Resolve default .env file
if [[ -z "${ENV_FILE}" ]]; then
    if [[ -f "/opt/potato/.env" ]]; then
        ENV_FILE="/opt/potato/.env"
    elif [[ -f ".env" ]]; then
        ENV_FILE=".env"
    fi
fi

OS="$(uname -s)"
if [[ "$OS" == "Linux" && $EUID -ne 0 ]]; then
    warn "On Linux, running with sudo is recommended for systemd/tailscale setup: sudo bash scripts/setup-tunnel.sh"
fi

# 1. Install Tailscale if missing
if ! command -v tailscale &>/dev/null; then
    log "Tailscale not detected. Installing Tailscale..."
    curl -fsSL https://tailscale.com/install.sh | sh || err "Failed to install Tailscale."
    ok "Tailscale installation complete."
else
    ok "Tailscale binary detected."
fi

# 2. Check Tailscale authentication status
log "Checking Tailscale node status..."
if ! tailscale status &>/dev/null; then
    log "Tailscale node is unauthenticated. Launching 'tailscale up'..."
    tailscale up || warn "Please run 'sudo tailscale up' manually to log into your Tailscale account."
fi

# 3. Provision Tailscale Funnel on port
log "Enabling Tailscale Funnel for local port ${PORT}..."
TAILSCALE_BIN="$(command -v tailscale || echo /usr/bin/tailscale)"
if command -v systemctl &>/dev/null && [[ $EUID -eq 0 ]]; then
    log "Creating systemd service for Tailscale Funnel..."
    cat <<EOF > /etc/systemd/system/tailscale-funnel-potato.service
[Unit]
Description=Potato Gateway Tailscale Funnel Daemon
After=network-online.target tailscaled.service
Wants=network-online.target tailscaled.service

[Service]
Type=simple
User=root
ExecStart=${TAILSCALE_BIN} funnel ${PORT}
Restart=always
RestartSec=5s

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload >/dev/null 2>&1 || true
    systemctl enable --now tailscale-funnel-potato >/dev/null 2>&1 || true
else
    pkill -f "tailscale funnel" || true
    nohup ${TAILSCALE_BIN} funnel "${PORT}" >/tmp/tailscale_funnel.log 2>&1 &
fi

# Also trigger interactive background funnel in case daemon mode requires it
${TAILSCALE_BIN} funnel --bg "${PORT}" >/dev/null 2>&1 || ${TAILSCALE_BIN} funnel "${PORT}" on >/dev/null 2>&1 || true

# 4. Extract Tailscale node domain name
log "Resolving Tailscale public HTTPS domain..."
TS_DOMAIN=""
for i in $(seq 1 15); do
    if command -v jq &>/dev/null; then
        TS_DOMAIN=$(tailscale status --json 2>/dev/null | jq -r '.Self.DNSName // empty' 2>/dev/null | sed 's/\.$//' || true)
    elif command -v python3 &>/dev/null; then
        TS_DOMAIN=$(tailscale status --json 2>/dev/null | python3 -c "import sys, json; data=json.load(sys.stdin); print(data.get('Self', {}).get('DNSName', '').rstrip('.'))" 2>/dev/null || true)
    fi
    if [[ -n "${TS_DOMAIN}" ]]; then
        break
    fi
    sleep 1
done

if [[ -z "${TS_DOMAIN}" ]]; then
    TS_DOMAIN=$(tailscale status 2>/dev/null | head -n1 | awk '{print $2}' || true)
fi

# 5. Update .env file if available
PUBLIC_URL=""
if [[ -n "${TS_DOMAIN}" ]]; then
    PUBLIC_URL="https://${TS_DOMAIN}"
    if [[ -n "${DOMAIN_NAME}" ]]; then
        PUBLIC_URL="https://${DOMAIN_NAME}"
    fi

    if [[ -n "${ENV_FILE}" && -f "${ENV_FILE}" ]]; then
        log "Updating ${ENV_FILE} with PUBLIC_BASE_URL=${PUBLIC_URL}..."
        sed -i.bak '/^PUBLIC_BASE_URL=/d' "${ENV_FILE}" 2>/dev/null || true
        sed -i.bak '/^SESSION_SECURE_COOKIE=/d' "${ENV_FILE}" 2>/dev/null || true
        echo "PUBLIC_BASE_URL=${PUBLIC_URL}" >> "${ENV_FILE}"
        echo "SESSION_SECURE_COOKIE=true" >> "${ENV_FILE}"
        rm -f "${ENV_FILE}.bak" 2>/dev/null || true
        ok "Environment file updated."
    fi
fi

# 6. Display Completion Summary & GoDaddy Instructions
echo ""
echo -e "${GREEN}${BOLD}=============================================================================="
echo "                  🎉 TAILSCALE TUNNEL SETUP COMPLETE!                       "
echo "=============================================================================="
echo -e "${NC}"

if [[ -n "${TS_DOMAIN}" ]]; then
    echo -e "  📌 ${BOLD}Tailscale Funnel Domain:${NC} https://${TS_DOMAIN}"
    if [[ -n "${DOMAIN_NAME}" ]]; then
        echo -e "  🌐 ${BOLD}Custom Domain URL:${NC}       https://${DOMAIN_NAME}"
    fi
    echo ""
    echo -e "${CYAN}${BOLD}--- GoDaddy / Custom DNS Setup Instructions ---${NC}"
    echo -e "  1. Log into GoDaddy → Domain Portfolio → DNS Records."
    echo -e "  2. Add a ${BOLD}CNAME Record${NC}:"
    if [[ -n "${DOMAIN_NAME}" ]]; then
        SUBDOMAIN="${DOMAIN_NAME%%.*}"
        if [[ "${SUBDOMAIN}" == "${DOMAIN_NAME}" ]]; then
            SUBDOMAIN="api"
        fi
        echo -e "     • Type:  ${BOLD}CNAME${NC}"
        echo -e "     • Name:  ${BOLD}${SUBDOMAIN}${NC}"
        echo -e "     • Value: ${BOLD}${TS_DOMAIN}${NC}"
    else
        echo -e "     • Type:  ${BOLD}CNAME${NC}"
        echo -e "     • Name:  ${BOLD}api${NC}"
        echo -e "     • Value: ${BOLD}${TS_DOMAIN}${NC}"
    fi
    echo -e "     • TTL:   ${BOLD}1 Hour${NC}"
    echo ""
    echo -e "  ✅ ${BOLD}499 Mitigation:${NC} Zero Cloudflare proxy timeouts, zero 100s drops!"
else
    warn "Tunnel started, but could not detect Tailscale domain automatically."
    warn "Check 'tailscale funnel status' or 'tailscale status' to verify."
fi
echo "=============================================================================="
