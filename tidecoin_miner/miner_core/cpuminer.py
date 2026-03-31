"""cpuminer-opt fallback miner (CPU-only)."""

import time
from pathlib import Path
from typing import Optional

from tidecoin_miner.config import (
    LOG_DIR, BIN_DIR, load_config, get_pool_url, get_cpu_threads
)
from tidecoin_miner.miner_core.process import (
    start_process, stop_process, is_running
)

MINER_NAME = "cpuminer"


def get_binary_path() -> Path:
    return BIN_DIR / "cpuminer-opt" / "cpuminer"


def build_command(cfg: dict, pool_name: Optional[str] = None) -> list[str]:
    """Build cpuminer-opt command line."""
    binary = get_binary_path()
    if not binary.exists():
        raise FileNotFoundError(f"cpuminer-opt not found at {binary}")

    pool = pool_name or cfg["pool"]["primary"]
    pool_url = get_pool_url(pool)
    wallet = cfg["wallet"]["address"]
    threads = get_cpu_threads(cfg)

    return [
        str(binary),
        "-a", "yespowertide",
        "-o", pool_url,
        "-u", wallet,
        "-p", "c=TDC",
        "-t", str(threads),
        "--no-color",
    ]


def start(cfg: Optional[dict] = None, pool_name: Optional[str] = None) -> int:
    """Start cpuminer-opt."""
    if cfg is None:
        cfg = load_config()

    cmd = build_command(cfg, pool_name)
    proc = start_process(MINER_NAME, cmd, nice=-10)
    print(f"[OK] cpuminer-opt started (PID: {proc.pid})")

    time.sleep(2)
    if not is_running(MINER_NAME):
        raise RuntimeError("cpuminer-opt failed to start")

    return proc.pid


def stop():
    """Stop cpuminer-opt."""
    if is_running(MINER_NAME):
        stop_process(MINER_NAME)
        print("[OK] cpuminer-opt stopped")
