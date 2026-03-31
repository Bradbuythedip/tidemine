"""Generic process management for mining subprocesses."""

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional

import psutil

from tidecoin_miner.config import DATA_DIR, LOG_DIR, ensure_dirs

PID_DIR = DATA_DIR


def get_pid_file(name: str) -> Path:
    return PID_DIR / f"{name}.pid"


def save_pid(name: str, pid: int):
    ensure_dirs()
    get_pid_file(name).write_text(str(pid))


def read_pid(name: str) -> Optional[int]:
    pf = get_pid_file(name)
    if pf.exists():
        try:
            return int(pf.read_text().strip())
        except ValueError:
            return None
    return None


def is_running(name: str) -> bool:
    pid = read_pid(name)
    if pid is None:
        return False
    try:
        proc = psutil.Process(pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def stop_process(name: str, timeout: int = 15) -> bool:
    """Gracefully stop a managed process."""
    pid = read_pid(name)
    if pid is None:
        return False

    try:
        proc = psutil.Process(pid)
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=timeout)
    except psutil.NoSuchProcess:
        pass
    except psutil.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except psutil.NoSuchProcess:
            pass

    get_pid_file(name).unlink(missing_ok=True)
    return True


def start_process(
    name: str,
    cmd: list[str],
    env: Optional[dict] = None,
    cwd: Optional[str] = None,
    nice: int = -10,
) -> subprocess.Popen:
    """Start a managed subprocess with priority and logging."""
    ensure_dirs()

    if is_running(name):
        stop_process(name)

    log_file = LOG_DIR / f"{name}.log"

    full_env = os.environ.copy()
    if env:
        full_env.update(env)

    with open(log_file, "a") as log:
        proc = subprocess.Popen(
            cmd,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=full_env,
            cwd=cwd,
            start_new_session=True,
        )

    # Set process priority
    try:
        p = psutil.Process(proc.pid)
        p.nice(nice)
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        pass

    save_pid(name, proc.pid)
    return proc


def get_process_info(name: str) -> Optional[dict]:
    """Get info about a managed process."""
    pid = read_pid(name)
    if pid is None:
        return None
    try:
        proc = psutil.Process(pid)
        return {
            "pid": pid,
            "name": name,
            "status": proc.status(),
            "cpu_percent": proc.cpu_percent(interval=0.1),
            "memory_mb": proc.memory_info().rss / (1024 * 1024),
            "create_time": proc.create_time(),
            "running": proc.is_running(),
        }
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None
