## OBJECTIVE

Build a production-grade, continuous Tidecoin (TDC) mining system optimized for an i9 CPU + RTX 5070 Ti GPU on Ubuntu 24.04. Tidecoin is a post-quantum cryptocurrency using Falcon-512 (NIST FN-DSA) signatures with YesPowerTide proof-of-work. This is a CPU-friendly algorithm but SRBMiner-MULTI recently added NVIDIA GPU support for yespowertide — we want to exploit BOTH CPU and GPU simultaneously.

## SYSTEM SPECS

- CPU: Intel Core i9 (latest gen, high core count)
- GPU: NVIDIA RTX 5070 Ti (CUDA, 16GB VRAM)
- OS: Ubuntu 24.04 LTS
- CUDA toolkit installed, nvidia-smi working
- Python 3.12+, Node.js 20+, Rust toolchain available
- Network: residential connection, Hudson Valley NY area

## ARCHITECTURE

Build a modular mining orchestrator in Python with the following components:

### 1. `miner_core/` — Mining Engine Manager

- Auto-download and install SRBMiner-MULTI latest release from GitHub (https://github.com/doktor83/SRBMiner-Multi/releases)
- CRITICAL: Do NOT use `--disable-gpu` flag. SRBMiner added NVIDIA GPU support for yespowertide. We want both CPU and GPU mining simultaneously
- Configure SRBMiner with optimal parameters:
  - `--algorithm yespowertide`
  - `--cpu-threads` set to (total_cores - 2) to leave headroom for system
  - `--gpu-id 0` to explicitly enable the RTX 5070 Ti
  - `--pool stratum+tcp://{selected_pool}`
  - `--wallet {tdc_address}`
  - `--password c=TDC`
  - `--log-file` for structured logging
  - `--api-enable --api-port 21550` for real-time stats via HTTP API
- Also support cpuminer-opt as a fallback CPU-only miner (build from source: https://github.com/JayDDee/cpuminer-opt with `-a yespowertide`)
- Miner process management: start, stop, restart, health check via PID tracking
- Parse SRBMiner's JSON API at http://127.0.0.1:21550 for real-time hashrate, accepted shares, rejected shares, GPU temp, GPU power, CPU hashrate vs GPU hashrate breakdown

### 2. `pool_manager/` — Multi-Pool Failover & Optimization

Implement intelligent pool selection and failover:

```
POOL_REGISTRY = {
    "tidecoin_official": {"host": "pool.tidecoin.exchange", "port": 3032, "fee": "1%"},
    "tidepool_world":    {"host": "tidepool.world",         "port": 6243, "fee": "1%"},
    "rplant_na":         {"host": "stratum-na.rplant.xyz",  "port": 7064, "fee": "1%"},
    "rplant_eu":         {"host": "stratum-eu.rplant.xyz",  "port": 7064, "fee": "1%"},
    "zpool":             {"host": "yespowertide.mine.zpool.ca", "port": 6243, "fee": "dynamic"},
    "zergpool":          {"host": "yespowertide.mine.zergpool.com", "port": 6243, "fee": "dynamic"},
}
```

- Pool health checking: ping latency, stale share rate tracking, connection stability
- Auto-failover: if current pool has >3% stale rate or goes down, switch to next best
- Pool rotation strategy: prefer pools with lowest latency from Hudson Valley NY
- Log pool performance metrics over time for optimization

### 3. `monitor/` — Real-Time Dashboard & Alerting

Build a terminal UI dashboard (using `rich` library) that displays:

- **Header**: Tidecoin branding, Falcon-512 / NIST FN-DSA badge, uptime counter
- **Hashrate Panel**: 
  - CPU hashrate (per-thread breakdown)
  - GPU hashrate (RTX 5070 Ti)
  - Combined total hashrate
  - 1min / 5min / 15min moving averages
  - Hashrate sparkline graph (last 60 data points)
- **Shares Panel**: accepted, rejected, stale counts and rates
- **Hardware Panel**:
  - CPU: per-core utilization, temperature (via `sensors`), frequency
  - GPU: utilization %, temperature, power draw, VRAM usage, fan speed (via `nvidia-smi` or `pynvml`)
  - Power efficiency: hashes per watt
- **Earnings Panel**:
  - TDC mined this session
  - TDC mined today
  - Estimated daily/weekly/monthly TDC at current rate
  - USD value estimate (fetch TDC price from CoinGecko API: https://api.coingecko.com/api/v3/simple/price?ids=tidecoin&vs_currencies=usd)
- **Pool Panel**: current pool, latency, uptime, share acceptance rate
- **Log Panel**: scrolling recent events (block found, share accepted, errors, pool switches)

Dashboard refresh interval: 5 seconds
Also expose metrics via a local JSON API at :8420 for external monitoring

### 4. `wallet/` — Wallet Integration

- Auto-download Tidecoin node binary from https://github.com/tidecoin/tidecoin/releases/download/v0.18.3/linux64.tar.gz
- Manage tidecoin.conf with RPC credentials
- Wallet address generation and validation
- Balance checking via RPC: `tidecoin-cli getbalance`
- Transaction history monitoring
- New block detection: compare `getblockcount` on interval, log when our miner finds a block

### 5. `optimizer/` — Performance Tuning

Auto-tune mining parameters for maximum hashrate:

- **CPU optimization**:
  - Detect CPU architecture (Intel vs AMD, generation, cache sizes)
  - Set optimal thread count (experiment: n-1, n-2, n/2 cores and measure hashrate per thread)
  - Enable huge pages: `sudo sysctl -w vm.nr_hugepages=1280` (critical for YesPower performance — can give 20-30% boost)
  - CPU governor: set to `performance` mode
  - NUMA awareness if applicable
  - Thread affinity/pinning for cache optimization
- **GPU optimization**:
  - Query GPU capabilities via pynvml
  - Test different SRBMiner GPU intensity settings
  - Monitor for thermal throttling and auto-reduce intensity if GPU > 85°C
  - Power limit tuning: start at 80% TDP, measure hashrate/watt, find optimal point
- **Benchmark mode**: `--benchmark` flag that runs 60-second tests across different thread/intensity configs and reports optimal settings
- Save optimal config to `~/.tidecoin-miner/optimal_config.json`

### 6. `config/` — Configuration Management

YAML-based configuration with sensible defaults:

```yaml
# ~/.tidecoin-miner/config.yaml
wallet:
  address: ""  # Required: TDC wallet address
  node_path: "~/tidecoin-mining/tidecoin-wallet"

mining:
  algorithm: yespowertide
  miner: srbminer  # srbminer | cpuminer
  cpu_threads: auto  # auto = nproc - 2
  gpu_enabled: true
  gpu_id: 0
  huge_pages: true
  cpu_governor: performance

pool:
  primary: tidecoin_official
  failover_order: [tidepool_world, rplant_na, zpool]
  max_stale_rate: 0.03
  health_check_interval: 30

monitor:
  dashboard: true
  api_port: 8420
  refresh_interval: 5
  price_check_interval: 300

alerts:
  hashrate_drop_threshold: 0.5  # alert if hashrate drops >50%
  gpu_temp_max: 85
  notify_on_block_found: true

logging:
  level: INFO
  file: ~/.tidecoin-miner/logs/miner.log
  max_size_mb: 100
  rotate_count: 5
```

### 7. `cli.py` — Command Line Interface

Using `click` or `typer`:

```
tidecoin-miner start [--wallet ADDR] [--pool POOL] [--threads N] [--no-gpu] [--benchmark]
tidecoin-miner stop
tidecoin-miner status          # Quick status check
tidecoin-miner dashboard       # Full rich terminal UI
tidecoin-miner benchmark       # Run optimization benchmark
tidecoin-miner pools           # List pools with latency test
tidecoin-miner earnings        # Show earnings summary
tidecoin-miner config          # Edit config interactively
tidecoin-miner install         # Download miner binaries + wallet
tidecoin-miner update          # Update miner to latest version
```

### 8. `systemd/` — Service Integration

Generate a systemd service file for auto-start on boot:

```ini
[Unit]
Description=Tidecoin Post-Quantum CPU+GPU Miner
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=%u
ExecStart=/usr/local/bin/tidecoin-miner start --daemon
ExecStop=/usr/local/bin/tidecoin-miner stop
Restart=always
RestartSec=30
Nice=-10

[Install]
WantedBy=multi-user.target
```

Also include a `tidecoin-miner install-service` command that sets this up.

## KEY TECHNICAL DETAILS

### YesPowerTide Algorithm
- Variant of YesPower (memory-hard PoW)
- Designed to be CPU-friendly, ASIC-resistant
- SRBMiner-MULTI added NVIDIA GPU support in recent versions (changelog: "Added NVIDIA GPU support for algorithms 'yespower' and 'yespowertide'")
- Huge pages are CRITICAL for performance — always enable them
- The algorithm is memory-bandwidth bound, so fast RAM and large L3 cache help

### Tidecoin Network
- Falcon-512 post-quantum signatures (NIST standardized as FN-DSA)
- 21 million total supply (same as Bitcoin)
- PoW consensus, ~2.5 min block time
- Block reward: check via RPC `getblocksubsidy` or calculate from halving schedule
- Current price: ~$0.04-0.08 USD (very thin liquidity)
- Blockchain explorer: https://tidecoin.org/explorer
- Pool stats: https://miningpoolstats.stream/tidecoin

### SRBMiner API
The SRBMiner HTTP API at port 21550 returns JSON:
```json
{
  "algorithms": [{
    "hashrate": {"cpu": {"total": 1234.5}, "gpu": {"total": 5678.9}},
    "pool": {"accepted_shares": 100, "rejected_shares": 2, "stale_shares": 1},
    ...
  }],
  "gpu_devices": [{"temperature": 65, "power": 180, "fan_speed": 45, ...}],
  "cpu": {"temperature": 72, "threads": 10}
}
```

## FILE STRUCTURE

```
tidecoin-miner/
├── pyproject.toml              # Poetry/pip project config
├── README.md                   # Setup guide + usage docs
├── tidecoin_miner/
│   ├── __init__.py
│   ├── cli.py                  # Typer CLI entrypoint
│   ├── config.py               # YAML config management
│   ├── miner_core/
│   │   ├── __init__.py
│   │   ├── srbminer.py         # SRBMiner process manager
│   │   ├── cpuminer.py         # cpuminer-opt fallback
│   │   ├── installer.py        # Binary downloader/updater
│   │   └── process.py          # Generic process management
│   ├── pool_manager/
│   │   ├── __init__.py
│   │   ├── pools.py            # Pool registry + health checking
│   │   └── failover.py         # Failover logic
│   ├── monitor/
│   │   ├── __init__.py
│   │   ├── dashboard.py        # Rich terminal dashboard
│   │   ├── stats.py            # Stats collection from SRBMiner API
│   │   ├── api.py              # Local JSON metrics API
│   │   └── alerts.py           # Alerting on thresholds
│   ├── wallet/
│   │   ├── __init__.py
│   │   ├── node.py             # Tidecoin node management
│   │   └── balance.py          # Balance/earnings tracking
│   ├── optimizer/
│   │   ├── __init__.py
│   │   ├── benchmark.py        # Auto-tuning benchmarks
│   │   ├── hugepages.py        # Huge pages setup
│   │   └── tuner.py            # CPU/GPU parameter tuning
│   └── systemd/
│       ├── __init__.py
│       └── service.py          # systemd service generator
├── tests/
│   └── ...
└── scripts/
    └── quick_start.sh          # One-liner bootstrap script
```

## DEPENDENCIES

```toml
[tool.poetry.dependencies]
python = "^3.10"
typer = "^0.9"
rich = "^13.0"
pyyaml = "^6.0"
httpx = "^0.27"
psutil = "^5.9"
pynvml = "^11.5"
aiohttp = "^3.9"

[tool.poetry.scripts]
tidecoin-miner = "tidecoin_miner.cli:app"
```

## CRITICAL REQUIREMENTS

1. **DO NOT use `--disable-gpu`** — this is the key insight. Official docs are wrong/outdated. SRBMiner supports NVIDIA GPU mining for yespowertide
2. **Always enable huge pages** — this is a 20-30% hashrate boost for free on YesPower algorithms
3. **Auto-restart on crash** — miner must be resilient, auto-recover from any failure
4. **Dual mining CPU+GPU** — the whole point is to use both the i9 AND the RTX 5070 Ti simultaneously
5. **Pool failover** — never stop mining due to a pool outage
6. **Thermal protection** — auto-throttle if GPU > 85°C or CPU > 95°C to protect the laptop
7. **Clean shutdown** — handle SIGTERM/SIGINT gracefully, stop miner process, save state
8. **Earnings tracking** — persistent SQLite database tracking shares, hashrate, earnings over time
9. **The dashboard must be beautiful** — use rich library with panels, tables, sparklines, color coding
10. **Make it installable** — `pip install -e .` should work, `tidecoin-miner install && tidecoin-miner start` should be all someone needs

## QUICK START FLOW

When a user runs `tidecoin-miner install && tidecoin-miner start`:

1. Download SRBMiner-MULTI latest release, extract to ~/.tidecoin-miner/bin/
2. Download Tidecoin wallet binary, extract
3. Prompt for TDC wallet address (or generate one via node)
4. Enable huge pages (with sudo prompt)
5. Set CPU governor to performance
6. Run 60-second benchmark to find optimal thread count + GPU intensity
7. Save optimal config
8. Start mining with CPU + GPU on best pool
9. Launch dashboard showing live stats
10. Log everything, auto-restart on failure, auto-failover on pool issues

Build the entire system. Make it production-quality. This is going to run 24/7 on a beast machine.
