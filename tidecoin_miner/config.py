"""Configuration management for Tidemine."""

import os
import yaml
from pathlib import Path
from typing import Any

BASE_DIR = Path.home() / ".tidecoin-miner"
CONFIG_PATH = BASE_DIR / "config.yaml"
BIN_DIR = BASE_DIR / "bin"
LOG_DIR = BASE_DIR / "logs"
DATA_DIR = BASE_DIR / "data"

POOL_REGISTRY = {
    "tidecoin_official": {"host": "pool.tidecoin.exchange", "port": 3032, "fee": "1%"},
    "tidepool_world":    {"host": "tidepool.world",         "port": 6243, "fee": "1%"},
    "rplant_na":         {"host": "stratum-na.rplant.xyz",  "port": 7064, "fee": "1%"},
    "rplant_eu":         {"host": "stratum-eu.rplant.xyz",  "port": 7064, "fee": "1%"},
    "zpool":             {"host": "yespowertide.mine.zpool.ca", "port": 6243, "fee": "dynamic"},
    "zergpool":          {"host": "yespowertide.mine.zergpool.com", "port": 6243, "fee": "dynamic"},
}

DEFAULT_CONFIG = {
    "wallet": {
        "address": "",
        "node_path": str(BASE_DIR / "tidecoin-wallet"),
    },
    "mining": {
        "algorithm": "yespowertide",
        "miner": "srbminer",
        "cpu_threads": "auto",
        "gpu_enabled": True,   # GPU mining works on SRBMiner 3.2.5 (confirmed Blackwell/5070Ti)
        "gpu_id": 0,
        "huge_pages": True,
        "cpu_governor": "performance",
        "cpu_affinity": "auto",  # auto = P-cores only on hybrid Intel
        "numa_bind": True,       # Bind to NUMA node 0 for cache locality
    },
    "pool": {
        "primary": "tidecoin_official",
        "failover_order": ["tidepool_world", "rplant_na", "zpool"],
        "max_stale_rate": 0.03,
        "health_check_interval": 30,
    },
    "monitor": {
        "dashboard": True,
        "api_port": 8420,
        "refresh_interval": 5,
        "price_check_interval": 300,
    },
    "alerts": {
        "hashrate_drop_threshold": 0.5,
        "cpu_temp_max": 95,
        "notify_on_block_found": True,
    },
    "logging": {
        "level": "INFO",
        "file": str(LOG_DIR / "miner.log"),
        "max_size_mb": 100,
        "rotate_count": 5,
    },
}


def ensure_dirs():
    """Create all required directories."""
    for d in [BASE_DIR, BIN_DIR, LOG_DIR, DATA_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def load_config() -> dict[str, Any]:
    """Load config from disk, merging with defaults."""
    ensure_dirs()
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            user_cfg = yaml.safe_load(f) or {}
        return _deep_merge(DEFAULT_CONFIG, user_cfg)
    return DEFAULT_CONFIG.copy()


def save_config(cfg: dict[str, Any]):
    """Save config to disk."""
    ensure_dirs()
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)


def get_pool_url(pool_name: str) -> str:
    """Get stratum URL for a pool."""
    pool = POOL_REGISTRY[pool_name]
    return f"stratum+tcp://{pool['host']}:{pool['port']}"


def get_cpu_threads(cfg: dict) -> int:
    """Resolve CPU thread count."""
    import psutil
    threads = cfg["mining"]["cpu_threads"]
    if threads == "auto":
        # Use physical cores minus 2 for system headroom
        physical = psutil.cpu_count(logical=False) or 4
        return max(1, physical - 2)
    return int(threads)


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base."""
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result
