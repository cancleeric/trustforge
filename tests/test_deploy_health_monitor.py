from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "monitor_deploy_health.sh"


class _QuietHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        return


class _ThreadedServer:
    def __init__(self) -> None:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
        self.port = int(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._stopped = False

    def start(self) -> "_ThreadedServer":
        self.thread.start()
        for _ in range(50):
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.1):
                    return self
            except OSError:
                time.sleep(0.02)
        self.stop()
        raise RuntimeError("test HTTP server did not start")

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


def _monitor(tmp_path: Path, port: int, command: list[str]) -> subprocess.CompletedProcess[str]:
    evidence = tmp_path / "canary.jsonl"
    env = {
        **os.environ,
        "TRUSTFORGE_DEPLOY_MONITOR_INTERVAL": "0.03",
        "TRUSTFORGE_DEPLOY_MONITOR_CONNECT_TIMEOUT": "0.2",
        "TRUSTFORGE_DEPLOY_MONITOR_REQUEST_TIMEOUT": "0.2",
        "TRUSTFORGE_DEPLOY_MONITOR_EVIDENCE": str(evidence),
    }
    return subprocess.run(
        ["bash", str(SCRIPT), f"http://127.0.0.1:{port}/", "--", *command],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
    )


def test_monitor_records_healthy_deploy(tmp_path):
    server = _ThreadedServer().start()
    try:
        result = _monitor(tmp_path, server.port, ["sleep", "0.15"])
    finally:
        server.stop()

    assert result.returncode == 0, result.stderr
    rows = [json.loads(line) for line in (tmp_path / "canary.jsonl").read_text().splitlines()]
    assert rows[0]["phase"] == "before"
    assert rows[-1]["phase"] == "after"
    assert all(row["http_code"] == 200 and row["curl_exit"] == 0 for row in rows)


def test_monitor_fails_when_service_drops_during_deploy(tmp_path):
    server = _ThreadedServer().start()
    try:
        stopper = threading.Thread(
            target=lambda: (time.sleep(0.08), server.stop()),
            daemon=True,
        )
        stopper.start()
        result = _monitor(
            tmp_path,
            server.port,
            [sys.executable, "-c", "import time; time.sleep(0.15)"],
        )
    finally:
        server.stop()

    assert result.returncode != 0
    rows = [json.loads(line) for line in (tmp_path / "canary.jsonl").read_text().splitlines()]
    assert any(row["http_code"] == 0 or row["curl_exit"] != 0 for row in rows)


def test_production_workflow_wraps_backend_deploy():
    workflow = (ROOT / ".github" / "workflows" / "deploy-production.yml.disabled").read_text()
    assert "scripts/monitor_deploy_health.sh" in workflow
    assert "out/release/deploy-health-canary.jsonl" in workflow
    assert workflow.index("Deploy frontend to EC2 nginx") < workflow.index(
        "Deploy backend to EC2 through SSM"
    )
