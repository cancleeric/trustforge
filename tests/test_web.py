"""web 服務 smoke 測試（不開真 socket，直接測處理邏輯）。"""
import json

from trustforge import web
from trustforge.schema import COIN_POOL


def test_do_analyze_returns_report():
    report, evidence, log = web._do_analyze({"coin": ["BTC"], "type": ["multi_source"], "q": ["test"]})
    assert report.coin == "BTC"
    assert report.market_judgment
    assert evidence
    assert log.events


def test_render_report_is_html():
    report, evidence, _ = web._do_analyze({"coin": ["ETH"], "type": ["hypothesis"], "q": ["ETH 盤整"]})
    htmlout = web._render_report(report, evidence)
    assert "市場判斷" in htmlout
    assert "<table>" in htmlout


def test_analyze_json_is_serialisable():
    import dataclasses
    report, evidence, log = web._do_analyze({"coin": ["SOL"], "type": ["multi_source"], "q": ["x"]})
    payload = {
        "report": dataclasses.asdict(report),
        "evidence": [e.to_dict() for e in evidence],
        "execution_log": log.events,
    }
    s = json.dumps(payload, ensure_ascii=False)  # 不可拋例外
    assert "report" in json.loads(s)


def test_bad_coin_rejected():
    import pytest
    with pytest.raises(ValueError):
        web._do_analyze({"coin": ["DOGE"], "type": ["multi_source"], "q": ["x"]})
    assert "BTC" in COIN_POOL
