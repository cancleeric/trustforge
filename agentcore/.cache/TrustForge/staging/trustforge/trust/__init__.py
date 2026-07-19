"""信任提煉層 — TrustForge 的核心競爭力。"""
from .scoring import (
    Claim,
    ScoredClaim,
    TrustedBrief,
    DEFAULT_WEIGHTS,
    extract_claims,
    score,
    aggregate,
)

__all__ = [
    "Claim",
    "ScoredClaim",
    "TrustedBrief",
    "DEFAULT_WEIGHTS",
    "extract_claims",
    "score",
    "aggregate",
]
