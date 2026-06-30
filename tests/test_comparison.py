"""P1-1 comparison 題型測試。

驗收對齊 DEV-PLAN P1-1：
- run_comparison(coin_a, coin_b, query) 正常完成
- comparison report 含並列比較章節
- evidence 含兩幣各自證據（有 coin 欄位）
- 只給一個幣種 → ValueError
- 不破壞 multi_source / hypothesis 兩題型
- 離線 + monkeypatch，不打真網路
"""
from __future__ import annotations

import json

import pytest

from trustforge.agent.orchestrator import run_agent_pipeline
from trustforge.bedrock import BedrockClient, BedrockConfig
from trustforge.execlog import ExecutionLog
from trustforge.ingestion.base import Document
from trustforge.pipeline import run, run_comparison
from trustforge.schema import (
    COIN_POOL,
    QuestionType,
    comparison_to_markdown,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _doc(id, kind, source, text, ts=1_000.0):
    return Document(id=id, kind=kind, source=source, text=text, ts=ts)


def _make_docs(coin: str):
    """合成一組帶幣種前綴的文件，用於 monkeypatch。"""
    return [
        _doc(f"{coin}_p1", "price",   "hoya-ohlcv", f"{coin} 今日收盤 30000 美元，漲幅 1.2%。"),
        _doc(f"{coin}_o1", "onchain", "glassnode",   f"大額 {coin} 流出交易所，減少賣壓。"),
        _doc(f"{coin}_n1", "news",    "coindesk",    f"分析師認為 {coin} 本週走勢偏多。"),
    ]


# ---------------------------------------------------------------------------
# run_comparison 正常流程
# ---------------------------------------------------------------------------

def test_run_comparison_returns_five_tuple(monkeypatch):
    """run_comparison 回傳 (report_a, ev_a, report_b, ev_b, log) 五元組。"""
    def fake_collect(query, coin, offline, data_dir=None):
        return _make_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    result = run_comparison("BTC", "ETH", "比較兩幣", offline=True)
    assert len(result) == 5, f"期望 5 元組，實際長度 {len(result)}"
    report_a, ev_a, report_b, ev_b, log = result
    assert report_a.coin == "BTC"
    assert report_b.coin == "ETH"


def test_run_comparison_both_pipelines_executed(monkeypatch):
    """兩個幣種各自有獨立 pipeline 輸出。"""
    def fake_collect(query, coin, offline, data_dir=None):
        return _make_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    report_a, ev_a, report_b, ev_b, log = run_comparison("BTC", "ETH", "比較", offline=True)
    assert report_a.coin == "BTC"
    assert report_b.coin == "ETH"
    assert ev_a, "BTC evidence 不可空"
    assert ev_b, "ETH evidence 不可空"


def test_run_comparison_evidence_has_source_fields(monkeypatch):
    """兩幣 evidence 每筆都有官方必備欄位。"""
    def fake_collect(query, coin, offline, data_dir=None):
        return _make_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    _, ev_a, _, ev_b, _ = run_comparison("BTC", "ETH", "比較", offline=True)
    for ev in ev_a + ev_b:
        d = ev.to_dict()
        for field in ("source", "fetched_at", "content_reference", "related_claim"):
            assert field in d, f"evidence 缺欄位 {field}"


def test_run_comparison_shared_log(monkeypatch):
    """比較分析共用同一 ExecutionLog，log 事件數應超過兩個 pipeline 各自起始事件。"""
    def fake_collect(query, coin, offline, data_dir=None):
        return _make_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    _, _, _, _, log = run_comparison("BTC", "ETH", "比較", offline=True)
    # 共用 log 應包含 comparison.start + 兩幣各自 ingestion.collect 等事件
    tools = [e["tool"] for e in log.events]
    assert "comparison.start" in tools
    assert "comparison.done" in tools
    assert tools.count("ingestion.collect") >= 2


# ---------------------------------------------------------------------------
# 降級：只給一個幣種
# ---------------------------------------------------------------------------

def test_run_comparison_single_coin_raises():
    """只給一個幣種 → ValueError。"""
    with pytest.raises(ValueError, match="兩個幣種"):
        run_comparison("BTC", "BTC", "比較", offline=True)


def test_run_comparison_invalid_coin_raises():
    """非 COIN_POOL 幣種 → ValueError。"""
    with pytest.raises(ValueError):
        run_comparison("BTC", "DOGE", "比較", offline=True)


def test_run_comparison_one_coin_via_cli(monkeypatch):
    """CLI：--type comparison 但 --coin 只有一個幣種 → 回傳 exit code 2。"""
    from trustforge.cli import main

    ret = main(["analyze", "--coin", "BTC", "--type", "comparison",
                "--query", "比較", "--offline", "--quiet"])
    assert ret == 2


# ---------------------------------------------------------------------------
# comparison_to_markdown 並列章節
# ---------------------------------------------------------------------------

def test_comparison_markdown_has_required_sections(monkeypatch):
    """comparison_to_markdown 含所有必備並列比較章節。"""
    def fake_collect(query, coin, offline, data_dir=None):
        return _make_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    report_a, ev_a, report_b, ev_b, _ = run_comparison("BTC", "ETH", "比較", offline=True)
    md = comparison_to_markdown(report_a, ev_a, report_b, ev_b, "比較")

    for section in ("相對強弱比較", "流動性", "各類訊號一致程度", "合併證據清單"):
        assert section in md, f"markdown 缺少章節：{section}"


def test_comparison_markdown_labels_both_coins(monkeypatch):
    """並列報告中兩個幣種名稱都必須出現。"""
    def fake_collect(query, coin, offline, data_dir=None):
        return _make_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    report_a, ev_a, report_b, ev_b, _ = run_comparison("BTC", "ETH", "比較", offline=True)
    md = comparison_to_markdown(report_a, ev_a, report_b, ev_b, "比較")
    assert "BTC" in md
    assert "ETH" in md


def test_comparison_evidence_json_coin_field(monkeypatch):
    """evidence.json 合併後，每筆加 coin 欄位標明歸屬（CLI 行為驗證）。"""
    import tempfile, pathlib

    def fake_collect(query, coin, offline, data_dir=None):
        return _make_docs(coin)

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

        ev_path = pathlib.Path(tmpdir) / "evidence.json"
        assert ev_path.exists()
        data = json.loads(ev_path.read_text(encoding="utf-8"))
        assert data, "evidence.json 不可空"
        for item in data:
            assert "coin" in item, f"evidence 缺少 coin 欄位：{item}"
        coins_found = {item["coin"] for item in data}
        assert "BTC" in coins_found, "evidence 缺少 BTC"
        assert "ETH" in coins_found, "evidence 缺少 ETH"


# ---------------------------------------------------------------------------
# 不破壞既有題型
# ---------------------------------------------------------------------------

def test_multi_source_still_works(monkeypatch):
    """multi_source 題型不受 comparison 改動影響。"""
    def fake_collect(query, coin, offline, data_dir=None):
        return _make_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    report, evidence, log = run("BTC", "分析 BTC", QuestionType.MULTI_SOURCE, offline=True)
    assert report.coin == "BTC"
    assert evidence


def test_hypothesis_still_works(monkeypatch):
    """hypothesis 題型不受 comparison 改動影響。"""
    def fake_collect(query, coin, offline, data_dir=None):
        return _make_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    report, evidence, log = run("ETH", "ETH 短期將盤整", QuestionType.HYPOTHESIS, offline=True)
    assert report.coin == "ETH"
    assert "假設" in report.market_judgment


# ---------------------------------------------------------------------------
# web.py _parse_comparison_coins
# ---------------------------------------------------------------------------

def test_parse_comparison_coins_from_param():
    """_parse_comparison_coins：從 coin 參數解析。"""
    from trustforge.web import _parse_comparison_coins

    result = _parse_comparison_coins("BTC,ETH", "")
    assert result == ("BTC", "ETH")


def test_parse_comparison_coins_from_query():
    """_parse_comparison_coins：從 query 文字解析。"""
    from trustforge.web import _parse_comparison_coins

    result = _parse_comparison_coins("BTC", "比較 BTC 與 ETH 當前市場")
    assert result == ("BTC", "ETH")


def test_parse_comparison_coins_invalid():
    """_parse_comparison_coins：無效幣種回傳 None。"""
    from trustforge.web import _parse_comparison_coins

    result = _parse_comparison_coins("DOGE,SHIB", "比較 DOGE SHIB")
    assert result is None


# ---------------------------------------------------------------------------
# web.py _do_analyze comparison 路徑
# ---------------------------------------------------------------------------

def test_do_analyze_comparison_returns_seven_tuple(monkeypatch):
    """_do_analyze(type=comparison) 回傳 7 元組（含兩幣各自結果）。"""
    def fake_collect(query, coin, offline, data_dir=None):
        return _make_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    from trustforge.web import _do_analyze

    result = _do_analyze(
        {"coin": ["BTC,ETH"], "type": ["comparison"], "q": ["比較 BTC 與 ETH"]},
        client_ip="",
    )
    assert len(result) == 6, f"期望 6 元組，實際長度 {len(result)}"


def test_do_analyze_comparison_missing_pair_raises(monkeypatch):
    """_do_analyze(comparison)，coin 只給一個且 query 無法解析 → ValueError。"""
    from trustforge.web import _do_analyze

    with pytest.raises(ValueError, match="兩個幣種"):
        _do_analyze(
            {"coin": ["BTC"], "type": ["comparison"], "q": ["分析 BTC"]},
            client_ip="",
        )
