#!/usr/bin/env python3
"""Offline source-reliability trainer for TrustForge.

Reads historical_sample JSONL, computes per-source reliability with
small-sample shrinkage, and produces a versioned artifact.

Usage:
    .venv/bin/python scripts/train_source_reliability.py \
        --samples out/samples/historical_samples.jsonl \
        --out data/model-artifacts/source_reputation_v1.json

Milestone 3 for #195: dynamic reputation calibration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

MIN_SAMPLE_PER_SOURCE = 30  # 最低樣本數，不足不收斂
SHRINKAGE_TARGET = 0.50  # 小樣本朝 0.50 shrink
ALPHA = 0.05  # 95% confidence interval


@dataclass
class SourceStats:
    name: str
    sample_count: int
    correct: int
    accuracy: float
    brier: float
    auc_proxy: float  # max(accuracy, 1 - accuracy)，無完整 ROC 時的代理
    ci_low: float
    ci_high: float
    shrinkage_weight: float  # 小樣本 shrinkage 程度


def wilson_interval(k: int, n: int, alpha: float = ALPHA) -> tuple[float, float]:
    """Wilson score interval for binomial proportion.
    More robust than Wald for small n or extreme p."""
    if n == 0:
        return 0.0, 0.0
    p = k / n
    z = 1.96  # normal approx for alpha=0.05
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half_width = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half_width), min(1.0, centre + half_width)


def compute_source_stats(
    name: str, samples: list[dict]
) -> Optional[SourceStats]:
    """Compute reliability stats for a single source."""
    n = len(samples)
    if n < MIN_SAMPLE_PER_SOURCE:
        return None  # Too few samples to be trustworthy

    # Binary correct: claim_direction == outcome_direction
    correct = sum(
        1
        for s in samples
        if s["claim_direction"] == s["outcome_direction"]
    )
    accuracy = correct / n

    # Brier score: (1/N) * Σ( (strength_i - correct_i)^2 )
    # where correct_i = 1 if claim_direction == outcome_direction else 0
    brier = sum(
        (s["evidence_strength"] - (
            1.0 if s["claim_direction"] == s["outcome_direction"] else 0.0
        )) ** 2
        for s in samples
    ) / n

    # AUC proxy: max(accuracy, 1 - accuracy) — proxy when we only have
    # binary correctness labels (not ROC scores)
    auc_proxy = max(accuracy, 1.0 - accuracy)

    # Wilson CI
    ci_low, ci_high = wilson_interval(correct, n)

    # Small-sample shrinkage: weight toward SHRINKAGE_TARGET
    # shrinkage_weight = n / (n + k) where k is an effective sample size
    # We use k=100: at n=30, weight=0.23; at n=1000, weight=0.91
    shrinkage_k = 100.0
    shrinkage_weight = n / (n + shrinkage_k)
    shrinked = accuracy * shrinkage_weight + SHRINKAGE_TARGET * (1 - shrinkage_weight)

    return SourceStats(
        name=name,
        sample_count=n,
        correct=correct,
        accuracy=shrinked,
        brier=brier,
        auc_proxy=auc_proxy,
        ci_low=ci_low,
        ci_high=ci_high,
        shrinkage_weight=shrinkage_weight,
    )


def load_samples(path: str) -> list[dict]:
    samples: list[dict] = []
    for line in Path(path).read_text(encoding="utf-8").strip().split("\n"):
        if line.strip():
            samples.append(json.loads(line))
    return samples


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", required=True)
    parser.add_argument("--out", default="data/model-artifacts/source_reputation_v1.json")
    parser.add_argument("--cutoff", default=None,
                        help="Training cutoff date (default: today)")
    args = parser.parse_args()

    samples = load_samples(args.samples)
    cutoff = args.cutoff or str(Path(args.samples).stat().st_mtime)[:10]

    # Group by source
    by_source: dict[str, list[dict]] = {}
    for s in samples:
        src = s["source"]
        by_source.setdefault(src, []).append(s)

    # Compute per-source stats
    sources: dict[str, dict] = {}
    baseline_correct = 0
    baseline_total = 0

    for name, src_samples in by_source.items():
        stats = compute_source_stats(name, src_samples)
        if stats is None:
            continue
        sources[name] = {
            "sample_count": stats.sample_count,
            "reliability": round(stats.accuracy, 4),
            "brier": round(stats.brier, 4),
            "auc_proxy": round(stats.auc_proxy, 4),
            "confidence_interval": [round(stats.ci_low, 4), round(stats.ci_high, 4)],
            "shrinkage_weight": round(stats.shrinkage_weight, 4),
        }
        baseline_correct += stats.correct
        baseline_total += stats.sample_count

    baseline_accuracy = baseline_correct / baseline_total if baseline_total > 0 else 0.0

    # Dataset hash
    dataset_hash = hashlib.sha256(Path(args.samples).read_bytes()).hexdigest()

    artifact = {
        "version": "source-reputation-v1",
        "training_cutoff": cutoff,
        "dataset_hash": dataset_hash,
        "min_sample_per_source": MIN_SAMPLE_PER_SOURCE,
        "shrinkage_target": SHRINKAGE_TARGET,
        "baseline_accuracy": round(baseline_accuracy, 4),
        "sources": sources,
        "warnings": [],
    }

    # Add warnings
    if len(sources) == 0:
        artifact["warnings"].append("No sources met minimum sample threshold")
    elif len(sources) == 1:
        artifact["warnings"].append("Only one source — shadow comparison impossible")
    
    for name, info in sources.items():
        if info["reliability"] < 0.5:
            artifact["warnings"].append(
                f"Source {name} reliability ({info['reliability']}) below 0.5"
            )
        if info["brier"] > 0.25:
            artifact["warnings"].append(
                f"Source {name} Brier score ({info['brier']}) above 0.25 baseline"
            )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n")

    print(json.dumps({
        "artifact": str(out_path),
        "sources": len(sources),
        "total_samples": len(samples),
        "warnings": len(artifact["warnings"]),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
