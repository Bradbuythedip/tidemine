"""systemd service generator and manager."""

import os
import subprocess
from pathlib import Path

SERVICE_NAME = "tidemine"
SERVICE_FILE = f"/etc/systemd/system/{SERVICE_NAME}.service"


def generate_service_file() -> str:
    """Generate systemd service unit content."""
    user = os.environ.get("USER", "miner")
    home = Path.home()
    # Find the tidecoin-miner binary
    venv_bin = home / ".tidecoin-miner" / "venv" / "bin" / "tidecoin-miner"
    local_bin = Path("/usr/local/bin/tidecoin-miner")

    if venv_bin.exists():
        exec_start = str(venv_bin)
    elif local_bin.exists():
        exec_start = str(local_bin)
    else:
        exec_start = "tidecoin-miner"

    return f"""[Unit]
Description=Tidemine - Tidecoin Post-Quantum CPU+GPU Miner
Documentation=https://github.com/bradbuythedip/tidemine
After=network-online.target nvidia-persistenced.service
Wants=network-online.target

[Service]
Type=simple
User={user}
Group={user}
WorkingDirectory={home}
ExecStart={exec_start} start --daemon
ExecStop={exec_start} stop
Restart=always
RestartSec=30
TimeoutStopSec=60

# Performance optimizations
Nice=-10
IOSchedulingClass=realtime
IOSchedulingPriority=0
CPUSchedulingPolicy=batch

# Resource limits
LimitNOFILE=65536
LimitMEMLOCK=infinity

# Security hardening
ProtectSystem=false
ProtectHome=false
NoNewPrivileges=false

# Environment
Environment=HOME={home}
Environment=CUDA_VISIBLE_DEVICES=0

[Install]
WantedBy=multi-user.target
"""


def install_service() -> bool:
    """Install and enable the systemd service."""
    content = generate_service_file()

    try:
        # Write service file
        subprocess.run(
            ["sudo", "tee", SERVICE_FILE],
            input=content.encode(),
            check=True, capture_output=True, timeout=10,
        )

        # Reload systemd and enable
        subprocess.run(
            ["sudo", "systemctl", "daemon-reload"],
            check=True, capture_output=True, timeout=10,
        )
        subprocess.run(
            ["sudo", "systemctl", "enable", SERVICE_NAME],
            check=True, capture_output=True, timeout=10,
        )

        print(f"[OK] Service installed: {SERVICE_FILE}")
        print(f"     Start with: sudo systemctl start {SERVICE_NAME}")
        print(f"     Status:     sudo systemctl status {SERVICE_NAME}")
        print(f"     Logs:       journalctl -u {SERVICE_NAME} -f")
        return True

    except subprocess.CalledProcessError as e:
        print(f"[!] Failed to install service: {e}")
        return False


def uninstall_service() -> bool:
    """Remove the systemd service."""
    try:
        subprocess.run(
            ["sudo", "systemctl", "stop", SERVICE_NAME],
            capture_output=True, timeout=10,
        )
        subprocess.run(
            ["sudo", "systemctl", "disable", SERVICE_NAME],
            capture_output=True, timeout=10,
        )
        if Path(SERVICE_FILE).exists():
            subprocess.run(
                ["sudo", "rm", SERVICE_FILE],
                check=True, capture_output=True, timeout=10,
            )
        subprocess.run(
            ["sudo", "systemctl", "daemon-reload"],
            capture_output=True, timeout=10,
        )
        print(f"[OK] Service removed")
        return True
    except subprocess.CalledProcessError:
        return False


def service_status() -> str:
    """Check systemd service status."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", SERVICE_NAME],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return "unknown"
