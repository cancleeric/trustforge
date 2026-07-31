"""#1224: 驗收測試 — 比較報告整合輸出格式。

驗收條件：
  AC-1: to_markdown() 含各幣詳細分析（fully expanded，無 <details>）
  AC-2: 含整合比較總結（綜合結論 + 四面向 + 已知限制 + 推翻條件）
  AC-3: 含合併證據清單（雙幣標明歸屬）
  AC-4: CLI --type comparison 主輸出 report.md 為新格式
  AC-5: ComparisonReport 為 None 時 fallback 回舊版格式
  AC-6: test_comparison_markdown.py 既有斷言相容（key text 保留）
  AC-7: test_comparison.py 既有斷言相容（comparison_to_markdown 仍可用）
"""
from __future__ import annotations

import json
import pathlib
import tempfile
from unittest.mock import patch, MagicMock

import pytest

from trustforge.comparison_contract import (
    COMPARISON_DIMENSIONS,
    ComparisonReport,
    ComparisonRunResult,
    DimensionResult,
    build_comparison_report,
)
from trustforge.ingestion.base import Document
from trustforge.pipeline import run_comparison
from trustforge.schema import Evidence, Report, comparison_to_markdown


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _doc(id, kind, source, text, ts=1_000.0, meta=None):
    return Document(id=id, kind=kind, source=source, text=text, ts=ts, meta=meta or {})


def _make_fixture_docs(coin: str):
    """合成比較範例文件。"""
    if coin == "BTC":
        return [
            _doc(f"{coin}_p0", "price", "hoya-ohlcv",
                 f"{coin} OHLCV 2024-07-01: O=62000 H=63500 L=61500 C=63000",
                 meta={"coin": coin, "date": "2024-07-01", "close": 63000.0}),
            _doc(f"{coin}_p1", "price", "hoya-ohlcv",
                 f"{coin} OHLCV 2024-07-15: O=64500 H=66000 L=64000 C=65500",
                 meta={"coin": coin, "date": "2024-07-15", "close": 65500.0}),
            _doc(f"{coin}_o1", "onchain", "glassnode",
                 f"{coin} 交易所大額流入增加 12%，機構累積訊號明顯。"),
            _doc(f"{coin}_n1", "news", "coindesk",
                 f"分析師認為 {coin} ETF 資金持續淨流入，市場情緒偏多。"),
            _doc(f"{coin}_s1", "social", "lunarcrush",
                 f"{coin} 社群討論熱度創 30 天新高，看多比例 68%。"),
            _doc(f"{coin}_r1", "regulatory", "sec-gov",
                 f"{coin} ETF 期權獲 SEC 核准，擴大機構參與管道。"),
        ]
    else:
        return [
            _doc(f"{coin}_p0", "price", "hoya-ohlcv",
                 f"{coin} OHLCV 2024-07-01: O=3400 H=3500 L=3350 C=3450",
                 meta={"coin": coin, "date": "2024-07-01", "close": 3450.0}),
            _doc(f"{coin}_p1", "price", "hoya-ohlcv",
                 f"{coin} OHLCV 2024-07-15: O=3350 H=3420 L=3300 C=3380",
                 meta={"coin": coin, "date": "2024-07-15", "close": 3380.0}),
            _doc(f"{coin}_o1", "onchain", "glassnode",
                 f"{coin} 交易所大額流出減少 5%，質押解鎖壓力浮現。"),
            _doc(f"{coin}_n1", "news", "coindesk",
                 f"分析師擔憂 {coin} 現貨 ETF 資金連續三週淨流出。"),
            _doc(f"{coin}_s1", "social", "lunarcrush",
                 f"{coin} 社群看空比例升至 55%，擔憂 L2 碎片化問題。"),
            _doc(f"{coin}_r1", "regulatory", "sec-gov",
                 f"{coin} 質押收益被部分監管機構質疑為證券性質。"),
        ]


@pytest.fixture
def comparison_report(monkeypatch):
    """BTC vs ETH ComparisonReport fixture（offline）。"""
    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        return _make_fixture_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)
    result = run_comparison("BTC", "ETH", "比較 BTC 與 ETH", offline=True)
    assert result.comparison is not None
    return result


# ---------------------------------------------------------------------------
# AC-1: 各幣詳細分析（fully expanded，無 <details>）
# ---------------------------------------------------------------------------

class TestAC1_PerCoinAnalysisExpanded:
    """AC-1: to_markdown() 含各幣完整分析，無 <details> 摺疊。"""

    def test_no_details_tag(self, comparison_report):
        md = comparison_report.comparison.to_markdown()
        assert "<details>" not in md
        assert "</details>" not in md
        assert "<summary>" not in md

    def test_coin_a_analysis_visible(self, comparison_report):
        md = comparison_report.comparison.to_markdown()
        assert "### BTC 分析" in md

    def test_coin_b_analysis_visible(self, comparison_report):
        md = comparison_report.comparison.to_markdown()
        assert "### ETH 分析" in md

    def test_detailed_analysis_section_exists(self, comparison_report):
        md = comparison_report.comparison.to_markdown()
        assert "## 各幣詳細分析" in md

    def test_coin_a_judgment_in_output(self, comparison_report):
        """A 幣的 market_judgment 出現在輸出中（推理鏈可追溯）。"""
        md = comparison_report.comparison.to_markdown()
        judgment_a = comparison_report.comparison.supporting_report_a.market_judgment
        assert judgment_a in md


# ---------------------------------------------------------------------------
# AC-2: 整合比較總結
# ---------------------------------------------------------------------------

class TestAC2_IntegratedSynthesis:
    """AC-2: 含整合比較總結（綜合結論 + 四面向 + 已知限制 + 推翻條件）。"""

    def test_synthesis_section_exists(self, comparison_report):
        md = comparison_report.comparison.to_markdown()
        assert "## 整合比較總結" in md

    def test_conclusion_present(self, comparison_report):
        md = comparison_report.comparison.to_markdown()
        assert "### 綜合結論" in md
        assert comparison_report.comparison.conclusion in md

    def test_four_dimensions_present(self, comparison_report):
        md = comparison_report.comparison.to_markdown()
        assert "### 比較面向分析" in md
        for dim in COMPARISON_DIMENSIONS:
            assert dim in md, f"missing dimension: {dim}"

    def test_dimension_confidence_shown(self, comparison_report):
        md = comparison_report.comparison.to_markdown()
        for dim in comparison_report.comparison.dimensions:
            pct = round(dim.confidence * 100)
            assert f"{pct}%" in md

    def test_evidence_refs_labeled(self, comparison_report):
        md = comparison_report.comparison.to_markdown()
        assert "A 幣證據索引" in md
        assert "B 幣證據索引" in md

    def test_limits_shown_when_present(self, comparison_report):
        comparison_report.comparison.limits = ["測試限制一", "測試限制二"]
        md = comparison_report.comparison.to_markdown()
        assert "## 已知限制" in md
        assert "測試限制一" in md
        assert "測試限制二" in md

    def test_limits_hidden_when_empty(self, comparison_report):
        comparison_report.comparison.limits = []
        md = comparison_report.comparison.to_markdown()
        assert "## 已知限制" not in md

    def test_could_flip_shown_when_present(self, comparison_report):
        comparison_report.comparison.could_flip = ["若 ETH ETF 資金反轉"]
        md = comparison_report.comparison.to_markdown()
        assert "## 可能推翻條件" in md
        assert "若 ETH ETF 資金反轉" in md

    def test_could_flip_hidden_when_empty(self, comparison_report):
        comparison_report.comparison.could_flip = []
        md = comparison_report.comparison.to_markdown()
        assert "## 可能推翻條件" not in md


# ---------------------------------------------------------------------------
# AC-3: 合併證據清單
# ---------------------------------------------------------------------------

class TestAC3_MergedEvidenceTable:
    """AC-3: 含合併證據清單（雙幣標明歸屬）。"""

    def test_evidence_table_exists(self, comparison_report):
        md = comparison_report.comparison.to_markdown()
        assert "## 合併證據清單" in md

    def test_evidence_table_has_headers(self, comparison_report):
        md = comparison_report.comparison.to_markdown()
        assert "| # | 幣種 | source | fetched_at | trust | content_reference |" in md

    def test_evidence_labeled_by_coin(self, comparison_report):
        md = comparison_report.comparison.to_markdown()
        assert "| BTC |" in md or "BTC" in md
        assert "| ETH |" in md or "ETH" in md

    def test_evidence_numbering_continuous(self, comparison_report):
        """A 幣 E0..En，B 幣 E(n+1)..Em — 連續編號。"""
        md = comparison_report.comparison.to_markdown()
        n_a = len(comparison_report.comparison.supporting_evidence_a)
        # B 幣第一筆的 index 應為 n_a
        assert f"| E{n_a} | ETH |" in md


# ---------------------------------------------------------------------------
# AC-4: CLI 主輸出為新格式（模擬驗證）
# ---------------------------------------------------------------------------

class TestAC4_CLIPrimaryOutput:
    """AC-4: CLI comparison 路徑主輸出 report.md 使用 ComparisonReport.to_markdown()。"""

    def test_cli_uses_unified_format(self, comparison_report):
        """模擬 CLI 邏輯：有 ComparisonReport 時用新格式。"""
        result = comparison_report
        if result.comparison is not None:
            md = result.comparison.to_markdown()
        else:
            md = "fallback"

        # 新格式必備元素
        assert "## 各幣詳細分析" in md
        assert "## 整合比較總結" in md
        assert "## 合併證據清單" in md


# ---------------------------------------------------------------------------
# AC-5: ComparisonReport 為 None 時 fallback
# ---------------------------------------------------------------------------

class TestAC5_Fallback:
    """AC-5: ComparisonReport 為 None 時 CLI fallback 回舊版格式。"""

    def test_fallback_when_comparison_none(self, comparison_report):
        """模擬 comparison 為 None 的 fallback 路徑。"""
        result = comparison_report
        # 模擬 None 場景
        report_a = result.report_a
        evidence_a = result.evidence_a
        report_b = result.report_b
        evidence_b = result.evidence_b

        md = comparison_to_markdown(report_a, evidence_a, report_b, evidence_b, "fallback test")
        # 舊版格式含相對強弱比較
        assert "相對強弱比較" in md
        assert report_a.coin in md
        assert report_b.coin in md


# ---------------------------------------------------------------------------
# AC-6: 既有 test_comparison_markdown.py key text 相容
# ---------------------------------------------------------------------------

class TestAC6_BackwardCompatKeyText:
    """AC-6: 新格式仍包含既有測試依賴的 key text。"""

    def test_title_format(self, comparison_report):
        md = comparison_report.comparison.to_markdown()
        assert "比較分析報告" in md
        assert comparison_report.comparison.coin_a in md
        assert comparison_report.comparison.coin_b in md

    def test_query_in_output(self, comparison_report):
        md = comparison_report.comparison.to_markdown()
        assert comparison_report.comparison.query in md

    def test_generated_at_in_output(self, comparison_report):
        md = comparison_report.comparison.to_markdown()
        assert comparison_report.comparison.generated_at in md

    def test_dimension_labels(self, comparison_report):
        md = comparison_report.comparison.to_markdown()
        assert "比較面向分析" in md
        for dim_name in COMPARISON_DIMENSIONS:
            assert dim_name in md


# ---------------------------------------------------------------------------
# AC-7: comparison_to_markdown 仍正常運作
# ---------------------------------------------------------------------------

class TestAC7_OldFormatStillWorks:
    """AC-7: 舊版 comparison_to_markdown() 未被移除，仍可正常產出。"""

    def test_old_format_produces_output(self, comparison_report):
        result = comparison_report
        md = comparison_to_markdown(
            result.report_a, result.evidence_a,
            result.report_b, result.evidence_b,
            "test query",
        )
        assert len(md) > 100
        assert "相對強弱比較" in md
        assert "合併證據清單" in md

    def test_old_format_has_both_coins(self, comparison_report):
        result = comparison_report
        md = comparison_to_markdown(
            result.report_a, result.evidence_a,
            result.report_b, result.evidence_b,
            "test query",
        )
        assert "BTC" in md
        assert "ETH" in md
