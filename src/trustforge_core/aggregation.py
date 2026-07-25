"""Pure aggregation and decision policy for already-scored kernel claims."""

from __future__ import annotations

import math
import re

from .contracts import (
    KernelOutput,
    KernelScoredClaim,
    require_supported_contract_version,
)
from .corroboration import canonical_source
from .scoring import interpolate_calibration


FIXED_HEURISTIC_VERSION = "fixed-heuristic-v1"
ISOTONIC_VERSION = "isotonic-v1"
SUPPORTED_CALIBRATION_MODEL_VERSIONS = frozenset(
    {FIXED_HEURISTIC_VERSION, ISOTONIC_VERSION}
)
FIXED_HEURISTIC_TABLE: tuple[tuple[float, float], ...] = (
    (0.00, 0.00),
    (0.10, 0.03),
    (0.20, 0.08),
    (0.30, 0.20),
    (0.40, 0.40),
    (0.55, 0.55),
    (0.70, 0.70),
    (0.85, 0.85),
    (1.00, 1.00),
)

_STRENGTH_WEIGHTS = (0.35, 0.30, 0.15, 0.20)
_COIN_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("BTC", ("btc", "bitcoin", "比特幣", "比特")),
    ("ETH", ("eth", "ethereum", "以太坊", "以太")),
    ("SOL", ("sol", "solana")),
    ("BNB", ("bnb", "binance")),
    ("XRP", ("xrp", "ripple", "瑞波")),
)


def _exact_string(value: object, *, field: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{field} must be an exact string")
    return value


def _probability(value: object, *, field: str) -> float:
    if type(value) not in {int, float}:
        raise ValueError(f"{field} must be a finite number between zero and one")
    try:
        finite = math.isfinite(value)
    except OverflowError as exc:
        raise ValueError(f"{field} must be a finite number between zero and one") from exc
    if not finite or not 0.0 <= value <= 1.0:
        raise ValueError(f"{field} must be a finite number between zero and one")
    return float(value)


def _calibration_table(
    version: str, table: tuple[tuple[float, float], ...]
) -> tuple[tuple[float, float], ...]:
    _exact_string(version, field="calibration_model_version")
    if version not in SUPPORTED_CALIBRATION_MODEL_VERSIONS:
        raise ValueError("unsupported calibration model version")
    if type(table) is not tuple:
        raise ValueError("calibration_table must be an exact tuple")
    if version == FIXED_HEURISTIC_VERSION:
        if table:
            raise ValueError("fixed-heuristic-v1 does not accept a calibration table")
        return FIXED_HEURISTIC_TABLE
    if len(table) < 2:
        raise ValueError("isotonic-v1 calibration_table must contain at least two points")
    validated: list[tuple[float, float]] = []
    previous_x = -1.0
    previous_y = -1.0
    for index, point in enumerate(table):
        if type(point) is not tuple or len(point) != 2:
            raise ValueError("calibration_table points must be exact tuples")
        x = _probability(point[0], field=f"calibration_table[{index}].x")
        y = _probability(point[1], field=f"calibration_table[{index}].y")
        if x <= previous_x:
            raise ValueError("calibration_table x values must be strictly increasing")
        if y < previous_y:
            raise ValueError("calibration_table y values must be nondecreasing")
        validated.append((x, y))
        previous_x, previous_y = x, y
    return tuple(validated)


def _normalize(value: str) -> set[str]:
    return {token for token in re.findall(r"[\w一-鿿]+", value.lower()) if len(token) > 1}


def _alias_in(alias: str, text: str) -> bool:
    if alias.isascii():
        return re.search(r"\b" + re.escape(alias) + r"\b", text, re.I | re.ASCII) is not None
    return alias in text


def _mentioned_coins(text: str) -> set[str]:
    return {
        code
        for code, aliases in _COIN_ALIASES
        if any(_alias_in(alias, text) for alias in aliases)
    }


def _explicit_coin(item: KernelScoredClaim) -> str | None:
    values = [value for key, value in item.claim.document.metadata if key == "coin"]
    if len(values) > 1:
        raise ValueError("claim document metadata coin key must be unique")
    if not values:
        return None
    return _exact_string(values[0], field="claim document metadata coin")


def _coin_relation(item: KernelScoredClaim, targets: set[str]) -> tuple[bool, bool]:
    explicit = _explicit_coin(item)
    if explicit:
        matches = explicit.upper() in targets
        return matches, matches
    document = item.claim.document
    mentioned = _mentioned_coins(document.id + " " + document.text)
    if not mentioned:
        return True, False
    exact = bool(mentioned & targets) and not (mentioned - targets)
    return exact, exact


def evidence_strength(
    *,
    supporting: tuple[KernelScoredClaim, ...],
    contrarian: tuple[KernelScoredClaim, ...],
    trust_score: float,
) -> float:
    """Return legacy evidence strength from exact immutable scored values."""
    for field, values in (("supporting", supporting), ("contrarian", contrarian)):
        if type(values) is not tuple or not all(
            type(item) is KernelScoredClaim for item in values
        ):
            raise ValueError(
                f"{field} must be an exact tuple of exact KernelScoredClaim values"
            )
        for index, item in enumerate(values):
            _probability(item.trust, field=f"{field}[{index}].trust")
    trust_score = _probability(trust_score, field="trust_score")
    sources = {canonical_source(item.claim.document.source) for item in supporting}
    kinds = {item.claim.document.kind for item in supporting}
    contrary_sources = {
        canonical_source(item.claim.document.source) for item in contrarian
    }
    independent = max(0.0, min((len(sources) - 1) / 3, 1.0))
    diversity = max(0.0, min((len(kinds) - 1) / 2, 1.0))
    total = len(sources) + len(contrary_sources)
    dominance = len(sources) / total if total else 0.0
    trust_w, independent_w, diversity_w, dominance_w = _STRENGTH_WEIGHTS
    return max(
        0.0,
        min(
            trust_w * trust_score
            + independent_w * independent
            + diversity_w * diversity
            + dominance_w * dominance,
            1.0,
        ),
    )


def aggregate_scored_claims(
    *,
    scored_claims: tuple[KernelScoredClaim, ...],
    query: str,
    coin: str,
    support_threshold: float,
    contract_version: str,
    calibration_model_version: str,
    calibration_table: tuple[tuple[float, float], ...],
    resolved_direction: str,
) -> KernelOutput:
    """Aggregate immutable scored claims without callbacks, I/O, or ambient state."""
    require_supported_contract_version(contract_version)
    if type(scored_claims) is not tuple or not all(
        type(item) is KernelScoredClaim for item in scored_claims
    ):
        raise ValueError("scored_claims must be an exact tuple of exact KernelScoredClaim values")
    for index, item in enumerate(scored_claims):
        _probability(item.trust, field=f"scored_claims[{index}].trust")
    query = _exact_string(query, field="query")
    coin = _exact_string(coin, field="coin")
    resolved_direction = _exact_string(resolved_direction, field="resolved_direction")
    threshold = _probability(support_threshold, field="support_threshold")
    table = _calibration_table(calibration_model_version, calibration_table)

    query_tokens = _normalize(query)
    candidates = list(scored_claims)
    if coin:
        targets = {token.strip().upper() for token in re.split(r"[,\s]+", coin) if token.strip()}
        related = [(item, _coin_relation(item, targets)) for item in candidates]
        candidates = [item for item, (matches, _specific) in related if matches]
        specific = {id(item): is_specific for item, (_matches, is_specific) in related}
        candidates.sort(key=lambda item: (0 if specific[id(item)] else 1, -item.trust))
    else:
        relevant = [
            item
            for item in candidates
            if not query_tokens or (_normalize(item.claim.text) & query_tokens)
        ]
        candidates = relevant or candidates
        candidates.sort(key=lambda item: item.trust, reverse=True)

    all_supporting = [item for item in candidates if item.trust >= threshold]
    all_contrarian = [item for item in candidates if item.trust < threshold]
    trust_score = (
        sum(item.trust for item in all_supporting) / len(all_supporting)
        if all_supporting
        else 0.0
    )
    strength = evidence_strength(
        supporting=tuple(all_supporting),
        contrarian=tuple(all_contrarian),
        trust_score=trust_score,
    )
    confidence = interpolate_calibration(strength, table)
    supporting = tuple(all_supporting[:10])
    contrarian = tuple(all_contrarian[:5])
    independent_sources = len(
        {canonical_source(item.claim.document.source) for item in supporting}
    )
    low_calibrated = confidence < 0.35
    insufficient = independent_sources < 2
    abstain = low_calibrated or insufficient
    if abstain:
        state = "abstain"
        reasons = tuple(
            reason
            for condition, reason in (
                (low_calibrated, "low_calibrated_confidence"),
                (insufficient, "insufficient_independent_sources"),
            )
            if condition
        )
    elif confidence < 0.5:
        state = "low_confidence"
        reasons = ("below_normal_confidence",)
    else:
        state = "normal"
        reasons = ()
    return KernelOutput(
        trust_score=trust_score,
        confidence=confidence,
        abstain=abstain,
        direction=resolved_direction,
        reason_codes=reasons,
        supporting_count=len(supporting),
        independent_sources=independent_sources,
        contract_version=contract_version,
        query=query,
        scored_claims=scored_claims,
        supporting=supporting,
        contrarian=contrarian,
        decision_state=state,
    )


__all__ = [
    "FIXED_HEURISTIC_TABLE",
    "FIXED_HEURISTIC_VERSION",
    "ISOTONIC_VERSION",
    "SUPPORTED_CALIBRATION_MODEL_VERSIONS",
    "aggregate_scored_claims",
    "evidence_strength",
]
