#!/usr/bin/env python3
"""Run backend test files in isolated processes and combine their coverage.

The repository's full suite accumulates process-global state when all test files
share one pytest process. Running each file independently preserves the full
set of tests while preventing one file's global state or native resource usage
from poisoning later files. Each child writes an independent coverage.py data
file; the files are combined only after every batch succeeds. The caller
performs the final coverage threshold check after this script exits.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from pathlib import Path

_GRACE_SECONDS = 10


def _test_files(root: Path) -> list[Path]:
    return sorted((root / "tests").glob("test_*.py"))


def _run_isolated(command: list[str], *, cwd: Path, env: dict[str, str]) -> int:
    """Run one child in its own session, but still die when the hook is cancelled.

    The child needs its own session so a test that signals its own process group
    cannot take down this runner or the git hook driving it. That isolation
    otherwise makes Ctrl-C orphan a running pytest, so forward the interrupt to
    the child's group by hand and escalate if it does not leave.
    """
    child = subprocess.Popen(command, cwd=cwd, env=env, start_new_session=True)
    received: int | None = None

    def _forward(signum: int, _frame: object) -> None:
        nonlocal received
        received = signum

    previous_int = signal.signal(signal.SIGINT, _forward)
    previous_term = signal.signal(signal.SIGTERM, _forward)
    try:
        while True:
            try:
                return child.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                if received is None:
                    continue
                try:
                    group = os.getpgid(child.pid)
                    os.killpg(group, signal.SIGTERM)
                    try:
                        child.wait(timeout=_GRACE_SECONDS)
                    except subprocess.TimeoutExpired:
                        os.killpg(group, signal.SIGKILL)
                        child.wait()
                except ProcessLookupError:
                    child.wait()
                return 128 + received
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunk-size", type=int, default=1)
    args = parser.parse_args(argv)
    if args.chunk_size < 1:
        parser.error("--chunk-size must be positive")

    root = Path(__file__).resolve().parents[1]
    files = _test_files(root)
    if not files:
        print("no backend test files found", file=sys.stderr)
        return 2

    environment = os.environ.copy()
    source_path = str(root / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        item for item in (source_path, environment.get("PYTHONPATH", "")) if item
    )
    # Keep the ledger location deterministic for the pre-push coverage report.
    # coverage run --parallel-mode then adds the hostname/pid suffix per child,
    # avoiding concurrent SQLite writes to one shared .coverage file.
    environment["COVERAGE_FILE"] = str(root / ".coverage")
    erase = subprocess.run(
        [sys.executable, "-m", "coverage", "erase"],
        cwd=root,
        env=environment,
        start_new_session=True,
    )
    if erase.returncode != 0:
        print(
            f"[batched-pytest] FAILED coverage cleanup (exit {erase.returncode})",
            file=sys.stderr,
        )
        return erase.returncode

    total = (len(files) + args.chunk_size - 1) // args.chunk_size
    for batch_number, start in enumerate(range(0, len(files), args.chunk_size), start=1):
        batch = files[start : start + args.chunk_size]
        print(
            f"[batched-pytest] batch {batch_number}/{total}: "
            f"{batch[0].relative_to(root)} .. {batch[-1].relative_to(root)}",
            flush=True,
        )
        command = [
            sys.executable,
            "-m",
            "coverage",
            "run",
            "--parallel-mode",
            "--source=trustforge,trustforge_core",
            "-m",
            "pytest",
            "-p",
            "no:cov",
            "--override-ini",
            "addopts=",
            "-q",
            "--no-header",
            "--no-summary",
            "--disable-warnings",
        ]
        command.extend(str(path) for path in batch)
        returncode = _run_isolated(command, cwd=root, env=environment)
        if returncode == 5:
            print(
                f"[batched-pytest] batch {batch_number} collected no runnable tests; continuing",
                flush=True,
            )
        elif returncode != 0:
            print(
                f"[batched-pytest] FAILED batch {batch_number} "
                f"(exit {returncode})",
                file=sys.stderr,
            )
            return returncode

    combined = _run_isolated(
        [sys.executable, "-m", "coverage", "combine"],
        cwd=root,
        env=environment,
    )
    if combined != 0:
        print(
            f"[batched-pytest] FAILED coverage combine (exit {combined})",
            file=sys.stderr,
        )
        return combined
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
