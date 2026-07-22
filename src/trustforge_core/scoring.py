"""Deterministic scoring primitives with no TrustForge application dependencies."""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence


def reputation_floor(
    kind: str, reputations: Mapping[str, float], *, unknown: float = 0.35
) -> float:
    """Return the bounded dynamic-reputation floor for a source kind."""
    return round(0.3 * reputations.get(kind, unknown), 4)


def source_reputation(
    *,
    kind: str,
    source_key: str,
    metadata: Mapping[str, object],
    reputations: Mapping[str, float],
    dynamic: Mapping[str, float] | None = None,
) -> float:
    """Resolve static or dynamic source reputation from plain immutable inputs."""
    base = reputations.get(kind, 0.5)
    unverified_celebrity = kind == "celebrity_trade" and not metadata.get(
        "verified_onchain", False
    )
    if unverified_celebrity:
        base = reputations.get("social", 0.35)
    override = metadata.get("reputation")
    prior = float(override) if override is not None else base
    if dynamic is None:
        return prior
    resolved = dynamic.get(source_key, prior)
    if unverified_celebrity:
        return min(resolved, reputations.get("social", 0.35))
    return resolved


def recency_decay(*, timestamp: float, now: float, half_life_hours: float) -> float:
    """Return exponential recency decay; invalid/unknown time is neutral (0.5)."""
    if not timestamp:
        return 0.5
    if not math.isfinite(timestamp) or not math.isfinite(now):
        return 0.5
    age_hours = (now - timestamp) / 3600.0
    if not math.isfinite(age_hours) or age_hours < 0:
        return 0.5
    return math.pow(0.5, age_hours / half_life_hours)


def stable_sigmoid(value: float, *, clamp: float = 30.0) -> float:
    """Return a numerically stable sigmoid with a bounded exponent."""
    bounded = max(-clamp, min(clamp, value))
    return 1.0 / (1.0 + math.exp(-bounded))


def interpolate_calibration(raw: float, table: Sequence[tuple[float, float]]) -> float:
    """Clamp and linearly interpolate a deterministic calibration table."""
    x = max(0.0, min(1.0, raw))
    if not table:
        return round(x, 4)
    if x <= table[0][0]:
        return table[0][1]
    if x >= table[-1][0]:
        return table[-1][1]
    for (x0, y0), (x1, y1) in zip(table, table[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return y0
            ratio = (x - x0) / (x1 - x0)
            return round(y0 + ratio * (y1 - y0), 4)
    return round(x, 4)


__all__ = [
    "interpolate_calibration",
    "recency_decay",
    "reputation_floor",
    "source_reputation",
    "stable_sigmoid",
]
