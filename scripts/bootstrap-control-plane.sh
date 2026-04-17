#!/bin/bash
#
# Harness Lab Control Plane Bootstrap Script
#
# Deploys complete Harness Lab infrastructure:
#   - PostgreSQL 16 (task storage, worker registry)
#   - Redis 7 (queue, lease tracking)
#   - Docker (sandbox backend)
#   - FastAPI service (control plane API)
#   - Local worker (auto-registered)
#
# Usage:
#   curl -sSL https://get.harness-lab.dev/control-plane.sh | bash
#   ./bootstrap-control-plane.sh [--port 4600] [--with-worker]
#
# Options:
#   --port PORT         API server port (default: 4600)
#   --with-worker       Register and start local worker
#   --worker-role ROLE  Local worker role (default: executor)
#   --db-port PORT      PostgreSQL port (default: 5432)
#   --redis-port PORT   Redis port (default: 6379)
#   --dev               Development mode (no Docker sandbox, mock backend)
#   --help              Show this help
#
# Requirements:
#   - Ubuntu 22.04+ or Debian 12+ (for PostgreSQL 16)
#   - sudo access
#   - curl, git

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_ok() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_err() { echo -e "${RED}[ERR]${NC} $1"; }
log_step() { echo -e "${CYAN}[STEP]${NC} $1"; }

# Defaults
API_PORT=4600
WITH_WORKER=false
WORKER_ROLE="executor"
DB_PORT=5432
REDIS_PORT=6379
DEV_MODE=false
REPO_URL="https://github.com/3452808350-max/Harness-Lab.git"
INSTALL_DIR="${HOME}/.harness-lab"
DB_NAME="harness_lab"
DB_USER="harness"
DB_PASSWORD=""

# Parse args
while [[ $# -gt 0 ]]; do
    case $1 in
        --port)
            API_PORT="$2"
            shift 2
            ;;
        --with-worker)
            WITH_WORKER=true
            shift
            ;;
        --worker-role)
            WORKER_ROLE="$2"
            shift 2
            ;;
        --db-port)
            DB_PORT="$2"
            shift 2
            ;;
        --redis-port)
            REDIS_PORT="$2"
            shift 2
            ;;
        --dev)
            DEV_MODE=true
            shift
            ;;
        --help)
            head -30 "$0" | tail -25
            exit 0
            ;;
        *)
            log_err "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Check OS
check_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS=$ID
        VER=$VERSION_ID
    else
        log_err "Cannot detect OS. /etc/os-release missing."
        exit 1
    fi
    
    log_info "Detected: $OS $VER"
    
    if [[ "$OS" != "ubuntu" && "$OS" != "debian" ]]; then
        log_warn "This script is optimized for Ubuntu/Debian. Proceeding anyway..."
    fi
}

# Install system packages
install_system_deps() {
    log_step "Installing system dependencies..."
    
    sudo apt-get update -qq
    
    # Essential tools
    sudo apt-get install -y curl git ca-certificates gnupg lsb-release
    
    log_ok "System dependencies installed"
}

# Install PostgreSQL 16
install_postgres() {
    log_step "Installing PostgreSQL 16..."
    
    # Check if already installed
    if command -v psql &> /dev/null && psql --version | grep -q "16"; then
        log_ok "PostgreSQL 16 already installed"
        return 0
    fi
    
    # Add PostgreSQL apt repository
    sudo install -d /usr/share/keyrings
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo gpg --dearmor -o /usr/share/keyrings/postgresql-keyring.gpg
    
    echo "deb [signed-by=/usr/share/keyrings/postgresql-keyring.gpg] http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" | \
        sudo tee /etc/apt/sources.list.d/pgdg.list > /dev/null
    
    sudo apt-get update -qq
    sudo apt-get install -y postgresql-16 postgresql-contrib-16
    
    # Ensure service running
    sudo systemctl enable postgresql
    sudo systemctl start postgresql
    
    log_ok "PostgreSQL 16 installed and running"
}

# Configure PostgreSQL database
setup_database() {
    log_step "Configuring database..."
    
    # Generate password if not set
    if [ -z "$DB_PASSWORD" ]; then
        DB_PASSWORD=$(openssl rand -base64 24 | tr -dc 'a-zA-Z0-9' | head -c 32)
        log_info "Generated database password (saved to .env)"
    fi
    
    # Create user and database
    sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASSWORD';" 2>/dev/null || true
    sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;" 2>/dev/null || true
    sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;" 2>/dev/null || true
    
    # Configure pg_hba for local connections (password auth)
    if ! sudo grep -q "local.*$DB_NAME.*$DB_USER" /etc/postgresql/16/main/pg_hba.conf; then
        echo "local $DB_NAME $DB_USER md5" | sudo tee -a /etc/postgresql/16/main/pg_hba.conf
        sudo systemctl reload postgresql
    fi
    
    log_ok "Database '$DB_NAME' created for user '$DB_USER'"
}

# Install Redis 7
install_redis() {
    log_step "Installing Redis 7..."
    
    # Check if already installed
    if command -v redis-cli &> /dev/null && redis-cli --version | grep -q "7"; then
        log_ok "Redis 7 already installed"
        return 0
    fi
    
    curl -fsSL https://packages.redis.io/gpg | sudo gpg --dearmor -o /usr/share/keyrings/redis-keyring.gpg
    
    echo "deb [signed-by=/usr/share/keyrings/redis-keyring.gpg] https://packages.redis.io/deb $(lsb_release -cs) main" | \
        sudo tee /etc/apt/sources.list.d/redis.list > /dev/null
    
    sudo apt-get update -qq
    sudo apt-get install -y redis
    
    # Configure Redis
    sudo sed -i 's/^bind 127.0.0.1/bind 127.0.0.1 ::1/' /etc/redis/redis.conf
    sudo sed -i 's/^# maxmemory-policy/maxmemory-policy noeviction/' /etc/redis/redis.conf
    
    sudo systemctl enable redis-server
    sudo systemctl start redis-server
    
    log_ok "Redis installed and running"
}

# Install Docker
install_docker() {
    if [ "$DEV_MODE" = true ]; then
        log_warn "Development mode: skipping Docker installation (using mock sandbox)"
        return 0
    fi
    
    log_step "Installing Docker..."
    
    # Check if already installed
    if command -v docker &> /dev/null; then
        log_ok "Docker already installed"
        return 0
    fi
    
    # Add Docker apt repository
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/$OS/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg
    
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$OS $(lsb_release -cs) stable" | \
        sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    
    sudo apt-get update -qq
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    
    # Add current user to docker group
    sudo usermod -aG docker $USER
    
    sudo systemctl enable docker
    sudo systemctl start docker
    
    log_ok "Docker installed (note: you may need to re-login for docker group to take effect)"
}

# Clone repo and setup Python
setup_project() {
    log_step "Setting up Harness Lab project..."
    
    if [ -d "$INSTALL_DIR" ]; then
        log_info "Updating existing installation at $INSTALL_DIR"
        cd "$INSTALL_DIR"
        git pull --quiet
    else
        log_info "Cloning Harness Lab to $INSTALL_DIR"
        git clone --quiet --depth 1 "$REPO_URL" "$INSTALL_DIR"
        cd "$INSTALL_DIR"
    fi
    
    # Create venv
    if [ ! -d "venv" ]; then
        python3 -m venv venv
    fi
    
    log_ok "Repository ready"
}

# Install Python dependencies
install_python_deps() {
    log_step "Installing Python dependencies..."
    
    # Core dependencies
    ./venv/bin/pip install --quiet --upgrade pip
    
    if [ "$DEV_MODE" = true ]; then
        # Light dependencies for dev mode (no ML)
        ./venv/bin/pip install --quiet \
            fastapi uvicorn pydantic pydantic-settings \
            psycopg[binary] redis python-dotenv \
            requests openai numpy sqlalchemy \
            boto3 aiofiles jinja2
    else
        # Full dependencies (with ML for knowledge service)
        ./venv/bin/pip install --quiet --index-url https://pypi.org/simple \
            fastapi uvicorn pydantic pydantic-settings \
            psycopg[binary] redis python-dotenv \
            requests openai numpy sqlalchemy \
            boto3 aiofiles jinja2 \
            faiss-cpu sentence-transformers transformers torch
    fi
    
    log_ok "Python dependencies installed"
}

# Configure environment
setup_env() {
    log_step "Configuring environment..."
    
    cat > .env << EOF
# Harness Lab Environment Configuration
# Generated by bootstrap-control-plane.sh

# Database
DATABASE_URL=postgresql://$DB_USER:$DB_PASSWORD@127.0.0.1:$DB_PORT/$DB_NAME

# Redis
REDIS_URL=redis://127.0.0.1:$REDIS_PORT/0

# API Server
API_HOST=0.0.0.0
API_PORT=$API_PORT

# Sandbox Backend
SANDBOX_BACKEND=$([ "$DEV_MODE" = true ] && echo "mock" || echo "docker")

# Control Plane URL (for remote workers)
CONTROL_PLANE_URL=http://127.0.0.1:$API_PORT

# Optional: SOCKS5 proxy for remote workers
# HARNESS_SOCKS5_PROXY_HOST=
# HARNESS_SOCKS5_PROXY_PORT=1080
EOF
    
    log_ok "Environment configured (saved to .env)"
}

# Initialize database schema
init_database() {
    log_step "Initializing database schema..."
    
    # Run migrations if available
    if [ -f "backend/app/harness_lab/storage.py" ]; then
        ./venv/bin/python3 -c "
from backend.app.harness_lab.storage import HarnessLabDatabase
from backend.app.harness_lab.bootstrap import harness_lab_services
import asyncio

async def init():
    db = harness_lab_services.storage
    await db.initialize_schema()
    print('Schema initialized')

asyncio.run(init())
" 2>&1 || log_warn "Schema init skipped (may already exist)"
    fi
    
    log_ok "Database schema ready"
}

# Build sandbox Docker image (if not dev mode)
build_sandbox_image() {
    if [ "$DEV_MODE" = true ]; then
        return 0
    fi
    
    log_step "Building sandbox Docker image..."
    
    if [ -f "sandbox/Dockerfile" ]; then
        docker build -t harness-lab/sandbox:local sandbox/ 2>&1 || \
            log_warn "Sandbox image build skipped (Dockerfile may not exist)"
    else
        log_warn "No sandbox/Dockerfile found. Using default sandbox configuration."
    fi
    
    log_ok "Sandbox ready"
}

# Create systemd service for control plane
create_systemd_service() {
    log_step "Creating systemd service..."
    
    sudo tee /etc/systemd/system/harness-lab.service > /dev/null << EOF
[Unit]
Description=Harness Lab Control Plane
After=network.target postgresql.service redis.service docker.service

[Service]
Type=simple
User=$USER
WorkingDirectory=$INSTALL_DIR
Environment=PYTHONPATH=$INSTALL_DIR/backend
ExecStart=$INSTALL_DIR/venv/bin/python3 -m uvicorn backend.app.main:app --host 0.0.0.0 --port $API_PORT
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    
    sudo systemctl daemon-reload
    sudo systemctl enable harness-lab
    
    log_ok "Systemd service created: harness-lab.service"
}

# Start control plane
start_control_plane() {
    log_step "Starting control plane..."
    
    sudo systemctl start harness-lab
    
    # Wait for service to be ready
    sleep 3
    
    if curl -s http://127.0.0.1:$API_PORT/health > /dev/null 2>&1; then
        log_ok "Control plane running at http://127.0.0.1:$API_PORT"
    else
        log_warn "Control plane may not be fully ready yet. Check: sudo systemctl status harness-lab"
    fi
}

# Register local worker (optional)
register_local_worker() {
    if [ "$WITH_WORKER" != true ]; then
        return 0
    fi
    
    log_step "Registering local worker..."
    
    ./venv/bin/python3 -m backend.app.harness_lab.cli worker auto-pair \
        --role $WORKER_ROLE \
        --label "$(hostname)-local"
    
    log_ok "Local worker registered (role: $WORKER_ROLE)"
}

# Print summary
print_summary() {
    echo ""
    echo -e "${CYAN}╔════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║         Harness Lab Control Plane Ready               ║${NC}"
    echo -e "${CYAN}╚════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo "  API URL:     http://127.0.0.1:$API_PORT"
    echo "  Health:      http://127.0.0.1:$API_PORT/health"
    echo "  Database:    $DB_NAME (user: $DB_USER)"
    echo "  Redis:       127.0.0.1:$REDIS_PORT"
    echo "  Sandbox:     $([ "$DEV_MODE" = true ] && echo "mock (dev mode)" || echo "docker")"
    echo ""
    echo "  Commands:"
    echo "    Status:    sudo systemctl status harness-lab"
    echo "    Logs:      sudo journalctl -u harness-lab -f"
    echo "    Restart:   sudo systemctl restart harness-lab"
    echo "    Stop:      sudo systemctl stop harness-lab"
    echo ""
    if [ "$WITH_WORKER" = true ]; then
        echo "  Local Worker: registered as $(hostname)-local (role: $WORKER_ROLE)"
    else
        echo "  To register local worker:"
        echo "    $INSTALL_DIR/venv/bin/python3 -m backend.app.harness_lab.cli worker auto-pair --role executor"
    fi
    echo ""
    echo "  To add remote workers on other machines:"
    echo "    curl -sSL https://get.harness-lab.dev/worker.sh | bash -s -- --control-plane-url http://THIS_SERVER_IP:$API_PORT --role executor --serve"
    echo ""
    echo "  Config saved to: $INSTALL_DIR/.env"
    echo ""
}

# Main flow
main() {
    echo ""
    echo -e "${CYAN}=== Harness Lab Control Plane Bootstrap ===${NC}"
    echo ""
    
    check_os
    install_system_deps
    install_postgres
    setup_database
    install_redis
    install_docker
    setup_project
    install_python_deps
    setup_env
    init_database
    build_sandbox_image
    create_systemd_service
    start_control_plane
    register_local_worker
    print_summary
    
    log_ok "=== Bootstrap Complete ==="
}

main