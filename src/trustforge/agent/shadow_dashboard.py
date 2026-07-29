"""Read-only aggregate dashboard for shadow observation health (Issue #871).

This module is strictly observational: it never activates, promotes, or cuts
over a release, and it never mutates official confidence, calibrated
confidence, decision state, direction, or market judgment.  A report is a
deterministic aggregate derived from the append-only SQLite ledger.
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Any, Sequence

from .shadow_contracts import load_policy, to_dict
from .shadow_evidence_store import (
    ShadowEvidenceStore,
    ShadowEvidenceStoreError,
)
from .shadow_identity import measured_release_identity

_DASHBOARD_VERSION = "trustforge.shadow-dashboard/v1"
_STALE_WINDOW_FRACTION = 0.75
_MAX_DELTA_SAMPLES = 500


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _runtime_flags() -> dict[str, bool]:
    return {
        "runtime_enabled": _enabled("TRUSTFORGE_SHADOW_RUNTIME_ENABLED"),
        "observe_enabled": _enabled("KERNEL_SHADOW_OBSERVE"),
        "intrinsic_enabled": _enabled("TRUSTFORGE_SHADOW_INTRINSIC_ENABLED"),
    }


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp lacks timezone")
    return parsed.astimezone(timezone.utc)


def _percentile(sorted_values: Sequence[float], fraction: float) -> float | None:
    if not sorted_values:
        return None
    index = max(0, math.ceil(fraction * len(sorted_values)) - 1)
    return sorted_values[index]


def _distribution(values: Sequence[float]) -> dict[str, Any]:
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "min": ordered[0] if ordered else None,
        "max": ordered[-1] if ordered else None,
        "p50": _percentile(ordered, 0.50),
        "p95": _percentile(ordered, 0.95),
        "samples": list(ordered[:_MAX_DELTA_SAMPLES]),
    }


def _failed_report(now: str, reason: str) -> dict[str, Any]:
    empty = _distribution([])
    return {
        "report_version": _DASHBOARD_VERSION,
        "evaluated_at": now,
        "read_only": True,
        "enabled": False,
        "reason": reason,
        "runtime_flags": _runtime_flags(),
        "coverage": {
            "observation_count": 0,
            "coin_count": 0,
            "question_type_count": 0,
            "minimum_per_cell": 0,
            "scenario_cells": {},
        },
        "missing": {
            "observations_without_intrinsic_shadow": 0,
            "fraction": 0.0,
        },
        "stale": {
            "max_age_hours": None,
            "window_hours": None,
            "approaching_expiry": 0,
        },
        "conflict": {
            "parity_failed": 0,
            "intrinsic_gate_failed": 0,
            "total": 0,
            "fraction": 0.0,
        },
        "deltas": {
            "trust_delta": empty,
            "confidence_delta": empty,
            "intrinsic_total_delta": empty,
            "intrinsic_trust_delta": empty,
        },
    }


def build_shadow_dashboard_report(*, now: str | None = None) -> dict[str, Any]:
    """Build a deterministic, fail-closed aggregate dashboard from durable evidence."""
    evaluated_at = now or _utc_now()
    store: ShadowEvidenceStore | None = None
    try:
        policy = load_policy()
        measured = measured_release_identity(policy)
        store = ShadowEvidenceStore(read_only=True)
        result = store.read_only_evaluate(
            measured.identity, policy, now=evaluated_at,
        )
        observations = result.observations
        boundary = _parse_timestamp(evaluated_at)
        window_seconds = policy.window_hours * 3600

        cells: dict[str, int] = {}
        trust_deltas: list[float] = []
        confidence_deltas: list[float] = []
        intrinsic_total_deltas: list[float] = []
        intrinsic_trust_deltas: list[float] = []
        ages_hours: list[float] = []
        missing_intrinsic = 0
        parity_failed = 0
        intrinsic_gate_failed = 0
        stale_approaching = 0

        for observation in observations:
            cell_key = (
                f"{observation.canonical_input.coin}/"
                f"{observation.canonical_input.question_type}"
            )
            cells[cell_key] = cells.get(cell_key, 0) + 1

            trust_deltas.append(observation.trust_delta)
            confidence_deltas.append(observation.confidence_delta)

            age_seconds = (
                boundary - _parse_timestamp(observation.observed_at)
            ).total_seconds()
            if age_seconds >= 0:
                ages_hours.append(age_seconds / 3600.0)
            if age_seconds >= _STALE_WINDOW_FRACTION * window_seconds:
                stale_approaching += 1

            intrinsic = observation.intrinsic_shadow
            if not isinstance(intrinsic, dict):
                missing_intrinsic += 1
            else:
                total_delta = intrinsic.get("total_delta")
                if isinstance(total_delta, (int, float)) and math.isfinite(total_delta):
                    intrinsic_total_deltas.append(float(total_delta))
                intrinsic_trust = intrinsic.get("trust_delta")
                if isinstance(intrinsic_trust, (int, float)) and math.isfinite(intrinsic_trust):
                    intrinsic_trust_deltas.append(float(intrinsic_trust))
                gate = intrinsic.get("gate")
                if isinstance(gate, dict) and gate.get("passed") is False:
                    intrinsic_gate_failed += 1

            if not observation.parity_passed:
                parity_failed += 1

        total = len(observations)
        minimum_cell = min(cells.values(), default=0)
        conflict_total = len({*([o for o in observations if not o.parity_passed])})

        return {
            "report_version": _DASHBOARD_VERSION,
            "evaluated_at": evaluated_at,
            "read_only": True,
            "enabled": True,
            "identity": to_dict(measured.identity),
            "runtime_flags": _runtime_flags(),
            "coverage": {
                "observation_count": total,
                "coin_count": result.decision.aggregate.coin_count,
                "question_type_count": result.decision.aggregate.question_type_count,
                "minimum_per_cell": minimum_cell,
                "scenario_cells": dict(sorted(cells.items())),
            },
            "missing": {
                "observations_without_intrinsic_shadow": missing_intrinsic,
                "fraction": (missing_intrinsic / total) if total else 0.0,
            },
            "stale": {
                "max_age_hours": max(ages_hours, default=None),
                "window_hours": policy.window_hours,
                "approaching_expiry": stale_approaching,
            },
            "conflict": {
                "parity_failed": parity_failed,
                "intrinsic_gate_failed": intrinsic_gate_failed,
                "total": parity_failed + intrinsic_gate_failed,
                "fraction": (conflict_total / total) if total else 0.0,
            },
            "deltas": {
                "trust_delta": _distribution(trust_deltas),
                "confidence_delta": _distribution(confidence_deltas),
                "intrinsic_total_delta": _distribution(intrinsic_total_deltas),
                "intrinsic_trust_delta": _distribution(intrinsic_trust_deltas),
            },
        }
    except Exception as exc:
        reason = (
            "evidence_store_unavailable"
            if isinstance(exc, ShadowEvidenceStoreError)
            else "identity_policy_or_attestation_invalid"
        )
        return _failed_report(evaluated_at, reason)
    finally:
        if store is not None:
            store.close()
