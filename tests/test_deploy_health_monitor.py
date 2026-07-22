from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "monitor_deploy_health.sh"


def _unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _server(port: int) -> subprocess.Popen[bytes]:
    process = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--directory", str(ROOT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return process
        except OSError:
            time.sleep(0.02)
    process.terminate()
    process.wait(timeout=5)
    raise RuntimeError("test HTTP server did not start")


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
    port = _unused_port()
    server = _server(port)
    try:
        result = _monitor(tmp_path, port, ["sleep", "0.15"])
    finally:
        server.terminate()
        server.wait(timeout=5)

    assert result.returncode == 0, result.stderr
    rows = [json.loads(line) for line in (tmp_path / "canary.jsonl").read_text().splitlines()]
    assert rows[0]["phase"] == "before"
    assert rows[-1]["phase"] == "after"
    assert all(row["http_code"] == 200 and row["curl_exit"] == 0 for row in rows)


def test_monitor_fails_when_service_drops_during_deploy(tmp_path):
    port = _unused_port()
    server = _server(port)
    try:
        result = _monitor(
            tmp_path,
            port,
            ["sh", "-c", f"sleep 0.08; kill {server.pid}; sleep 0.15"],
        )
    finally:
        if server.poll() is None:
            server.terminate()
        server.wait(timeout=5)

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
