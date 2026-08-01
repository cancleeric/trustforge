"""#864 退件修正：report/API 端到端驗證 cross_source_signal。

驗證 build_report 輸出的 Report 物件中 cross_source_signal 欄位：
  - divergence 時含 type、supporting_claim_ids、summary
  - claim_id 格式正確且可追溯
  - summary 含方向標籤

使用固定 fixture 走完 build_report，不打 Bedrock（offline=True）。
"""
from __future__ import annotations

import json
import re

import pytest

from trustforge.agent.orchestrator import build_report, detect_cross_source_signal
from trustforge.bedrock import BedrockClient
from trustforge.execlog import ExecutionLog
from trustforge.ingestion.base import Document
from trustforge.schema import Evidence, QuestionType
from trustforge import web
from trustforge.trust.scoring import (
    Claim,
    ScoredClaim,
    TrustedBrief,
    extract_claims,
    score,
    aggregate,
)


NOW = 1_750_000_000.0
CLAIM_ID_RE = re.compile(r"[\w\-]+#(?:llm)?\d+")


def _doc(id_: str, kind: str, source: str, text: str, ts: float = NOW, meta: dict | None = None) -> Document:
    return Document(id=id_, kind=kind, source=source, text=text, ts=ts, url="", meta=meta or {})


def _sc(id_: str, kind: str, source: str, direction: str, trust: float) -> ScoredClaim:
    """手工構造 ScoredClaim，供直接測試 detect_cross_source_signal。"""
    doc = _doc(id_, kind, source, text=f"claim-{id_}")
    claim = Claim(id=id_, text=f"claim-{id_}", doc=doc, direction=direction)
    return ScoredClaim(claim=claim, trust=trust)


# ===========================================================================
# Test: Report.cross_source_signal 端到端
# ===========================================================================

class TestReportCrossSourceSignal:
    """驗證 build_report 產出的 Report 中 cross_source_signal 可見性。"""

    def test_divergence_signal_in_report(self):
        """客觀 bullish + 情緒 bearish → Report 含 divergence signal。"""
        # 手工構造 scored claims（已通過 scoring pipeline 的結果）
        scored = [
            # 客觀類：price bullish, trust > 0.5
            _sc("price_btc_001#0", "price", "ohlcv-csv", "bullish", 0.85),
            _sc("price_btc_001#1", "price", "ohlcv-csv", "bullish", 0.80),
            # 情緒類：news bearish from 2 different sources, trust > 0.5
            _sc("news_cd_001#0", "news", "coindesk", "bearish", 0.65),
            _sc("news_ct_001#0", "news", "cointelegraph", "bearish", 0.60),
        ]

        # 直接呼叫 detect_cross_source_signal 驗證行為
        signal = detect_cross_source_signal(scored)
        assert signal is not None
        assert signal["type"] == "divergence"
        assert signal["objective_direction"] == "bullish"
        assert signal["sentiment_direction"] == "bearish"
        assert "supporting_claim_ids" in signal
        assert len(signal["supporting_claim_ids"]) > 0

    def test_consensus_signal_in_report(self):
        """客觀 + 情緒同向 bullish → consensus signal。"""
        scored = [
            _sc("price_btc_002#0", "price", "ohlcv-csv", "bullish", 0.85),
            _sc("news_cd_002#0", "news", "coindesk", "bullish", 0.65),
            _sc("news_ct_002#0", "news", "cointelegraph", "bullish", 0.60),
        ]
        signal = detect_cross_source_signal(scored)
        assert signal is not None
        assert signal["type"] == "consensus"
        assert signal["objective_direction"] == "bullish"
        assert signal["sentiment_direction"] == "bullish"

    def test_signal_in_build_report_output(self, monkeypatch):
        """build_report 結果經真實 API handler 序列化後仍含完整 signal。"""
        # 構造一組有分歧的 claims
        price_doc = _doc("price_001", "price", "ohlcv-csv",
                         "BTC 2025-06-14 C=68000", meta={"coin": "BTC", "close": 68000, "date": "2025-06-14"})
        news_doc = _doc("news_001", "news", "coindesk",
                        "BTC faces significant selling pressure amid regulatory concerns",
                        meta={"coin": "BTC"})
        news_doc2 = _doc("news_002", "news", "cointelegraph",
                         "Bitcoin encountering strong selling pressure from regulatory uncertainty",
                         meta={"coin": "BTC"})

        price_claim = Claim(id="price_001#0", text="BTC C=68000", doc=price_doc, direction="bullish")
        news_claim = Claim(id="news_001#0", text="BTC faces selling pressure", doc=news_doc, direction="bearish")
        news_claim2 = Claim(id="news_002#0", text="Bitcoin encountering selling pressure", doc=news_doc2, direction="bearish")

        scored_claims = [
            ScoredClaim(claim=price_claim, trust=0.85),
            ScoredClaim(claim=news_claim, trust=0.60),
            ScoredClaim(claim=news_claim2, trust=0.58),
        ]

        brief = TrustedBrief(
            query="BTC 近期走勢",
            supporting=scored_claims,
            contrarian=[],
            confidence=0.6,
            calibrated_confidence=0.55,
        )

        client = BedrockClient(offline=True)
        report, evidence = build_report(
            query="BTC 近期走勢",
            coin="BTC",
            qtype=QuestionType.MULTI_SOURCE,
            brief=brief,
            client=client,
            scored=scored_claims,
            run_scope_id="test-864-report-api",
        )

        # Report 應含 cross_source_signal（divergence）
        assert report.cross_source_signal is not None
        assert report.cross_source_signal["type"] == "divergence"
        assert "summary" in report.cross_source_signal
        assert len(report.cross_source_signal.get("supporting_claim_ids", [])) > 0

        # 通過 `/api/analyze` 實際使用的 handler 與 JSON envelope，而非只
        # 檢查 dataclass。mock 僅隔離 connector/Bedrock，serialization、
        # public report filtering 與 response envelope 都走 production code。
        monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
        monkeypatch.setattr(
            web,
            "_do_analyze",
            lambda _qs, **_kwargs: (report, evidence, ExecutionLog()),
        )
        code, body = web._handle_api_analyze(
            {"coin": ["BTC"], "type": ["multi_source"], "q": ["#864 api evidence"]},
            client_ip="198.51.100.864",
        )
        assert code == 200
        payload = json.loads(body)
        api_signal = payload["data"]["report"]["cross_source_signal"]
        assert api_signal["type"] == "divergence"
        assert api_signal["summary"] == report.cross_source_signal["summary"]
        assert api_signal["supporting_claim_ids"] == (
            report.cross_source_signal["supporting_claim_ids"]
        )

    def test_summary_contains_direction_labels(self):
        """signal summary 含方向標籤（偏多/偏空）。"""
        scored = [
            _sc("price_003#0", "price", "ohlcv-csv", "bullish", 0.85),
            _sc("news_003#0", "news", "coindesk", "bearish", 0.65),
            _sc("news_004#0", "news", "cointelegraph", "bearish", 0.60),
        ]
        signal = detect_cross_source_signal(scored)
        assert signal is not None
        summary = signal["summary"]
        # summary 應含方向標籤
        assert "偏多" in summary or "偏空" in summary, (
            f"Summary 缺少方向標籤：{summary}"
        )

    def test_supporting_claim_ids_format(self):
        """supporting_claim_ids 格式正確（符合 claim_id pattern）。"""
        scored = [
            _sc("price_btc_004#0", "price", "ohlcv-csv", "bullish", 0.85),
            _sc("news_cd_004#0", "news", "coindesk", "bearish", 0.65),
            _sc("news_ct_004#0", "news", "cointelegraph", "bearish", 0.60),
        ]
        signal = detect_cross_source_signal(scored)
        assert signal is not None
        for cid in signal["supporting_claim_ids"]:
            assert CLAIM_ID_RE.match(cid), (
                f"claim_id '{cid}' 不符合預期格式"
            )


# ===========================================================================
# Test: claim_id 可追溯性
# ===========================================================================

class TestClaimIdTraceability:
    """signal 中的 claim_id 可追溯回原始 Document。"""

    def test_claim_ids_traceable_to_source(self):
        """每個 supporting_claim_id 可追回 ScoredClaim 的 source/kind。"""
        scored = [
            _sc("price_btc_005#0", "price", "ohlcv-csv", "bullish", 0.85),
            _sc("news_cd_005#0", "news", "coindesk", "bearish", 0.65),
            _sc("news_ct_005#0", "news", "cointelegraph", "bearish", 0.60),
        ]
        signal = detect_cross_source_signal(scored)
        assert signal is not None

        # 建立 claim_id → ScoredClaim 反查
        claim_map = {sc.claim.id: sc for sc in scored}

        for cid in signal["supporting_claim_ids"]:
            assert cid in claim_map, f"claim_id '{cid}' 不在 scored claims 中"
            sc = claim_map[cid]
            # 可追溯到 source 和 kind
            assert sc.claim.doc.source, f"claim_id '{cid}' 缺少 source"
            assert sc.claim.doc.kind, f"claim_id '{cid}' 缺少 kind"

    def test_no_investment_advice_in_summary(self):
        """summary 無投資建議字眼。"""
        scored = [
            _sc("price_btc_006#0", "price", "ohlcv-csv", "bullish", 0.85),
            _sc("news_cd_006#0", "news", "coindesk", "bearish", 0.65),
            _sc("news_ct_006#0", "news", "cointelegraph", "bearish", 0.60),
        ]
        signal = detect_cross_source_signal(scored)
        assert signal is not None
        summary = signal["summary"]
        forbidden = ["建議買入", "建議賣出", "應該買", "應該賣", "buy", "sell", "recommend"]
        for word in forbidden:
            assert word not in summary.lower(), (
                f"Summary 含投資建議字眼 '{word}': {summary}"
            )
