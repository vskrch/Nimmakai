#!/usr/bin/env bash
# ==============================================================================
# Potato (🥔) All-in-One Deployment & Setup Script
# Cross-Platform: Linux (Debian, Ubuntu, Mint, Fedora, CentOS, Arch) & macOS
#
# Usage:
#   Linux: sudo bash deploy.sh
#   macOS: bash deploy.sh
# ==============================================================================
set -euo pipefail

# Visual colors
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m' # No Color

log() { echo -e "${CYAN}${BOLD}[Potato Deploy]${NC} $1"; }
ok()  { echo -e "${GREEN}${BOLD}[SUCCESS]${NC} $1"; }
warn(){ echo -e "${YELLOW}${BOLD}[WARNING]${NC} $1"; }
err() { echo -e "${RED}${BOLD}[ERROR]${NC} $1"; exit 1; }

echo -e "${BOLD}"
echo "=============================================================================="
echo "                   🥔 POTATO (API GATEWAY) DEPLOYMENT                      "
echo "=============================================================================="
echo -e "${NC}"

OS="$(uname -s)"
# 1. OS-Specific Privileges Check
if [[ "$OS" == "Linux" ]]; then
    if [[ $EUID -ne 0 ]]; then
        err "On Linux, this deployment script must be run as root. Try: sudo bash deploy.sh"
    fi
elif [[ "$OS" == "Darwin" ]]; then
    if [[ $EUID -eq 0 ]]; then
        warn "Running as root on macOS is not recommended (Homebrew may fail). Proceeding, but you may encounter issues."
    fi
else
    warn "Unsupported OS detected: $OS. Attempting to proceed anyway..."
fi

INSTALL_DIR="${INSTALL_DIR:-/opt/potato}"
if [[ "$OS" == "Darwin" ]]; then
    # On macOS, use a user-local directory if they can't write to /opt
    if [[ ! -w "/opt" && $EUID -ne 0 ]]; then
        INSTALL_DIR="${HOME}/.potato"
        log "Using local install dir on macOS: ${INSTALL_DIR}"
    fi
fi

# CLI Argument Parsing
ENABLE_CLOUDFLARE_TUNNEL="${ENABLE_CLOUDFLARE_TUNNEL:-false}"
CLOUDFLARE_TUNNEL_TOKEN="${CLOUDFLARE_TUNNEL_TOKEN:-}"

for arg in "$@"; do
    case $arg in
        --tunnel|-t)
            ENABLE_CLOUDFLARE_TUNNEL="true"
            ;;
        --token=*)
            CLOUDFLARE_TUNNEL_TOKEN="${arg#*=}"
            ENABLE_CLOUDFLARE_TUNNEL="true"
            ;;
        --domain=*)
            DOMAIN_NAME="${arg#*=}"
            ;;
    esac
done

DOMAIN_NAME="${DOMAIN_NAME:-}"
PROXY_KEY="${PROXY_API_KEYS:-}"
ADMIN_PASS="${ADMIN_PASSWORD:-}"
ADMIN_EMAIL_ADDR="${ADMIN_EMAIL:-admin@localhost}"
NIM_KEY="${NIM_API_KEYS:-}"
# All provider env keys that deploy.sh must preserve across redeploys.
_PROVIDER_ENV_VARS=(
    NIM_API_KEYS
    GROQ_API_KEYS
    CEREBRAS_API_KEYS
    OPENROUTER_API_KEYS
    GEMINI_API_KEYS
    TOGETHER_API_KEYS
    FIREWORKS_API_KEYS
    SAMBANOVA_API_KEYS
    DEEPSEEK_API_KEYS
    DEEPINFRA_API_KEYS
    GITHUB_MODELS_API_KEYS
    MISTRAL_API_KEYS
    HYPERBOLIC_API_KEYS
    OLLAMA_CLOUD_API_KEYS
    OPENCODE_ZEN_API_KEYS
    OPENCODE_API_KEYS
    OPENCODE_GO_API_KEYS
)

# 2. Package Manager Detection & Dependency Installation
detect_pm() {
    if command -v apt-get &>/dev/null; then echo "apt";
    elif command -v dnf &>/dev/null; then echo "dnf";
    elif command -v yum &>/dev/null; then echo "yum";
    elif command -v pacman &>/dev/null; then echo "pacman";
    elif command -v brew &>/dev/null; then echo "brew";
    else echo "unknown";
    fi
}
PM=$(detect_pm)
log "Detected Package Manager: ${PM}"

install_deps() {
    log "Installing dependencies (curl, git, openssl, jq, ca-certificates)..."
    case $PM in
        apt)
            export DEBIAN_FRONTEND=noninteractive
            apt-get update -qq
            apt-get install -y -qq ca-certificates curl git jq openssl >/dev/null
            ;;
        dnf|yum)
            $PM install -y -q ca-certificates curl git jq openssl >/dev/null
            ;;
        pacman)
            pacman -Sy --noconfirm --needed ca-certificates curl git jq openssl >/dev/null
            ;;
        brew)
            brew install curl git openssl jq >/dev/null
            ;;
        *)
            warn "Could not auto-install dependencies. Please ensure curl, git, openssl, and jq are installed."
            ;;
    esac
}
install_deps

# 3. Docker Engine & Compose Check
if ! command -v docker &>/dev/null; then
    if [[ "$OS" == "Darwin" ]]; then
        err "Docker is not installed. Please install Docker Desktop for macOS: https://docs.docker.com/desktop/install/mac-install/"
    else
        log "Docker Engine not detected. Installing Docker via official script..."
        curl -fsSL https://get.docker.com | sh >/dev/null
        systemctl enable --now docker
    fi
fi

if ! docker compose version &>/dev/null; then
    if [[ "$OS" == "Linux" ]]; then
        log "Installing Docker Compose plugin..."
        if [[ "$PM" == "apt" ]]; then
            apt-get install -y -qq docker-compose-plugin >/dev/null
        elif [[ "$PM" == "dnf" || "$PM" == "yum" ]]; then
            $PM install -y -q docker-compose-plugin >/dev/null
        fi
    else
        warn "Docker compose command not found. Ensure Docker Desktop is fully installed."
    fi
fi

if ! docker info &>/dev/null; then
    err "Docker daemon is not running. Please start Docker and try again."
fi

ok "Docker & Docker Compose are ready."

# 4. Clone / Prepare Workspace
if [[ ! -f "docker-compose.do.yml" ]]; then
    log "Preparing workspace at ${INSTALL_DIR}..."
    if [[ ! -d "${INSTALL_DIR}" ]]; then
        mkdir -p "${INSTALL_DIR}"
    fi
    if [[ ! -d "${INSTALL_DIR}/.git" ]]; then
        log "Cloning Potato repository into ${INSTALL_DIR}..."
        rm -rf "${INSTALL_DIR:?}/"* || true
        git clone --depth 1 https://github.com/vskrch/potato-gateway.git "${INSTALL_DIR}"
        cd "${INSTALL_DIR}"
    else
        log "Repository exists at ${INSTALL_DIR}. Updating to latest release..."
        cd "${INSTALL_DIR}"
        git fetch origin main >/dev/null 2>&1 || true
        git reset --hard origin/main >/dev/null 2>&1 || true
    fi
else
    if [[ -d ".git" ]]; then
        log "Updating current workspace to latest release..."
        git fetch origin main >/dev/null 2>&1 || true
        git reset --hard origin/main >/dev/null 2>&1 || true
    fi
fi

# 5. Secret Preservation & Generation
#    Capture every provider key from the existing .env so redeploy never
#    wipes keys the user set via the admin UI or .env directly.
declare -A SAVED_PROVIDER_KEYS
if [[ -f .env ]]; then
    log "Existing .env configuration found. Preserving your credentials..."
    if [[ -z "${PROXY_KEY}" ]]; then
        PROXY_KEY=$(grep -E "^PROXY_API_KEYS=" .env | cut -d'=' -f2- || true)
    fi
    if [[ -z "${ADMIN_PASS}" ]]; then
        ADMIN_PASS=$(grep -E "^ADMIN_PASSWORD=" .env | cut -d'=' -f2- || true)
    fi
    if [[ "${ADMIN_EMAIL_ADDR}" == "admin@localhost" ]]; then
        ADMIN_EMAIL_ADDR=$(grep -E "^ADMIN_EMAIL=" .env | cut -d'=' -f2- || true)
        ADMIN_EMAIL_ADDR="${ADMIN_EMAIL_ADDR:-admin@localhost}"
    fi
    if [[ -z "${NIM_KEY}" ]]; then
        NIM_KEY=$(grep -E "^NIM_API_KEYS=" .env | cut -d'=' -f2- || true)
    fi
    if [[ "${ENABLE_CLOUDFLARE_TUNNEL}" == "false" ]]; then
        ENABLE_CLOUDFLARE_TUNNEL=$(grep -E "^ENABLE_CLOUDFLARE_TUNNEL=" .env | cut -d'=' -f2- || echo "false")
    fi
    if [[ -z "${CLOUDFLARE_TUNNEL_TOKEN}" ]]; then
        CLOUDFLARE_TUNNEL_TOKEN=$(grep -E "^CLOUDFLARE_TUNNEL_TOKEN=" .env | cut -d'=' -f2- || true)
    fi
    # Preserve every provider env var present in the old .env.
    for var in "${_PROVIDER_ENV_VARS[@]}"; do
        val=$(grep -E "^${var}=" .env | cut -d'=' -f2- || true)
        if [[ -n "${val}" ]]; then
            SAVED_PROVIDER_KEYS["${var}"]="${val}"
        fi
    done
fi

if [[ -z "${PROXY_KEY}" ]]; then
    RAND_KEY=$(openssl rand -hex 16)
    PROXY_KEY="sk-potato-${RAND_KEY}"
fi

if [[ -z "${ADMIN_PASS}" ]]; then
    ADMIN_PASS=$(openssl rand -hex 12)
fi

log "Writing production configuration (.env)..."
{
    echo "PROXY_API_KEYS=${PROXY_KEY}"
    echo "ALLOW_INSECURE_AUTH=false"
    echo "SQLITE_SEED_FREE_PRESETS=true"
    echo "ANALYTICS_ENABLED=true"
    echo "ROUTING_ENABLED=true"
    echo "ADMIN_PASSWORD=${ADMIN_PASS}"
    echo "ADMIN_EMAIL=${ADMIN_EMAIL_ADDR}"
    echo "ENABLE_CLOUDFLARE_TUNNEL=${ENABLE_CLOUDFLARE_TUNNEL}"
    echo "CLOUDFLARE_TUNNEL_TOKEN=${CLOUDFLARE_TUNNEL_TOKEN}"
    echo "NIM_API_KEYS=${NIM_KEY}"
    echo "HOST=0.0.0.0"
    echo "PORT=8080"
    # Write every preserved provider key (Groq, Cerebras, OpenRouter, ...).
    for var in "${_PROVIDER_ENV_VARS[@]}"; do
        [[ "${var}" == "NIM_API_KEYS" ]] && continue
        val="${SAVED_PROVIDER_KEYS[$var]:-}"
        [[ -z "${val}" ]] && val="${!var:-}"
        if [[ -n "${val}" ]]; then
            echo "${var}=${val}"
        fi
    done
} > .env
chmod 600 .env
ok "Production credentials configured in .env"

# 6. Build & Launch Docker Container
log "Building and starting Potato Gateway container..."
docker compose -f docker-compose.do.yml up -d --build

# 7. Health Check Verification Loop
log "Waiting for Potato container healthcheck (/ready or /health)..."
READY=0
HEALTHY=0
for i in $(seq 1 15); do
    if curl -fsS http://127.0.0.1:8080/ready &>/dev/null; then
        READY=1
        HEALTHY=1
        break
    fi
    sleep 2
done

if [[ ${READY} -eq 0 ]]; then
    # /ready failed (likely no upstream API keys set yet). Test basic /health endpoint.
    for i in $(seq 1 15); do
        if curl -fsS http://127.0.0.1:8080/health &>/dev/null; then
            HEALTHY=1
            break
        fi
        sleep 2
    done
fi

if [[ ${HEALTHY} -ne 1 ]]; then
    warn "Potato container did not respond on /ready or /health within 60s. Checking logs:"
    docker compose -f docker-compose.do.yml logs --tail=50
    warn "Your generated credentials are saved in .env:"
    warn "  ADMIN_PASSWORD=${ADMIN_PASS}"
    warn "  PROXY_API_KEYS=${PROXY_KEY}"
    err "Deployment health check failed."
fi

if [[ ${READY} -eq 1 ]]; then
    ok "Potato Gateway container is running and HEALTHY!"
else
    warn "Potato Gateway container is RUNNING in DEGRADED mode (no upstream API keys configured)."
    warn "Log into the Web Dashboard at /dashboard using your Admin Password to add provider keys!"
fi

# 8. Firewall Configuration (Linux Only)
if [[ "$OS" == "Linux" ]]; then
    log "Configuring firewall rules (22, 80, 443, 8080)..."
    if command -v ufw &>/dev/null; then
        ufw allow 22/tcp >/dev/null 2>&1 || true
        ufw allow 80/tcp >/dev/null 2>&1 || true
        ufw allow 443/tcp >/dev/null 2>&1 || true
        ufw allow 8080/tcp >/dev/null 2>&1 || true
        ufw --force enable >/dev/null 2>&1 || true
        log "UFW configured."
    elif command -v firewall-cmd &>/dev/null; then
        firewall-cmd --permanent --add-port=22/tcp >/dev/null 2>&1 || true
        firewall-cmd --permanent --add-port=80/tcp >/dev/null 2>&1 || true
        firewall-cmd --permanent --add-port=443/tcp >/dev/null 2>&1 || true
        firewall-cmd --permanent --add-port=8080/tcp >/dev/null 2>&1 || true
        firewall-cmd --reload >/dev/null 2>&1 || true
        log "firewalld configured."
    else
        warn "No supported firewall manager found (ufw/firewalld). Skipping firewall config."
    fi
else
    log "Skipping firewall configuration on macOS/Non-Linux."
fi

# 9. Automatic Reverse Proxy / Caddy Setup
PUBLIC_IP=$(curl -fsS http://checkip.amazonaws.com 2>/dev/null || curl -fsS http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address 2>/dev/null || echo "127.0.0.1")

log "Setting up reverse proxy (Caddy)..."
if ! command -v caddy &>/dev/null; then
    case $PM in
        apt)
            apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https >/dev/null 2>&1 || true
            curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg >/dev/null 2>&1 || true
            curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null 2>&1 || true
            apt-get update -qq >/dev/null 2>&1 || true
            apt-get install -y -qq caddy >/dev/null 2>&1 || true
            ;;
        dnf)
            dnf install -y 'dnf-command(copr)' >/dev/null 2>&1 || true
            dnf copr enable -y @caddy/caddy >/dev/null 2>&1 || true
            dnf install -y caddy >/dev/null 2>&1 || true
            ;;
        yum)
            yum install -y yum-plugin-copr >/dev/null 2>&1 || true
            yum copr enable -y @caddy/caddy >/dev/null 2>&1 || true
            yum install -y caddy >/dev/null 2>&1 || true
            ;;
        pacman)
            pacman -Sy --noconfirm caddy >/dev/null 2>&1 || true
            ;;
        brew)
            brew install caddy >/dev/null 2>&1 || true
            ;;
        *)
            warn "Could not automatically install Caddy using ${PM}."
            ;;
    esac
fi

# Determine Caddyfile location
if [[ "$OS" == "Darwin" ]]; then
    CADDY_FILE="/usr/local/etc/Caddyfile"
    if command -v brew &>/dev/null && [[ -d "$(brew --prefix 2>/dev/null)/etc" ]]; then
        CADDY_FILE="$(brew --prefix)/etc/Caddyfile"
    fi
else
    CADDY_FILE="/etc/caddy/Caddyfile"
fi

if [[ -n "${DOMAIN_NAME}" ]]; then
    CADDY_SERVER_NAME="${DOMAIN_NAME}"
    PUBLIC_URL="https://${DOMAIN_NAME}"
else
    CADDY_SERVER_NAME=":80"
    PUBLIC_URL="http://${PUBLIC_IP}"
fi

if command -v caddy &>/dev/null; then
    cat <<EOF > "${CADDY_FILE}"
${CADDY_SERVER_NAME} {
    reverse_proxy 127.0.0.1:8080
}
EOF
    if command -v systemctl &>/dev/null; then
        systemctl enable --now caddy >/dev/null 2>&1 || true
        systemctl reload caddy >/dev/null 2>&1 || systemctl restart caddy >/dev/null 2>&1 || true
    elif command -v brew &>/dev/null && [[ "$OS" == "Darwin" ]]; then
        brew services restart caddy >/dev/null 2>&1 || true
    else
        caddy start --config "${CADDY_FILE}" >/dev/null 2>&1 || true
    fi
else
    if [[ -z "${DOMAIN_NAME}" ]]; then
        PUBLIC_URL="http://${PUBLIC_IP}:8080"
    fi
fi

# 9.5 Self-Healing Cloudflare Tunnel Setup (if enabled or --tunnel flag)
setup_cloudflare_tunnel() {
    log "Configuring self-healing Cloudflare Tunnel..."
    ARCH="$(uname -m)"
    case "${ARCH}" in
        x86_64|amd64) CF_ARCH="amd64" ;;
        aarch64|arm64) CF_ARCH="arm64" ;;
        *) CF_ARCH="amd64" ;;
    esac

    if ! command -v cloudflared &>/dev/null; then
        log "Installing cloudflared daemon..."
        if [[ "$OS" == "Linux" ]]; then
            if [[ "$PM" == "apt" ]]; then
                curl -L "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${CF_ARCH}.deb" -o /tmp/cloudflared.deb >/dev/null 2>&1 || true
                if [[ -f /tmp/cloudflared.deb ]]; then
                    dpkg -i /tmp/cloudflared.deb >/dev/null 2>&1 || true
                    rm -f /tmp/cloudflared.deb
                fi
            fi
            if ! command -v cloudflared &>/dev/null; then
                curl -L "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${CF_ARCH}" -o /usr/local/bin/cloudflared >/dev/null 2>&1 || true
                chmod +x /usr/local/bin/cloudflared || true
            fi
        elif [[ "$OS" == "Darwin" ]]; then
            if command -v brew &>/dev/null; then
                brew install cloudflared >/dev/null 2>&1 || true
            else
                curl -L "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-${CF_ARCH}.tgz" -o /tmp/cloudflared.tgz >/dev/null 2>&1 || true
                if [[ -f /tmp/cloudflared.tgz ]]; then
                    tar -xzf /tmp/cloudflared.tgz -C /usr/local/bin >/dev/null 2>&1 || true
                    chmod +x /usr/local/bin/cloudflared 2>/dev/null || true
                    rm -f /tmp/cloudflared.tgz
                fi
            fi
        fi
    fi

    if ! command -v cloudflared &>/dev/null; then
        warn "cloudflared binary installation failed. Falling back to default URL: ${PUBLIC_URL}"
        return
    fi
    CLOUDFLARED_BIN="$(command -v cloudflared || echo /usr/local/bin/cloudflared)"

    if [[ -n "${CLOUDFLARE_TUNNEL_TOKEN}" ]]; then
        log "Provisioning persistent Cloudflare Token Tunnel service..."
        if command -v systemctl &>/dev/null; then
            cat <<EOF > /etc/systemd/system/cloudflared-potato.service
[Unit]
Description=Potato Gateway Cloudflare Tunnel Daemon (Token)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
ExecStart=${CLOUDFLARED_BIN} tunnel --protocol http2 run --token ${CLOUDFLARE_TUNNEL_TOKEN}
Restart=always
RestartSec=5s

[Install]
WantedBy=multi-user.target
EOF
            systemctl daemon-reload >/dev/null 2>&1 || true
            systemctl enable --now cloudflared-potato >/dev/null 2>&1 || true
        fi
    else
        log "Provisioning Cloudflare Quick Tunnel (http2 fallback, auto-reconnect)..."
        if command -v systemctl &>/dev/null; then
            cat <<EOF > /etc/systemd/system/cloudflared-potato.service
[Unit]
Description=Potato Gateway Cloudflare Quick Tunnel Daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
ExecStart=${CLOUDFLARED_BIN} tunnel --protocol http2 --metrics 127.0.0.1:45678 --url http://127.0.0.1:8080
Restart=always
RestartSec=5s

[Install]
WantedBy=multi-user.target
EOF
            systemctl daemon-reload >/dev/null 2>&1 || true
            systemctl enable --now cloudflared-potato >/dev/null 2>&1 || true
        else
            pkill -f "cloudflared tunnel" || true
            nohup "${CLOUDFLARED_BIN}" tunnel --protocol http2 --metrics 127.0.0.1:45678 --url http://127.0.0.1:8080 >/tmp/cloudflared.log 2>&1 &
        fi
    fi

    log "Extracting active Cloudflare HTTPS Tunnel endpoint..."
    TUNNEL_URL=""
    for i in $(seq 1 15); do
        TUNNEL_URL=$(curl -s http://127.0.0.1:45678/quicktunnel 2>/dev/null | jq -r '.hostname // empty' 2>/dev/null || true)
        if [[ -n "${TUNNEL_URL}" ]]; then
            TUNNEL_URL="https://${TUNNEL_URL}"
            break
        fi
        if command -v journalctl &>/dev/null; then
            TUNNEL_URL=$(journalctl -u cloudflared-potato --no-pager -n 40 2>/dev/null | grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' | tail -n1 || true)
        fi
        if [[ -z "${TUNNEL_URL}" && -f /tmp/cloudflared.log ]]; then
            TUNNEL_URL=$(grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' /tmp/cloudflared.log | tail -n1 || true)
        fi
        if [[ -n "${TUNNEL_URL}" ]]; then
            break
        fi
        sleep 1
    done

    if [[ -n "${TUNNEL_URL}" ]]; then
        PUBLIC_URL="${TUNNEL_URL}"
        ok "Cloudflare HTTPS Tunnel active & self-healed: ${PUBLIC_URL}"
        if [[ -f .env ]]; then
            sed -i.bak '/^PUBLIC_BASE_URL=/d' .env 2>/dev/null || true
            sed -i.bak '/^SESSION_SECURE_COOKIE=/d' .env 2>/dev/null || true
            echo "PUBLIC_BASE_URL=${PUBLIC_URL}" >> .env
            echo "SESSION_SECURE_COOKIE=true" >> .env
            rm -f .env.bak 2>/dev/null || true
        fi
    else
        warn "Cloudflare Tunnel service launched, but dynamic URL extraction timed out."
    fi
}

if [[ "${ENABLE_CLOUDFLARE_TUNNEL}" == "true" || -n "${CLOUDFLARE_TUNNEL_TOKEN}" ]]; then
    setup_cloudflare_tunnel
fi

# 10. Completion Summary Display
if [[ ${READY} -eq 1 ]]; then
    echo -e "\n${GREEN}${BOLD}"
    echo "=============================================================================="
    echo "                 🎉 POTATO DEPLOYMENT COMPLETE (HEALTHY)!                     "
    echo "=============================================================================="
    echo -e "${NC}"
else
    echo -e "\n${YELLOW}${BOLD}"
    echo "=============================================================================="
    echo "         ⚠️  POTATO RUNNING IN DEGRADED MODE (NO PROVIDER KEYS SET)          "
    echo "=============================================================================="
    echo -e "${NC}"
    echo -e "👉 ${YELLOW}Next Step:${NC} Log into ${BOLD}${PUBLIC_URL}/dashboard${NC} using your Admin Password below"
    echo -e "   and add provider keys under ${BOLD}Providers${NC} to start routing requests!"
    echo "------------------------------------------------------------------------------"
fi
echo -e "🔗 ${BOLD}Dashboard URL:${NC}       ${PUBLIC_URL}/dashboard"
echo -e "🔑 ${BOLD}Dashboard Login${NC} (${BOLD}Sign In${NC} tab):"
echo -e "   ${BOLD}Email:${NC}      ${ADMIN_EMAIL_ADDR}"
echo -e "   ${BOLD}Password:${NC}   ${ADMIN_PASS}"
echo -e "🔑 ${BOLD}API Key Direct${NC} (${BOLD}API Key Direct${NC} tab):"
echo -e "   ${BOLD}API Key:${NC}    ${PROXY_KEY}"
echo -e "⚡ ${BOLD}OpenAI API Base:${NC}     ${PUBLIC_URL}/v1"
echo -e "💬 ${BOLD}Anthropic API Base:${NC}  ${PUBLIC_URL}/v1"
echo -e "🎯 ${BOLD}Default Model:${NC}       potato/auto"
echo "------------------------------------------------------------------------------"
echo -e "${BOLD}Integration Snippets:${NC}"
echo
echo "1. Cursor IDE / OpenAI Compatible:"
echo "   Base URL: ${PUBLIC_URL}/v1"
echo "   API Key:  ${PROXY_KEY}"
echo "   Model:    potato/auto"
echo
echo "2. Claude Code CLI:"
echo "   export ANTHROPIC_BASE_URL=\"${PUBLIC_URL}/v1\""
echo "   export ANTHROPIC_API_KEY=\"${PROXY_KEY}\""
echo "   claude"
echo "=============================================================================="
