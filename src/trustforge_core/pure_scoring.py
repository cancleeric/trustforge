"""Pure scoring kernels — zero external dependencies (no provider/LLM/IO).

These functions are deterministic given their inputs. They form the
foundation of TrustForge's scoring model and are safe to call from
any runtime context (offline/live/staging).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PureScore:
    """Immutable scoring result from a pure kernel."""
    score: float
    confidence: float
    direction: str
    evidence_count: int


def compute_corroboration_score(sources: list[str], stances: list[str]) -> PureScore:
    """Pure corroboration scoring: agreement strength from independent sources.

    Deterministic: same inputs = same outputs. No Bedrock/LLM dependency.
    """
    if not sources or not stances:
        return PureScore(score=0.0, confidence=0.0, direction="neutral", evidence_count=0)

    unique_sources = len(set(sources))
    bullish = stances.count("bullish")
    bearish = stances.count("bearish")
    neutral = len(stances) - bullish - bearish

    score = abs(bullish - bearish) / max(len(stances), 1)
    confidence = min(unique_sources / 5.0, 1.0)

    if bullish > bearish:
        direction = "bullish"
    elif bearish > bullish:
        direction = "bearish"
    else:
        direction = "neutral"

    return PureScore(
        score=round(score, 4),
        confidence=round(confidence, 4),
        direction=direction,
        evidence_count=len(stances),
    )


def compute_consensus_weight(evidence_scores: list[float]) -> float:
    """Compute consensus weight from evidence agreement levels.

    Pure mathematical aggregation: no external dependencies.
    """
    if not evidence_scores:
        return 0.0
    n = len(evidence_scores)
    mean = sum(evidence_scores) / n
    variance = sum((s - mean) ** 2 for s in evidence_scores) / n
    consensus = 1.0 / (1.0 + variance)
    return round(consensus, 4)
