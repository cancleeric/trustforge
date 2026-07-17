#!/usr/bin/env python3
"""Generate Hermes self-improvement proposals from durable measurements."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from trustforge.improvement import diagnose  # noqa: E402
from trustforge.analysis_flow import AnalysisFlow  # noqa: E402
from trustforge.scheduler_log import get_recent_scheduler_runs  # noqa: E402
from trustforge.schema import COIN_POOL  # noqa: E402
from trustforge.upgrade_queue import UpgradeQueue  # noqa: E402


def _read_json(path: Path | None) -> dict | None:
    if path is None or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose Hermes deficits; emits proposals only")
    parser.add_argument("--question-bank", type=Path)
    parser.add_argument("--replay", type=Path)
    parser.add_argument(
        "--historical-coverage", type=Path,
        default=REPO / "out" / "historical-coverage-latest.json",
    )
    parser.add_argument("--recent-runs", type=int, default=30)
    parser.add_argument(
        "--connector-reliability", type=Path,
        default=REPO / "out" / "connector-reliability.json",
    )
    parser.add_argument("--out", type=Path, default=REPO / "out" / "hermes-improvement-latest.json")
    args = parser.parse_args(argv)
    if args.recent_runs < 1:
        parser.error("--recent-runs must be >= 1")
    # Autonomous cycles consume their latest durable measurements by default;
    # callers may still provide explicit immutable artifacts for review.
    question_bank = args.question_bank or (REPO / "out" / "question-bank-latest.json")
    if args.replay:
        replay = _read_json(args.replay)
    else:
        reports = [_read_json(REPO / "out" / f"historical-replay-{coin.lower()}.json") for coin in COIN_POOL]
        reports = [report for report in reports if report is not None]
        replay = {"available_snapshot_count": sum(int(report.get("available_snapshot_count", 0)) for report in reports), "horizons": {}} if reports else None
    report = diagnose(
        scheduler_runs=get_recent_scheduler_runs(args.recent_runs),
        connector_reliability=_read_json(args.connector_reliability),
        question_bank=_read_json(question_bank), replay=replay,
        historical_coverage=_read_json(args.historical_coverage),
        analysis_history=AnalysisFlow().improvement_history(),
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    UpgradeQueue().sync_diagnostic(report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
