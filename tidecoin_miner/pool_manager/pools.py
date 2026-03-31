"""Pool registry, health checking, and latency testing."""

import socket
import time
from typing import Optional

from tidecoin_miner.config import POOL_REGISTRY


def test_pool_latency(host: str, port: int, timeout: float = 5.0) -> Optional[float]:
    """Test TCP connection latency to a pool (ms)."""
    try:
        start = time.monotonic()
        sock = socket.create_connection((host, port), timeout=timeout)
        latency = (time.monotonic() - start) * 1000
        sock.close()
        return round(latency, 1)
    except (socket.timeout, socket.error, OSError):
        return None


def test_all_pools() -> list[dict]:
    """Test latency for all pools, sorted by latency."""
    results = []
    for name, info in POOL_REGISTRY.items():
        latency = test_pool_latency(info["host"], info["port"])
        results.append({
            "name": name,
            "host": info["host"],
            "port": info["port"],
            "fee": info["fee"],
            "latency_ms": latency,
            "reachable": latency is not None,
        })
    results.sort(key=lambda x: x["latency_ms"] if x["latency_ms"] is not None else 99999)
    return results


def get_best_pool(exclude: Optional[list[str]] = None) -> Optional[str]:
    """Find the pool with lowest latency."""
    exclude = exclude or []
    results = test_all_pools()
    for r in results:
        if r["reachable"] and r["name"] not in exclude:
            return r["name"]
    return None


def resolve_pool_host(pool_name: str) -> str:
    """Resolve pool hostname to IP for faster connection."""
    pool = POOL_REGISTRY.get(pool_name)
    if not pool:
        raise ValueError(f"Unknown pool: {pool_name}")
    try:
        ip = socket.gethostbyname(pool["host"])
        return ip
    except socket.gaierror:
        return pool["host"]
