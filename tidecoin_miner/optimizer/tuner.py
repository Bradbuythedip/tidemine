"""CPU performance tuning for maximum YesPowerTide hashrate.

YesPowerTide is CPU-only (GPU support removed in SRBMiner v3.2.4).
The algorithm is memory-bandwidth bound - performance scales with:
1. L2/L3 cache size and speed
2. Memory bandwidth (dual/quad channel)
3. Core count (physical cores, NOT hyperthreads)
4. Memory latency (NUMA-local access critical)

AVX-512/AVX2 provide NO benefit for the yespower kernel itself.
The algorithm deliberately uses SSE2-level operations.
"""

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
        "cache_l2_kb": 0,
        "cache_l3_kb": 0,
        "avx512": False,
        "avx2": False,
        "aes_ni": False,
        "is_intel": False,
        "is_amd": False,
        "numa_nodes": 1,
        "is_hybrid": False,
        "p_cores": 0,
        "e_cores": 0,
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

    # Get cache sizes and NUMA info from lscpu
    try:
        result = subprocess.run(
            ["lscpu"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            if "L2 cache" in line:
                val = line.split(":")[-1].strip()
                if "MiB" in val:
                    info["cache_l2_kb"] = int(float(val.replace("MiB", "").strip()) * 1024)
                elif "KiB" in val:
                    info["cache_l2_kb"] = int(val.replace("KiB", "").strip())
            elif "L3 cache" in line:
                val = line.split(":")[-1].strip()
                if "MiB" in val:
                    info["cache_l3_kb"] = int(float(val.replace("MiB", "").strip()) * 1024)
                elif "KiB" in val:
                    info["cache_l3_kb"] = int(val.replace("KiB", "").strip())
            elif "NUMA node(s)" in line:
                info["numa_nodes"] = int(line.split(":")[-1].strip())
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass

    # Detect hybrid architecture (Intel 12th+ gen with P-cores and E-cores)
    try:
        cpus = sorted(Path("/sys/devices/system/cpu/").glob("cpu[0-9]*"))
        p_count = 0
        e_count = 0
        for cpu_dir in cpus:
            core_type_path = cpu_dir / "topology" / "core_type"
            if core_type_path.exists():
                core_type = core_type_path.read_text().strip()
                if core_type == "0":
                    p_count += 1
                else:
                    e_count += 1
        if p_count > 0 and e_count > 0:
            info["is_hybrid"] = True
            info["p_cores"] = p_count
            info["e_cores"] = e_count
    except (ValueError, OSError):
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
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # Try cpupower fallback
        try:
            subprocess.run(
                ["sudo", "cpupower", "frequency-set", "-g", governor],
                check=True, capture_output=True, timeout=10,
            )
            print(f"[OK] CPU governor set via cpupower")
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            print(f"[!] Failed to set CPU governor to {governor}")
            return False


def disable_cpu_cstates() -> bool:
    """Disable deep C-states for consistent mining performance.

    Deep sleep states cause hashrate drops when cores wake up.
    Limiting to C1 keeps cores responsive.
    """
    success = True
    try:
        # Limit max C-state for Intel idle driver
        subprocess.run(
            ["sudo", "sh", "-c",
             "echo 1 > /sys/module/intel_idle/parameters/max_cstate 2>/dev/null || true"],
            check=False, capture_output=True, timeout=5,
        )
    except subprocess.TimeoutExpired:
        success = False

    # Disable individual C-states > C1 via sysfs
    try:
        for cpu_dir in Path("/sys/devices/system/cpu/").glob("cpu[0-9]*/cpuidle"):
            for state_dir in sorted(cpu_dir.glob("state[2-9]*")):
                disable_path = state_dir / "disable"
                if disable_path.exists():
                    subprocess.run(
                        ["sudo", "tee", str(disable_path)],
                        input=b"1",
                        capture_output=True, timeout=2,
                    )
    except (subprocess.TimeoutExpired, OSError):
        pass

    if success:
        print("[OK] Deep C-states disabled for consistent performance")
    return success


def disable_numa_balancing() -> bool:
    """Disable automatic NUMA balancing to prevent page migration overhead."""
    try:
        subprocess.run(
            ["sudo", "sysctl", "-w", "kernel.numa_balancing=0"],
            check=True, capture_output=True, timeout=5,
        )
        print("[OK] NUMA auto-balancing disabled")
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def set_kernel_params() -> bool:
    """Set kernel parameters for mining optimization."""
    params = {
        "vm.swappiness": "1",                          # Minimize swapping
        "vm.dirty_ratio": "10",                         # Reduce dirty page ratio
        "vm.dirty_background_ratio": "5",
        "kernel.sched_migration_cost_ns": "5000000",    # Reduce thread migration
        "kernel.sched_autogroup_enabled": "0",           # Disable autogroup for mining
        "kernel.numa_balancing": "0",                    # Disable NUMA auto-balance
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


def optimize_irq_affinity() -> bool:
    """Pin IRQ handlers away from mining cores.

    Mining threads should never be interrupted by IRQ processing.
    Pin all IRQs to the last 2 logical cores.
    """
    try:
        cores = psutil.cpu_count(logical=True) or 4
        # Reserve last 2 cores for IRQ handling
        irq_mask = hex((1 << cores) - (1 << (cores - 2)))
        subprocess.run(
            ["sudo", "sh", "-c", f"echo {irq_mask} > /proc/irq/default_smp_affinity"],
            check=True, capture_output=True, timeout=5,
        )
        print("[OK] IRQ affinity: pinned to last 2 cores")
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def disable_transparent_hugepages() -> bool:
    """Disable THP - explicit huge pages are better for mining.

    THP causes allocation stalls and fragmentation. Explicit 2MB
    hugepages are pre-allocated and faster.
    """
    try:
        subprocess.run(
            ["sudo", "sh", "-c", "echo never > /sys/kernel/mm/transparent_hugepage/enabled"],
            check=True, capture_output=True, timeout=5,
        )
        subprocess.run(
            ["sudo", "sh", "-c", "echo never > /sys/kernel/mm/transparent_hugepage/defrag"],
            check=True, capture_output=True, timeout=5,
        )
        print("[OK] Transparent huge pages disabled")
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def set_memory_lock_limits() -> bool:
    """Set unlimited memory locking for huge page usage."""
    try:
        # Add limits for current user
        user = os.environ.get("USER", "")
        if user:
            limits_conf = f"# Tidemine mining\n{user} soft memlock unlimited\n{user} hard memlock unlimited\n"
            subprocess.run(
                ["sudo", "tee", "/etc/security/limits.d/99-tidemine.conf"],
                input=limits_conf.encode(),
                check=True, capture_output=True, timeout=5,
            )
            print("[OK] Memory lock limits set to unlimited")
            return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass
    return False


def get_optimal_thread_count() -> int:
    """Calculate optimal thread count for YesPowerTide mining.

    YesPowerTide is memory-bound with heavy L2 cache dependency.
    Each thread needs dedicated L2 cache access.

    Rules:
    - Use physical cores only (no hyperthreading benefit)
    - On hybrid Intel: use P-cores only (E-cores have smaller caches)
    - Reserve 2 cores for system/IRQ handling
    """
    cpu = get_cpu_info()

    # Hybrid Intel: P-cores only
    if cpu["is_hybrid"] and cpu["p_cores"] > 0:
        optimal = max(1, cpu["p_cores"] - 2)
        return optimal

    physical = cpu["cores_physical"] or 4
    optimal = max(1, physical - 2)

    return optimal


def apply_all_optimizations() -> dict:
    """Apply all system optimizations for CPU mining.

    GPU optimizations removed - YesPowerTide is CPU-only.
    Focus is on CPU cache, memory bandwidth, and scheduling.
    """
    print("[*] Applying system optimizations for YesPowerTide CPU mining...")

    results = {
        "cpu_governor": set_cpu_governor("performance"),
        "cpu_cstates": disable_cpu_cstates(),
        "kernel_params": set_kernel_params(),
        "irq_affinity": optimize_irq_affinity(),
        "thp_disabled": disable_transparent_hugepages(),
        "memlock_limits": set_memory_lock_limits(),
    }

    applied = sum(1 for v in results.values() if v)
    print(f"[OK] {applied}/{len(results)} optimizations applied")
    return results
