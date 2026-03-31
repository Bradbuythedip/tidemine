"""SRBMiner-MULTI process manager for dual CPU+GPU YesPowerTide mining.

GPU mining for yespowertide IS supported in SRBMiner 3.2.5, confirmed on
NVIDIA Blackwell (RTX 5070 Ti Laptop). GPU provides ~3700 H/s alongside
CPU ~1500 H/s per thread. Do NOT use taskset or process-level CPU affinity
as it constrains GPU thread scheduling and causes crashes.
"""

import json
import os
import shutil
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


def _detect_p_cores() -> Optional[list[int]]:
    """Detect Intel P-core IDs on hybrid architectures (12th+ gen).

    On hybrid i9 (P-cores + E-cores), only P-cores should mine
    because E-cores have smaller caches and lower IPC.
    """
    try:
        # Intel Thread Director exposes core types via sysfs
        p_cores = []
        e_cores = []
        cpus = sorted(Path("/sys/devices/system/cpu/").glob("cpu[0-9]*"))
        for cpu_dir in cpus:
            core_type_path = cpu_dir / "topology" / "core_type"
            cpu_id = int(cpu_dir.name.replace("cpu", ""))
            if core_type_path.exists():
                core_type = core_type_path.read_text().strip()
                if core_type == "0":  # P-core (Intel Core type)
                    p_cores.append(cpu_id)
                else:
                    e_cores.append(cpu_id)

        if p_cores and e_cores:
            return p_cores
    except (ValueError, OSError):
        pass
    return None


def _get_numa_node_cpus(node: int = 0) -> Optional[list[int]]:
    """Get CPUs on a specific NUMA node for cache-local mining."""
    try:
        path = Path(f"/sys/devices/system/node/node{node}/cpulist")
        if path.exists():
            cpulist = path.read_text().strip()
            cpus = []
            for part in cpulist.split(","):
                if "-" in part:
                    start, end = part.split("-")
                    cpus.extend(range(int(start), int(end) + 1))
                else:
                    cpus.append(int(part))
            return cpus
    except (ValueError, OSError):
        pass
    return None


def get_mining_cpus(cfg: dict) -> tuple[list[int], int]:
    """Determine which CPUs to use for mining and thread count.

    Returns (cpu_list, thread_count). cpu_list may be empty if
    no specific affinity is needed.

    Strategy:
    1. On hybrid Intel (P+E cores): use only P-cores
    2. On NUMA systems: bind to node 0 for shared L3 cache
    3. Reserve 2 threads for system overhead
    """
    import psutil
    requested = cfg["mining"]["cpu_threads"]

    # Detect P-cores on hybrid Intel
    p_cores = _detect_p_cores()
    if p_cores:
        # Use P-cores only, minus 2 for system
        mining_cpus = p_cores[:-2] if len(p_cores) > 2 else p_cores
        thread_count = len(mining_cpus)
        return mining_cpus, thread_count

    # NUMA optimization: bind to node 0
    numa_cpus = _get_numa_node_cpus(0)

    physical = psutil.cpu_count(logical=False) or 4

    if requested != "auto":
        thread_count = int(requested)
    else:
        thread_count = max(1, physical - 2)

    # On NUMA, return node 0 CPUs limited to thread count
    if numa_cpus and len(numa_cpus) >= thread_count:
        return numa_cpus[:thread_count], thread_count

    return [], thread_count


def build_command(cfg: dict, pool_name: Optional[str] = None) -> list[str]:
    """Build SRBMiner command line for dual CPU+GPU mining.

    GPU mining for yespowertide IS supported in SRBMiner 3.2.5,
    confirmed on NVIDIA Blackwell (RTX 5070 Ti). Do NOT use taskset
    or process-level affinity - it breaks GPU thread scheduling.
    """
    binary = get_srbminer_path()
    if not binary.exists():
        raise FileNotFoundError(f"SRBMiner not found at {binary}. Run 'tidecoin-miner install' first.")

    pool = pool_name or cfg["pool"]["primary"]
    pool_url = get_pool_url(pool)
    wallet = cfg["wallet"]["address"]
    if not wallet:
        raise ValueError("Wallet address not configured. Set it in ~/.tidecoin-miner/config.yaml")

    _mining_cpus, threads = get_mining_cpus(cfg)

    cmd = [
        str(binary),
        "--algorithm", "yespowertide",
        "--pool", pool_url,
        "--wallet", wallet,
        "--password", "c=TDC",
        "--cpu-threads", str(threads),
        # Enable GPU mining (confirmed working on SRBMiner 3.2.5 + Blackwell)
        "--gpu-id", str(cfg["mining"].get("gpu_id", 0)),
        # API for monitoring
        "--api-enable",
        "--api-port", str(API_PORT),
        # Logging
        "--log-file", str(LOG_DIR / "srbminer.log"),
        # CPU priority (3 = above normal)
        "--cpu-priority", "3",
        # Keepalive for pool connection stability (flag only, no value)
        "--keepalive",
        # Fast reconnect on disconnect
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
    """Start SRBMiner with dual CPU+GPU mining."""
    if cfg is None:
        cfg = load_config()

    cmd = build_command(cfg, pool_name)

    _mining_cpus, threads = get_mining_cpus(cfg)

    # NOTE: Do NOT use taskset or process-level CPU affinity!
    # SRBMiner manages both CPU and GPU threads internally.
    # taskset constrains GPU threads too, causing crashes.

    print(f"[*] Starting SRBMiner ({threads} CPU threads + GPU)...")

    env = {}

    proc = start_process(MINER_NAME, cmd, nice=-10, env=env)
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
    """Get current hashrate breakdown (CPU + GPU)."""
    stats = get_api_stats()
    if not stats:
        return {"cpu": 0.0, "gpu": 0.0, "total": 0.0}

    try:
        algo = stats["algorithms"][0]
        cpu_hr = algo.get("hashrate", {}).get("cpu", {}).get("total", 0.0)
        gpu_hr = algo.get("hashrate", {}).get("gpu", {}).get("total", 0.0)
        # Fallback: check top-level hashrate
        if cpu_hr == 0 and gpu_hr == 0:
            total = algo.get("hashrate", {}).get("total", 0.0)
            return {"cpu": 0.0, "gpu": 0.0, "total": total}
        return {
            "cpu": cpu_hr,
            "gpu": gpu_hr,
            "total": cpu_hr + gpu_hr,
        }
    except (KeyError, IndexError):
        return {"cpu": 0.0, "gpu": 0.0, "total": 0.0}


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
            "clock_core": gpu.get("clock_core", 0),
            "clock_memory": gpu.get("clock_memory", 0),
        }
    except (KeyError, IndexError):
        return None


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
