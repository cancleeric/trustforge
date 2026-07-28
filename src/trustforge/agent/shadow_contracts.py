"""Immutable, versioned, fail-closed shadow-observation contracts."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

CONTRACT_VERSION = "shadow-contract.v1"
POLICY_PATH = Path(__file__).parents[3] / "data" / "contracts" / "shadow-policy.v1.json"
_INPUT_DOMAIN = b"trustforge.shadow.input.v1\x00"
_POLICY_DOMAIN = b"trustforge.shadow.policy.v1\x00"
_OBSERVATION_DOMAIN = b"trustforge.shadow.observation.v1\x00"
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_RELEASE_RE = re.compile(r"release:[A-Za-z0-9._-]{1,64}@[A-Za-z0-9._+-]{1,64}\Z")
_MAX_TEXT = 256
_MAX_CLAIMS = 1_000
_MAX_PAYLOAD_BYTES = 1_000_000
_MAX_COLLECTION_ITEMS = 10_000
_MAX_DEPTH = 16
_MAX_NODES = 20_000
_MAX_INTEGER = 2**63 - 1
_TERMINAL_FAILURES = frozenset({"timeout", "error", "corrupt"})


class ShadowContractError(ValueError):
    """Untrusted or incompatible shadow evidence."""


class ShadowDecisionAction(str, Enum):
    CONTINUE_OBSERVATION = "continue_observation"
    STOP = "stop"
    ELIGIBLE_FOR_OPERATOR_REVIEW = "eligible_for_operator_review"


class ShadowBlocker(str, Enum):
    POLICY_DIGEST_MISMATCH = "policy_digest_mismatch"
    MIXED_RELEASE_IDENTITY = "mixed_release_identity"
    DUPLICATE_OBSERVATION = "duplicate_observation"
    REPLAY_REQUEST_ID = "replay_request_id"
    REPLAY_INPUT_DIGEST = "replay_input_digest"
    PIT_AFTER_OBSERVATION = "pit_after_observation"
    PIT_OUTSIDE_WINDOW = "pit_outside_window"
    MISSING_STALE_OR_FUTURE = "missing_stale_or_future_observation"
    INSUFFICIENT_OBSERVATIONS = "insufficient_observations"
    INSUFFICIENT_COIN_COVERAGE = "insufficient_coin_coverage"
    INSUFFICIENT_QTYPE_COVERAGE = "insufficient_question_type_coverage"
    INCOMPLETE_SCENARIO_MATRIX = "incomplete_scenario_matrix"
    TERMINAL_TIMEOUT = "terminal_timeout"
    TERMINAL_ERROR = "terminal_error"
    TERMINAL_CORRUPT = "terminal_corrupt"
    PARITY_FAILURE = "parity_failure"
    PARITY_RATE = "parity_rate_below_policy"
    TERMINAL_STREAK = "terminal_failure_streak"
    LATENCY_EACH = "latency_each_exceeded"
    LATENCY_P95 = "latency_p95_exceeded"
    NONZERO_PROVIDER_OR_COST = "nonzero_provider_or_cost"


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value or len(value.encode()) > _MAX_TEXT:
        raise ShadowContractError(f"{name} must be non-empty and <= {_MAX_TEXT} bytes")
    return value


def _finite(value: float, name: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ShadowContractError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise ShadowContractError(f"{name} must be finite and >= {minimum}")
    return result


def canonical_json(value: Any) -> bytes:
    """Bounded canonical JSON with cycle/depth/node/integer defenses."""
    seen: set[int] = set()
    nodes = 0

    def validate(node: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > _MAX_NODES or depth > _MAX_DEPTH:
            raise ShadowContractError("canonical JSON exceeds structural limits")
        if isinstance(node, str):
            if len(node.encode()) > _MAX_TEXT:
                raise ShadowContractError("canonical JSON string exceeds size limit")
        elif isinstance(node, bool) or node is None:
            return
        elif isinstance(node, int):
            if abs(node) > _MAX_INTEGER:
                raise ShadowContractError("canonical JSON integer exceeds range")
        elif isinstance(node, float):
            if not math.isfinite(node):
                raise ShadowContractError("canonical JSON number must be finite")
        elif isinstance(node, Mapping):
            marker = id(node)
            if marker in seen:
                raise ShadowContractError("canonical JSON cycle detected")
            seen.add(marker)
            if len(node) > _MAX_COLLECTION_ITEMS or any(not isinstance(key, str) for key in node):
                raise ShadowContractError("canonical JSON object is invalid or oversized")
            for key, item in node.items():
                validate(key, depth + 1)
                validate(item, depth + 1)
            seen.remove(marker)
        elif isinstance(node, (list, tuple)):
            marker = id(node)
            if marker in seen:
                raise ShadowContractError("canonical JSON cycle detected")
            seen.add(marker)
            if len(node) > _MAX_COLLECTION_ITEMS:
                raise ShadowContractError("canonical JSON collection exceeds size limit")
            for item in node:
                validate(item, depth + 1)
            seen.remove(marker)
        else:
            raise ShadowContractError("value is not canonical JSON")

    validate(value, 0)
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
        ).encode()
    except (TypeError, ValueError, RecursionError) as exc:
        raise ShadowContractError("value is not canonical JSON") from exc
    if len(encoded) > _MAX_PAYLOAD_BYTES:
        raise ShadowContractError("canonical payload exceeds size limit")
    return encoded


def _digest(domain: bytes, value: Any) -> str:
    return "sha256:" + hashlib.sha256(domain + canonical_json(value)).hexdigest()


def input_digest(value: Any) -> str:
    return _digest(_INPUT_DOMAIN, value)


def policy_digest_value(value: Any) -> str:
    return _digest(_POLICY_DOMAIN, value)


def observation_digest(value: Any) -> str:
    return _digest(_OBSERVATION_DOMAIN, value)


def _require_digest(value: str, name: str) -> None:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ShadowContractError(f"{name} must be a lowercase sha256 digest")


@dataclass(frozen=True, slots=True)
class ShadowReleaseIdentity:
    active_release: str
    candidate_release: str
    active_artifact_digest: str
    candidate_artifact_digest: str
    policy_digest: str
    contract_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.active_release, str) or _RELEASE_RE.fullmatch(self.active_release) is None:
            raise ShadowContractError("active_release has invalid grammar")
        if not isinstance(self.candidate_release, str) or _RELEASE_RE.fullmatch(self.candidate_release) is None:
            raise ShadowContractError("candidate_release has invalid grammar")
        for name in ("active_artifact_digest", "candidate_artifact_digest", "policy_digest"):
            _require_digest(getattr(self, name), name)
        if self.active_release == self.candidate_release:
            raise ShadowContractError("active and candidate releases must differ")
        if self.active_artifact_digest == self.candidate_artifact_digest:
            raise ShadowContractError("active and candidate artifacts must differ")
        if self.contract_version != CONTRACT_VERSION:
            raise ShadowContractError("unsupported contract_version")


@dataclass(frozen=True, slots=True)
class ShadowInput:
    request_id: str
    coin: str
    question_type: str
    pit_epoch: float
    query: str

    def __post_init__(self) -> None:
        for name in ("request_id", "coin", "question_type", "query"):
            _text(getattr(self, name), name)
        _finite(self.pit_epoch, "pit_epoch")
        canonical_json(asdict(self))


@dataclass(frozen=True, slots=True)
class ShadowObservation:
    release_identity: ShadowReleaseIdentity
    canonical_input: ShadowInput
    input_digest: str
    observed_at: str
    status: str
    parity_passed: bool
    confidence_delta: float
    trust_delta: float
    supporting_jaccard: float
    elapsed_ms: float
    provider_calls: int
    cost_usd: float
    claim_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_digest(self.input_digest, "input_digest")
        if self.input_digest != input_digest(asdict(self.canonical_input)):
            raise ShadowContractError("input_digest does not match canonical_input")
        _parse_timestamp(self.observed_at)
        if self.status not in {"success", *_TERMINAL_FAILURES}:
            raise ShadowContractError("unknown observation status")
        if not isinstance(self.parity_passed, bool):
            raise ShadowContractError("parity_passed must be bool")
        for name in ("confidence_delta", "trust_delta", "elapsed_ms", "cost_usd"):
            _finite(getattr(self, name), name)
        if _finite(self.supporting_jaccard, "supporting_jaccard") > 1:
            raise ShadowContractError("supporting_jaccard must be <= 1")
        if isinstance(self.provider_calls, bool) or not isinstance(self.provider_calls, int):
            raise ShadowContractError("provider_calls must be int")
        if self.provider_calls < 0:
            raise ShadowContractError("provider_calls must be >= 0")
        if len(self.claim_ids) > _MAX_CLAIMS or len(set(self.claim_ids)) != len(self.claim_ids):
            raise ShadowContractError("claim_ids must be bounded and unique")
        for claim_id in self.claim_ids:
            _text(claim_id, "claim_id")
        canonical_json(to_dict(self))


@dataclass(frozen=True, slots=True)
class ShadowPolicy:
    version: str
    default_enabled: bool
    minimum_observations: int
    window_hours: int
    minimum_coins: int
    minimum_question_types: int
    minimum_per_cell: int
    confidence_delta_max: float
    trust_delta_max: float
    supporting_jaccard_min: float
    parity_rate_min: float
    terminal_failure_streak: int
    latency_p95_ms_max: float
    latency_each_ms_max: float
    provider_calls_max: int
    cost_usd_max: float

    def __post_init__(self) -> None:
        fixed = {
            "version": "shadow-policy.v1", "default_enabled": False,
            "minimum_observations": 30, "window_hours": 24, "minimum_coins": 3,
            "minimum_question_types": 2, "minimum_per_cell": 2,
            "confidence_delta_max": 0.05, "trust_delta_max": 0.05,
            "supporting_jaccard_min": 0.70, "parity_rate_min": 0.90,
            "terminal_failure_streak": 3, "latency_p95_ms_max": 250.0,
            "latency_each_ms_max": 1000.0, "provider_calls_max": 0, "cost_usd_max": 0.0,
        }
        if any(getattr(self, name) != expected for name, expected in fixed.items()):
            raise ShadowContractError("v1 policy values are immutable")


@dataclass(frozen=True, slots=True)
class ShadowAggregate:
    release_identity: ShadowReleaseIdentity
    observation_count: int
    coin_count: int
    question_type_count: int
    minimum_cell_count: int
    parity_rate: float
    terminal_failure_streak: int
    latency_p95_ms: float
    blockers: tuple[ShadowBlocker, ...]

    def __post_init__(self) -> None:
        for name in (
            "observation_count", "coin_count", "question_type_count",
            "minimum_cell_count", "terminal_failure_streak",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ShadowContractError(f"{name} must be a non-negative int")
        if _finite(self.parity_rate, "parity_rate") > 1:
            raise ShadowContractError("parity_rate must be <= 1")
        _finite(self.latency_p95_ms, "latency_p95_ms")
        if any(not isinstance(item, ShadowBlocker) for item in self.blockers):
            raise ShadowContractError("blockers must be typed ShadowBlocker values")
        if len(set(self.blockers)) != len(self.blockers):
            raise ShadowContractError("blockers must be unique")


@dataclass(frozen=True, slots=True)
class ShadowDecision:
    release_identity: ShadowReleaseIdentity
    action: ShadowDecisionAction
    aggregate: ShadowAggregate

    def __post_init__(self) -> None:
        if not isinstance(self.action, ShadowDecisionAction):
            raise ShadowContractError("action must be a ShadowDecisionAction")
        if self.release_identity != self.aggregate.release_identity:
            raise ShadowContractError("decision and aggregate identities differ")
        if self.action is ShadowDecisionAction.ELIGIBLE_FOR_OPERATOR_REVIEW and self.aggregate.blockers:
            raise ShadowContractError("eligible decision cannot contain blockers")
        if self.action is not ShadowDecisionAction.ELIGIBLE_FOR_OPERATOR_REVIEW and not self.aggregate.blockers:
            raise ShadowContractError("non-eligible decision requires blockers")


def _parse_timestamp(value: str) -> datetime:
    _text(value, "observed_at")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ShadowContractError("observed_at must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ShadowContractError("observed_at must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def to_dict(value: Any) -> dict[str, Any]:
    try:
        result = asdict(value)
    except (TypeError, RecursionError) as exc:
        raise ShadowContractError("contract cannot be serialized") from exc
    def jsonable(node: Any) -> Any:
        if isinstance(node, Enum):
            return node.value
        if isinstance(node, dict):
            return {key: jsonable(item) for key, item in node.items()}
        if isinstance(node, (list, tuple)):
            return [jsonable(item) for item in node]
        return node

    return jsonable(result)


def load_policy(path: Path = POLICY_PATH) -> ShadowPolicy:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ShadowContractError("policy file cannot be read") from exc
    if len(raw) > 16_384:
        raise ShadowContractError("policy file exceeds size limit")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ShadowContractError("invalid policy JSON") from exc
    if not isinstance(payload, Mapping) or set(payload) != set(ShadowPolicy.__dataclass_fields__):
        raise ShadowContractError("policy fields do not exactly match v1")
    try:
        return ShadowPolicy(**payload)
    except (TypeError, ValueError) as exc:
        if isinstance(exc, ShadowContractError):
            raise
        raise ShadowContractError("invalid policy values") from exc


def policy_digest(policy: ShadowPolicy) -> str:
    return policy_digest_value(to_dict(policy))


def evaluate_shadow(
    observations: Sequence[ShadowObservation],
    policy: ShadowPolicy,
    *,
    now: str,
) -> ShadowDecision:
    if not observations or len(observations) > _MAX_COLLECTION_ITEMS:
        raise ShadowContractError("observation window size is invalid")
    boundary = _parse_timestamp(now)
    release_identity = observations[0].release_identity
    blockers: list[ShadowBlocker] = []
    if release_identity.policy_digest != policy_digest(policy):
        blockers.append(ShadowBlocker.POLICY_DIGEST_MISMATCH)
    if any(item.release_identity != release_identity for item in observations):
        blockers.append(ShadowBlocker.MIXED_RELEASE_IDENTITY)
    fingerprints = [observation_digest(to_dict(item)) for item in observations]
    if len(set(fingerprints)) != len(fingerprints):
        blockers.append(ShadowBlocker.DUPLICATE_OBSERVATION)
    request_ids = [item.canonical_input.request_id for item in observations]
    if len(set(request_ids)) != len(request_ids):
        blockers.append(ShadowBlocker.REPLAY_REQUEST_ID)
    input_digests = [item.input_digest for item in observations]
    if len(set(input_digests)) != len(input_digests):
        blockers.append(ShadowBlocker.REPLAY_INPUT_DIGEST)
    ordered = sorted(observations, key=lambda item: _parse_timestamp(item.observed_at))
    cutoff_seconds = policy.window_hours * 3600
    fresh = [
        item for item in ordered
        if 0 <= (boundary - _parse_timestamp(item.observed_at)).total_seconds() <= cutoff_seconds
    ]
    if len(fresh) != len(ordered):
        blockers.append(ShadowBlocker.MISSING_STALE_OR_FUTURE)
    window_start = boundary.timestamp() - cutoff_seconds
    for item in observations:
        pit_epoch = item.canonical_input.pit_epoch
        observed_epoch = _parse_timestamp(item.observed_at).timestamp()
        if pit_epoch > observed_epoch:
            blockers.append(ShadowBlocker.PIT_AFTER_OBSERVATION)
        if pit_epoch < window_start or pit_epoch > boundary.timestamp():
            blockers.append(ShadowBlocker.PIT_OUTSIDE_WINDOW)
    if len(fresh) < policy.minimum_observations:
        blockers.append(ShadowBlocker.INSUFFICIENT_OBSERVATIONS)
    coins = {item.canonical_input.coin for item in fresh}
    qtypes = {item.canonical_input.question_type for item in fresh}
    if len(coins) < policy.minimum_coins:
        blockers.append(ShadowBlocker.INSUFFICIENT_COIN_COVERAGE)
    if len(qtypes) < policy.minimum_question_types:
        blockers.append(ShadowBlocker.INSUFFICIENT_QTYPE_COVERAGE)
    cells: dict[tuple[str, str], int] = {}
    for item in fresh:
        key = (item.canonical_input.coin, item.canonical_input.question_type)
        cells[key] = cells.get(key, 0) + 1
    complete_counts = [cells.get((coin, qtype), 0) for coin in coins for qtype in qtypes]
    minimum_cell = min(complete_counts, default=0)
    if minimum_cell < policy.minimum_per_cell:
        blockers.append(ShadowBlocker.INCOMPLETE_SCENARIO_MATRIX)
    for item in fresh:
        if item.status != "success":
            blockers.append(ShadowBlocker(f"terminal_{item.status}"))
        if item.elapsed_ms > policy.latency_each_ms_max:
            blockers.append(ShadowBlocker.LATENCY_EACH)
        if item.provider_calls or item.cost_usd:
            blockers.append(ShadowBlocker.NONZERO_PROVIDER_OR_COST)
    effective_parity = (
        item.status == "success"
        and item.parity_passed
        and item.confidence_delta <= policy.confidence_delta_max
        and item.trust_delta <= policy.trust_delta_max
        and item.supporting_jaccard >= policy.supporting_jaccard_min
        for item in fresh
    )
    parity_rate = sum(effective_parity) / max(len(fresh), 1)
    if parity_rate < policy.parity_rate_min:
        blockers.append(ShadowBlocker.PARITY_RATE)
    terminal_streak = 0
    for item in reversed(fresh):
        if item.status in _TERMINAL_FAILURES:
            terminal_streak += 1
        else:
            break
    if terminal_streak >= policy.terminal_failure_streak:
        blockers.append(ShadowBlocker.TERMINAL_STREAK)
    latencies = sorted(item.elapsed_ms for item in fresh)
    p95 = latencies[max(0, math.ceil(0.95 * len(latencies)) - 1)] if latencies else 0.0
    if p95 > policy.latency_p95_ms_max:
        blockers.append(ShadowBlocker.LATENCY_P95)
    unique = tuple(dict.fromkeys(blockers))
    aggregate = ShadowAggregate(
        release_identity=release_identity, observation_count=len(fresh),
        coin_count=len(coins), question_type_count=len(qtypes),
        minimum_cell_count=minimum_cell, parity_rate=parity_rate,
        terminal_failure_streak=terminal_streak, latency_p95_ms=p95, blockers=unique,
    )
    stopping = {
        ShadowBlocker.MIXED_RELEASE_IDENTITY, ShadowBlocker.DUPLICATE_OBSERVATION,
        ShadowBlocker.REPLAY_REQUEST_ID, ShadowBlocker.REPLAY_INPUT_DIGEST,
        ShadowBlocker.PIT_AFTER_OBSERVATION, ShadowBlocker.PIT_OUTSIDE_WINDOW,
        ShadowBlocker.NONZERO_PROVIDER_OR_COST, ShadowBlocker.TERMINAL_TIMEOUT,
        ShadowBlocker.TERMINAL_ERROR, ShadowBlocker.TERMINAL_CORRUPT,
    }
    action = (
        ShadowDecisionAction.STOP if stopping.intersection(unique)
        else ShadowDecisionAction.CONTINUE_OBSERVATION if unique
        else ShadowDecisionAction.ELIGIBLE_FOR_OPERATOR_REVIEW
    )
    return ShadowDecision(release_identity=release_identity, action=action, aggregate=aggregate)
