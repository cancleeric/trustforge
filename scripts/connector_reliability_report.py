#!/usr/bin/env python3
"""Write the connector reliability gate report consumed by Hermes diagnostics."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from trustforge.connector_reliability import build_reliability_report
from trustforge.scheduler_log import get_recent_scheduler_runs
from trustforge.source_archive import SourceEventArchive


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build connector reliability evidence")
    parser.add_argument("--window", type=int, default=30)
    parser.add_argument("--required-successes", type=int, default=7)
    parser.add_argument("--source-window-seconds", type=float, default=86400.0)
    parser.add_argument("--freshness-slo-seconds", type=float, default=3600.0)
    parser.add_argument("--latency-p95-slo-ms", type=float, default=2000.0)
    parser.add_argument("--out", type=Path, default=REPO / "out" / "connector-reliability.json")
    args = parser.parse_args(argv)
    if args.window < 1:
        parser.error("--window must be >= 1")
    archive = SourceEventArchive()
    try:
        source_metrics = archive.observability_snapshot(window_seconds=args.source_window_seconds)
    finally:
        archive.close()
    report = build_reliability_report(
        get_recent_scheduler_runs(args.window),
        required_consecutive_successes=args.required_successes,
        source_metrics=source_metrics,
        freshness_slo_seconds=args.freshness_slo_seconds,
        latency_p95_slo_ms=args.latency_p95_slo_ms,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
