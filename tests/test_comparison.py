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
    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        return _make_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    result = run_comparison("BTC", "ETH", "比較兩幣", offline=True)
    assert len(result) == 5, f"期望 5 元組，實際長度 {len(result)}"
    report_a, ev_a, report_b, ev_b, log = result
    assert report_a.coin == "BTC"
    assert report_b.coin == "ETH"


def test_run_comparison_both_pipelines_executed(monkeypatch):
    """兩個幣種各自有獨立 pipeline 輸出。"""
    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        return _make_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    report_a, ev_a, report_b, ev_b, log = run_comparison("BTC", "ETH", "比較", offline=True)
    assert report_a.coin == "BTC"
    assert report_b.coin == "ETH"
    assert ev_a, "BTC evidence 不可空"
    assert ev_b, "ETH evidence 不可空"


def test_run_comparison_evidence_has_source_fields(monkeypatch):
    """兩幣 evidence 每筆都有官方必備欄位。"""
    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        return _make_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    _, ev_a, _, ev_b, _ = run_comparison("BTC", "ETH", "比較", offline=True)
    for ev in ev_a + ev_b:
        d = ev.to_dict()
        for field in ("source", "fetched_at", "content_reference", "related_claim"):
            assert field in d, f"evidence 缺欄位 {field}"


def test_run_comparison_shared_log(monkeypatch):
    """比較分析共用同一 ExecutionLog，log 事件數應超過兩個 pipeline 各自起始事件。"""
    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
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
    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        return _make_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    report_a, ev_a, report_b, ev_b, _ = run_comparison("BTC", "ETH", "比較", offline=True)
    md = comparison_to_markdown(report_a, ev_a, report_b, ev_b, "比較")

    for section in ("相對強弱比較", "流動性", "各類訊號一致程度", "合併證據清單"):
        assert section in md, f"markdown 缺少章節：{section}"


def test_comparison_markdown_labels_both_coins(monkeypatch):
    """並列報告中兩個幣種名稱都必須出現。"""
    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        return _make_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    report_a, ev_a, report_b, ev_b, _ = run_comparison("BTC", "ETH", "比較", offline=True)
    md = comparison_to_markdown(report_a, ev_a, report_b, ev_b, "比較")
    assert "BTC" in md
    assert "ETH" in md


def test_comparison_evidence_json_coin_field(monkeypatch):
    """evidence.json 合併後，每筆加 coin 欄位標明歸屬（CLI 行為驗證）。"""
    import tempfile, pathlib

    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
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
    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        return _make_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    report, evidence, log = run("BTC", "分析 BTC", QuestionType.MULTI_SOURCE, offline=True)
    assert report.coin == "BTC"
    assert evidence


def test_hypothesis_still_works(monkeypatch):
    """hypothesis 題型不受 comparison 改動影響。"""
    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
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
    """_parse_comparison_coins：含逗號但幣種不在 COIN_POOL → ValueError（不靜默回 None）。"""
    from trustforge.web import _parse_comparison_coins

    with pytest.raises(ValueError):
        _parse_comparison_coins("DOGE,SHIB", "比較 DOGE SHIB")


# ---------------------------------------------------------------------------
# web.py _do_analyze comparison 路徑
# ---------------------------------------------------------------------------

def test_do_comparison_returns_five_tuple(monkeypatch):
    """_do_comparison 回傳 5 元組 (report_a, ev_a, report_b, ev_b, log)。"""
    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        return _make_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    from trustforge.web import _do_comparison

    result = _do_comparison(
        {"coin": ["BTC,ETH"], "type": ["comparison"], "q": ["比較 BTC 與 ETH"]},
        client_ip="",
    )
    assert len(result) == 5, f"期望 5 元組，實際長度 {len(result)}"
    report_a, evidence_a, report_b, evidence_b, log = result
    assert report_a.coin == "BTC"
    assert report_b.coin == "ETH"


def test_do_comparison_missing_pair_raises(monkeypatch):
    """_do_comparison，coin 只給一個且 query 無法解析 → ValueError 含「兩個幣種」。"""
    from trustforge.web import _do_comparison

    with pytest.raises(ValueError, match="兩個幣種"):
        _do_comparison(
            {"coin": ["BTC"], "type": ["comparison"], "q": ["分析 BTC"]},
            client_ip="",
        )


# ---------------------------------------------------------------------------
# 新補測試（PR #6 修正驗收）
# ---------------------------------------------------------------------------

def test_parse_comparison_coins_fullwidth_comma():
    """全形逗號「，」應正規化為半形，等同 coin=BTC,ETH。"""
    from trustforge.web import _parse_comparison_coins

    result = _parse_comparison_coins("BTC，ETH", "")
    assert result == ("BTC", "ETH"), f"全形逗號未正規化，實際 {result}"


def test_parse_comparison_coins_three_coins_error():
    """3 幣逗號分隔 → ValueError（不可靜默挑兩個）。"""
    from trustforge.web import _parse_comparison_coins

    with pytest.raises(ValueError, match="2 個"):
        _parse_comparison_coins("BTC,ETH,SOL", "")


def test_parse_comparison_coins_same_coin_error():
    """相同幣種重複 → ValueError。"""
    from trustforge.web import _parse_comparison_coins

    with pytest.raises(ValueError, match="不能相同"):
        _parse_comparison_coins("BTC,BTC", "")


def test_parse_comparison_coins_lefttoright_order():
    """query fallback（無連接詞）應按文字左到右順序，而非 COIN_POOL 順序。"""
    from trustforge.web import _parse_comparison_coins

    # SOL 在 BTC 前，COIN_POOL 順序是 BTC 先 — 左到右應回 (SOL, BTC)
    result = _parse_comparison_coins("", "SOL 表現比 BTC 好嗎")
    assert result == ("SOL", "BTC"), f"期望 (SOL, BTC)，實際 {result}"


def test_report_direction_field_set(monkeypatch):
    """build_report 應把 _direction(brief) 存入 report.direction。"""
    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        return _make_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    report, _ev, _log = run("BTC", "分析 BTC", QuestionType.MULTI_SOURCE, offline=True)
    assert report.direction in ("偏多", "偏空", "中性"), (
        f"direction 欄位未正確填入，實際 {report.direction!r}"
    )


def test_comparison_markdown_uses_direction_field(monkeypatch):
    """comparison_to_markdown 應讀 report.direction 而非掃 market_judgment。"""
    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        return _make_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    report_a, ev_a, report_b, ev_b, _ = run_comparison("BTC", "ETH", "比較", offline=True)
    # 人工覆寫 direction（確保測的是結構化欄位，不是 market_judgment 掃字）
    report_a.direction = "偏多_TEST"
    report_b.direction = "偏空_TEST"
    md = comparison_to_markdown(report_a, ev_a, report_b, ev_b, "比較")
    assert "偏多_TEST" in md, "比較報告未使用 report_a.direction"
    assert "偏空_TEST" in md, "比較報告未使用 report_b.direction"


def test_lambda_comparison_html(monkeypatch):
    """Lambda handler：comparison 題型回 HTML（含幣種對比標題，不 502）。"""
    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        return _make_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    from trustforge.lambda_handler import handler

    event = {
        "rawPath": "/analyze",
        "queryStringParameters": {
            "coin": "BTC,ETH",
            "type": "comparison",
            "q": "比較 BTC 與 ETH",
        },
        "requestContext": {},
    }
    resp = handler(event)
    assert resp["statusCode"] == 200, f"Lambda comparison HTML 回 {resp['statusCode']}"
    assert "BTC" in resp["body"] and "ETH" in resp["body"]
    assert "comparison" in resp["body"].lower() or "vs" in resp["body"].lower()


def test_lambda_comparison_json(monkeypatch):
    """Lambda handler：comparison.json 回 JSON，含 report_a/report_b/evidence_a/evidence_b。"""
    def fake_collect(query, coin=None, offline=False, data_dir=None, _failed=None):
        return _make_docs(coin)

    monkeypatch.setattr("trustforge.pipeline.collect", fake_collect)

    from trustforge.lambda_handler import handler

    event = {
        "rawPath": "/analyze.json",
        "queryStringParameters": {
            "coin": "BTC,ETH",
            "type": "comparison",
            "q": "比較 BTC 與 ETH",
        },
        "requestContext": {},
    }
    resp = handler(event)
    assert resp["statusCode"] == 200, f"Lambda comparison JSON 回 {resp['statusCode']}"
    data = json.loads(resp["body"])
    for key in ("report_a", "evidence_a", "report_b", "evidence_b", "execution_log"):
        assert key in data, f"Lambda comparison JSON 缺欄位 {key}"
    assert data["report_a"]["coin"] == "BTC"
    assert data["report_b"]["coin"] == "ETH"
