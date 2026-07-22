"""Application-owned market direction resolution before trust scoring.

The resolver consumes normalized application claims and already-loaded document
facts only.  It deliberately has no dependency on scoring, aggregation, the
kernel runtime, connectors, or persistence.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from itertools import islice

from trustforge_core import KernelClaim, KernelDocument

from .coin_scope import matches_coin_fields
from .semantic_direction import DirectionVote, aggregate_votes

DIRECTION_POLICY_VERSION = "direction-v1"
CANONICAL_DIRECTIONS = frozenset({"bullish", "bearish", "neutral", "unknown"})
_KIND_TO_SEMANTIC_TYPE = {
    "price": "price",
    "news": "news",
    "regulatory": "news",
    "onchain": "onchain",
    "market": "onchain",
    "sentiment": "sentiment",
    "social": "sentiment",
}
SemanticDirectionProvider = Callable[
    [Mapping[str, list[str]]], Sequence[DirectionVote]
]


@dataclass(frozen=True, slots=True)
class ResolvedDirection:
    """Versioned, immutable direction with deterministic input lineage."""

    value: str
    policy_version: str
    method: str
    input_ids: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        if type(self.value) is not str or self.value not in CANONICAL_DIRECTIONS:
            raise ValueError("unsupported resolved direction")
        if (
            type(self.policy_version) is not str
            or self.policy_version != DIRECTION_POLICY_VERSION
        ):
            raise ValueError(
                f"policy_version must be {DIRECTION_POLICY_VERSION}"
            )
        if type(self.method) is not str or self.method not in {
            "semantic-provider",
            "ohlcv-close",
            "ohlcv-return",
            "no-signal",
        }:
            raise ValueError("unsupported direction resolution method")
        if (
            type(self.input_ids) is not tuple
            or not all(
                type(item) is str
                and item
                and len(item) <= 256
                and not any(ord(char) < 32 for char in item)
                for item in self.input_ids
            )
            or len(set(self.input_ids)) != len(self.input_ids)
        ):
            raise ValueError("input_ids must be a unique tuple of nonempty strings")
        if (
            type(self.reason) is not str
            or len(self.reason) > 512
            or any(ord(char) < 32 for char in self.reason)
        ):
            raise ValueError("reason must be a safe string of at most 512 characters")

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("ResolvedDirection is sealed and cannot be subclassed")


def _validated_claims(
    claims: Sequence[KernelClaim], *, coin: str
) -> tuple[KernelClaim, ...]:
    if type(coin) is not str or not coin.strip():
        raise ValueError("coin must be a nonempty string")
    normalized = tuple(claims)
    if not all(type(claim) is KernelClaim for claim in normalized):
        raise ValueError("claims must contain exact KernelClaim values")
    ids = tuple(claim.id for claim in normalized)
    if any(type(item) is not str or not item for item in ids):
        raise ValueError("claim IDs must be nonempty strings")
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate claim IDs are not allowed")
    revalidated: list[KernelClaim] = []
    for claim in normalized:
        document = claim.document
        if type(document) is not KernelDocument:
            continue
        try:
            clean_document = KernelDocument(
                id=document.id,
                kind=document.kind,
                source=document.source,
                text=document.text,
                timestamp=document.timestamp,
                url=document.url,
                metadata=document.metadata,
            )
            revalidated.append(
                KernelClaim(
                    id=claim.id,
                    text=claim.text,
                    document=clean_document,
                    claim_type=claim.claim_type,
                    direction=claim.direction,
                )
            )
        except (AttributeError, TypeError, ValueError, OverflowError):
            continue
    return tuple(
        claim for claim in revalidated if _matches_coin(claim.document, coin)
    )


def _metadata_value(document: KernelDocument, key: str) -> object:
    for item_key, value in document.metadata:
        if item_key == key:
            return value
    return None


def _metadata_contains(document: KernelDocument, key: str) -> bool:
    return any(item_key == key for item_key, _ in document.metadata)


def _nested_value(value: object, key: str) -> object:
    if type(value) is not tuple:
        return None
    for item in value:
        if type(item) is tuple and len(item) == 2 and item[0] == key:
            return item[1]
    return None


def _matches_coin(document: KernelDocument, coin: str) -> bool:
    """Pure equivalent of app coin scope over an immutable kernel document."""
    explicit = _metadata_value(document, "coin")
    if explicit is not None and type(explicit) is not str:
        return False
    return matches_coin_fields(
        document_id=document.id,
        text=document.text,
        explicit_coin=explicit,
        target_coin=coin,
    )


def semantic_evidence(
    claims: Sequence[KernelClaim], *, coin: str
) -> dict[str, list[str]]:
    """Build the exact legacy kind/text provider input without scored claims."""
    scoped = _validated_claims(claims, coin=coin)
    evidence: dict[str, list[str]] = {}
    for claim in scoped:
        semantic_type = _KIND_TO_SEMANTIC_TYPE.get(claim.document.kind or "unknown")
        if semantic_type:
            evidence.setdefault(semantic_type, []).append(claim.text)
    return evidence


def _finite_number(value: object) -> float | None:
    if type(value) not in {int, float, str} or type(value) is bool:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _pit_date(pit_epoch: float) -> date:
    if type(pit_epoch) not in {int, float} or type(pit_epoch) is bool:
        raise ValueError("pit_epoch must be a finite number")
    try:
        if not math.isfinite(pit_epoch) or pit_epoch < 0:
            raise ValueError("pit_epoch must be a finite number")
        return datetime.fromtimestamp(float(pit_epoch), timezone.utc).date()
    except (OverflowError, OSError) as exc:
        raise ValueError("pit_epoch must be a valid Unix timestamp") from exc


def _parse_window(value: object, *, pit_day: date) -> tuple[date, date] | None:
    if type(value) is not str or value.count("~") != 1:
        return None
    raw_start, raw_end = value.split("~")
    try:
        start = date.fromisoformat(raw_start)
        end = date.fromisoformat(raw_end)
    except ValueError:
        return None
    if start > end or end > pit_day:
        return None
    return start, end


def _price_fact_window_is_valid(document: KernelDocument, *, pit_day: date) -> bool:
    date_range = _metadata_value(document, "date_range")
    lineage = _metadata_value(document, "data_lineage")
    analysis_window = _nested_value(lineage, "analysis_window")
    parsed_range = _parse_window(date_range, pit_day=pit_day)
    parsed_lineage = _parse_window(analysis_window, pit_day=pit_day)
    return (
        parsed_range is not None
        and parsed_lineage is not None
        and parsed_range == parsed_lineage
    )


def _claim_is_pit_valid(
    claim: KernelClaim, *, pit_epoch: float, pit_day: date
) -> bool:
    raw_timestamp = claim.document.timestamp
    if type(raw_timestamp) not in {int, float} or type(raw_timestamp) is bool:
        return False
    try:
        timestamp = float(raw_timestamp)
    except OverflowError:
        return False
    if not math.isfinite(timestamp) or timestamp < 0 or timestamp > pit_epoch:
        return False
    document = claim.document
    raw_date = _metadata_value(document, "date")
    if raw_date is not None:
        if type(raw_date) is not str:
            return False
        try:
            if date.fromisoformat(raw_date) > pit_day:
                return False
        except ValueError:
            return False
    if document.kind == "price" and _metadata_contains(document, "ret_pct"):
        return _price_fact_window_is_valid(document, pit_day=pit_day)
    return True


def _eligible_price_claims(
    claims: Sequence[KernelClaim], *, coin: str, pit_epoch: float
) -> tuple[KernelClaim, ...]:
    pit_day = _pit_date(pit_epoch)
    scoped = _validated_claims(claims, coin=coin)
    eligible: list[KernelClaim] = []
    for claim in scoped:
        doc = claim.document
        if doc.kind != "price":
            continue
        if not _claim_is_pit_valid(
            claim, pit_epoch=float(pit_epoch), pit_day=pit_day
        ):
            continue
        eligible.append(claim)
    return tuple(eligible)


def resolve_ohlcv_direction(
    claims: Sequence[KernelClaim], *, coin: str, pit_epoch: float
) -> ResolvedDirection:
    """Resolve direction from loaded, coin-scoped, point-in-time price facts."""
    price_claims = _eligible_price_claims(claims, coin=coin, pit_epoch=pit_epoch)
    points: list[tuple[date, float, str]] = []
    returns: list[tuple[float, str]] = []
    for claim in price_claims:
        ret_pct = _metadata_value(claim.document, "ret_pct")
        if ret_pct is not None:
            value = _finite_number(ret_pct)
            if value is not None:
                returns.append((value, claim.id))
            continue
        close = _finite_number(_metadata_value(claim.document, "close"))
        raw_date = _metadata_value(claim.document, "date")
        if close is None or close <= 0 or type(raw_date) is not str:
            continue
        try:
            points.append((date.fromisoformat(raw_date), close, claim.id))
        except ValueError:
            continue

    # Objective daily closes are more precise than pre-aggregated return facts.
    points_by_date: dict[date, list[tuple[float, str]]] = {}
    for point_date, close, claim_id in points:
        points_by_date.setdefault(point_date, []).append((close, claim_id))
    daily_points: list[tuple[date, float, tuple[str, ...]]] = []
    for point_date, observations in points_by_date.items():
        closes = {item[0] for item in observations}
        if len(closes) == 1:
            daily_points.append(
                (point_date, next(iter(closes)), tuple(sorted(item[1] for item in observations)))
            )

    if len(daily_points) >= 2:
        daily_points.sort(key=lambda item: item[0])
        latest_date, latest_close, _ = daily_points[-1]
        base_close = daily_points[0][1]
        for point_date, close, _ in reversed(daily_points[:-1]):
            if (latest_date - point_date).days >= 14:
                base_close = close
                break
        change = (latest_close - base_close) / base_close
        if not math.isfinite(change):
            return _unknown_direction()
        value = "bullish" if change > 0.03 else "bearish" if change < -0.03 else "neutral"
        return ResolvedDirection(
            value=value,
            policy_version=DIRECTION_POLICY_VERSION,
            method="ohlcv-close",
            input_ids=tuple(
                sorted(
                    claim_id
                    for _, _, claim_ids in daily_points
                    for claim_id in claim_ids
                )
            ),
            reason=f"finite PIT-valid close return {change:.6f}",
        )

    if returns:
        try:
            average = math.fsum(item[0] for item in returns) / len(returns)
        except OverflowError:
            return _unknown_direction()
        if not math.isfinite(average):
            return _unknown_direction()
        value = "bullish" if average > 3.0 else "bearish" if average < -3.0 else "neutral"
        return ResolvedDirection(
            value=value,
            policy_version=DIRECTION_POLICY_VERSION,
            method="ohlcv-return",
            input_ids=tuple(sorted(item[1] for item in returns)),
            reason=f"finite PIT-valid mean ret_pct {average:.6f}",
        )

    return _unknown_direction()


def _unknown_direction() -> ResolvedDirection:
    return ResolvedDirection(
        value="unknown",
        policy_version=DIRECTION_POLICY_VERSION,
        method="no-signal",
        input_ids=(),
        reason="no valid coin-scoped PIT price fact",
    )


def resolve_direction(
    claims: Sequence[KernelClaim],
    *,
    coin: str,
    pit_epoch: float,
    semantic_provider: SemanticDirectionProvider | None = None,
) -> ResolvedDirection:
    """Resolve once before scoring, preserving the legacy semantic call shape.

    The callback is invoked at most once.  Its implementation may retain the
    legacy ``analyze_direction`` behavior (up to three model completions); this
    resolver neither records nor duplicates provider token/cost events.
    """
    pit_day = _pit_date(pit_epoch)
    scoped = tuple(
        claim
        for claim in _validated_claims(claims, coin=coin)
        if _claim_is_pit_valid(
            claim, pit_epoch=float(pit_epoch), pit_day=pit_day
        )
    )
    evidence = semantic_evidence(scoped, coin=coin)
    if semantic_provider is not None and evidence:
        try:
            votes = list(islice(iter(semantic_provider(evidence)), 5))
            if len(votes) > 4 or len(
                {vote.source_type for vote in votes if type(vote) is DirectionVote}
            ) != len(votes):
                votes = []
            if not all(
                type(vote) is DirectionVote
                and vote.source_type in {"price", "news", "onchain", "sentiment"}
                and vote.direction in {"bullish", "bearish", "neutral"}
                and type(vote.confidence) in {int, float}
                and type(vote.confidence) is not bool
                and math.isfinite(vote.confidence)
                and 0.0 <= vote.confidence <= 1.0
                and type(vote.reasoning) is str
                for vote in votes
            ):
                votes = []
            if votes:
                value, confidence = aggregate_votes(votes)
                if (
                    value not in {"bullish", "bearish", "neutral"}
                    or type(confidence) not in {int, float}
                    or type(confidence) is bool
                    or not math.isfinite(confidence)
                    or not 0.0 <= confidence <= 1.0
                ):
                    raise ValueError("semantic aggregate returned an invalid result")
        except Exception:
            votes = []
        if votes:
            return ResolvedDirection(
                value=value,
                policy_version=DIRECTION_POLICY_VERSION,
                method="semantic-provider",
                input_ids=tuple(sorted(claim.id for claim in scoped if _KIND_TO_SEMANTIC_TYPE.get(claim.document.kind))),
                reason=f"legacy semantic vote aggregate confidence {confidence:.6f}",
            )
    return resolve_ohlcv_direction(scoped, coin=coin, pit_epoch=pit_epoch)
