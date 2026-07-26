"""Tests for pure scoring kernels."""
from __future__ import annotations
from trustforge_core.pure_scoring import compute_corroboration_score, compute_consensus_weight


def test_pure_scoring_empty_inputs():
    s = compute_corroboration_score([], [])
    assert s.score == 0.0 and s.confidence == 0.0 and s.direction == "neutral"


def test_pure_scoring_unanimous_bullish():
    s = compute_corroboration_score(["a", "b", "c"], ["bullish", "bullish", "bullish"])
    assert s.direction == "bullish" and s.score == 1.0


def test_pure_scoring_split():
    s = compute_corroboration_score(["a", "b"], ["bullish", "bearish"])
    assert s.direction == "neutral"


def test_pure_scoring_bearish_majority():
    s = compute_corroboration_score(["x", "y", "z"], ["bearish", "bearish", "bullish"])
    assert s.direction == "bearish"


def test_consensus_weight_uniform():
    w = compute_consensus_weight([0.8, 0.8, 0.8])
    assert w == 1.0


def test_consensus_weight_divided():
    w = compute_consensus_weight([0.8, 0.2])
    assert w < 1.0
