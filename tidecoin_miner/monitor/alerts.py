"""Alert system for mining anomalies and thermal protection."""

import time
from typing import Optional, Callable


class AlertManager:
    """Monitor metrics and trigger alerts/actions."""

    def __init__(self, cfg: dict):
        self.cfg = cfg["alerts"]
        self.baseline_hashrate: Optional[float] = None
        self._last_alert_time: dict[str, float] = {}
        self._cooldown = 60  # Min seconds between same alert type
        self.alert_log: list[dict] = []

    def check(self, snapshot: dict) -> list[dict]:
        """Check a stats snapshot for alert conditions."""
        alerts = []

        hr = snapshot.get("hashrate", {}).get("total", 0)
        cpu_temps = snapshot.get("cpu", {}).get("temps", [])

        # Set baseline after warmup
        if self.baseline_hashrate is None and hr > 0:
            self.baseline_hashrate = hr

        # Hashrate drop
        if self.baseline_hashrate and hr > 0:
            drop = 1 - (hr / self.baseline_hashrate)
            threshold = self.cfg.get("hashrate_drop_threshold", 0.5)
            if drop > threshold:
                alerts.append(self._make_alert(
                    "hashrate_drop",
                    f"Hashrate dropped {drop:.0%} (from {self.baseline_hashrate:.1f} to {hr:.1f} H/s)",
                    severity="warning",
                ))

        # CPU temperature
        if cpu_temps:
            max_cpu_temp = max(cpu_temps)
            cpu_max = self.cfg.get("cpu_temp_max", 95)
            if max_cpu_temp > cpu_max:
                alerts.append(self._make_alert(
                    "cpu_overheat",
                    f"CPU temperature {max_cpu_temp:.0f}C exceeds limit {cpu_max}C",
                    severity="critical",
                    action="reduce_threads",
                ))

        # Zero hashrate (miner may have crashed)
        if hr == 0 and self.baseline_hashrate is not None and self.baseline_hashrate > 0:
            alerts.append(self._make_alert(
                "zero_hashrate",
                "Hashrate is 0 - miner may have crashed",
                severity="critical",
                action="restart_miner",
            ))

        # Stale rate
        shares = snapshot.get("shares", {})
        stale_rate = shares.get("stale_rate", 0)
        if stale_rate > 0.05:  # 5%
            alerts.append(self._make_alert(
                "high_stale_rate",
                f"Stale share rate is {stale_rate:.1%}",
                severity="warning",
                action="check_pool",
            ))

        self.alert_log.extend(alerts)
        # Keep last 100 alerts
        if len(self.alert_log) > 100:
            self.alert_log = self.alert_log[-100:]

        return alerts

    def _make_alert(self, alert_type: str, message: str,
                    severity: str = "info", action: Optional[str] = None) -> Optional[dict]:
        """Create an alert with cooldown."""
        now = time.time()
        last = self._last_alert_time.get(alert_type, 0)
        if now - last < self._cooldown:
            return None

        self._last_alert_time[alert_type] = now
        alert = {
            "type": alert_type,
            "message": message,
            "severity": severity,
            "action": action,
            "timestamp": now,
        }
        return alert

    def update_baseline(self, hashrate: float):
        """Update baseline hashrate (e.g., after benchmark)."""
        if hashrate > 0:
            self.baseline_hashrate = hashrate
