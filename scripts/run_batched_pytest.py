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
import subprocess
import sys
from pathlib import Path


def _test_files(root: Path) -> list[Path]:
    return sorted((root / "tests").glob("test_*.py"))


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
        result = subprocess.run(command, cwd=root, env=environment, start_new_session=True)
        if result.returncode == 5:
            print(
                f"[batched-pytest] batch {batch_number} collected no runnable tests; continuing",
                flush=True,
            )
        elif result.returncode != 0:
            print(
                f"[batched-pytest] FAILED batch {batch_number} "
                f"(exit {result.returncode})",
                file=sys.stderr,
            )
            return result.returncode

    combine = subprocess.run(
        [sys.executable, "-m", "coverage", "combine"],
        cwd=root,
        env=environment,
        start_new_session=True,
    )
    if combine.returncode != 0:
        print(
            f"[batched-pytest] FAILED coverage combine (exit {combine.returncode})",
            file=sys.stderr,
        )
        return combine.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
