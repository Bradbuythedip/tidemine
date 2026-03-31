"""Huge pages setup for YesPowerTide performance optimization."""

import os
import subprocess
from pathlib import Path


def get_hugepage_count() -> int:
    """Get current number of allocated huge pages."""
    try:
        val = Path("/proc/sys/vm/nr_hugepages").read_text().strip()
        return int(val)
    except (FileNotFoundError, ValueError):
        return 0


def get_hugepage_size_kb() -> int:
    """Get huge page size in KB."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("Hugepagesize:"):
                    return int(line.split()[1])
    except FileNotFoundError:
        pass
    return 2048  # Default 2MB


def get_1gb_hugepage_count() -> int:
    """Get number of 1GB huge pages allocated."""
    path = Path("/sys/kernel/mm/hugepages/hugepages-1048576kB/nr_hugepages")
    try:
        return int(path.read_text().strip())
    except (FileNotFoundError, ValueError):
        return 0


def calculate_optimal_hugepages(total_ram_gb: float = 0) -> int:
    """Calculate optimal number of 2MB huge pages for mining."""
    if total_ram_gb == 0:
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        total_ram_gb = int(line.split()[1]) / (1024 * 1024)
                        break
        except FileNotFoundError:
            total_ram_gb = 16

    # YesPowerTide: allocate ~2.5GB for mining (1280 x 2MB pages)
    # Scale with available RAM - use up to 20% of total RAM
    max_pages = int((total_ram_gb * 0.20) * 512)  # 512 pages per GB
    return max(1280, min(max_pages, 3072))


def setup_hugepages(nr_pages: int = 0) -> bool:
    """Set up 2MB huge pages. Requires sudo."""
    if nr_pages == 0:
        nr_pages = calculate_optimal_hugepages()

    current = get_hugepage_count()
    if current >= nr_pages:
        print(f"[OK] Huge pages already configured: {current} (requested: {nr_pages})")
        return True

    print(f"[*] Setting up {nr_pages} huge pages (2MB each = {nr_pages * 2}MB)...")

    # Drop caches first for better allocation
    try:
        subprocess.run(
            ["sudo", "sh", "-c", "echo 3 > /proc/sys/vm/drop_caches"],
            check=True, capture_output=True, timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass

    # Set huge pages
    try:
        subprocess.run(
            ["sudo", "sysctl", "-w", f"vm.nr_hugepages={nr_pages}"],
            check=True, capture_output=True, timeout=10,
        )
    except subprocess.CalledProcessError as e:
        print(f"[!] Failed to set huge pages: {e}")
        return False

    actual = get_hugepage_count()
    if actual < nr_pages:
        print(f"[!] Only allocated {actual}/{nr_pages} huge pages (memory fragmentation)")
        print("[*] Tip: Reboot and set hugepages early via kernel param: hugepages={nr_pages}")
    else:
        print(f"[OK] Allocated {actual} huge pages")

    return actual > 0


def setup_1gb_hugepages(count: int = 2) -> bool:
    """Set up 1GB huge pages for maximum performance. Requires kernel param."""
    current = get_1gb_hugepage_count()
    if current >= count:
        print(f"[OK] 1GB huge pages: {current}")
        return True

    # 1GB hugepages typically need kernel boot param
    print(f"[*] 1GB huge pages need kernel boot parameter.")
    print(f"    Add to GRUB_CMDLINE_LINUX: 'hugepagesz=1G hugepages={count}'")
    print(f"    Then run: sudo update-grub && sudo reboot")
    return False


def make_persistent(nr_pages: int = 0) -> bool:
    """Make huge pages persistent across reboots."""
    if nr_pages == 0:
        nr_pages = calculate_optimal_hugepages()

    sysctl_conf = "/etc/sysctl.d/99-tidemine-hugepages.conf"
    content = f"# Tidemine mining optimization\nvm.nr_hugepages = {nr_pages}\n"

    try:
        subprocess.run(
            ["sudo", "tee", sysctl_conf],
            input=content.encode(),
            check=True, capture_output=True, timeout=10,
        )
        print(f"[OK] Huge pages persistent: {sysctl_conf}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[!] Failed to persist huge pages: {e}")
        return False


def setup_all() -> dict:
    """Full huge pages setup with persistence."""
    nr_pages = calculate_optimal_hugepages()
    result = {
        "2mb_pages": setup_hugepages(nr_pages),
        "persistent": make_persistent(nr_pages),
        "1gb_pages_available": get_1gb_hugepage_count() > 0,
        "total_pages": get_hugepage_count(),
        "page_size_kb": get_hugepage_size_kb(),
    }
    return result
