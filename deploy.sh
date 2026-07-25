#!/usr/bin/env bash
# ==============================================================================
# Nimmakai (🍋) All-in-One Deployment & Setup Script for DigitalOcean / Linux
#
# Usage:
#   sudo bash deploy.sh
#   OR
#   curl -fsSL https://raw.githubusercontent.com/vskrch/Nimmakai/main/deploy.sh | sudo bash
# ==============================================================================
set -euo pipefail

# Visual colors
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m' # No Color

log() { echo -e "${CYAN}${BOLD}[Nimmakai Deploy]${NC} $1"; }
ok()  { echo -e "${GREEN}${BOLD}[SUCCESS]${NC} $1"; }
warn(){ echo -e "${YELLOW}${BOLD}[WARNING]${NC} $1"; }
err() { echo -e "${RED}${BOLD}[ERROR]${NC} $1"; exit 1; }

echo -e "${BOLD}"
echo "=============================================================================="
echo "                   🍋 NIMMAKAI (API GATEWAY) DEPLOYMENT                      "
echo "=============================================================================="
echo -e "${NC}"

# 1. Root Privilege Check
if [[ $EUID -ne 0 ]]; then
   err "This deployment script must be run as root. Try: sudo bash deploy.sh"
fi

INSTALL_DIR="/opt/nimmakai"
DOMAIN_NAME="${DOMAIN_NAME:-}"
PROXY_KEY="${PROXY_API_KEYS:-}"
ADMIN_PASS="${ADMIN_PASSWORD:-}"
NIM_KEY="${NIM_API_KEYS:-}"

# 2. System Dependency Installation
log "Installing dependencies (curl, git, openssl, jq, ca-certificates)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl git jq openssl ufw >/dev/null

# 3. Docker Engine & Compose Check
if ! command -v docker &>/dev/null; then
    log "Docker Engine not detected. Installing Docker via official script..."
    curl -fsSL https://get.docker.com | sh >/dev/null
    systemctl enable --now docker
fi

if ! docker compose version &>/dev/null; then
    log "Installing Docker Compose plugin..."
    apt-get install -y -qq docker-compose-plugin >/dev/null
fi
ok "Docker & Docker Compose are ready."

# 4. Clone / Prepare Workspace
if [[ ! -f "docker-compose.do.yml" ]]; then
    log "Cloning Nimmakai repository into ${INSTALL_DIR}..."
    rm -rf "${INSTALL_DIR}"
    git clone --depth 1 https://github.com/vskrch/Nimmakai.git "${INSTALL_DIR}"
    cd "${INSTALL_DIR}"
fi

# 5. Interactive / Default Secret Generation
if [[ -z "${PROXY_KEY}" ]]; then
    RAND_KEY=$(openssl rand -hex 16)
    PROXY_KEY="sk-nimmakai-${RAND_KEY}"
fi

if [[ -z "${ADMIN_PASS}" ]]; then
    ADMIN_PASS=$(openssl rand -hex 12)
fi

log "Writing production configuration (.env)..."
cat <<EOF > .env
PROXY_API_KEYS=${PROXY_KEY}
ALLOW_INSECURE_AUTH=false
SQLITE_SEED_FREE_PRESETS=true
ANALYTICS_ENABLED=true
ROUTING_ENABLED=true
ADMIN_PASSWORD=${ADMIN_PASS}
NIM_API_KEYS=${NIM_KEY}
HOST=0.0.0.0
PORT=8080
EOF
chmod 600 .env
ok "Generated production credentials saved to .env"

# 6. Build & Launch Docker Container
log "Building and starting Nimmakai Gateway container..."
docker compose -f docker-compose.do.yml up -d --build

# 7. Health Check Verification Loop
log "Waiting for Nimmakai container healthcheck (/ready)..."
READY=0
for i in $(seq 1 60); do
    if curl -fsS http://127.0.0.1:8080/ready &>/dev/null; then
        READY=1
        break
    fi
    sleep 2
done

if [[ ${READY} -ne 1 ]]; then
    warn "Nimmakai did not respond on /ready within 120s. Checking logs:"
    docker compose -f docker-compose.do.yml logs --tail=50
    err "Deployment health check failed."
fi
ok "Nimmakai Gateway container is running and HEALTHY!"

# 8. Firewall Configuration
log "Configuring UFW firewall rules (22, 80, 443)..."
ufw allow 22/tcp >/dev/null 2>&1 || true
ufw allow 80/tcp >/dev/null 2>&1 || true
ufw allow 443/tcp >/dev/null 2>&1 || true
ufw --force enable >/dev/null 2>&1 || true

# 9. Automatic SSL / Caddy Setup (Optional / Auto)
PUBLIC_IP=$(curl -fsS http://checkip.amazonaws.com 2>/dev/null || curl -fsS http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address 2>/dev/null || echo "localhost")

if [[ -n "${DOMAIN_NAME}" ]]; then
    log "Configuring Caddy for automatic Let's Encrypt SSL on ${DOMAIN_NAME}..."
    if ! command -v caddy &>/dev/null; then
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
        apt-get update -qq && apt-get install -y -qq caddy >/dev/null
    fi
    cat <<EOF > /etc/caddy/Caddyfile
${DOMAIN_NAME} {
    reverse_proxy 127.0.0.1:8080
}
EOF
    systemctl reload caddy || systemctl restart caddy
    PUBLIC_URL="https://${DOMAIN_NAME}"
else
    PUBLIC_URL="http://${PUBLIC_IP}"
fi

# 10. Completion Summary Display
echo -e "\n${GREEN}${BOLD}"
echo "=============================================================================="
echo "                 🎉 NIMMAKAI DEPLOYMENT COMPLETE!                             "
echo "=============================================================================="
echo -e "${NC}"
echo -e "🔗 ${BOLD}Dashboard URL:${NC}       ${PUBLIC_URL}/dashboard"
echo -e "🔑 ${BOLD}Admin Password:${NC}      ${ADMIN_PASS}"
echo -e "⚡ ${BOLD}OpenAI API Base:${NC}     ${PUBLIC_URL}/v1"
echo -e "💬 ${BOLD}Anthropic API Base:${NC}  ${PUBLIC_URL}/v1"
echo -e "🔑 ${BOLD}Proxy API Key:${NC}       ${PROXY_KEY}"
echo -e "🎯 ${BOLD}Default Model:${NC}       nimmakai/auto"
echo "------------------------------------------------------------------------------"
echo -e "${BOLD}Integration Snippets:${NC}"
echo
echo "1. Cursor IDE / OpenAI Compatible:"
echo "   Base URL: ${PUBLIC_URL}/v1"
echo "   API Key:  ${PROXY_KEY}"
echo "   Model:    nimmakai/auto"
echo
echo "2. Claude Code CLI:"
echo "   export ANTHROPIC_BASE_URL=\"${PUBLIC_URL}/v1\""
echo "   export ANTHROPIC_API_KEY=\"${PROXY_KEY}\""
echo "   claude"
echo "=============================================================================="
