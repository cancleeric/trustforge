"""CA-08: ComparisonReport.to_markdown() 的輸出驗證。

驗證 to_markdown() Markdown 輸出含：
  - 四個比較面向
  - 綜合結論（conclusion）
  - 已知限制（limits）
  - 可能推翻條件（could_flip）
  - 各幣詳細分析（摺疊區）
  - report.md 檔案正確寫入（CLI 行為）
"""
from __future__ import annotations

import pathlib
import tempfile

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
from trustforge.schema import Evidence, Report


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _doc(id, kind, source, text, ts=1_000.0, meta=None):
    return Document(id=id, kind=kind, source=source, text=text, ts=ts, meta=meta or {})


def _make_fixture_docs(coin: str):
    """合成一組官方比較範例文件。"""
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
    else:  # ETH
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
def btc_eth_comparison(monkeypatch):
    """BTC vs ETH 完整 ComparisonReport fixture（offline）。"""
    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        return _make_fixture_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)
    result = run_comparison("BTC", "ETH", "比較 BTC 與 ETH 的市場表現、鏈上活動、情緒與生態發展", offline=True)
    assert result.comparison is not None, "comparison 應已填入"
    return result.comparison


# ---------------------------------------------------------------------------
# to_markdown() 輸出驗證
# ---------------------------------------------------------------------------

class TestComparisonToMarkdown:
    """CA-08: ComparisonReport.to_markdown() 輸出結構驗證。"""

    def test_to_markdown_has_title_and_meta(self, btc_eth_comparison):
        """標題與基本資訊在輸出中。"""
        md = btc_eth_comparison.to_markdown()
        assert btc_eth_comparison.coin_a in md
        assert btc_eth_comparison.coin_b in md
        assert "比較分析報告" in md
        assert btc_eth_comparison.query in md
        assert btc_eth_comparison.generated_at in md

    def test_to_markdown_has_conclusion(self, btc_eth_comparison):
        """綜合結論出現在輸出中。"""
        md = btc_eth_comparison.to_markdown()
        assert "綜合結論" in md
        assert btc_eth_comparison.conclusion in md

    def test_to_markdown_has_four_dimensions(self, btc_eth_comparison):
        """四個比較面向全部出現在輸出中。"""
        md = btc_eth_comparison.to_markdown()
        assert "比較面向分析" in md
        for dim in COMPARISON_DIMENSIONS:
            # DIMENSION_LABEL_MAP 的值如「價格動能比較」會出現在 markdown 中
            assert dim in md, f"missing dimension: {dim}"

    def test_to_markdown_dimension_has_confidence(self, btc_eth_comparison):
        """每個面向的信心值都有輸出。"""
        md = btc_eth_comparison.to_markdown()
        for dim in btc_eth_comparison.dimensions:
            assert dim.finding in md, f"missing finding for {dim.dimension}"
            # 信心以百分比顯示（:.0% 四捨五入，與 int 截斷不同，用 round）
            pct = round(dim.confidence * 100)
            assert f"{pct}%" in md, f"missing confidence {pct}% for {dim.dimension}"

    def test_to_markdown_has_evidence_refs(self, btc_eth_comparison):
        """Evidence refs 索引在輸出中可見。"""
        md = btc_eth_comparison.to_markdown()
        assert "A 幣證據索引" in md
        assert "B 幣證據索引" in md

    def test_to_markdown_has_limits(self, btc_eth_comparison):
        """已知限制出現在輸出中（若有）。"""
        btc_eth_comparison.limits = ["測試限制：資料窗不足", "ETH 鏈上資料僅兩週"]
        md = btc_eth_comparison.to_markdown()
        assert "已知限制" in md
        assert "測試限制：資料窗不足" in md
        assert "ETH 鏈上資料僅兩週" in md

    def test_to_markdown_has_could_flip(self, btc_eth_comparison):
        """可能推翻條件出現在輸出中（若有）。"""
        btc_eth_comparison.could_flip = ["若 ETH ETF 資金轉為淨流入", "若 BTC 出現重大監管事件"]
        md = btc_eth_comparison.to_markdown()
        assert "可能推翻條件" in md
        assert "若 ETH ETF 資金轉為淨流入" in md
        assert "若 BTC 出現重大監管事件" in md

    def test_to_markdown_has_detailed_analysis(self, btc_eth_comparison):
        """各幣詳細分析區塊在輸出中。"""
        md = btc_eth_comparison.to_markdown()
        assert "各幣詳細分析" in md
        assert btc_eth_comparison.coin_a in md
        assert btc_eth_comparison.coin_b in md

    def test_to_markdown_no_empty_sections_when_no_limits(self, btc_eth_comparison):
        """無已知限制時不出現 ComparisonReport 層級的已知限制章節。

        注意：內嵌的 supporting Report.to_markdown() 可能自帶「已知限制」文字，
        那是各幣報告的資訊完整度說明，非 ComparisonReport 層級的限制章節。
        本測試驗證的是 ComparisonReport-level 的 `## 已知限制` 不存在。
        """
        btc_eth_comparison.limits = []
        btc_eth_comparison.could_flip = []
        md = btc_eth_comparison.to_markdown()
        # ComparisonReport 層級的已知限制章節開頭是 `## 已知限制`
        assert "## 已知限制" not in md
        assert "## 可能推翻條件" not in md

    def test_to_markdown_empty_limits_no_section(self, btc_eth_comparison):
        """空 limits 清單不會產生 ComparisonReport-level 的章節。"""
        btc_eth_comparison.limits = []
        md = btc_eth_comparison.to_markdown()
        assert "## 已知限制" not in md

    def test_to_markdown_empty_could_flip_no_section(self, btc_eth_comparison):
        """空 could_flip 清單不會產生 ComparisonReport-level 的章節。"""
        btc_eth_comparison.could_flip = []
        md = btc_eth_comparison.to_markdown()
        assert "## 可能推翻條件" not in md

    def test_to_markdown_has_confidence_section(self, btc_eth_comparison):
        """整體比較信心出現在綜合結論區。"""
        btc_eth_comparison.confidence = 0.78
        md = btc_eth_comparison.to_markdown()
        assert "整體比較信心" in md
        assert "78%" in md


# ---------------------------------------------------------------------------
# CLI report.md 寫入驗證
# ---------------------------------------------------------------------------

class TestCLIComparisonMarkdown:
    """CA-08: CLI 的 report.md / comparison_report.md 輸出驗證。"""

    def test_cli_writes_report_md_and_comparison_report_md(self, monkeypatch):
        """CLI comparison 產生 report.md 與 comparison_report.md。"""
        def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
            return _make_fixture_docs(coin)

        monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

        from trustforge.cli import main

        with tempfile.TemporaryDirectory() as tmpdir:
            ret = main([
                "analyze",
                "--coin", "BTC,ETH",
                "--type", "comparison",
                "--query", "比較 BTC 與 ETH",
                "--offline",
                "--quiet",
                "--out", tmpdir,
            ])
            assert ret == 0, f"CLI 回傳非 0：{ret}"

            report_path = pathlib.Path(tmpdir) / "report.md"
            assert report_path.exists(), "report.md 應存在"

            report_content = report_path.read_text(encoding="utf-8")
            assert "BTC" in report_content
            assert "ETH" in report_content
            assert "相對強弱比較" in report_content

            # CA-08: comparison_report.md 也應存在
            cmp_path = pathlib.Path(tmpdir) / "comparison_report.md"
            assert cmp_path.exists(), "comparison_report.md 應存在（CA-08 unified format）"

            cmp_content = cmp_path.read_text(encoding="utf-8")
            assert "比較分析報告" in cmp_content
            assert "綜合結論" in cmp_content
            assert "比較面向分析" in cmp_content
            assert "各幣詳細分析" in cmp_content


# ---------------------------------------------------------------------------
# to_markdown() 完整性（四面向一定全）
# ---------------------------------------------------------------------------

class TestToMarkdownCompleteness:
    """CA-08: to_markdown() 不遺漏任何面向。"""

    def test_to_markdown_contains_each_dimension_by_name(self, btc_eth_comparison):
        """四個 COMPARISON_DIMENSIONS 全數出現。"""
        md = btc_eth_comparison.to_markdown()
        for dim in COMPARISON_DIMENSIONS:
            assert dim in md, f"輸出缺面向：{dim}"

    def test_to_markdown_dimension_count(self, btc_eth_comparison):
        """輸出的比較面向數量應為 4。"""
        md = btc_eth_comparison.to_markdown()
        # 每個面向以 "### N. " 標題形式出現
        header_count = sum(1 for dim in COMPARISON_DIMENSIONS if dim in md)
        assert header_count == 4
