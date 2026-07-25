"""Legacy aggregate and current structured-decision goldens for #452."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from trustforge.ingestion.base import Document
from trustforge.trust import scoring
from trustforge_core import canonical_source


def _scored(
    claim_id: str,
    trust: float,
    source: str = "wire",
    kind: str = "news",
    text: str = "generic market update",
    direction: str = "neutral",
    flags: list[str] | None = None,
) -> scoring.ScoredClaim:
    document = Document(
        f"doc-{claim_id}",
        kind,
        source,
        text,
        ts=1.0,
        url=f"https://example.test/{claim_id}",
        meta={},
    )
    claim = scoring.Claim(claim_id, text, document, "fact", direction)
    return scoring.ScoredClaim(claim, trust, {}, None, flags or [], [])


def _structured_result(
    items: list[scoring.ScoredClaim],
    *,
    query: str = "",
    coin: str | None = None,
    resolved_direction: str = "neutral",
) -> dict[str, Any]:
    brief = scoring.aggregate(items, query=query, coin=coin)
    independent_sources = len(
        {canonical_source(item.claim.doc.source) for item in brief.supporting}
    )
    low_calibrated = brief.calibrated_confidence < 0.35
    insufficient_sources = independent_sources < 2
    is_abstain = low_calibrated or insufficient_sources
    state = (
        "abstain"
        if is_abstain
        else "low_confidence"
        if brief.calibrated_confidence < 0.5
        else "normal"
    )
    return {
        "supporting_ids": [item.claim.id for item in brief.supporting],
        "contrarian_ids": [item.claim.id for item in brief.contrarian],
        "raw_confidence": brief.confidence,
        "calibrated_confidence": brief.calibrated_confidence,
        "supporting_count": len(brief.supporting),
        "contrarian_count": len(brief.contrarian),
        "canonical_independent_sources": independent_sources,
        # Legacy has no reason-code field.  These are the two independently
        # derivable facts that drive its current structured decision.
        "low_calibrated": low_calibrated,
        "insufficient_sources": insufficient_sources,
        "decision_state": state,
        # Direction is an outer resolved fixture value; aggregate does not
        # calculate it and this golden does not invoke a provider/_direction.
        "resolved_direction": resolved_direction,
    }


def _empty() -> list[scoring.ScoredClaim]:
    return []


def _sparse() -> list[scoring.ScoredClaim]:
    return [_scored("a", 0.8, "one")]


def _canonical_alias() -> list[scoring.ScoredClaim]:
    return [_scored("a", 0.8, "CoinDesk"), _scored("b", 0.8, " coindesk ")]


def _two_independent() -> list[scoring.ScoredClaim]:
    return [_scored("a", 0.8, "one"), _scored("b", 0.8, "two")]


def _directions_and_contradiction() -> list[scoring.ScoredClaim]:
    return [
        _scored("n", 0.7, "n", text="neutral outlook", direction="neutral"),
        _scored("bull", 0.8, "bull", text="bull outlook", direction="bullish"),
        _scored("bear", 0.75, "bear", text="bear outlook", direction="bearish"),
        _scored(
            "contra",
            0.4,
            "contra",
            text="contradiction outlook",
            direction="bearish",
        ),
    ]


def _manipulation_contrarian() -> list[scoring.ScoredClaim]:
    return [
        _scored("good", 0.7, "good"),
        _scored(
            "manip",
            0.2,
            "x",
            "social",
            "BTC pump shill",
            "bullish",
            ["shill", "pump"],
        ),
    ]


def _threshold() -> list[scoring.ScoredClaim]:
    return [_scored("at", 0.5, "a"), _scored("below", 0.499, "b")]


def _calibrated_035() -> list[scoring.ScoredClaim]:
    return [
        _scored("a", 0.5, "a"),
        _scored("b", 0.5, "b"),
        _scored("c1", 0.4, "c1"),
        _scored("c2", 0.4, "c2"),
    ]


def _calibrated_below_035() -> list[scoring.ScoredClaim]:
    return [*_calibrated_035(), _scored("c3", 0.4, "c3")]


def _calibrated_above_050() -> list[scoring.ScoredClaim]:
    return [_scored("a", 0.6, "a"), _scored("b", 0.6, "b")]


def _source_flood() -> list[scoring.ScoredClaim]:
    return [_scored(f"s{index:02}", 0.8, "same") for index in range(12)]


def _truncation_pool() -> list[scoring.ScoredClaim]:
    supporting = [
        _scored(
            f"s{index:02}",
            0.8,
            f"src{index}",
            ("price", "onchain", "news")[index % 3],
        )
        for index in range(12)
    ]
    contrarian = [
        _scored(f"c{index:02}", 0.4, f"contra{index}", "social")
        for index in range(8)
    ]
    return supporting + contrarian


def _coin_pool() -> list[scoring.ScoredClaim]:
    return [
        _scored("btc", 0.8, "btc", text="BTC ETF inflow", direction="bullish"),
        _scored("generic", 0.7, "generic", text="market liquidity update"),
        _scored("eth", 0.9, "eth", text="ETH staking inflow", direction="bullish"),
    ]


def _query_fallback() -> list[scoring.ScoredClaim]:
    return [_scored("low", 0.6, "low"), _scored("high", 0.9, "high")]


def _equal_trust() -> list[scoring.ScoredClaim]:
    return [
        _scored("first", 0.7, "a"),
        _scored("second", 0.7, "b"),
        _scored("third", 0.7, "c"),
    ]


GOLDEN_CASES = [
    pytest.param(
        _empty,
        {},
        {
            "supporting_ids": [],
            "contrarian_ids": [],
            "raw_confidence": 0.0,
            "calibrated_confidence": 0.0,
            "supporting_count": 0,
            "contrarian_count": 0,
            "canonical_independent_sources": 0,
            "low_calibrated": True,
            "insufficient_sources": True,
            "decision_state": "abstain",
            "resolved_direction": "neutral",
        },
        id="empty",
    ),
    pytest.param(
        _sparse,
        {},
        {
            "supporting_ids": ["a"],
            "contrarian_ids": [],
            "raw_confidence": 0.8,
            "calibrated_confidence": 0.48,
            "supporting_count": 1,
            "contrarian_count": 0,
            "canonical_independent_sources": 1,
            "low_calibrated": False,
            "insufficient_sources": True,
            "decision_state": "abstain",
            "resolved_direction": "neutral",
        },
        id="sparse-single-source",
    ),
    pytest.param(
        _canonical_alias,
        {},
        {
            "supporting_ids": ["a", "b"],
            "contrarian_ids": [],
            "raw_confidence": 0.8,
            "calibrated_confidence": 0.48,
            "supporting_count": 2,
            "contrarian_count": 0,
            "canonical_independent_sources": 1,
            "low_calibrated": False,
            "insufficient_sources": True,
            "decision_state": "abstain",
            "resolved_direction": "neutral",
        },
        id="canonical-alias-same-source",
    ),
    pytest.param(
        _two_independent,
        {"resolved_direction": "bullish"},
        {
            "supporting_ids": ["a", "b"],
            "contrarian_ids": [],
            "raw_confidence": 0.8,
            "calibrated_confidence": 0.58,
            "supporting_count": 2,
            "contrarian_count": 0,
            "canonical_independent_sources": 2,
            "low_calibrated": False,
            "insufficient_sources": False,
            "decision_state": "normal",
            "resolved_direction": "bullish",
        },
        id="two-independent-sources",
    ),
    pytest.param(
        _directions_and_contradiction,
        {"resolved_direction": "bearish"},
        {
            "supporting_ids": ["bull", "bear", "n"],
            "contrarian_ids": ["contra"],
            "raw_confidence": 0.75,
            "calibrated_confidence": 0.6125,
            "supporting_count": 3,
            "contrarian_count": 1,
            "canonical_independent_sources": 3,
            "low_calibrated": False,
            "insufficient_sources": False,
            "decision_state": "normal",
            "resolved_direction": "bearish",
        },
        id="neutral-bullish-bearish-contradiction",
    ),
    pytest.param(
        _manipulation_contrarian,
        {},
        {
            "supporting_ids": ["good"],
            "contrarian_ids": ["manip"],
            "raw_confidence": 0.7,
            "calibrated_confidence": 0.29,
            "supporting_count": 1,
            "contrarian_count": 1,
            "canonical_independent_sources": 1,
            "low_calibrated": True,
            "insufficient_sources": True,
            "decision_state": "abstain",
            "resolved_direction": "neutral",
        },
        id="manipulation-low-trust-contrarian",
    ),
    pytest.param(
        _threshold,
        {},
        {
            "supporting_ids": ["at"],
            "contrarian_ids": ["below"],
            "raw_confidence": 0.5,
            "calibrated_confidence": 0.17,
            "supporting_count": 1,
            "contrarian_count": 1,
            "canonical_independent_sources": 1,
            "low_calibrated": True,
            "insufficient_sources": True,
            "decision_state": "abstain",
            "resolved_direction": "neutral",
        },
        id="trust-exactly-point-five",
    ),
    pytest.param(
        _calibrated_035,
        {},
        {
            "supporting_ids": ["a", "b"],
            "contrarian_ids": ["c1", "c2"],
            "raw_confidence": 0.5,
            "calibrated_confidence": 0.35,
            "supporting_count": 2,
            "contrarian_count": 2,
            "canonical_independent_sources": 2,
            "low_calibrated": False,
            "insufficient_sources": False,
            "decision_state": "low_confidence",
            "resolved_direction": "neutral",
        },
        id="calibrated-exactly-point-three-five",
    ),
    pytest.param(
        _calibrated_below_035,
        {},
        {
            "supporting_ids": ["a", "b"],
            "contrarian_ids": ["c1", "c2", "c3"],
            "raw_confidence": 0.5,
            "calibrated_confidence": 0.31,
            "supporting_count": 2,
            "contrarian_count": 3,
            "canonical_independent_sources": 2,
            "low_calibrated": True,
            "insufficient_sources": False,
            "decision_state": "abstain",
            "resolved_direction": "neutral",
        },
        id="calibrated-below-point-three-five",
    ),
    pytest.param(
        _calibrated_above_050,
        {},
        {
            "supporting_ids": ["a", "b"],
            "contrarian_ids": [],
            "raw_confidence": 0.6,
            "calibrated_confidence": 0.51,
            "supporting_count": 2,
            "contrarian_count": 0,
            "canonical_independent_sources": 2,
            "low_calibrated": False,
            "insufficient_sources": False,
            "decision_state": "normal",
            "resolved_direction": "neutral",
        },
        id="calibrated-above-point-five",
    ),
    pytest.param(
        _source_flood,
        {},
        {
            "supporting_ids": [f"s{index:02}" for index in range(10)],
            "contrarian_ids": [],
            "raw_confidence": 0.7999999999999999,
            "calibrated_confidence": 0.48,
            "supporting_count": 10,
            "contrarian_count": 0,
            "canonical_independent_sources": 1,
            "low_calibrated": False,
            "insufficient_sources": True,
            "decision_state": "abstain",
            "resolved_direction": "neutral",
        },
        id="duplicate-source-flooding",
    ),
    pytest.param(
        _truncation_pool,
        {},
        {
            "supporting_ids": [f"s{index:02}" for index in range(10)],
            "contrarian_ids": [f"c{index:02}" for index in range(5)],
            "raw_confidence": 0.7999999999999999,
            "calibrated_confidence": 0.85,
            "supporting_count": 10,
            "contrarian_count": 5,
            "canonical_independent_sources": 10,
            "low_calibrated": False,
            "insufficient_sources": False,
            "decision_state": "normal",
            "resolved_direction": "neutral",
        },
        id="pre-truncation-calibration-post-truncation-decision",
    ),
    pytest.param(
        _coin_pool,
        {"query": "anything", "coin": "BTC", "resolved_direction": "bullish"},
        {
            "supporting_ids": ["btc", "generic"],
            "contrarian_ids": [],
            "raw_confidence": 0.75,
            "calibrated_confidence": 0.5625,
            "supporting_count": 2,
            "contrarian_count": 0,
            "canonical_independent_sources": 2,
            "low_calibrated": False,
            "insufficient_sources": False,
            "decision_state": "normal",
            "resolved_direction": "bullish",
        },
        id="btc-excludes-explicit-eth-keeps-generic",
    ),
    pytest.param(
        _query_fallback,
        {"query": "unmatched token"},
        {
            "supporting_ids": ["high", "low"],
            "contrarian_ids": [],
            "raw_confidence": 0.75,
            "calibrated_confidence": 0.5625,
            "supporting_count": 2,
            "contrarian_count": 0,
            "canonical_independent_sources": 2,
            "low_calibrated": False,
            "insufficient_sources": False,
            "decision_state": "normal",
            "resolved_direction": "neutral",
        },
        id="query-no-match-falls-back-to-all",
    ),
    pytest.param(
        _equal_trust,
        {},
        {
            "supporting_ids": ["first", "second", "third"],
            "contrarian_ids": [],
            "raw_confidence": 0.6999999999999998,
            "calibrated_confidence": 0.645,
            "supporting_count": 3,
            "contrarian_count": 0,
            "canonical_independent_sources": 3,
            "low_calibrated": False,
            "insufficient_sources": False,
            "decision_state": "normal",
            "resolved_direction": "neutral",
        },
        id="equal-trust-stable-order",
    ),
]


@pytest.fixture(autouse=True)
def _fixed_legacy_calibration(monkeypatch):
    monkeypatch.setattr(scoring, "_load_cached_calibration_model", lambda: None)


@pytest.mark.parametrize("factory,kwargs,expected", GOLDEN_CASES)
def test_legacy_aggregate_and_current_decision_golden(
    factory: Callable[[], list[scoring.ScoredClaim]],
    kwargs: dict[str, Any],
    expected: dict[str, Any],
):
    assert _structured_result(factory(), **kwargs) == expected


def test_legacy_parsed_isotonic_model_consumption_golden(monkeypatch):
    parsed_model = [
        {"confidence": 0.0, "calibrated": 0.1},
        {"confidence": 1.0, "calibrated": 0.9},
    ]
    monkeypatch.setattr(
        scoring, "_load_cached_calibration_model", lambda: parsed_model
    )

    actual = _structured_result(_two_independent())

    assert actual == {
        "supporting_ids": ["a", "b"],
        "contrarian_ids": [],
        "raw_confidence": 0.8,
        "calibrated_confidence": 0.564,
        "supporting_count": 2,
        "contrarian_count": 0,
        "canonical_independent_sources": 2,
        "low_calibrated": False,
        "insufficient_sources": False,
        "decision_state": "normal",
        "resolved_direction": "neutral",
    }
