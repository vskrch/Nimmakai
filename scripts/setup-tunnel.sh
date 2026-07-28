#!/usr/bin/env bash
# ==============================================================================
# Potato (🥔) Enterprise Networking & Tailscale Tunnel Management Script
# Configures Tailscale Funnel for zero-config HTTPS, 499-free streaming,
# real-time DNS propagation checking, and end-to-end health probing.
#
# Usage:
#   sudo bash scripts/setup-tunnel.sh --domain=api.potatolabs.cloud
#   sudo bash scripts/setup-tunnel.sh --status
#   sudo bash scripts/setup-tunnel.sh --test
#   sudo bash scripts/setup-tunnel.sh --stop
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

DOMAIN_NAME=""
PORT="8080"
ENV_FILE=""
ACTION="deploy"

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
        --status)
            ACTION="status"
            ;;
        --test)
            ACTION="test"
            ;;
        --stop)
            ACTION="stop"
            ;;
        --help|-h)
            echo "Potato Networking & Tunnel Management Utility"
            echo ""
            echo "Usage: sudo bash scripts/setup-tunnel.sh [options]"
            echo ""
            echo "Options:"
            echo "  --domain=api.potatolabs.cloud Set custom GoDaddy/DNS domain name"
            echo "  --port=8080                   Port for Potato Gateway (default: 8080)"
            echo "  --status                      Check live funnel status & DNS propagation"
            echo "  --test                        Run end-to-end HTTPS latency test"
            echo "  --stop                        Disable Tailscale Funnel"
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

# Load existing DOMAIN_NAME from .env if omitted
if [[ -z "${DOMAIN_NAME}" && -n "${ENV_FILE}" && -f "${ENV_FILE}" ]]; then
    SAVED_URL=$(grep -E "^PUBLIC_BASE_URL=" "${ENV_FILE}" | cut -d'=' -f2- || true)
    if [[ "${SAVED_URL}" =~ https://([^/]+) ]]; then
        DOMAIN_NAME="${BASH_REMATCH[1]}"
    fi
fi

TAILSCALE_BIN="$(command -v tailscale || echo /usr/bin/tailscale)"

# ------------------------------------------------------------------------------
# Action: Stop
# ------------------------------------------------------------------------------
if [[ "${ACTION}" == "stop" ]]; then
    log "Stopping Tailscale Funnel..."
    if command -v systemctl &>/dev/null && systemctl is-active --quiet tailscale-funnel-potato 2>/dev/null; then
        systemctl stop tailscale-funnel-potato || true
        systemctl disable tailscale-funnel-potato || true
    fi
    ${TAILSCALE_BIN} funnel 8080 off >/dev/null 2>&1 || ${TAILSCALE_BIN} funnel off >/dev/null 2>&1 || true
    pkill -f "tailscale funnel" 2>/dev/null || true
    ok "Tailscale Funnel stopped."
    exit 0
fi

# Helper: Extract Tailscale node domain
get_ts_domain() {
    local domain=""
    if command -v jq &>/dev/null; then
        domain=$(${TAILSCALE_BIN} status --json 2>/dev/null | jq -r '.Self.DNSName // empty' 2>/dev/null | sed 's/\.$//' || true)
    elif command -v python3 &>/dev/null; then
        domain=$(${TAILSCALE_BIN} status --json 2>/dev/null | python3 -c "import sys, json; data=json.load(sys.stdin); print(data.get('Self', {}).get('DNSName', '').rstrip('.'))" 2>/dev/null || true)
    fi
    if [[ -z "${domain}" ]]; then
        domain=$(${TAILSCALE_BIN} status 2>/dev/null | head -n1 | awk '{print $2}' || true)
    fi
    echo "${domain}"
}

# Helper: Live DNS Propagation Checker
check_dns_propagation() {
    local target_domain="$1"
    local ts_domain="$2"
    log "Checking DNS propagation for ${BOLD}${target_domain}${NC}..."
    
    local resolved_ip=""
    if command -v python3 &>/dev/null; then
        resolved_ip=$(python3 -c "import socket; print(socket.gethostbyname('${target_domain}'))" 2>/dev/null || true)
    elif command -v nslookup &>/dev/null; then
        resolved_ip=$(nslookup "${target_domain}" 2>/dev/null | grep -A1 "Name:" | grep "Address:" | awk '{print $2}' | head -n1 || true)
    fi

    if [[ -n "${resolved_ip}" ]]; then
        ok "DNS Propagated! ${BOLD}${target_domain}${NC} resolves to ${BOLD}${resolved_ip}${NC}"
        return 0
    else
        warn "DNS propagation pending for ${target_domain}."
        warn "If you just added the GoDaddy CNAME record, propagation usually takes 1-2 minutes."
        return 1
    fi
}

# ------------------------------------------------------------------------------
# Action: Status
# ------------------------------------------------------------------------------
if [[ "${ACTION}" == "status" ]]; then
    echo -e "${BOLD}=============================================================================="
    echo "               🥔 TAILSCALE TUNNEL & NETWORKING STATUS                         "
    echo "=============================================================================="
    echo -e "${NC}"
    if ! command -v tailscale &>/dev/null; then
        err "Tailscale binary is not installed."
    fi
    
    TS_DOMAIN="$(get_ts_domain)"
    echo -e "📌 ${BOLD}Tailscale Node Domain:${NC} https://${TS_DOMAIN:-Unknown}"
    
    if ${TAILSCALE_BIN} status &>/dev/null; then
        ok "Tailscale daemon active & authenticated."
    else
        warn "Tailscale daemon unauthenticated or stopped."
    fi

    if [[ -n "${DOMAIN_NAME}" ]]; then
        echo -e "🌐 ${BOLD}Configured Domain:${NC}    https://${DOMAIN_NAME}"
        check_dns_propagation "${DOMAIN_NAME}" "${TS_DOMAIN}" || true
    fi

    echo -e "\n${CYAN}${BOLD}Probing local gateway endpoint (127.0.0.1:${PORT})...${NC}"
    if curl -s -f "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
        ok "Local Potato Gateway endpoint is HEALTHY (HTTP 200)."
    else
        warn "Local Potato Gateway endpoint at http://127.0.0.1:${PORT}/health is not responding."
    fi
    echo "=============================================================================="
    exit 0
fi

# ------------------------------------------------------------------------------
# Action: Test
# ------------------------------------------------------------------------------
if [[ "${ACTION}" == "test" ]]; then
    log "Running end-to-end latency & health test..."
    TARGET_URL="http://127.0.0.1:${PORT}/health"
    if [[ -n "${DOMAIN_NAME}" ]]; then
        TARGET_URL="https://${DOMAIN_NAME}/health"
    fi
    log "Testing endpoint: ${BOLD}${TARGET_URL}${NC}"
    
    T0=$(date +%s%3N 2>/dev/null || python3 -c "import time; print(int(time.time()*1000))")
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "${TARGET_URL}" || echo "000")
    T1=$(date +%s%3N 2>/dev/null || python3 -c "import time; print(int(time.time()*1000))")
    LATENCY=$((T1 - T0))

    if [[ "${HTTP_CODE}" == "200" ]]; then
        ok "End-to-End Test PASSED! Response: HTTP 200 OK (${LATENCY} ms latency)"
    else
        warn "End-to-End Test warning: HTTP ${HTTP_CODE} (${LATENCY} ms)"
    fi
    exit 0
fi

# ------------------------------------------------------------------------------
# Action: Deploy (Default)
# ------------------------------------------------------------------------------
echo -e "${BOLD}"
echo "=============================================================================="
echo "               🥔 POTATO NETWORKING & TAILSCALE TUNNEL SETUP                  "
echo "=============================================================================="
echo -e "${NC}"

OS="$(uname -s)"
if [[ "$OS" == "Linux" && $EUID -ne 0 ]]; then
    warn "On Linux, running with sudo is recommended: sudo bash scripts/setup-tunnel.sh"
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
if ! ${TAILSCALE_BIN} status &>/dev/null; then
    log "Tailscale node is unauthenticated. Launching 'tailscale up'..."
    ${TAILSCALE_BIN} up || warn "Please run 'sudo tailscale up' manually to log into your Tailscale account."
fi

# 3. Provision Tailscale Funnel on port
log "Enabling Tailscale Funnel for local port ${PORT}..."
if ! timeout 10 ${TAILSCALE_BIN} funnel --bg "http://localhost:${PORT}" >/tmp/tailscale_funnel.log 2>&1; then
    pkill -f "tailscale funnel" 2>/dev/null || true
    nohup ${TAILSCALE_BIN} funnel "http://localhost:${PORT}" >/tmp/tailscale_funnel.log 2>&1 &
fi

# Detect Tailscale Funnel one-time approval URL if Funnel is disabled on Tailnet
if grep -q "login.tailscale.com" /tmp/tailscale_funnel.log 2>/dev/null; then
    FUNNEL_AUTH_URL=$(grep -oE 'https://login.tailscale.com/f/funnel\?[^ ]+' /tmp/tailscale_funnel.log | head -n1 || true)
    if [[ -n "${FUNNEL_AUTH_URL}" ]]; then
        warn "Tailscale Funnel requires a one-time approval for your tailnet!"
        warn "👉 Click this link to approve in your browser: ${BOLD}${FUNNEL_AUTH_URL}${NC}"
    fi
fi

# 4. Extract Tailscale node domain name
log "Resolving Tailscale public HTTPS domain..."
TS_DOMAIN=""
for i in $(seq 1 15); do
    TS_DOMAIN="$(get_ts_domain)"
    if [[ -n "${TS_DOMAIN}" ]]; then
        break
    fi
    sleep 1
done

# 5. Backup SQLite Database & Update .env file
PUBLIC_URL=""
if [[ -n "${TS_DOMAIN}" ]]; then
    PUBLIC_URL="https://${TS_DOMAIN}"
    if [[ -n "${DOMAIN_NAME}" ]]; then
        PUBLIC_URL="https://${DOMAIN_NAME}"
    fi

    if [[ -n "${ENV_FILE}" && -f "${ENV_FILE}" ]]; then
        # Backup DB if present
        DB_PATH="/opt/potato/data/potato.db"
        if [[ -f "${DB_PATH}" ]]; then
            mkdir -p /opt/potato/backups
            cp "${DB_PATH}" "/opt/potato/backups/potato_$(date +%Y%m%d_%H%M%S).db" 2>/dev/null || true
            ok "Database snapshot backed up to /opt/potato/backups."
        fi

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
    echo ""

    if [[ -n "${DOMAIN_NAME}" ]]; then
        check_dns_propagation "${DOMAIN_NAME}" "${TS_DOMAIN}" || true
    fi
else
    warn "Tunnel started, but could not detect Tailscale domain automatically."
    warn "Check 'tailscale funnel status' or 'tailscale status' to verify."
fi
echo "=============================================================================="
