#!/usr/bin/env python3
"""Run backend test files in isolated processes and combine their coverage.

The repository's full suite accumulates process-global state when all test files
share one pytest process. Running each file independently preserves the full
set of tests while preventing one file's global state or native resource usage
from poisoning later files. Each child writes an independent coverage.py data
file; the files are combined only after every batch succeeds. The caller
performs the final coverage threshold check after this script exits.

Batches are independent processes, so several of them can be in flight at once
(``--workers``). That keeps the per-file isolation guarantee exactly as it is —
concurrency happens *between* processes, never inside one pytest run — while
recovering the wall time lost to ~400 interpreter starts. Files that reach for a
host-global resource (fixed port, fixed temp path, shared lock file) cannot
tolerate a concurrent neighbour; they are listed in ``tests/serial_batches.txt``
and run alone after the concurrent lane drains.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_GRACE_SECONDS = 10
_SERIAL_LIST = "tests/serial_batches.txt"


def _test_files(root: Path) -> list[Path]:
    return sorted((root / "tests").glob("test_*.py"))


def _serial_names(root: Path) -> set[str]:
    """Read the opt-out list of files that must not share the host with others."""
    listing = root / _SERIAL_LIST
    if not listing.is_file():
        return set()
    names: set[str] = set()
    for raw in listing.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            names.add(line)
    return names


class _ChildRegistry:
    """Track live children so one interrupt can tear down every batch in flight.

    The signal handler must live on the main thread, but batches are waited on
    by pool threads, so the handler cannot simply close over a single child the
    way the serial runner did.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._children: dict[int, subprocess.Popen] = {}
        self.cancelled = threading.Event()
        self.signal_number: int | None = None

    def add(self, child: subprocess.Popen) -> None:
        with self._lock:
            self._children[child.pid] = child

    def discard(self, child: subprocess.Popen) -> None:
        with self._lock:
            self._children.pop(child.pid, None)

    def _snapshot(self) -> list[subprocess.Popen]:
        with self._lock:
            return list(self._children.values())

    def terminate_all(self) -> None:
        """SIGTERM every child's process group, escalating to SIGKILL."""
        for child in self._snapshot():
            try:
                group = os.getpgid(child.pid)
            except ProcessLookupError:
                continue
            try:
                os.killpg(group, signal.SIGTERM)
            except ProcessLookupError:
                continue
        for child in self._snapshot():
            try:
                child.wait(timeout=_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(child.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def cancel(self, signum: int | None = None) -> None:
        if signum is not None and self.signal_number is None:
            self.signal_number = signum
        self.cancelled.set()
        self.terminate_all()


def _run_isolated(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    registry: _ChildRegistry,
    capture: bool,
) -> tuple[int, str]:
    """Run one child in its own session and return its exit code plus output.

    The child needs its own session so a test that signals its own process group
    cannot take down this runner or the git hook driving it. Concurrent batches
    would interleave their output line by line, so each child's stream is held
    and printed as one block by the caller.
    """
    if registry.cancelled.is_set():
        return 130, ""
    child = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        start_new_session=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        text=True if capture else None,
    )
    registry.add(child)
    try:
        output, _ = child.communicate()
        return child.returncode, output or ""
    finally:
        registry.discard(child)


def _pytest_command(batch: list[Path]) -> list[str]:
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
    return command


def _chunk(files: list[Path], size: int) -> list[list[Path]]:
    return [files[start : start + size] for start in range(0, len(files), size)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunk-size", type=int, default=1)
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="batches in flight at once; 0 or 1 keeps the original serial behaviour",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="repository root to scan; defaults to this script's repository",
    )
    args = parser.parse_args(argv)
    if args.chunk_size < 1:
        parser.error("--chunk-size must be positive")
    if args.workers < 0:
        parser.error("--workers must not be negative")
    workers = max(1, args.workers)

    root = (args.root or Path(__file__).resolve().parents[1]).resolve()
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

    registry = _ChildRegistry()

    def _forward(signum: int, _frame: object) -> None:
        registry.cancel(signum)

    previous_int = signal.signal(signal.SIGINT, _forward)
    previous_term = signal.signal(signal.SIGTERM, _forward)
    try:
        return _run_all(
            root=root,
            files=files,
            environment=environment,
            registry=registry,
            chunk_size=args.chunk_size,
            workers=workers,
        )
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)


def _run_all(
    *,
    root: Path,
    files: list[Path],
    environment: dict[str, str],
    registry: _ChildRegistry,
    chunk_size: int,
    workers: int,
) -> int:
    erase, _ = _run_isolated(
        [sys.executable, "-m", "coverage", "erase"],
        cwd=root,
        env=environment,
        registry=registry,
        capture=False,
    )
    if erase != 0:
        print(f"[batched-pytest] FAILED coverage cleanup (exit {erase})", file=sys.stderr)
        return erase

    serial_names = _serial_names(root)
    concurrent_files = [f for f in files if f.relative_to(root).as_posix() not in serial_names]
    serial_files = [f for f in files if f.relative_to(root).as_posix() in serial_names]

    # Serial batches run last and alone, so the concurrent lane never shares the
    # host with a file that was declared incompatible with a neighbour.
    batches = _chunk(concurrent_files, chunk_size) + _chunk(serial_files, chunk_size)
    total = len(batches)
    concurrent_count = len(_chunk(concurrent_files, chunk_size))
    if workers > 1:
        print(
            f"[batched-pytest] {total} batches: {concurrent_count} across "
            f"{workers} workers, {total - concurrent_count} serial",
            flush=True,
        )

    lanes: list[tuple[list[list[Path]], int, int]] = [
        (batches[:concurrent_count], workers, 1),
        (batches[concurrent_count:], 1, concurrent_count + 1),
    ]

    failure: int | None = None
    for lane_batches, lane_workers, first_number in lanes:
        if not lane_batches or failure is not None or registry.cancelled.is_set():
            continue
        result = _run_lane(
            lane_batches,
            first_number=first_number,
            total=total,
            root=root,
            environment=environment,
            registry=registry,
            workers=lane_workers,
        )
        if result is not None:
            failure = result

    if registry.cancelled.is_set():
        return 128 + (registry.signal_number or signal.SIGINT)
    if failure is not None:
        return failure

    combined, _ = _run_isolated(
        [sys.executable, "-m", "coverage", "combine"],
        cwd=root,
        env=environment,
        registry=registry,
        capture=False,
    )
    if combined != 0:
        print(f"[batched-pytest] FAILED coverage combine (exit {combined})", file=sys.stderr)
        return combined
    return 0


def _run_lane(
    lane_batches: list[list[Path]],
    *,
    first_number: int,
    total: int,
    root: Path,
    environment: dict[str, str],
    registry: _ChildRegistry,
    workers: int,
) -> int | None:
    """Run one lane, returning the exit code of the earliest failing batch."""
    capture = workers > 1
    failures: dict[int, int] = {}
    failure_lock = threading.Lock()

    def _aborted() -> bool:
        with failure_lock:
            return bool(failures) or registry.cancelled.is_set()

    def _run_one(index: int, batch: list[Path]) -> None:
        # Queued batches become no-ops once any batch has failed: the pool holds
        # every batch of the lane, so fail-fast has to be decided here rather
        # than at submit time.
        if _aborted():
            return
        batch_number = first_number + index
        label = (
            f"[batched-pytest] batch {batch_number}/{total}: "
            f"{batch[0].relative_to(root)} .. {batch[-1].relative_to(root)}"
        )
        if not capture:
            print(label, flush=True)
        returncode, output = _run_isolated(
            _pytest_command(batch),
            cwd=root,
            env=environment,
            registry=registry,
            capture=capture,
        )
        if capture:
            # One write keeps a batch's header and its pytest output together
            # even though neighbours finish in between. A batch torn down
            # mid-progress-line leaves output without a trailing newline, which
            # would otherwise glue the next batch's header onto it.
            if output and not output.endswith("\n"):
                output += "\n"
            sys.stdout.write(f"{label}\n{output}")
            sys.stdout.flush()
        if returncode == 5:
            print(
                f"[batched-pytest] batch {batch_number} collected no runnable tests; continuing",
                flush=True,
            )
            return
        if returncode == 0 or registry.cancelled.is_set():
            return
        with failure_lock:
            # A neighbour already failed and tore this child down; its exit code
            # is collateral, not a finding worth reporting.
            collateral = bool(failures)
            if not collateral:
                failures[batch_number] = returncode
        if collateral:
            return
        print(
            f"[batched-pytest] FAILED batch {batch_number} "
            f"({batch[0].relative_to(root)}, exit {returncode})",
            file=sys.stderr,
            flush=True,
        )
        # Stop the rest of the lane: a failed gate has nothing left to learn
        # from the remaining batches, and the caller aborts the push anyway.
        registry.terminate_all()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for index, batch in enumerate(lane_batches):
            if failures or registry.cancelled.is_set():
                break
            pool.submit(_run_one, index, batch)

    if not failures:
        return None
    return failures[min(failures)]


if __name__ == "__main__":
    raise SystemExit(main())
