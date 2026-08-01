"""N11：EN 模式下敘事輸出必須是英文（market_judgment / narrative / explanation）。

反向對照原則：每個測試都斷言「實際產生的字串內容」，不是「原始碼裡有某段
字面值」——把 orchestrator 的 locale 分派拿掉就必須變 RED。
"""
import io
import json
import re

import pytest

from trustforge.agent.narrative_locale import normalize_locale
from trustforge.agent.orchestrator import build_report
from trustforge.bedrock import BedrockClient
from trustforge.execlog import ExecutionLog
from trustforge.ingestion.base import OHLCV_DIR, collect
from trustforge.schema import QuestionType
from trustforge.trust.scoring import aggregate, extract_claims, score

_CJK = re.compile(r"[　-〿㐀-䶿一-鿿＀-￯]")


def _has_cjk(text: str) -> bool:
    return bool(_CJK.search(text))


def _run(locale, coin="BTC", query="analyse BTC market state", qtype=QuestionType.MULTI_SOURCE,
         client=None):
    docs = collect(query, coin=coin, offline=True, data_dir=OHLCV_DIR)
    now = max(d.ts for d in docs)
    brief = aggregate(score(extract_claims(docs), now=now), query)
    log = ExecutionLog(now_fn=lambda: 1000.0)
    return build_report(query, coin, qtype, brief,
                        client=client or BedrockClient(offline=True), log=log,
                        now_fn=lambda: 1000.0, locale=locale, run_scope_id="test-narrative-locale")


# --- locale 收斂 -----------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("en", "en"), ("EN", "en"), ("en-US", "en"),
    ("zh-Hant", "zh-Hant"), ("zh-TW", "zh-Hant"),
    ("klingon", "zh-Hant"), ("", "zh-Hant"), (None, "zh-Hant"),
    (123, "zh-Hant"), ({"a": 1}, "zh-Hant"),
])
def test_normalize_locale(raw, expected):
    assert normalize_locale(raw) == expected


# --- market_judgment -------------------------------------------------------

def test_market_judgment_is_english_under_en_locale():
    report, _ = _run("en")
    assert report.market_judgment
    assert not _has_cjk(report.market_judgment), report.market_judgment
    assert "independent sources" in report.market_judgment


def test_market_judgment_defaults_to_chinese():
    report, _ = _run("zh-Hant")
    assert _has_cjk(report.market_judgment)
    assert "個獨立來源支撐" in report.market_judgment


def test_illegal_locale_falls_back_to_chinese_narrative():
    report, _ = _run("klingon")
    assert "個獨立來源支撐" in report.market_judgment


# --- narrative（inferences 內的行文層）-------------------------------------

def test_offline_narrative_placeholder_is_english_under_en_locale():
    report, _ = _run("en")
    assert report.inferences
    for line in report.inferences:
        assert not _has_cjk(line), line
    assert any("No online model generation" in line for line in report.inferences)


def test_offline_narrative_placeholder_stays_chinese_by_default():
    report, _ = _run("zh-Hant")
    assert any("本次未執行線上模型生成" in line for line in report.inferences)


class _FailingClient(BedrockClient):
    def complete(self, *a, **kw):  # noqa: D102
        raise RuntimeError("bedrock down")


def test_degraded_narrative_is_english_under_en_locale():
    client = _FailingClient(offline=False)
    report, _ = _run("en", client=client)
    joined = " ".join(report.inferences)
    assert "Narrative service temporarily unavailable" in joined
    assert not _has_cjk(joined), joined


def test_degraded_narrative_stays_chinese_by_default():
    client = _FailingClient(offline=False)
    report, _ = _run("zh-Hant", client=client)
    assert any("行文服務暫時無法使用" in line for line in report.inferences)


def test_llm_prompt_is_english_under_en_locale():
    """Step3 送給模型的 system/prompt 在 EN locale 必須要求英文輸出。"""
    captured = {}

    class _CaptureClient(BedrockClient):
        def complete(self, *, system, prompt):
            captured["system"] = system
            captured["prompt"] = prompt
            return super().complete(system=system, prompt=prompt)

    _run("en", client=_CaptureClient(offline=True))
    assert "Write your entire answer in English." in captured["system"]
    assert "Our judgement:" in captured["prompt"]
    # 指令段（非 UNTRUSTED_DATA_JSON 內的原始資料）不得殘留中文指令
    instructions = captured["prompt"].split("</UNTRUSTED_DATA_JSON>", 1)[1]
    assert not _has_cjk(instructions), instructions


def test_llm_prompt_stays_chinese_by_default():
    captured = {}

    class _CaptureClient(BedrockClient):
        def complete(self, *, system, prompt):
            captured["system"] = system
            captured["prompt"] = prompt
            return super().complete(system=system, prompt=prompt)

    _run("zh-Hant", client=_CaptureClient(offline=True))
    assert "你是加密市場分析助理" in captured["system"]
    assert "我方判斷：" in captured["prompt"]


# --- BasisItem.explanation -------------------------------------------------

def test_basis_explanation_is_english_under_en_locale():
    report, _ = _run("en")
    assert report.key_basis
    for basis in report.key_basis:
        assert not _has_cjk(basis.explanation), basis.explanation
        assert basis.explanation.startswith("Source ")


def test_basis_explanation_stays_chinese_by_default():
    report, _ = _run("zh-Hant")
    assert all(b.explanation.startswith("來源 ") for b in report.key_basis)


# --- 結構化欄位不得被語系污染 ---------------------------------------------

def test_structured_direction_field_is_locale_independent():
    en, _ = _run("en")
    zh, _ = _run("zh-Hant")
    assert en.direction == zh.direction
    assert en.decision_state == zh.decision_state


# --- API 層 ----------------------------------------------------------------

def _post_analysis_question(payload, monkeypatch):
    from trustforge import web

    seen = {}

    class _FakeFlow:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def submit_manual(self, coin, mode, question, *, locale="zh-Hant"):
            seen["locale"] = locale
            return "question-1", "flow-1"

    import trustforge.analysis_flow as af
    monkeypatch.setattr(af, "AnalysisFlow", _FakeFlow)
    monkeypatch.setattr(web, "_check_status_rate_limit", lambda *a, **k: None)
    body = json.dumps(payload).encode()
    headers = {"Content-Length": str(len(body)), "Content-Type": "application/json"}
    code, text = web._handle_api_analysis_question(headers, io.BytesIO(body))
    return code, text, seen


# --- 端到端：flow 佇列 → build_report → 已發布結果 ---------------------------

def _flow_docs():
    from trustforge.ingestion.base import Document
    now = 1_700_000_000.0
    return [
        Document(id="a", kind="price", source="source-a", text="BTC 價格盤整。",
                 url="https://a.test", ts=now, meta={}),
        Document(id="b", kind="news", source="source-b", text="BTC 市場成交量保持穩定。",
                 url="https://b.test", ts=now, meta={}),
    ]


def _published_judgment(tmp_path, monkeypatch, locale):
    from trustforge.analysis_flow import AnalysisFlow
    monkeypatch.setattr("trustforge.analysis_flow.collect", lambda *a, **k: _flow_docs())
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    snapshot = flow.create_snapshot("BTC")
    job_id = flow.enqueue_job(snapshot, "risk", "what is the risk?", locale=locale)
    assert job_id
    flow.start(); flow.join(); flow.stop()
    payload = json.loads(flow._conn().execute(
        "SELECT payload_json FROM analysis_results WHERE job_id=?", (job_id,),
    ).fetchone()[0])
    return payload["report"]


def test_flow_end_to_end_publishes_english_report(tmp_path, monkeypatch):
    report = _published_judgment(tmp_path, monkeypatch, "en")
    assert not _has_cjk(report["market_judgment"]), report["market_judgment"]
    for basis in report["key_basis"]:
        assert not _has_cjk(basis["explanation"]), basis["explanation"]


def test_flow_end_to_end_defaults_to_chinese_report(tmp_path, monkeypatch):
    report = _published_judgment(tmp_path, monkeypatch, "zh-Hant")
    assert _has_cjk(report["market_judgment"])


def test_api_accepts_and_forwards_en_locale(monkeypatch):
    code, text, seen = _post_analysis_question(
        {"coin": "BTC", "mode": "multi_source", "question": "state?", "locale": "en"}, monkeypatch)
    assert code == 202, text
    assert seen["locale"] == "en"


def test_api_illegal_locale_falls_back_without_500(monkeypatch):
    code, text, seen = _post_analysis_question(
        {"coin": "BTC", "mode": "multi_source", "question": "state?", "locale": "klingon"},
        monkeypatch)
    assert code == 202, text
    assert seen["locale"] == "zh-Hant"


def test_api_without_locale_defaults_to_chinese(monkeypatch):
    code, text, seen = _post_analysis_question(
        {"coin": "BTC", "mode": "multi_source", "question": "state?"}, monkeypatch)
    assert code == 202, text
    assert seen["locale"] == "zh-Hant"


# --- N11 生產回歸：submit_manual 的 5 分鐘 dedup 窗口不得忽略 locale --------
#
# 真實事故：`AnalysisFlow.submit_manual` 對同一 coin/mode/question 在
# MANUAL_DEDUP_WINDOW_SEC（5 分鐘）內重複呼叫時，會直接回傳「先前那個已完成
# job 的 job_id」——但 analysis_jobs 表沒有 locale 欄位，這個 dedup 查詢完全
# 不看 locale。結果：先用預設中文跑過一次的題目，緊接著用 locale='en' 再問
# 一次，會原封不動拿回舊的中文 job/報告，且 retry_count=0（乾淨路徑，不會被
# 27 個只測 build_report / enqueue_job 的單元測試發現，因為那些測試都是各自
# 獨立、乾淨的 flow 實例，從未在同一個 flow 上對同一題目連續打兩種 locale）。
# --- N11 生產回歸：跨行程（POST → 獨立 daemon 行程輪詢 DB 撿起工作）--------
#
# 這才是真正命中生產缺陷的路徑：`/api/analysis-question` 由 web 行程處理，
# 它建立一個「用完即丟」的 `AnalysisFlow()`（未呼叫 `.start()`），寫完 DB
# 就結束——真正跑 pipeline 的是另一個完全獨立的 OS 行程
# （`run_analysis_flow.py --daemon`），靠輪詢 DB（`recover()`/
# `adopt_pending()`）重建 package。在此之前，`locale` 只活在 web 行程的
# in-memory package 裡，daemon 行程重建 package 時只帶 job_id，locale 必然
# 掉回中文——不需要 dedup、不需要 retry，全新一次性 job 就會中獎，這才是
# CEO 回報的真實症狀（retry_count=0、乾淨路徑）。
def test_api_analysis_question_en_locale_survives_separate_worker_process(tmp_path, monkeypatch):
    from trustforge import web
    import trustforge.analysis_flow as af

    db_path = tmp_path / "flow.sqlite3"
    monkeypatch.setattr(af, "_db_path", lambda path=None: db_path if path is None else af.Path(path))
    monkeypatch.setattr(af, "collect", lambda *a, **k: _flow_docs())
    monkeypatch.setattr(web, "_check_status_rate_limit", lambda *a, **k: None)

    body = json.dumps({"coin": "ETH", "mode": "risk",
                        "question": "Is it a good time to enter ETH now?", "locale": "en"}).encode()
    headers = {"Content-Length": str(len(body)), "Content-Type": "application/json"}
    # This calls the real `_handle_api_analysis_question`, which internally
    # constructs its own short-lived `AnalysisFlow()` (no `.start()`), just
    # like production.
    code, text = web._handle_api_analysis_question(headers, io.BytesIO(body))
    assert code == 202, text
    job_id = json.loads(text)["data"]["job_id"]
    assert job_id

    # Simulate the *separate* `run_analysis_flow.py --daemon` process: a
    # brand-new `AnalysisFlow` instance with no shared memory with the web
    # handler above, discovering the job purely by polling the DB.
    daemon_flow = af.AnalysisFlow()
    daemon_flow.start()
    try:
        daemon_flow.join()
    finally:
        daemon_flow.stop()

    payload = json.loads(daemon_flow._conn().execute(
        "SELECT payload_json FROM analysis_results WHERE job_id=?", (job_id,)).fetchone()[0])
    report = payload["report"]
    assert not _has_cjk(report["market_judgment"]), report["market_judgment"]


def test_submit_manual_dedup_window_respects_locale_change(tmp_path, monkeypatch):
    from trustforge.analysis_flow import AnalysisFlow

    monkeypatch.setattr("trustforge.analysis_flow.collect", lambda *a, **k: _flow_docs())
    flow = AnalysisFlow(tmp_path / "flow.sqlite3")
    flow.start()
    try:
        _, job1 = flow.submit_manual("ETH", "risk", "Is it a good time to enter ETH now?",
                                      locale="zh-Hant")
        flow.join()
        row1 = json.loads(flow._conn().execute(
            "SELECT payload_json FROM analysis_results WHERE job_id=?", (job1,)).fetchone()[0])
        assert _has_cjk(row1["report"]["market_judgment"])

        # Same coin/mode/question, well inside the 5-minute manual dedup
        # window, but a *different* locale this time.
        _, job2 = flow.submit_manual("ETH", "risk", "Is it a good time to enter ETH now?",
                                      locale="en")
        flow.join()
        row2 = json.loads(flow._conn().execute(
            "SELECT payload_json FROM analysis_results WHERE job_id=?", (job2,)).fetchone()[0])
        assert not _has_cjk(row2["report"]["market_judgment"]), row2["report"]["market_judgment"]
    finally:
        flow.stop()
