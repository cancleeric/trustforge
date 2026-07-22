"""Pure aggregate and decision engine parity tests (#452)."""

from __future__ import annotations

import dataclasses
import math

import pytest

from trustforge.ingestion.base import Document
from trustforge.trust.scoring import Claim, ScoredClaim, aggregate
from trustforge_core import (
    KERNEL_CONTRACT_VERSION,
    KernelClaim,
    KernelDocument,
    KernelScoredClaim,
    UnsupportedKernelContractVersion,
    aggregate_scored_claims,
)


BASE_TS = 1_700_000_000.0


def _legacy_scored(
    claim_id: str,
    *,
    text: str,
    trust: float,
    source: str,
    kind: str = "news",
    direction: str = "bullish",
    coin: str | None = "BTC",
) -> ScoredClaim:
    meta = {} if coin is None else {"coin": coin}
    document = Document(
        claim_id.replace("#", "-"),
        kind,
        source,
        text,
        ts=BASE_TS,
        meta=meta,
    )
    return ScoredClaim(
        Claim(claim_id, text, document, "fact", direction),
        trust,
        {"reputation": trust, "recency": 1.0},
    )


def _core_scored(item: ScoredClaim) -> KernelScoredClaim:
    doc = item.claim.doc
    metadata = tuple(
        (key, value)
        for key, value in doc.meta.items()
        if type(key) is str and type(value) in {str, int, float, bool}
    )
    return KernelScoredClaim(
        KernelClaim(
            item.claim.id,
            item.claim.text,
            KernelDocument(
                doc.id,
                doc.kind,
                doc.source,
                doc.text,
                doc.ts,
                doc.url,
                metadata,
            ),
            item.claim.claim_type,
            item.claim.direction,
        ),
        item.trust,
        tuple(item.components.items()),
        manip_flags=tuple(item.manip_flags),
        info_flags=tuple(item.info_flags),
    )


def _parity_payload(legacy_items: list[ScoredClaim], *, query: str, coin: str = "") -> dict:
    legacy = aggregate(legacy_items, query=query, coin=coin or None)
    core = aggregate_scored_claims(
        tuple(_core_scored(item) for item in legacy_items),
        query=query,
        coin=coin,
    )
    return {
        "legacy_supporting": [item.claim.id for item in legacy.supporting],
        "core_supporting": [item.claim.id for item in core.supporting],
        "legacy_contrarian": [item.claim.id for item in legacy.contrarian],
        "core_contrarian": [item.claim.id for item in core.contrarian],
        "legacy_confidence": legacy.confidence,
        "core_confidence": core.trust_score,
        "legacy_calibrated": legacy.calibrated_confidence,
        "core_calibrated": core.confidence,
    }


def test_core_aggregate_matches_legacy_support_contrarian_and_confidence():
    scored = [
        _legacy_scored("a#0", text="BTC ETF inflow supports price", trust=0.91, source="sec"),
        _legacy_scored("b#0", text="BTC demand grows", trust=0.72, source="coindesk"),
        _legacy_scored(
            "c#0",
            text="BTC liquidity is thin",
            trust=0.28,
            source="wire",
            direction="bearish",
        ),
        _legacy_scored("d#0", text="unrelated SOL note", trust=0.99, source="other", coin="SOL"),
    ]

    assert _parity_payload(scored, query="BTC", coin="BTC") == {
        "legacy_supporting": ["a#0", "b#0"],
        "core_supporting": ["a#0", "b#0"],
        "legacy_contrarian": ["c#0"],
        "core_contrarian": ["c#0"],
        "legacy_confidence": pytest.approx(0.815),
        "core_confidence": pytest.approx(0.815),
        "legacy_calibrated": pytest.approx(0.5186),
        "core_calibrated": pytest.approx(0.5186),
    }


def test_core_aggregate_preserves_sparse_empty_neutral_and_abstain_decision():
    result = aggregate_scored_claims(
        tuple(),
        query="BTC",
    )

    assert result.supporting == ()
    assert result.contrarian == ()
    assert result.trust_score == 0.0
    assert result.confidence == 0.0
    assert result.abstain is True
    assert result.direction == "不明"
    assert result.decision_state == "abstain"
    assert result.reason_codes == ("low_confidence", "no_supporting_claims")


def test_core_aggregate_coin_scope_keeps_market_wide_and_excludes_other_coin():
    scored = [
        _legacy_scored("btc#0", text="BTC demand grows", trust=0.62, source="btc-wire"),
        _legacy_scored(
            "market#0",
            text="ETF market liquidity improves",
            trust=0.58,
            source="market-wire",
            coin=None,
        ),
        _legacy_scored("eth#0", text="ETH demand grows", trust=0.99, source="eth-wire", coin="ETH"),
    ]

    result = aggregate_scored_claims(
        tuple(_core_scored(item) for item in scored),
        query="BTC",
        coin="BTC",
    )

    assert [item.claim.id for item in result.supporting] == ["btc#0", "market#0"]
    assert result.trust_score == pytest.approx(0.6)


def test_core_aggregate_rejects_unknown_version_and_nonfinite_values():
    scored = (
        KernelScoredClaim(
            KernelClaim(
                "claim",
                "BTC demand grows",
                KernelDocument("doc", "news", "wire", "BTC demand grows", BASE_TS),
                "fact",
                "bullish",
            ),
            0.7,
        ),
    )

    with pytest.raises(UnsupportedKernelContractVersion):
        aggregate_scored_claims(
            scored,
            query="BTC",
            contract_version=KERNEL_CONTRACT_VERSION + "-future",
        )
    with pytest.raises(ValueError):
        aggregate_scored_claims(scored, query="BTC", support_threshold=math.nan)
    with pytest.raises(dataclasses.FrozenInstanceError):
        scored[0].trust = 0.0  # type: ignore[misc]


def test_legacy_aggregate_normalizes_nonfinite_timestamp_before_core_boundary():
    item = _legacy_scored(
        "bad-ts#0",
        text="BTC demand grows",
        trust=0.7,
        source="malformed-feed",
    )
    item.claim.doc.ts = math.nan

    brief = aggregate([item], query="BTC", coin="BTC")

    assert [sc.claim.id for sc in brief.supporting] == ["bad-ts#0"]
