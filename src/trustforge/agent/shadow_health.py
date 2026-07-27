"""Read-only health and operator-readiness reporting for shadow evidence.

This module has no activation, promotion, or cutover operation.  A report can
only recommend continued observation, a stop, or manual operator review.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from .shadow_contracts import (
    ShadowDecisionAction,
    load_policy,
    to_dict,
)
from .shadow_evidence_store import (
    ShadowEvidenceStore,
    ShadowEvidenceStoreError,
)
from .shadow_identity import measured_release_identity

EXIT_ELIGIBLE_FOR_REVIEW = 0
EXIT_CONTINUE_OBSERVATION = 2
EXIT_STOP = 3


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _failed_report(now: str, reason: str) -> dict[str, Any]:
    return {
        "report_version": "trustforge.shadow-health/v1",
        "evaluated_at": now,
        "read_only": True,
        "automatic_activation": False,
        "requires_manual_review": True,
        "action": ShadowDecisionAction.STOP.value,
        "blockers": [reason],
        "identity": None,
        "evidence": {
            "observation_root_digest": None,
            "aggregate_event_id": None,
            "decision_event_id": None,
            "ordered_observation_event_ids": [],
        },
        "checks": {
            "schema": False,
            "policy": False,
            "release_manifest": False,
            "runtime_attestation": False,
            "completion_evidence": False,
        },
        "metrics": {
            "observations": 0,
            "coins": 0,
            "question_types": 0,
            "minimum_per_cell": 0,
            "scenario_cells": {},
            "parity_pass_rate": 0.0,
            "confidence_delta_max": None,
            "trust_delta_max": None,
            "supporting_jaccard_min": None,
            "latency_p95_ms": None,
            "latency_max_ms": None,
            "terminal_failure_streak": 0,
            "provider_calls": None,
            "cost_usd": None,
        },
        "runtime_flags": {
            "runtime_enabled": _enabled("TRUSTFORGE_SHADOW_RUNTIME_ENABLED"),
            "observe_enabled": _enabled("KERNEL_SHADOW_OBSERVE"),
        },
    }


def build_shadow_health_report(*, now: str | None = None) -> dict[str, Any]:
    """Build a deterministic, fail-closed report from exact durable evidence."""
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
        cells: dict[str, int] = {}
        for observation in observations:
            key = (
                f"{observation.canonical_input.coin}/"
                f"{observation.canonical_input.question_type}"
            )
            cells[key] = cells.get(key, 0) + 1
        aggregate = result.decision.aggregate
        completion_ok = all(item.status != "corrupt" for item in observations)
        return {
            "report_version": "trustforge.shadow-health/v1",
            "evaluated_at": evaluated_at,
            "read_only": True,
            "automatic_activation": False,
            "requires_manual_review": True,
            "action": result.decision.action.value,
            "blockers": [item.value for item in aggregate.blockers],
            "identity": to_dict(measured.identity),
            "evidence": {
                "observation_root_digest": result.observation_root_digest,
                "aggregate_event_id": result.aggregate_event_id,
                "decision_event_id": result.decision_event_id,
                "ordered_observation_event_ids": list(
                    result.ordered_observation_event_ids
                ),
                "ids_are_deterministically_derived": True,
            },
            "checks": {
                "schema": True,
                "policy": True,
                "release_manifest": True,
                "runtime_attestation": True,
                "completion_evidence": completion_ok,
            },
            "metrics": {
                "observations": aggregate.observation_count,
                "coins": aggregate.coin_count,
                "question_types": aggregate.question_type_count,
                "minimum_per_cell": aggregate.minimum_cell_count,
                "scenario_cells": dict(sorted(cells.items())),
                "parity_pass_rate": aggregate.parity_rate,
                "confidence_delta_max": max(
                    (item.confidence_delta for item in observations),
                    default=None,
                ),
                "trust_delta_max": max(
                    (item.trust_delta for item in observations), default=None,
                ),
                "supporting_jaccard_min": min(
                    (item.supporting_jaccard for item in observations),
                    default=None,
                ),
                "latency_p95_ms": aggregate.latency_p95_ms,
                "latency_max_ms": max(
                    (item.elapsed_ms for item in observations), default=None,
                ),
                "terminal_failure_streak": aggregate.terminal_failure_streak,
                "provider_calls": sum(
                    item.provider_calls for item in observations
                ),
                "cost_usd": sum(item.cost_usd for item in observations),
            },
            "runtime_flags": {
                "runtime_enabled": _enabled(
                    "TRUSTFORGE_SHADOW_RUNTIME_ENABLED"
                ),
                "observe_enabled": _enabled("KERNEL_SHADOW_OBSERVE"),
            },
        }
    except Exception as exc:
        # Do not expose paths, database details, or attacker-controlled payloads
        # in operator output.  The exception class is sufficient for triage.
        reason = (
            "evidence_store_unavailable"
            if isinstance(exc, ShadowEvidenceStoreError)
            else "identity_policy_or_attestation_invalid"
        )
        return _failed_report(evaluated_at, reason)
    finally:
        if store is not None:
            store.close()


def shadow_health_exit_code(report: dict[str, Any]) -> int:
    action = report.get("action")
    if action == ShadowDecisionAction.ELIGIBLE_FOR_OPERATOR_REVIEW.value:
        return EXIT_ELIGIBLE_FOR_REVIEW
    if action == ShadowDecisionAction.CONTINUE_OBSERVATION.value:
        return EXIT_CONTINUE_OBSERVATION
    return EXIT_STOP
