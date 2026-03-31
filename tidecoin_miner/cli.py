"""Tidemine CLI - Tidecoin Post-Quantum CPU+GPU Mining Orchestrator."""

import signal
import sys
import time
import threading
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from tidecoin_miner.config import (
    load_config, save_config, CONFIG_PATH, POOL_REGISTRY, get_pool_url
)

app = typer.Typer(
    name="tidecoin-miner",
    help="Tidemine - Tidecoin Post-Quantum CPU+GPU Mining Orchestrator",
    no_args_is_help=True,
)
console = Console()


@app.command()
def install(force: bool = typer.Option(False, "--force", "-f", help="Force reinstall")):
    """Download and install mining binaries and wallet."""
    from tidecoin_miner.miner_core.installer import install_all
    console.print("[bold cyan]Installing Tidemine components...[/]")
    install_all(force=force)
    console.print("[bold green]Installation complete![/]")


@app.command()
def start(
    wallet: Optional[str] = typer.Option(None, "--wallet", "-w", help="TDC wallet address"),
    pool: Optional[str] = typer.Option(None, "--pool", "-p", help="Pool name"),
    threads: Optional[int] = typer.Option(None, "--threads", "-t", help="CPU threads"),
    benchmark_first: bool = typer.Option(False, "--benchmark", "-b", help="Run benchmark first"),
    daemon: bool = typer.Option(False, "--daemon", "-d", help="Run in daemon mode (no dashboard)"),
):
    """Start CPU mining (YesPowerTide is CPU-only, GPU-resistant by design)."""
    from tidecoin_miner.miner_core import srbminer
    from tidecoin_miner.optimizer.hugepages import setup_hugepages
    from tidecoin_miner.optimizer.tuner import apply_all_optimizations
    from tidecoin_miner.pool_manager.failover import PoolFailover
    from tidecoin_miner.monitor.api import MetricsServer
    from tidecoin_miner.monitor.stats import StatsCollector
    from tidecoin_miner.monitor.alerts import AlertManager

    cfg = load_config()

    # Apply overrides
    if wallet:
        cfg["wallet"]["address"] = wallet
        save_config(cfg)
    if threads:
        cfg["mining"]["cpu_threads"] = threads

    if not cfg["wallet"]["address"]:
        console.print("[bold red]Error:[/] Wallet address required. Use --wallet or set in config.")
        raise typer.Exit(1)

    # Apply system optimizations
    console.print("[bold cyan]Applying system optimizations...[/]")
    setup_hugepages()
    apply_all_optimizations()

    # Optional benchmark
    if benchmark_first:
        from tidecoin_miner.optimizer.benchmark import run_benchmark
        console.print("[bold cyan]Running benchmark...[/]")
        run_benchmark(duration=60)
        cfg = load_config()  # Reload after benchmark updates config

    # Start mining
    pool_name = pool or cfg["pool"]["primary"]
    console.print(f"[bold green]Starting miner on pool: {pool_name}[/]")
    srbminer.start(cfg, pool_name)

    # Start pool failover monitor
    failover = PoolFailover()
    failover.start(pool_name, interval=cfg["pool"]["health_check_interval"])

    # Start metrics API
    collector = StatsCollector()
    metrics = MetricsServer(port=cfg["monitor"]["api_port"], collector=collector)
    metrics.start()

    # Start alert monitoring
    alert_mgr = AlertManager(cfg)

    if daemon:
        # Daemon mode - run in background with health monitoring
        console.print("[bold green]Mining started in daemon mode[/]")
        console.print(f"  Metrics API: http://localhost:{cfg['monitor']['api_port']}")
        console.print(f"  Dashboard:   tidecoin-miner dashboard")

        running = True
        def handle_signal(sig, frame):
            nonlocal running
            running = False

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

        while running:
            try:
                # Health check loop
                from tidecoin_miner.miner_core.process import is_running as _is_running
                if not _is_running("srbminer"):
                    console.print("[yellow]Miner stopped unexpectedly. Restarting...[/]")
                    srbminer.start(cfg, failover.current_pool or pool_name)

                # Collect stats and check alerts
                snapshot = collector.collect()
                alerts = alert_mgr.check(snapshot)
                for a in alerts:
                    if a and a.get("action") == "restart_miner":
                        srbminer.restart(cfg, failover.current_pool or pool_name)

                time.sleep(cfg["monitor"]["refresh_interval"])
            except KeyboardInterrupt:
                break

        # Cleanup
        srbminer.stop()
        failover.stop()
        metrics.stop()
        console.print("[bold]Miner stopped.[/]")
    else:
        # Interactive mode - launch dashboard
        from tidecoin_miner.monitor.dashboard import run_dashboard
        try:
            run_dashboard()
        finally:
            srbminer.stop()
            failover.stop()
            metrics.stop()


@app.command()
def stop():
    """Stop the miner."""
    from tidecoin_miner.miner_core import srbminer
    srbminer.stop()


@app.command()
def status():
    """Show miner status."""
    from tidecoin_miner.miner_core import srbminer as srb

    st = srb.status()
    if st["running"]:
        hr = srb.get_hashrate()
        shares = srb.get_shares()

        table = Table(title="Tidemine Status", show_header=False)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="bold")

        gpu = srb.get_gpu_info()
        table.add_row("Status", "[bold green]MINING[/]")
        table.add_row("PID", str(st["process"]["pid"]) if st["process"] else "?")
        table.add_row("Mode", "CPU + GPU")
        table.add_row("CPU Hashrate", f"{hr['cpu']:.1f} H/s")
        table.add_row("GPU Hashrate", f"{hr.get('gpu', 0):.1f} H/s")
        table.add_row("Total Hashrate", f"[bold]{hr['total']:.1f} H/s[/]")
        table.add_row("Accepted Shares", str(shares.get("accepted", 0)))
        table.add_row("Rejected Shares", str(shares.get("rejected", 0)))
        if gpu:
            table.add_row("GPU Temp", f"{gpu.get('temperature', 0)}C")
            table.add_row("GPU Power", f"{gpu.get('power', 0)}W")

        console.print(table)
    else:
        console.print("[bold red]Miner is not running[/]")


@app.command()
def dashboard():
    """Launch the live monitoring dashboard."""
    from tidecoin_miner.monitor.dashboard import run_dashboard
    run_dashboard()


@app.command(name="benchmark")
def run_bench(
    duration: int = typer.Option(60, "--duration", "-d", help="Benchmark duration in seconds"),
):
    """Run performance benchmark to find optimal settings."""
    from tidecoin_miner.optimizer.benchmark import run_benchmark
    from tidecoin_miner.optimizer.hugepages import setup_hugepages
    from tidecoin_miner.optimizer.tuner import apply_all_optimizations

    setup_hugepages()
    apply_all_optimizations()
    results = run_benchmark(duration=duration)

    table = Table(title="Benchmark Results")
    table.add_column("Threads", justify="right")
    table.add_column("CPU H/s", justify="right")
    table.add_column("GPU H/s", justify="right")
    table.add_column("Total H/s", justify="right")
    table.add_column("Per Thread", justify="right")

    for t in results.get("tests", []):
        table.add_row(
            str(t["threads"]),
            f"{t['avg_cpu_hashrate']:.1f}",
            f"{t['avg_gpu_hashrate']:.1f}",
            f"{t['avg_total_hashrate']:.1f}",
            f"{t['per_thread_hashrate']:.1f}",
        )

    console.print(table)
    best = results.get("best", {})
    console.print(f"\n[bold green]Optimal: {best.get('threads')} threads @ {best.get('hashrate', 0):.1f} H/s[/]")


@app.command()
def pools():
    """List all pools with latency test."""
    from tidecoin_miner.pool_manager.pools import test_all_pools

    console.print("[bold]Testing pool latency...[/]\n")
    results = test_all_pools()

    table = Table(title="Pool Latency Results")
    table.add_column("Pool", style="cyan")
    table.add_column("Host")
    table.add_column("Port", justify="right")
    table.add_column("Fee")
    table.add_column("Latency", justify="right")
    table.add_column("Status")

    for r in results:
        latency = f"{r['latency_ms']:.0f}ms" if r['latency_ms'] else "N/A"
        status = "[green]OK[/]" if r['reachable'] else "[red]DOWN[/]"
        table.add_row(r["name"], r["host"], str(r["port"]), r["fee"], latency, status)

    console.print(table)


@app.command()
def earnings():
    """Show earnings summary."""
    from tidecoin_miner.wallet.balance import load_earnings, estimate_earnings, get_tdc_price
    from tidecoin_miner.miner_core.srbminer import get_hashrate

    data = load_earnings()
    hr = get_hashrate()
    price = get_tdc_price()
    est = estimate_earnings(hr["total"], price)

    table = Table(title="Earnings Summary")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="bold")

    table.add_row("Total TDC Mined", f"{data.get('total_tdc', 0):.4f}")
    table.add_row("Current Hashrate", f"{hr['total']:.1f} H/s")
    table.add_row("TDC Price", f"${est['tdc_price_usd']:.4f}")
    table.add_row("Est. Daily TDC", f"{est['tdc_per_day']:.4f}")
    table.add_row("Est. Daily USD", f"${est['usd_per_day']:.4f}")
    table.add_row("Est. Monthly TDC", f"{est['tdc_per_month']:.4f}")
    table.add_row("Est. Monthly USD", f"${est['usd_per_month']:.4f}")
    table.add_row("Sessions", str(len(data.get("sessions", []))))

    console.print(table)


@app.command()
def config():
    """Show current configuration."""
    cfg = load_config()
    import yaml
    console.print(f"[bold]Config file:[/] {CONFIG_PATH}\n")
    console.print(yaml.dump(cfg, default_flow_style=False))


@app.command()
def update():
    """Update miner to latest version."""
    from tidecoin_miner.miner_core.installer import install_srbminer
    console.print("[bold cyan]Updating SRBMiner...[/]")
    install_srbminer(force=True)
    console.print("[bold green]Update complete![/]")


@app.command(name="install-service")
def install_svc():
    """Install systemd service for auto-start on boot."""
    from tidecoin_miner.systemd.service import install_service
    install_service()


@app.command(name="uninstall-service")
def uninstall_svc():
    """Remove systemd service."""
    from tidecoin_miner.systemd.service import uninstall_service
    uninstall_service()


if __name__ == "__main__":
    app()
