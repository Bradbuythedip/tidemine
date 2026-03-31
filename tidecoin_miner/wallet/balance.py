"""Earnings tracking with persistent storage."""

import json
import time
from pathlib import Path
from typing import Optional

import httpx

from tidecoin_miner.config import DATA_DIR

EARNINGS_FILE = DATA_DIR / "earnings.json"
COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price?ids=tidecoin&vs_currencies=usd"


def load_earnings() -> dict:
    """Load earnings history."""
    if EARNINGS_FILE.exists():
        with open(EARNINGS_FILE) as f:
            return json.load(f)
    return {
        "sessions": [],
        "total_tdc": 0,
        "last_balance": 0,
    }


def save_earnings(data: dict):
    """Save earnings data."""
    EARNINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(EARNINGS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def record_session(tdc_mined: float, duration_secs: float, avg_hashrate: float):
    """Record a mining session."""
    data = load_earnings()
    session = {
        "timestamp": time.time(),
        "tdc_mined": tdc_mined,
        "duration_secs": duration_secs,
        "avg_hashrate": avg_hashrate,
    }
    data["sessions"].append(session)
    data["total_tdc"] += tdc_mined
    # Keep last 1000 sessions
    if len(data["sessions"]) > 1000:
        data["sessions"] = data["sessions"][-1000:]
    save_earnings(data)


def get_tdc_price() -> Optional[float]:
    """Fetch current TDC price from CoinGecko."""
    try:
        resp = httpx.get(COINGECKO_URL, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("tidecoin", {}).get("usd")
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    return None


def estimate_earnings(hashrate: float, price_usd: Optional[float] = None) -> dict:
    """Estimate daily/weekly/monthly earnings."""
    if price_usd is None:
        price_usd = get_tdc_price() or 0.05  # Fallback estimate

    # Rough estimation based on network difficulty
    # This is a simplified model; actual earnings depend on network hashrate
    # Assume ~100 TDC/day per 1000 H/s as rough baseline (adjust based on actual data)
    tdc_per_day = (hashrate / 1000) * 100  # Very rough estimate

    return {
        "hashrate": hashrate,
        "tdc_per_day": round(tdc_per_day, 4),
        "tdc_per_week": round(tdc_per_day * 7, 4),
        "tdc_per_month": round(tdc_per_day * 30, 4),
        "usd_per_day": round(tdc_per_day * price_usd, 4),
        "usd_per_week": round(tdc_per_day * 7 * price_usd, 4),
        "usd_per_month": round(tdc_per_day * 30 * price_usd, 4),
        "tdc_price_usd": price_usd,
    }
