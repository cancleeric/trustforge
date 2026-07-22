"""Immutable, side-effect-free contracts for the Trust Kernel.

The contracts contain values only.  They deliberately do not expose provider
callbacks, application ``Document`` objects, persistence handles, or runtime
configuration.  Application code must normalize its data before crossing this
boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias


KERNEL_CONTRACT_VERSION = "2.0.0"

JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | tuple["JsonValue", ...] | tuple[tuple[str, "JsonValue"], ...]


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


@dataclass(frozen=True, slots=True)
class KernelClaim:
    """A normalized claim with no dependency on application schema classes."""

    id: str
    text: str
    document: KernelDocument
    claim_type: str = "inference"
    direction: str = "neutral"


@dataclass(frozen=True, slots=True)
class KernelInput:
    """Versioned input to deterministic trust computation."""

    claims: tuple[KernelClaim, ...]
    pit_epoch: float
    coin: str
    query: str
    contract_version: str = KERNEL_CONTRACT_VERSION


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
