"""Stats collection from SRBMiner API and system sensors."""

import time
import collections
from typing import Optional

import psutil

from tidecoin_miner.miner_core.srbminer import get_api_stats, get_hashrate, get_shares, get_gpu_info


class StatsCollector:
    """Collect and aggregate mining and system statistics."""

    def __init__(self, history_size: int = 720):
        # 720 samples @ 5s = 1 hour of history
        self.history_size = history_size
        self.hashrate_history: collections.deque = collections.deque(maxlen=history_size)
        self.cpu_temp_history: collections.deque = collections.deque(maxlen=history_size)
        self.gpu_temp_history: collections.deque = collections.deque(maxlen=history_size)
        self.share_history: collections.deque = collections.deque(maxlen=history_size)
        self.start_time = time.time()
        self._last_shares = {"accepted": 0, "rejected": 0, "stale": 0}

    def collect(self) -> dict:
        """Collect a full snapshot of all metrics."""
        hr = get_hashrate()
        shares = get_shares()
        gpu = get_gpu_info()
        cpu_temps = self._get_cpu_temps()
        cpu_usage = psutil.cpu_percent(percpu=True)

        snapshot = {
            "timestamp": time.time(),
            "uptime": time.time() - self.start_time,
            "hashrate": hr,
            "shares": shares,
            "gpu": gpu,
            "cpu": {
                "temps": cpu_temps,
                "usage": cpu_usage,
                "avg_usage": sum(cpu_usage) / len(cpu_usage) if cpu_usage else 0,
                "freq": self._get_cpu_freq(),
            },
            "memory": {
                "total_gb": round(psutil.virtual_memory().total / (1024**3), 1),
                "used_gb": round(psutil.virtual_memory().used / (1024**3), 1),
                "percent": psutil.virtual_memory().percent,
            },
            "power_efficiency": self._calc_efficiency(hr, gpu),
        }

        # Update histories
        self.hashrate_history.append(hr["total"])
        if gpu:
            self.gpu_temp_history.append(gpu.get("temperature", 0))
        if cpu_temps:
            self.cpu_temp_history.append(max(cpu_temps) if cpu_temps else 0)

        return snapshot

    def get_averages(self) -> dict:
        """Calculate moving averages."""
        hr_list = list(self.hashrate_history)
        now = len(hr_list)

        def avg(data, n):
            recent = data[-n:] if len(data) >= n else data
            return sum(recent) / len(recent) if recent else 0

        return {
            "hashrate_1m": round(avg(hr_list, 12), 2),    # 12 x 5s = 1min
            "hashrate_5m": round(avg(hr_list, 60), 2),    # 60 x 5s = 5min
            "hashrate_15m": round(avg(hr_list, 180), 2),  # 180 x 5s = 15min
            "hashrate_1h": round(avg(hr_list, 720), 2),
        }

    def get_sparkline_data(self, points: int = 60) -> list[float]:
        """Get last N hashrate points for sparkline."""
        data = list(self.hashrate_history)
        if len(data) <= points:
            return data
        # Downsample
        step = len(data) / points
        return [data[int(i * step)] for i in range(points)]

    def _get_cpu_temps(self) -> list[float]:
        """Get CPU core temperatures."""
        temps = []
        try:
            sensor_data = psutil.sensors_temperatures()
            for name, entries in sensor_data.items():
                if "coretemp" in name.lower() or "k10temp" in name.lower():
                    temps.extend(e.current for e in entries if e.current > 0)
        except (AttributeError, RuntimeError):
            pass
        return temps

    def _get_cpu_freq(self) -> dict:
        """Get CPU frequency info."""
        freq = psutil.cpu_freq()
        if freq:
            return {
                "current": round(freq.current, 0),
                "max": round(freq.max, 0) if freq.max else 0,
            }
        return {"current": 0, "max": 0}

    def _calc_efficiency(self, hashrate: dict, gpu: Optional[dict]) -> float:
        """Calculate hashes per watt."""
        total_hr = hashrate.get("total", 0)
        if total_hr == 0:
            return 0

        # Estimate power draw
        gpu_power = gpu.get("power", 0) if gpu else 0
        # Estimate CPU power (rough: ~10W per active core for i9)
        cpu_cores = psutil.cpu_count(logical=False) or 8
        cpu_power = cpu_cores * 10  # Rough estimate

        total_power = gpu_power + cpu_power
        if total_power == 0:
            return 0

        return round(total_hr / total_power, 3)
