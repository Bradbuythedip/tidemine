"""Pool failover logic with stale rate monitoring."""

import time
import threading
from typing import Optional, Callable

from tidecoin_miner.config import load_config, POOL_REGISTRY
from tidecoin_miner.miner_core import srbminer
from tidecoin_miner.pool_manager.pools import test_pool_latency, get_best_pool


class PoolFailover:
    """Monitor pool health and trigger failover when needed."""

    def __init__(self, on_switch: Optional[Callable] = None):
        self.current_pool: Optional[str] = None
        self.failed_pools: list[str] = []
        self.switch_count = 0
        self.last_check = 0.0
        self.on_switch = on_switch
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stale_history: list[float] = []

    def start(self, pool_name: str, interval: int = 30):
        """Start failover monitoring in background."""
        self.current_pool = pool_name
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, args=(interval,), daemon=True)
        self._thread.start()

    def stop(self):
        """Stop failover monitoring."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _monitor_loop(self, interval: int):
        """Main monitoring loop."""
        while self._running:
            try:
                self._check_health()
            except Exception:
                pass
            time.sleep(interval)

    def _check_health(self):
        """Check current pool health and failover if needed."""
        cfg = load_config()
        max_stale = cfg["pool"]["max_stale_rate"]

        # Check share stats
        shares = srbminer.get_shares()
        stale_rate = shares.get("stale_rate", 0.0)
        self._stale_history.append(stale_rate)
        if len(self._stale_history) > 10:
            self._stale_history = self._stale_history[-10:]

        # Average stale rate over recent checks
        avg_stale = sum(self._stale_history) / len(self._stale_history)

        # Check if pool is reachable
        pool_info = POOL_REGISTRY.get(self.current_pool)
        if pool_info:
            latency = test_pool_latency(pool_info["host"], pool_info["port"], timeout=10)
            pool_down = latency is None
        else:
            pool_down = True

        should_switch = pool_down or avg_stale > max_stale

        if should_switch:
            self._do_failover(reason="pool_down" if pool_down else f"stale_rate={avg_stale:.2%}")

    def _do_failover(self, reason: str):
        """Switch to next best pool."""
        self.failed_pools.append(self.current_pool)
        # Only keep last 3 failures to allow retry
        if len(self.failed_pools) > 3:
            self.failed_pools = self.failed_pools[-3:]

        new_pool = get_best_pool(exclude=self.failed_pools)
        if new_pool is None:
            # All pools failed, reset exclusions and try again
            self.failed_pools = []
            new_pool = get_best_pool()

        if new_pool and new_pool != self.current_pool:
            old_pool = self.current_pool
            self.current_pool = new_pool
            self.switch_count += 1
            self._stale_history = []

            print(f"[!] Pool failover: {old_pool} -> {new_pool} (reason: {reason})")

            # Restart miner with new pool
            try:
                srbminer.restart(pool_name=new_pool)
            except Exception as e:
                print(f"[!] Failover restart failed: {e}")

            if self.on_switch:
                self.on_switch(old_pool, new_pool, reason)
