"""SRBMiner-MULTI process manager with full CPU+GPU support."""

import json
import time
from pathlib import Path
from typing import Optional

import httpx

from tidecoin_miner.config import (
    LOG_DIR, load_config, get_pool_url, get_cpu_threads, POOL_REGISTRY
)
from tidecoin_miner.miner_core.installer import get_srbminer_path
from tidecoin_miner.miner_core.process import (
    start_process, stop_process, is_running, get_process_info
)

MINER_NAME = "srbminer"
API_PORT = 21550


def build_command(cfg: dict, pool_name: Optional[str] = None) -> list[str]:
    """Build SRBMiner command line with all optimizations."""
    binary = get_srbminer_path()
    if not binary.exists():
        raise FileNotFoundError(f"SRBMiner not found at {binary}. Run 'tidecoin-miner install' first.")

    pool = pool_name or cfg["pool"]["primary"]
    pool_url = get_pool_url(pool)
    wallet = cfg["wallet"]["address"]
    if not wallet:
        raise ValueError("Wallet address not configured. Set it in ~/.tidecoin-miner/config.yaml")

    threads = get_cpu_threads(cfg)

    cmd = [
        str(binary),
        "--algorithm", "yespowertide",
        "--pool", pool_url,
        "--wallet", wallet,
        "--password", "c=TDC",
        "--cpu-threads", str(threads),
        # GPU - CRITICAL: do NOT disable GPU
        "--gpu-id", str(cfg["mining"]["gpu_id"]),
        # API for monitoring
        "--api-enable",
        "--api-port", str(API_PORT),
        # Logging
        "--log-file", str(LOG_DIR / "srbminer.log"),
        # Performance
        "--cpu-priority", "3",
        "--randomx-use-1gb-pages",
        # Keepalive for pool connection stability
        "--keepalive", "true",
        # Retry on disconnect
        "--retry-time", "5",
    ]

    # Add failover pools
    failover_pools = cfg["pool"].get("failover_order", [])
    for fp in failover_pools:
        if fp in POOL_REGISTRY and fp != pool:
            cmd.extend(["--pool", get_pool_url(fp)])
            cmd.extend(["--wallet", wallet])
            cmd.extend(["--password", "c=TDC"])

    return cmd


def start(cfg: Optional[dict] = None, pool_name: Optional[str] = None) -> int:
    """Start SRBMiner with optimal configuration."""
    if cfg is None:
        cfg = load_config()

    cmd = build_command(cfg, pool_name)
    print(f"[*] Starting SRBMiner: {' '.join(cmd[:6])}...")

    proc = start_process(MINER_NAME, cmd, nice=-10)
    print(f"[OK] SRBMiner started (PID: {proc.pid})")

    # Wait briefly and verify it's running
    time.sleep(2)
    if not is_running(MINER_NAME):
        raise RuntimeError("SRBMiner failed to start. Check logs at ~/.tidecoin-miner/logs/srbminer.log")

    return proc.pid


def stop():
    """Stop SRBMiner gracefully."""
    if is_running(MINER_NAME):
        stop_process(MINER_NAME)
        print("[OK] SRBMiner stopped")
    else:
        print("[*] SRBMiner is not running")


def restart(cfg: Optional[dict] = None, pool_name: Optional[str] = None) -> int:
    """Restart SRBMiner."""
    stop()
    time.sleep(1)
    return start(cfg, pool_name)


def status() -> dict:
    """Get SRBMiner status."""
    running = is_running(MINER_NAME)
    info = get_process_info(MINER_NAME) if running else None
    api_data = get_api_stats() if running else None

    return {
        "running": running,
        "process": info,
        "stats": api_data,
    }


def get_api_stats() -> Optional[dict]:
    """Fetch real-time stats from SRBMiner HTTP API."""
    try:
        resp = httpx.get(f"http://127.0.0.1:{API_PORT}", timeout=5)
        if resp.status_code == 200:
            return resp.json()
    except (httpx.ConnectError, httpx.TimeoutException, json.JSONDecodeError):
        pass
    return None


def get_hashrate() -> dict:
    """Get current hashrate breakdown."""
    stats = get_api_stats()
    if not stats:
        return {"cpu": 0.0, "gpu": 0.0, "total": 0.0}

    try:
        algo = stats["algorithms"][0]
        cpu_hr = algo.get("hashrate", {}).get("cpu", {}).get("total", 0.0)
        gpu_hr = algo.get("hashrate", {}).get("gpu", {}).get("total", 0.0)
        return {
            "cpu": cpu_hr,
            "gpu": gpu_hr,
            "total": cpu_hr + gpu_hr,
        }
    except (KeyError, IndexError):
        return {"cpu": 0.0, "gpu": 0.0, "total": 0.0}


def get_shares() -> dict:
    """Get share statistics."""
    stats = get_api_stats()
    if not stats:
        return {"accepted": 0, "rejected": 0, "stale": 0, "rate": 0.0}

    try:
        algo = stats["algorithms"][0]
        pool = algo.get("pool", {})
        accepted = pool.get("accepted_shares", 0)
        rejected = pool.get("rejected_shares", 0)
        stale = pool.get("stale_shares", 0)
        total = accepted + rejected + stale
        rate = accepted / total if total > 0 else 0.0
        return {
            "accepted": accepted,
            "rejected": rejected,
            "stale": stale,
            "total": total,
            "acceptance_rate": rate,
            "stale_rate": stale / total if total > 0 else 0.0,
        }
    except (KeyError, IndexError):
        return {"accepted": 0, "rejected": 0, "stale": 0, "rate": 0.0}


def get_gpu_info() -> Optional[dict]:
    """Get GPU telemetry from SRBMiner API."""
    stats = get_api_stats()
    if not stats:
        return None

    try:
        gpu = stats["gpu_devices"][0]
        return {
            "temperature": gpu.get("temperature", 0),
            "power": gpu.get("power", 0),
            "fan_speed": gpu.get("fan_speed", 0),
            "vram_used": gpu.get("vram_used", 0),
        }
    except (KeyError, IndexError):
        return None
