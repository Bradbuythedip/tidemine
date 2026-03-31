"""Generic process management for mining subprocesses."""

import os
import shutil
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
    """Start a managed subprocess with a PTY via screen.

    SRBMiner requires a pseudo-terminal (PTY) to run correctly.
    Without a TTY, it exits on non-fatal warnings. We use screen
    to provide a proper PTY session.
    """
    ensure_dirs()

    if is_running(name):
        stop_process(name)

    log_file = LOG_DIR / f"{name}.log"
    screen_name = f"tidemine-{name}"

    full_env = os.environ.copy()
    if env:
        full_env.update(env)

    # Write a launcher script for clean argument handling
    launcher = DATA_DIR / f"launch_{name}.sh"
    binary_dir = cwd or str(Path(cmd[0]).parent)
    cmd_str = " ".join(str(c) for c in cmd)
    launcher.write_text(
        f"#!/usr/bin/env bash\n"
        f"cd \"{binary_dir}\"\n"
        f"{cmd_str} 2>&1 | tee -a \"{log_file}\"\n"
    )
    launcher.chmod(0o755)

    # Kill any existing screen session with this name
    subprocess.run(
        ["screen", "-S", screen_name, "-X", "quit"],
        capture_output=True,
    )

    # Launch in screen
    proc = subprocess.Popen(
        ["screen", "-dmS", screen_name, "bash", str(launcher)],
        env=full_env,
        start_new_session=True,
    )
    proc.wait()  # screen -dm returns immediately

    # Find the actual miner PID
    time.sleep(2)
    miner_pid = _find_process_pid(cmd[0])

    if miner_pid:
        # Set process priority
        try:
            p = psutil.Process(miner_pid)
            p.nice(nice)
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass
        save_pid(name, miner_pid)
        # Return a fake Popen-like with the real PID
        proc.pid = miner_pid
    else:
        save_pid(name, proc.pid)

    return proc


def _find_process_pid(binary_path: str) -> Optional[int]:
    """Find PID of a running process by binary path."""
    binary_name = Path(binary_path).name
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = p.info.get("cmdline") or []
            if any(binary_name in str(c) for c in cmdline):
                return p.info["pid"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return None


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
