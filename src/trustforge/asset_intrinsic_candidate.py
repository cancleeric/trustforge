"""Canonical scorer candidate (shadow-only) for asset-intrinsic facts (#876).

This module is **observational only**.  It never feeds back into the active
``KernelOutput`` or the projected report.  When the feature flag
``TRUSTFORGE_SHADOW_INTRINSIC_CANDIDATE_ENABLED`` is OFF (the default) the
shadow runtime never imports or calls this module, so kernel output and report
remain byte-for-byte identical to the baseline.

Design constraints
------------------
* Lives in the ``trustforge`` (app) package; reads ``trustforge_core`` only.
* Does **not** touch direction (``CandidateShadow`` has no direction field).
* Does **not** re-implement calibration: it reuses the existing
  ``evidence_strength`` + ``interpolate_calibration`` chain from
  ``trustforge_core.scoring`` with the canonical ``DEFAULT_CALIBRATION_TABLE``.
* Fail-closed: any exception collapses to a zero-delta ``CandidateShadow`` so
  the official pipeline is never disturbed.

Approximation note
------------------
``candidate_calibrated`` is reconstructed from the report-facing (capped)
``KernelOutput.supporting`` / ``KernelOutput.contrarian`` tuples.  When a run
has more than ``SUPPORTING_LIMIT`` (10) supporting or ``CONTRARIAN_LIMIT`` (5)
contrarian claims, the capped tuples differ from the kernel's internal uncapped
tuples, so ``candidate_calibrated`` may diverge slightly from the value the
kernel would have produced.  This is an accepted property of a shadow-only
candidate: ``baseline_calibrated`` is always the exact kernel value, and the
divergence is observable, not hidden.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass

from trustforge_core.contracts import KernelOutput
from trustforge_core.scoring import (
    DEFAULT_CALIBRATION_TABLE,
    evidence_strength,
    interpolate_calibration,
)

from trustforge.asset_intrinsic_shadow import assess_intrinsic_shadow

CANDIDATE_SCHEMA_VERSION = "1.0.0"
_FACTS_DIGEST_DOMAIN = b"trustforge.intrinsic.candidate.v1\x00"

# Mirrors the canonical decision-state thresholds in
# trustforge_core.scoring.aggregate_scored_claims so the candidate classifies
# runs exactly as the kernel would.  Kept private; the kernel is the source of
# truth for the baseline state.
_ABSTAIN_CALIBRATED_CEILING = 0.35
_LOW_CONFIDENCE_CEILING = 0.5
_INSUFFICIENT_SOURCES = 2


@dataclass(frozen=True, slots=True)
class CandidateShadow:
    """Shadow-only diff between the canonical kernel result and the candidate.

    No field here ever reaches ``KernelOutput`` or the projected report.  In
    particular there is no ``direction`` field: the candidate never touches the
    decision direction.
    """

    baseline_raw: float
    candidate_raw: float
    total_delta: float
    baseline_calibrated: float
    candidate_calibrated: float
    calibrated_delta: float
    baseline_decision_state: str
    candidate_decision_state: str
    decision_state_changed: bool
    facts_hash: str


def _decision_state(calibrated: float, independent_sources: int) -> str:
    """Classify a calibrated confidence using the canonical kernel thresholds."""
    if calibrated < _ABSTAIN_CALIBRATED_CEILING or independent_sources < _INSUFFICIENT_SOURCES:
        return "abstain"
    if calibrated < _LOW_CONFIDENCE_CEILING:
        return "low_confidence"
    return "normal"


def _safe_float(value: object, default: float = 0.0) -> float:
    """Coerce to a finite float without ever raising (fail-closed helper)."""
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if not math.isfinite(result):
        return default
    return result


def _candidate_facts_hash(assessment: dict) -> str:
    """Deterministic candidate-local digest of the shadow assessment.

    ``assess_intrinsic_shadow`` does not emit a ``facts_hash`` (only
    ``build_intrinsic_shadow_observation`` does, with URL sanitization).  This
    helper derives a stable identifier from the assessment's own fields so
    repeated observations of the same facts collapse to the same digest.  It is
    self-contained and does not reimplement the observation-layer sanitizer.
    """
    material = json.dumps(
        {
            "schema_version": assessment.get("schema_version", ""),
            "asset_id": assessment.get("asset_id", ""),
            "as_of": assessment.get("as_of", ""),
            "total_delta": assessment.get("total_delta", 0.0),
            "dimensions": [
                {
                    "name": dim.get("name"),
                    "status": dim.get("status"),
                    "signed_delta": dim.get("signed_delta"),
                }
                for dim in assessment.get("dimensions", [])
            ],
        },
        sort_keys=True,
    )
    return "sha256:" + hashlib.sha256(
        _FACTS_DIGEST_DOMAIN + material.encode("utf-8")
    ).hexdigest()


def compute_candidate_shadow(
    kernel_output: KernelOutput, view, *, query: str = ""
) -> CandidateShadow:
    """Compute the shadow-only candidate diff for one kernel result + view.

    Reads the canonical ``kernel_output`` (read-only) and the PIT-safe intrinsic
    ``view``.  Pure and deterministic.  Fail-closed: any error returns a
    zero-delta ``CandidateShadow`` whose ``candidate_*`` fields equal the
    ``baseline_*`` fields, so downstream observers always see a well-formed
    diff and never an exception.

    The ``query`` argument is accepted for API symmetry with the shadow
    observation layer; it does not influence the computation.
    """
    try:
        baseline_raw = float(kernel_output.trust_score)
        assessment = assess_intrinsic_shadow(view)
        total_delta = float(assessment["total_delta"])
        if not math.isfinite(total_delta):
            raise ValueError("shadow total_delta must be finite")
        candidate_raw = max(0.0, min(1.0, baseline_raw + total_delta))

        baseline_calibrated = float(kernel_output.confidence)
        independent_sources = int(kernel_output.independent_sources)

        # Reuse the EXISTING calibration chain; do not reimplement scoring.
        # evidence_strength's third positional argument is the raw confidence
        # (mean supporting trust); here shifted by the intrinsic total_delta.
        strength = evidence_strength(
            kernel_output.supporting,
            kernel_output.contrarian,
            candidate_raw,
        )
        candidate_calibrated = float(
            interpolate_calibration(strength, DEFAULT_CALIBRATION_TABLE)
        )

        baseline_decision_state = kernel_output.decision_state
        candidate_decision_state = _decision_state(
            candidate_calibrated, independent_sources
        )

        return CandidateShadow(
            baseline_raw=baseline_raw,
            candidate_raw=candidate_raw,
            total_delta=total_delta,
            baseline_calibrated=baseline_calibrated,
            candidate_calibrated=candidate_calibrated,
            calibrated_delta=round(candidate_calibrated - baseline_calibrated, 8),
            baseline_decision_state=baseline_decision_state,
            candidate_decision_state=candidate_decision_state,
            decision_state_changed=(
                baseline_decision_state != candidate_decision_state
            ),
            facts_hash=_candidate_facts_hash(assessment),
        )
    except Exception:
        # Fail closed: zero delta, no change, never raise.  Use _safe_float so
        # a non-numeric trust_score/confidence cannot re-raise inside the
        # handler itself.
        baseline_raw = _safe_float(getattr(kernel_output, "trust_score", 0.0))
        baseline_calibrated = _safe_float(getattr(kernel_output, "confidence", 0.0))
        baseline_decision_state = getattr(kernel_output, "decision_state", "normal")
        if not isinstance(baseline_decision_state, str):
            baseline_decision_state = "normal"
        return CandidateShadow(
            baseline_raw=baseline_raw,
            candidate_raw=baseline_raw,
            total_delta=0.0,
            baseline_calibrated=baseline_calibrated,
            candidate_calibrated=baseline_calibrated,
            calibrated_delta=0.0,
            baseline_decision_state=baseline_decision_state,
            candidate_decision_state=baseline_decision_state,
            decision_state_changed=False,
            facts_hash="",
        )


__all__ = [
    "CANDIDATE_SCHEMA_VERSION",
    "CandidateShadow",
    "compute_candidate_shadow",
]
