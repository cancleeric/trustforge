"""Read-only pytest runtime benchmark inventory for issue #479."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


SUMMARY_RE = re.compile(
    r"(?P<count>\d+)\s+"
    r"(?P<kind>passed|failed|skipped|error|errors|xfailed|xpassed|deselected)"
)
DURATION_RE = re.compile(
    r"^\s*(?P<seconds>\d+(?:\.\d+)?)s\s+"
    r"(?P<phase>setup|call|teardown)\s+"
    r"(?P<nodeid>.+)$"
)
SECRET_RE = re.compile(r"(?i)(token|secret|password|key|credential|authorization)")
PATH_RE = re.compile(r"(?<![\w.-])(?:/[\w./@+=,: -]+)+")


@dataclass(frozen=True)
class RunResult:
    name: str
    command: list[str]
    returncode: int
    wall_seconds: float
    stdout: str
    stderr: str


def _redact(text: str, repo_root: Path) -> str:
    home = str(Path.home())
    redacted = text.replace(str(repo_root), "<repo>")
    redacted = redacted.replace(home, "<home>")

    lines: list[str] = []
    for line in redacted.splitlines():
        if SECRET_RE.search(line):
            lines.append("<redacted secret-like line>")
        else:
            lines.append(PATH_RE.sub("<path>", line))
    return "\n".join(lines)


def _run(command: Sequence[str], name: str, repo_root: Path) -> RunResult:
    start = time.perf_counter()
    completed = subprocess.run(
        list(command),
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return RunResult(
        name=name,
        command=list(command),
        returncode=completed.returncode,
        wall_seconds=time.perf_counter() - start,
        stdout=_redact(completed.stdout, repo_root),
        stderr=_redact(completed.stderr, repo_root),
    )


def _pytest_version(repo_root: Path) -> str:
    result = _run([_pytest_bin(), "--version"], "pytest-version", repo_root)
    return (result.stdout or result.stderr).strip()


def _pytest_bin() -> str:
    configured = os.environ.get("PYTEST_BIN")
    if configured:
        return configured
    return "pytest"


def _collect_inventory(result: RunResult) -> dict[str, object]:
    nodeids = [
        line.strip()
        for line in result.stdout.splitlines()
        if "::" in line and not line.lstrip().startswith("<")
    ]
    return {
        "returncode": result.returncode,
        "wall_seconds": round(result.wall_seconds, 3),
        "collected_count": len(nodeids),
        "nodeids": nodeids,
    }


def _collect_args(pytest_args: Sequence[str]) -> list[str]:
    args: list[str] = []
    for arg in pytest_args:
        if arg in {"-q", "--quiet"} or (arg.startswith("-") and set(arg[1:]) == {"q"}):
            continue
        args.append(arg)
    return [*args, "--collect-only", "-q"]


def _summary_counts(output: str) -> dict[str, int]:
    counts = {
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "errors": 0,
        "xfailed": 0,
        "xpassed": 0,
        "deselected": 0,
    }
    for match in SUMMARY_RE.finditer(output):
        kind = match.group("kind")
        if kind == "error":
            kind = "errors"
        counts[kind] += int(match.group("count"))
    return counts


def _slowest(output: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in output.splitlines():
        match = DURATION_RE.match(line)
        if not match:
            continue
        rows.append(
            {
                "seconds": float(match.group("seconds")),
                "phase": match.group("phase"),
                "nodeid": match.group("nodeid"),
            }
        )
    return rows[:50]


def _run_inventory(run: RunResult) -> dict[str, object]:
    output = "\n".join(part for part in [run.stdout, run.stderr] if part)
    return {
        "name": run.name,
        "returncode": run.returncode,
        "wall_seconds": round(run.wall_seconds, 3),
        "counts": _summary_counts(output),
        "slowest": _slowest(output),
        "stdout_tail": "\n".join(run.stdout.splitlines()[-40:]),
        "stderr_tail": "\n".join(run.stderr.splitlines()[-40:]),
    }


def _markdown(report: dict[str, object]) -> str:
    measured = report["measured_runs"]
    lines = [
        "# Pytest Benchmark Inventory",
        "",
        f"- Created: `{report['created_at']}`",
        f"- Python: `{report['python_version']}`",
        f"- Pytest: `{report['pytest_version']}`",
        f"- Command: `{' '.join(report['pytest_command'])}`",
        f"- Collection count: `{report['collection']['collected_count']}`",
        f"- Collection exit: `{report['collection']['returncode']}`",
        f"- Warmup exit: `{report['warmup']['returncode']}`",
        f"- Measured median seconds: `{report['measured_wall_seconds']['median']}`",
        f"- Measured range seconds: `{report['measured_wall_seconds']['min']}..{report['measured_wall_seconds']['max']}`",
        "",
        "## Measured Runs",
        "",
    ]
    for run in measured:
        counts = ", ".join(f"{key}={value}" for key, value in run["counts"].items() if value)
        lines.append(
            f"- `{run['name']}`: exit `{run['returncode']}`, "
            f"wall `{run['wall_seconds']}`s, {counts or 'no terminal counts parsed'}"
        )
    lines.extend(["", "## Slowest 50 From Last Measured Run", ""])
    last_slowest = measured[-1]["slowest"] if measured else []
    for item in last_slowest[:50]:
        lines.append(f"- `{item['seconds']:.3f}s` `{item['phase']}` `{item['nodeid']}`")
    if not last_slowest:
        lines.append("- No duration rows parsed.")
    lines.append("")
    return "\n".join(lines)


def build_report(repo_root: Path, pytest_args: Sequence[str], measured_runs: int) -> tuple[int, dict[str, object]]:
    base_command = [_pytest_bin(), *pytest_args]
    collect = _run([_pytest_bin(), *_collect_args(pytest_args)], "collect", repo_root)
    warmup = _run([*base_command, "--durations=50", "--durations-min=0"], "warmup", repo_root)
    measured = [
        _run([*base_command, "--durations=50", "--durations-min=0"], f"measured-{index}", repo_root)
        for index in range(1, measured_runs + 1)
    ]

    measured_seconds = [run.wall_seconds for run in measured]
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": "<repo>",
        "python_version": platform.python_version(),
        "pytest_version": _pytest_version(repo_root),
        "pytest_command": base_command,
        "collection": _collect_inventory(collect),
        "warmup": _run_inventory(warmup),
        "measured_runs": [_run_inventory(run) for run in measured],
        "measured_wall_seconds": {
            "median": round(statistics.median(measured_seconds), 3),
            "min": round(min(measured_seconds), 3),
            "max": round(max(measured_seconds), 3),
        },
    }

    for result in [collect, warmup, *measured]:
        if result.returncode != 0:
            return result.returncode, report
    return 0, report


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/pytest-benchmark-inventory"),
        help="Directory for JSON and Markdown reports.",
    )
    parser.add_argument(
        "--measured-runs",
        type=int,
        default=3,
        help="Number of measured pytest runs after the warmup.",
    )
    parser.add_argument(
        "pytest_args",
        nargs=argparse.REMAINDER,
        help="Arguments after -- are passed to pytest.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.measured_runs < 1:
        raise SystemExit("--measured-runs must be >= 1")

    pytest_args = list(args.pytest_args)
    if pytest_args[:1] == ["--"]:
        pytest_args = pytest_args[1:]

    repo_root = Path.cwd()
    exit_code, report = build_report(repo_root, pytest_args, args.measured_runs)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = args.output_dir / f"pytest-benchmark-{stamp}.json"
    md_path = args.output_dir / f"pytest-benchmark-{stamp}.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(_markdown(report), encoding="utf-8")

    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
