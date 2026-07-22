"""Immutable, side-effect-free contracts for the Trust Kernel.

The contracts contain values only.  They deliberately do not expose provider
callbacks, application ``Document`` objects, persistence handles, or runtime
configuration.  Application code must normalize its data before crossing this
boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import TypeAlias

from .source_identity import canonical_source


KERNEL_CONTRACT_VERSION = "2.2.0"
KERNEL_RESOLUTION_VERSION = "1.0.0"
FIXED_HEURISTIC_VERSION = "fixed-heuristic-v1"
ISOTONIC_VERSION = "isotonic-v1"
SUPPORTED_CALIBRATION_MODEL_VERSIONS = frozenset(
    {FIXED_HEURISTIC_VERSION, ISOTONIC_VERSION}
)
STRICT_JSON_MAX_INTEGER = (1 << 53) - 1

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = (
    JsonScalar | tuple["JsonValue", ...] | tuple[tuple[str, "JsonValue"], ...]
)


class UnsupportedKernelContractVersion(ValueError):
    """Raised when a caller crosses the kernel boundary with an unknown version."""


def require_supported_contract_version(contract_version: str) -> None:
    """Fail closed unless *contract_version* is the exact current contract."""
    if type(contract_version) is not str:
        raise UnsupportedKernelContractVersion(
            "unsupported kernel contract version type; expected exact str"
        )
    if contract_version != KERNEL_CONTRACT_VERSION:
        raise UnsupportedKernelContractVersion(
            f"unsupported kernel contract version; expected {KERNEL_CONTRACT_VERSION}"
        )


def _require_finite_number(value: float, *, field: str) -> None:
    if type(value) not in {int, float}:
        raise ValueError(f"{field} must be a finite number")
    if (
        type(value) is int
        and not -STRICT_JSON_MAX_INTEGER <= value <= STRICT_JSON_MAX_INTEGER
    ):
        raise ValueError(f"{field} integer exceeds strict JSON safe range")
    try:
        finite = isfinite(value)
    except OverflowError as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not finite:
        raise ValueError(f"{field} must be a finite number")


def _require_nonnegative_int(value: int, *, field: str) -> None:
    if type(value) is not int or not 0 <= value <= STRICT_JSON_MAX_INTEGER:
        raise ValueError(f"{field} must be a strict-JSON-safe nonnegative integer")


def _require_json_value(value: object, *, field: str) -> None:
    """Validate the immutable subset of values accepted by JSON contracts."""
    if value is None or type(value) in {bool, str}:
        return
    if type(value) is int:
        if not -STRICT_JSON_MAX_INTEGER <= value <= STRICT_JSON_MAX_INTEGER:
            raise ValueError(f"{field} integer exceeds strict JSON safe range")
        return
    if type(value) is float:
        if not isfinite(value):
            raise ValueError(f"{field} must contain only finite JSON values")
        return
    if type(value) is tuple:
        for index, item in enumerate(value):
            _require_json_value(item, field=f"{field}[{index}]")
        return
    raise ValueError(f"{field} must contain only immutable JSON values")


def _require_exact_string_tuple(value: object, *, field: str) -> None:
    if type(value) is not tuple or not all(type(item) is str for item in value):
        raise ValueError(f"{field} must be a tuple of exact strings")


def _require_policy_table(
    value: object,
    *,
    field: str,
    minimum: float,
    maximum: float | None,
) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise ValueError(f"{field} must be a tuple")
    keys: list[str] = []
    for index, item in enumerate(value):
        if type(item) is not tuple or len(item) != 2 or type(item[0]) is not str:
            raise ValueError(f"{field} must contain exact (str, number) tuples")
        number = item[1]
        _require_finite_number(number, field=f"{field}[{index}].value")
        if number < minimum or (maximum is not None and number > maximum):
            raise ValueError(f"{field}[{index}].value is outside the supported range")
        keys.append(item[0])
    if len(keys) != len(set(keys)):
        raise ValueError(f"{field} keys must be unique")
    return tuple(keys)


@dataclass(frozen=True, slots=True)
class KernelDocument:
    """Normalized evidence document accepted by the deterministic core."""

    id: str
    kind: str
    source: str
    text: str
    timestamp: float
    url: str = ""
    metadata: tuple[tuple[str, JsonValue], ...] = ()

    def __post_init__(self) -> None:
        validate_document_graph(self)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("KernelDocument is sealed and cannot be subclassed")


@dataclass(frozen=True, slots=True)
class KernelClaim:
    """A normalized claim with no dependency on application schema classes."""

    id: str
    text: str
    document: KernelDocument
    claim_type: str = "inference"
    direction: str = "neutral"

    def __post_init__(self) -> None:
        validate_claim_graph(self)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("KernelClaim is sealed and cannot be subclassed")


@dataclass(frozen=True, slots=True)
class KernelClaimResolution:
    """Outer-resolved, deterministic inputs for one claim."""

    claim_id: str
    independent_sources: tuple[str, ...] = ()
    dynamic_reputation: float | None = None
    reputation_trace: KernelReputationTrace | None = None
    info_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        validate_claim_resolution_graph(self)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("KernelClaimResolution is sealed and cannot be subclassed")


@dataclass(frozen=True, slots=True)
class KernelRunResolution:
    """Immutable run-level resolution and optional explicit scoring policy."""

    claim_resolutions: tuple[KernelClaimResolution, ...]
    score_weights: tuple[tuple[str, float], ...] = ()
    reputations: tuple[tuple[str, float], ...] = ()
    half_lives: tuple[tuple[str, float], ...] = ()
    calibration_model_version: str = FIXED_HEURISTIC_VERSION
    calibration_table: tuple[tuple[float, float], ...] = ()
    resolved_direction: str = "neutral"
    resolution_version: str = KERNEL_RESOLUTION_VERSION

    def __post_init__(self) -> None:
        validate_run_resolution_graph(self)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("KernelRunResolution is sealed and cannot be subclassed")


def validate_claim_resolution_order(
    claims: tuple[KernelClaim, ...], resolution: KernelRunResolution
) -> None:
    """Require one resolution per claim, with exactly matching IDs and order."""
    if type(claims) is not tuple or not all(
        type(claim) is KernelClaim for claim in claims
    ):
        raise ValueError("claims must be a tuple of exact KernelClaim values")
    if type(resolution) is not KernelRunResolution:
        raise ValueError("resolution must be an exact KernelRunResolution")
    for claim in claims:
        validate_claim_graph(claim)
    validate_run_resolution_graph(resolution)
    claim_ids = tuple(claim.id for claim in claims)
    resolution_ids = tuple(item.claim_id for item in resolution.claim_resolutions)
    if resolution_ids != claim_ids:
        raise ValueError("claim_resolutions must match claim IDs exactly and in order")


@dataclass(frozen=True, slots=True)
class KernelInput:
    """Versioned input to deterministic trust computation."""

    claims: tuple[KernelClaim, ...]
    pit_epoch: float
    coin: str
    query: str
    contract_version: str = KERNEL_CONTRACT_VERSION
    resolution: KernelRunResolution | None = None

    def __post_init__(self) -> None:
        validate_kernel_input_graph(self)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("KernelInput is sealed and cannot be subclassed")


@dataclass(frozen=True, slots=True)
class KernelReputationTrace:
    """Immutable source-reputation explanation for a scored claim."""

    source: str
    prior: float
    final: float
    agree_n: int
    contradict_n: int
    iterations_run: int
    mode: str = "entailment"

    def __post_init__(self) -> None:
        validate_reputation_trace_graph(self)

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("KernelReputationTrace is sealed and cannot be subclassed")


def validate_document_graph(document: KernelDocument) -> None:
    """Revalidate a document, including strict-JSON metadata after tampering."""
    if type(document) is not KernelDocument:
        raise ValueError("document must be an exact KernelDocument")
    for field, value in (
        ("id", document.id),
        ("kind", document.kind),
        ("source", document.source),
        ("text", document.text),
        ("url", document.url),
    ):
        if type(value) is not str:
            raise ValueError(f"{field} must be an exact string")
    _require_finite_number(document.timestamp, field="timestamp")
    if type(document.metadata) is not tuple:
        raise ValueError("metadata must be a tuple")
    for index, item in enumerate(document.metadata):
        if type(item) is not tuple or len(item) != 2 or type(item[0]) is not str:
            raise ValueError("metadata must contain exact (str, JsonValue) tuples")
        _require_json_value(item[1], field=f"metadata[{index}].value")


def validate_claim_graph(claim: KernelClaim) -> None:
    """Revalidate one claim and its complete document graph."""
    if type(claim) is not KernelClaim:
        raise ValueError("claim must be an exact KernelClaim")
    for field, value in (
        ("id", claim.id),
        ("text", claim.text),
        ("claim_type", claim.claim_type),
        ("direction", claim.direction),
    ):
        if type(value) is not str:
            raise ValueError(f"{field} must be an exact string")
    validate_document_graph(claim.document)


def validate_reputation_trace_graph(trace: KernelReputationTrace) -> None:
    """Revalidate every reputation-trace field after possible tampering."""
    if type(trace) is not KernelReputationTrace:
        raise ValueError("reputation_trace must be an exact KernelReputationTrace")
    if type(trace.source) is not str:
        raise ValueError("source must be an exact string")
    if type(trace.mode) is not str or trace.mode not in {"entailment", "ds_em"}:
        raise ValueError("mode must be entailment or ds_em")
    _require_finite_number(trace.prior, field="prior")
    _require_finite_number(trace.final, field="final")
    _require_nonnegative_int(trace.agree_n, field="agree_n")
    _require_nonnegative_int(trace.contradict_n, field="contradict_n")
    _require_nonnegative_int(trace.iterations_run, field="iterations_run")


def validate_claim_resolution_graph(resolution: KernelClaimResolution) -> None:
    """Revalidate a complete claim-resolution graph after possible tampering."""
    if type(resolution) is not KernelClaimResolution:
        raise ValueError("claim resolution must be an exact KernelClaimResolution")
    if type(resolution.claim_id) is not str or not resolution.claim_id:
        raise ValueError("claim_id must be a nonempty exact string")
    _require_exact_string_tuple(
        resolution.independent_sources, field="independent_sources"
    )
    seen: set[str] = set()
    for source in resolution.independent_sources:
        if not source or canonical_source(source) != source:
            raise ValueError("independent_sources must contain canonical identities")
        if source in seen:
            raise ValueError("independent_sources must be unique")
        seen.add(source)
    if resolution.dynamic_reputation is not None:
        _require_finite_number(
            resolution.dynamic_reputation, field="dynamic_reputation"
        )
        if not 0.0 <= resolution.dynamic_reputation <= 1.0:
            raise ValueError("dynamic_reputation must be in [0, 1]")
    if resolution.reputation_trace is not None:
        validate_reputation_trace_graph(resolution.reputation_trace)
    if (
        resolution.dynamic_reputation is not None
        and resolution.reputation_trace is not None
        and resolution.dynamic_reputation != resolution.reputation_trace.final
    ):
        raise ValueError("dynamic_reputation must equal reputation_trace.final")
    _require_exact_string_tuple(resolution.info_flags, field="info_flags")


def validate_run_resolution_graph(resolution: KernelRunResolution) -> None:
    """Revalidate the complete run-resolution graph and policy provenance."""
    if type(resolution) is not KernelRunResolution:
        raise ValueError("resolution must be an exact KernelRunResolution")
    if type(resolution.claim_resolutions) is not tuple:
        raise ValueError("claim_resolutions must be an exact tuple")
    for item in resolution.claim_resolutions:
        validate_claim_resolution_graph(item)
    if (
        type(resolution.resolution_version) is not str
        or resolution.resolution_version != KERNEL_RESOLUTION_VERSION
    ):
        raise ValueError(f"resolution_version must be {KERNEL_RESOLUTION_VERSION}")
    if type(resolution.resolved_direction) is not str:
        raise ValueError("resolved_direction must be an exact string")
    if (
        type(resolution.calibration_model_version) is not str
        or resolution.calibration_model_version
        not in SUPPORTED_CALIBRATION_MODEL_VERSIONS
    ):
        raise ValueError("unsupported calibration_model_version")
    weight_keys = _require_policy_table(
        resolution.score_weights,
        field="score_weights",
        minimum=0.0,
        maximum=1.0,
    )
    if weight_keys and set(weight_keys) != {"src", "corr", "rec", "manip"}:
        raise ValueError("score_weights must contain src, corr, rec, and manip")
    _require_policy_table(
        resolution.reputations,
        field="reputations",
        minimum=0.0,
        maximum=1.0,
    )
    half_life_keys = _require_policy_table(
        resolution.half_lives,
        field="half_lives",
        minimum=0.0,
        maximum=None,
    )
    if any(value <= 0.0 for _, value in resolution.half_lives):
        raise ValueError("half_lives values must be positive")
    if half_life_keys and "default" not in half_life_keys:
        raise ValueError("nonempty half_lives must contain default")
    if type(resolution.calibration_table) is not tuple:
        raise ValueError("calibration_table must be an exact tuple")
    for index, point in enumerate(resolution.calibration_table):
        if type(point) is not tuple or len(point) != 2:
            raise ValueError("calibration_table must contain exact (x, y) tuples")
        for axis, value in zip(("x", "y"), point, strict=True):
            _require_finite_number(value, field=f"calibration_table[{index}].{axis}")
            if not 0.0 <= value <= 1.0:
                raise ValueError("calibration_table values must be in [0, 1]")
    points = resolution.calibration_table
    if any(points[index - 1][0] >= points[index][0] for index in range(1, len(points))):
        raise ValueError("calibration_table x values must be strictly increasing")
    if any(points[index - 1][1] > points[index][1] for index in range(1, len(points))):
        raise ValueError("calibration_table y values must be nondecreasing")
    if resolution.calibration_model_version == FIXED_HEURISTIC_VERSION and points:
        raise ValueError("fixed calibration does not accept calibration_table")
    if resolution.calibration_model_version == ISOTONIC_VERSION and len(points) < 2:
        raise ValueError("isotonic calibration requires at least two points")


def validate_kernel_input_graph(value: KernelInput) -> None:
    """Revalidate a complete kernel input graph after possible tampering."""
    if type(value) is not KernelInput:
        raise ValueError("input must be an exact KernelInput")
    require_supported_contract_version(value.contract_version)
    if type(value.claims) is not tuple:
        raise ValueError("claims must be an exact tuple")
    for claim in value.claims:
        validate_claim_graph(claim)
    _require_finite_number(value.pit_epoch, field="pit_epoch")
    if type(value.coin) is not str:
        raise ValueError("coin must be an exact string")
    if type(value.query) is not str:
        raise ValueError("query must be an exact string")
    if value.resolution is not None:
        validate_claim_resolution_order(value.claims, value.resolution)


@dataclass(frozen=True, slots=True)
class KernelScoredClaim:
    """Immutable report-facing score and explanation for one kernel claim."""

    claim: KernelClaim
    trust: float
    components: tuple[tuple[str, float], ...] = ()
    reputation_trace: KernelReputationTrace | None = None
    manip_flags: tuple[str, ...] = ()
    info_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.claim) is not KernelClaim:
            raise ValueError("claim must be an exact KernelClaim")
        if (
            self.reputation_trace is not None
            and type(self.reputation_trace) is not KernelReputationTrace
        ):
            raise ValueError(
                "reputation_trace must be an exact KernelReputationTrace or None"
            )
        _require_finite_number(self.trust, field="trust")
        if type(self.components) is not tuple:
            raise ValueError("components must be a tuple")
        for index, component in enumerate(self.components):
            if (
                type(component) is not tuple
                or len(component) != 2
                or type(component[0]) is not str
            ):
                raise ValueError("components must contain (str, float) tuples")
            _require_finite_number(component[1], field=f"components[{index}].value")
        if type(self.manip_flags) is not tuple or not all(
            type(flag) is str for flag in self.manip_flags
        ):
            raise ValueError("manip_flags must be a tuple of strings")
        if type(self.info_flags) is not tuple or not all(
            type(flag) is str for flag in self.info_flags
        ):
            raise ValueError("info_flags must be a tuple of strings")

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("KernelScoredClaim is sealed and cannot be subclassed")


@dataclass(frozen=True, slots=True)
class KernelOutput:
    """Versioned result returned by deterministic trust computation."""

    trust_score: float
    confidence: float
    abstain: bool
    direction: str
    reason_codes: tuple[str, ...]
    supporting_count: int
    independent_sources: int
    contract_version: str = KERNEL_CONTRACT_VERSION
    query: str = ""
    scored_claims: tuple[KernelScoredClaim, ...] = ()
    supporting: tuple[KernelScoredClaim, ...] = ()
    contrarian: tuple[KernelScoredClaim, ...] = ()
    decision_state: str = "normal"

    def __post_init__(self) -> None:
        require_supported_contract_version(self.contract_version)
        if type(self.abstain) is not bool:
            raise ValueError("abstain must be a boolean")
        if type(self.direction) is not str:
            raise ValueError("direction must be a string")
        if type(self.query) is not str:
            raise ValueError("query must be a string")
        _require_finite_number(self.trust_score, field="trust_score")
        _require_finite_number(self.confidence, field="confidence")
        _require_nonnegative_int(self.supporting_count, field="supporting_count")
        _require_nonnegative_int(self.independent_sources, field="independent_sources")
        if type(self.decision_state) is not str or self.decision_state not in {
            "abstain",
            "low_confidence",
            "normal",
        }:
            raise ValueError(
                "decision_state must be abstain, low_confidence, or normal"
            )
        if type(self.reason_codes) is not tuple or not all(
            type(code) is str for code in self.reason_codes
        ):
            raise ValueError("reason_codes must be a tuple of strings")
        for field, values in (
            ("scored_claims", self.scored_claims),
            ("supporting", self.supporting),
            ("contrarian", self.contrarian),
        ):
            if type(values) is not tuple or not all(
                type(value) is KernelScoredClaim for value in values
            ):
                raise ValueError(
                    f"{field} must be a tuple of exact KernelScoredClaim values"
                )

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("KernelOutput is sealed and cannot be subclassed")
