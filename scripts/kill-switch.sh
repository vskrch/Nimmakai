#!/usr/bin/env bash
# ==============================================================================
# Potato (🥔) All-in-One Kill Switch & Reset Utility
# Completely stops, disables, and nukes all Potato Gateway Docker containers,
# systemd services, background tunnel daemons, and temporary state.
#
# Usage:
#   sudo bash scripts/kill-switch.sh
#   sudo bash scripts/kill-switch.sh --force
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

log() { echo -e "${CYAN}${BOLD}[Kill Switch]${NC} $1"; }
ok()  { echo -e "${GREEN}${BOLD}[SUCCESS]${NC} $1"; }
warn(){ echo -e "${YELLOW}${BOLD}[WARNING]${NC} $1"; }
err() { echo -e "${RED}${BOLD}[ERROR]${NC} $1"; exit 1; }

FORCE="false"
for arg in "$@"; do
    case $arg in
        --force|-f)
            FORCE="true"
            ;;
        --help|-h)
            echo "Potato Gateway All-in-One Kill Switch Utility"
            echo ""
            echo "Usage: sudo bash scripts/kill-switch.sh [options]"
            echo ""
            echo "Options:"
            echo "  --force, -f    Skip interactive confirmation prompt"
            exit 0
            ;;
    esac
done

echo -e "${RED}${BOLD}"
echo "=============================================================================="
echo "               ⚠️  POTATO GATEWAY ALL-IN-ONE KILL SWITCH                       "
echo "=============================================================================="
echo -e "${NC}"

OS="$(uname -s)"
if [[ "$OS" == "Linux" && $EUID -ne 0 ]]; then
    err "On Linux, the kill switch must be run as root: sudo bash scripts/kill-switch.sh"
fi

if [[ "${FORCE}" != "true" ]]; then
    warn "This will forcibly stop and remove all Potato Gateway containers, services, and tunnels."
    read -rp "Are you sure you want to proceed? (y/N): " confirm
    if [[ ! "${confirm}" =~ ^[Yy]$ ]]; then
        log "Kill switch operation aborted by user."
        exit 0
    fi
fi

# 1. Stop & Remove systemd services
log "1/5 Stopping and removing systemd daemons..."
SERVICES=(
    "potato.service"
    "potato-gateway.service"
    "tailscale-funnel-potato.service"
    "cloudflared-potato.service"
)

if command -v systemctl &>/dev/null; then
    for service in "${SERVICES[@]}"; do
        if systemctl is-active --quiet "${service}" 2>/dev/null; then
            systemctl stop "${service}" 2>/dev/null || true
            log "Stopped ${service}"
        fi
        if systemctl is-enabled --quiet "${service}" 2>/dev/null; then
            systemctl disable "${service}" 2>/dev/null || true
        fi
        rm -f "/etc/systemd/system/${service}" 2>/dev/null || true
    done
    systemctl daemon-reload 2>/dev/null || true
    ok "Systemd services cleaned."
fi

# 2. Stop & Remove Docker containers and compose setups
log "2/5 Nuking Docker containers, networks, and volumes..."
if command -v docker &>/dev/null; then
    docker stop potato-gateway 2>/dev/null || true
    docker rm -f potato-gateway 2>/dev/null || true
    if [[ -f "docker-compose.do.yml" ]]; then
        docker compose -f docker-compose.do.yml down --volumes --remove-orphans 2>/dev/null || true
    fi
    docker container prune -f >/dev/null 2>&1 || true
    ok "Docker containers and volumes cleaned."
fi

# 3. Kill background processes
log "3/5 Terminating background process pools..."
pkill -9 -f "potato.main" 2>/dev/null || true
pkill -9 -f "uvicorn potato" 2>/dev/null || true
pkill -9 -f "cloudflared tunnel" 2>/dev/null || true
pkill -9 -f "tailscale funnel" 2>/dev/null || true
ok "Background processes terminated."

# 4. Clean reverse proxy configurations
log "4/5 Cleaning proxy configurations..."
if [[ -f "/etc/caddy/Caddyfile" ]]; then
    rm -f /etc/caddy/Caddyfile 2>/dev/null || true
    if command -v systemctl &>/dev/null && systemctl is-active --quiet caddy 2>/dev/null; then
        systemctl reload caddy 2>/dev/null || true
    fi
fi
ok "Proxy configurations reset."

# 5. Clean temporary log & lock files
log "5/5 Wiping temporary logs and lock artifacts..."
rm -f /tmp/cloudflared.log /tmp/cloudflared.deb /tmp/tailscale_funnel.log /tmp/potato*.log 2>/dev/null || true
ok "Temporary artifacts wiped."

echo ""
echo -e "${GREEN}${BOLD}=============================================================================="
echo "                 🎉 ALL POTATO GATEWAY SERVICES NUKED CLEAN!                   "
echo "=============================================================================="
echo -e "${NC}"
echo -e "  ✅ Containers:    All Docker instances stopped & removed"
echo -e "  ✅ Systemd:       All daemons uninstalled & reloaded"
echo -e "  ✅ Processes:     All background tasks killed"
echo -e "  ✅ Tunnels:       All Tailscale & Cloudflare tunnels terminated"
echo ""
echo -e "💡 ${BOLD}To redeploy cleanly anytime:${NC}"
echo -e "   sudo bash deploy.sh"
echo "=============================================================================="
