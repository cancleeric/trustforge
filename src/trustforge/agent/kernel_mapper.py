"""Application-to-core contract normalization.
 
This is intentionally an application adapter: it may know the current
TrustForge ``Claim``/``Document`` shapes, while ``trustforge_core`` may not.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from trustforge_core import (
    JsonValue,
    KernelClaim,
    KernelDocument,
    KernelInput,
    KernelOutput,
    KernelReputationTrace,
    canonical_source,
    run_kernel,
)

from ..direction_resolution import ResolvedDirection
from ..trust.scoring import Claim, ScoredClaim, TrustedBrief
from ..trust.scoring import resolve_kernel_run_resolution
from .kernel_projection import KernelJudgment, project


def _freeze_json(value: Any) -> JsonValue:
    """Convert JSON-compatible application metadata to immutable values."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Mapping):
        return tuple(
            (str(key), _freeze_json(item))
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze_json(item) for item in value)
    return str(value)


def to_kernel_claim(claim: Claim) -> KernelClaim:
    """Normalize one application claim into the independent core contract."""
    document = claim.doc
    metadata = _freeze_json(document.meta)
    if not isinstance(metadata, tuple):
        metadata = (("value", metadata),)
    try:
        timestamp = float(document.ts)
    except (TypeError, ValueError, OverflowError):
        timestamp = 0.0
    if not math.isfinite(timestamp):
        timestamp = 0.0
    return KernelClaim(
        id=claim.id,
        text=claim.text,
        claim_type=claim.claim_type,
        direction=claim.direction,
        document=KernelDocument(
            id=document.id,
            kind=document.kind,
            source=document.source,
            text=document.text,
            timestamp=timestamp,
            url=document.url,
            metadata=metadata,
        ),
    )


def to_kernel_input(
    claims: Sequence[Claim],
    *,
    pit_epoch: float,
    coin: str,
    query: str,
    direction: ResolvedDirection | None = None,
    stance_fn: Any = None,
    offline: bool = False,
) -> KernelInput:
    """Build the fully resolved immutable production kernel request."""
    normalized = list(claims)
    if direction is None:
        # Retained only for archived shadow/parity utilities. Production calls
        # ``run_authoritative_judgment``, which requires an exact direction and
        # therefore always supplies a non-null resolution.
        return KernelInput(
            claims=tuple(to_kernel_claim(claim) for claim in normalized),
            pit_epoch=float(pit_epoch),
            coin=coin,
            query=query,
        )
    if type(direction) is not ResolvedDirection:
        raise ValueError("direction must be an exact ResolvedDirection")
    resolution = resolve_kernel_run_resolution(
        normalized,
        pit_epoch,
        resolved_direction=direction.value,
        stance_fn=stance_fn,
        dynamic_reputation=True,
        offline=offline,
    )
    return KernelInput(
        claims=tuple(to_kernel_claim(claim) for claim in normalized),
        pit_epoch=float(pit_epoch),
        coin=coin,
        query=query,
        resolution=resolution,
    )


def _trace_to_dict(trace: KernelReputationTrace | None) -> dict | None:
    if trace is None:
        return None
    return {
        "source": trace.source,
        "prior": trace.prior,
        "final": trace.final,
        "agree_n": trace.agree_n,
        "contradict_n": trace.contradict_n,
        "iterations_run": trace.iterations_run,
        "mode": trace.mode,
    }


def _validate_kernel_output(output: KernelOutput, claims: Sequence[Claim]) -> None:
    if type(output) is not KernelOutput:
        raise ValueError("output must be an exact KernelOutput")
    if not 0.0 <= output.trust_score <= 1.0:
        raise ValueError("trust_score must be in [0, 1]")
    if not 0.0 <= output.confidence <= 1.0:
        raise ValueError("confidence must be in [0, 1]")
    if output.supporting_count != len(output.supporting):
        raise ValueError("supporting_count must match supporting")
    expected_independent = len(
        {canonical_source(item.claim.document.source) for item in output.supporting}
    )
    if output.independent_sources != expected_independent:
        raise ValueError("independent_sources must match")
    if output.abstain != (output.decision_state == "abstain"):
        raise ValueError("abstain must match decision_state")
    claim_by_id = {claim.id: claim for claim in claims}
    if len(claim_by_id) != len(claims):
        raise ValueError("duplicate claim IDs")
    for item in output.scored_claims:
        if type(item.trust) is not float or not 0.0 <= item.trust <= 1.0:
            raise ValueError("trust must be in [0, 1]")
        if type(item.components) is not tuple:
            raise ValueError("components must be a tuple")
        for component in item.components:
            if (
                type(component) is not tuple
                or len(component) != 2
                or type(component[0]) is not str
            ):
                raise ValueError("components must contain (str, float) tuples")
        if type(item.manip_flags) is not tuple or not all(
            type(flag) is str for flag in item.manip_flags
        ):
            raise ValueError("manip_flags must be exact")
        if type(item.info_flags) is not tuple or not all(
            type(flag) is str for flag in item.info_flags
        ):
            raise ValueError("info_flags must be exact")
    if len([c for c in claims if type(c) is not Claim]):
        raise ValueError("claims must be exact list of exact Claim")
    output_ids = {item.claim.id for item in output.scored_claims}
    provided_ids = {claim.id for claim in claims}
    if output_ids != provided_ids:
        raise ValueError("complete app claim graph equivalence")
    if len(set(item.claim.id for item in output.scored_claims)) != len(
        output.scored_claims
    ):
        raise ValueError("score claims must contain unique claim IDs")
    # INTEGRITY-001: validate contrarian claims are subset of input and disjoint from supporting
    supporting_ids = {item.claim.id for item in output.supporting}
    contrarian_ids = {item.claim.id for item in output.contrarian}
    if not contrarian_ids <= provided_ids:
        raise ValueError("contrarian claims must be a subset of input claim IDs")
    if contrarian_ids & supporting_ids:
        raise ValueError("contrarian and supporting claim IDs must be disjoint")
    if len(contrarian_ids) != len(output.contrarian):
        raise ValueError("contrarian claims must contain unique claim IDs")


def to_app_scoring(
    output: KernelOutput, claims: Sequence[Claim]
) -> tuple[list[ScoredClaim], TrustedBrief]:
    """Project kernel output into the application's presentation-only shapes.

    Validates the complete output graph before mapping fields.  Field
    mappings are exact and deterministic:
    * KernelScoredClaim.trust -> ScoredClaim.trust
    * KernelOutput.supporting -> TrustedBrief.supporting
    * KernelOutput.confidence -> TrustedBrief.calibrated_confidence
    """
    _validate_kernel_output(output, claims)
    claim_by_id = {claim.id: claim for claim in claims}
    scored_claims: list[ScoredClaim] = []
    for item in output.scored_claims:
        original = claim_by_id[item.claim.id]
        scored_claims.append(
            ScoredClaim(
                claim=original,
                trust=item.trust,
                components=dict(item.components),
                reputation_trace=_trace_to_dict(item.reputation_trace),
                manip_flags=list(item.manip_flags),
                info_flags=list(item.info_flags),
            )
        )
    supporting = [claim_by_id[item.claim.id] for item in output.supporting]
    contrarian = [claim_by_id[item.claim.id] for item in output.contrarian]
    supporting_scored = [s for s in scored_claims if s.claim.id in {c.id for c in supporting}]
    contrarian_scored = [s for s in scored_claims if s.claim.id in {c.id for c in contrarian}]
    brief = TrustedBrief(
        query=output.query,
        supporting=supporting_scored,
        contrarian=contrarian_scored,
        confidence=output.trust_score,
        calibrated_confidence=output.confidence,
    )
    return scored_claims, brief


def run_authoritative_judgment(
    claims: Sequence[Claim],
    *,
    pit_epoch: float,
    coin: str,
    query: str,
    direction: ResolvedDirection,
    stance_fn: Any = None,
    offline: bool = False,
) -> tuple[KernelOutput, list[ScoredClaim], TrustedBrief, KernelJudgment]:
    """Run the sole production judgment engine and project its immutable result.

    There is intentionally no legacy scoring fallback here.  A kernel contract
    or execution failure aborts the request so release-level A/B rollback—not a
    second in-process judgment engine—remains the only recovery mechanism.
    """

    output = run_kernel(
        to_kernel_input(
            claims,
            pit_epoch=pit_epoch,
            coin=coin,
            query=query,
            direction=direction,
            stance_fn=stance_fn,
            offline=offline,
        )
    )
    scored, brief = to_app_scoring(output, claims)
    return output, scored, brief, project(output, coin=coin)


def to_legacy_scoring(
    output: KernelOutput, claims: Sequence[Claim]
) -> tuple[list[ScoredClaim], TrustedBrief]:
    """Compatibility shape facade for offline parity consumers.

    Production entrypoints use :func:`run_authoritative_judgment`.  This named
    wrapper preserves the public adapter symbol while delegating only immutable
    output projection; it cannot score, aggregate, or select a judgment.
    """
    return to_app_scoring(output, claims)
