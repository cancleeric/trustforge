#!/usr/bin/env python3
"""Run the local, no-AWS multi-angle release gate and emit one JSON result."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "tests" / "test_multi_angle_public_e2e.py"
TIMEOUT_SECONDS = 120
AWS_GUARD = r"""
import pathlib

MARKER = pathlib.Path(__import__("os").environ["TRUSTFORGE_AWS_GUARD_MARKER"])

def blocked(*args, **kwargs):
    MARKER.write_text("blocked", encoding="utf-8")
    raise RuntimeError("AWS access blocked by multi-angle release gate")

try:
    import boto3
except ImportError:
    pass
else:
    boto3.client = blocked
    boto3.resource = blocked
    boto3.session.Session.client = blocked
    boto3.session.Session.resource = blocked
"""


def _output_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    return (value or "").strip()


def main() -> int:
    started = time.monotonic()
    deadline = started + TIMEOUT_SECONDS
    with tempfile.TemporaryDirectory(prefix="trustforge-no-aws-") as temp:
        guard_dir = Path(temp)
        marker = guard_dir / "aws-access-attempted"
        config = guard_dir / "config"
        credentials = guard_dir / "credentials"
        config.touch()
        credentials.touch()
        (guard_dir / "sitecustomize.py").write_text(
            AWS_GUARD, encoding="utf-8"
        )
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("AWS_")
        }
        env.pop("TRUSTFORGE_ATOMIC_BATCH_TABLE", None)
        env.update({
            "AWS_CONFIG_FILE": str(config),
            "AWS_SHARED_CREDENTIALS_FILE": str(credentials),
            "AWS_EC2_METADATA_DISABLED": "true",
            "TRUSTFORGE_AWS_GUARD_MARKER": str(marker),
            "TRUSTFORGE_ENV": "test",
            "PYTHONPATH": os.pathsep.join(
                filter(None, (str(guard_dir), env.get("PYTHONPATH", "")))
            ),
        })
        try:
            negative = subprocess.run(
                [sys.executable, "-c", "import boto3; boto3.client('sts')"],
                cwd=ROOT, env=env, capture_output=True, text=True, check=False,
                timeout=min(15, max(0.1, deadline - time.monotonic())),
            )
            guard_proven = (
                negative.returncode != 0
                and marker.exists()
                and "AWS access blocked" in negative.stderr
            )
        except subprocess.TimeoutExpired:
            guard_proven = False
        marker.unlink(missing_ok=True)
        try:
            completed = subprocess.run(
                [
                    sys.executable, "-m", "pytest", "-q", str(SUITE),
                    "--no-cov",
                ],
                cwd=ROOT, env=env, capture_output=True, text=True, check=False,
                timeout=max(0.1, deadline - time.monotonic()),
            )
            aws_attempted = marker.exists()
            exit_code = completed.returncode
            status = (
                "passed"
                if exit_code == 0 and guard_proven and not aws_attempted
                else "failed"
            )
            if status == "failed" and exit_code == 0:
                exit_code = 2
            stdout = _output_text(completed.stdout)
            stderr = _output_text(completed.stderr)
        except subprocess.TimeoutExpired as exc:
            aws_attempted = marker.exists()
            exit_code = 124
            status = "timeout"
            stdout = _output_text(exc.stdout)
            stderr = _output_text(exc.stderr)
        result = {
            "gate": "multi-angle-release",
            "status": status,
            "exit_code": exit_code,
            "duration_seconds": round(time.monotonic() - started, 3),
            "timeout_seconds": TIMEOUT_SECONDS,
            "aws_enabled": not guard_proven or aws_attempted,
            "aws_guard_negative_control": guard_proven,
            "aws_access_attempted_by_suite": aws_attempted,
            "suite": str(SUITE.relative_to(ROOT)),
            "pytest_stdout": stdout,
            "pytest_stderr": stderr,
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
