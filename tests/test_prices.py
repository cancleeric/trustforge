"""OHLCV 價格連接器測試。"""
from pathlib import Path

from trustforge.ingestion.base import OFFICIAL_OHLCV_DIR, collect
from trustforge.ingestion.prices import load_ohlcv, ohlcv_lineage, price_facts
from trustforge.agent.orchestrator import build_report
from trustforge.bedrock import BedrockClient
from trustforge.execlog import ExecutionLog
from trustforge.schema import QuestionType
from trustforge.trust.scoring import aggregate, extract_claims, score

DATA = Path(__file__).resolve().parents[1] / "demo" / "sample_data" / "ohlcv"


def test_load_ohlcv_sorted_and_typed():
    bars = load_ohlcv("BTC", DATA)
    assert len(bars) >= 30
    assert bars == sorted(bars, key=lambda b: b.date)
    assert isinstance(bars[0].close, float)


def test_price_facts_have_traceable_reference():
    bars = load_ohlcv("BTC", DATA)
    facts = price_facts("BTC", bars, source_file="BTC.csv")
    assert facts, "應產出價格事實"
    for d in facts:
        assert d.kind == "price"
        assert d.meta.get("content_reference"), "每條事實需有可回溯的 content_reference"
        assert d.meta.get("trading_pair") == "BTC/USDT"


def test_btc_sample_is_downtrend():
    """樣本 BTC 設計為緩跌，方向事實應為下跌。"""
    bars = load_ohlcv("BTC", DATA)
    ret_fact = price_facts("BTC", bars)[0]
    assert "下跌" in ret_fact.text


def test_official_five_year_dataset_lineage_is_pinned_to_file_bytes():
    bars = load_ohlcv("BTC", OFFICIAL_OHLCV_DIR)
    lineage = ohlcv_lineage("BTC", OFFICIAL_OHLCV_DIR, bars)

    assert lineage["dataset_role"] == "competition_baseline"
    assert lineage["file"] == "BTC_daily_ohlcv.csv"
    assert lineage["rows"] == 1826
    assert lineage["coverage"] == {"start_date": "2021-06-01", "end_date": "2026-05-31"}
    assert len(lineage["sha256"]) == 64
    assert lineage["columns"] == ["date", "open", "high", "low", "close", "volume"]


def test_official_price_evidence_contains_five_year_context_and_lineage():
    docs = collect("分析 BTC", coin="BTC", offline=True)
    price_docs = [doc for doc in docs if doc.kind == "price"]

    assert any("完整歷史涵蓋 2021-06-01~2026-05-31（1826 日）" in doc.text for doc in price_docs)
    assert all(doc.meta["data_lineage"]["sha256"] for doc in price_docs)
    assert {doc.meta["data_lineage"]["analysis_window"] for doc in price_docs} >= {
        "2021-06-01~2026-05-31", "2026-05-18~2026-05-31",
    }


def test_final_report_exports_dataset_lineage_for_auditors():
    docs = collect("分析 BTC", coin="BTC", offline=True)
    now = max(doc.ts for doc in docs)
    brief = aggregate(score(extract_claims(docs), now=now), "分析 BTC", coin="BTC")
    report, evidence = build_report(
        "分析 BTC", "BTC", QuestionType.MULTI_SOURCE, brief,
        client=BedrockClient(offline=True), log=ExecutionLog(now_fn=lambda: 1_000_000.0),
        now_fn=lambda: 1_000_000.0, run_scope_id="test-prices",
    )
    markdown = report.to_markdown(evidence)

    assert "資料血緣 / 可重現性" in markdown
    assert "BTC_daily_ohlcv.csv" in markdown
    assert "SHA-256" in markdown
