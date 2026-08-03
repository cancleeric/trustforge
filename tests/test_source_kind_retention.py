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
) -> ScoredClaim:
    source_name = source or f"{kind}-source"
    doc = Document(
        id=f"doc-{claim_id}",
        kind=kind,
        source=source_name,
        text=f"BTC {kind} evidence {claim_id}",
        url=f"https://example.test/{claim_id}",
        ts=1000.0,
        meta={"coin": "BTC", "content_reference": f"{kind} ref {claim_id}"},
    )
    return ScoredClaim(
        claim=Claim(
            id=claim_id,
            text=f"BTC {kind} claim {claim_id}",
            doc=doc,
            claim_type="fact",
            direction="bullish",
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
