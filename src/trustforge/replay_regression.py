"""Regression gate projection for historical replay diagnostics."""

from __future__ import annotations

from typing import Any


DEFAULT_THRESHOLDS = {
    "min_eligible_predictions": 30,
    "max_brier_score_proxy": 0.28,
    "min_hit_rate": 0.5,
}


def evaluate_replay_regression_gate(
    replay_report: dict[str, Any],
    *,
    thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Evaluate replay diagnostics against stable delivery-plane thresholds.

    The gate is intentionally observational. It never changes model behavior;
    it gives the admin/API layer a compact pass/fail status for #271.
    """
    limits = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    horizons = replay_report.get("horizons") or {}
    checks: list[dict[str, Any]] = []

    for horizon, metrics in sorted(horizons.items()):
        eligible = int(metrics.get("eligible_predictions") or 0)
        hit_rate = metrics.get("hit_rate")
        brier = metrics.get("brier_score_proxy")
        failures: list[str] = []
        if eligible < limits["min_eligible_predictions"]:
            failures.append("insufficient_eligible_predictions")
        if hit_rate is None or float(hit_rate) < limits["min_hit_rate"]:
            failures.append("hit_rate_below_gate")
        if brier is None or float(brier) > limits["max_brier_score_proxy"]:
            failures.append("brier_score_above_gate")

        checks.append(
            {
                "horizon": horizon,
                "eligible_predictions": eligible,
                "hit_rate": hit_rate,
                "brier_score_proxy": brier,
                "status": "pass" if not failures else "fail",
                "failures": failures,
            }
        )

    passed = bool(checks) and all(check["status"] == "pass" for check in checks)
    return {
        "kind": "historical_replay_regression_gate",
        "status": "pass" if passed else "fail",
        "coin": replay_report.get("coin"),
        "thresholds": limits,
        "checks": checks,
        "automatic_apply": False,
        "requires_human_review": not passed,
    }
