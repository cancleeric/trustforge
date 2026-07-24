"""Legacy per-claim scoring goldens captured before the #451 extraction."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from trustforge.ingestion.base import Document
from trustforge.trust.scoring import Claim, ScoredClaim, score


BASE_TS = 1_700_000_000.0


class _NoStanceClient:
    """An incompatible client makes the legacy path skip cache/provider stance."""


def _claim(
    claim_id: str,
    kind: str,
    source: str,
    text: str,
    *,
    timestamp: float = BASE_TS,
    metadata: dict[str, Any] | None = None,
    direction: str = "neutral",
) -> Claim:
    document = Document(
        f"doc-{claim_id}",
        kind,
        source,
        text,
        ts=timestamp,
        url=f"https://example.test/{claim_id}",
        meta={} if metadata is None else metadata,
    )
    return Claim(claim_id, text, document, "fact", direction)


def _payload(scored: ScoredClaim) -> dict[str, Any]:
    return {
        "claim_id": scored.claim.id,
        "trust": scored.trust,
        "components": scored.components,
        "reputation_trace": scored.reputation_trace,
        "manip_flags": scored.manip_flags,
        "info_flags": scored.info_flags,
    }


STATIC_CASES = [
    pytest.param(
        "static-news",
        lambda: [_claim("target", "news", "wire", "BTC adoption expands")],
        BASE_TS,
        None,
        {
            "claim_id": "target",
            "trust": 0.475,
            "components": {
                "reputation": 0.65,
                "corroboration": 0.0,
                "recency": 1.0,
                "manipulation": 0.0,
            },
            "reputation_trace": None,
            "manip_flags": [],
            "info_flags": [],
        },
        id="static-news",
    ),
    pytest.param(
        "social-manipulation",
        lambda: [
            _claim("target", "social", "x", "BTC pump shill 但不會暴漲")
        ],
        BASE_TS,
        None,
        {
            "claim_id": "target",
            "trust": 0.0,
            "components": {
                "reputation": 0.35,
                "corroboration": 0.0,
                "recency": 1.0,
                "manipulation": 1.0,
            },
            "reputation_trace": None,
            "manip_flags": ["shill", "pump"],
            "info_flags": [],
        },
        id="social-multiple-hits-and-negation",
    ),
    pytest.param(
        "celebrity-verified",
        lambda: [
            _claim(
                "target",
                "celebrity_trade",
                "celeb",
                "BTC trade disclosed",
                metadata={"verified_onchain": True},
            )
        ],
        BASE_TS,
        None,
        {
            "claim_id": "target",
            "trust": 0.4,
            "components": {
                "reputation": 0.5,
                "corroboration": 0.0,
                "recency": 1.0,
                "manipulation": 0.0,
            },
            "reputation_trace": None,
            "manip_flags": [],
            "info_flags": [],
        },
        id="celebrity-verified",
    ),
    pytest.param(
        "celebrity-unverified",
        lambda: [
            _claim(
                "target",
                "celebrity_trade",
                "celeb",
                "BTC trade disclosed",
                metadata={"verified_onchain": False},
            )
        ],
        BASE_TS,
        None,
        {
            "claim_id": "target",
            "trust": 0.32499999999999996,
            "components": {
                "reputation": 0.35,
                "corroboration": 0.0,
                "recency": 1.0,
                "manipulation": 0.0,
            },
            "reputation_trace": None,
            "manip_flags": [],
            "info_flags": [],
        },
        id="celebrity-unverified",
    ),
    pytest.param(
        "old-timestamp",
        lambda: [
            _claim(
                "target",
                "news",
                "wire",
                "BTC adoption expands",
                timestamp=BASE_TS - 43_200,
            )
        ],
        BASE_TS,
        None,
        {
            "claim_id": "target",
            "trust": 0.4,
            "components": {
                "reputation": 0.65,
                "corroboration": 0.0,
                "recency": 0.5,
                "manipulation": 0.0,
            },
            "reputation_trace": None,
            "manip_flags": [],
            "info_flags": [],
        },
        id="recency-old",
    ),
    pytest.param(
        "future-timestamp",
        lambda: [
            _claim(
                "target",
                "news",
                "wire",
                "BTC adoption expands",
                timestamp=BASE_TS + 1,
            )
        ],
        BASE_TS,
        None,
        {
            "claim_id": "target",
            "trust": 0.4,
            "components": {
                "reputation": 0.65,
                "corroboration": 0.0,
                "recency": 0.5,
                "manipulation": 0.0,
            },
            "reputation_trace": None,
            "manip_flags": [],
            "info_flags": [],
        },
        id="recency-future",
    ),
    pytest.param(
        "nonfinite-timestamp",
        lambda: [
            _claim(
                "target",
                "news",
                "wire",
                "BTC adoption expands",
                timestamp=float("nan"),
            )
        ],
        BASE_TS,
        None,
        {
            "claim_id": "target",
            "trust": 0.4,
            "components": {
                "reputation": 0.65,
                "corroboration": 0.0,
                "recency": 0.5,
                "manipulation": 0.0,
            },
            "reputation_trace": None,
            "manip_flags": [],
            "info_flags": [],
        },
        id="recency-nonfinite",
    ),
    pytest.param(
        "custom-weights",
        lambda: [_claim("target", "news", "wire", "BTC adoption expands")],
        BASE_TS,
        {"src": 0.4, "corr": 0.3, "rec": 0.2, "manip": 0.5},
        {
            "claim_id": "target",
            "trust": 0.46,
            "components": {
                "reputation": 0.65,
                "corroboration": 0.0,
                "recency": 1.0,
                "manipulation": 0.0,
            },
            "reputation_trace": None,
            "manip_flags": [],
            "info_flags": [],
        },
        id="valid-custom-weights",
    ),
]


@pytest.mark.parametrize("_name,pool_factory,now,weights,expected", STATIC_CASES)
def test_legacy_static_per_claim_golden(
    _name: str,
    pool_factory: Callable[[], list[Claim]],
    now: float,
    weights: dict[str, float] | None,
    expected: dict[str, Any],
):
    claims = pool_factory()

    actual = score(
        claims,
        now=now,
        weights=weights,
        dynamic_reputation=False,
        stance_client=_NoStanceClient(),
    )

    assert len(actual) == 1
    assert actual[0].claim is claims[0]
    assert _payload(actual[0]) == expected


CORROBORATION_CASES = [
    pytest.param([], 0.475, 0.0, id="zero-independent-sources"),
    pytest.param([("b", "wire-b")], 0.6, 0.5, id="one-independent-source"),
    pytest.param(
        [("b", "wire-b"), ("c", "sec")],
        0.6625,
        0.75,
        id="two-independent-sources",
    ),
    pytest.param(
        [("b1", "wire-b"), ("b2", "wire-b")],
        0.6,
        0.5,
        id="duplicate-source-counts-once",
    ),
]


@pytest.mark.parametrize("others,expected_trust,expected_corroboration", CORROBORATION_CASES)
def test_legacy_resolved_corroboration_count_golden(
    others: list[tuple[str, str]],
    expected_trust: float,
    expected_corroboration: float,
):
    shared = "BTC ETF 資金 流入 推動 上漲"
    target = _claim("target", "news", "wire-a", shared, direction="bullish")
    claims = [target]
    for claim_id, source in others:
        kind = "regulatory" if source == "sec" else "news"
        claims.append(_claim(claim_id, kind, source, shared, direction="bullish"))

    actual = score(
        claims,
        now=BASE_TS,
        dynamic_reputation=False,
        stance_client=_NoStanceClient(),
    )[0]

    assert actual.claim is target
    assert _payload(actual) == {
        "claim_id": "target",
        "trust": expected_trust,
        "components": {
            "reputation": 0.65,
            "corroboration": expected_corroboration,
            "recency": 1.0,
            "manipulation": 0.0,
        },
        "reputation_trace": None,
        "manip_flags": [],
        "info_flags": [],
    }


def test_legacy_sparse_dynamic_trace_golden():
    claim = _claim("sparse", "news", "wire", "BTC adoption expands")

    actual = score(
        [claim],
        now=BASE_TS,
        dynamic_reputation=True,
        stance_client=_NoStanceClient(),
        offline=False,
    )[0]

    assert actual.claim is claim
    assert _payload(actual) == {
        "claim_id": "sparse",
        "trust": 0.475,
        "components": {
            "reputation": 0.65,
            "corroboration": 0.0,
            "recency": 1.0,
            "manipulation": 0.0,
        },
        "reputation_trace": {
            "source": "wire",
            "prior": 0.65,
            "final": 0.65,
            "agree_n": 0,
            "contradict_n": 0,
            "iterations_run": 1,
            "mode": "entailment",
        },
        "manip_flags": [],
        "info_flags": [],
    }


def _ds_claims() -> list[Claim]:
    text = {
        "bullish": "BTC 上漲突破阻力",
        "bearish": "BTC 下跌跌破支撐",
        "neutral": "BTC 區間盤整觀望",
    }
    true_by_window = {
        0: "bullish",
        1: "bearish",
        2: "neutral",
        3: "bullish",
        4: "bearish",
        5: "neutral",
    }
    vote = {
        "glassnode": lambda window: true_by_window[window],
        "coindesk": lambda window: (
            "bearish" if window == 3 else true_by_window[window]
        ),
        "x-analyst": lambda window: (
            "bearish"
            if window == 0
            else "bullish"
            if window == 1
            else true_by_window[window]
        ),
    }
    kind = {"glassnode": "onchain", "coindesk": "news", "x-analyst": "social"}
    claims: list[Claim] = []
    for window in range(6):
        for source, direction_for in vote.items():
            direction = direction_for(window)
            claims.append(
                _claim(
                    f"{source}-{window}",
                    kind[source],
                    source,
                    text[direction],
                    timestamp=window * 86_400.0,
                    direction=direction,
                )
            )
    return claims


def test_legacy_offline_ds_trace_golden():
    claims = _ds_claims()

    actual = score(
        claims,
        now=6 * 86_400.0,
        dynamic_reputation=True,
        stance_client=_NoStanceClient(),
        offline=True,
    )
    selected = {
        item.claim.doc.source: _payload(item)
        for item in actual
        if item.claim.id in {"glassnode-5", "coindesk-5", "x-analyst-5"}
    }

    assert selected == {
        "glassnode": {
            "claim_id": "glassnode-5",
            "trust": 0.7112499997815038,
            "components": {
                "reputation": 0.9724999995630077,
                "corroboration": 0.75,
                "recency": 0.25,
                "manipulation": 0.0,
            },
            "reputation_trace": {
                "source": "glassnode",
                "prior": 0.95,
                "final": 0.9725,
                "agree_n": 6,
                "contradict_n": 0,
                "iterations_run": 2,
                "mode": "ds_em",
            },
            "manip_flags": [],
            "info_flags": [],
        },
        "coindesk": {
            "claim_id": "coindesk-5",
            "trust": 0.5499999999999999,
            "components": {
                "reputation": 0.65,
                "corroboration": 0.75,
                "recency": 0.25,
                "manipulation": 0.0,
            },
            "reputation_trace": {
                "source": "coindesk",
                "prior": 0.65,
                "final": 0.65,
                "agree_n": 6,
                "contradict_n": 1,
                "iterations_run": 2,
                "mode": "ds_em",
            },
            "manip_flags": [],
            "info_flags": [],
        },
        "x-analyst": {
            "claim_id": "x-analyst-5",
            "trust": 0.39999999999999997,
            "components": {
                "reputation": 0.35,
                "corroboration": 0.75,
                "recency": 0.25,
                "manipulation": 0.0,
            },
            "reputation_trace": {
                "source": "x-analyst",
                "prior": 0.35,
                "final": 0.35,
                "agree_n": 6,
                "contradict_n": 2,
                "iterations_run": 2,
                "mode": "ds_em",
            },
            "manip_flags": [],
            "info_flags": [],
        },
    }


def test_legacy_dynamic_callback_sequence_and_full_payload_golden():
    specs = [
        (
            "c-sec",
            "regulatory",
            "sec",
            "SEC BTC ETF 資金 流入 推動 市場 上漲 pump alpha",
        ),
        (
            "c-news",
            "news",
            "coindesk",
            "Coindesk BTC ETF 資金 流入 推動 市場 上漲 pump beta",
        ),
        (
            "c-chain",
            "onchain",
            "glassnode",
            "Glassnode BTC ETF 資金 流入 推動 市場 上漲 pump gamma",
        ),
        (
            "c-social",
            "social",
            "x-analyst",
            "Analyst BTC ETF 資金 流入 推動 市場 上漲 pump delta",
        ),
    ]
    claims = [
        _claim(claim_id, kind, source, text, direction="bullish")
        for claim_id, kind, source, text in specs
    ]
    id_by_text = {claim.text: claim.id for claim in claims}
    callback_pairs: list[tuple[str, str]] = []

    def stance(target_text: str, candidate_text: str) -> str:
        callback_pairs.append((id_by_text[target_text], id_by_text[candidate_text]))
        return "entailment"

    actual = score(
        claims,
        now=BASE_TS + 3_600,
        dynamic_reputation=True,
        reputation_iterations=3,
        stance_fn=stance,
    )

    # Legacy performs one 12-pair W2 evidence pass, then one 12-pair
    # per-claim corroboration pass.  #451 must preserve this externally
    # visible callback order/count while delegating only resolved values.
    assert callback_pairs == [
        ("c-sec", "c-news"),
        ("c-sec", "c-chain"),
        ("c-sec", "c-social"),
        ("c-news", "c-sec"),
        ("c-news", "c-chain"),
        ("c-news", "c-social"),
        ("c-chain", "c-sec"),
        ("c-chain", "c-news"),
        ("c-chain", "c-social"),
        ("c-social", "c-sec"),
        ("c-social", "c-news"),
        ("c-social", "c-chain"),
        ("c-sec", "c-news"),
        ("c-sec", "c-chain"),
        ("c-sec", "c-social"),
        ("c-news", "c-sec"),
        ("c-news", "c-chain"),
        ("c-news", "c-social"),
        ("c-chain", "c-sec"),
        ("c-chain", "c-news"),
        ("c-chain", "c-social"),
        ("c-social", "c-sec"),
        ("c-social", "c-news"),
        ("c-social", "c-chain"),
    ]
    assert [item.claim is claim for item, claim in zip(actual, claims)] == [
        True,
        True,
        True,
        True,
    ]
    assert [_payload(item) for item in actual] == [
        {
            "claim_id": "c-sec",
            "trust": 0.6357384871376921,
            "components": {
                "reputation": 0.8708146804708763,
                "corroboration": 0.875,
                "recency": 0.9438743126816935,
                "manipulation": 0.4,
            },
            "reputation_trace": {
                "source": "sec",
                "prior": 0.9,
                "final": 0.8708,
                "agree_n": 3,
                "contradict_n": 0,
                "iterations_run": 2,
                "mode": "entailment",
            },
            "manip_flags": ["pump"],
            "info_flags": [],
        },
        {
            "claim_id": "c-news",
            "trust": 0.5689498313089109,
            "components": {
                "reputation": 0.7372373688133138,
                "corroboration": 0.875,
                "recency": 0.9438743126816935,
                "manipulation": 0.4,
            },
            "reputation_trace": {
                "source": "coindesk",
                "prior": 0.65,
                "final": 0.7372,
                "agree_n": 3,
                "contradict_n": 0,
                "iterations_run": 2,
                "mode": "entailment",
            },
            "manip_flags": ["pump"],
            "info_flags": [],
        },
        {
            "claim_id": "c-chain",
            "trust": 0.6490870751531669,
            "components": {
                "reputation": 0.8975118565018259,
                "corroboration": 0.875,
                "recency": 0.9438743126816935,
                "manipulation": 0.4,
            },
            "reputation_trace": {
                "source": "glassnode",
                "prior": 0.95,
                "final": 0.8975,
                "agree_n": 3,
                "contradict_n": 0,
                "iterations_run": 2,
                "mode": "entailment",
            },
            "manip_flags": ["pump"],
            "info_flags": [],
        },
        {
            "claim_id": "c-social",
            "trust": 0.41082819141813515,
            "components": {
                "reputation": 0.5809940890317624,
                "corroboration": 0.875,
                "recency": 0.9438743126816935,
                "manipulation": 0.6000000000000001,
            },
            "reputation_trace": {
                "source": "x-analyst",
                "prior": 0.35,
                "final": 0.581,
                "agree_n": 3,
                "contradict_n": 0,
                "iterations_run": 2,
                "mode": "entailment",
            },
            "manip_flags": ["pump"],
            "info_flags": [],
        },
    ]
