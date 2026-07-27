#!/usr/bin/env python3
"""Conformal prediction backtest on historical sample JSONL.

Reads the shared historical sample contract JSONL (Milestone 2),
runs split conformal prediction (single-stage, α=0.1),
and reports coverage, abstain rate, conditional error rate.

Unlike the original backtest_conformal.py (which derived 6 pseudo-independent
signals from a single OHLCV series), this script uses the real heterogeneous
evidence in the sample contract.

Milestone 4 for #197.

Usage:
    .venv/bin/python scripts/conformal_on_samples.py \
        --samples out/samples/historical_samples.jsonl \
        --out out/conformal/conformal_report.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


ALPHA = 0.10  # conformal coverage target


@dataclass
class ConformalResult:
    tau: float                       # conformal threshold
    calibration_samples: int         # used for calibration
    held_out_samples: int            # used for evaluation
    held_out_abstain: int            # evidence_strength ≤ tau
    held_out_pass: int               # evidence_strength > τ
    held_out_wrong: int              # wrong among pass
    joint_error: float               # P(wrong AND pass) / total
    abstain_rate: float              # abstain / held_out
    conditional_wrong: float         # wrong / pass (or NaN if pass=0)
    accuracy: float                  # overall accuracy of non-abstain claims
    source_families: int             # number of unique source_family values
    auc_proxy: float                 # accuracy-based AUC proxy


def load_samples(path: str) -> list[dict]:
    samples: list[dict] = []
    for line in Path(path).read_text(encoding="utf-8").strip().split("\n"):
        if line.strip():
            samples.append(json.loads(line))
    return samples


def conformal_threshold(
    strengths: list[float],  # evidence_strength for each calibration sample
    correct_flags: list[int],  # 1 if claim==outcome, 0 otherwise
    alpha: float = ALPHA,
) -> float:
    """Compute conformal threshold τ: 1 - quantile of (strength | wrong)."""
    n = len(strengths)
    if n == 0:
        return float("inf")

    # Use non-conformity: 1 - strength when wrong
    nonconformity = [
        1.0 - strengths[i]
        for i in range(n)
        if correct_flags[i] == 0  # wrong sample
    ]
    if not nonconformity:
        return 0.0  # No wrong samples → perfect calibration

    nonconformity.sort()
    # Split conformal: τ = 1 - (1 - α) quantile of nonconformity
    # With n_wrong calibration wrongs, the ⌈(1-α)(n_wrong+1)⌉-th nonconformity
    n_wrong = len(nonconformity)
    idx = min(math.ceil((1 - alpha) * (n_wrong + 1)) - 1, n_wrong - 1)
    target_nc = nonconformity[idx]
    tau = 1.0 - target_nc
    return max(0.0, min(1.0, tau))


def run_conformal(
    samples: list[dict],
    random_seed: int = 42,
    alpha: float = ALPHA,
) -> ConformalResult:
    """Run single-stage split conformal on historical samples."""
    import random
    random.seed(random_seed)
    random.shuffle(samples)

    total = len(samples)
    split = total // 2
    calib = samples[:split]
    held = samples[split:]

    # --- Calibration ---
    strengths_calib: list[float] = []
    correct_calib: list[int] = []
    for s in calib:
        correct = 1 if s["claim_direction"] == s["outcome_direction"] else 0
        strengths_calib.append(s["evidence_strength"])
        correct_calib.append(correct)

    tau = conformal_threshold(strengths_calib, correct_calib, alpha)

    # --- Evaluation ---
    held_wrong, held_abstain, held_pass = 0, 0, 0
    correct_pass = 0
    for s in held:
        correct = 1 if s["claim_direction"] == s["outcome_direction"] else 0
        if s["evidence_strength"] <= tau:
            held_abstain += 1
        else:
            held_pass += 1
            if correct == 0:
                held_wrong += 1
            else:
                correct_pass += 1

    joint_error = held_wrong / len(held) if len(held) > 0 else 0.0
    abstain_rate = held_abstain / len(held) if len(held) > 0 else 0.0
    conditional_wrong = held_wrong / held_pass if held_pass > 0 else float("nan")
    accuracy = correct_pass / held_pass if held_pass > 0 else 0.0

    # Count source families
    families = set(s.get("source_family", "unknown") for s in samples)

    # AUC proxy
    total_correct = sum(
        1
        for s in samples
        if s["claim_direction"] == s["outcome_direction"]
    )
    auc_proxy = max(total_correct / total, 1 - total_correct / total) if total > 0 else 0.5

    return ConformalResult(
        tau=tau,
        calibration_samples=len(calib),
        held_out_samples=len(held),
        held_out_abstain=held_abstain,
        held_out_pass=held_pass,
        held_out_wrong=held_wrong,
        joint_error=joint_error,
        abstain_rate=abstain_rate,
        conditional_wrong=conditional_wrong,
        accuracy=accuracy,
        source_families=len(families),
        auc_proxy=auc_proxy,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", required=True)
    parser.add_argument("--out", default="out/conformal/conformal_report.json")
    parser.add_argument("--alpha", type=float, default=ALPHA)
    args = parser.parse_args()

    samples = load_samples(args.samples)
    result = run_conformal(samples, alpha=args.alpha)

    # Build report
    report = {
        "alpha": args.alpha,
        "total_samples": len(samples),
        "source_families": result.source_families,
        "calibration_samples": result.calibration_samples,
        "held_out_samples": result.held_out_samples,
        "tau": round(result.tau, 4),
        "joint_error": round(result.joint_error, 4),
        "abstain_rate": round(result.abstain_rate, 4),
        "conditional_wrong": round(result.conditional_wrong, 4) if not math.isnan(result.conditional_wrong) else "NaN",
        "accuracy": round(result.accuracy, 4),
        "auc_proxy": round(result.auc_proxy, 4),
    }

    # Promotion check
    report["promotion_checks"] = _promotion_checks(result, args.alpha, samples)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    print(json.dumps(report, indent=2))
    return 0


def _promotion_checks(
    r: ConformalResult,
    alpha: float,
    samples: list[dict],
) -> dict:
    """Check promotion thresholds per Milestone 4 requirements."""
    checks: dict = {}
    
    # P1: Joint coverage
    checks["P1_joint_coverage"] = {
        "target": alpha,
        "actual": r.joint_error,
        "pass": r.joint_error <= alpha,
    }

    # P2: Abstain rate ≤ 0.60
    checks["P2_abstain_rate"] = {
        "target": 0.60,
        "actual": r.abstain_rate,
        "pass": r.abstain_rate <= 0.60,
    }

    # P3: Conditional wrong ≤ 0.55
    checks["P3_conditional_wrong"] = {
        "target": 0.55,
        "actual": r.conditional_wrong if not math.isnan(r.conditional_wrong) else 1.0,
        "pass": r.conditional_wrong <= 0.55 if not math.isnan(r.conditional_wrong) else False,
    }

    # P4: Held-out pass ≥ 100
    checks["P4_held_out_pass"] = {
        "target": 100,
        "actual": r.held_out_pass,
        "pass": r.held_out_pass >= 100,
    }

    # Extra: Source family check
    families = set(s.get("source_family", "unknown") for s in samples)
    checks["source_families"] = {
        "required": 2,
        "actual": len(families),
        "pass": len(families) >= 2,
        "families": sorted(families),
    }

    # Extra: AUC proxy > 0.5
    checks["auc_proxy"] = {
        "target": 0.5,
        "actual": r.auc_proxy,
        "pass": r.auc_proxy > 0.5,
    }

    # Overall
    checks["all_pass"] = all(
        v.get("pass", False)
        for k, v in checks.items()
        if k != "all_pass"
    )

    return checks


if __name__ == "__main__":
    raise SystemExit(main())
