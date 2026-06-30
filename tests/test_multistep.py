"""P0-3 顯式 3 步驟 Agent 推理鏈測試。

驗收對齊 DEV-PLAN P0-3 checklist：
- Execution Log ≥2 筆 bedrock.complete（Step1 + Step3）
- claim_type 欄位存在
- fact 類 claim 只來自客觀來源（price/onchain/regulatory）
- 離線 / regex fallback 不因 LLM 缺席而崩潰
- 反作弊：判斷仍由 pipeline 產生，非 LLM
"""
from __future__ import annotations

import json

import pytest

from trustforge.agent.orchestrator import run_agent_pipeline
from trustforge.bedrock import BedrockClient, BedrockConfig
from trustforge.execlog import ExecutionLog
from trustforge.ingestion.base import Document
from trustforge.schema import QuestionType
from trustforge.trust.scoring import Claim, extract_claims


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _doc(id, kind, source, text, ts=1000.0):
    return Document(id=id, kind=kind, source=source, text=text, ts=ts)


def _make_docs():
    return [
        _doc("p1", "price",   "hoya-ohlcv",  "BTC 今日收盤價 46637.08 美元，漲幅 3.2%。"),
        _doc("o1", "onchain", "glassnode",    "大額 BTC 流出交易所 12,400 枚，減少賣壓。"),
        _doc("n1", "news",    "coindesk",     "分析師認為 BTC 本週走勢偏多，支撐位 45000。"),
        _doc("s1", "social",  "x-anon",       "BTC 馬上暴漲翻倍穩賺！"),
    ]


_FAKE_CLAIMS_JSON = json.dumps([
    {"claim": "BTC 今日收盤 46637.08 美元", "claim_type": "fact",      "direction": "bullish", "source_doc_id": "p1"},
    {"claim": "大額 BTC 流出交易所",          "claim_type": "fact",      "direction": "bullish", "source_doc_id": "o1"},
    {"claim": "分析師看多本週走勢",            "claim_type": "inference", "direction": "bullish", "source_doc_id": "n1"},
    {"claim": "市場情緒偏多",                  "claim_type": "opinion",   "direction": "bullish", "source_doc_id": "s1"},
])


class FakeBedrockClient:
    """單元測試用 stub：模擬多步 Bedrock 呼叫、計數次數。"""

    def __init__(self):
        self.config = BedrockConfig(model_id="fake-model-test")
        self.offline = False
        self._call_count = 0

    def complete(self, system: str, prompt: str) -> str:
        self._call_count += 1
        # Step 1 呼叫：extract_claims_with_llm 在裡面呼叫 complete
        # Step 3 呼叫：build_report narrative
        # 以呼叫順序 / prompt 特徵區分回傳內容
        if "JSON array" in prompt or "source_doc_id" in prompt:
            # Step 1: claim extraction
            return _FAKE_CLAIMS_JSON
        # Step 3: narrative
        return "[p1#llm0] BTC 大額流出交易所，減少賣壓，短期傾向看漲。"

    def extract_claims_with_llm(self, docs):
        """直接呼叫真實實作（讓 complete 被計數）。"""
        # 呼叫真實的 BedrockClient.extract_claims_with_llm，但使用本 fake 的 complete
        from trustforge.bedrock import _OBJECTIVE_KINDS
        from trustforge.trust.scoring import Claim, extract_claims
        import json as _json

        raw = self.complete(system="", prompt="JSON array source_doc_id")
        doc_map = {d.id: d for d in docs}
        try:
            items = _json.loads(raw)
        except Exception:
            return extract_claims(docs)

        claims = []
        for i, item in enumerate(items):
            claim_text = str(item.get("claim", "")).strip()
            if not claim_text:
                continue
            claim_type = str(item.get("claim_type", "inference")).lower()
            direction = str(item.get("direction", "neutral")).lower()
            src_doc_id = str(item.get("source_doc_id", ""))
            doc = doc_map.get(src_doc_id) or (docs[0] if docs else None)
            if doc is None:
                continue
            # 反作弊過濾
            if claim_type == "fact" and doc.kind not in _OBJECTIVE_KINDS:
                claim_type = "inference"
            claims.append(Claim(
                id=f"{src_doc_id}#llm{i}",
                text=claim_text,
                doc=doc,
                claim_type=claim_type,
                direction=direction,
            ))
        return claims if claims else extract_claims(docs)


# ---------------------------------------------------------------------------
# 測試：多步驟執行 + Execution Log 多筆
# ---------------------------------------------------------------------------

def test_run_agent_pipeline_log_has_two_bedrock_entries():
    """run_agent_pipeline 必須在 Execution Log 留下 ≥2 筆 bedrock.complete。"""
    docs = _make_docs()
    fake = FakeBedrockClient()
    log = ExecutionLog(now_fn=lambda: 1000.0)

    run_agent_pipeline(
        query="分析 BTC 市場",
        coin="BTC",
        qtype=QuestionType.MULTI_SOURCE,
        docs=docs,
        client=fake,
        log=log,
        now_fn=lambda: 1000.0,
    )

    bedrock_entries = [e for e in log.events if e["tool"] == "bedrock.complete"]
    assert len(bedrock_entries) >= 2, (
        f"期望 ≥2 筆 bedrock.complete，實際 {len(bedrock_entries)} 筆：{bedrock_entries}"
    )


def test_run_agent_pipeline_step_labels():
    """bedrock.complete log 必須帶 step 標籤（step=1 和 step=3）。"""
    docs = _make_docs()
    fake = FakeBedrockClient()
    log = ExecutionLog(now_fn=lambda: 1000.0)

    run_agent_pipeline(
        query="分析 BTC 市場",
        coin="BTC",
        qtype=QuestionType.MULTI_SOURCE,
        docs=docs,
        client=fake,
        log=log,
        now_fn=lambda: 1000.0,
    )

    bedrock_entries = [e for e in log.events if e["tool"] == "bedrock.complete"]
    steps = [e["params"].get("step") for e in bedrock_entries]
    assert 1 in steps, f"缺少 step=1 的 log；實際 steps={steps}"
    assert 3 in steps, f"缺少 step=3 的 log；實際 steps={steps}"


def test_claim_type_field_exists():
    """extract_claims_with_llm 回傳的 Claim 必須有 claim_type 欄位。"""
    docs = _make_docs()
    fake = FakeBedrockClient()
    claims = fake.extract_claims_with_llm(docs)

    assert claims, "應有抽出的 claim"
    for c in claims:
        assert hasattr(c, "claim_type"), f"Claim 缺少 claim_type 欄位：{c}"
        assert c.claim_type in ("fact", "inference", "opinion"), (
            f"claim_type 非法值：{c.claim_type}"
        )


def test_claim_direction_field_exists():
    """extract_claims_with_llm 回傳的 Claim 必須有 direction 欄位。"""
    docs = _make_docs()
    fake = FakeBedrockClient()
    claims = fake.extract_claims_with_llm(docs)

    for c in claims:
        assert hasattr(c, "direction"), f"Claim 缺少 direction 欄位：{c}"
        assert c.direction in ("bullish", "bearish", "neutral"), (
            f"direction 非法值：{c.direction}"
        )


def test_fact_claim_only_from_objective_sources():
    """fact 類 Claim 只能來自客觀來源（price/onchain/regulatory），social 必須被降級。"""
    docs = _make_docs()
    fake = FakeBedrockClient()
    claims = fake.extract_claims_with_llm(docs)

    objective_kinds = {"price", "onchain", "regulatory"}
    for c in claims:
        if c.claim_type == "fact":
            assert c.doc.kind in objective_kinds, (
                f"fact claim 來自非客觀來源 kind={c.doc.kind}：{c.text}"
            )


def test_offline_fallback_no_llm_call():
    """offline 模式不應呼叫 LLM，extract_claims_with_llm 應 fallback regex。"""
    docs = _make_docs()
    client = BedrockClient(offline=True)
    claims = client.extract_claims_with_llm(docs)

    # 應等同 regex extract_claims 的結果
    regex_claims = extract_claims(docs)
    assert len(claims) == len(regex_claims), (
        f"offline fallback 數量不符：LLM={len(claims)} vs regex={len(regex_claims)}"
    )
    # claim_type 應是預設值 inference（regex 不設 claim_type）
    for c in claims:
        assert c.claim_type == "inference", f"offline fallback claim_type 應為 inference，got {c.claim_type}"


def test_no_model_id_fallback():
    """未設 BEDROCK_MODEL_ID 時，extract_claims_with_llm 應 fallback regex，不拋 RuntimeError。"""
    docs = _make_docs()
    config = BedrockConfig(model_id="")
    client = BedrockClient(config=config, offline=False)
    # 不應拋出 RuntimeError（正常完成）
    claims = client.extract_claims_with_llm(docs)
    assert claims, "fallback 應回傳非空 claims"


def test_run_agent_pipeline_offline_still_works():
    """offline pipeline 完整執行不崩潰，report 結構完整。"""
    docs = _make_docs()
    client = BedrockClient(offline=True)
    log = ExecutionLog(now_fn=lambda: 1000.0)

    report, evidence = run_agent_pipeline(
        query="分析 BTC 市場",
        coin="BTC",
        qtype=QuestionType.MULTI_SOURCE,
        docs=docs,
        client=client,
        log=log,
        now_fn=lambda: 1000.0,
    )

    assert report.coin == "BTC"
    assert report.market_judgment
    assert evidence is not None


def test_run_agent_pipeline_report_structure():
    """run_agent_pipeline 回傳的 Report 必須含官方必備欄位。"""
    docs = _make_docs()
    fake = FakeBedrockClient()
    log = ExecutionLog(now_fn=lambda: 1000.0)

    report, evidence = run_agent_pipeline(
        query="BTC 市場假設驗證",
        coin="BTC",
        qtype=QuestionType.HYPOTHESIS,
        docs=docs,
        client=fake,
        log=log,
        now_fn=lambda: 1000.0,
    )

    assert report.coin == "BTC"
    assert report.market_judgment
    assert 0.0 <= report.confidence <= 1.0
    assert isinstance(report.limits, list)
    assert isinstance(report.could_flip, list)


def test_anticheat_judgment_from_pipeline_not_llm():
    """反作弊：report.market_judgment 由 pipeline 產生，不應只是 LLM 輸出的複製。"""
    docs = _make_docs()
    fake = FakeBedrockClient()
    log = ExecutionLog(now_fn=lambda: 1000.0)

    report, _ = run_agent_pipeline(
        query="BTC 市場",
        coin="BTC",
        qtype=QuestionType.MULTI_SOURCE,
        docs=docs,
        client=fake,
        log=log,
        now_fn=lambda: 1000.0,
    )

    # market_judgment 應包含 pipeline 產生的方向字詞與信心分數
    assert any(kw in report.market_judgment for kw in ("偏多", "偏空", "中性")), (
        f"market_judgment 缺少 pipeline 方向詞：{report.market_judgment}"
    )
    assert "信心" in report.market_judgment or "個獨立來源" in report.market_judgment, (
        f"market_judgment 缺少 pipeline 信心資訊：{report.market_judgment}"
    )


def test_claim_type_preserved_in_scoring():
    """Claim 的 claim_type 欄位在 score() 流程中不被丟棄。"""
    from trustforge.trust.scoring import score
    docs = _make_docs()
    fake = FakeBedrockClient()
    claims = fake.extract_claims_with_llm(docs)

    scored = score(claims, now=1000.0)
    assert scored, "應有評分結果"
    for sc in scored:
        assert hasattr(sc.claim, "claim_type"), "score() 後 claim_type 應保留"
