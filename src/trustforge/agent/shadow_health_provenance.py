"""Independent provenance verification for exported shadow health reports.

The export is only a claim.  This verifier re-evaluates the exact release
identity and policy from the append-only SQLite ledger before accepting it.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from trustforge.safe_fs import read_regular_file

from .shadow_contracts import (
    ShadowPolicy,
    ShadowReleaseIdentity,
    canonical_json,
    policy_digest,
    to_dict,
)
from .shadow_evidence_store import ShadowEvidenceStore


class ShadowHealthProvenanceError(RuntimeError):
    """The protected export cannot be proven from durable shadow evidence."""


@dataclass(frozen=True, slots=True)
class VerifiedShadowHealthProvenance:
    report_digest: str
    evaluated_at: str
    identity: ShadowReleaseIdentity
    observation_root_digest: str
    aggregate_event_id: str
    decision_event_id: str
    ordered_observation_event_ids: tuple[str, ...]
    checks: dict[str, bool]
    metrics: dict[str, Any]


def _utc(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ShadowHealthProvenanceError(
            "shadow health evaluated_at is invalid"
        ) from exc
    if parsed.tzinfo is None:
        raise ShadowHealthProvenanceError(
            "shadow health evaluated_at must be timezone aware"
        )
    return parsed.astimezone(timezone.utc)


def _canonical_metrics(result: Any) -> dict[str, Any]:
    observations = result.observations
    aggregate = result.decision.aggregate
    cells: dict[str, int] = {}
    for observation in observations:
        key = (
            f"{observation.canonical_input.coin}/"
            f"{observation.canonical_input.question_type}"
        )
        cells[key] = cells.get(key, 0) + 1
    return {
        "observations": aggregate.observation_count,
        "coins": aggregate.coin_count,
        "question_types": aggregate.question_type_count,
        "minimum_per_cell": aggregate.minimum_cell_count,
        "scenario_cells": dict(sorted(cells.items())),
        "parity_pass_rate": aggregate.parity_rate,
        "confidence_delta_max": max(
            (item.confidence_delta for item in observations), default=None
        ),
        "trust_delta_max": max(
            (item.trust_delta for item in observations), default=None
        ),
        "supporting_jaccard_min": min(
            (item.supporting_jaccard for item in observations), default=None
        ),
        "latency_p95_ms": aggregate.latency_p95_ms,
        "latency_max_ms": max(
            (item.elapsed_ms for item in observations), default=None
        ),
        "terminal_failure_streak": aggregate.terminal_failure_streak,
        "provider_calls": sum(item.provider_calls for item in observations),
        "cost_usd": sum(item.cost_usd for item in observations),
    }


def _equal(actual: Any, expected: Any, name: str) -> None:
    try:
        matches = canonical_json(actual) == canonical_json(expected)
    except (TypeError, ValueError) as exc:
        raise ShadowHealthProvenanceError(
            f"shadow health {name} is not canonical JSON"
        ) from exc
    if not matches:
        raise ShadowHealthProvenanceError(
            f"shadow health {name} does not match durable evidence"
        )


def verify_shadow_health_provenance(
    export_path: str | Path,
    store_path: str | Path,
    *,
    identity: ShadowReleaseIdentity,
    policy: ShadowPolicy,
    now: datetime,
    maximum_age: timedelta = timedelta(minutes=10),
) -> VerifiedShadowHealthProvenance:
    """Verify a protected health export against an independent store snapshot."""
    if identity.policy_digest != policy_digest(policy):
        raise ShadowHealthProvenanceError(
            "release identity and policy digest differ"
        )
    try:
        raw, info = read_regular_file(Path(export_path), maximum_bytes=1_000_000)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise ShadowHealthProvenanceError(
                "shadow health export is not owner-protected"
            )
        report = json.loads(raw)
    except ShadowHealthProvenanceError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ShadowHealthProvenanceError(
            "shadow health export is unavailable or invalid"
        ) from exc
    if not isinstance(report, dict):
        raise ShadowHealthProvenanceError("shadow health export must be an object")

    evaluated_at_text = report.get("evaluated_at")
    evaluated_at = _utc(evaluated_at_text)
    age = now.astimezone(timezone.utc) - evaluated_at
    if age < timedelta(0) or age > maximum_age:
        raise ShadowHealthProvenanceError(
            "shadow health export is stale or future-dated"
        )

    store: ShadowEvidenceStore | None = None
    try:
        store = ShadowEvidenceStore(store_path, read_only=True)
        result = store.read_only_evaluate(
            identity, policy, now=evaluated_at_text
        )
    except ShadowHealthProvenanceError:
        raise
    except Exception as exc:
        raise ShadowHealthProvenanceError(
            "durable shadow evidence cannot be evaluated"
        ) from exc
    finally:
        if store is not None:
            store.close()

    observations = result.observations
    expected_checks = {
        "schema": True,
        "policy": True,
        "release_manifest": True,
        "runtime_attestation": True,
        "completion_evidence": all(
            item.status != "corrupt" for item in observations
        ),
    }
    expected_evidence = {
        "observation_root_digest": result.observation_root_digest,
        "aggregate_event_id": result.aggregate_event_id,
        "decision_event_id": result.decision_event_id,
        "ordered_observation_event_ids": list(
            result.ordered_observation_event_ids
        ),
        "ids_are_deterministically_derived": True,
    }
    expected_metrics = _canonical_metrics(result)
    expected_header = {
        "report_version": "trustforge.shadow-health/v1",
        "evaluated_at": evaluated_at_text,
        "read_only": True,
        "automatic_activation": False,
        "requires_manual_review": True,
        "action": result.decision.action.value,
        "blockers": [
            blocker.value for blocker in result.decision.aggregate.blockers
        ],
        "identity": to_dict(identity),
    }
    _equal(
        {name: report.get(name) for name in expected_header},
        expected_header,
        "identity and decision",
    )
    _equal(report.get("evidence"), expected_evidence, "evidence identifiers")
    _equal(report.get("checks"), expected_checks, "checks")
    _equal(report.get("metrics"), expected_metrics, "metrics and cost")

    return VerifiedShadowHealthProvenance(
        report_digest="sha256:" + hashlib.sha256(
            canonical_json(report)
        ).hexdigest(),
        evaluated_at=evaluated_at_text,
        identity=identity,
        observation_root_digest=result.observation_root_digest,
        aggregate_event_id=result.aggregate_event_id,
        decision_event_id=result.decision_event_id,
        ordered_observation_event_ids=result.ordered_observation_event_ids,
        checks=expected_checks,
        metrics=expected_metrics,
    )
