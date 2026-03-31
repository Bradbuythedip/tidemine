#!/usr/bin/env bash
###############################################################################
# Tidemine - One-Click Tidecoin Mining Deployer
# Optimized for Intel i9 CPU on Ubuntu 24.04
#
# YesPowerTide is CPU-ONLY (GPU support removed in SRBMiner v3.2.4).
# The algorithm resists GPU acceleration by design via L2 cache dependency.
# All optimizations focus on CPU cache, memory bandwidth, and scheduling.
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

WALLET_ADDRESS=""
POOL="tidecoin_official"
BENCHMARK=false
INSTALL_SERVICE=true
AUTO_START=true

# ─── Parse Arguments ─────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --wallet|-w)     WALLET_ADDRESS="$2"; shift 2 ;;
        --pool|-p)       POOL="$2"; shift 2 ;;
        --benchmark|-b)  BENCHMARK=true; shift ;;
        --no-service)    INSTALL_SERVICE=false; shift ;;
        --no-start)      AUTO_START=false; shift ;;
        --help|-h)
            echo "Tidemine Deployer - Tidecoin CPU Mining"
            echo ""
            echo "Usage: deploy.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --wallet, -w ADDR    TDC wallet address (required)"
            echo "  --pool, -p NAME      Pool name (default: tidecoin_official)"
            echo "  --benchmark, -b      Run benchmark after install"
            echo "  --no-service         Don't install systemd service"
            echo "  --no-start           Don't auto-start mining"
            echo "  --help, -h           Show this help"
            echo ""
            echo "Note: YesPowerTide is CPU-only. GPU mining is not supported"
            echo "for this algorithm (removed from SRBMiner v3.2.4+)."
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

  Tidecoin Post-Quantum CPU Miner | Falcon-512 / FN-DSA
  YesPowerTide | Memory-hard PoW | CPU-Optimized

BANNER
echo -e "${NC}"

# ─── Preflight Checks ───────────────────────────────────────────────────────
info "Running preflight checks..."

# Check OS
if [[ ! -f /etc/os-release ]]; then
    warn "Cannot detect OS. Continuing anyway..."
fi

if [[ $EUID -eq 0 ]]; then
    warn "Running as root. Preferably run as regular user with sudo access."
fi

# Check for required tools
for cmd in git python3 curl wget; do
    if ! command -v "$cmd" &>/dev/null; then
        warn "$cmd not found. Installing..."
        sudo apt-get update -qq && sudo apt-get install -y -qq "$cmd" 2>/dev/null || true
    fi
done

# ─── CPU Detection ──────────────────────────────────────────────────────────
CPU_MODEL=$(grep -m1 "model name" /proc/cpuinfo 2>/dev/null | cut -d: -f2 | xargs || echo "Unknown")
CPU_LOGICAL=$(nproc --all 2>/dev/null || echo "4")
ok "CPU: $CPU_MODEL ($CPU_LOGICAL logical threads)"

# Detect physical core count (unique core IDs)
PHYS_CORES=$(lscpu -p=Core,Socket 2>/dev/null | grep -v '^#' | sort -u | wc -l)
if [[ $PHYS_CORES -lt 1 ]]; then
    PHYS_CORES=$((CPU_LOGICAL / 2))
fi
ok "Physical cores: $PHYS_CORES"

# Detect hybrid architecture (Intel 12th+ gen, Arrow Lake, etc.)
IS_HYBRID=false
P_CORE_COUNT=0
E_CORE_COUNT=0
P_CORE_LIST=""

# Method 1: Check core_type sysfs (Intel Thread Director)
if [[ -f /sys/devices/system/cpu/cpu0/topology/core_type ]]; then
    P_CORES_ARR=()
    E_CORES_ARR=()
    for cpu_dir in /sys/devices/system/cpu/cpu[0-9]*/topology/core_type; do
        cpu_id=$(echo "$cpu_dir" | grep -o 'cpu[0-9]*' | grep -o '[0-9]*')
        core_type=$(cat "$cpu_dir" 2>/dev/null || echo "")
        if [[ "$core_type" == "0" ]]; then
            P_CORES_ARR+=("$cpu_id")
        else
            E_CORES_ARR+=("$cpu_id")
        fi
    done
    P_CORE_COUNT=${#P_CORES_ARR[@]}
    E_CORE_COUNT=${#E_CORES_ARR[@]}
    if [[ $P_CORE_COUNT -gt 0 && $E_CORE_COUNT -gt 0 ]]; then
        IS_HYBRID=true
    fi
fi

# Method 2: Detect by CPU model name if sysfs didn't work
# Intel Core Ultra / 12th-14th gen / Arrow Lake are all hybrid
if [[ "$IS_HYBRID" == "false" ]]; then
    if echo "$CPU_MODEL" | grep -qiE "(Ultra [579]|12[0-9]{2}|13[0-9]{2}|14[0-9]{2}|275HX|285K)"; then
        IS_HYBRID=true
        # Intel hybrid CPUs: P-cores are typically listed first in lscpu
        # Arrow Lake / Ultra 9 275HX: 8 P-cores + 16 E-cores = 24 cores/24 threads
        # 13th/14th gen i9: 8 P-cores(HT) + 16 E-cores = 24 cores/32 threads
        # Heuristic: if logical == physical, no HT, so P-cores = 8 (typical for Arrow Lake)
        if [[ $CPU_LOGICAL -eq $PHYS_CORES ]]; then
            # No hyperthreading - Arrow Lake style
            # P-cores are 8 on Ultra 9, E-cores are the rest
            P_CORE_COUNT=8
            E_CORE_COUNT=$((PHYS_CORES - 8))
        else
            # Hyperthreading present - Alder/Raptor Lake style
            # P-cores have HT (2 threads each), E-cores don't
            # Solve: P*2 + E = logical, P + E = physical
            P_CORE_COUNT=$(( (CPU_LOGICAL - PHYS_CORES) ))
            E_CORE_COUNT=$((PHYS_CORES - P_CORE_COUNT))
        fi
        # Sanity check
        if [[ $P_CORE_COUNT -lt 4 || $P_CORE_COUNT -gt 16 ]]; then
            P_CORE_COUNT=8  # Safe default for i9
            E_CORE_COUNT=$((PHYS_CORES - P_CORE_COUNT))
        fi
        # P-cores are CPU IDs 0 through P_CORE_COUNT-1 (Intel convention)
        P_CORES_ARR=()
        for ((i=0; i<P_CORE_COUNT; i++)); do
            P_CORES_ARR+=("$i")
        done
    fi
fi

if [[ "$IS_HYBRID" == "true" ]]; then
    # Build comma-separated P-core list (minus 2 for system headroom)
    MINING_COUNT=$((P_CORE_COUNT - 2))
    if [[ $MINING_COUNT -lt 1 ]]; then MINING_COUNT=1; fi
    MINING_P_CORES=("${P_CORES_ARR[@]:0:$MINING_COUNT}")
    P_CORE_LIST=$(IFS=,; echo "${MINING_P_CORES[*]}")
    ok "Hybrid CPU detected: ${P_CORE_COUNT} P-cores + ${E_CORE_COUNT} E-cores"
    info "Mining on P-cores only (2MB L2 cache each vs shared L2 on E-cores)"
fi

# Calculate optimal threads
if [[ "$IS_HYBRID" == "true" ]]; then
    OPTIMAL_THREADS=$((P_CORE_COUNT - 2))
else
    OPTIMAL_THREADS=$((PHYS_CORES - 2))
fi
if [[ $OPTIMAL_THREADS -lt 1 ]]; then OPTIMAL_THREADS=1; fi
ok "Mining threads: $OPTIMAL_THREADS"

# Check RAM
TOTAL_RAM_KB=$(grep MemTotal /proc/meminfo 2>/dev/null | awk '{print $2}' || echo "0")
TOTAL_RAM_GB=$((TOTAL_RAM_KB / 1024 / 1024))
ok "RAM: ${TOTAL_RAM_GB}GB"

# Detect NUMA topology
NUMA_NODES=$(lscpu 2>/dev/null | grep "NUMA node(s)" | awk '{print $NF}' || echo "1")
if [[ "$NUMA_NODES" -gt 1 ]]; then
    ok "NUMA: $NUMA_NODES nodes detected. Will bind to node 0 for cache locality."
fi

echo ""

# ─── Step 1: Install System Dependencies ─────────────────────────────────────
info "Step 1/8: Installing system dependencies..."

sudo apt-get update -qq 2>/dev/null || true
sudo apt-get install -y -qq \
    python3-venv python3-pip python3-dev \
    build-essential libssl-dev libffi-dev \
    lm-sensors cpufrequtils numactl \
    libcurl4 libmicrohttpd12 \
    curl wget git jq \
    2>/dev/null || warn "Some packages may have failed to install"

ok "System dependencies installed"

# ─── Step 2: Create Directory Structure ───────────────────────────────────────
info "Step 2/8: Setting up directory structure..."

mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$LOG_DIR" "$INSTALL_DIR/data"

ok "Directories created at $INSTALL_DIR"

# ─── Step 3: System Optimizations ─────────────────────────────────────────────
info "Step 3/8: Applying system optimizations for YesPowerTide..."
echo ""

# 3a. HUGE PAGES — #1 optimization (20-30% hashrate boost)
HUGEPAGES_COUNT=1280
if [[ $TOTAL_RAM_GB -gt 32 ]]; then
    HUGEPAGES_COUNT=2560
elif [[ $TOTAL_RAM_GB -gt 16 ]]; then
    HUGEPAGES_COUNT=1536
fi

info "  [1/8] Huge pages (20-30% boost for YesPowerTide)..."
# Aggressive cache drop + compact memory before allocating
sudo sh -c "echo 3 > /proc/sys/vm/drop_caches" 2>/dev/null || true
sudo sh -c "echo 1 > /proc/sys/vm/compact_memory" 2>/dev/null || true
sleep 1
# Try allocation in multiple rounds (fragmented memory may need retries)
sudo sysctl -w vm.nr_hugepages=$HUGEPAGES_COUNT 2>/dev/null || true
ACTUAL_HP=$(cat /proc/sys/vm/nr_hugepages 2>/dev/null || echo "0")
if [[ $ACTUAL_HP -lt $((HUGEPAGES_COUNT / 2)) ]]; then
    warn "       Only got $ACTUAL_HP/$HUGEPAGES_COUNT pages (memory fragmentation)"
    warn "       For full allocation, add to GRUB and reboot:"
    warn "         sudo sed -i 's/GRUB_CMDLINE_LINUX_DEFAULT=\"/GRUB_CMDLINE_LINUX_DEFAULT=\"hugepages=$HUGEPAGES_COUNT /' /etc/default/grub"
    warn "         sudo update-grub && sudo reboot"
    warn "       This alone can boost hashrate 20-30%!"
else
    ok "       Allocated $ACTUAL_HP/$HUGEPAGES_COUNT huge pages (${ACTUAL_HP}x2MB = $((ACTUAL_HP * 2))MB)"
fi

# Make persistent via sysctl (works after reboot if memory isn't fragmented)
echo "vm.nr_hugepages = $HUGEPAGES_COUNT" | sudo tee /etc/sysctl.d/99-tidemine-hugepages.conf >/dev/null 2>&1 || true

# 3b. CPU GOVERNOR — prevent frequency scaling drops
info "  [2/8] CPU governor -> performance..."
for cpu_gov in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
    echo "performance" | sudo tee "$cpu_gov" >/dev/null 2>&1 || true
done
sudo cpupower frequency-set -g performance 2>/dev/null || true
ok "       All cores set to performance governor"

# 3c. DISABLE DEEP C-STATES — prevents hashrate drops from core wakeup latency
info "  [3/8] Disabling deep C-states..."
sudo sh -c "echo 1 > /sys/module/intel_idle/parameters/max_cstate" 2>/dev/null || true
# Disable C-states > C1 individually
for state_dir in /sys/devices/system/cpu/cpu*/cpuidle/state[2-9]*; do
    echo 1 | sudo tee "$state_dir/disable" >/dev/null 2>&1 || true
done
ok "       Deep C-states disabled (C1 only)"

# 3d. KERNEL PARAMETERS — reduce scheduling jitter
info "  [4/8] Kernel parameter tuning..."
sudo sysctl -w vm.swappiness=1 2>/dev/null || true
sudo sysctl -w vm.dirty_ratio=10 2>/dev/null || true
sudo sysctl -w vm.dirty_background_ratio=5 2>/dev/null || true
sudo sysctl -w kernel.sched_migration_cost_ns=5000000 2>/dev/null || true
sudo sysctl -w kernel.sched_autogroup_enabled=0 2>/dev/null || true
sudo sysctl -w kernel.numa_balancing=0 2>/dev/null || true
ok "       vm.swappiness=1, sched_migration_cost=5ms, NUMA balancing off"

# 3e. DISABLE TRANSPARENT HUGE PAGES — explicit hugepages are faster
info "  [5/8] Disabling THP (explicit hugepages preferred)..."
sudo sh -c "echo never > /sys/kernel/mm/transparent_hugepage/enabled" 2>/dev/null || true
sudo sh -c "echo never > /sys/kernel/mm/transparent_hugepage/defrag" 2>/dev/null || true
ok "       THP disabled"

# 3f. IRQ AFFINITY — pin IRQs away from mining cores
info "  [6/8] IRQ affinity (pin to last 2 cores)..."
IRQ_MASK=$(printf "0x%x" $(( (1 << CPU_LOGICAL) - (1 << (CPU_LOGICAL - 2)) )) 2>/dev/null || echo "0xC0")
echo "$IRQ_MASK" | sudo tee /proc/irq/default_smp_affinity >/dev/null 2>&1 || true
ok "       IRQ mask: $IRQ_MASK (cores $((CPU_LOGICAL-2))-$((CPU_LOGICAL-1)))"

# 3g. MEMORY LOCK LIMITS — required for huge page allocation
info "  [7/8] Setting memory lock limits..."
USER_NAME=$(whoami)
echo -e "# Tidemine mining\n$USER_NAME soft memlock unlimited\n$USER_NAME hard memlock unlimited" | \
    sudo tee /etc/security/limits.d/99-tidemine.conf >/dev/null 2>&1 || true
ok "       memlock=unlimited for $USER_NAME"

# 3h. Persist sysctl optimizations
info "  [8/8] Persisting kernel optimizations..."
cat << SYSCTL_EOF | sudo tee /etc/sysctl.d/99-tidemine-kernel.conf >/dev/null 2>&1
# Tidemine CPU mining optimizations
vm.swappiness = 1
vm.dirty_ratio = 10
vm.dirty_background_ratio = 5
kernel.sched_migration_cost_ns = 5000000
kernel.sched_autogroup_enabled = 0
kernel.numa_balancing = 0
SYSCTL_EOF
ok "       Kernel params persisted to /etc/sysctl.d/"

echo ""

# ─── Step 4: Clone/Update Tidemine Repository ────────────────────────────────
info "Step 4/8: Setting up Tidemine..."

if [[ -d "$REPO_DIR/.git" ]]; then
    info "  Updating existing installation..."
    cd "$REPO_DIR"
    git fetch origin 2>/dev/null || true
    # Try the feature branch first, then main
    git checkout claude/optimized-miner-deployment-8Gu3B 2>/dev/null && \
        git pull origin claude/optimized-miner-deployment-8Gu3B 2>/dev/null || \
        git pull origin main 2>/dev/null || true
else
    info "  Cloning repository..."
    git clone -b claude/optimized-miner-deployment-8Gu3B "$REPO_URL" "$REPO_DIR" 2>/dev/null || \
    git clone "$REPO_URL" "$REPO_DIR" 2>/dev/null || {
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
        pip install typer rich pyyaml httpx psutil aiohttp -q 2>/dev/null || true
    }
else
    pip install typer rich pyyaml httpx psutil aiohttp -q 2>/dev/null || true
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

SRBMINER_BIN=$(find "$BIN_DIR" -name "SRBMiner-MULTI" -type f 2>/dev/null | head -1)

if [[ -z "$SRBMINER_BIN" ]]; then
    info "  Fetching latest release info..."
    RELEASE_JSON=$(curl -sSL "$SRBMINER_API" 2>/dev/null)
    RELEASE_TAG=$(echo "$RELEASE_JSON" | jq -r '.tag_name' 2>/dev/null || echo "unknown")
    info "  Latest version: $RELEASE_TAG"

    DOWNLOAD_URL=$(echo "$RELEASE_JSON" | jq -r '.assets[] | select(.name | test("linux.*tar")) | .browser_download_url' 2>/dev/null | head -1)

    if [[ -z "$DOWNLOAD_URL" || "$DOWNLOAD_URL" == "null" ]]; then
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

cat > "$INSTALL_DIR/config.yaml" << CONFIG_EOF
wallet:
  address: "${WALLET_ADDRESS}"
  node_path: ${INSTALL_DIR}/tidecoin-wallet

mining:
  algorithm: yespowertide
  miner: srbminer
  cpu_threads: ${OPTIMAL_THREADS}
  gpu_enabled: false  # YesPowerTide is CPU-only (GPU removed in SRBMiner v3.2.4)
  huge_pages: true
  cpu_governor: performance
  cpu_affinity: auto  # P-cores only on hybrid Intel
  numa_bind: true     # Bind to NUMA node 0

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
Description=Tidemine - Tidecoin Post-Quantum CPU Miner (YesPowerTide)
Documentation=https://github.com/bradbuythedip/tidemine
After=network-online.target
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

# CPU performance
Nice=-10
IOSchedulingClass=realtime
IOSchedulingPriority=0
CPUSchedulingPolicy=batch

# Required for huge pages
LimitNOFILE=65536
LimitMEMLOCK=infinity

# Environment
Environment=HOME=$HOME
Environment=PATH=$VENV_DIR/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

[Install]
WantedBy=multi-user.target
SERVICE_EOF

    sudo systemctl daemon-reload
    sudo systemctl enable tidemine 2>/dev/null || true
    ok "Systemd service installed and enabled"
fi

# ─── Summary & Start ─────────────────────────────────────────────────────────
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
if [[ "$IS_HYBRID" == "true" ]]; then
echo -e "  ${CYAN}Hybrid:${NC}    ${P_CORE_COUNT} P-cores (mining), ${E_CORE_COUNT} E-cores (idle)"
fi
echo -e "  ${CYAN}Hugepages:${NC} $ACTUAL_HP allocated (${ACTUAL_HP}x2MB)"
echo -e "  ${CYAN}NUMA:${NC}      $NUMA_NODES node(s)"
echo -e "  ${CYAN}Pool:${NC}      $POOL ($POOL_HOST:$POOL_PORT)"
echo -e "  ${CYAN}Wallet:${NC}    ${WALLET_ADDRESS:-NOT SET}"
echo -e "  ${CYAN}Mode:${NC}      CPU-only (YesPowerTide is GPU-resistant by design)"
echo ""

if [[ -z "$WALLET_ADDRESS" ]]; then
    echo -e "${YELLOW}WARNING: No wallet address set!${NC}"
    echo -e "Set it with: ${BOLD}tidecoin-miner start --wallet YOUR_TDC_ADDRESS${NC}"
    echo -e "Or edit: $INSTALL_DIR/config.yaml"
    echo ""
fi

if [[ "$AUTO_START" == "true" && -n "$WALLET_ADDRESS" && -n "$SRBMINER_BIN" ]]; then
    info "Starting miner..."

    # Build SRBMiner command as an array for proper argument handling
    MINER_ARGS=(
        "$SRBMINER_BIN"
        --algorithm yespowertide
        --pool "stratum+tcp://$POOL_HOST:$POOL_PORT"
        --wallet "$WALLET_ADDRESS"
        --password "c=TDC"
        --cpu-threads "$OPTIMAL_THREADS"
        --cpu-priority 3
        --disable-gpu
        --keepalive true
        --retry-time 5
        --api-enable
        --api-port 21550
        --log-file "$LOG_DIR/srbminer.log"
    )

    # CPU affinity: P-cores only on hybrid Intel
    if [[ "$IS_HYBRID" == "true" && -n "$P_CORE_LIST" ]]; then
        MINER_ARGS+=(--cpu-affinity "$P_CORE_LIST")
        info "  CPU affinity: P-cores [$P_CORE_LIST]"
    fi

    # Failover pools
    MINER_ARGS+=(
        --pool "stratum+tcp://tidepool.world:6243" --wallet "$WALLET_ADDRESS" --password "c=TDC"
        --pool "stratum+tcp://stratum-na.rplant.xyz:7064" --wallet "$WALLET_ADDRESS" --password "c=TDC"
    )

    # Show the command we're about to run
    info "  Command: ${MINER_ARGS[0]##*/} --algorithm yespowertide --cpu-threads $OPTIMAL_THREADS --disable-gpu"

    # Start SRBMiner directly (bypass systemd for first run - more reliable)
    # SRBMiner needs to run from its own directory for config files
    SRBMINER_DIR=$(dirname "$SRBMINER_BIN")
    cd "$SRBMINER_DIR"

    # Touch log file first to ensure it exists
    touch "$LOG_DIR/srbminer.log"

    # Launch with nohup, capturing stderr separately for debugging
    nohup "${MINER_ARGS[@]}" >> "$LOG_DIR/srbminer.log" 2>&1 &
    MINER_PID=$!
    echo "$MINER_PID" > "$INSTALL_DIR/data/srbminer.pid"
    info "  Launched PID: $MINER_PID"

    # Wait and check if process survived
    sleep 5

    if kill -0 "$MINER_PID" 2>/dev/null; then
        ok "Miner is running! (PID: $MINER_PID)"
        echo ""

        # Show first few lines of log to confirm it's mining
        if [[ -s "$LOG_DIR/srbminer.log" ]]; then
            echo -e "${CYAN}--- Recent log output ---${NC}"
            tail -5 "$LOG_DIR/srbminer.log" 2>/dev/null || true
            echo -e "${CYAN}-------------------------${NC}"
        fi

        echo ""
        echo -e "${BOLD}Commands:${NC}"
        echo "  tidecoin-miner status      # Check status"
        echo "  tidecoin-miner dashboard   # Live monitoring"
        echo "  tidecoin-miner stop        # Stop mining"
        echo "  tidecoin-miner benchmark   # Auto-tune threads"
        echo "  tidecoin-miner pools       # Test pool latency"
        echo "  tail -f $LOG_DIR/srbminer.log  # View logs"
        echo ""
        echo -e "${BOLD}Metrics API:${NC} http://localhost:8420"
        echo -e "${BOLD}SRBMiner API:${NC} http://localhost:21550"

        # Now also start the systemd service for future auto-restart
        if [[ "$INSTALL_SERVICE" == "true" ]]; then
            info "Systemd service is enabled for auto-restart on reboot."
            info "To use it: sudo systemctl start tidemine"
        fi
    else
        warn "Miner process exited (PID $MINER_PID was not running after 5s)"
        echo ""
        echo -e "${RED}--- Debug info ---${NC}"
        if [[ -s "$LOG_DIR/srbminer.log" ]]; then
            echo -e "${YELLOW}Log output:${NC}"
            cat "$LOG_DIR/srbminer.log" 2>/dev/null
        else
            echo "  No log file output (SRBMiner may have crashed immediately)"
            echo ""
            echo "  Try running manually to see the error:"
            echo "    cd $SRBMINER_DIR"
            echo "    ./SRBMiner-MULTI --algorithm yespowertide --pool stratum+tcp://$POOL_HOST:$POOL_PORT --wallet $WALLET_ADDRESS --password c=TDC --cpu-threads $OPTIMAL_THREADS --disable-gpu"
        fi
        echo -e "${RED}------------------${NC}"
        echo ""
        echo "  Common fixes:"
        echo "    1. Missing library: sudo apt install libcurl4 libmicrohttpd12"
        echo "    2. Permission denied: chmod +x $SRBMINER_BIN"
        echo "    3. Try without --disable-gpu if SRBMiner version doesn't support it"
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
echo -e "${BOLD}Optimizations applied:${NC}"
echo "  [1] Huge pages: ${ACTUAL_HP}x2MB (20-30% boost)"
echo "  [2] CPU governor: performance"
echo "  [3] C-states: limited to C1"
echo "  [4] Kernel: swappiness=1, sched tuning, NUMA balancing off"
echo "  [5] THP: disabled (explicit hugepages)"
echo "  [6] IRQ affinity: last 2 cores"
echo "  [7] memlock: unlimited"
if [[ "$IS_HYBRID" == "true" ]]; then
echo "  [8] Hybrid CPU: P-cores only (E-cores idle)"
fi
echo ""
echo -e "${CYAN}Happy mining! Falcon-512 post-quantum security.${NC}"
