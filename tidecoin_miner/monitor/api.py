"""Local JSON metrics API for external monitoring."""

import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

from tidecoin_miner.monitor.stats import StatsCollector


class _MetricsHandler(BaseHTTPRequestHandler):
    """HTTP handler for metrics endpoint."""

    collector: Optional[StatsCollector] = None

    def do_GET(self):
        if self.path == "/" or self.path == "/metrics":
            self._send_json(self._get_metrics())
        elif self.path == "/health":
            self._send_json({"status": "ok"})
        elif self.path == "/hashrate":
            snapshot = self.collector.collect() if self.collector else {}
            self._send_json(snapshot.get("hashrate", {}))
        else:
            self.send_response(404)
            self.end_headers()

    def _get_metrics(self) -> dict:
        if self.collector:
            snapshot = self.collector.collect()
            averages = self.collector.get_averages()
            snapshot["averages"] = averages
            return snapshot
        return {"error": "collector not initialized"}

    def _send_json(self, data: dict):
        body = json.dumps(data, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # Suppress HTTP logs


class MetricsServer:
    """Background HTTP server for mining metrics."""

    def __init__(self, port: int = 8420, collector: Optional[StatsCollector] = None):
        self.port = port
        self.collector = collector or StatsCollector()
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Start metrics server in background."""
        _MetricsHandler.collector = self.collector
        self._server = HTTPServer(("0.0.0.0", self.port), _MetricsHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        print(f"[OK] Metrics API running on http://localhost:{self.port}")

    def stop(self):
        """Stop metrics server."""
        if self._server:
            self._server.shutdown()
