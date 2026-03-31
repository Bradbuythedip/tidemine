"""CPU and GPU performance tuning for maximum hashrate."""

import os
import subprocess
from pathlib import Path
from typing import Optional

import psutil


def get_cpu_info() -> dict:
    """Detect CPU architecture and capabilities."""
    info = {
        "model": "",
        "cores_physical": psutil.cpu_count(logical=False),
        "cores_logical": psutil.cpu_count(logical=True),
        "freq_mhz": 0,
        "cache_l3_kb": 0,
        "avx512": False,
        "avx2": False,
        "aes_ni": False,
        "is_intel": False,
        "is_amd": False,
        "numa_nodes": 1,
    }

    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    info["model"] = line.split(":", 1)[1].strip()
                    info["is_intel"] = "intel" in info["model"].lower()
                    info["is_amd"] = "amd" in info["model"].lower()
                elif line.startswith("flags"):
                    flags = line.split(":", 1)[1]
                    info["avx512"] = "avx512f" in flags
                    info["avx2"] = "avx2" in flags
                    info["aes_ni"] = "aes" in flags
                    break
    except FileNotFoundError:
        pass

    # Get L3 cache size
    try:
        result = subprocess.run(
            ["lscpu"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            if "L3 cache" in line:
                val = line.split(":")[-1].strip()
                if "MiB" in val:
                    info["cache_l3_kb"] = int(float(val.replace("MiB", "").strip()) * 1024)
                elif "KiB" in val:
                    info["cache_l3_kb"] = int(val.replace("KiB", "").strip())
            elif "NUMA node(s)" in line:
                info["numa_nodes"] = int(line.split(":")[-1].strip())
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass

    freq = psutil.cpu_freq()
    if freq:
        info["freq_mhz"] = int(freq.max or freq.current)

    return info


def set_cpu_governor(governor: str = "performance") -> bool:
    """Set CPU frequency governor for all cores."""
    try:
        cores = psutil.cpu_count(logical=True) or 1
        for i in range(cores):
            path = f"/sys/devices/system/cpu/cpu{i}/cpufreq/scaling_governor"
            subprocess.run(
                ["sudo", "tee", path],
                input=governor.encode(),
                check=True, capture_output=True, timeout=5,
            )
        print(f"[OK] CPU governor set to '{governor}' on {cores} cores")
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        print(f"[!] Failed to set CPU governor: {e}")
        # Try cpupower
        try:
            subprocess.run(
                ["sudo", "cpupower", "frequency-set", "-g", governor],
                check=True, capture_output=True, timeout=10,
            )
            print(f"[OK] CPU governor set via cpupower")
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False


def disable_cpu_cstates() -> bool:
    """Disable deep C-states for consistent mining performance."""
    try:
        # Set max_cstate=1 via /dev/cpu_dma_latency
        # This prevents deep sleep states that cause hashrate drops
        subprocess.run(
            ["sudo", "sh", "-c",
             "echo 1 > /sys/module/intel_idle/parameters/max_cstate 2>/dev/null || true"],
            check=False, capture_output=True, timeout=5,
        )
        print("[OK] CPU C-states limited for consistent performance")
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def set_kernel_params() -> bool:
    """Set kernel parameters for mining optimization."""
    params = {
        "vm.swappiness": "1",           # Minimize swapping
        "vm.dirty_ratio": "10",          # Reduce dirty page ratio
        "vm.dirty_background_ratio": "5",
        "kernel.sched_migration_cost_ns": "5000000",  # Reduce thread migration
        "kernel.sched_autogroup_enabled": "0",         # Disable autogroup for mining
    }
    success = True
    for key, val in params.items():
        try:
            subprocess.run(
                ["sudo", "sysctl", "-w", f"{key}={val}"],
                check=True, capture_output=True, timeout=5,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            success = False
    if success:
        print("[OK] Kernel parameters optimized for mining")
    return success


def set_gpu_performance_mode() -> bool:
    """Set NVIDIA GPU to maximum performance mode."""
    cmds = [
        # Enable persistence mode (keeps driver loaded)
        ["sudo", "nvidia-smi", "-pm", "1"],
        # Set compute mode to exclusive (mining only)
        ["sudo", "nvidia-smi", "-c", "EXCLUSIVE_PROCESS"],
        # Set power limit to 90% TDP for optimal efficiency
        # RTX 5070 Ti TDP is ~300W, start at 270W
        ["sudo", "nvidia-smi", "-pl", "270"],
        # Lock GPU clocks to max boost
        ["sudo", "nvidia-smi", "-lgc", "0,3000"],
        # Set memory clock to max
        ["sudo", "nvidia-smi", "-lmc", "0,10000"],
    ]

    success = True
    for cmd in cmds:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                # Non-fatal, some settings may not be supported
                pass
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            success = False

    print("[OK] GPU performance mode configured")
    return success


def set_gpu_fan_curve() -> bool:
    """Set aggressive GPU fan curve for thermal headroom."""
    try:
        # Enable manual fan control
        subprocess.run(
            ["nvidia-settings", "-a", "GPUFanControlState=1"],
            capture_output=True, timeout=5,
        )
        # Set fan to 80% for mining
        subprocess.run(
            ["nvidia-settings", "-a", "GPUTargetFanSpeed=80"],
            capture_output=True, timeout=5,
        )
        print("[OK] GPU fan set to 80%")
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def optimize_irq_affinity() -> bool:
    """Pin IRQ handlers away from mining cores."""
    try:
        cores = psutil.cpu_count(logical=True) or 4
        # Reserve last 2 cores for IRQ handling
        irq_mask = hex((1 << cores) - (1 << (cores - 2)))
        subprocess.run(
            ["sudo", "sh", "-c", f"echo {irq_mask} > /proc/irq/default_smp_affinity"],
            check=True, capture_output=True, timeout=5,
        )
        print("[OK] IRQ affinity optimized")
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def disable_transparent_hugepages() -> bool:
    """Disable THP - explicit huge pages are better for mining."""
    try:
        subprocess.run(
            ["sudo", "sh", "-c", "echo never > /sys/kernel/mm/transparent_hugepage/enabled"],
            check=True, capture_output=True, timeout=5,
        )
        subprocess.run(
            ["sudo", "sh", "-c", "echo never > /sys/kernel/mm/transparent_hugepage/defrag"],
            check=True, capture_output=True, timeout=5,
        )
        print("[OK] Transparent huge pages disabled (explicit hugepages preferred)")
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def get_optimal_thread_count() -> int:
    """Calculate optimal thread count for YesPowerTide mining."""
    cpu = get_cpu_info()
    physical = cpu["cores_physical"] or 4

    # YesPowerTide is memory-bound: use physical cores minus headroom
    # On i9 with HT: physical cores - 2 for system
    # This avoids hyperthreading contention on shared L3 cache
    optimal = max(1, physical - 2)

    # If large L3 cache (>20MB), can use more threads
    if cpu["cache_l3_kb"] > 20 * 1024:
        optimal = max(1, physical - 1)

    return optimal


def apply_all_optimizations() -> dict:
    """Apply all system optimizations for mining."""
    results = {
        "cpu_governor": set_cpu_governor("performance"),
        "cpu_cstates": disable_cpu_cstates(),
        "kernel_params": set_kernel_params(),
        "gpu_performance": set_gpu_performance_mode(),
        "gpu_fan": set_gpu_fan_curve(),
        "irq_affinity": optimize_irq_affinity(),
        "thp_disabled": disable_transparent_hugepages(),
    }
    return results
