"""Application-to-core contract normalization.

This is intentionally an application adapter: it may know the current
TrustForge ``Claim``/``Document`` shapes, while ``trustforge_core`` may not.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any

from trustforge_core import (
    KERNEL_RESOLUTION_VERSION,
    JsonValue,
    KernelClaim,
    KernelClaimResolution,
    KernelDocument,
    KernelInput,
    KernelOutput,
    KernelRunResolution,
    validate_kernel_output_graph,
)

from ..direction_resolution import ResolvedDirection
from ..ingestion.base import Document
from ..trust.scoring import Claim, ScoredClaim, TrustedBrief


def _freeze_json(value: Any) -> JsonValue:
    """Convert JSON-compatible application metadata to immutable values."""
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float:
        return value if math.isfinite(value) else str(value)
    if type(value) is dict:
        if not all(type(key) is str for key in value):
            raise ValueError("metadata keys must be exact strings")
        return tuple(
            (key, _freeze_json(value[key])) for key in sorted(value)
        )
    if type(value) in {list, tuple}:
        return tuple(_freeze_json(item) for item in value)
    raise ValueError("metadata must contain only exact JSON values")


def _finite_timestamp(value: Any, *, fallback: float) -> float:
    """Keep malformed upstream timestamps from violating the core contract."""
    try:
        timestamp = float(value)
    except (TypeError, ValueError, OverflowError):
        timestamp = fallback
    if not math.isfinite(timestamp):
        timestamp = fallback
    return timestamp if math.isfinite(timestamp) else 0.0


def to_kernel_claim(claim: Claim, *, pit_epoch: float | None = None) -> KernelClaim:
    """Normalize one application claim into the independent core contract."""
    if type(claim) is not Claim:
        raise ValueError("claim must be an exact Claim")
    document = claim.doc
    if type(document) is not Document:
        raise ValueError("document must be an exact Document")
    metadata = _freeze_json(document.meta)
    if not isinstance(metadata, tuple):
        metadata = (("value", metadata),)
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
            timestamp=_finite_timestamp(
                document.ts,
                fallback=0.0 if pit_epoch is None else pit_epoch,
            ),
            url=document.url,
            metadata=metadata,
        ),
    )


def to_kernel_input(
    claims: Sequence[Claim], *, pit_epoch: float, coin: str, query: str
) -> KernelInput:
    """Build an immutable kernel request at the application boundary."""
    return KernelInput(
        claims=tuple(to_kernel_claim(claim, pit_epoch=pit_epoch) for claim in claims),
        pit_epoch=float(pit_epoch),
        coin=coin,
        query=query,
    )


def to_kernel_run_resolution(
    claim_resolutions: Sequence[KernelClaimResolution],
    direction: ResolvedDirection,
    *,
    resolution_version: str = KERNEL_RESOLUTION_VERSION,
) -> KernelRunResolution:
    """Map an app direction into the v2 run contract without reinterpretation."""
    if type(direction) is not ResolvedDirection:
        raise ValueError("direction must be an exact ResolvedDirection")
    validated_direction = ResolvedDirection(
        value=direction.value,
        policy_version=direction.policy_version,
        method=direction.method,
        input_ids=direction.input_ids,
        reason=direction.reason,
    )
    if resolution_version != KERNEL_RESOLUTION_VERSION:
        raise ValueError("unsupported kernel resolution version")
    return KernelRunResolution(
        claim_resolutions=tuple(claim_resolutions),
        resolved_direction=validated_direction.value,
        resolution_version=resolution_version,
    )


def to_resolved_kernel_input(
    claims: Sequence[Claim],
    *,
    pit_epoch: float,
    coin: str,
    query: str,
    direction: ResolvedDirection,
    stance_fn: Callable[[str, str], str] | None = None,
    stance_client: Any = None,
    stance_pair_budget: int = 40,
    stance_remaining_time_fn: Callable[[], float] | None = None,
    dynamic_reputation: bool = True,
    reputation_iterations: int = 3,
    offline: bool = False,
) -> KernelInput:
    """Resolve app-owned facts and compose one provider-free kernel request."""
    if type(direction) is not ResolvedDirection:
        raise ValueError("direction must be an exact ResolvedDirection")
    validated_direction = ResolvedDirection(
        value=direction.value,
        policy_version=direction.policy_version,
        method=direction.method,
        input_ids=direction.input_ids,
        reason=direction.reason,
    )
    from ..trust.scoring import (
        resolve_kernel_run_resolution,
    )

    normalized_claims = list(claims)
    claim_ids = tuple(claim.id for claim in normalized_claims)
    if len(set(claim_ids)) != len(claim_ids):
        raise ValueError("duplicate claim IDs are not allowed")
    base_input = to_kernel_input(
        normalized_claims,
        pit_epoch=pit_epoch,
        coin=coin,
        query=query,
    )
    run_resolution = resolve_kernel_run_resolution(
        normalized_claims,
        pit_epoch,
        resolved_direction=validated_direction.value,
        stance_client=stance_client,
        stance_pair_budget=stance_pair_budget,
        stance_remaining_time_fn=stance_remaining_time_fn,
        stance_fn=stance_fn,
        dynamic_reputation=dynamic_reputation,
        reputation_iterations=reputation_iterations,
        offline=offline,
    )
    return KernelInput(
        claims=base_input.claims,
        pit_epoch=base_input.pit_epoch,
        coin=base_input.coin,
        query=base_input.query,
        resolution=run_resolution,
    )


def to_legacy_scoring(
    output: KernelOutput, claims: Sequence[Claim]
) -> tuple[list[ScoredClaim], TrustedBrief]:
    """Adapt a kernel result to existing report/evidence consumer DTOs.

    Core provenance retains full precision.  The legacy trace presentation is
    intentionally rounded to its historical four-decimal representation.
    """
    validate_kernel_output_graph(output)
    normalized_claims = list(claims)
    normalized_kernel_claims = tuple(to_kernel_claim(claim) for claim in normalized_claims)
    claim_ids = tuple(claim.id for claim in normalized_kernel_claims)
    output_ids = tuple(item.claim.id for item in output.scored_claims)
    if len(set(claim_ids)) != len(claim_ids) or output_ids != claim_ids:
        raise ValueError("kernel output must match app claim IDs exactly and in order")
    if any(
        app_claim != item.claim
        for app_claim, item in zip(normalized_kernel_claims, output.scored_claims, strict=True)
    ):
        raise ValueError("kernel output must match the complete app claim graph")

    legacy_by_id: dict[str, ScoredClaim] = {}
    scored: list[ScoredClaim] = []
    for claim, item in zip(normalized_claims, output.scored_claims, strict=True):
        trace = item.reputation_trace
        legacy_trace = None
        if trace is not None:
            legacy_trace = {
                "source": trace.source,
                "prior": round(trace.prior, 4),
                "final": round(trace.final, 4),
                "agree_n": trace.agree_n,
                "contradict_n": trace.contradict_n,
                "iterations_run": trace.iterations_run,
                "mode": trace.mode,
            }
        legacy_item = ScoredClaim(
            claim=claim,
            trust=item.trust,
            components=dict(item.components),
            reputation_trace=legacy_trace,
            manip_flags=list(item.manip_flags),
            info_flags=list(item.info_flags),
        )
        scored.append(legacy_item)
        legacy_by_id[claim.id] = legacy_item

    supporting_ids = tuple(item.claim.id for item in output.supporting)
    contrarian_ids = tuple(item.claim.id for item in output.contrarian)
    if any(item not in legacy_by_id for item in supporting_ids + contrarian_ids):
        raise ValueError("kernel output references an unknown scored claim")
    brief = TrustedBrief(
        query=output.query,
        supporting=[legacy_by_id[item] for item in supporting_ids],
        contrarian=[legacy_by_id[item] for item in contrarian_ids],
        confidence=output.trust_score,
        calibrated_confidence=output.confidence,
    )
    return scored, brief
