"""Issue #875 (sub-ticket G): asset-intrinsic promotion / non-inferiority gate.

This module is a **policy engine** (recommend-only).  It reads accumulated
asset-intrinsic shadow observations, checks them against thresholds that are
**versioned before evaluation**, and emits one content-addressed, machine-
readable decision receipt (``PASS`` / ``CONDITIONAL`` / ``BLOCK`` + ordered
reason codes).  It never activates, promotes, cuts over a release, mutates a
feature flag, or touches the official scorer / calibration / decision state /
direction / market judgment.

Physical isolation (AC for kernel parity): this module deliberately does **not**
import :class:`trustforge.agent.shadow_contracts.ShadowPolicy` /
``ShadowDecision`` / ``ShadowBlocker`` / ``evaluate_shadow``.  The intrinsic
promotion contract is an independent domain that mirrors their digest-bound,
versioned, fail-closed discipline without sharing their mutable enumerations.
"""

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

POLICY_VERSION = "intrinsic-promotion-policy.v1"
RECEIPT_DOMAIN_VERSION = "trustforge.intrinsic.promotion.receipt/v1"
POLICY_PATH = Path(__file__).resolve().parents[2] / "data" / "contracts" / "intrinsic-promotion-policy.v1.json"

_POLICY_DOMAIN = b"trustforge.intrinsic.promotion.policy.v1\x00"
_OBSERVATION_DOMAIN = b"trustforge.intrinsic.promotion.observation.v1\x00"
_ROOT_DOMAIN = b"trustforge.intrinsic.promotion.observation-root.v1\x00"
_RECEIPT_DOMAIN = b"trustforge.intrinsic.promotion.receipt.v1\x00"

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_MAX_TEXT = 1024
_MAX_COLLECTION_ITEMS = 10_000
_MAX_DEPTH = 16
_MAX_NODES = 50_000
_MAX_INTEGER = 2**63 - 1
_MAX_PAYLOAD_BYTES = 2_000_000
_MISSING_DIM_STATUSES = frozenset({"unknown", "stale", "conflicted", "unavailable"})


class IntrinsicPromotionError(ValueError):
    """Untrusted or incompatible intrinsic promotion evidence."""


class IntrinsicPromotionDecision(str, Enum):
    PASS = "pass"
    CONDITIONAL = "conditional"
    BLOCK = "block"


class IntrinsicPromotionReason(str, Enum):
    POLICY_UNVERSIONED = "policy_unversioned"
    RECEIPT_MALFORMED = "receipt_malformed"
    INSUFFICIENT_OBSERVATIONS = "insufficient_observations"
    INSUFFICIENT_ASSET_COVERAGE = "insufficient_asset_coverage"
    INSUFFICIENT_OBSERVATION_SPAN = "insufficient_observation_span"
    INELIGIBLE_ASSESSMENT_IN_WINDOW = "ineligible_assessment_in_window"
    INSUFFICIENT_ELIGIBLE_FRACTION = "insufficient_eligible_fraction"
    DELTA_EXCEEDS_NON_INFERIORITY_MARGIN = "delta_exceeds_non_inferiority_margin"
    CORRUPT_OBSERVATIONS = "corrupt_observations"
    IDENTICAL_FACTS_DIVERGENT_DELTA = "identical_facts_divergent_delta"
    DIRECTION_OR_DECISION_FLIP = "direction_or_decision_flip"
    COVERAGE_DISPARITY = "coverage_disparity"
    MISSINGNESS_RATE_EXCEEDED = "missingness_rate_exceeded"
    SENSITIVITY_OUT_OF_BOUND = "sensitivity_out_of_bound"
    SINGLE_SOURCE_DEPENDENCY = "single_source_dependency"
    CALIBRATION_REGRESSION = "calibration_regression"


# ---------------------------------------------------------------------------
# Canonical JSON (independent of the parity kernel; bounded + fail-closed).
# ---------------------------------------------------------------------------


def canonical_json(value: Any) -> bytes:
    """Bounded canonical JSON with structural defenses (sort_keys, finite-only)."""
    nodes = 0

    def validate(node: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > _MAX_NODES or depth > _MAX_DEPTH:
            raise IntrinsicPromotionError("canonical JSON exceeds structural limits")
        if isinstance(node, str):
            if len(node.encode()) > _MAX_TEXT:
                raise IntrinsicPromotionError("canonical JSON string exceeds size limit")
        elif isinstance(node, bool) or node is None:
            return
        elif isinstance(node, int):
            if abs(node) > _MAX_INTEGER:
                raise IntrinsicPromotionError("canonical JSON integer exceeds range")
        elif isinstance(node, float):
            if not math.isfinite(node):
                raise IntrinsicPromotionError("canonical JSON number must be finite")
        elif isinstance(node, Mapping):
            if len(node) > _MAX_COLLECTION_ITEMS or any(
                not isinstance(key, str) for key in node
            ):
                raise IntrinsicPromotionError("canonical JSON object is invalid or oversized")
            for key, item in node.items():
                validate(key, depth + 1)
                validate(item, depth + 1)
        elif isinstance(node, (list, tuple)):
            if len(node) > _MAX_COLLECTION_ITEMS:
                raise IntrinsicPromotionError("canonical JSON collection exceeds size limit")
            for item in node:
                validate(item, depth + 1)
        else:
            raise IntrinsicPromotionError("value is not canonical JSON")

    validate(value, 0)
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError, RecursionError) as exc:
        raise IntrinsicPromotionError("value is not canonical JSON") from exc
    if len(encoded) > _MAX_PAYLOAD_BYTES:
        raise IntrinsicPromotionError("canonical payload exceeds size limit")
    return encoded


def _digest(domain: bytes, value: Any) -> str:
    return "sha256:" + hashlib.sha256(domain + canonical_json(value)).hexdigest()


def _require_digest(value: str, name: str) -> None:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise IntrinsicPromotionError(f"{name} must be a lowercase sha256 digest")


# ---------------------------------------------------------------------------
# D1: versioned promotion policy (thresholds versioned before evaluation, AC6).
# ---------------------------------------------------------------------------


# The v1 fixed values.  Any drift here is a version change: the policy_digest
# changes, which forces a new receipt_id for every evaluation (AC7).  Mirrors
# the parity kernel's immutable-v1 discipline without importing it.
_V1_FIXED: Mapping[str, Any] = {
    "version": POLICY_VERSION,
    "min_observations": 200,
    "min_assets": 5,
    "min_days": 30,
    "min_known": 3,
    "min_families": 2,
    "max_abs_delta": 0.08,
    "brier_degradation_limit": 0.01,
    "ece_degradation_limit": 0.01,
    "labels_mature": False,
    "min_eligible_fraction": 0.6,
    "max_decision_flips": 0,
    "max_coverage_disparity": 2,
    "max_missingness_rate": 0.5,
    "sensitivity_bound": 0.08,
    "max_single_source_family_share": 0.6,
    "corrupt_rate_max": 0.05,
}


@dataclass(frozen=True, slots=True)
class IntrinsicPromotionPolicy:
    """Frozen, versioned thresholds for the intrinsic promotion gate."""

    version: str
    min_observations: int
    min_assets: int
    min_days: int
    min_known: int
    min_families: int
    max_abs_delta: float
    brier_degradation_limit: float
    ece_degradation_limit: float
    labels_mature: bool
    min_eligible_fraction: float
    max_decision_flips: int
    max_coverage_disparity: int
    max_missingness_rate: float
    sensitivity_bound: float
    max_single_source_family_share: float
    corrupt_rate_max: float

    def __post_init__(self) -> None:
        # Type + sane-range validation only.  The exact v1 values are enforced
        # tamper-evidently by :func:`load_intrinsic_promotion_policy` (the
        # production file path).  Keeping construction permissive lets a future
        # version (or an audit/test) build an alternative threshold set while
        # the content-addressed :func:`policy_digest` makes any drift visible:
        # different values -> different digest -> different receipt_id (AC7).
        if self.version != POLICY_VERSION:
            raise IntrinsicPromotionError(f"version must be {POLICY_VERSION!r}")
        for name in ("min_observations", "min_assets", "min_days", "min_known", "min_families"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise IntrinsicPromotionError(f"{name} must be a positive int")
        if isinstance(self.max_decision_flips, bool) or not isinstance(self.max_decision_flips, int):
            raise IntrinsicPromotionError("max_decision_flips must be an int")
        if isinstance(self.max_coverage_disparity, bool) or not isinstance(
            self.max_coverage_disparity, int
        ) or self.max_coverage_disparity < 0:
            raise IntrinsicPromotionError("max_coverage_disparity must be a non-negative int")
        for name in (
            "max_abs_delta",
            "brier_degradation_limit",
            "ece_degradation_limit",
            "min_eligible_fraction",
            "max_missingness_rate",
            "sensitivity_bound",
            "max_single_source_family_share",
            "corrupt_rate_max",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise IntrinsicPromotionError(f"{name} must be numeric")
            if not math.isfinite(float(value)) or float(value) < 0:
                raise IntrinsicPromotionError(f"{name} must be finite and >= 0")
        if not 0.0 <= float(self.min_eligible_fraction) <= 1.0:
            raise IntrinsicPromotionError("min_eligible_fraction must be within [0, 1]")
        if not 0.0 <= float(self.max_missingness_rate) <= 1.0:
            raise IntrinsicPromotionError("max_missingness_rate must be within [0, 1]")
        if not 0.0 <= float(self.max_single_source_family_share) <= 1.0:
            raise IntrinsicPromotionError("max_single_source_family_share must be within [0, 1]")
        if not 0.0 <= float(self.corrupt_rate_max) <= 1.0:
            raise IntrinsicPromotionError("corrupt_rate_max must be within [0, 1]")
        if not isinstance(self.labels_mature, bool):
            raise IntrinsicPromotionError("labels_mature must be bool")
        canonical_json(policy_to_dict(self))


def policy_to_dict(policy: IntrinsicPromotionPolicy) -> dict[str, Any]:
    return asdict(policy)


def policy_digest(policy: IntrinsicPromotionPolicy) -> str:
    """Content-addressed digest over the versioned thresholds (AC6)."""
    return _digest(_POLICY_DOMAIN, policy_to_dict(policy))


def load_intrinsic_promotion_policy(
    path: Path = POLICY_PATH,
) -> IntrinsicPromotionPolicy:
    """Load and validate the versioned policy file.

    The on-disk field set must exactly match the v1 schema; any missing,
    extra, or mismatched value fails closed (AC6 version-immutability).
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise IntrinsicPromotionError("policy file cannot be read") from exc
    if len(raw) > 16_384:
        raise IntrinsicPromotionError("policy file exceeds size limit")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise IntrinsicPromotionError("invalid policy JSON") from exc
    if not isinstance(payload, Mapping):
        raise IntrinsicPromotionError("policy payload must be an object")
    expected_fields = set(IntrinsicPromotionPolicy.__dataclass_fields__)
    if set(payload) != expected_fields:
        raise IntrinsicPromotionError("policy fields do not exactly match v1")
    for name, expected in _V1_FIXED.items():
        if payload.get(name) != expected:
            raise IntrinsicPromotionError(
                f"v1 policy field {name!r} is tamper-evident: expected {expected!r}"
            )
    try:
        return IntrinsicPromotionPolicy(**payload)
    except (TypeError, ValueError) as exc:
        if isinstance(exc, IntrinsicPromotionError):
            raise
        raise IntrinsicPromotionError("invalid policy values") from exc


# ---------------------------------------------------------------------------
# D2: receipt + pure gate engine.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IntrinsicPromotionReceipt:
    """Content-addressed, append-only decision receipt (AC7).

    Field order is deliberate: ``policy_digest`` precedes every result field so
    the versioned thresholds are provably fixed before any evaluation number is
    materialized (AC6).  ``receipt_id`` is derived from the canonical form, so
    mutating any decision-relevant field forces a new identity.
    """

    receipt_domain_version: str
    policy_digest: str
    observation_root_digest: str
    benchmark_manifest_digest: str
    evaluated_at: str
    policy: Mapping[str, Any]
    decision: IntrinsicPromotionDecision
    reasons: tuple[IntrinsicPromotionReason, ...]
    calibration_claim: str
    counts: Mapping[str, Any]

    def __post_init__(self) -> None:
        _require_digest(self.policy_digest, "policy_digest")
        _require_digest(self.observation_root_digest, "observation_root_digest")
        _require_digest(self.benchmark_manifest_digest, "benchmark_manifest_digest")
        if not isinstance(self.decision, IntrinsicPromotionDecision):
            raise IntrinsicPromotionError("decision must be IntrinsicPromotionDecision")
        if any(not isinstance(r, IntrinsicPromotionReason) for r in self.reasons):
            raise IntrinsicPromotionError("reasons must be IntrinsicPromotionReason values")
        if len(set(self.reasons)) != len(self.reasons):
            raise IntrinsicPromotionError("reasons must be unique")
        if self.decision is IntrinsicPromotionDecision.PASS and self.reasons:
            raise IntrinsicPromotionError("PASS decision cannot carry reasons")
        if self.decision is not IntrinsicPromotionDecision.BLOCK and any(
            r
            in {
                IntrinsicPromotionReason.POLICY_UNVERSIONED,
                IntrinsicPromotionReason.RECEIPT_MALFORMED,
                IntrinsicPromotionReason.INSUFFICIENT_OBSERVATIONS,
                IntrinsicPromotionReason.INSUFFICIENT_ASSET_COVERAGE,
                IntrinsicPromotionReason.INSUFFICIENT_OBSERVATION_SPAN,
                IntrinsicPromotionReason.DELTA_EXCEEDS_NON_INFERIORITY_MARGIN,
                IntrinsicPromotionReason.CORRUPT_OBSERVATIONS,
                IntrinsicPromotionReason.IDENTICAL_FACTS_DIVERGENT_DELTA,
                IntrinsicPromotionReason.DIRECTION_OR_DECISION_FLIP,
                IntrinsicPromotionReason.COVERAGE_DISPARITY,
                IntrinsicPromotionReason.MISSINGNESS_RATE_EXCEEDED,
                IntrinsicPromotionReason.SENSITIVITY_OUT_OF_BOUND,
                IntrinsicPromotionReason.SINGLE_SOURCE_DEPENDENCY,
                IntrinsicPromotionReason.CALIBRATION_REGRESSION,
            }
            for r in self.reasons
        ):
            raise IntrinsicPromotionError("hard-block reasons require BLOCK decision")
        canonical_json(receipt_canonical_dict(self))


# AC7: BLOCK->PASS is impossible by construction.  The decision is a pure
# function of (policy, observations, manifest_digest, evaluated_at); the only
# way to change a decision is to change an input, which changes policy_digest
# or observation_root_digest, which changes receipt_id.  There is no setter.
BLOCK_REASONS = frozenset({
    IntrinsicPromotionReason.POLICY_UNVERSIONED,
    IntrinsicPromotionReason.RECEIPT_MALFORMED,
    IntrinsicPromotionReason.INSUFFICIENT_OBSERVATIONS,
    IntrinsicPromotionReason.INSUFFICIENT_ASSET_COVERAGE,
    IntrinsicPromotionReason.INSUFFICIENT_OBSERVATION_SPAN,
    IntrinsicPromotionReason.INELIGIBLE_ASSESSMENT_IN_WINDOW,
    IntrinsicPromotionReason.INSUFFICIENT_ELIGIBLE_FRACTION,
    IntrinsicPromotionReason.DELTA_EXCEEDS_NON_INFERIORITY_MARGIN,
    IntrinsicPromotionReason.CORRUPT_OBSERVATIONS,
    IntrinsicPromotionReason.IDENTICAL_FACTS_DIVERGENT_DELTA,
    IntrinsicPromotionReason.DIRECTION_OR_DECISION_FLIP,
    IntrinsicPromotionReason.COVERAGE_DISPARITY,
    IntrinsicPromotionReason.MISSINGNESS_RATE_EXCEEDED,
    IntrinsicPromotionReason.SENSITIVITY_OUT_OF_BOUND,
    IntrinsicPromotionReason.SINGLE_SOURCE_DEPENDENCY,
    IntrinsicPromotionReason.CALIBRATION_REGRESSION,
})


def receipt_canonical_dict(receipt: IntrinsicPromotionReceipt) -> dict[str, Any]:
    """Insertion-ordered dict: policy fields precede result fields (AC6)."""
    return {
        "receipt_domain_version": receipt.receipt_domain_version,
        "policy_digest": receipt.policy_digest,
        "observation_root_digest": receipt.observation_root_digest,
        "benchmark_manifest_digest": receipt.benchmark_manifest_digest,
        "evaluated_at": receipt.evaluated_at,
        "policy": dict(receipt.policy),
        "decision": receipt.decision.value,
        "reasons": [r.value for r in receipt.reasons],
        "calibration_claim": receipt.calibration_claim,
        "counts": dict(receipt.counts),
    }


def receipt_digest(receipt: IntrinsicPromotionReceipt) -> str:
    return _digest(_RECEIPT_DOMAIN, receipt_canonical_dict(receipt))


def receipt_id(receipt: IntrinsicPromotionReceipt) -> str:
    """Alias of :func:`receipt_digest` (content-addressed identity, AC7)."""
    return receipt_digest(receipt)


def serialize_receipt(receipt: IntrinsicPromotionReceipt) -> str:
    """Human-readable, insertion-order JSON for the commit-bound artifact.

    Uses ``sort_keys=False`` so the on-disk byte order keeps ``policy_digest``
    ahead of every result field (T4 byte-position invariant).  The digest is
    computed over the canonical (sorted) form in :func:`receipt_digest`, so
    stability is unaffected.
    """
    payload = receipt_canonical_dict(receipt)
    return json.dumps(payload, indent=2, sort_keys=False, ensure_ascii=False) + "\n"


def _parse_ts(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise IntrinsicPromotionError("timestamp must be a non-empty string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise IntrinsicPromotionError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise IntrinsicPromotionError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IntrinsicPromotionError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise IntrinsicPromotionError(f"{name} must be finite")
    return result


@dataclass(frozen=True, slots=True)
class _ObsRecord:
    """Defensively-extracted projection of one intrinsic observation."""

    raw: Mapping[str, Any]
    asset_id: str
    observed_at: datetime
    total_delta: float
    facts_hash: str
    gate_passed: bool
    known_count: int
    source_family_count: int
    families: tuple[str, ...]
    dim_statuses: tuple[str, ...]
    corrupt: bool


def _extract_observation(raw: Any) -> _ObsRecord:
    """Project one observation payload defensively; mark corrupt on any defect.

    Fail-closed (AC3): a corrupt observation never raises out of the gate; it
    is counted and, if the corrupt rate exceeds policy, blocks the window.
    """
    corrupt = False
    if not isinstance(raw, Mapping):
        return _corrupt_record(raw)
    try:
        asset_id = raw.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id:
            corrupt = True
            asset_id = ""
        ts_value = raw.get("observed_at") or raw.get("as_of")
        observed_at = _parse_ts(ts_value) if isinstance(ts_value, str) and ts_value else None
        if observed_at is None:
            corrupt = True
            observed_at = datetime.fromtimestamp(0, tz=timezone.utc)
        total_delta_raw = raw.get("total_delta")
        try:
            total_delta = _finite(total_delta_raw, "total_delta")
        except IntrinsicPromotionError:
            corrupt = True
            total_delta = 0.0
        facts_hash = raw.get("facts_hash")
        if not isinstance(facts_hash, str) or not facts_hash:
            corrupt = True
            facts_hash = ""
        gate = raw.get("gate")
        if not isinstance(gate, Mapping):
            corrupt = True
            gate_passed = False
            known_count = 0
            source_family_count = 0
        else:
            gate_passed = bool(gate.get("passed", False))
            known_count = _coerce_int(gate.get("known_count"))
            source_family_count = _coerce_int(gate.get("source_family_count"))
        families = _extract_families(raw)
        dim_statuses = _extract_dim_statuses(raw)
        return _ObsRecord(
            raw=dict(raw),
            asset_id=asset_id,
            observed_at=observed_at,
            total_delta=total_delta,
            facts_hash=facts_hash,
            gate_passed=gate_passed,
            known_count=known_count,
            source_family_count=source_family_count,
            families=families,
            dim_statuses=dim_statuses,
            corrupt=corrupt,
        )
    except Exception:
        return _corrupt_record(raw)


def _corrupt_record(raw: Any) -> _ObsRecord:
    return _ObsRecord(
        raw=raw if isinstance(raw, Mapping) else {"_unparseable": str(type(raw))},
        asset_id="",
        observed_at=datetime.fromtimestamp(0, tz=timezone.utc),
        total_delta=0.0,
        facts_hash="",
        gate_passed=False,
        known_count=0,
        source_family_count=0,
        families=(),
        dim_statuses=(),
        corrupt=True,
    )


def _coerce_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _extract_families(raw: Mapping[str, Any]) -> tuple[str, ...]:
    families: set[str] = set()
    dimensions = raw.get("dimensions")
    if not isinstance(dimensions, (list, tuple)):
        return ()
    for dim in dimensions:
        if not isinstance(dim, Mapping):
            continue
        provenance = dim.get("provenance")
        if not isinstance(provenance, Mapping):
            continue
        urls = provenance.get("source_urls")
        if not isinstance(urls, (list, tuple)):
            continue
        for url in urls:
            if isinstance(url, str) and url:
                families.add(url)
    return tuple(sorted(families))


def _extract_dim_statuses(raw: Mapping[str, Any]) -> tuple[str, ...]:
    dimensions = raw.get("dimensions")
    if not isinstance(dimensions, (list, tuple)):
        return ()
    statuses: list[str] = []
    for dim in dimensions:
        if isinstance(dim, Mapping):
            status = dim.get("status")
            statuses.append(status if isinstance(status, str) else "unknown")
    return tuple(statuses)


def _obs_projection(record: _ObsRecord) -> dict[str, Any]:
    """Finite, JSON-safe projection of one observation for digesting.

    The raw payload may carry non-finite values (corrupt observations); this
    projection normalizes them so the root digest never raises out of the gate
    (fail-closed, AC3).  The projection is deterministic and observation-bound.
    """
    return {
        "asset_id": record.asset_id,
        "observed_at": record.observed_at.isoformat().replace("+00:00", "Z"),
        "total_delta": record.total_delta,
        "facts_hash": record.facts_hash,
        "gate_passed": record.gate_passed,
        "known_count": record.known_count,
        "source_family_count": record.source_family_count,
        "families": list(record.families),
        "dim_statuses": list(record.dim_statuses),
        "corrupt": record.corrupt,
    }


def observation_event_digest(raw_observation: Mapping[str, Any]) -> str:
    """Per-observation digest over the observation domain (raw payload)."""
    return _digest(_OBSERVATION_DOMAIN, dict(raw_observation))


def _observation_root_digest(records: Sequence[_ObsRecord]) -> str:
    ordered = sorted(
        records,
        key=lambda r: (r.observed_at, r.asset_id, r.facts_hash),
    )
    leaves = "".join(
        _digest(_OBSERVATION_DOMAIN, _obs_projection(r)) for r in ordered
    )
    return "sha256:" + hashlib.sha256(
        _ROOT_DOMAIN + leaves.encode("utf-8")
    ).hexdigest()


def evaluate_promotion(
    policy: IntrinsicPromotionPolicy,
    observations: Sequence[Mapping[str, Any]],
    *,
    benchmark_manifest_digest: str,
    now: str,
    sensitivity_report: Mapping[str, Any] | None = None,
    calibration: Mapping[str, Any] | None = None,
) -> IntrinsicPromotionReceipt:
    """Pure gate: ``policy + observations + manifest -> receipt``.

    No file, network, or DB access.  Thresholds are versioned
    (:func:`policy_digest`) **before** any observation is read (AC6).  Every
    defect degrades to a BLOCK reason rather than raising (fail-closed, AC3).
    """
    # AC6: first action fixes the versioned policy identity.
    try:
        pdig = policy_digest(policy)
    except Exception:
        return _malformed_receipt(
            benchmark_manifest_digest, now, policy_snapshot={}, policy_unversioned=True
        )

    _require_digest(benchmark_manifest_digest, "benchmark_manifest_digest")
    evaluated_at = _parse_ts(now).isoformat().replace("+00:00", "Z")

    if not isinstance(observations, Sequence) or isinstance(observations, (str, bytes)):
        records: list[_ObsRecord] = []
    else:
        records = [_extract_observation(obs) for obs in observations]

    root_digest = _observation_root_digest(records)

    reasons: list[IntrinsicPromotionReason] = []

    total = len(records)
    corrupt_count = sum(1 for r in records if r.corrupt)
    valid_records = [r for r in records if not r.corrupt]

    asset_ids = {r.asset_id for r in valid_records if r.asset_id}
    asset_count = len(asset_ids)

    timestamps = [r.observed_at for r in valid_records]
    if timestamps:
        day_span = (max(timestamps) - min(timestamps)).days
    else:
        day_span = 0

    # --- AC1: minimum evidence ---
    if total < policy.min_observations:
        reasons.append(IntrinsicPromotionReason.INSUFFICIENT_OBSERVATIONS)
    if asset_count < policy.min_assets:
        reasons.append(IntrinsicPromotionReason.INSUFFICIENT_ASSET_COVERAGE)
    if day_span < policy.min_days:
        reasons.append(IntrinsicPromotionReason.INSUFFICIENT_OBSERVATION_SPAN)

    # --- AC2: per-eligible coverage + eligible fraction ---
    eligible = [r for r in valid_records if r.gate_passed]
    for r in eligible:
        if r.known_count < policy.min_known or r.source_family_count < policy.min_families:
            reasons.append(IntrinsicPromotionReason.INELIGIBLE_ASSESSMENT_IN_WINDOW)
            break
    eligible_fraction = (len(eligible) / total) if total else 0.0
    if total and eligible_fraction < policy.min_eligible_fraction:
        reasons.append(IntrinsicPromotionReason.INSUFFICIENT_ELIGIBLE_FRACTION)

    # --- AC3: non-inferiority margin + corrupt rate + identity invariant ---
    for r in valid_records:
        if abs(r.total_delta) > policy.max_abs_delta:
            reasons.append(IntrinsicPromotionReason.DELTA_EXCEEDS_NON_INFERIORITY_MARGIN)
            break
    corrupt_rate = (corrupt_count / total) if total else 0.0
    if corrupt_count > 0:
        if corrupt_rate > policy.corrupt_rate_max:
            reasons.append(IntrinsicPromotionReason.CORRUPT_OBSERVATIONS)
    # Identity-invariant (core metamorphic, AC3): identical facts_hash across
    # symbols must yield byte-equal total_delta.  Divergence is a structural
    # honesty signal -> immediate BLOCK.
    facts_to_delta: dict[str, float] = {}
    facts_conflict = False
    for r in valid_records:
        if not r.facts_hash:
            continue
        if r.facts_hash in facts_to_delta:
            if facts_to_delta[r.facts_hash] != r.total_delta:
                facts_conflict = True
                break
        else:
            facts_to_delta[r.facts_hash] = r.total_delta
    if facts_conflict:
        reasons.append(IntrinsicPromotionReason.IDENTICAL_FACTS_DIVERGENT_DELTA)

    # --- AC4: stop conditions ---
    _check_decision_flips(valid_records, policy, reasons)
    _check_coverage_disparity(valid_records, policy, reasons)
    _check_missingness(valid_records, policy, reasons)
    _check_sensitivity(sensitivity_report, policy, reasons)
    _check_single_source(valid_records, policy, reasons)

    # --- AC5: calibration ---
    calibration_claim = _evaluate_calibration(policy, calibration, reasons)

    # Dedupe reasons preserving first-seen order.
    seen: set[IntrinsicPromotionReason] = set()
    unique_reasons: list[IntrinsicPromotionReason] = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            unique_reasons.append(r)

    if unique_reasons:
        decision = IntrinsicPromotionDecision.BLOCK
    elif policy.labels_mature:
        decision = IntrinsicPromotionDecision.PASS
    else:
        decision = IntrinsicPromotionDecision.CONDITIONAL

    counts = {
        "observation_count": total,
        "valid_count": len(valid_records),
        "corrupt_count": corrupt_count,
        "asset_count": asset_count,
        "day_span": day_span,
        "eligible_count": len(eligible),
        "eligible_fraction": round(eligible_fraction, 8),
        "corrupt_rate": round(corrupt_rate, 8),
        "known_count_min": min((r.known_count for r in valid_records), default=0),
        "known_count_max": max((r.known_count for r in valid_records), default=0),
        "source_family_count_min": min(
            (r.source_family_count for r in valid_records), default=0
        ),
        "distinct_family_count": len({f for r in valid_records for f in r.families}),
        "decision_flips": _count_decision_flips(valid_records),
        "missingness_rate": round(_missingness_rate(valid_records), 8),
    }

    receipt = IntrinsicPromotionReceipt(
        receipt_domain_version=RECEIPT_DOMAIN_VERSION,
        policy_digest=pdig,
        observation_root_digest=root_digest,
        benchmark_manifest_digest=benchmark_manifest_digest,
        evaluated_at=evaluated_at,
        policy=policy_to_dict(policy),
        decision=decision,
        reasons=tuple(unique_reasons),
        calibration_claim=calibration_claim,
        counts=counts,
    )
    return receipt


def _malformed_receipt(
    benchmark_manifest_digest: str,
    now: str,
    *,
    policy_snapshot: Mapping[str, Any],
    policy_unversioned: bool,
) -> IntrinsicPromotionReceipt:
    digest = benchmark_manifest_digest if (
        isinstance(benchmark_manifest_digest, str)
        and _DIGEST_RE.fullmatch(benchmark_manifest_digest)
    ) else "sha256:" + "0" * 64
    try:
        evaluated_at = _parse_ts(now).isoformat().replace("+00:00", "Z")
    except IntrinsicPromotionError:
        evaluated_at = "1970-01-01T00:00:00Z"
    reasons = [IntrinsicPromotionReason.RECEIPT_MALFORMED]
    if policy_unversioned:
        reasons.insert(0, IntrinsicPromotionReason.POLICY_UNVERSIONED)
    return IntrinsicPromotionReceipt(
        receipt_domain_version=RECEIPT_DOMAIN_VERSION,
        policy_digest="sha256:" + "0" * 64,
        observation_root_digest="sha256:" + "0" * 64,
        benchmark_manifest_digest=digest,
        evaluated_at=evaluated_at,
        policy=dict(policy_snapshot),
        decision=IntrinsicPromotionDecision.BLOCK,
        reasons=tuple(reasons),
        calibration_claim="withheld_no_mature_labels",
        counts={
            "observation_count": 0,
            "valid_count": 0,
            "corrupt_count": 0,
            "asset_count": 0,
            "day_span": 0,
            "eligible_count": 0,
            "eligible_fraction": 0.0,
            "corrupt_rate": 0.0,
            "known_count_min": 0,
            "known_count_max": 0,
            "source_family_count_min": 0,
            "distinct_family_count": 0,
            "decision_flips": 0,
            "missingness_rate": 0.0,
        },
    )


def _count_decision_flips(records: Sequence[_ObsRecord]) -> int:
    flips = 0
    per_asset: dict[str, list[_ObsRecord]] = {}
    for r in records:
        if not r.asset_id:
            continue
        per_asset.setdefault(r.asset_id, []).append(r)
    for sequence in per_asset.values():
        sequence.sort(key=lambda r: r.observed_at)
        signs = [ (1 if r.total_delta > 0 else -1 if r.total_delta < 0 else 0) for r in sequence ]
        last: int | None = None
        for s in signs:
            if s != 0:
                if last is not None and s != last:
                    flips += 1
                last = s
    return flips


def _check_decision_flips(
    records: Sequence[_ObsRecord],
    policy: IntrinsicPromotionPolicy,
    reasons: list[IntrinsicPromotionReason],
) -> None:
    if _count_decision_flips(records) > policy.max_decision_flips:
        reasons.append(IntrinsicPromotionReason.DIRECTION_OR_DECISION_FLIP)


def _check_coverage_disparity(
    records: Sequence[_ObsRecord],
    policy: IntrinsicPromotionPolicy,
    reasons: list[IntrinsicPromotionReason],
) -> None:
    per_asset_max: dict[str, int] = {}
    for r in records:
        if not r.asset_id:
            continue
        per_asset_max[r.asset_id] = max(per_asset_max.get(r.asset_id, 0), r.known_count)
    if per_asset_max:
        disparity = max(per_asset_max.values()) - min(per_asset_max.values())
        if disparity > policy.max_coverage_disparity:
            reasons.append(IntrinsicPromotionReason.COVERAGE_DISPARITY)


def _missingness_rate(records: Sequence[_ObsRecord]) -> float:
    total_dims = 0
    missing_dims = 0
    for r in records:
        for status in r.dim_statuses:
            total_dims += 1
            if status in _MISSING_DIM_STATUSES:
                missing_dims += 1
    return (missing_dims / total_dims) if total_dims else 0.0


def _check_missingness(
    records: Sequence[_ObsRecord],
    policy: IntrinsicPromotionPolicy,
    reasons: list[IntrinsicPromotionReason],
) -> None:
    if _missingness_rate(records) > policy.max_missingness_rate:
        reasons.append(IntrinsicPromotionReason.MISSINGNESS_RATE_EXCEEDED)


def _check_sensitivity(
    sensitivity_report: Mapping[str, Any] | None,
    policy: IntrinsicPromotionPolicy,
    reasons: list[IntrinsicPromotionReason],
) -> None:
    """Sensitivity stop condition (AC4): bound via the benchmark manifest digest.

    The gate consumes the extracted signal (not the benchmark itself); the
    receipt is bound to the exact benchmark run through
    ``benchmark_manifest_digest`` so a stale sensitivity probe is visible.
    """
    if not isinstance(sensitivity_report, Mapping):
        return
    if sensitivity_report.get("out_of_bound") is True:
        reasons.append(IntrinsicPromotionReason.SENSITIVITY_OUT_OF_BOUND)
        return
    max_abs = sensitivity_report.get("max_abs_response")
    if isinstance(max_abs, (int, float)) and not isinstance(max_abs, bool):
        if math.isfinite(float(max_abs)) and abs(float(max_abs)) > policy.sensitivity_bound:
            reasons.append(IntrinsicPromotionReason.SENSITIVITY_OUT_OF_BOUND)


def _check_single_source(
    records: Sequence[_ObsRecord],
    policy: IntrinsicPromotionPolicy,
    reasons: list[IntrinsicPromotionReason],
) -> None:
    family_counts: dict[str, int] = {}
    total = 0
    for r in records:
        if not r.gate_passed:
            continue
        for family in r.families:
            family_counts[family] = family_counts.get(family, 0) + 1
            total += 1
    if not total:
        return
    dominant_share = max(family_counts.values()) / total
    if dominant_share > policy.max_single_source_family_share:
        reasons.append(IntrinsicPromotionReason.SINGLE_SOURCE_DEPENDENCY)


def _evaluate_calibration(
    policy: IntrinsicPromotionPolicy,
    calibration: Mapping[str, Any] | None,
    reasons: list[IntrinsicPromotionReason],
) -> str:
    """AC5: no-mature-labels locks the wording; regression blocks."""
    if not policy.labels_mature:
        return "withheld_no_mature_labels"
    if not isinstance(calibration, Mapping):
        reasons.append(IntrinsicPromotionReason.RECEIPT_MALFORMED)
        return "withheld_no_mature_labels"
    try:
        brier_delta = _finite(calibration.get("brier_delta"), "brier_delta")
        ece_delta = _finite(calibration.get("ece_delta"), "ece_delta")
    except IntrinsicPromotionError:
        reasons.append(IntrinsicPromotionReason.RECEIPT_MALFORMED)
        return "withheld_no_mature_labels"
    if brier_delta > policy.brier_degradation_limit or ece_delta > policy.ece_degradation_limit:
        reasons.append(IntrinsicPromotionReason.CALIBRATION_REGRESSION)
        return "calibration_regression_detected"
    return "verified_no_regression"


# ---------------------------------------------------------------------------
# D4: dataset-bound adapter.  Builds intrinsic observations from the real
# checked-in records (path A: read-only, no new ledger table) and runs the
# gate against them.  Reads observations only; never writes to a ledger and
# never mutates official state.  The commit-bound receipt is an artifact of
# this run, written to data/intrinsic_promotion/.
# ---------------------------------------------------------------------------

CURRENT_DATASET_EVALUATED_AT = "2026-07-29T00:00:00Z"
REAL_RECORDS_PATH = Path(__file__).resolve().parents[2] / "data" / "asset_intrinsic_records.json"
BENCHMARK_MANIFEST_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "asset_intrinsic_benchmark" / "manifest.json"
)
RECEIPT_DIR = Path(__file__).resolve().parents[2] / "data" / "intrinsic_promotion"
COMMIT_BOUND_RECEIPT_PATH = RECEIPT_DIR / "receipt-current.json"


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_observations_from_records(
    records: Sequence[Any],
    *,
    pit_cutoff: datetime,
) -> list[Mapping[str, Any]]:
    """Path A adapter: project each real record to an intrinsic observation.

    Reuses the read-only pattern demonstrated by ``shadow_dashboard``: load
    records, take the PIT view, and call the real observation builder.  Each
    observation is tagged with ``observed_at`` (the record's ``valid_from``)
    so the gate can measure the temporal span honestly.
    """
    # Lazy import keeps the pure gate importable without the asset_intrinsic
    # stack; these are the legitimate observational source layers.
    from trustforge.asset_intrinsic import AssetIntrinsicRepository
    from trustforge.asset_intrinsic_shadow import build_intrinsic_shadow_observation

    repo = AssetIntrinsicRepository(records)
    observations: list[Mapping[str, Any]] = []
    for record in records:
        view = repo.pit_view(record.profile.asset_id, pit_cutoff)
        if view is None:
            continue
        observation = build_intrinsic_shadow_observation(
            view,
            baseline_trust=0.5,
            candidate_trust=0.5,
            query=f"intrinsic-promotion/pit-cutoff/{_iso(pit_cutoff)}",
        )
        observation["observed_at"] = _iso(record.valid_from)
        observations.append(observation)
    return observations


def _benchmark_manifest_digest(path: Path = BENCHMARK_MANIFEST_PATH) -> str:
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise IntrinsicPromotionError("benchmark manifest cannot be read") from exc
    if not isinstance(payload, Mapping):
        raise IntrinsicPromotionError("benchmark manifest must be an object")
    digest = payload.get("data_version")
    if not isinstance(digest, str) or _DIGEST_RE.fullmatch(digest) is None:
        raise IntrinsicPromotionError("benchmark manifest data_version is invalid")
    return digest


def _sensitivity_from_manifest(path: Path = BENCHMARK_MANIFEST_PATH) -> Mapping[str, Any] | None:
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    measurements = payload.get("measurements")
    if not isinstance(measurements, Mapping):
        return None
    sweep = measurements.get("extreme_value_sensitivity")
    if not isinstance(sweep, Mapping):
        return None
    rows = sweep.get("rows")
    if not isinstance(rows, list):
        return None
    max_abs = 0.0
    found = False
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        by_value = row.get("total_delta_by_value")
        if not isinstance(by_value, Mapping):
            continue
        for raw in by_value.values():
            if isinstance(raw, (int, float)) and not isinstance(raw, bool) and math.isfinite(raw):
                max_abs = max(max_abs, abs(float(raw)))
                found = True
    if not found:
        return None
    return {"max_abs_response": round(max_abs, 8)}


def evaluate_current_dataset(
    *,
    evaluated_at: str = CURRENT_DATASET_EVALUATED_AT,
    records_path: Path = REAL_RECORDS_PATH,
    benchmark_manifest_path: Path = BENCHMARK_MANIFEST_PATH,
    policy: IntrinsicPromotionPolicy | None = None,
) -> IntrinsicPromotionReceipt:
    """Evaluate the real checked-in intrinsic records; expected BLOCK.

    Honest current-state evaluation: 3 real records (BTC/ETH/BNB), <30 day
    span, <5 assets, <200 observations.  The gate must fail closed.
    """
    from trustforge.asset_intrinsic import load_asset_intrinsic_records

    resolved_policy = policy or load_intrinsic_promotion_policy()
    cutoff = _parse_ts(evaluated_at)
    records = load_asset_intrinsic_records(records_path)
    observations = build_observations_from_records(records, pit_cutoff=cutoff)
    return evaluate_promotion(
        resolved_policy,
        observations,
        benchmark_manifest_digest=_benchmark_manifest_digest(benchmark_manifest_path),
        now=evaluated_at,
        sensitivity_report=_sensitivity_from_manifest(benchmark_manifest_path),
    )


def write_commit_bound_receipt(
    receipt: IntrinsicPromotionReceipt,
    *,
    out_path: Path = COMMIT_BOUND_RECEIPT_PATH,
) -> Path:
    """Persist a commit-bound receipt artifact (recommend-only evidence)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    artifact = {
        "receipt": receipt_canonical_dict(receipt),
        "receipt_id": receipt_id(receipt),
    }
    out_path.write_text(
        json.dumps(artifact, indent=2, sort_keys=False, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return out_path
