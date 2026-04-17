#!/bin/bash
#
# Harness Lab Worker Bootstrap Script
# 
# Usage:
#   curl -sSL https://get.harness-lab.dev/worker.sh | bash -s -- --control-plane-url https://...
#   ./bootstrap-worker.sh --control-plane-url https://... --role executor
#
# Options:
#   --control-plane-url URL    Control plane URL (required for remote worker)
#   --role ROLE                Worker role: general|executor|reviewer|planner (default: general)
#   --label LABEL              Custom worker label
#   --serve                    Start worker daemon after registration
#   --dry-run                  Show config without registering
#   --help                     Show this help
#
# Requirements:
#   - Python 3.12+ (auto-installed if missing on Ubuntu/Debian)
#   - curl, git

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_ok() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_err() { echo -e "${RED}[ERR]${NC} $1"; }

# Default values
CONTROL_PLANE_URL=""
ROLE="general"
LABEL=""
SERVE=false
DRY_RUN=false
REPO_URL="https://github.com/3452808350-max/Harness-Lab.git"
INSTALL_DIR="${HOME}/.harness-lab"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --control-plane-url)
            CONTROL_PLANE_URL="$2"
            shift 2
            ;;
        --role)
            ROLE="$2"
            shift 2
            ;;
        --label)
            LABEL="$2"
            shift 2
            ;;
        --serve)
            SERVE=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --help)
            head -25 "$0" | tail -20
            exit 0
            ;;
        *)
            log_err "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Check Python
check_python() {
    if command -v python3 &> /dev/null; then
        PYTHON_VERSION=$(python3 -c "import sys; print(sys.version_info.major * 100 + sys.version_info.minor)")
        if [ "$PYTHON_VERSION" -ge 312 ]; then
            log_ok "Python $(python3 --version) detected"
            return 0
        else
            log_warn "Python version too old: $(python3 --version), need 3.12+"
        fi
    fi
    
    # Try to install Python 3.12 on Ubuntu/Debian
    if command -v apt-get &> /dev/null; then
        log_info "Installing Python 3.12..."
        sudo apt-get update -qq
        sudo apt-get install -y python3.12 python3.12-venv python3-pip
        log_ok "Python 3.12 installed"
        return 0
    fi
    
    log_err "Python 3.12+ required. Please install manually."
    exit 1
}

# Check dependencies
check_deps() {
    local missing=()
    for dep in curl git; do
        if ! command -v "$dep" &> /dev/null; then
            missing+=("$dep")
        fi
    done
    
    if [ ${#missing[@]} -gt 0 ]; then
        log_info "Installing missing dependencies: ${missing[*]}"
        if command -v apt-get &> /dev/null; then
            sudo apt-get install -y "${missing[@]}"
        elif command -v yum &> /dev/null; then
            sudo yum install -y "${missing[@]}"
        else
            log_err "Please install: ${missing[*]}"
            exit 1
        fi
        log_ok "Dependencies installed"
    fi
}

# Clone or update repo
setup_repo() {
    if [ -d "$INSTALL_DIR" ]; then
        log_info "Updating existing installation at $INSTALL_DIR"
        cd "$INSTALL_DIR"
        git pull --quiet
    else
        log_info "Cloning Harness Lab to $INSTALL_DIR"
        git clone --quiet --depth 1 "$REPO_URL" "$INSTALL_DIR"
        cd "$INSTALL_DIR"
    fi
    log_ok "Repository ready"
}

# Create venv and install minimal dependencies
setup_venv() {
    log_info "Setting up Python environment..."
    
    if [ ! -d "venv" ]; then
        python3.12 -m venv venv || python3 -m venv venv
    fi
    
    # Minimal dependencies for worker CLI (no ML heavy deps)
    ./venv/bin/pip install --quiet --upgrade pip
    ./venv/bin/pip install --quiet \
        fastapi uvicorn pydantic pydantic-settings \
        psycopg[binary] redis python-dotenv \
        requests openai numpy sqlalchemy \
        boto3 aiofiles jinja2
    
    log_ok "Environment ready"
}

# Configure environment
setup_config() {
    if [ -n "$CONTROL_PLANE_URL" ]; then
        log_info "Configuring control plane URL..."
        echo "HARNESS_CONTROL_PLANE_URL=${CONTROL_PLANE_URL}" > .env.worker
        
        # Prompt for SOCKS5 proxy if needed
        if [ -z "$HARNESS_SOCKS5_PROXY_HOST" ]; then
            log_warn "If control plane requires SOCKS5 proxy, set HARNESS_SOCKS5_PROXY_HOST/PORT"
        fi
    fi
}

# Run auto-pair
run_auto_pair() {
    log_info "Running worker auto-pair..."
    
    CMD_ARGS="--role $ROLE"
    if [ -n "$LABEL" ]; then
        CMD_ARGS="$CMD_ARGS --label $LABEL"
    fi
    if [ "$DRY_RUN" = true ]; then
        CMD_ARGS="$CMD_ARGS --dry-run"
    fi
    if [ -n "$CONTROL_PLANE_URL" ]; then
        CMD_ARGS="$CMD_ARGS --control-plane-url $CONTROL_PLANE_URL"
    fi
    
    ./venv/bin/python3 -m backend.app.harness_lab.cli worker auto-pair $CMD_ARGS
    
    if [ "$DRY_RUN" = true ]; then
        log_ok "Dry-run complete. Run without --dry-run to register."
        exit 0
    fi
}

# Start worker daemon
start_serve() {
    if [ "$SERVE" = true ]; then
        log_info "Starting worker daemon..."
        
        CMD_ARGS="--role $ROLE --serve"
        if [ -n "$LABEL" ]; then
            CMD_ARGS="$CMD_ARGS --label $LABEL"
        fi
        if [ -n "$CONTROL_PLANE_URL" ]; then
            CMD_ARGS="$CMD_ARGS --control-plane-url $CONTROL_PLANE_URL"
        fi
        
        ./venv/bin/python3 -m backend.app.harness_lab.cli worker $CMD_ARGS
    else
        log_ok "Worker registered. To start daemon, run:"
        echo "  $INSTALL_DIR/venv/bin/python3 -m backend.app.harness_lab.cli worker serve --role $ROLE --control-plane-url $CONTROL_PLANE_URL"
    fi
}

# Main flow
main() {
    log_info "=== Harness Lab Worker Bootstrap ==="
    echo ""
    
    check_python
    check_deps
    setup_repo
    setup_venv
    setup_config
    run_auto_pair
    start_serve
    
    echo ""
    log_ok "=== Bootstrap Complete ==="
}

main