from __future__ import annotations

from trustforge.agent.orchestrator import build_report
from trustforge.bedrock import BedrockClient
from trustforge.execlog import ExecutionLog
from trustforge.ingestion.base import Document
from trustforge.schema import QuestionType
from trustforge.trust.scoring import Claim, ScoredClaim, TrustedBrief


def _scored_claim(
    claim_id: str,
    kind: str,
    trust: float,
    *,
    source: str | None = None,
    direction: str = "bullish",
    coin: str = "BTC",
    content_reference: str | None = None,
) -> ScoredClaim:
    source_name = source or f"{kind}-source"
    doc = Document(
        id=f"doc-{claim_id}",
        kind=kind,
        source=source_name,
        text=f"{coin} {kind} evidence {claim_id}",
        url=f"https://example.test/{claim_id}",
        ts=1000.0,
        meta={
            "coin": coin,
            "content_reference": content_reference or f"{kind} ref {claim_id}",
        },
    )
    return ScoredClaim(
        claim=Claim(
            id=claim_id,
            text=f"{coin} {kind} claim {claim_id}",
            doc=doc,
            claim_type="fact",
            direction=direction,
        ),
        trust=trust,
        components={"kind": 1.0},
    )


def _report(brief: TrustedBrief, scored: list[ScoredClaim]):
    return build_report(
        "分析 BTC 多源訊號",
        "BTC",
        QuestionType.MULTI_SOURCE,
        brief,
        client=BedrockClient(offline=True),
        log=ExecutionLog(now_fn=lambda: 1000.0),
        now_fn=lambda: 1000.0,
        scored=scored,
        run_scope_id="test-source-kind-retention",
    )


def test_rich_snapshot_retains_representative_source_kinds() -> None:
    price_1 = _scored_claim("price-1", "price", 0.92)
    price_2 = _scored_claim("price-2", "price", 0.90, source="price-source-2")
    news = _scored_claim("news-1", "news", 0.72)
    onchain = _scored_claim("onchain-1", "onchain", 0.68)
    scored = [price_1, price_2, news, onchain]
    brief = TrustedBrief(
        query="分析 BTC 多源訊號",
        supporting=[price_1, price_2],
        contrarian=[],
        confidence=0.82,
        calibrated_confidence=0.74,
    )

    report, evidence = _report(brief, scored)

    admitted_kinds = {ev.kind for ev in evidence}
    assert {"price", "news", "onchain"} <= admitted_kinds
    assert report.source_kind_distribution["price"] == 2
    assert report.source_kind_distribution["news"] == 1
    assert report.source_kind_distribution["onchain"] == 1
    assert report.excluded_source_kind_counts == {}


def test_sparse_snapshot_does_not_fabricate_source_kind_coverage() -> None:
    price_1 = _scored_claim("price-1", "price", 0.92)
    price_2 = _scored_claim("price-2", "price", 0.90)
    scored = [price_1, price_2]
    brief = TrustedBrief(
        query="分析 BTC 稀疏訊號",
        supporting=[price_1],
        contrarian=[],
        confidence=0.6,
        calibrated_confidence=0.3,
    )

    report, evidence = _report(brief, scored)

    assert {ev.kind for ev in evidence} == {"price"}
    assert report.source_kind_distribution == {"price": 1}
    assert report.excluded_source_kind_counts == {"price": 1}


def test_contrarian_claim_is_not_promoted_as_supporting_representative() -> None:
    price = _scored_claim("price-1", "price", 0.92)
    news = _scored_claim("news-1", "news", 0.72)
    bearish_onchain = _scored_claim(
        "onchain-bearish-1",
        "onchain",
        0.88,
        direction="bearish",
    )
    scored = [price, news, bearish_onchain]
    brief = TrustedBrief(
        query="分析 BTC 多源訊號",
        supporting=[price, news],
        contrarian=[bearish_onchain],
        confidence=0.82,
        calibrated_confidence=0.74,
    )

    report, evidence = _report(brief, scored)

    supporting_evidence = [
        ev for ev in evidence if ev.related_claim == "BTC 市場判斷"
    ]
    onchain_indices = {idx for idx, ev in enumerate(evidence) if ev.kind == "onchain"}
    assert [ev.kind for ev in supporting_evidence] == ["price", "news"]
    assert all("onchain-bearish-1" not in ev.claim_id for ev in supporting_evidence)
    assert all(
        not onchain_indices.intersection(basis.evidence_idx)
        for basis in report.key_basis
    )


def test_off_coin_claim_is_not_promoted_as_supporting_representative() -> None:
    price = _scored_claim("price-1", "price", 0.92)
    news = _scored_claim("news-1", "news", 0.72)
    eth_onchain = _scored_claim("eth-onchain-1", "onchain", 0.94, coin="ETH")
    scored = [price, news, eth_onchain]
    brief = TrustedBrief(
        query="分析 BTC 多源訊號",
        supporting=[price, news],
        contrarian=[],
        confidence=0.82,
        calibrated_confidence=0.74,
    )

    report, evidence = _report(brief, scored)

    assert all("eth-onchain-1" not in ev.claim_id for ev in evidence)
    assert all("ETH onchain claim eth-onchain-1" != basis.claim for basis in report.key_basis)
    assert report.source_kind_distribution == {"price": 1, "news": 1}
    assert report.excluded_source_kind_counts == {"onchain": 1}


def test_deduped_representative_does_not_add_mismatched_basis() -> None:
    price = _scored_claim(
        "price-1",
        "price",
        0.92,
        source="shared-feed",
        content_reference="shared ref",
    )
    news = _scored_claim("news-1", "news", 0.72)
    onchain_duplicate = _scored_claim(
        "onchain-duplicate",
        "onchain",
        0.88,
        source="shared-feed",
        content_reference="shared ref",
    )
    scored = [price, news, onchain_duplicate]
    brief = TrustedBrief(
        query="分析 BTC 多源訊號",
        supporting=[price, news],
        contrarian=[],
        confidence=0.82,
        calibrated_confidence=0.74,
    )

    report, evidence = _report(brief, scored)

    assert sum(ev.source == "shared-feed" for ev in evidence) == 1
    assert all("onchain-duplicate" not in ev.claim_id for ev in evidence)
    assert all(basis.claim != onchain_duplicate.claim.text for basis in report.key_basis)
