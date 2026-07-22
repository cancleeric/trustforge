"""Deterministic scoring primitives with no TrustForge application dependencies."""
from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence

from .contracts import KernelClaim, KernelReputationTrace, KernelScoredClaim


DEFAULT_SCORE_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("src", 0.50),
    ("corr", 0.25),
    ("rec", 0.15),
    ("manip", 0.40),
)

DEFAULT_SOURCE_REPUTATIONS: tuple[tuple[str, float], ...] = (
    ("price", 0.95),
    ("onchain", 0.95),
    ("regulatory", 0.90),
    ("hoyabit", 0.85),
    ("news", 0.65),
    ("social", 0.35),
    ("price_live", 0.90),
    ("sentiment", 0.50),
    ("dev_activity", 0.50),
    ("whale_onchain", 0.88),
    ("celebrity_trade", 0.50),
)

DEFAULT_HALF_LIVES: tuple[tuple[str, float], ...] = (
    ("default", 12.0),
    ("whale_onchain", 2.0),
    ("celebrity_trade", 2.0),
)

_MANIP_PATTERNS: tuple[str, ...] = (
    r"to the moon",
    r"暴漲",
    r"翻倍",
    r"\bshill\b",
    r"喊單",
    r"穩賺",
    r"financial advice",
    r"\bpump\b",
    r"快上車",
    r"百倍",
)
_MANIP_NEGATION = re.compile(r"不會|不太|不致|不至|不再|沒有|沒|尚未|未|無法|別|勿|非")


def _exact_number(value: object, *, field: str) -> float:
    if type(value) not in {int, float}:
        raise ValueError(f"{field} must be a finite number")
    try:
        finite = math.isfinite(value)
    except OverflowError as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not finite:
        raise ValueError(f"{field} must be a finite number")
    return float(value)


def _validated_table(
    table: tuple[tuple[str, float], ...],
    *,
    field: str,
    required: frozenset[str] | None = None,
    positive: bool = False,
    probability: bool = False,
) -> dict[str, float]:
    if type(table) is not tuple:
        raise ValueError(f"{field} must be an immutable tuple table")
    result: dict[str, float] = {}
    for index, item in enumerate(table):
        if type(item) is not tuple or len(item) != 2 or type(item[0]) is not str:
            raise ValueError(f"{field} entries must be exact (str, number) tuples")
        key = item[0]
        if key in result:
            raise ValueError(f"{field} keys must be unique")
        value = _exact_number(item[1], field=f"{field}[{index}].value")
        if positive and value <= 0:
            raise ValueError(f"{field} values must be positive")
        if not positive and value < 0:
            raise ValueError(f"{field} values must be nonnegative")
        if probability and value > 1:
            raise ValueError(f"{field} values must be between zero and one")
        result[key] = value
    if required is not None and frozenset(result) != required:
        raise ValueError(f"{field} must contain exactly the required keys")
    return result


def manipulation_hits(text: str) -> tuple[str, ...]:
    """Return manipulation-keyword matches in stable legacy pattern order."""
    if type(text) is not str:
        raise ValueError("text must be an exact string")
    hits: list[str] = []
    for pattern in _MANIP_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            if _MANIP_NEGATION.search(text[max(0, match.start() - 4) : match.start()]):
                continue
            hits.append(match.group(0))
    return tuple(hits)


def manipulation_flags(text: str) -> tuple[str, ...]:
    """Return unique manipulation matches without changing their first-seen case."""
    seen: list[str] = []
    for hit in manipulation_hits(text):
        if hit not in seen:
            seen.append(hit)
    return tuple(seen)


def manipulation_penalty(text: str, kind: str, *, extra_hits: int = 0) -> float:
    """Return the bounded legacy keyword penalty from immutable inputs."""
    if type(text) is not str:
        raise ValueError("text must be an exact string")
    if type(kind) is not str:
        raise ValueError("kind must be an exact string")
    if type(extra_hits) is not int or extra_hits < 0:
        raise ValueError("extra_hits must be a nonnegative exact integer")
    weight = 1.5 if kind == "social" else 1.0
    hit_count = len(manipulation_hits(text))
    saturation_count = 2 if kind == "social" else 3
    if extra_hits >= max(0, saturation_count - hit_count):
        return 1.0
    return (hit_count + extra_hits) * 0.4 * weight


def corroboration_score(independent_sources: tuple[str, ...]) -> float:
    """Convert resolved unique source identities to the saturated legacy scalar."""
    if type(independent_sources) is not tuple or not all(
        type(source) is str for source in independent_sources
    ):
        raise ValueError("independent_sources must be an exact tuple of exact strings")
    count = len(set(independent_sources))
    return 1.0 - math.pow(0.5, count) if count else 0.0


def score_claim(
    claim: KernelClaim,
    *,
    now: float,
    weights: tuple[tuple[str, float], ...] = DEFAULT_SCORE_WEIGHTS,
    reputations: tuple[tuple[str, float], ...] = DEFAULT_SOURCE_REPUTATIONS,
    half_lives: tuple[tuple[str, float], ...] = DEFAULT_HALF_LIVES,
    independent_sources: tuple[str, ...] = (),
    dynamic_reputation: float | None = None,
    reputation_trace: KernelReputationTrace | None = None,
    info_flags: tuple[str, ...] = (),
) -> KernelScoredClaim:
    """Score one claim using only resolved, provider-free deterministic values."""
    if type(claim) is not KernelClaim:
        raise ValueError("claim must be an exact KernelClaim")
    now_value = _exact_number(now, field="now")
    weight_map = _validated_table(
        weights,
        field="weights",
        required=frozenset({"src", "corr", "rec", "manip"}),
    )
    reputation_map = _validated_table(
        reputations, field="reputations", probability=True
    )
    half_life_map = _validated_table(half_lives, field="half_lives", positive=True)
    if "default" not in half_life_map:
        raise ValueError("half_lives must contain a default entry")
    if type(info_flags) is not tuple or not all(type(flag) is str for flag in info_flags):
        raise ValueError("info_flags must be an exact tuple of exact strings")
    if reputation_trace is not None and type(reputation_trace) is not KernelReputationTrace:
        raise ValueError("reputation_trace must be an exact KernelReputationTrace or None")

    metadata: dict[str, object] = {}
    for key, value in claim.document.metadata:
        if key in metadata:
            raise ValueError("claim metadata keys must be unique")
        metadata[key] = value
    verified = metadata.get("verified_onchain")
    if verified is not None and type(verified) is not bool:
        raise ValueError("verified_onchain metadata must be an exact boolean")
    override = metadata.get("reputation")
    if override is not None:
        validated_override = _exact_number(override, field="metadata reputation")
        if not 0.0 <= validated_override <= 1.0:
            raise ValueError("metadata reputation must be between zero and one")
    resolved_dynamic: object = _NO_DYNAMIC_REPUTATION
    if dynamic_reputation is not None:
        resolved_dynamic = _exact_number(
            dynamic_reputation, field="dynamic_reputation"
        )
        if not 0.0 <= resolved_dynamic <= 1.0:  # type: ignore[operator]
            raise ValueError("dynamic_reputation must be between zero and one")
    reputation = _resolve_source_reputation(
        kind=claim.document.kind,
        metadata=metadata,
        reputations=reputation_map,
        dynamic_value=resolved_dynamic,
    )

    corroboration = corroboration_score(independent_sources)
    half_life = half_life_map.get(claim.document.kind, half_life_map["default"])
    recency = recency_decay(
        timestamp=claim.document.timestamp,
        now=now_value,
        half_life_hours=half_life,
    )
    manipulation = manipulation_penalty(claim.text, claim.document.kind)
    raw = (
        weight_map["src"] * reputation
        + weight_map["corr"] * corroboration
        + weight_map["rec"] * recency
        - weight_map["manip"] * manipulation
    )
    trust = max(0.0, min(1.0, raw))
    return KernelScoredClaim(
        claim=claim,
        trust=trust,
        components=(
            ("reputation", reputation),
            ("corroboration", corroboration),
            ("recency", recency),
            ("manipulation", manipulation),
        ),
        reputation_trace=reputation_trace,
        manip_flags=manipulation_flags(claim.text),
        info_flags=info_flags,
    )


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
    prior = _resolve_source_reputation(
        kind=kind,
        metadata=metadata,
        reputations=reputations,
    )
    dynamic_value = prior if dynamic is None else dynamic.get(source_key, prior)
    return _resolve_source_reputation(
        kind=kind,
        metadata=metadata,
        reputations=reputations,
        dynamic_value=dynamic_value,
    )


_NO_DYNAMIC_REPUTATION = object()


def _resolve_source_reputation(
    *,
    kind: str,
    metadata: Mapping[str, object],
    reputations: Mapping[str, float],
    dynamic_value: object = _NO_DYNAMIC_REPUTATION,
) -> float:
    """Canonical legacy-compatible source-reputation resolver."""
    base = reputations.get(kind, 0.5)
    unverified_celebrity = kind == "celebrity_trade" and not metadata.get(
        "verified_onchain", False
    )
    if unverified_celebrity:
        base = reputations.get("social", 0.35)
    override = metadata.get("reputation")
    prior = float(override) if override is not None else base
    if dynamic_value is _NO_DYNAMIC_REPUTATION:
        return prior
    if unverified_celebrity:
        return min(dynamic_value, reputations.get("social", 0.35))  # type: ignore[type-var]
    return dynamic_value  # type: ignore[return-value]


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
    "DEFAULT_HALF_LIVES",
    "DEFAULT_SCORE_WEIGHTS",
    "DEFAULT_SOURCE_REPUTATIONS",
    "corroboration_score",
    "interpolate_calibration",
    "manipulation_flags",
    "manipulation_hits",
    "manipulation_penalty",
    "recency_decay",
    "reputation_floor",
    "score_claim",
    "source_reputation",
    "stable_sigmoid",
]
