"""Sole production boundary from application claims to core judgment."""

from __future__ import annotations

import math
from typing import Any, Sequence

from trustforge_core import KernelInput, KernelOutput, run_kernel

from ..direction_resolution import ResolvedDirection
from ..trust.scoring import (
    Claim,
    ScoredClaim,
    TrustedBrief,
    resolve_kernel_run_resolution,
)
from .kernel_mapper import to_kernel_claim, to_legacy_scoring
from .kernel_projection import KernelJudgment, project


def _validated_claims(claims: Sequence[Claim]) -> list[Claim]:
    normalized = list(claims)
    for claim in normalized:
        try:
            timestamp = float(claim.doc.ts)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("document timestamp must be a finite number") from exc
        if not math.isfinite(timestamp):
            raise ValueError("document timestamp must be a finite number")
    return normalized


def to_kernel_input(
    claims: Sequence[Claim],
    *,
    pit_epoch: float,
    coin: str,
    query: str,
    direction: ResolvedDirection,
    stance_fn: Any = None,
    offline: bool = False,
) -> KernelInput:
    """Build a fully resolved immutable production kernel request."""
    if type(direction) is not ResolvedDirection:
        raise ValueError("direction must be an exact ResolvedDirection")
    normalized = _validated_claims(claims)
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
    """Run the only production judgment engine without an in-process fallback."""
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
    scored, brief = to_legacy_scoring(output, claims)
    return output, scored, brief, project(output, coin=coin)
