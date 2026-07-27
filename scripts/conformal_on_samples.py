#!/usr/bin/env python3
"""Chronological, research-only conformal evaluation for sample-contract JSONL.

This command is deliberately fail-closed.  It never shuffles time-series rows
and it never promotes or writes production configuration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

ALPHA = 0.10
MIN_UNIQUE_DATES = 4
REQUIRED = {
    "sample_id", "coin", "as_of", "source_family", "claim_direction",
    "evidence_strength", "outcome_direction", "outcome_observed_at",
}
VALID_DIRECTIONS = {"bullish", "bearish", "neutral"}


class DatasetError(ValueError):
    """Input cannot support an honest chronological evaluation."""


@dataclass(frozen=True)
class Split:
    calibration: list[dict[str, Any]]
    held_out: list[dict[str, Any]]
    calibration_start: str
    calibration_end: str
    held_out_start: str
    held_out_end: str


@dataclass(frozen=True)
class ConformalResult:
    tau: float
    calibration_samples: int
    held_out_samples: int
    held_out_abstain: int
    held_out_pass: int
    held_out_wrong: int
    joint_error: float
    abstain_rate: float
    conditional_wrong: float | None
    accuracy: float | None
    source_families: int


def _iso(value: Any, field: str, line_no: int) -> datetime:
    if not isinstance(value, str):
        raise DatasetError(f"line {line_no}: {field} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DatasetError(f"line {line_no}: invalid {field}: {value!r}") from exc
    if parsed.tzinfo is None:
        raise DatasetError(f"line {line_no}: {field} must include timezone")
    return parsed


def load_samples(path: str) -> list[dict]:
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise DatasetError(f"cannot read samples: {exc}") from exc
    samples: list[dict[str, Any]] = []
    for line_no, raw_line in enumerate(raw.splitlines(), 1):
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DatasetError(f"line {line_no}: malformed JSON") from exc
        missing = REQUIRED.difference(row) if isinstance(row, dict) else REQUIRED
        if not isinstance(row, dict) or missing:
            absent = sorted(missing)
            raise DatasetError(f"line {line_no}: missing fields: {absent}")
        as_of = _iso(row["as_of"], "as_of", line_no)
        observed = _iso(row["outcome_observed_at"], "outcome_observed_at", line_no)
        if observed <= as_of:
            raise DatasetError(f"line {line_no}: outcome must be strictly after as_of")
        if row["claim_direction"] not in VALID_DIRECTIONS or row["outcome_direction"] not in VALID_DIRECTIONS:
            raise DatasetError(f"line {line_no}: invalid direction")
        strength = row["evidence_strength"]
        if isinstance(strength, bool) or not isinstance(strength, (int, float)) or not math.isfinite(strength):
            raise DatasetError(f"line {line_no}: evidence_strength must be finite")
        if not 0.0 <= float(strength) <= 1.0:
            raise DatasetError(f"line {line_no}: evidence_strength outside [0,1]")
        row["_date"] = as_of.date().isoformat()
        samples.append(row)
    if not samples:
        raise DatasetError("dataset is empty")
    return samples


def chronological_split(samples: list[dict[str, Any]]) -> Split:
    dates = sorted({str(row["_date"]) for row in samples})
    if len(dates) < MIN_UNIQUE_DATES:
        raise DatasetError(
            f"need at least {MIN_UNIQUE_DATES} unique dates; got {len(dates)}"
        )
    boundary = len(dates) // 2
    calibration_dates = set(dates[:boundary])
    held_dates = set(dates[boundary:])
    calibration = [row for row in samples if row["_date"] in calibration_dates]
    held = [row for row in samples if row["_date"] in held_dates]
    if not calibration or not held or dates[boundary - 1] >= dates[boundary]:
        raise DatasetError("cannot form strictly ordered non-empty partitions")
    return Split(
        calibration, held, dates[0], dates[boundary - 1], dates[boundary], dates[-1]
    )


def conformal_threshold(
    strengths: list[float], correct_flags: list[int], alpha: float = ALPHA
) -> float:
    if not 0.0 < alpha < 1.0:
        raise DatasetError("alpha must be strictly between 0 and 1")
    wrong = sorted(1.0 - strengths[i] for i, flag in enumerate(correct_flags) if flag == 0)
    if not wrong:
        return math.inf
    index = math.ceil((1 - alpha) * (len(wrong) + 1)) - 1
    if index >= len(wrong):
        return math.inf
    return max(0.0, min(1.0, 1.0 - wrong[index]))


def run_conformal(
    samples: list[dict],
    random_seed: int = 42,
    alpha: float = ALPHA,
) -> ConformalResult:
    """Evaluate a chronological split; ``random_seed`` is retained but unused."""
    del random_seed
    return _evaluate_split(chronological_split(samples), alpha)


def _evaluate_split(split: Split, alpha: float) -> ConformalResult:
    flags = [
        int(row["claim_direction"] == row["outcome_direction"])
        for row in split.calibration
    ]
    tau = conformal_threshold(
        [float(row["evidence_strength"]) for row in split.calibration], flags, alpha
    )
    passed = [row for row in split.held_out if float(row["evidence_strength"]) > tau]
    wrong = sum(row["claim_direction"] != row["outcome_direction"] for row in passed)
    held_n = len(split.held_out)
    pass_n = len(passed)
    return ConformalResult(
        tau=tau,
        calibration_samples=len(split.calibration),
        held_out_samples=held_n,
        held_out_abstain=held_n - pass_n,
        held_out_pass=pass_n,
        held_out_wrong=wrong,
        joint_error=wrong / held_n,
        abstain_rate=(held_n - pass_n) / held_n,
        conditional_wrong=wrong / pass_n if pass_n else None,
        accuracy=(pass_n - wrong) / pass_n if pass_n else None,
        source_families=len(_counts(split.calibration + split.held_out, "source_family")),
    )


def _counts(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    return dict(sorted(Counter(str(row[field]) for row in rows).items()))


def build_report(
    samples: list[dict[str, Any]], digest: str, split: Split, alpha: float
) -> dict[str, Any]:
    result = _evaluate_split(split, alpha)
    metrics = asdict(result)
    metrics["tau"] = result.tau if math.isfinite(result.tau) else "Infinity"
    checks = {
        "joint_error": result.joint_error <= alpha,
        "abstain_rate": result.abstain_rate <= 0.60,
        "conditional_wrong": (
            result.conditional_wrong is not None and result.conditional_wrong <= 0.55
        ),
        "held_out_support": result.held_out_pass >= 100,
        "source_families": len(_counts(samples, "source_family")) >= 2,
    }
    return {
        "status": "research-only",
        "production_wiring_allowed": False,
        "alpha": alpha,
        "input_sha256": digest,
        "total_samples": len(samples),
        "unique_dates": len({row["_date"] for row in samples}),
        "split": {
            "strategy": "global-unique-date-chronological-half",
            "calibration_start": split.calibration_start,
            "calibration_end": split.calibration_end,
            "held_out_start": split.held_out_start,
            "held_out_end": split.held_out_end,
            "calibration_strictly_before_held_out": split.calibration_end < split.held_out_start,
        },
        "counts": {
            "per_family": _counts(samples, "source_family"),
            "per_coin": _counts(samples, "coin"),
            "calibration_per_family": _counts(split.calibration, "source_family"),
            "held_out_per_family": _counts(split.held_out, "source_family"),
            "calibration_per_coin": _counts(split.calibration, "coin"),
            "held_out_per_coin": _counts(split.held_out, "coin"),
        },
        "metrics": metrics,
        "promotion_checks": {**checks, "all_pass": all(checks.values())},
        "decision": "research-only; production promotion requires a separate approved change",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", required=True, type=Path)
    parser.add_argument("--out", default=Path("out/conformal/conformal_report.json"), type=Path)
    parser.add_argument("--alpha", type=float, default=ALPHA)
    args = parser.parse_args(argv)
    try:
        samples = load_samples(str(args.samples))
        digest = hashlib.sha256(args.samples.read_bytes()).hexdigest()
        split = chronological_split(samples)
        report = build_report(samples, digest, split, args.alpha)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    except DatasetError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
