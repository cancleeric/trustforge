"""Deterministic TrustForge kernel contracts.

This package is intentionally dependency-free.  Runtime providers, storage,
application schemas, and orchestration belong outside this boundary.
"""

from .contracts import (
    KERNEL_CONTRACT_VERSION,
    JsonValue,
    KernelClaim,
    KernelDocument,
    KernelInput,
    KernelOutput,
)
from .corroboration import (
    CorroborationClaim,
    CorroborationResult,
    DOMAIN_STOP,
    StanceLabel,
    StancePair,
    canonical_source,
    corroborate,
    directional_word_polarities,
)
from .dawid_skene import LABELS, N_LABELS, em_source_reliability
from .scoring import (
    interpolate_calibration,
    recency_decay,
    reputation_floor,
    source_reputation,
    stable_sigmoid,
)

__all__ = [
    "KERNEL_CONTRACT_VERSION",
    "JsonValue",
    "KernelClaim",
    "KernelDocument",
    "KernelInput",
    "KernelOutput",
    "CorroborationClaim",
    "CorroborationResult",
    "DOMAIN_STOP",
    "StanceLabel",
    "StancePair",
    "canonical_source",
    "corroborate",
    "directional_word_polarities",
    "LABELS",
    "N_LABELS",
    "em_source_reliability",
    "interpolate_calibration",
    "recency_decay",
    "reputation_floor",
    "source_reputation",
    "stable_sigmoid",
]
