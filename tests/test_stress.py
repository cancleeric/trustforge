"""P0-5 壓測矩陣測試 + 失敗降級驗證。

測試範圍：
1. 矩陣：5 幣 × multi_source + 5 幣 × hypothesis（offline，monkeypatch collect）
2. 矩陣：5 comparison 配對（offline，monkeypatch collect）
3. 來源逾時降級：FailingSource → collect 跳過 + limits 有記錄 + pipeline 不崩
4. Bedrock 降級：offline=True → regex fallback → pipeline 不崩

全部 offline，不打真網路。
"""
from __future__ import annotations

import pytest

from trustforge.ingestion.base import (
    Document,
    OfflineSampleSource,
    Source,
    SOURCE_KINDS,
    collect,
)
from trustforge.pipeline import run, run_comparison
from trustforge.schema import QuestionType


# ── helpers ──────────────────────────────────────────────────────────────────

def _doc(id: str, kind: str, source: str, text: str, ts: float = 1_000.0) -> Document:
    return Document(id=id, kind=kind, source=source, text=text, ts=ts)


def _make_docs(coin: str) -> list[Document]:
    """合成有幣種前綴的文件（multi-kind，讓 scoring 有足夠輸入）。"""
    c = coin
    return [
        _doc(f"{c}_p1", "price",      "hoya-ohlcv", f"{c} 今日收盤 50000 美元，漲幅 2.1%。", ts=2_000.0),
        _doc(f"{c}_o1", "onchain",    "glassnode",  f"大額 {c} 流出交易所，鯨魚累積中。",   ts=1_900.0),
        _doc(f"{c}_n1", "news",       "coindesk",   f"分析師看多 {c} 本週走勢。",             ts=1_800.0),
        _doc(f"{c}_s1", "social",     "reddit",     f"{c} 社群情緒高漲，看多聲浪上升。",       ts=1_700.0),
        _doc(f"{c}_r1", "regulatory", "sec-rss",    f"監管機構重申 {c} 合規框架。",           ts=1_600.0),
        _doc(f"{c}_o2", "onchain",    "blockchain", f"{c} 礦工持倉增加，賣壓降低。",          ts=1_500.0),
        _doc(f"{c}_n2", "news",       "decrypt",    f"{c} 成交量突破近期高點。",              ts=1_400.0),
        _doc(f"{c}_s2", "social",     "cryptopanic",f"{c} 熱度指數上升至 72。",              ts=1_300.0),
        _doc(f"{c}_h1", "hoyabit",    "hoyabit",    f"HOYA BIT: {c} 7 日漲幅 8.3%。",        ts=1_200.0),
    ]


def _fake_collect(coin_override: str | None = None):
    """回傳一個 fake collect，使用 _make_docs 而非真實 API / 樣本檔。"""
    def _fn(query, coin=None, offline=False, data_dir=None, _failed=None):
        return _make_docs(coin_override or coin or "BTC")
    return _fn


# ── 矩陣：單幣 multi_source + hypothesis ─────────────────────────────────────

SINGLE_MATRIX = [
    (coin, qtype)
    for coin in ["BTC", "ETH", "SOL", "BNB", "XRP"]
    for qtype in [QuestionType.MULTI_SOURCE, QuestionType.HYPOTHESIS]
]


@pytest.mark.parametrize("coin,qtype", SINGLE_MATRIX)
def test_matrix_single_offline(coin, qtype, monkeypatch):
    """矩陣：單幣 offline 可跑 → 4 交付件非空 → 不拋例外。

    4 交付件：report（有 market_judgment）、evidence（非空 list）、
    execution_log（有 events）、code（repo 本身，永遠存在）。
    """
    monkeypatch.setattr("trustforge.pipeline.collect", _fake_collect(coin))

    report, evidence, log = run(coin, f"分析 {coin}", qtype, offline=True)

    # 交付件 1：report 有內容
    assert report.market_judgment, f"[{coin}/{qtype}] market_judgment 不可空"
    assert report.facts is not None, f"[{coin}/{qtype}] facts 不可為 None"
    assert report.key_basis is not None, f"[{coin}/{qtype}] key_basis 不可為 None"
    # 交付件 2：evidence 非空
    assert evidence, f"[{coin}/{qtype}] evidence 不可空"
    assert len(evidence) >= 1, f"[{coin}/{qtype}] evidence 至少 1 筆"
    # 交付件 3：execution_log 有事件
    assert log.events, f"[{coin}/{qtype}] execution_log 不可空"
    # 基本欄位完整性
    for ev in evidence:
        d = ev.to_dict()
        for field in ("source", "fetched_at", "content_reference", "related_claim"):
            assert field in d, f"[{coin}/{qtype}] evidence 缺欄位 {field}"


# ── 矩陣：comparison 配對 ─────────────────────────────────────────────────────

COMPARISON_MATRIX = [
    ("BTC", "ETH"),
    ("ETH", "SOL"),
    ("SOL", "BNB"),
    ("BNB", "XRP"),
    ("XRP", "BTC"),
]


@pytest.mark.parametrize("coin_a,coin_b", COMPARISON_MATRIX)
def test_matrix_comparison_offline(coin_a, coin_b, monkeypatch):
    """矩陣：comparison 配對 offline 可跑 → 4 交付件 + 兩幣各有 evidence → 不拋例外。"""
    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        return _make_docs(coin or "BTC")

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    report_a, ev_a, report_b, ev_b, log = run_comparison(
        coin_a, coin_b, f"比較 {coin_a} 與 {coin_b}", offline=True
    )

    # 各幣 report
    assert report_a.coin == coin_a, f"report_a.coin 期望 {coin_a}，實際 {report_a.coin}"
    assert report_b.coin == coin_b, f"report_b.coin 期望 {coin_b}，實際 {report_b.coin}"
    # 各幣 evidence 非空
    assert ev_a, f"[{coin_a}/{coin_b}] ev_a 不可空"
    assert ev_b, f"[{coin_a}/{coin_b}] ev_b 不可空"
    # 合計 evidence 計數（DEV-PLAN：comparison ≥ 12）
    total = len(ev_a) + len(ev_b)
    assert total >= 12, f"[{coin_a}/{coin_b}] 合計 evidence 期望 ≥12，實際 {total}"
    # execution_log
    assert log.events, f"[{coin_a}/{coin_b}] execution_log 不可空"
    # market_judgment 兩幣非空
    assert report_a.market_judgment, f"[{coin_a}] market_judgment 不可空"
    assert report_b.market_judgment, f"[{coin_b}] market_judgment 不可空"


# ── 失敗降級驗證 1：來源逾時 ─────────────────────────────────────────────────


class FailingSource(Source):
    """模擬逾時/失敗的來源。"""
    kind = "news"
    name = "failing-news-src"

    def fetch(self, query: str, coin: str = "") -> list[Document]:  # noqa: ARG002
        raise TimeoutError("模擬來源逾時（測試用）")


def test_collect_skips_failing_source():
    """collect 對 FailingSource 應靜默跳過，其餘 working sources 照常回傳文件。"""
    class GoodSource(Source):
        kind = "onchain"
        name = "good-onchain-src"

        def fetch(self, query: str, coin: str = "") -> list[Document]:
            return [_doc("g1", "onchain", "good-onchain-src", "test doc", ts=1_000.0)]

    failed_names: list[str] = []
    docs = collect(
        "測試",
        coin="BTC",
        sources=[FailingSource(), GoodSource()],
        offline=True,
        _failed=failed_names,
    )

    assert len(docs) >= 1, "GoodSource 的文件應被收集"
    assert "failing-news-src" in failed_names, (
        f"collect 應將 FailingSource 記入 _failed，實際：{failed_names}"
    )


def test_source_failure_reflected_in_limits(monkeypatch):
    """collect 記錄失敗來源 → pipeline.run 應將其加入 report.limits。"""
    def fake_collect_with_fail(query, coin=None, offline=False, data_dir=None, _failed=None):
        """回傳 docs，同時模擬 failing-news-src 失敗記錄。"""
        if _failed is not None:
            _failed.append("failing-news-src")
        return _make_docs(coin or "BTC")

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect_with_fail)

    report, evidence, log = run("BTC", "分析 BTC", QuestionType.MULTI_SOURCE, offline=True)

    # report.limits 應含失敗來源名稱
    limits_text = " ".join(report.limits)
    assert "failing-news-src" in limits_text, (
        f"report.limits 應記錄失敗來源 'failing-news-src'，實際：{report.limits}"
    )
    # pipeline 不崩，evidence 仍有內容
    assert evidence, "evidence 不可空（working sources 仍提供文件）"


def test_pipeline_does_not_crash_with_failing_source(monkeypatch):
    """end-to-end：FailingSource + 其他 working sources → pipeline 不崩 → report 完整。"""
    working_sources = [OfflineSampleSource(k, k) for k in SOURCE_KINDS]
    sources_with_fail = [FailingSource()] + working_sources

    def patched_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        return collect(
            query,
            coin=coin,
            sources=sources_with_fail,
            offline=True,
            _failed=_failed,
        )

    monkeypatch.setattr("trustforge.pipeline.collect", patched_collect)

    # 不應拋任何未捕獲例外
    report, evidence, log = run("BTC", "分析 BTC 降級測試", QuestionType.MULTI_SOURCE, offline=True)

    assert report is not None, "report 不可為 None"
    assert report.market_judgment, "market_judgment 不可空"
    assert evidence, "evidence 不可空"
    # limits 應有 failing-news-src 記錄
    limits_text = " ".join(report.limits)
    assert "failing-news-src" in limits_text, (
        f"limits 應含失敗來源名稱，實際：{report.limits}"
    )


# ── 失敗降級驗證 2：Bedrock 離線 ─────────────────────────────────────────────

def test_bedrock_offline_fallback_does_not_crash(monkeypatch):
    """Bedrock 離線（offline=True）→ regex fallback → pipeline 不崩 → narrative 非空。"""
    monkeypatch.setattr("trustforge.pipeline.collect", _fake_collect("ETH"))

    report, evidence, log = run(
        "ETH", "假設 ETH 短期將突破歷史高點", QuestionType.HYPOTHESIS, offline=True
    )

    assert report is not None, "Bedrock offline 時 report 不可為 None"
    assert report.market_judgment, "Bedrock offline 時 market_judgment 不可空"
    assert evidence, "evidence 不可空"

    # Execution log 至少有 bedrock.complete 事件（Step1 / Step3 的 offline 記錄）
    bedrock_events = [e for e in log.events if e["tool"] == "bedrock.complete"]
    assert len(bedrock_events) >= 1, (
        f"offline 模式應有 bedrock.complete 事件（offline/regex-fallback），"
        f"實際事件：{[e['tool'] for e in log.events]}"
    )


def test_bedrock_offline_narrative_has_content(monkeypatch):
    """offline 降級模式：narrative 應有實質內容（非空字串）。"""
    monkeypatch.setattr("trustforge.pipeline.collect", _fake_collect("XRP"))

    report, evidence, log = run(
        "XRP", "分析 XRP 近兩週市場狀況", QuestionType.MULTI_SOURCE, offline=True
    )

    # market_judgment 非空且非僅空白
    assert report.market_judgment.strip(), "narrative 不可為空或純空白"
    # facts 有內容
    assert report.facts, "offline 模式 facts 不可空"


# ── 確保既有題型不受影響 ──────────────────────────────────────────────────────

def test_existing_multi_source_unaffected(monkeypatch):
    """確保新改動不破壞既有 multi_source 題型行為。"""
    monkeypatch.setattr("trustforge.pipeline.collect", _fake_collect("BTC"))
    report, evidence, log = run("BTC", "分析 BTC", QuestionType.MULTI_SOURCE, offline=True)
    assert report.coin == "BTC"
    assert evidence


def test_existing_hypothesis_unaffected(monkeypatch):
    """確保新改動不破壞既有 hypothesis 題型行為。"""
    monkeypatch.setattr("trustforge.pipeline.collect", _fake_collect("SOL"))
    report, evidence, log = run("SOL", "SOL 將上漲", QuestionType.HYPOTHESIS, offline=True)
    assert report.coin == "SOL"
    assert "假設" in report.market_judgment


def test_existing_comparison_unaffected(monkeypatch):
    """確保新改動不破壞既有 comparison 題型行為。"""
    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        return _make_docs(coin or "BTC")
    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)
    ra, ea, rb, eb, log = run_comparison("BTC", "ETH", "比較 BTC ETH", offline=True)
    assert ra.coin == "BTC"
    assert rb.coin == "ETH"
    assert ea and eb


# ── _failed 去重測試 ──────────────────────────────────────────────────────────

def test_pipeline_limits_dedup_on_repeated_failure(monkeypatch):
    """同一來源多次記入 _failed → report.limits 不重複出現相同條目。"""
    def fake_collect_with_dup_fail(query, coin=None, offline=False, data_dir=None, _failed=None):
        """模擬同一來源失敗兩次（例如 collect 被呼叫兩次或 source 重複記錄）。"""
        if _failed is not None:
            _failed.append("dup-src")
            _failed.append("dup-src")   # 故意重複
            _failed.append("other-src")
        return _make_docs(coin or "BTC")

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect_with_dup_fail)
    report, evidence, log = run("BTC", "分析 BTC", QuestionType.MULTI_SOURCE, offline=True)

    dup_entries = [l for l in report.limits if "dup-src" in l]
    assert len(dup_entries) == 1, (
        f"dup-src 應只出現一次，實際 limits={report.limits}"
    )
    other_entries = [l for l in report.limits if "other-src" in l]
    assert len(other_entries) == 1, (
        f"other-src 應出現一次，實際 limits={report.limits}"
    )
