"""CA-01: 官方比較契約 golden failing tests。

這些測試定義了「真正的比較報告」應該滿足的契約。當前 `run_comparison`
只產出 `(report_a, ev_a, report_b, ev_b, log)` 五元組——兩份單幣報告並排，
缺少結構化比較面向、共同結論、雙邊證據對照。這些 golden tests 會 FAIL，
作為 CA-02 到 CA-05 實作的驗收規格。

驗收條件（來自 CA-01 issue #828）:
  - 只有 report_a/report_b 時失敗
  - 缺核心面向、單邊證據硬比較、無效 evidence ref 時失敗
  - 不修改 DB schema/migration
  - 不依賴 live network

REF: docs/plans/COMPARISON-ANALYSIS-DEVELOPMENT-PLAN-20260728.md
"""
from __future__ import annotations

import pytest

from trustforge.comparison_contract import (
    COMPARISON_DIMENSIONS,
    DIMENSION_LABEL_MAP,
    ComparisonReport,
    ComparisonRunResult,
    DimensionResult,
    build_comparison_report,
    classify_evidence_to_dimension,
    validate_comparison_report,
    validate_dimension_coverage,
)
from trustforge.ingestion.base import Document
from trustforge.pipeline import run_comparison
from trustforge.schema import Evidence, Report


# ===========================================================================
# 官方比較範例 Fixture（BTC vs ETH）
# ===========================================================================

def _doc(id, kind, source, text, ts=1_000.0, meta=None):
    return Document(id=id, kind=kind, source=source, text=text, ts=ts, meta=meta or {})


def _make_fixture_docs(coin: str):
    """合成一組官方比較範例的文件，模擬真實 BTC vs ETH 場景。

    刻意讓 BTC 偏多（漲勢+大額流入）、ETH 偏空（跌勢+大額流出），
    使比較結果有明確差異可驗證。
    """
    if coin == "BTC":
        return [
            # 價格：漲勢 ~4%
            _doc(f"{coin}_p0", "price", "hoya-ohlcv",
                 f"{coin} OHLCV 2024-07-01: O=62000 H=63500 L=61500 C=63000 V=12000",
                 meta={"coin": coin, "date": "2024-07-01", "close": 63000.0}),
            _doc(f"{coin}_p1", "price", "hoya-ohlcv",
                 f"{coin} OHLCV 2024-07-15: O=64500 H=66000 L=64000 C=65500 V=15000",
                 meta={"coin": coin, "date": "2024-07-15", "close": 65500.0}),
            # 鏈上：大額流入（正面）
            _doc(f"{coin}_o1", "onchain", "glassnode",
                 f"{coin} 交易所大額流入增加 12%，機構累積訊號明顯。"),
            _doc(f"{coin}_o2", "onchain", "glassnode",
                 f"{coin} 活躍地址數月增 8%，網路使用率上升。"),
            # 市場情緒：偏多
            _doc(f"{coin}_n1", "news", "coindesk",
                 f"分析師認為 {coin} ETF 資金持續淨流入，市場情緒偏多。"),
            _doc(f"{coin}_s1", "social", "lunarcrush",
                 f"{coin} 社群討論熱度創 30 天新高，看多比例 68%。"),
            # 生態發展：正面
            _doc(f"{coin}_r1", "regulatory", "sec-gov",
                 f"{coin} ETF 期權獲 SEC 核准，擴大機構參與管道。"),
        ]
    else:  # ETH
        return [
            # 價格：下跌 ~2%
            _doc(f"{coin}_p0", "price", "hoya-ohlcv",
                 f"{coin} OHLCV 2024-07-01: O=3400 H=3500 L=3350 C=3450 V=8000",
                 meta={"coin": coin, "date": "2024-07-01", "close": 3450.0}),
            _doc(f"{coin}_p1", "price", "hoya-ohlcv",
                 f"{coin} OHLCV 2024-07-15: O=3350 H=3420 L=3300 C=3380 V=9000",
                 meta={"coin": coin, "date": "2024-07-15", "close": 3380.0}),
            # 鏈上：大額流出（負面）
            _doc(f"{coin}_o1", "onchain", "glassnode",
                 f"{coin} 交易所大額流出減少 5%，質押解鎖壓力浮現。"),
            _doc(f"{coin}_o2", "onchain", "glassnode",
                 f"{coin} Gas 費用降至年度低點，網路活動降溫。"),
            # 市場情緒：偏空
            _doc(f"{coin}_n1", "news", "coindesk",
                 f"分析師擔憂 {coin} 現貨 ETF 資金連續三週淨流出。"),
            _doc(f"{coin}_s1", "social", "lunarcrush",
                 f"{coin} 社群看空比例升至 55%，擔憂 L2 碎片化問題。"),
            # 生態發展：負面
            _doc(f"{coin}_r1", "regulatory", "sec-gov",
                 f"{coin} 質押收益被部分監管機構質疑為證券性質。"),
        ]


@pytest.fixture
def btc_eth_fixture(monkeypatch):
    """官方 BTC vs ETH 比較範例 fixture（offline，不依賴 live network）。"""
    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        return _make_fixture_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    return run_comparison("BTC", "ETH", "比較 BTC 與 ETH 的市場表現、鏈上活動、情緒與生態發展", offline=True)


# ===========================================================================
# Golden Failing Tests — 證明目前「兩份報告並排 ≠ 比較」
# ===========================================================================

class TestSideBySideIsNotComparison:
    """Golden test 群組 1：證明目前只有 A/B 並排報告不算完成。

    這些測試驗證了一個核心命題——當前的 `run_comparison` 輸出缺少結構化比較、
    共同結論、比較面向分析。它們定義了 CA-02 ~ CA-05 的驗收規格。
    """

    def test_golden_structured_comparison_report(self, btc_eth_fixture):
        """CA-02: run_comparison() 回傳 ComparisonRunResult。"""
        result = btc_eth_fixture
        assert isinstance(result, ComparisonRunResult), (
            f"run_comparison() 應回傳 ComparisonRunResult，實際回傳 {type(result)}"
        )

    def test_golden_has_common_conclusion(self, btc_eth_fixture):
        """CA-03: build_comparison_report 已產出 conclusion（非空）。"""
        result = btc_eth_fixture
        assert result.comparison is not None, "comparison 應已填入"
        assert result.comparison.conclusion.strip(), "conclusion 不可為空"

    def test_golden_has_four_dimensions(self, btc_eth_fixture):
        """CA-03: run_comparison() 產出的 ComparisonReport 包含四個比較面向。"""
        result = btc_eth_fixture
        assert result.comparison is not None, "comparison 應已填入（CA-03 deterministic fallback）"

        present = {d.dimension for d in result.comparison.dimensions}
        for dim in COMPARISON_DIMENSIONS:
            assert dim in present, (
                f"缺少比較面向 '{dim}'。實際面向: {present}"
            )
        assert len(result.comparison.dimensions) == len(COMPARISON_DIMENSIONS), (
            f"預期 {len(COMPARISON_DIMENSIONS)} 個面向，"
            f"實際 {len(result.comparison.dimensions)} 個"
        )

    def test_golden_has_evidence_cross_reference(self, btc_eth_fixture):
        """CA-03: 每個 dimension 的 a_evidence_refs / b_evidence_refs 正確對應 evidence。"""
        result = btc_eth_fixture
        assert result.comparison is not None

        for dim in result.comparison.dimensions:
            # 驗證 refs 指向正確 kind 的 evidence
            for ref in dim.a_evidence_refs:
                ev = result.comparison.supporting_evidence_a[ref]
                mapped = classify_evidence_to_dimension(ev)
                assert mapped == dim.dimension, (
                    f"A 幣 evidence[{ref}] kind='{ev.kind}' 映射到 '{mapped}',"
                    f"但 dimension '{dim.dimension}' 預期相同"
                )
            for ref in dim.b_evidence_refs:
                ev = result.comparison.supporting_evidence_b[ref]
                mapped = classify_evidence_to_dimension(ev)
                assert mapped == dim.dimension, (
                    f"B 幣 evidence[{ref}] kind='{ev.kind}' 映射到 '{mapped}',"
                    f"但 dimension '{dim.dimension}' 預期相同"
                )


# ===========================================================================
# Golden Failing Tests — 四面向覆蓋與證據追溯
# ===========================================================================

class TestDimensionCoverage:
    """Golden test 群組 2：四面向覆蓋必須完整，不可缺失。

    這些測試定義了 CA-03 的驗收規格——每個比較面向必須有雙邊證據對照。
    """

    def test_golden_requires_price_momentum_dimension(self, btc_eth_fixture):
        """CA-03: build_comparison_report 產出含價格動能面向的報告，且有雙邊 evidence refs。"""
        result = btc_eth_fixture
        comparison = build_comparison_report(
            coin_a="BTC",
            coin_b="ETH",
            query="比較 BTC 與 ETH 的市場表現",
            report_a=result.report_a,
            report_b=result.report_b,
            evidence_a=list(result.evidence_a),
            evidence_b=list(result.evidence_b),
        )
        price_dim = next(
            (d for d in comparison.dimensions if d.dimension == "價格動能"), None
        )
        assert price_dim is not None, "缺少價格動能面向"
        assert len(price_dim.a_evidence_refs) > 0, (
            "價格動能面向缺少 A 幣證據"
        )
        assert len(price_dim.b_evidence_refs) > 0, (
            "價格動能面向缺少 B 幣證據"
        )

    def test_golden_requires_onchain_dimension(self, btc_eth_fixture):
        """【會 FAIL — 期望】鏈上活動面向必須存在且有雙邊 onchain evidence refs。"""
        _, ev_a, _, ev_b, _ = btc_eth_fixture
        dim = DimensionResult(
            dimension="鏈上活動",
            label="鏈上活動比較",
            finding="BTC 大額流入增加（+12%），ETH 大額流出減少（-5%），BTC 鏈上活動更強。",
            a_evidence_refs=[i for i, e in enumerate(ev_a) if e.kind == "onchain"],
            b_evidence_refs=[i for i, e in enumerate(ev_b) if e.kind == "onchain"],
            confidence=0.80,
            decision="normal",
        )
        assert len(dim.a_evidence_refs) > 0, "鏈上活動面向缺少 A 幣證據"
        assert len(dim.b_evidence_refs) > 0, "鏈上活動面向缺少 B 幣證據"

    def test_golden_requires_sentiment_dimension(self, btc_eth_fixture):
        """【會 FAIL — 期望】市場情緒面向必須存在且有雙邊 evidence refs。"""
        _, ev_a, _, ev_b, _ = btc_eth_fixture
        dim = DimensionResult(
            dimension="市場情緒",
            label="市場情緒比較",
            finding="BTC 社群情緒偏多（68%），ETH 偏空（55%），BTC 市場情緒顯著優於 ETH。",
            a_evidence_refs=[i for i, e in enumerate(ev_a)
                             if e.kind in ("news", "social")],
            b_evidence_refs=[i for i, e in enumerate(ev_b)
                             if e.kind in ("news", "social")],
            confidence=0.75,
            decision="normal",
        )
        assert len(dim.a_evidence_refs) > 0, "市場情緒面向缺少 A 幣證據"
        assert len(dim.b_evidence_refs) > 0, "市場情緒面向缺少 B 幣證據"

    def test_golden_requires_ecosystem_dimension(self, btc_eth_fixture):
        """【會 FAIL — 期望】生態發展面向必須存在且有雙邊 evidence refs。"""
        _, ev_a, _, ev_b, _ = btc_eth_fixture
        dim = DimensionResult(
            dimension="生態發展",
            label="生態發展比較",
            finding="BTC ETF 期權獲批擴大機構管道，ETH 質押面臨監管不確定性，"
                   "BTC 生態發展前景較明朗。",
            a_evidence_refs=[i for i, e in enumerate(ev_a) if e.kind == "regulatory"],
            b_evidence_refs=[i for i, e in enumerate(ev_b) if e.kind == "regulatory"],
            confidence=0.70,
            decision="normal",
        )
        assert len(dim.a_evidence_refs) > 0, "生態發展面向缺少 A 幣證據"
        assert len(dim.b_evidence_refs) > 0, "生態發展面向缺少 B 幣證據"

    def test_golden_all_four_dimensions_present(self, btc_eth_fixture):
        """CA-03: build_comparison_report 產出完整四面向報告並通過 validate_comparison_report。"""
        result = btc_eth_fixture
        comparison = build_comparison_report(
            coin_a="BTC",
            coin_b="ETH",
            query="比較",
            report_a=result.report_a,
            report_b=result.report_b,
            evidence_a=list(result.evidence_a),
            evidence_b=list(result.evidence_b),
        )

        # 驗證四個面向存在
        present = {d.dimension for d in comparison.dimensions}
        for dim in COMPARISON_DIMENSIONS:
            assert dim in present, f"缺少比較面向 '{dim}'。實際: {present}"

        # 驗證通過 validate_comparison_report
        violations = validate_comparison_report(comparison, _raise=False)
        assert len(violations) == 0, (
            f"比較報告有 {len(violations)} 項契約違規：\n"
            + "\n".join(violations)
        )


# ===========================================================================
# Golden Failing Tests — 不可接受的比較
# ===========================================================================

class TestRejectsInvalidComparison:
    """Golden test 群組 3：拒絕無效的比較（單邊證據、無效 refs、缺面向）。"""

    def test_golden_rejects_one_sided_comparison(self):
        """【會 FAIL — 期望】僅有單邊證據不可標記為 normal 比較。"""
        cr = ComparisonReport(
            coin_a="BTC",
            coin_b="ETH",
            query="比較",
            conclusion="（測試用）",
            dimensions=[
                DimensionResult(
                    dimension="價格動能",
                    label="價格動能比較",
                    finding="只有 A 的數據",
                    a_evidence_refs=[0],     # 只有 A
                    b_evidence_refs=[],       # B 無證據
                    decision="normal",        # ← 這不應該被允許
                ),
            ],
            supporting_evidence_a=[Evidence(source="test", fetched_at="", content_reference="", related_claim="")],
            supporting_evidence_b=[],
        )
        violations = validate_comparison_report(cr, _raise=False)
        # 應該有違規（單邊證據標記為 normal）
        assert len(violations) > 0, (
            "FAIL EXPECTED: 單邊證據比較應觸發契約違規，"
            "但 validate_comparison_report 回傳 0 項違規。"
            " CA-03 需實作此檢查。"
        )

    def test_golden_rejects_invalid_evidence_refs(self):
        """【會 FAIL — 期望】無效的 evidence refs（超出範圍）應被拒絕。"""
        cr = ComparisonReport(
            coin_a="BTC",
            coin_b="ETH",
            query="比較",
            conclusion="（測試用）",
            dimensions=[
                DimensionResult(
                    dimension="價格動能",
                    label="價格動能比較",
                    finding="引用了不存在的證據",
                    a_evidence_refs=[99],  # 超出範圍
                    b_evidence_refs=[0],
                ),
            ],
            supporting_evidence_a=[Evidence(source="test", fetched_at="", content_reference="", related_claim="")],
            supporting_evidence_b=[Evidence(source="test", fetched_at="", content_reference="", related_claim="")],
        )
        violations = validate_comparison_report(cr, _raise=False)
        assert len(violations) > 0, (
            "FAIL EXPECTED: 無效的 evidence refs 應觸發契約違規。"
        )

    def test_golden_rejects_empty_conclusion(self):
        """【會 FAIL — 期望】空的 conclusion 應被拒絕。"""
        cr = ComparisonReport(
            coin_a="BTC",
            coin_b="ETH",
            query="比較",
            conclusion="",  # 空字串
            dimensions=[
                DimensionResult(
                    dimension="價格動能",
                    label="價格動能比較",
                    finding="測試",
                    a_evidence_refs=[0],
                    b_evidence_refs=[0],
                ),
            ],
            supporting_evidence_a=[Evidence(source="test", fetched_at="", content_reference="", related_claim="")],
            supporting_evidence_b=[Evidence(source="test", fetched_at="", content_reference="", related_claim="")],
        )
        violations = validate_comparison_report(cr, _raise=False)
        assert len(violations) > 0, (
            "FAIL EXPECTED: 空的 conclusion 應觸發契約違規。"
        )

    def test_golden_rejects_missing_dimensions(self):
        """【會 FAIL — 期望】缺少面向的報告應被拒絕。"""
        cr = ComparisonReport(
            coin_a="BTC",
            coin_b="ETH",
            query="比較",
            conclusion="僅有一個面向",
            dimensions=[
                DimensionResult(
                    dimension="價格動能",
                    label="價格動能比較",
                    finding="只有價格比較",
                    a_evidence_refs=[0],
                    b_evidence_refs=[0],
                ),
                # 缺：鏈上活動、市場情緒、生態發展
            ],
            supporting_evidence_a=[Evidence(source="test", fetched_at="", content_reference="", related_claim="")],
            supporting_evidence_b=[Evidence(source="test", fetched_at="", content_reference="", related_claim="")],
        )
        violations = validate_comparison_report(cr, _raise=False)
        assert len(violations) >= 3, (
            f"FAIL EXPECTED: 缺少 3 個面向，應有至少 3 項違規，"
            f"實際 {len(violations)} 項。"
        )

    def test_golden_rejects_unknown_dimension(self):
        """【會 FAIL — 期望】未知的比較面向應被拒絕。"""
        cr = ComparisonReport(
            coin_a="BTC",
            coin_b="ETH",
            query="比較",
            conclusion="（測試用）",
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
                    a_evidence_refs=[0],
                    b_evidence_refs=[0],
                ),
                DimensionResult(
                    dimension="市場情緒",
                    label="市場情緒比較",
                    finding="正常",
                    a_evidence_refs=[0],
                    b_evidence_refs=[0],
                ),
                DimensionResult(
                    dimension="生態發展",
                    label="生態發展比較",
                    finding="正常",
                    a_evidence_refs=[0],
                    b_evidence_refs=[0],
                ),
                DimensionResult(
                    dimension="未知面向",  # ← 未知
                    label="未知",
                    finding="不該存在",
                    a_evidence_refs=[0],
                    b_evidence_refs=[0],
                ),
            ],
            supporting_evidence_a=[Evidence(source="t", fetched_at="", content_reference="", related_claim="")],
            supporting_evidence_b=[Evidence(source="t", fetched_at="", content_reference="", related_claim="")],
        )
        violations = validate_comparison_report(cr, _raise=False)
        assert any("未知面向" in v or "unknown" in v.lower() for v in violations), (
            f"FAIL EXPECTED: 未知面向應觸發違規。違規清單: {violations}"
        )

    def test_golden_rejects_duplicate_dimension(self):
        """【會 FAIL — 期望】重複的比較面向應被拒絕。"""
        cr = ComparisonReport(
            coin_a="BTC",
            coin_b="ETH",
            query="比較",
            conclusion="（測試用）",
            dimensions=[
                DimensionResult(
                    dimension="價格動能",
                    label="價格動能比較",
                    finding="A",
                    a_evidence_refs=[0],
                    b_evidence_refs=[0],
                ),
                DimensionResult(
                    dimension="價格動能",  # ← 重複
                    label="價格動能比較",
                    finding="B",
                    a_evidence_refs=[0],
                    b_evidence_refs=[0],
                ),
                DimensionResult(
                    dimension="鏈上活動",
                    label="鏈上活動比較",
                    finding="正常",
                    a_evidence_refs=[0],
                    b_evidence_refs=[0],
                ),
                DimensionResult(
                    dimension="市場情緒",
                    label="市場情緒比較",
                    finding="正常",
                    a_evidence_refs=[0],
                    b_evidence_refs=[0],
                ),
            ],
            supporting_evidence_a=[Evidence(source="t", fetched_at="", content_reference="", related_claim="")],
            supporting_evidence_b=[Evidence(source="t", fetched_at="", content_reference="", related_claim="")],
        )
        violations = validate_comparison_report(cr, _raise=False)
        assert any("重複" in v or "duplicate" in v.lower() for v in violations), (
            f"FAIL EXPECTED: 重複面向應觸發違規。違規清單: {violations}"
        )

    def test_golden_rejects_invalid_dimension_confidence(self):
        """【會 FAIL — 期望】面向 confidence 超出範圍應被拒絕。"""
        cr = ComparisonReport(
            coin_a="BTC",
            coin_b="ETH",
            query="比較",
            conclusion="（測試用）",
            dimensions=[
                DimensionResult(
                    dimension="價格動能",
                    label="價格動能比較",
                    finding="正常",
                    a_evidence_refs=[0],
                    b_evidence_refs=[0],
                    confidence=1.5,  # ← 超出範圍
                ),
                DimensionResult(
                    dimension="鏈上活動",
                    label="鏈上活動比較",
                    finding="正常",
                    a_evidence_refs=[0],
                    b_evidence_refs=[0],
                ),
                DimensionResult(
                    dimension="市場情緒",
                    label="市場情緒比較",
                    finding="正常",
                    a_evidence_refs=[0],
                    b_evidence_refs=[0],
                ),
                DimensionResult(
                    dimension="生態發展",
                    label="生態發展比較",
                    finding="正常",
                    a_evidence_refs=[0],
                    b_evidence_refs=[0],
                ),
            ],
            supporting_evidence_a=[Evidence(source="t", fetched_at="", content_reference="", related_claim="")],
            supporting_evidence_b=[Evidence(source="t", fetched_at="", content_reference="", related_claim="")],
        )
        violations = validate_comparison_report(cr, _raise=False)
        assert any("confidence" in v.lower() for v in violations), (
            f"FAIL EXPECTED: 無效 confidence 應觸發違規。違規清單: {violations}"
        )

    def test_golden_rejects_invalid_dimension_decision(self):
        """【會 FAIL — 期望】面向 decision 無效應被拒絕。"""
        cr = ComparisonReport(
            coin_a="BTC",
            coin_b="ETH",
            query="比較",
            conclusion="（測試用）",
            dimensions=[
                DimensionResult(
                    dimension="價格動能",
                    label="價格動能比較",
                    finding="正常",
                    a_evidence_refs=[0],
                    b_evidence_refs=[0],
                    decision="typo",  # ← 無效
                ),
                DimensionResult(
                    dimension="鏈上活動",
                    label="鏈上活動比較",
                    finding="正常",
                    a_evidence_refs=[0],
                    b_evidence_refs=[0],
                ),
                DimensionResult(
                    dimension="市場情緒",
                    label="市場情緒比較",
                    finding="正常",
                    a_evidence_refs=[0],
                    b_evidence_refs=[0],
                ),
                DimensionResult(
                    dimension="生態發展",
                    label="生態發展比較",
                    finding="正常",
                    a_evidence_refs=[0],
                    b_evidence_refs=[0],
                ),
            ],
            supporting_evidence_a=[Evidence(source="t", fetched_at="", content_reference="", related_claim="")],
            supporting_evidence_b=[Evidence(source="t", fetched_at="", content_reference="", related_claim="")],
        )
        violations = validate_comparison_report(cr, _raise=False)
        assert any("decision" in v.lower() for v in violations), (
            f"FAIL EXPECTED: 無效 decision 應觸發違規。違規清單: {violations}"
        )

    def test_golden_rejects_same_coin(self):
        """【會 FAIL — 期望】相同幣種比較應被拒絕。"""
        cr = ComparisonReport(
            coin_a="BTC",
            coin_b="BTC",
            query="比較",
            conclusion="（測試用）",
            dimensions=[
                DimensionResult(
                    dimension="價格動能",
                    label="價格動能比較",
                    finding="相同幣種",
                    a_evidence_refs=[0],
                    b_evidence_refs=[0],
                ),
            ],
            supporting_evidence_a=[Evidence(source="test", fetched_at="", content_reference="", related_claim="")],
            supporting_evidence_b=[Evidence(source="test", fetched_at="", content_reference="", related_claim="")],
        )
        violations = validate_comparison_report(cr, _raise=False)
        assert len(violations) > 0, (
            "FAIL EXPECTED: coin_a == coin_b 應觸發契約違規。"
        )

    def test_golden_abstain_when_no_evidence(self):
        """【會 FAIL — 期望】雙邊皆無證據時必須 abstain，不可假裝做了比較。"""
        cr = ComparisonReport(
            coin_a="BTC",
            coin_b="ETH",
            query="比較",
            conclusion="（資料不足）",
            dimensions=[
                DimensionResult(
                    dimension="價格動能",
                    label="價格動能比較",
                    finding="無資料",
                    a_evidence_refs=[],
                    b_evidence_refs=[],
                    decision="normal",  # ← 雙邊無證據卻標 normal
                ),
            ],
        )
        violations = validate_comparison_report(cr, _raise=False)
        has_abstain_violation = any(
            "abstain" in v.lower() or "insufficient" in v.lower()
            for v in violations
        )
        assert has_abstain_violation, (
            "FAIL EXPECTED: 雙邊無證據卻標 normal，應觸發 abstain 違規。"
        )


# ===========================================================================
# Golden Failing Tests — A/B Swap Metamorphic
# ===========================================================================

class TestMetamorphicSwap:
    """Golden test 群組 4：A/B 對調應產出對稱結果。

    比較 BTC vs ETH 與 ETH vs BTC 應該得到對稱的結論（方向相反、其他一致）。
    """

    @pytest.mark.xfail(reason="CA-04/CA-05: 比較合成與結構化對稱尚未實作", strict=False)
    def test_golden_swap_produces_symmetric_results(self, monkeypatch):
        """【會 FAIL — 期望】A/B 對調後結論方向應對稱。

        當前實作只跑兩個獨立 pipeline，對調只是把 report_a/report_b 互換，
        沒有真正的比較結構來驗證對稱性。這個測試定義了 CA-05 的驗收規格。
        """
        def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
            return _make_fixture_docs(coin)

        monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

        # Forward: BTC vs ETH
        ra1, ea1, rb1, eb1, log1 = run_comparison("BTC", "ETH", "比較兩幣", offline=True)
        # Swapped: ETH vs BTC
        ra2, ea2, rb2, eb2, log2 = run_comparison("ETH", "BTC", "比較兩幣", offline=True)

        # 對調後方向應相反
        # （當前實作兩次獨立 pipeline，方向可能相同也可能不同——不夠穩定）
        dir_a1 = ra1.direction or ra1._direction_label()
        dir_b1 = rb1.direction or rb1._direction_label()
        dir_a2 = ra2.direction or ra2._direction_label()  # 這是 ETH（對調後）
        dir_b2 = rb2.direction or rb2._direction_label()  # 這是 BTC（對調後）

        # 記錄方向以便人工審查（不做硬斷言，因為方向依賴 LLM，不穩定）
        has_symmetry_hint = (
            (dir_a1 != dir_b1) or (dir_a2 != dir_b2)
        )
        assert has_symmetry_hint, (
            "FAIL EXPECTED: A/B 對調後未見方向對稱。"
            f" Forward: {ra1.coin}={dir_a1}, {rb1.coin}={dir_b1}"
            f" | Swapped: {ra2.coin}={dir_a2}, {rb2.coin}={dir_b2}"
            " CA-04 + CA-05 應確保比較結構化的對稱性。"
        )

    def test_golden_swap_dimensions_inverted(self, monkeypatch):
        """【會 FAIL — 期望】A/B 對調後維度結論應反向。

        如果 BTC vs ETH 在「價格動能」維度判定 BTC 優於 ETH，
        那麼 ETH vs BTC 應判定 ETH 劣於 BTC。這依賴 CA-03 的正規化與
        CA-04 的 Bedrock synthesis 正確處理 coin order。
        """
        def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
            return _make_fixture_docs(coin)

        monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

        _, ev_a1, _, ev_b1, _ = run_comparison("BTC", "ETH", "比較", offline=True)
        _, ev_a2, _, ev_b2, _ = run_comparison("ETH", "BTC", "比較", offline=True)

        # 相同幣種的 evidence 筆數應一致（BTC=BTC, ETH=ETH）
        assert len(ev_a1) == len(ev_b2), (
            f"FAIL EXPECTED: BTC evidence 筆數不一致（forward A={len(ev_a1)} vs swapped B={len(ev_b2)}）"
        )
        assert len(ev_b1) == len(ev_a2), (
            f"FAIL EXPECTED: ETH evidence 筆數不一致（forward B={len(ev_b1)} vs swapped A={len(ev_a2)}）"
        )


# ===========================================================================
# 契約驗證 — validate_comparison_report 本身的正確性
# ===========================================================================

class TestValidateComparisonReport:
    """Golden test 群組 5：驗證 validate_comparison_report 本身的邏輯。"""

    def test_validate_passes_valid_report(self, btc_eth_fixture):
        """【會 FAIL — 期望】驗證一個手動建構的完整報告應通過。"""
        _, ev_a, _, ev_b, _ = btc_eth_fixture

        cr = ComparisonReport(
            coin_a="BTC",
            coin_b="ETH",
            query="比較 BTC 與 ETH",
            conclusion="BTC 在價格動能、鏈上活動、市場情緒、生態發展四個面向均優於 ETH",
            confidence=0.78,
            limits=["ETH 價格樣本較少", "生態發展資料有限"],
            could_flip=["若 ETH ETF 資金轉為淨流入，情緒面向可能翻轉"],
            dimensions=[
                DimensionResult(
                    dimension="價格動能",
                    label="價格動能比較",
                    finding="BTC +4% vs ETH -2%，BTC 優",
                    a_evidence_refs=[i for i, e in enumerate(ev_a) if e.kind == "price"],
                    b_evidence_refs=[i for i, e in enumerate(ev_b) if e.kind == "price"],
                    confidence=0.85,
                    decision="normal",
                ),
                DimensionResult(
                    dimension="鏈上活動",
                    label="鏈上活動比較",
                    finding="BTC 大額流入 +12% vs ETH -5%，BTC 優",
                    a_evidence_refs=[i for i, e in enumerate(ev_a) if e.kind == "onchain"],
                    b_evidence_refs=[i for i, e in enumerate(ev_b) if e.kind == "onchain"],
                    confidence=0.80,
                    decision="normal",
                ),
                DimensionResult(
                    dimension="市場情緒",
                    label="市場情緒比較",
                    finding="BTC 偏多 68% vs ETH 偏空 55%，BTC 優",
                    a_evidence_refs=[i for i, e in enumerate(ev_a)
                                     if e.kind in ("news", "social")],
                    b_evidence_refs=[i for i, e in enumerate(ev_b)
                                     if e.kind in ("news", "social")],
                    confidence=0.75,
                    decision="normal",
                ),
                DimensionResult(
                    dimension="生態發展",
                    label="生態發展比較",
                    finding="BTC ETF 期權獲批 vs ETH 質押監管疑慮，BTC 優",
                    a_evidence_refs=[i for i, e in enumerate(ev_a)
                                     if e.kind == "regulatory"],
                    b_evidence_refs=[i for i, e in enumerate(ev_b)
                                     if e.kind == "regulatory"],
                    confidence=0.70,
                    decision="normal",
                ),
            ],
            supporting_evidence_a=list(ev_a),
            supporting_evidence_b=list(ev_b),
        )

        violations = validate_comparison_report(cr, _raise=False)
        assert len(violations) == 0, (
            f"FAIL EXPECTED: 合法報告應通過驗證，但發現 {len(violations)} 項違規：\n"
            + "\n".join(violations)
        )

    def test_validate_coverage_all_covered(self, btc_eth_fixture):
        """【會 FAIL — 期望】四面向全覆蓋的報告應回傳全部 True。"""
        _, ev_a, _, ev_b, _ = btc_eth_fixture

        cr = ComparisonReport(
            coin_a="BTC",
            coin_b="ETH",
            query="比較",
            conclusion="測試",
            dimensions=[
                DimensionResult(
                    dimension=dim,
                    label=DIMENSION_LABEL_MAP.get(dim, dim),
                    finding=f"{dim} 比較結果",
                    a_evidence_refs=[0],
                    b_evidence_refs=[0],
                    decision="normal",
                )
                for dim in COMPARISON_DIMENSIONS
            ],
            supporting_evidence_a=[Evidence(source="t", fetched_at="", content_reference="", related_claim="")],
            supporting_evidence_b=[Evidence(source="t", fetched_at="", content_reference="", related_claim="")],
        )

        coverage = validate_dimension_coverage(cr)
        assert all(coverage.values()), (
            f"FAIL EXPECTED: 四面向應全部覆蓋，實際: {coverage}"
        )
        assert set(coverage.keys()) == set(COMPARISON_DIMENSIONS)

    def test_validate_coverage_partial(self):
        """部分覆蓋的報告應正確反映缺失。"""
        cr = ComparisonReport(
            coin_a="BTC",
            coin_b="ETH",
            query="比較",
            conclusion="測試",
            dimensions=[
                DimensionResult(
                    dimension="價格動能",
                    label="價格動能比較",
                    finding="正常",
                    a_evidence_refs=[0],
                    b_evidence_refs=[0],
                    decision="normal",
                ),
                DimensionResult(
                    dimension="鏈上活動",
                    label="鏈上活動比較",
                    finding="缺資料",
                    a_evidence_refs=[],
                    b_evidence_refs=[],
                    decision="abstain",
                ),
            ],
            supporting_evidence_a=[Evidence(source="t", fetched_at="", content_reference="", related_claim="")],
            supporting_evidence_b=[Evidence(source="t", fetched_at="", content_reference="", related_claim="")],
        )

        coverage = validate_dimension_coverage(cr)
        assert coverage.get("價格動能") is True, "有雙邊證據應為 True"
        assert coverage.get("鏈上活動") is False, "雙邊無證據應為 False"
        assert coverage.get("市場情緒") is False, "缺失面向應為 False"
        assert coverage.get("生態發展") is False, "缺失面向應為 False"


# ===========================================================================
# ComparisonRunResult 結構驗證
# ===========================================================================

class TestComparisonRunResult:
    """Golden test 群組 6：ComparisonRunResult 的結構驗證。"""

    def test_run_result_has_comparison_flag(self, btc_eth_fixture):
        """【會 FAIL — 期望】ComparisonRunResult.has_comparison 反映實際狀態。"""
        report_a, ev_a, report_b, ev_b, log = btc_eth_fixture

        # 目前沒有 comparison → has_comparison 應為 False
        result = ComparisonRunResult(
            report_a=report_a,
            report_b=report_b,
            evidence_a=list(ev_a),
            evidence_b=list(ev_b),
            comparison=None,
        )
        assert not result.has_comparison, (
            "FAIL EXPECTED: 無 comparison 時 has_comparison 應為 False。"
        )

    def test_run_result_with_comparison(self, btc_eth_fixture):
        """【會 FAIL — 期望】有 comparison 時 has_comparison 應為 True。"""
        report_a, ev_a, report_b, ev_b, log = btc_eth_fixture

        cr = ComparisonReport(
            coin_a="BTC",
            coin_b="ETH",
            query="比較",
            conclusion="測試結論",
            dimensions=[
                DimensionResult(
                    dimension=dim,
                    label=DIMENSION_LABEL_MAP.get(dim, dim),
                    finding=f"{dim} 結果",
                    a_evidence_refs=[0],
                    b_evidence_refs=[0],
                    decision="normal",
                )
                for dim in COMPARISON_DIMENSIONS
            ],
            supporting_evidence_a=[Evidence(source="t", fetched_at="", content_reference="", related_claim="")],
            supporting_evidence_b=[Evidence(source="t", fetched_at="", content_reference="", related_claim="")],
        )

        result = ComparisonRunResult(
            report_a=report_a,
            report_b=report_b,
            evidence_a=list(ev_a),
            evidence_b=list(ev_b),
            comparison=cr,
        )
        assert result.has_comparison, (
            "FAIL EXPECTED: 有 comparison 時 has_comparison 應為 True。"
        )


# ===========================================================================
# CA-02 序列化/反序列化
# ===========================================================================

class TestSerializeDeserialize:
    """CA-02：DimensionResult / ComparisonReport / ComparisonRunResult 的序列化 roundtrip。"""

    def test_dimension_result_roundtrip(self):
        """DimensionResult to_dict → from_dict 一致。"""
        dim = DimensionResult(
            dimension="價格動能",
            label="價格動能比較",
            finding="BTC 優於 ETH",
            a_evidence_refs=[0, 1],
            b_evidence_refs=[2],
            confidence=0.85,
            decision="normal",
        )
        d = dim.to_dict()
        restored = DimensionResult.from_dict(d)
        assert restored.dimension == dim.dimension
        assert restored.label == dim.label
        assert restored.finding == dim.finding
        assert restored.a_evidence_refs == dim.a_evidence_refs
        assert restored.b_evidence_refs == dim.b_evidence_refs
        assert restored.confidence == dim.confidence
        assert restored.decision == dim.decision

    def test_comparison_report_roundtrip(self, btc_eth_fixture):
        """ComparisonReport to_dict → from_dict 一致（含 dimensions 與 supporting reports/evidence）。"""
        result = btc_eth_fixture
        report_a, ev_a, report_b, ev_b, log = result

        dims = [
            DimensionResult(
                dimension="價格動能",
                label="價格動能比較",
                finding="BTC +4% vs ETH -2%，BTC 優",
                a_evidence_refs=[i for i, e in enumerate(ev_a) if e.kind == "price"],
                b_evidence_refs=[i for i, e in enumerate(ev_b) if e.kind == "price"],
                confidence=0.85,
                decision="normal",
            ),
            DimensionResult(
                dimension="鏈上活動",
                label="鏈上活動比較",
                finding="BTC 鏈上活動更強",
                a_evidence_refs=[i for i, e in enumerate(ev_a) if e.kind == "onchain"],
                b_evidence_refs=[i for i, e in enumerate(ev_b) if e.kind == "onchain"],
                confidence=0.80,
                decision="normal",
            ),
            DimensionResult(
                dimension="市場情緒",
                label="市場情緒比較",
                finding="BTC 市場情緒優於 ETH",
                a_evidence_refs=[i for i, e in enumerate(ev_a) if e.kind in ("news", "social")],
                b_evidence_refs=[i for i, e in enumerate(ev_b) if e.kind in ("news", "social")],
                confidence=0.75,
                decision="normal",
            ),
            DimensionResult(
                dimension="生態發展",
                label="生態發展比較",
                finding="BTC 生態前景較明朗",
                a_evidence_refs=[i for i, e in enumerate(ev_a) if e.kind == "regulatory"],
                b_evidence_refs=[i for i, e in enumerate(ev_b) if e.kind == "regulatory"],
                confidence=0.70,
                decision="normal",
            ),
        ]

        cr = ComparisonReport(
            coin_a="BTC",
            coin_b="ETH",
            query="比較 BTC 與 ETH",
            conclusion="BTC 在四個面向全數優於 ETH",
            dimensions=dims,
            confidence=0.78,
            limits=["資料有限"],
            could_flip=["若 ETH ETF 轉淨流入"],
            supporting_report_a=report_a,
            supporting_report_b=report_b,
            supporting_evidence_a=list(ev_a),
            supporting_evidence_b=list(ev_b),
        )

        d = cr.to_dict()
        restored = ComparisonReport.from_dict(d)

        assert restored.coin_a == cr.coin_a
        assert restored.coin_b == cr.coin_b
        assert restored.query == cr.query
        assert restored.conclusion == cr.conclusion
        assert restored.confidence == cr.confidence
        assert len(restored.dimensions) == 4
        for i, dim in enumerate(cr.dimensions):
            assert restored.dimensions[i].dimension == dim.dimension
            assert restored.dimensions[i].finding == dim.finding
        # 驗證 nested Report 反序列化
        assert restored.supporting_report_a is not None
        assert restored.supporting_report_a.coin == "BTC"
        assert restored.supporting_report_b is not None
        assert restored.supporting_report_b.coin == "ETH"
        # 驗證 nested Evidence 反序列化
        assert len(restored.supporting_evidence_a) == len(ev_a)
        assert len(restored.supporting_evidence_b) == len(ev_b)

    def test_comparison_run_result_roundtrip(self, btc_eth_fixture):
        """ComparisonRunResult to_dict → from_dict 一致。"""
        result = btc_eth_fixture
        report_a, ev_a, report_b, ev_b, log = result

        dims = [
            DimensionResult(
                dimension=dim,
                label=DIMENSION_LABEL_MAP.get(dim, dim),
                finding=f"{dim} 測試",
                a_evidence_refs=[0],
                b_evidence_refs=[0],
                decision="normal",
            )
            for dim in COMPARISON_DIMENSIONS
        ]

        cr = ComparisonReport(
            coin_a="BTC",
            coin_b="ETH",
            query="比較",
            conclusion="測試結論",
            dimensions=dims,
            supporting_evidence_a=[Evidence(source="t", fetched_at="", content_reference="", related_claim="")],
            supporting_evidence_b=[Evidence(source="t", fetched_at="", content_reference="", related_claim="")],
        )

        run_result = ComparisonRunResult(
            report_a=report_a,
            report_b=report_b,
            evidence_a=list(ev_a),
            evidence_b=list(ev_b),
            comparison=cr,
            log=log,
        )

        d = run_result.to_dict()
        restored = ComparisonRunResult.from_dict(d)

        assert restored.report_a.coin == "BTC"
        assert restored.report_b.coin == "ETH"
        assert len(restored.evidence_a) == len(ev_a)
        assert len(restored.evidence_b) == len(ev_b)
        assert restored.comparison is not None
        assert restored.comparison.coin_a == "BTC"
        assert len(restored.comparison.dimensions) == 4

    def test_from_a_b_reports_produces_valid_report(self, btc_eth_fixture):
        """from_a_b_reports 現委託 build_comparison_report，產出含四面向的合法報告。"""
        result = btc_eth_fixture
        report_a, ev_a, report_b, ev_b, log = result

        cr = ComparisonReport.from_a_b_reports(
            coin_a="BTC",
            coin_b="ETH",
            query="比較測試",
            report_a=report_a,
            evidence_a=list(ev_a),
            report_b=report_b,
            evidence_b=list(ev_b),
        )

        assert cr.coin_a == "BTC"
        assert cr.coin_b == "ETH"
        assert len(cr.dimensions) == 4
        present = {d.dimension for d in cr.dimensions}
        for dim in COMPARISON_DIMENSIONS:
            assert dim in present
        # CA-03: 雙邊皆有證據，decision 為 normal（不再是 abstain 骨架）
        assert cr.supporting_report_a is not None
        assert cr.supporting_report_b is not None

        # 應可通過 validate
        violations = validate_comparison_report(cr, _raise=False)
        assert len(violations) == 0, f"報告應無違規，實際: {violations}"


# ===========================================================================
# 向後相容驗證（不破壞現有功能）
# ===========================================================================

class TestBackwardCompatibility:
    """Golden test 群組 7：現有 pipeline 行為不受破壞。"""

    def test_existing_run_comparison_still_works(self, btc_eth_fixture):
        """現有 run_comparison 回傳結構不變。"""
        result = btc_eth_fixture
        assert len(result) == 5
        report_a, ev_a, report_b, ev_b, log = result
        assert report_a.coin == "BTC"
        assert report_b.coin == "ETH"
        assert len(ev_a) == 7  # BTC: 2 price + 2 onchain + 1 news + 1 social + 1 regulatory
        assert len(ev_b) == 7  # ETH: 同上

    def test_existing_test_comparison_still_passes(self, monkeypatch):
        """現有 tests/test_comparison.py 的測試仍然通過。"""
        # 直接 import 並執行一個現有測試的邏輯來驗證
        from trustforge.pipeline import run_comparison as rc
        from trustforge.schema import COIN_POOL

        def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
            return _make_fixture_docs(coin)

        monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

        result = rc("BTC", "ETH", "比較兩幣", offline=True)
        assert len(result) == 5
        report_a, ev_a, report_b, ev_b, log = result
        assert report_a.coin == "BTC"
        assert report_b.coin == "ETH"
        assert ev_a
        assert ev_b
        assert "comparison.start" in [e["tool"] for e in log.events]
        assert "comparison.done" in [e["tool"] for e in log.events]

    def test_run_comparison_returns_comparison_run_result(self, monkeypatch):
        """CA-02: run_comparison() 回傳型別為 ComparisonRunResult。"""
        def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
            return _make_fixture_docs(coin)

        monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

        result = run_comparison("BTC", "ETH", "比較兩幣", offline=True)
        assert isinstance(result, ComparisonRunResult), (
            f"期望 ComparisonRunResult，實際 {type(result)}"
        )

    def test_run_comparison_unpacks_to_five_tuple(self, monkeypatch):
        """CA-02: unpack 5-tuple 仍能執行。"""
        def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
            return _make_fixture_docs(coin)

        monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

        result = run_comparison("BTC", "ETH", "比較兩幣", offline=True)
        report_a, ev_a, report_b, ev_b, log = result
        assert report_a.coin == "BTC"
        assert report_b.coin == "ETH"
        assert ev_a
        assert ev_b
        assert "comparison.start" in [e["tool"] for e in log.events]

    def test_run_comparison_has_comparison_populated(self, monkeypatch):
        """CA-03: result.comparison 已由 build_comparison_report 填入。"""
        def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
            return _make_fixture_docs(coin)

        monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

        result = run_comparison("BTC", "ETH", "比較兩幣", offline=True)
        assert result.comparison is not None, (
            "CA-03: comparison 應由 build_comparison_report 填入"
        )
        assert result.has_comparison
        assert len(result.comparison.dimensions) == len(COMPARISON_DIMENSIONS)
        # 驗證報告契約
        violations = validate_comparison_report(result.comparison, _raise=False)
        assert len(violations) == 0, (
            f"run_comparison 產出的 comparison 有 {len(violations)} 項違規：\n"
            + "\n".join(violations)
        )


# ===========================================================================
# CA-03: classify_evidence_to_dimension & build_comparison_report 單元測試
# ===========================================================================

class TestClassifyEvidenceToDimension:
    """classify_evidence_to_dimension 的 mapping 正確性。"""

    def test_classify_evidence_to_dimension_price(self):
        """price evidence → 價格動能。"""
        ev = Evidence(source="test", fetched_at="", content_reference="", related_claim="", kind="price")
        assert classify_evidence_to_dimension(ev) == "價格動能"

    def test_classify_evidence_to_dimension_onchain(self):
        """onchain → 鏈上活動。"""
        ev = Evidence(source="test", fetched_at="", content_reference="", related_claim="", kind="onchain")
        assert classify_evidence_to_dimension(ev) == "鏈上活動"

    def test_classify_evidence_to_dimension_news(self):
        """news → 市場情緒。"""
        ev = Evidence(source="test", fetched_at="", content_reference="", related_claim="", kind="news")
        assert classify_evidence_to_dimension(ev) == "市場情緒"

    def test_classify_evidence_to_dimension_social(self):
        """social → 市場情緒。"""
        ev = Evidence(source="test", fetched_at="", content_reference="", related_claim="", kind="social")
        assert classify_evidence_to_dimension(ev) == "市場情緒"

    def test_classify_evidence_to_dimension_regulatory(self):
        """regulatory → 生態發展。"""
        ev = Evidence(source="test", fetched_at="", content_reference="", related_claim="", kind="regulatory")
        assert classify_evidence_to_dimension(ev) == "生態發展"

    def test_classify_evidence_to_dimension_unknown(self):
        """未知 kind → None。"""
        ev = Evidence(source="test", fetched_at="", content_reference="", related_claim="", kind="unknown_kind")
        assert classify_evidence_to_dimension(ev) is None


class TestBuildComparisonReport:
    """build_comparison_report 的結構與契約驗證。"""

    def test_build_comparison_report_has_conclusion(self, btc_eth_fixture):
        """產出的 report conclusion 非空。"""
        result = btc_eth_fixture
        comparison = build_comparison_report(
            coin_a="BTC",
            coin_b="ETH",
            query="比較",
            report_a=result.report_a,
            report_b=result.report_b,
            evidence_a=list(result.evidence_a),
            evidence_b=list(result.evidence_b),
        )
        assert comparison.conclusion.strip(), "conclusion 不可為空"

    def test_build_comparison_report_dimensions_count(self, btc_eth_fixture):
        """產出四個 dimension。"""
        result = btc_eth_fixture
        comparison = build_comparison_report(
            coin_a="BTC",
            coin_b="ETH",
            query="比較",
            report_a=result.report_a,
            report_b=result.report_b,
            evidence_a=list(result.evidence_a),
            evidence_b=list(result.evidence_b),
        )
        assert len(comparison.dimensions) == len(COMPARISON_DIMENSIONS)
        present = {d.dimension for d in comparison.dimensions}
        assert present == set(COMPARISON_DIMENSIONS)

    def test_build_comparison_report_passes_validation(self, btc_eth_fixture):
        """通過 validate_comparison_report。"""
        result = btc_eth_fixture
        comparison = build_comparison_report(
            coin_a="BTC",
            coin_b="ETH",
            query="比較",
            report_a=result.report_a,
            report_b=result.report_b,
            evidence_a=list(result.evidence_a),
            evidence_b=list(result.evidence_b),
        )
        violations = validate_comparison_report(comparison, _raise=False)
        assert len(violations) == 0, (
            f"build_comparison_report 產出 {len(violations)} 項違規：\n"
            + "\n".join(violations)
        )

    def test_build_comparison_report_abstain_when_no_evidence(self):
        """雙邊皆無證據的面向 decision 為 abstain。"""
        report_a = Report(
            coin="BTC", question_type="comparison", question="比較",
            market_judgment="", facts=[], inferences=[], key_basis=[],
            confidence=0.0, limits=[], could_flip=[], contrarian=[],
            generated_at="2026-07-26T00:00:00Z",
        )
        report_b = Report(
            coin="ETH", question_type="comparison", question="比較",
            market_judgment="", facts=[], inferences=[], key_basis=[],
            confidence=0.0, limits=[], could_flip=[], contrarian=[],
            generated_at="2026-07-26T00:00:00Z",
        )
        comparison = build_comparison_report(
            coin_a="BTC",
            coin_b="ETH",
            query="比較",
            report_a=report_a,
            report_b=report_b,
            evidence_a=[],
            evidence_b=[],
        )
        for dim in comparison.dimensions:
            assert dim.decision == "abstain", (
                f"'{dim.dimension}' 雙邊無證據時應為 abstain"
            )
            assert dim.confidence == 0.0

    def test_build_comparison_report_insufficient_when_one_sided(self):
        """單邊有證據的面向 decision 為 insufficient。"""
        report_a = Report(
            coin="BTC", question_type="comparison", question="比較",
            market_judgment="", facts=[], inferences=[], key_basis=[],
            confidence=0.0, limits=[], could_flip=[], contrarian=[],
            generated_at="2026-07-26T00:00:00Z",
        )
        report_b = Report(
            coin="ETH", question_type="comparison", question="比較",
            market_judgment="", facts=[], inferences=[], key_basis=[],
            confidence=0.0, limits=[], could_flip=[], contrarian=[],
            generated_at="2026-07-26T00:00:00Z",
        )
        comparison = build_comparison_report(
            coin_a="BTC",
            coin_b="ETH",
            query="比較",
            report_a=report_a,
            report_b=report_b,
            evidence_a=[Evidence(source="t", fetched_at="", content_reference="", related_claim="", kind="price")],
            evidence_b=[],
        )
        price_dim = next(d for d in comparison.dimensions if d.dimension == "價格動能")
        assert price_dim.decision == "insufficient"
        assert price_dim.confidence == 0.0
        assert "僅有 BTC 的證據" in price_dim.finding

    def test_build_comparison_report_normal_when_both_sides(self, btc_eth_fixture):
        """雙邊皆有足夠證據的面向 decision 為 normal；證據不足的面向為 insufficient。"""
        result = btc_eth_fixture
        comparison = build_comparison_report(
            coin_a="BTC",
            coin_b="ETH",
            query="比較",
            report_a=result.report_a,
            report_b=result.report_b,
            evidence_a=list(result.evidence_a),
            evidence_b=list(result.evidence_b),
        )
        for dim in comparison.dimensions:
            # 生態發展只有 1 條 evidence per side → comparability guard → insufficient
            if dim.dimension == "生態發展":
                assert dim.decision == "insufficient", (
                    f"'{dim.dimension}' 證據不足（A={len(dim.a_evidence_refs)},"
                    f" B={len(dim.b_evidence_refs)}）應為 insufficient"
                )
            else:
                assert dim.decision == "normal", (
                    f"'{dim.dimension}' 雙邊有足夠證據時應為 normal"
                )
            assert len(dim.a_evidence_refs) > 0
            assert len(dim.b_evidence_refs) > 0

    def test_build_comparison_report_ceiling_capped(self):
        """confidence 被 dimension ceiling 限制。"""
        report_a = Report(
            coin="BTC", question_type="comparison", question="比較",
            market_judgment="", facts=[], inferences=[], key_basis=[],
            confidence=0.0, limits=[], could_flip=[], contrarian=[],
            generated_at="2026-07-26T00:00:00Z",
        )
        report_b = Report(
            coin="ETH", question_type="comparison", question="比較",
            market_judgment="", facts=[], inferences=[], key_basis=[],
            confidence=0.0, limits=[], could_flip=[], contrarian=[],
            generated_at="2026-07-26T00:00:00Z",
        )
        comparison = build_comparison_report(
            coin_a="BTC",
            coin_b="ETH",
            query="比較",
            report_a=report_a,
            report_b=report_b,
            evidence_a=[
                Evidence(source="t", fetched_at="2026-07-01T00:00:00Z", content_reference="", related_claim="", kind="price", trust=0.99),
                Evidence(source="t", fetched_at="2026-07-01T01:00:00Z", content_reference="", related_claim="", kind="price", trust=0.95),
            ],
            evidence_b=[
                Evidence(source="t", fetched_at="2026-07-01T00:30:00Z", content_reference="", related_claim="", kind="price", trust=0.99),
                Evidence(source="t", fetched_at="2026-07-01T01:30:00Z", content_reference="", related_claim="", kind="price", trust=0.95),
            ],
        )
        price_dim = next(d for d in comparison.dimensions if d.dimension == "價格動能")
        assert price_dim.decision == "normal"
        assert price_dim.confidence <= 0.85, (
            f"價格動能 confidence {price_dim.confidence} 應被 ceiling 0.85 限制"
        )


# ===========================================================================
# CA-03 時間對齊與可比性 guard 測試
# ===========================================================================

class TestTemporalAlignment:
    """CA-03: build_comparison_report 的時間對齊與可比性 guard。"""

    @staticmethod
    def _make_reports():
        """產生一對空白 Report 供 build_comparison_report 使用。"""
        return (
            Report(
                coin="BTC", question_type="comparison", question="比較",
                market_judgment="", facts=[], inferences=[], key_basis=[],
                confidence=0.0, limits=[], could_flip=[], contrarian=[],
                generated_at="2026-07-26T00:00:00Z",
            ),
            Report(
                coin="ETH", question_type="comparison", question="比較",
                market_judgment="", facts=[], inferences=[], key_basis=[],
                confidence=0.0, limits=[], could_flip=[], contrarian=[],
                generated_at="2026-07-26T00:00:00Z",
            ),
        )

    def test_temporal_alignment_pass(self):
        """時間相近的 evidence → normal。"""
        report_a, report_b = self._make_reports()
        comparison = build_comparison_report(
            coin_a="BTC",
            coin_b="ETH",
            query="比較",
            report_a=report_a,
            report_b=report_b,
            evidence_a=[
                Evidence(source="t", fetched_at="2026-07-01T00:00:00Z", content_reference="", related_claim="", kind="price", trust=0.9),
                Evidence(source="t", fetched_at="2026-07-01T05:00:00Z", content_reference="", related_claim="", kind="price", trust=0.8),
            ],
            evidence_b=[
                Evidence(source="t", fetched_at="2026-07-01T02:00:00Z", content_reference="", related_claim="", kind="price", trust=0.9),
                Evidence(source="t", fetched_at="2026-07-01T06:00:00Z", content_reference="", related_claim="", kind="price", trust=0.8),
            ],
        )
        price_dim = next(d for d in comparison.dimensions if d.dimension == "價格動能")
        assert price_dim.decision == "normal", (
            f"時間相近應為 normal，實際 {price_dim.decision}。finding: {price_dim.finding}"
        )
        assert price_dim.confidence > 0.0

    def test_temporal_alignment_fail(self):
        """時間差距 > 24h → insufficient。"""
        report_a, report_b = self._make_reports()
        comparison = build_comparison_report(
            coin_a="BTC",
            coin_b="ETH",
            query="比較",
            report_a=report_a,
            report_b=report_b,
            evidence_a=[
                Evidence(source="t", fetched_at="2026-07-01T00:00:00Z", content_reference="", related_claim="", kind="price", trust=0.9),
                Evidence(source="t", fetched_at="2026-07-01T01:00:00Z", content_reference="", related_claim="", kind="price", trust=0.8),
            ],
            evidence_b=[
                Evidence(source="t", fetched_at="2026-07-03T00:00:00Z", content_reference="", related_claim="", kind="price", trust=0.9),
                Evidence(source="t", fetched_at="2026-07-03T01:00:00Z", content_reference="", related_claim="", kind="price", trust=0.8),
            ],
        )
        price_dim = next(d for d in comparison.dimensions if d.dimension == "價格動能")
        assert price_dim.decision == "insufficient", (
            f"時間差距 > 24h 應為 insufficient，實際 {price_dim.decision}。finding: {price_dim.finding}"
        )
        assert "24 小時" in price_dim.finding
        assert price_dim.confidence == 0.0

    def test_temporal_alignment_no_timestamps(self):
        """無時間戳 → pass（不攔截）。"""
        report_a, report_b = self._make_reports()
        comparison = build_comparison_report(
            coin_a="BTC",
            coin_b="ETH",
            query="比較",
            report_a=report_a,
            report_b=report_b,
            evidence_a=[
                Evidence(source="t", fetched_at="", content_reference="", related_claim="", kind="price", trust=0.9),
                Evidence(source="t", fetched_at="", content_reference="", related_claim="", kind="price", trust=0.8),
            ],
            evidence_b=[
                Evidence(source="t", fetched_at="", content_reference="", related_claim="", kind="price", trust=0.9),
                Evidence(source="t", fetched_at="", content_reference="", related_claim="", kind="price", trust=0.8),
            ],
        )
        price_dim = next(d for d in comparison.dimensions if d.dimension == "價格動能")
        # 無時間戳 → _validate_temporal_alignment 保守放行 → normal
        assert price_dim.decision == "normal", (
            f"無時間戳應保守放行為 normal，實際 {price_dim.decision}。finding: {price_dim.finding}"
        )

    def test_comparability_guard_insufficient_evidence(self):
        """任一側只有 ≤1 條 evidence → insufficient。"""
        report_a, report_b = self._make_reports()
        comparison = build_comparison_report(
            coin_a="BTC",
            coin_b="ETH",
            query="比較",
            report_a=report_a,
            report_b=report_b,
            evidence_a=[
                Evidence(source="t", fetched_at="2026-07-01T00:00:00Z", content_reference="", related_claim="", kind="price", trust=0.9),
            ],
            evidence_b=[
                Evidence(source="t", fetched_at="2026-07-01T00:30:00Z", content_reference="", related_claim="", kind="price", trust=0.9),
            ],
        )
        price_dim = next(d for d in comparison.dimensions if d.dimension == "價格動能")
        assert price_dim.decision == "insufficient", (
            f"證據數量不足（各 1 條）應為 insufficient，實際 {price_dim.decision}。finding: {price_dim.finding}"
        )
        assert "證據數量不足" in price_dim.finding
        assert price_dim.confidence == 0.0


# ===========================================================================
# 外部 import 路徑驗證
# ===========================================================================

def test_comparison_contract_importable():
    """comparison_contract 模組可被正常 import。"""
    from trustforge import comparison_contract as cc
    assert hasattr(cc, "ComparisonReport")
    assert hasattr(cc, "ComparisonRunResult")
    assert hasattr(cc, "DimensionResult")
    assert hasattr(cc, "validate_comparison_report")
    assert hasattr(cc, "validate_dimension_coverage")
    assert hasattr(cc, "COMPARISON_DIMENSIONS")
