"""Canonical, provider-free composition of intrinsic candidate observations."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .contracts import KernelOutput
from .scoring import (
    DEFAULT_CALIBRATION_TABLE,
    classify_decision_state,
    evidence_strength,
    interpolate_calibration,
)

CANDIDATE_SCHEMA_VERSION = "1.0.0"
INTRINSIC_TOTAL_DELTA_CAP = 0.08


@dataclass(frozen=True, slots=True)
class IntrinsicCandidateFacts:
    total_delta: float
    facts_hash: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.total_delta, bool)
            or not isinstance(self.total_delta, (int, float))
            or not math.isfinite(float(self.total_delta))
            or abs(float(self.total_delta)) > INTRINSIC_TOTAL_DELTA_CAP
        ):
            raise ValueError("intrinsic candidate total_delta is invalid")
        if not isinstance(self.facts_hash, str):
            raise ValueError("intrinsic candidate facts_hash is invalid")


@dataclass(frozen=True, slots=True)
class CandidateShadow:
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


@dataclass(frozen=True, slots=True)
class CandidateComposition:
    official_output: KernelOutput
    shadow: CandidateShadow
    promoted: bool = False
    promotion_reason: str = "signed_gate_not_passed"


def compose_intrinsic_candidate(
    baseline: KernelOutput,
    facts: IntrinsicCandidateFacts,
    *,
    signed_promotion_passed: bool = False,
) -> CandidateComposition:
    """Compose from the baseline only; never mutate or replace official output."""
    if type(baseline) is not KernelOutput or type(facts) is not IntrinsicCandidateFacts:
        raise TypeError("canonical composition requires exact core contracts")
    baseline_raw = float(baseline.trust_score)
    total_delta = float(facts.total_delta)
    candidate_raw = max(0.0, min(1.0, baseline_raw + total_delta))
    strength = evidence_strength(
        baseline.supporting, baseline.contrarian, candidate_raw
    )
    candidate_calibrated = float(
        interpolate_calibration(strength, DEFAULT_CALIBRATION_TABLE)
    )
    candidate_state = classify_decision_state(
        candidate_calibrated, baseline.independent_sources
    )
    shadow = CandidateShadow(
        baseline_raw=baseline_raw,
        candidate_raw=candidate_raw,
        total_delta=total_delta,
        baseline_calibrated=float(baseline.confidence),
        candidate_calibrated=candidate_calibrated,
        calibrated_delta=round(candidate_calibrated - baseline.confidence, 8),
        baseline_decision_state=baseline.decision_state,
        candidate_decision_state=candidate_state,
        decision_state_changed=baseline.decision_state != candidate_state,
        facts_hash=facts.facts_hash,
    )
    return CandidateComposition(
        official_output=baseline,
        shadow=shadow,
        promoted=False,
        promotion_reason=(
            "activation_not_implemented"
            if signed_promotion_passed
            else "signed_gate_not_passed"
        ),
    )


__all__ = [
    "CANDIDATE_SCHEMA_VERSION",
    "CandidateComposition",
    "CandidateShadow",
    "IntrinsicCandidateFacts",
    "compose_intrinsic_candidate",
]
