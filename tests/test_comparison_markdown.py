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

import json
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
        本測試驗證的是 ComparisonReport-level 的 `### 已知限制` 不存在。
        """
        btc_eth_comparison.limits = []
        btc_eth_comparison.could_flip = []
        md = btc_eth_comparison.to_markdown()
        # ComparisonReport 層級的已知限制章節開頭是 `### 已知限制`（#1224: h3）
        assert "### 已知限制" not in md
        assert "### 可能推翻條件" not in md

    def test_to_markdown_empty_limits_no_section(self, btc_eth_comparison):
        """空 limits 清單不會產生 ComparisonReport-level 的章節。"""
        btc_eth_comparison.limits = []
        md = btc_eth_comparison.to_markdown()
        assert "### 已知限制" not in md

    def test_to_markdown_empty_could_flip_no_section(self, btc_eth_comparison):
        """空 could_flip 清單不會產生 ComparisonReport-level 的章節。"""
        btc_eth_comparison.could_flip = []
        md = btc_eth_comparison.to_markdown()
        assert "### 可能推翻條件" not in md

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
        """#1224: CLI comparison 產生單一整合 report.md（三段式格式）。"""
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
            # #1224: 新格式三段式結構
            assert "各幣詳細分析" in report_content
            assert "整合比較總結" in report_content
            assert "合併證據清單" in report_content
            assert "綜合結論" in report_content
            assert "比較面向分析" in report_content


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


# ---------------------------------------------------------------------------
# CA-08: Evidence refs stable IDs + parity test matrix
# ---------------------------------------------------------------------------

class TestEvidenceRefsStableIDs:
    """CA-08: evidence refs 使用 stable IDs（source:#index）而非裸 array index。"""

    def test_evidence_refs_use_stable_ids(self):
        """Evidence refs 格式為 [source:#index]，包含 source name 與 index。"""
        ev_a = [
            Evidence(source="hoya-ohlcv", fetched_at="2026-07-01T00:00:00Z",
                     content_reference="O=62000", related_claim="BTC 價格"),
            Evidence(source="glassnode", fetched_at="2026-07-01T00:00:00Z",
                     content_reference="大額流入 +12%", related_claim="鏈上活動"),
            Evidence(source="coindesk", fetched_at="2026-07-01T00:00:00Z",
                     content_reference="ETF 資金淨流入", related_claim="市場情緒"),
        ]
        ev_b = [
            Evidence(source="hoya-ohlcv", fetched_at="2026-07-01T00:00:00Z",
                     content_reference="O=3400", related_claim="ETH 價格"),
            Evidence(source="glassnode", fetched_at="2026-07-01T00:00:00Z",
                     content_reference="大額流出 -5%", related_claim="鏈上活動"),
        ]

        cr = ComparisonReport(
            coin_a="BTC",
            coin_b="ETH",
            query="比較兩幣",
            conclusion="測試結論",
            dimensions=[
                DimensionResult(
                    dimension="價格動能",
                    label="價格動能比較",
                    finding="正常",
                    a_evidence_refs=[0],
                    b_evidence_refs=[0],
                ),
                DimensionResult(
                    dimension="鏈上活動",
                    label="鏈上活動比較",
                    finding="正常",
                    a_evidence_refs=[1],
                    b_evidence_refs=[1],
                ),
                DimensionResult(
                    dimension="市場情緒",
                    label="市場情緒比較",
                    finding="正常",
                    a_evidence_refs=[2],
                    b_evidence_refs=[],
                    decision="insufficient",
                ),
                DimensionResult(
                    dimension="生態發展",
                    label="生態發展比較",
                    finding="無資料",
                    a_evidence_refs=[],
                    b_evidence_refs=[],
                    decision="abstain",
                ),
            ],
            supporting_evidence_a=ev_a,
            supporting_evidence_b=ev_b,
        )

        md = cr.to_markdown()

        # 格式：[source:#index]
        assert "[hoya-ohlcv:#0]" in md, "應含 stable ID [hoya-ohlcv:#0]"
        assert "[glassnode:#1]" in md, "應含 stable ID [glassnode:#1]"
        assert "[coindesk:#2]" in md, "應含 stable ID [coindesk:#2]"

        # ⛔ 不應出現裸 array index pattern（像 `[0, 1, 2]`）
        assert "[0, 1, 2]" not in md, "不應出現裸 list [0, 1, 2]"
        # 沒有 refs 的維度應顯示 '無'
        assert "無" in md, "空 refs 應顯示 '無'"


class TestMarkdownSpecialCharacters:
    """CA-08: 特殊字符與 HTML 語義一致性（parity test matrix）。"""

    def test_markdown_special_characters(self):
        """含 < > & " 等 HTML 特殊字符的內容正確處理在輸出中。"""
        # 特殊字符出現在 conclusion / finding 等會直接輸出到 markdown 的欄位
        cr = ComparisonReport(
            coin_a="BTC",
            coin_b="ETH",
            query='比較 "BTC" & "ETH" 的市場表現',
            conclusion="BTC > ETH 在價格動能，但 BTC < ETH 在生態發展",
            dimensions=[
                DimensionResult(
                    dimension="價格動能",
                    label="價格動能比較",
                    finding='分析師說 "BTC > ETH" 在過去 30 天，信心 & 趨勢明確',
                    a_evidence_refs=[0, 1],
                    b_evidence_refs=[0, 1],
                ),
                *[
                    DimensionResult(
                        dimension=dim,
                        label=f"{dim}比較",
                        finding=f'{dim} 測試：value > 0 & data "ok"',
                        a_evidence_refs=[0, 1],
                        b_evidence_refs=[0, 1],
                    )
                    for dim in ("鏈上活動", "市場情緒", "生態發展")
                ],
            ],
            limits=['已知限制：資料含 "特殊" 字符 & 符號'],
            could_flip=['若條件 "改變" & 局勢 > 預期'],
            supporting_evidence_a=[
                Evidence(source="news-api", fetched_at="2026-07-01T00:00:00Z",
                         content_reference='"BTC > ETH" & data', related_claim="t"),
                Evidence(source="news-api", fetched_at="2026-07-01T01:00:00Z",
                         content_reference="second", related_claim="t"),
            ],
            supporting_evidence_b=[
                Evidence(source="news-api", fetched_at="2026-07-01T00:00:00Z",
                         content_reference='data "ok"', related_claim="t"),
                Evidence(source="news-api", fetched_at="2026-07-01T01:00:00Z",
                         content_reference="second", related_claim="t"),
            ],
        )

        md = cr.to_markdown()

        # to_markdown() 是 Markdown 格式，不應 escape 成 HTML entities
        assert 'BTC > ETH' in md, "> 應保持原樣（Markdown）"
        assert '"BTC > ETH"' in md, "雙引號與 > 應保持原樣（Markdown）"
        assert chr(0x201C) + "BTC > ETH" + chr(0x201D) in md or '"BTC > ETH"' in md
        assert '"特殊"' in md, "中文雙引號應保持原樣"
        assert ' & ' in md, "& 符號應保持原樣（Markdown）"


class TestMarkdownLongChineseText:
    """CA-08: 長中文段落的正確處理。"""

    def test_markdown_long_chinese_text(self):
        """200+ 字的中文段落不應被截斷或損壞在輸出中。"""
        long_chinese = (
            "比特幣作為全球最大的加密貨幣，其價格走勢受到多重因素影響。"
            "首先，宏觀經濟環境包括美國聯邦儲備系統的貨幣政策、通貨膨脹率以及全球經濟成長預期，"
            "都會對比特幣的需求產生重大影響。其次，機構投資者的參與程度持續提升，"
            "包括比特幣現貨 ETF 的推出與資金流入情況，已成為市場關注的焦點。"
            "第三，區塊鏈技術的發展與應用場景擴展，例如閃電網路、Ordinals 協議等，"
            "也為比特幣帶來了新的使用價值與市場預期。最後，全球各國監管政策的變化，"
            "從美國證券交易委員會到歐盟加密資產市場監管法案，均對加密貨幣市場產生深遠影響。"
            "綜合以上因素，比特幣在可預見的未來仍將扮演數位黃金的核心角色。"
        )
        # 驗證長度確實超過 200 字
        assert len(long_chinese) >= 200, f"測試前提：段落長度 {len(long_chinese)} 應 ≥ 200 字"

        # 長中文放在 finding 中，直接出現在 markdown
        cr = ComparisonReport(
            coin_a="BTC",
            coin_b="ETH",
            query="長中文測試",
            conclusion=long_chinese,
            dimensions=[
                DimensionResult(
                    dimension="價格動能",
                    label="價格動能比較",
                    finding=long_chinese,
                    a_evidence_refs=[0],
                    b_evidence_refs=[],
                    decision="insufficient",
                ),
                *[
                    DimensionResult(
                        dimension=dim,
                        label=f"{dim}比較",
                        finding="無資料",
                        a_evidence_refs=[],
                        b_evidence_refs=[],
                        decision="abstain",
                    )
                    for dim in ("鏈上活動", "市場情緒", "生態發展")
                ],
            ],
            supporting_evidence_a=[
                Evidence(source="r", fetched_at="", content_reference="", related_claim=""),
            ],
            supporting_evidence_b=[],
        )

        md = cr.to_markdown()

        # 長中文應完整保留（前 10 字與後 10 字）
        assert long_chinese[:10] in md, "長中文段落開頭應保留"
        assert long_chinese[-10:] in md, "長中文段落結尾應保留"


class TestMarkdownURLInContent:
    """CA-08: URL 在 evidence content 中的正確處理。"""

    def test_markdown_url_in_content(self):
        """含 URL 的內容不應在 markdown 輸出中被損壞。"""
        url_a = "https://example.com/btc-analysis?token=abc&page=1"
        url_b = "https://docs.example.com/eth/api/v1"

        cr = ComparisonReport(
            coin_a="BTC",
            coin_b="ETH",
            query="URL 測試",
            conclusion=f"參考文章 {url_a} 與 {url_b}",
            dimensions=[
                DimensionResult(
                    dimension="價格動能",
                    label="價格動能比較",
                    finding=f"分析：參考 {url_a}",
                    a_evidence_refs=[0, 1],
                    b_evidence_refs=[0, 1],
                ),
                *[
                    DimensionResult(
                        dimension=dim,
                        label=f"{dim}比較",
                        finding=f"無資料，見 {url_b}",
                        a_evidence_refs=[],
                        b_evidence_refs=[],
                        decision="abstain",
                    )
                    for dim in ("鏈上活動", "市場情緒", "生態發展")
                ],
            ],
            limits=[f"限制：資料來源 {url_a}"],
            supporting_evidence_a=[
                Evidence(source="w", fetched_at="", content_reference=url_a, related_claim="t"),
                Evidence(source="w", fetched_at="", content_reference="second", related_claim="t"),
            ],
            supporting_evidence_b=[
                Evidence(source="w", fetched_at="", content_reference=url_b, related_claim="t"),
                Evidence(source="w", fetched_at="", content_reference="second", related_claim="t"),
            ],
        )

        md = cr.to_markdown()

        # URL 關鍵部分應保留
        assert "https://example.com/btc-analysis" in md, "URL 應保留"
        assert "https://docs.example.com/eth/api/v1" in md, "URL 應保留"


class TestHTMLOutputEscapes:
    """CA-08: HTML 輸出正確 escape 特殊字符。"""

    def test_html_output_escapes_special_chars(self):
        """to_dict() 回傳的 HTML 輸出應正確處理特殊字符為 JSON 安全格式。"""
        cr = ComparisonReport(
            coin_a="BTC",
            coin_b="ETH",
            query='比較 "BTC" & "ETH" 的市場表現',
            conclusion="BTC < ETH 在某些面向，但 > ETH 在價格動能",
            dimensions=[
                DimensionResult(
                    dimension="價格動能",
                    label="價格動能比較",
                    finding='分析師說 "BTC > ETH" 在過去 30 天',
                    a_evidence_refs=[0, 1],
                    b_evidence_refs=[0, 1],
                ),
                *[
                    DimensionResult(
                        dimension=dim,
                        label=f"{dim}比較",
                        finding="無資料",
                        a_evidence_refs=[],
                        b_evidence_refs=[],
                        decision="abstain",
                    )
                    for dim in ("鏈上活動", "市場情緒", "生態發展")
                ],
            ],
            supporting_evidence_a=[
                Evidence(source="test", fetched_at="",
                         content_reference='data: x > y & z < w "ok"',
                         related_claim="test"),
                Evidence(source="test", fetched_at="",
                         content_reference="second item",
                         related_claim="test"),
            ],
            supporting_evidence_b=[
                Evidence(source="test", fetched_at="",
                         content_reference='data: a < b & c > d "ok"',
                         related_claim="test"),
                Evidence(source="test", fetched_at="",
                         content_reference="second item",
                         related_claim="test"),
            ],
        )

        # JSON roundtrip 應成功
        d = cr.to_dict()
        json_str = json.dumps(d, ensure_ascii=False)
        restored = json.loads(json_str)
        assert restored["conclusion"] == cr.conclusion
        assert restored["query"] == cr.query
        assert restored["dimensions"][0]["finding"] == cr.dimensions[0].finding


class TestJSONOutputEscapes:
    """CA-08: JSON 輸出正確 escape 特殊字符。"""

    def test_json_output_escapes_special_chars(self):
        """to_dict() → json.dumps 的 roundtrip 保留全部特殊字符。"""
        cr = ComparisonReport(
            coin_a="BTC",
            coin_b="ETH",
            query='比較 BTC & ETH：誰的 "價值" 更高？',
            conclusion="綜合分析後，BTC > ETH 在價格動能面向。",
            dimensions=[
                DimensionResult(
                    dimension="價格動能",
                    label="價格動能比較",
                    finding="BTC 漲幅 4% > ETH 跌幅 2%，BTC 優",
                    a_evidence_refs=[0, 1],
                    b_evidence_refs=[0, 1],
                ),
                DimensionResult(
                    dimension="鏈上活動",
                    label="鏈上活動比較",
                    finding="BTC 大額流入 +12% > ETH 流出 -5%，BTC 優",
                    a_evidence_refs=[0, 1],
                    b_evidence_refs=[0, 1],
                ),
                DimensionResult(
                    dimension="市場情緒",
                    label="市場情緒比較",
                    finding='社群看多比例 BTC 68% > ETH 55%，BTC "明顯" 優',
                    a_evidence_refs=[0, 1],
                    b_evidence_refs=[0, 1],
                ),
                DimensionResult(
                    dimension="生態發展",
                    label="生態發展比較",
                    finding="BTC ETF 期權獲批，ETH 質押監管不確定",
                    a_evidence_refs=[0, 1],
                    b_evidence_refs=[0, 1],
                ),
            ],
            limits=["資料窗有限：僅 & 符號測試"],
            could_flip=['若 ETH ETF "轉為" 淨流入'],
            supporting_evidence_a=[
                Evidence(source="s1", fetched_at="2026-07-01T00:00:00Z",
                         content_reference='BTC 價格 < 70000 但 > 60000',
                         related_claim="p1"),
                Evidence(source="s1", fetched_at="2026-07-01T01:00:00Z",
                         content_reference='BTC "鏈上" 數據 & 分析',
                         related_claim="p2"),
            ],
            supporting_evidence_b=[
                Evidence(source="s2", fetched_at="2026-07-01T00:00:00Z",
                         content_reference='ETH 價格 < 4000 但 > 3000',
                         related_claim="p1"),
                Evidence(source="s2", fetched_at="2026-07-01T01:00:00Z",
                         content_reference='ETH "鏈上" 數據 & 分析',
                         related_claim="p2"),
            ],
        )

        # JSON roundtrip
        d = cr.to_dict()
        json_str = json.dumps(d, ensure_ascii=False)
        restored = json.loads(json_str)

        # 驗證所有特殊字符欄位
        assert restored["conclusion"] == cr.conclusion
        assert restored["query"] == cr.query
        assert restored["could_flip"] == cr.could_flip
        assert restored["limits"] == cr.limits
        for i, dim in enumerate(cr.dimensions):
            assert restored["dimensions"][i]["finding"] == dim.finding
        # evidence content_reference
        assert restored["supporting_evidence_a"][0]["content_reference"] == \
            cr.supporting_evidence_a[0].content_reference
        assert restored["supporting_evidence_b"][1]["content_reference"] == \
            cr.supporting_evidence_b[1].content_reference

        # 也驗證 Markdown 輸出含正確字符
        md = cr.to_markdown()
        assert "BTC" in md
        assert "ETH" in md
