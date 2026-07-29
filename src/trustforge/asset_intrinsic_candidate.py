"""Application adapter for immutable asset-intrinsic candidate facts.

Score composition belongs exclusively to ``trustforge_core``.  This module may
assess PIT-safe application views and serialize their facts, but contains no
baseline-plus-delta arithmetic.
"""

from __future__ import annotations

import hashlib
import json

from trustforge_core import (
    CANDIDATE_SCHEMA_VERSION,
    CandidateShadow,
    IntrinsicCandidateFacts,
    KernelOutput,
    compose_intrinsic_candidate,
)

from trustforge.asset_intrinsic_shadow import assess_intrinsic_shadow

_FACTS_DIGEST_DOMAIN = b"trustforge.intrinsic.candidate.v1\x00"


def build_intrinsic_candidate_facts(view) -> IntrinsicCandidateFacts:
    """Convert a validated PIT-safe view into immutable core input facts."""
    assessment = assess_intrinsic_shadow(view)
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
    facts_hash = (
        "sha256:"
        + hashlib.sha256(_FACTS_DIGEST_DOMAIN + material.encode("utf-8")).hexdigest()
    )
    return IntrinsicCandidateFacts(
        total_delta=float(assessment["total_delta"]),
        facts_hash=facts_hash,
    )


def compute_candidate_shadow(
    kernel_output: KernelOutput, view, *, query: str = ""
) -> CandidateShadow:
    """Compatibility adapter; canonical arithmetic executes only in core."""
    del query
    try:
        facts = build_intrinsic_candidate_facts(view)
        return compose_intrinsic_candidate(kernel_output, facts).shadow
    except Exception:
        facts = IntrinsicCandidateFacts(total_delta=0.0, facts_hash="")
        try:
            return compose_intrinsic_candidate(kernel_output, facts).shadow
        except Exception:
            # Preserve the historical malformed-output fail-closed contract.
            raw = getattr(kernel_output, "trust_score", 0.0)
            confidence = getattr(kernel_output, "confidence", 0.0)
            state = getattr(kernel_output, "decision_state", "normal")
            raw = float(raw) if isinstance(raw, (int, float)) else 0.0
            confidence = (
                float(confidence) if isinstance(confidence, (int, float)) else 0.0
            )
            state = state if isinstance(state, str) else "normal"
            return CandidateShadow(
                raw,
                raw,
                0.0,
                confidence,
                confidence,
                0.0,
                state,
                state,
                False,
                "",
            )


__all__ = [
    "CANDIDATE_SCHEMA_VERSION",
    "CandidateShadow",
    "build_intrinsic_candidate_facts",
    "compute_candidate_shadow",
]
