"""端到端報告管線測試 — 對齊官方交付規格。"""
from trustforge.agent.orchestrator import build_report
from trustforge.bedrock import BedrockClient
from trustforge.execlog import ExecutionLog
from trustforge.ingestion.base import OHLCV_DIR, collect
from trustforge.schema import QuestionType
from trustforge.trust.scoring import aggregate, extract_claims, score


def _run(coin="BTC", query="分析 BTC 過去兩週市場狀況", qtype=QuestionType.MULTI_SOURCE):
    # 釘合成樣本資料夾，確保判斷可預期（不受官方真實資料波動影響）
    docs = collect(query, coin=coin, offline=True, data_dir=OHLCV_DIR)
    now = max(d.ts for d in docs)
    brief = aggregate(score(extract_claims(docs), now=now), query)
    log = ExecutionLog(now_fn=lambda: 1000.0)
    return build_report(query, coin, qtype, brief,
                        client=BedrockClient(offline=True), log=log, now_fn=lambda: 1000.0)


def test_report_has_required_sections():
    report, evidence = _run()
    assert report.market_judgment
    assert report.facts, "需有事實層"
    assert report.key_basis, "需有關鍵依據"
    assert 0.0 <= report.confidence <= 1.0
    md = report.to_markdown(evidence)
    for section in ("結論 / 市場判斷", "關鍵依據", "信心說明", "事實", "推論"):
        assert section in md


def test_evidence_has_official_fields():
    _, evidence = _run()
    assert evidence
    for e in evidence:
        d = e.to_dict()
        for fld in ("source", "fetched_at", "content_reference", "related_claim"):
            assert fld in d, f"Evidence 缺官方欄位 {fld}"
        assert d["content_reference"], "content_reference 不可空"


def test_price_evidence_is_traceable():
    """價格證據需帶交易對 + 區間，可回溯。"""
    _, evidence = _run()
    price_ev = [e for e in evidence if e.kind == "price"]
    assert price_ev, "應有價格證據"
    assert any("/USDT" in e.content_reference for e in price_ev)


def test_btc_judgment_is_bearish_from_data():
    """樣本 BTC 緩跌 → 我方判斷應偏空（由 pipeline 產生，非外部結論）。"""
    report, _ = _run()
    assert "偏空" in report.market_judgment


def test_limits_reported_when_sources_thin():
    """信心說明須含限制（樣本來源類型有限）。"""
    report, _ = _run()
    assert report.limits, "資料有限時須明確說明限制，不可硬給結論"


def test_hypothesis_framing():
    report, _ = _run(query="BTC 短期將盤整", qtype=QuestionType.HYPOTHESIS)
    assert "假設" in report.market_judgment
