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

# Failsafe 1: Ensure system binary directories are in PATH
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/opt/homebrew/bin:${PATH:-}"

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
USE_DOCKER="${USE_DOCKER:-true}"
DOMAIN_NAME="${DOMAIN_NAME:-}"

for arg in "$@"; do
    case $arg in
        --domain=*)
            DOMAIN_NAME="${arg#*=}"
            ;;
        --native|-n)
            USE_DOCKER="false"
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

# Failsafe 2: DNS Resolution check
ensure_dns_resolution() {
    if ! curl -s --connect-timeout 3 https://raw.githubusercontent.com &>/dev/null; then
        if [[ "$OS" == "Linux" && -w "/etc/resolv.conf" ]]; then
            log "Configuring fallback DNS resolver (1.1.1.1)..."
            echo "nameserver 1.1.1.1" >> /etc/resolv.conf || true
            echo "nameserver 8.8.8.8" >> /etc/resolv.conf || true
        fi
    fi
}
ensure_dns_resolution

# Failsafe 3: Swapfile OOM Prevention for small RAM instances (< 1.5GB)
ensure_sufficient_memory() {
    if [[ "$OS" == "Linux" && $EUID -eq 0 ]]; then
        MEM_TOTAL_MB=$(free -m 2>/dev/null | awk '/^Mem:/{print $2}' || echo "2048")
        SWAP_TOTAL_MB=$(free -m 2>/dev/null | awk '/^Swap:/{print $2}' || echo "0")
        if [[ ${MEM_TOTAL_MB} -lt 1500 && ${SWAP_TOTAL_MB} -eq 0 ]]; then
            log "Low RAM instance detected (${MEM_TOTAL_MB}MB RAM, 0MB Swap). Enabling 1GB swapfile to prevent OOM kills..."
            fallocate -l 1G /potato_swapfile 2>/dev/null || dd if=/dev/zero of=/potato_swapfile bs=1M count=1024 >/dev/null 2>&1 || true
            if [[ -f /potato_swapfile ]]; then
                chmod 600 /potato_swapfile
                mkswap /potato_swapfile >/dev/null 2>&1 || true
                swapon /potato_swapfile >/dev/null 2>&1 || true
                ok "Temporary 1GB swapfile activated."
            fi
        fi
    fi
}
ensure_sufficient_memory

install_deps() {
    log "Installing dependencies (curl, git, openssl, jq, ca-certificates)..."
    case $PM in
        apt)
            export DEBIAN_FRONTEND=noninteractive
            # Failsafe 4: Dpkg recovery & Lock waiting
            if dpkg --audit 2>/dev/null | grep -q "unpacked\|half-configured"; then
                log "Auto-repairing interrupted dpkg configuration..."
                dpkg --configure -a >/dev/null 2>&1 || true
            fi
            for i in $(seq 1 12); do
                if ! fuser /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock /var/lib/dpkg/lock &>/dev/null; then
                    break
                fi
                log "Waiting for background system package manager (apt/dpkg lock) to release..."
                sleep 3
            done
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

# 3. Docker Engine & Native Fallback Verification
if [[ "${USE_DOCKER}" == "true" ]]; then
    if ! command -v docker &>/dev/null || ! docker info &>/dev/null; then
        if [[ "$OS" == "Linux" ]]; then
            log "Docker Engine not active. Attempting automatic Docker installation..."
            curl -fsSL https://get.docker.com | sh >/dev/null 2>&1 || true
            systemctl enable --now docker >/dev/null 2>&1 || true
        fi
    fi

    if ! command -v docker &>/dev/null || ! docker info &>/dev/null; then
        warn "Docker Engine is not running or unprivileged. Auto-switching to Native Python Mode..."
        USE_DOCKER="false"
    else
        ok "Docker Engine & Daemon active."
    fi
fi

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
    if [[ "${EXPLICIT_QUICK_TUNNEL}" == "true" ]]; then
        CLOUDFLARE_TUNNEL_TOKEN=""
    elif [[ -z "${CLOUDFLARE_TUNNEL_TOKEN}" ]]; then
        CLOUDFLARE_TUNNEL_TOKEN=$(grep -E "^[\"\']?CLOUDFLARE_TUNNEL_TOKEN[\"\']?=" .env 2>/dev/null | cut -d'=' -f2- | tr -d '"' | tr -d "'" | tr -d '\r' | tr -d '\n' | tr -d ' ' | grep -v '^$' | tail -n1 || true)
    fi
    # Sanitize token: remove quotes, newlines, and trailing spaces
    CLOUDFLARE_TUNNEL_TOKEN=$(echo "${CLOUDFLARE_TUNNEL_TOKEN}" | tr -d '"' | tr -d "'" | tr -d '\r' | tr -d '\n' | tr -d ' ')
    if [[ -n "${CLOUDFLARE_TUNNEL_TOKEN}" ]]; then
        ENABLE_CLOUDFLARE_TUNNEL="true"
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

# 6. Build & Launch Potato Gateway Service (Docker vs Native)
if [[ "${USE_DOCKER}" == "true" ]]; then
    log "Building and starting Potato Gateway Docker container..."
    docker compose -f docker-compose.do.yml up -d --build
else
    log "Preparing Native Python 3 environment..."
    if ! command -v python3 &>/dev/null; then
        case $PM in
            apt) apt-get install -y -qq python3 python3-venv python3-pip >/dev/null 2>&1 || true ;;
            dnf|yum) $PM install -y -q python3 python3-pip >/dev/null 2>&1 || true ;;
            pacman) pacman -Sy --noconfirm python python-pip >/dev/null 2>&1 || true ;;
            brew) brew install python >/dev/null 2>&1 || true ;;
        esac
    fi
    PYTHON_BIN="$(command -v python3 || echo python)"
    if [[ ! -d .venv ]]; then
        log "Creating Python virtual environment (.venv)..."
        "${PYTHON_BIN}" -m venv .venv
    fi
    log "Installing Potato Gateway Python dependencies..."
    .venv/bin/python -m pip install -q --upgrade pip >/dev/null 2>&1 || true
    .venv/bin/python -m pip install -q -e . >/dev/null 2>&1 || true

    log "Starting Potato Gateway native service (uvicorn)..."
    if command -v systemctl &>/dev/null && [[ "$OS" == "Linux" ]]; then
        cat <<EOF > /etc/systemd/system/potato.service
[Unit]
Description=Potato Gateway Native Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$(pwd)
EnvironmentFile=$(pwd)/.env
ExecStart=$(pwd)/.venv/bin/python -m uvicorn potato.main:app --host 0.0.0.0 --port 8080
Restart=always
RestartSec=3s

[Install]
WantedBy=multi-user.target
EOF
        systemctl daemon-reload >/dev/null 2>&1 || true
        systemctl enable --now potato >/dev/null 2>&1 || true
    else
        pkill -f "uvicorn potato.main:app" || true
        nohup .venv/bin/python -m uvicorn potato.main:app --host 0.0.0.0 --port 8080 > /tmp/potato.log 2>&1 &
    fi
fi

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
echo -e "🌐 ${BOLD}Networking & Public Domain Setup:${NC}"
echo -e "   To expose Potato Gateway via Tailscale Funnel (0 port-forwarding, zero 499s):"
echo -e "   ${CYAN}sudo bash scripts/setup-tunnel.sh --domain=api.yourdomain.com${NC}"
echo "------------------------------------------------------------------------------"
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
