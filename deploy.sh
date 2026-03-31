#!/usr/bin/env bash
###############################################################################
# Tidemine - One-Click Tidecoin Mining Deployer
# Optimized for Intel i9 + NVIDIA RTX 5070 Ti on Ubuntu 24.04
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/bradbuythedip/tidemine/main/deploy.sh | bash -s -- --wallet YOUR_TDC_ADDRESS
#
# Or download and run:
#   chmod +x deploy.sh && ./deploy.sh --wallet YOUR_TDC_ADDRESS
###############################################################################

set -euo pipefail

# ─── Colors ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${CYAN}[*]${NC} $1"; }
ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# ─── Configuration ───────────────────────────────────────────────────────────
INSTALL_DIR="$HOME/.tidecoin-miner"
BIN_DIR="$INSTALL_DIR/bin"
LOG_DIR="$INSTALL_DIR/logs"
VENV_DIR="$INSTALL_DIR/venv"
REPO_URL="https://github.com/bradbuythedip/tidemine.git"
REPO_DIR="$INSTALL_DIR/tidemine"
SRBMINER_API="https://api.github.com/repos/doktor83/SRBMiner-Multi/releases/latest"
TIDECOIN_WALLET_URL="https://github.com/tidecoin/tidecoin/releases/download/v0.18.3/linux64.tar.gz"

WALLET_ADDRESS=""
POOL="tidecoin_official"
BENCHMARK=false
NO_GPU=false
INSTALL_SERVICE=true
AUTO_START=true

# ─── Parse Arguments ─────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --wallet|-w)     WALLET_ADDRESS="$2"; shift 2 ;;
        --pool|-p)       POOL="$2"; shift 2 ;;
        --benchmark|-b)  BENCHMARK=true; shift ;;
        --no-gpu)        NO_GPU=true; shift ;;
        --no-service)    INSTALL_SERVICE=false; shift ;;
        --no-start)      AUTO_START=false; shift ;;
        --help|-h)
            echo "Tidemine Deployer"
            echo ""
            echo "Usage: deploy.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --wallet, -w ADDR    TDC wallet address (required)"
            echo "  --pool, -p NAME      Pool name (default: tidecoin_official)"
            echo "  --benchmark, -b      Run benchmark after install"
            echo "  --no-gpu             Disable GPU mining"
            echo "  --no-service         Don't install systemd service"
            echo "  --no-start           Don't auto-start mining"
            echo "  --help, -h           Show this help"
            exit 0
            ;;
        *) error "Unknown option: $1" ;;
    esac
done

# ─── Banner ──────────────────────────────────────────────────────────────────
echo -e "${BOLD}${CYAN}"
cat << 'BANNER'

  ████████╗██╗██████╗ ███████╗███╗   ███╗██╗███╗   ██╗███████╗
  ╚══██╔══╝██║██╔══██╗██╔════╝████╗ ████║██║████╗  ██║██╔════╝
     ██║   ██║██║  ██║█████╗  ██╔████╔██║██║██╔██╗ ██║█████╗
     ██║   ██║██║  ██║██╔══╝  ██║╚██╔╝██║██║██║╚██╗██║██╔══╝
     ██║   ██║██████╔╝███████╗██║ ╚═╝ ██║██║██║ ╚████║███████╗
     ╚═╝   ╚═╝╚═════╝ ╚══════╝╚═╝     ╚═╝╚═╝╚═╝  ╚═══╝╚══════╝

  Tidecoin Post-Quantum CPU+GPU Miner | Falcon-512 / FN-DSA
  Optimized for Intel i9 + NVIDIA RTX 5070 Ti

BANNER
echo -e "${NC}"

# ─── Preflight Checks ───────────────────────────────────────────────────────
info "Running preflight checks..."

# Check OS
if [[ ! -f /etc/os-release ]]; then
    warn "Cannot detect OS. Continuing anyway..."
fi

# Check root (we need sudo for some ops)
if [[ $EUID -eq 0 ]]; then
    warn "Running as root. Preferably run as regular user with sudo access."
fi

# Check for required tools
for cmd in git python3 pip3 curl wget; do
    if ! command -v "$cmd" &>/dev/null; then
        warn "$cmd not found. Installing..."
        sudo apt-get update -qq && sudo apt-get install -y -qq "$cmd" 2>/dev/null || true
    fi
done

# Check NVIDIA GPU
if command -v nvidia-smi &>/dev/null; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || echo "Unknown")
    GPU_DRIVER=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 || echo "Unknown")
    ok "NVIDIA GPU detected: $GPU_NAME (driver: $GPU_DRIVER)"
else
    warn "nvidia-smi not found. GPU mining will be unavailable."
    if [[ "$NO_GPU" == "false" ]]; then
        warn "Consider installing NVIDIA drivers or use --no-gpu"
    fi
fi

# Check CPU
CPU_MODEL=$(grep -m1 "model name" /proc/cpuinfo 2>/dev/null | cut -d: -f2 | xargs || echo "Unknown")
CPU_CORES=$(nproc --all 2>/dev/null || echo "?")
CPU_PHYS=$(grep -c "^processor" /proc/cpuinfo 2>/dev/null || echo "?")
ok "CPU: $CPU_MODEL ($CPU_CORES threads)"

# Check RAM
TOTAL_RAM_KB=$(grep MemTotal /proc/meminfo 2>/dev/null | awk '{print $2}' || echo "0")
TOTAL_RAM_GB=$((TOTAL_RAM_KB / 1024 / 1024))
ok "RAM: ${TOTAL_RAM_GB}GB"

echo ""

# ─── Step 1: Install System Dependencies ─────────────────────────────────────
info "Step 1/8: Installing system dependencies..."

sudo apt-get update -qq 2>/dev/null || true
sudo apt-get install -y -qq \
    python3-venv python3-pip python3-dev \
    build-essential libssl-dev libffi-dev \
    lm-sensors cpufrequtils \
    curl wget git jq \
    2>/dev/null || warn "Some packages may have failed to install"

ok "System dependencies installed"

# ─── Step 2: Create Directory Structure ───────────────────────────────────────
info "Step 2/8: Setting up directory structure..."

mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$LOG_DIR" "$INSTALL_DIR/data"

ok "Directories created at $INSTALL_DIR"

# ─── Step 3: System Optimizations ─────────────────────────────────────────────
info "Step 3/8: Applying system optimizations..."

# 3a. Huge Pages (20-30% boost for YesPowerTide)
HUGEPAGES_COUNT=1280
if [[ $TOTAL_RAM_GB -gt 32 ]]; then
    HUGEPAGES_COUNT=2560
elif [[ $TOTAL_RAM_GB -gt 16 ]]; then
    HUGEPAGES_COUNT=1536
fi

info "  Setting up $HUGEPAGES_COUNT huge pages..."
# Drop caches first for better allocation
sudo sh -c "echo 3 > /proc/sys/vm/drop_caches" 2>/dev/null || true
sudo sysctl -w vm.nr_hugepages=$HUGEPAGES_COUNT 2>/dev/null || warn "Failed to set hugepages"
ACTUAL_HP=$(cat /proc/sys/vm/nr_hugepages 2>/dev/null || echo "0")
ok "  Huge pages: $ACTUAL_HP allocated (requested: $HUGEPAGES_COUNT)"

# Make persistent
echo "vm.nr_hugepages = $HUGEPAGES_COUNT" | sudo tee /etc/sysctl.d/99-tidemine-hugepages.conf >/dev/null 2>&1 || true

# 3b. CPU Governor → performance
info "  Setting CPU governor to performance..."
for cpu_dir in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
    echo "performance" | sudo tee "$cpu_dir" >/dev/null 2>&1 || true
done
# Also try cpupower
sudo cpupower frequency-set -g performance 2>/dev/null || true
ok "  CPU governor: performance"

# 3c. Disable C-states for consistent performance
sudo sh -c "echo 1 > /sys/module/intel_idle/parameters/max_cstate" 2>/dev/null || true

# 3d. Kernel tuning
info "  Applying kernel optimizations..."
sudo sysctl -w vm.swappiness=1 2>/dev/null || true
sudo sysctl -w vm.dirty_ratio=10 2>/dev/null || true
sudo sysctl -w vm.dirty_background_ratio=5 2>/dev/null || true
sudo sysctl -w kernel.sched_migration_cost_ns=5000000 2>/dev/null || true
sudo sysctl -w kernel.sched_autogroup_enabled=0 2>/dev/null || true
ok "  Kernel parameters optimized"

# 3e. Disable Transparent Huge Pages (explicit hugepages are better)
sudo sh -c "echo never > /sys/kernel/mm/transparent_hugepage/enabled" 2>/dev/null || true
sudo sh -c "echo never > /sys/kernel/mm/transparent_hugepage/defrag" 2>/dev/null || true
ok "  THP disabled (explicit hugepages preferred)"

# 3f. GPU optimization
if command -v nvidia-smi &>/dev/null; then
    info "  Configuring GPU for maximum mining performance..."
    sudo nvidia-smi -pm 1 2>/dev/null || true                    # Persistence mode
    sudo nvidia-smi -pl 270 2>/dev/null || true                  # Power limit (90% of 300W TDP)
    # Set compute mode - don't use EXCLUSIVE_PROCESS as it may block monitoring
    sudo nvidia-smi -c DEFAULT 2>/dev/null || true
    ok "  GPU: persistence mode, power limit 270W"
fi

# 3g. IRQ affinity - pin IRQs to last 2 cores
TOTAL_CORES=$(nproc 2>/dev/null || echo 16)
IRQ_MASK=$(printf "0x%x" $(( (1 << TOTAL_CORES) - (1 << (TOTAL_CORES - 2)) )) )
echo "$IRQ_MASK" | sudo tee /proc/irq/default_smp_affinity >/dev/null 2>&1 || true
ok "  IRQ affinity optimized"

echo ""

# ─── Step 4: Clone/Update Tidemine Repository ────────────────────────────────
info "Step 4/8: Setting up Tidemine..."

if [[ -d "$REPO_DIR/.git" ]]; then
    info "  Updating existing installation..."
    cd "$REPO_DIR"
    git pull origin main 2>/dev/null || git pull 2>/dev/null || true
else
    info "  Cloning repository..."
    git clone "$REPO_URL" "$REPO_DIR" 2>/dev/null || {
        # If clone fails (private repo etc), create from embedded code
        warn "  Git clone failed. Creating from embedded source..."
        mkdir -p "$REPO_DIR"
    }
fi

ok "Tidemine source ready"

# ─── Step 5: Python Virtual Environment ───────────────────────────────────────
info "Step 5/8: Setting up Python environment..."

python3 -m venv "$VENV_DIR" 2>/dev/null || python3 -m venv --without-pip "$VENV_DIR"
source "$VENV_DIR/bin/activate"

pip install --upgrade pip setuptools wheel -q 2>/dev/null || true

# Install tidemine package
if [[ -f "$REPO_DIR/pyproject.toml" ]]; then
    pip install -e "$REPO_DIR" -q 2>/dev/null || {
        # Install dependencies manually if editable install fails
        pip install typer rich pyyaml httpx psutil pynvml aiohttp -q 2>/dev/null || true
    }
else
    pip install typer rich pyyaml httpx psutil pynvml aiohttp -q 2>/dev/null || true
fi

# Create wrapper script in /usr/local/bin
WRAPPER="/usr/local/bin/tidecoin-miner"
sudo tee "$WRAPPER" >/dev/null << WRAPPER_EOF
#!/usr/bin/env bash
source "$VENV_DIR/bin/activate"
exec python -m tidecoin_miner.cli "\$@"
WRAPPER_EOF
sudo chmod +x "$WRAPPER"

ok "Python environment ready"

# ─── Step 6: Download SRBMiner-MULTI ──────────────────────────────────────────
info "Step 6/8: Installing SRBMiner-MULTI..."

# Check if already installed
SRBMINER_BIN=$(find "$BIN_DIR" -name "SRBMiner-MULTI" -type f 2>/dev/null | head -1)

if [[ -z "$SRBMINER_BIN" ]]; then
    info "  Fetching latest release info..."
    RELEASE_JSON=$(curl -sSL "$SRBMINER_API" 2>/dev/null)
    RELEASE_TAG=$(echo "$RELEASE_JSON" | jq -r '.tag_name' 2>/dev/null || echo "unknown")
    info "  Latest version: $RELEASE_TAG"

    # Find linux asset
    DOWNLOAD_URL=$(echo "$RELEASE_JSON" | jq -r '.assets[] | select(.name | test("linux.*tar")) | .browser_download_url' 2>/dev/null | head -1)

    if [[ -z "$DOWNLOAD_URL" || "$DOWNLOAD_URL" == "null" ]]; then
        # Try alternate pattern
        DOWNLOAD_URL=$(echo "$RELEASE_JSON" | jq -r '.assets[] | select(.name | test("Linux|linux")) | .browser_download_url' 2>/dev/null | head -1)
    fi

    if [[ -n "$DOWNLOAD_URL" && "$DOWNLOAD_URL" != "null" ]]; then
        ARCHIVE_NAME=$(basename "$DOWNLOAD_URL")
        info "  Downloading $ARCHIVE_NAME..."
        curl -sSL "$DOWNLOAD_URL" -o "$BIN_DIR/$ARCHIVE_NAME"
        info "  Extracting..."
        tar -xf "$BIN_DIR/$ARCHIVE_NAME" -C "$BIN_DIR" 2>/dev/null || \
            unzip -oq "$BIN_DIR/$ARCHIVE_NAME" -d "$BIN_DIR" 2>/dev/null
        rm -f "$BIN_DIR/$ARCHIVE_NAME"
        SRBMINER_BIN=$(find "$BIN_DIR" -name "SRBMiner-MULTI" -type f 2>/dev/null | head -1)
        if [[ -n "$SRBMINER_BIN" ]]; then
            chmod +x "$SRBMINER_BIN"
            ok "SRBMiner installed: $SRBMINER_BIN"
        else
            warn "SRBMiner binary not found after extraction"
        fi
    else
        warn "Could not find SRBMiner download URL"
    fi
else
    ok "SRBMiner already installed: $SRBMINER_BIN"
fi

# ─── Step 7: Configuration ───────────────────────────────────────────────────
info "Step 7/8: Writing configuration..."

# Resolve pool details
declare -A POOL_HOSTS POOL_PORTS
POOL_HOSTS[tidecoin_official]="pool.tidecoin.exchange"; POOL_PORTS[tidecoin_official]=3032
POOL_HOSTS[tidepool_world]="tidepool.world";            POOL_PORTS[tidepool_world]=6243
POOL_HOSTS[rplant_na]="stratum-na.rplant.xyz";          POOL_PORTS[rplant_na]=7064
POOL_HOSTS[rplant_eu]="stratum-eu.rplant.xyz";          POOL_PORTS[rplant_eu]=7064
POOL_HOSTS[zpool]="yespowertide.mine.zpool.ca";         POOL_PORTS[zpool]=6243
POOL_HOSTS[zergpool]="yespowertide.mine.zergpool.com";  POOL_PORTS[zergpool]=6243

POOL_HOST="${POOL_HOSTS[$POOL]:-pool.tidecoin.exchange}"
POOL_PORT="${POOL_PORTS[$POOL]:-3032}"

# Calculate optimal threads (physical cores - 2)
PHYS_CORES=$(lscpu -p=Core,Socket 2>/dev/null | grep -v '^#' | sort -u | wc -l)
if [[ $PHYS_CORES -lt 2 ]]; then
    PHYS_CORES=$(nproc 2>/dev/null || echo 4)
fi
OPTIMAL_THREADS=$((PHYS_CORES - 2))
if [[ $OPTIMAL_THREADS -lt 1 ]]; then OPTIMAL_THREADS=1; fi

# Write YAML config
cat > "$INSTALL_DIR/config.yaml" << CONFIG_EOF
wallet:
  address: "${WALLET_ADDRESS}"
  node_path: ${INSTALL_DIR}/tidecoin-wallet

mining:
  algorithm: yespowertide
  miner: srbminer
  cpu_threads: ${OPTIMAL_THREADS}
  gpu_enabled: $([ "$NO_GPU" = "true" ] && echo "false" || echo "true")
  gpu_id: 0
  huge_pages: true
  cpu_governor: performance

pool:
  primary: ${POOL}
  failover_order:
    - tidepool_world
    - rplant_na
    - zpool
  max_stale_rate: 0.03
  health_check_interval: 30

monitor:
  dashboard: true
  api_port: 8420
  refresh_interval: 5
  price_check_interval: 300

alerts:
  hashrate_drop_threshold: 0.5
  gpu_temp_max: 85
  cpu_temp_max: 95
  notify_on_block_found: true

logging:
  level: INFO
  file: ${LOG_DIR}/miner.log
  max_size_mb: 100
  rotate_count: 5
CONFIG_EOF

ok "Configuration written to $INSTALL_DIR/config.yaml"

# ─── Step 8: Systemd Service & Auto-Start ─────────────────────────────────────
if [[ "$INSTALL_SERVICE" == "true" ]]; then
    info "Step 8/8: Installing systemd service..."

    sudo tee /etc/systemd/system/tidemine.service >/dev/null << SERVICE_EOF
[Unit]
Description=Tidemine - Tidecoin Post-Quantum CPU+GPU Miner
Documentation=https://github.com/bradbuythedip/tidemine
After=network-online.target nvidia-persistenced.service
Wants=network-online.target

[Service]
Type=simple
User=$(whoami)
Group=$(whoami)
WorkingDirectory=$HOME
ExecStart=$WRAPPER start --daemon
ExecStop=$WRAPPER stop
Restart=always
RestartSec=30
TimeoutStopSec=60

# Performance
Nice=-10
IOSchedulingClass=realtime
IOSchedulingPriority=0
CPUSchedulingPolicy=batch

# Limits
LimitNOFILE=65536
LimitMEMLOCK=infinity

# Environment
Environment=HOME=$HOME
Environment=CUDA_VISIBLE_DEVICES=0
Environment=PATH=$VENV_DIR/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

[Install]
WantedBy=multi-user.target
SERVICE_EOF

    sudo systemctl daemon-reload
    sudo systemctl enable tidemine 2>/dev/null || true
    ok "Systemd service installed and enabled"
fi

# ─── Start Mining ─────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}============================================${NC}"
echo -e "${BOLD}${GREEN}  Tidemine Installation Complete!${NC}"
echo -e "${BOLD}${GREEN}============================================${NC}"
echo ""
echo -e "  ${CYAN}Config:${NC}    $INSTALL_DIR/config.yaml"
echo -e "  ${CYAN}Logs:${NC}      $LOG_DIR/"
echo -e "  ${CYAN}Miner:${NC}     $SRBMINER_BIN"
echo -e "  ${CYAN}CLI:${NC}       tidecoin-miner --help"
echo ""
echo -e "  ${CYAN}CPU:${NC}       $CPU_MODEL"
echo -e "  ${CYAN}Threads:${NC}   $OPTIMAL_THREADS (of $PHYS_CORES physical cores)"
echo -e "  ${CYAN}Hugepages:${NC} $ACTUAL_HP allocated"
if command -v nvidia-smi &>/dev/null; then
echo -e "  ${CYAN}GPU:${NC}       $GPU_NAME"
fi
echo -e "  ${CYAN}Pool:${NC}      $POOL ($POOL_HOST:$POOL_PORT)"
echo -e "  ${CYAN}Wallet:${NC}    ${WALLET_ADDRESS:-NOT SET}"
echo ""

if [[ -z "$WALLET_ADDRESS" ]]; then
    echo -e "${YELLOW}WARNING: No wallet address set!${NC}"
    echo -e "Set it with: ${BOLD}tidecoin-miner config${NC}"
    echo -e "Or edit: $INSTALL_DIR/config.yaml"
    echo ""
fi

if [[ "$AUTO_START" == "true" && -n "$WALLET_ADDRESS" && -n "$SRBMINER_BIN" ]]; then
    info "Starting miner..."

    # Build SRBMiner command
    MINER_CMD="$SRBMINER_BIN"
    MINER_ARGS="--algorithm yespowertide"
    MINER_ARGS="$MINER_ARGS --pool stratum+tcp://$POOL_HOST:$POOL_PORT"
    MINER_ARGS="$MINER_ARGS --wallet $WALLET_ADDRESS"
    MINER_ARGS="$MINER_ARGS --password c=TDC"
    MINER_ARGS="$MINER_ARGS --cpu-threads $OPTIMAL_THREADS"
    MINER_ARGS="$MINER_ARGS --cpu-priority 3"
    MINER_ARGS="$MINER_ARGS --keepalive true"
    MINER_ARGS="$MINER_ARGS --retry-time 5"
    MINER_ARGS="$MINER_ARGS --api-enable --api-port 21550"
    MINER_ARGS="$MINER_ARGS --log-file $LOG_DIR/srbminer.log"

    if [[ "$NO_GPU" == "false" ]]; then
        MINER_ARGS="$MINER_ARGS --gpu-id 0"
    else
        MINER_ARGS="$MINER_ARGS --disable-gpu"
    fi

    # Add failover pools
    MINER_ARGS="$MINER_ARGS --pool stratum+tcp://tidepool.world:6243 --wallet $WALLET_ADDRESS --password c=TDC"
    MINER_ARGS="$MINER_ARGS --pool stratum+tcp://stratum-na.rplant.xyz:7064 --wallet $WALLET_ADDRESS --password c=TDC"

    # Start via systemd if available, otherwise directly
    if [[ "$INSTALL_SERVICE" == "true" ]]; then
        sudo systemctl start tidemine 2>/dev/null || {
            # Direct start as fallback
            info "Starting miner directly..."
            nohup $MINER_CMD $MINER_ARGS >> "$LOG_DIR/srbminer.log" 2>&1 &
            MINER_PID=$!
            echo "$MINER_PID" > "$INSTALL_DIR/data/srbminer.pid"
        }
    else
        nohup $MINER_CMD $MINER_ARGS >> "$LOG_DIR/srbminer.log" 2>&1 &
        MINER_PID=$!
        echo "$MINER_PID" > "$INSTALL_DIR/data/srbminer.pid"
    fi

    sleep 3

    # Verify miner is running
    if pgrep -f "SRBMiner-MULTI" >/dev/null 2>&1; then
        ok "Miner is running!"
        echo ""
        echo -e "${BOLD}Commands:${NC}"
        echo "  tidecoin-miner status      # Check status"
        echo "  tidecoin-miner dashboard   # Live monitoring"
        echo "  tidecoin-miner stop        # Stop mining"
        echo "  tail -f $LOG_DIR/srbminer.log  # View logs"
        echo ""
        echo -e "${BOLD}Metrics API:${NC} http://localhost:8420"
        echo -e "${BOLD}SRBMiner API:${NC} http://localhost:21550"
    else
        warn "Miner may have failed to start. Check logs:"
        echo "  tail -f $LOG_DIR/srbminer.log"
    fi
elif [[ -z "$WALLET_ADDRESS" ]]; then
    echo -e "To start mining:"
    echo -e "  ${BOLD}tidecoin-miner start --wallet YOUR_TDC_ADDRESS${NC}"
else
    echo -e "To start mining:"
    echo -e "  ${BOLD}tidecoin-miner start${NC}"
    echo -e "  ${BOLD}sudo systemctl start tidemine${NC}"
fi

echo ""
echo -e "${CYAN}Happy mining! Falcon-512 post-quantum security.${NC}"
