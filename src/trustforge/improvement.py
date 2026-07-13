"""Bounded self-improvement diagnostics for Hermes.

Hermes may inspect its own durable outcomes and propose experiments, but it may
not silently change production code, source weights, models, prompts, or formal
conclusions.  This keeps learning useful without turning an audit system into an
unreviewable self-modifying system.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable


@dataclass(frozen=True)
class ImprovementProposal:
    id: str
    area: str
    severity: str
    evidence: dict[str, Any]
    proposed_experiment: str
    success_metric: str
    approval_required: bool = True
    automatic_apply: bool = False


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def diagnose(
    *,
    scheduler_runs: Iterable[dict[str, Any]] = (),
    question_bank: dict[str, Any] | None = None,
    replay: dict[str, Any] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Turn observed deficits into reviewable experiments, never live edits."""
    proposals: list[ImprovementProposal] = []
    runs = list(scheduler_runs)
    failed_runs = [run for run in runs if _number(run.get("failure_count")) > 0]
    if failed_runs:
        failures = Counter(
            str(label) for run in failed_runs for label in (run.get("failures") or [])
        )
        proposals.append(ImprovementProposal(
            id="source-reliability-investigation", area="data-acquisition", severity="high",
            evidence={"failed_runs": len(failed_runs), "failure_labels": dict(sorted(failures.items()))},
            proposed_experiment="Reproduce the failing source in a sandbox; add a bounded retry, fallback, or freshness rule only after a regression test.",
            success_metric="Seven consecutive scheduled cycles with zero failures for the affected source.",
        ))

    if question_bank is not None:
        failed = int(_number(question_bank.get("failed")))
        if failed:
            gap_counts = Counter(
                str(gap) for result in question_bank.get("results", [])
                for gap in (result.get("gaps") or [])
            )
            proposals.append(ImprovementProposal(
                id="report-contract-regression", area="report-evidence-log", severity="high",
                evidence={"failed_cases": failed, "gap_counts": dict(sorted(gap_counts.items()))},
                proposed_experiment="Add a minimal regression fixture for the dominant gap, then fix only the responsible pipeline boundary.",
                success_metric="Question-bank failures return to zero without reducing required Evidence or log fields.",
            ))
        for source, latency in sorted((question_bank.get("source_latency_ms") or {}).items()):
            if _number(latency.get("samples")) >= 5 and _number(latency.get("p95")) > 2000:
                proposals.append(ImprovementProposal(
                    id=f"latency-{source}", area="execution-efficiency", severity="medium",
                    evidence={"source": source, "p95_ms": _number(latency.get("p95")), "samples": int(_number(latency.get("samples")))},
                    proposed_experiment="Profile this connector with a fixed fixture and live timeout budget; evaluate cache, batching, or interval changes in a sandbox.",
                    success_metric="p95 source latency below 2000ms without lowering source freshness or Evidence coverage.",
                ))

    if replay is not None:
        available = int(_number(replay.get("available_snapshot_count")))
        if available < 100:
            proposals.append(ImprovementProposal(
                id="calibration-data-accumulation", area="historical-calibration", severity="medium",
                evidence={"available_snapshot_count": available, "minimum_for_experiment": 100},
                proposed_experiment="Continue point-in-time source archiving; do not fit a confidence model yet.",
                success_metric="At least 100 eligible, leakage-safe directional outcomes across multiple market conditions.",
            ))
        for horizon, metrics in sorted((replay.get("horizons") or {}).items()):
            eligible = int(_number(metrics.get("eligible_predictions")))
            hit_rate = metrics.get("hit_rate")
            reliability = metrics.get("reliability") or []
            if eligible >= 100 and hit_rate is not None and any(
                abs(_number(bin_.get("mean_information_completeness")) - _number(bin_.get("empirical_hit_rate"))) >= 0.15
                for bin_ in reliability
            ):
                proposals.append(ImprovementProposal(
                    id=f"confidence-calibrator-{horizon.lower()}", area="historical-calibration", severity="medium",
                    evidence={"horizon": horizon, "eligible_predictions": eligible, "hit_rate": hit_rate},
                    proposed_experiment="Compare an explainable logistic-regression and isotonic calibrator on time-separated train/holdout periods.",
                    success_metric="Holdout calibration error improves while no future source or OHLCV data crosses the run boundary.",
                ))

    timestamp = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "agent": "hermes", "kind": "self_improvement_diagnostic", "generated_at": timestamp,
        "automation_boundary": "propose, test in sandbox, require human approval; never self-apply production changes",
        "inputs": {"scheduler_runs": len(runs), "question_bank": question_bank is not None, "replay": replay is not None},
        "proposals": [asdict(proposal) for proposal in proposals],
        "status": "attention_required" if proposals else "healthy_or_insufficient_evidence",
    }
