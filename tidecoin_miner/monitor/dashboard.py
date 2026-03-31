"""Rich terminal dashboard for live mining monitoring."""

import time
import signal
import sys
from typing import Optional

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.columns import Columns

from tidecoin_miner.monitor.stats import StatsCollector
from tidecoin_miner.monitor.alerts import AlertManager
from tidecoin_miner.config import load_config
from tidecoin_miner.miner_core.srbminer import is_running


SPARKLINE_CHARS = "▁▂▃▄▅▆▇█"


def sparkline(data: list[float], width: int = 40) -> str:
    """Generate a sparkline string from data."""
    if not data:
        return ""
    mn, mx = min(data), max(data)
    rng = mx - mn if mx != mn else 1
    return "".join(SPARKLINE_CHARS[min(int((v - mn) / rng * 7), 7)] for v in data[-width:])


def format_hashrate(hr: float) -> str:
    """Format hashrate with appropriate unit."""
    if hr >= 1_000_000:
        return f"{hr/1_000_000:.2f} MH/s"
    elif hr >= 1_000:
        return f"{hr/1_000:.2f} KH/s"
    return f"{hr:.1f} H/s"


def format_uptime(seconds: float) -> str:
    """Format uptime as human readable."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    elif m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def make_header(snapshot: dict) -> Panel:
    """Create header panel."""
    uptime = format_uptime(snapshot.get("uptime", 0))
    status = "[bold green]MINING[/]" if is_running("srbminer") else "[bold red]STOPPED[/]"
    text = Text.from_markup(
        f"  [bold cyan]TIDEMINE[/] | Tidecoin Post-Quantum Miner "
        f"| Falcon-512 / FN-DSA | {status} | Uptime: {uptime}"
    )
    return Panel(text, style="bold blue")


def make_hashrate_panel(snapshot: dict, averages: dict, spark_data: list) -> Panel:
    """Create hashrate display panel."""
    hr = snapshot.get("hashrate", {})
    table = Table(show_header=True, header_style="bold cyan", expand=True, box=None)
    table.add_column("Source", width=10)
    table.add_column("Hashrate", width=14, justify="right")
    table.add_column("1min avg", width=12, justify="right")
    table.add_column("5min avg", width=12, justify="right")
    table.add_column("15min avg", width=12, justify="right")

    table.add_row(
        "[yellow]CPU[/]",
        format_hashrate(hr.get("cpu", 0)),
        "", "", "",
    )
    table.add_row(
        "[green]GPU[/]",
        format_hashrate(hr.get("gpu", 0)),
        "", "", "",
    )
    table.add_row(
        "[bold white]TOTAL[/]",
        f"[bold]{format_hashrate(hr.get('total', 0))}[/]",
        format_hashrate(averages.get("hashrate_1m", 0)),
        format_hashrate(averages.get("hashrate_5m", 0)),
        format_hashrate(averages.get("hashrate_15m", 0)),
    )

    spark = sparkline(spark_data)
    content = f"{table}\n\n  [dim]{spark}[/]"
    return Panel(content, title="[bold]Hashrate[/]", border_style="cyan")


def make_shares_panel(snapshot: dict) -> Panel:
    """Create shares statistics panel."""
    shares = snapshot.get("shares", {})
    table = Table(show_header=False, expand=True, box=None)
    table.add_column("Metric", width=14)
    table.add_column("Value", justify="right")

    table.add_row("Accepted", f"[green]{shares.get('accepted', 0)}[/]")
    table.add_row("Rejected", f"[red]{shares.get('rejected', 0)}[/]")
    table.add_row("Stale", f"[yellow]{shares.get('stale', 0)}[/]")
    rate = shares.get("acceptance_rate", 0)
    color = "green" if rate > 0.98 else "yellow" if rate > 0.95 else "red"
    table.add_row("Accept Rate", f"[{color}]{rate:.1%}[/]")

    return Panel(table, title="[bold]Shares[/]", border_style="green")


def make_hardware_panel(snapshot: dict) -> Panel:
    """Create hardware telemetry panel."""
    gpu = snapshot.get("gpu") or {}
    cpu = snapshot.get("cpu", {})
    mem = snapshot.get("memory", {})

    table = Table(show_header=False, expand=True, box=None)
    table.add_column("Metric", width=16)
    table.add_column("Value", justify="right")

    # GPU
    gpu_temp = gpu.get("temperature", 0)
    temp_color = "green" if gpu_temp < 70 else "yellow" if gpu_temp < 85 else "red"
    table.add_row("[bold]GPU[/]", "")
    table.add_row("  Temperature", f"[{temp_color}]{gpu_temp}C[/]")
    table.add_row("  Power", f"{gpu.get('power', 0)}W")
    table.add_row("  Core Clock", f"{gpu.get('clock_core', 0)} MHz")

    # CPU
    cpu_temps = cpu.get("temps", [])
    max_temp = max(cpu_temps) if cpu_temps else 0
    cpu_color = "green" if max_temp < 80 else "yellow" if max_temp < 95 else "red"
    table.add_row("[bold]CPU[/]", "")
    table.add_row("  Temperature", f"[{cpu_color}]{max_temp:.0f}C[/]")
    table.add_row("  Frequency", f"{cpu.get('freq', {}).get('current', 0):.0f} MHz")
    table.add_row("  Usage", f"{cpu.get('avg_usage', 0):.0f}%")

    # Memory
    table.add_row("[bold]Memory[/]", f"{mem.get('used_gb', 0):.1f}/{mem.get('total_gb', 0):.1f} GB")

    # Efficiency
    eff = snapshot.get("power_efficiency", 0)
    table.add_row("[bold]Efficiency[/]", f"{eff:.3f} H/W")

    return Panel(table, title="[bold]Hardware[/]", border_style="yellow")


def make_alert_panel(alerts: list[dict]) -> Panel:
    """Create alert/event log panel."""
    if not alerts:
        content = "[dim]No alerts[/]"
    else:
        lines = []
        for a in alerts[-8:]:
            if a is None:
                continue
            ts = time.strftime("%H:%M:%S", time.localtime(a["timestamp"]))
            sev = a["severity"]
            color = "red" if sev == "critical" else "yellow" if sev == "warning" else "blue"
            lines.append(f"[{color}]{ts} [{sev.upper()}] {a['message']}[/]")
        content = "\n".join(lines) if lines else "[dim]No alerts[/]"

    return Panel(content, title="[bold]Alerts[/]", border_style="red")


def run_dashboard():
    """Run the live terminal dashboard."""
    console = Console()
    cfg = load_config()
    stats = StatsCollector()
    alert_mgr = AlertManager(cfg)
    refresh = cfg["monitor"]["refresh_interval"]

    running = True

    def handle_signal(sig, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    console.clear()
    console.print("[bold cyan]TIDEMINE Dashboard[/] - Press Ctrl+C to exit\n")

    try:
        with Live(console=console, refresh_per_second=1, screen=True) as live:
            while running:
                snapshot = stats.collect()
                averages = stats.get_averages()
                spark_data = stats.get_sparkline_data(60)
                new_alerts = alert_mgr.check(snapshot)

                layout = Layout()
                layout.split_column(
                    Layout(make_header(snapshot), size=3),
                    Layout(name="main"),
                    Layout(make_alert_panel(alert_mgr.alert_log), size=12),
                )
                layout["main"].split_row(
                    Layout(name="left"),
                    Layout(make_hardware_panel(snapshot), ratio=1),
                )
                layout["left"].split_column(
                    Layout(make_hashrate_panel(snapshot, averages, spark_data), ratio=2),
                    Layout(make_shares_panel(snapshot), ratio=1),
                )

                live.update(layout)
                time.sleep(refresh)
    except KeyboardInterrupt:
        pass

    console.print("\n[bold]Dashboard stopped.[/]")
