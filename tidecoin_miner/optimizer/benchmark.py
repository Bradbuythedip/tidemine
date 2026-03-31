"""Auto-tuning benchmark to find optimal mining parameters."""

import json
import time
from pathlib import Path
from typing import Optional

from tidecoin_miner.config import load_config, save_config, DATA_DIR
from tidecoin_miner.miner_core import srbminer
from tidecoin_miner.optimizer.tuner import get_cpu_info, get_optimal_thread_count

OPTIMAL_CONFIG_PATH = DATA_DIR / "optimal_config.json"


def run_benchmark(duration: int = 60, test_threads: bool = True) -> dict:
    """Run mining benchmark to find optimal settings.

    Tests different thread counts and measures hashrate for each.
    """
    cfg = load_config()
    cpu_info = get_cpu_info()
    physical_cores = cpu_info["cores_physical"] or 4
    results = {"tests": [], "best": None, "cpu_info": cpu_info}

    if test_threads:
        # Test thread counts: n-1, n-2, n/2, optimal
        thread_options = sorted(set([
            max(1, physical_cores - 1),
            max(1, physical_cores - 2),
            max(1, physical_cores // 2),
            get_optimal_thread_count(),
        ]))
    else:
        thread_options = [get_optimal_thread_count()]

    print(f"[*] Benchmarking with {len(thread_options)} thread configs, {duration}s each...")
    print(f"[*] CPU: {cpu_info['model']} ({physical_cores} cores)")

    best_hashrate = 0
    best_threads = thread_options[0]

    for threads in thread_options:
        print(f"\n[*] Testing {threads} threads...")
        cfg["mining"]["cpu_threads"] = threads

        try:
            srbminer.start(cfg)
        except Exception as e:
            print(f"[!] Failed to start miner: {e}")
            continue

        # Warmup
        print(f"    Warming up (15s)...")
        time.sleep(15)

        # Collect samples
        samples = []
        sample_interval = 5
        num_samples = max(1, (duration - 15) // sample_interval)

        for i in range(num_samples):
            time.sleep(sample_interval)
            hr = srbminer.get_hashrate()
            if hr["total"] > 0:
                samples.append(hr)
                print(f"    Sample {i+1}/{num_samples}: CPU={hr['cpu']:.1f} GPU={hr['gpu']:.1f} Total={hr['total']:.1f} H/s")

        srbminer.stop()
        time.sleep(2)

        if samples:
            avg_cpu = sum(s["cpu"] for s in samples) / len(samples)
            avg_gpu = sum(s["gpu"] for s in samples) / len(samples)
            avg_total = sum(s["total"] for s in samples) / len(samples)
            per_thread = avg_cpu / threads if threads > 0 else 0

            result = {
                "threads": threads,
                "avg_cpu_hashrate": round(avg_cpu, 2),
                "avg_gpu_hashrate": round(avg_gpu, 2),
                "avg_total_hashrate": round(avg_total, 2),
                "per_thread_hashrate": round(per_thread, 2),
                "samples": len(samples),
            }
            results["tests"].append(result)

            if avg_total > best_hashrate:
                best_hashrate = avg_total
                best_threads = threads

    results["best"] = {
        "threads": best_threads,
        "hashrate": round(best_hashrate, 2),
    }

    # Save optimal config
    save_optimal(best_threads, best_hashrate)

    print(f"\n[OK] Benchmark complete!")
    print(f"     Best: {best_threads} threads @ {best_hashrate:.1f} H/s total")
    return results


def save_optimal(threads: int, hashrate: float):
    """Save optimal configuration."""
    data = {
        "optimal_threads": threads,
        "benchmark_hashrate": hashrate,
        "timestamp": time.time(),
    }
    OPTIMAL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OPTIMAL_CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=2)

    # Also update main config
    cfg = load_config()
    cfg["mining"]["cpu_threads"] = threads
    save_config(cfg)


def load_optimal() -> Optional[dict]:
    """Load previously saved optimal config."""
    if OPTIMAL_CONFIG_PATH.exists():
        with open(OPTIMAL_CONFIG_PATH) as f:
            return json.load(f)
    return None
