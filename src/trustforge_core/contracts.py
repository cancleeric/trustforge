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


KERNEL_CONTRACT_VERSION = "2.1.0"

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | tuple["JsonValue", ...] | tuple[tuple[str, "JsonValue"], ...]


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
    try:
        finite = isfinite(value)
    except OverflowError as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not finite:
        raise ValueError(f"{field} must be a finite number")


def _require_nonnegative_int(value: int, *, field: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")


def _require_json_value(value: object, *, field: str) -> None:
    """Validate the immutable subset of values accepted by JSON contracts."""
    if value is None or type(value) in {bool, int, str}:
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
        for field, value in (
            ("id", self.id),
            ("kind", self.kind),
            ("source", self.source),
            ("text", self.text),
            ("url", self.url),
        ):
            if type(value) is not str:
                raise ValueError(f"{field} must be a string")
        _require_finite_number(self.timestamp, field="timestamp")
        if type(self.metadata) is not tuple:
            raise ValueError("metadata must be a tuple")
        for index, item in enumerate(self.metadata):
            if (
                type(item) is not tuple
                or len(item) != 2
                or type(item[0]) is not str
            ):
                raise ValueError("metadata must contain (str, JsonValue) tuples")
            _require_json_value(item[1], field=f"metadata[{index}].value")

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
        for field, value in (
            ("id", self.id),
            ("text", self.text),
            ("claim_type", self.claim_type),
            ("direction", self.direction),
        ):
            if type(value) is not str:
                raise ValueError(f"{field} must be a string")
        if type(self.document) is not KernelDocument:
            raise ValueError("document must be an exact KernelDocument")

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("KernelClaim is sealed and cannot be subclassed")


@dataclass(frozen=True, slots=True)
class KernelInput:
    """Versioned input to deterministic trust computation."""

    claims: tuple[KernelClaim, ...]
    pit_epoch: float
    coin: str
    query: str
    contract_version: str = KERNEL_CONTRACT_VERSION

    def __post_init__(self) -> None:
        require_supported_contract_version(self.contract_version)
        if type(self.claims) is not tuple or not all(
            type(claim) is KernelClaim for claim in self.claims
        ):
            raise ValueError("claims must be a tuple of exact KernelClaim values")
        _require_finite_number(self.pit_epoch, field="pit_epoch")
        if type(self.coin) is not str:
            raise ValueError("coin must be a string")
        if type(self.query) is not str:
            raise ValueError("query must be a string")

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
        if type(self.source) is not str:
            raise ValueError("source must be a string")
        if type(self.mode) is not str or self.mode not in {"entailment", "ds_em"}:
            raise ValueError("mode must be entailment or ds_em")
        _require_finite_number(self.prior, field="prior")
        _require_finite_number(self.final, field="final")
        _require_nonnegative_int(self.agree_n, field="agree_n")
        _require_nonnegative_int(self.contradict_n, field="contradict_n")
        _require_nonnegative_int(self.iterations_run, field="iterations_run")

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("KernelReputationTrace is sealed and cannot be subclassed")


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
        if self.reputation_trace is not None and type(self.reputation_trace) is not KernelReputationTrace:
            raise ValueError("reputation_trace must be an exact KernelReputationTrace or None")
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
            raise ValueError("decision_state must be abstain, low_confidence, or normal")
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
                raise ValueError(f"{field} must be a tuple of exact KernelScoredClaim values")

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise TypeError("KernelOutput is sealed and cannot be subclassed")
