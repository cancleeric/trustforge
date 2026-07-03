"""W2 truth-discovery 動態來源信譽 — 啟用驗收（gray `docs/PLAN-w2-enable-final.md`）。

涵蓋 5 條驗收：
  1. `run_agent_pipeline()` 生產路徑實際以 `dynamic_reputation=True` 呼叫
     `score()`（`orchestrator.py:778`）。
  1b. codex 對抗審 [HIGH，#24] 修正：生產預設離線（`llm_mode=off`）時，
      W2 對信譽是**誠實 no-op**——`agent.orchestrator` 離線走
      `stance_client=None`，`cached_stance_fn` 對任何配對都 fail-safe 回
      `"neutral"`，而 W2 動態信譽只認真 `entailment`（見
      `scoring._corroboration_detail` `require_entailment` 說明），沒有真的
      跑過語意分類就不能宣稱「已驗證佐證」。只有真連上 Bedrock/W1.5 且判定為
      `entailment` 時，信譽才會上升（見下方「真 entailment」「genuine
      neutral」「budget/timeout 耗盡」三組情境測試）。
  2. 可解釋性接線：`_scored_to_evidence` 把 `sc.reputation_trace` 併入
     `Evidence.trust_components`（`reputation_prior/final/agree_n/
     contradict_n/iterations_run`），`web._render_trust_breakdown` 能把它
     渲染成人話 WHY caption。
  3. $0：啟用後不多打真 stance/Bedrock 呼叫（`_reputation_evidence` 與
     `_corroboration` 共用同一顆 `cached_stance_fn`，比照
     `tests/test_stance_budget_sharing.py` 既有手法）。
  4. 小樣本守門（`MIN_INDEPENDENT_EVIDENCE=3`）啟用後仍生效：獨立佐證
     < 3 的來源，動態信譽必須與先驗完全相同。

本檔全部用 offline/fake client，不打真 AWS/Bedrock（比照既有慣例）。
"""
from __future__ import annotations

from trustforge import web
from trustforge.agent.orchestrator import _scored_to_evidence, run_agent_pipeline
from trustforge.bedrock import BedrockClient
from trustforge.execlog import ExecutionLog
from trustforge.ingestion.base import Document
from trustforge.schema import QuestionType
from trustforge.trust.scoring import Claim, ScoredClaim, build_stance_fn, extract_claims, score


def _doc(id: str, kind: str, source: str, text: str, ts: float = 1.0) -> Document:
    return Document(id=id, kind=kind, source=source, text=text, ts=ts)


def _shared_text_docs() -> list[Document]:
    """4 個不同 source、完全相同文本，讓每個 source 的獨立佐證來源數達到
    `MIN_INDEPENDENT_EVIDENCE`(3)，跳脫小樣本守門、真正觸發動態信譽調整
    （沿用 `tests/test_trust_scoring.py::_shared_text_docs` 同一手法）。
    """
    shared = "大額 機構 資金 布局 現貨 ETF 通過 推升 市場 信心"
    return [
        _doc("w2-a", "onchain", "glassnode", shared),
        _doc("w2-b", "news", "coindesk", shared),
        _doc("w2-c", "regulatory", "sec-filing", shared),
        _doc("w2-d", "social", "x-analyst", shared),
    ]


def _small_sample_docs() -> list[Document]:
    """只有 2 個獨立來源互相佐證（< 3）→ 小樣本守門應強制維持先驗。"""
    shared = "大額 BTC 轉入 交易所 造成 賣壓 比特幣 下跌"
    return [
        _doc("sg-a", "onchain", "glassnode", shared),
        _doc("sg-b", "news", "coindesk", shared),
    ]


# ---------------------------------------------------------------------------
# 1) 生產路徑真的啟用了 dynamic_reputation=True
# ---------------------------------------------------------------------------

def test_run_agent_pipeline_dynamic_reputation_offline_is_honest_noop():
    """codex 對抗審 [HIGH，#24] 修正後反轉：`run_agent_pipeline()`（生產唯一
    呼叫點、預設離線 `client=BedrockClient(offline=True)`）啟用 W2 後，離線
    模式下 `stance_client=None`，任何配對都 fail-safe 回 `"neutral"`——W2
    只認真 `entailment`，沒有真的跑過語意分類，信譽**必須維持先驗**，
    `reputation_agree_n`/`reputation_contradict_n` 皆為 0。

    這是 codex 抓到的 HIGH：舊版（修正前）把離線 fail-safe neutral 誤當
    agreement，讓沒有真驗證的離線 demo 也顯示信譽上升，虛增信任訊號，違反
    #24。此測試取代舊版同名測試（原本斷言離線信譽會上升，依賴該 bug 行為）。
    """
    docs = _shared_text_docs()
    report, evidence = run_agent_pipeline(
        query="分析 BTC", coin="BTC", qtype=QuestionType.MULTI_SOURCE,
        docs=docs, client=BedrockClient(offline=True),
        log=ExecutionLog(now_fn=lambda: 1.0), now_fn=lambda: 1.0,
    )
    social_ev = next(e for e in evidence if e.source == "x-analyst")
    assert "reputation_prior" in social_ev.trust_components, (
        "reputation_trace 未接線進 Evidence.trust_components——W2 未真正啟用"
    )
    assert social_ev.trust_components["reputation_final"] == social_ev.trust_components["reputation_prior"], (
        "離線模式沒有真的跑過語意分類（全部 fail-safe neutral），"
        "x-analyst 動態信譽必須誠實維持先驗，不可虛增"
    )
    assert social_ev.trust_components["reputation_agree_n"] == 0, (
        "離線 fail-safe neutral 不可被當成 agreement 計入 agree_n"
    )
    assert social_ev.trust_components["reputation_contradict_n"] == 0


def test_dynamic_reputation_rises_only_with_genuine_entailment_client():
    """[codex 對抗審 HIGH，#24 降級情境 1] 真的接上一個「線上、且明確判定為
    entailment」的假 client（模擬真連上 Bedrock/W1.5 語意分類成功）：這種
    情況下 W2 才應該讓信譽上升——證明修正後 W2 並非永遠 no-op，只是要求
    真驗證。
    """
    docs = _shared_text_docs()
    claims = extract_claims(docs)

    class _EntailmentClient:
        offline = False

        def classify_stance(self, a: str, b: str) -> str:
            return "entailment"

    stance_fn = build_stance_fn(stance_client=_EntailmentClient(), stance_pair_budget=20)
    scored = score(claims, now=1.0, stance_fn=stance_fn, dynamic_reputation=True)
    social_sc = next(sc for sc in scored if sc.claim.doc.source == "x-analyst")
    assert social_sc.reputation_trace["agree_n"] == 3
    assert social_sc.reputation_trace["contradict_n"] == 0
    assert social_sc.reputation_trace["final"] > social_sc.reputation_trace["prior"], (
        "真的跑過語意分類且判定為 entailment 時，信譽應正常上升"
    )


def test_dynamic_reputation_genuine_neutral_stance_stays_at_prior():
    """[codex 對抗審 HIGH，#24 降級情境 2] 線上 client 真的有跑，但明確判定為
    `"neutral"`（真中立，非 fail-safe）：W2 仍不應計入 agreement——`"neutral"`
    無論是真中立還是 fail-safe 降級，回傳值本身無法區分，一律保守不採信，
    信譽維持先驗。
    """
    docs = _shared_text_docs()
    claims = extract_claims(docs)

    class _NeutralClient:
        offline = False

        def classify_stance(self, a: str, b: str) -> str:
            return "neutral"

    stance_fn = build_stance_fn(stance_client=_NeutralClient(), stance_pair_budget=20)
    scored = score(claims, now=1.0, stance_fn=stance_fn, dynamic_reputation=True)
    social_sc = next(sc for sc in scored if sc.claim.doc.source == "x-analyst")
    assert social_sc.reputation_trace["agree_n"] == 0
    assert social_sc.reputation_trace["contradict_n"] == 0
    assert social_sc.reputation_trace["final"] == social_sc.reputation_trace["prior"], (
        "明確 neutral（真中立）不可被當 agreement，信譽應維持先驗"
    )


def test_dynamic_reputation_budget_exhausted_stays_at_prior():
    """[codex 對抗審 HIGH，#24 降級情境 3] 時間預算耗盡（`stance_remaining_time_fn`
    回傳 0 秒，低於 `STANCE_TIME_RESERVE_SEC`）：即使配對硬上限還很充裕，也必須
    fail-safe 降級成 neutral、完全不呼叫真 client（$0 保證），且信譽維持先驗
    （不可把「沒空間驗證」誤當「已驗證」）。
    """
    docs = _shared_text_docs()
    claims = extract_claims(docs)
    calls: list[tuple[str, str]] = []

    class _EntailmentClient:
        offline = False

        def classify_stance(self, a: str, b: str) -> str:
            calls.append((a, b))
            return "entailment"  # 就算真的呼叫到也讓它明確可辨識為佐證

    stance_fn = build_stance_fn(
        stance_client=_EntailmentClient(),
        stance_pair_budget=100,
        stance_remaining_time_fn=lambda: 0.0,
    )
    scored = score(claims, now=1.0, stance_fn=stance_fn, dynamic_reputation=True)
    social_sc = next(sc for sc in scored if sc.claim.doc.source == "x-analyst")
    assert len(calls) == 0, (
        f"時間預算耗盡時不應呼叫真 client（$0 保證），實際呼叫 {len(calls)} 次"
    )
    assert social_sc.reputation_trace["agree_n"] == 0
    assert social_sc.reputation_trace["final"] == social_sc.reputation_trace["prior"], (
        "budget/timeout 耗盡 fail-safe 降級為 neutral，信譽應維持先驗，不可誤判為已驗證"
    )


# ---------------------------------------------------------------------------
# 2) 可解釋性接線：Evidence.trust_components + web WHY caption
# ---------------------------------------------------------------------------

def test_scored_to_evidence_includes_reputation_trace_when_present():
    doc = Document(id="d1", kind="social", source="x-analyst", text="t", ts=1.0)
    claim = Claim(id="c1", text="t", doc=doc)
    sc = ScoredClaim(
        claim=claim, trust=0.5, components={"reputation": 0.5, "corroboration": 0.5,
                                            "recency": 1.0, "manipulation": 0.0},
        reputation_trace={"source": "x-analyst", "prior": 0.35, "final": 0.59,
                           "agree_n": 3, "contradict_n": 0, "iterations_run": 2},
    )
    ev = _scored_to_evidence(sc, related="測試")
    assert ev.trust_components["reputation_prior"] == 0.35
    assert ev.trust_components["reputation_final"] == 0.59
    assert ev.trust_components["reputation_agree_n"] == 3
    assert ev.trust_components["reputation_contradict_n"] == 0
    assert ev.trust_components["reputation_iterations_run"] == 2
    # 既有分項不受影響
    assert ev.trust_components["reputation"] == 0.5


def test_scored_to_evidence_omits_reputation_trace_when_absent():
    """`dynamic_reputation=False`（reputation_trace=None）逐字向後相容：
    `trust_components` 不應被硬塞任何 reputation_* key。"""
    doc = Document(id="d1", kind="news", source="coindesk", text="t", ts=1.0)
    claim = Claim(id="c1", text="t", doc=doc)
    sc = ScoredClaim(
        claim=claim, trust=0.5, components={"reputation": 0.65, "corroboration": 0.0,
                                            "recency": 1.0, "manipulation": 0.0},
        reputation_trace=None,
    )
    ev = _scored_to_evidence(sc, related="測試")
    assert not any(k.startswith("reputation_") for k in ev.trust_components), (
        f"reputation_trace=None 時不應出現 reputation_* key：{ev.trust_components}"
    )


def test_render_trust_breakdown_shows_reputation_trace_change():
    tc = {"reputation": 0.59, "corroboration": 0.5, "recency": 0.8, "manipulation": 0.0,
          "reputation_prior": 0.35, "reputation_final": 0.59,
          "reputation_agree_n": 3, "reputation_contradict_n": 0,
          "reputation_iterations_run": 2}
    out = web._render_trust_breakdown(tc, trust=0.7)
    assert "動態信譽" in out
    assert "0.35" in out and "0.59" in out
    assert "3 源互證" in out


def test_render_trust_breakdown_shows_reputation_trace_unchanged_small_sample():
    tc = {"reputation": 0.65, "corroboration": 0.0, "recency": 0.8, "manipulation": 0.0,
          "reputation_prior": 0.65, "reputation_final": 0.65,
          "reputation_agree_n": 1, "reputation_contradict_n": 0,
          "reputation_iterations_run": 1}
    out = web._render_trust_breakdown(tc, trust=0.7)
    assert "動態信譽" in out
    assert "樣本不足" in out, "prior==final 時應誠實標註『維持先驗』，避免被誤判為接線失敗"


def test_render_trust_breakdown_no_reputation_trace_line_when_absent():
    """`dynamic_reputation=False`／舊資料（無 reputation_prior/final）時，不應
    出現動態信譽這一行——優雅略過，不誤導成『已啟用但無變化』。"""
    tc = {"reputation": 0.65, "corroboration": 0.5, "recency": 0.8, "manipulation": 0.0}
    out = web._render_trust_breakdown(tc, trust=0.7)
    assert "動態信譽" not in out


# ---------------------------------------------------------------------------
# 3) $0：啟用後不多打真 stance 呼叫（比照 test_stance_budget_sharing 手法）
# ---------------------------------------------------------------------------

def test_dynamic_reputation_enable_does_not_increase_real_stance_calls():
    """同一組 claims、同一顆 budget，`dynamic_reputation=False` vs `True`
    的真實（cache-miss）stance 呼叫次數必須相等——`_reputation_evidence`
    與 `_corroboration` 共用同一個 `cached_stance_fn`，K 輪迭代/trace 建構
    不應是「多打一次 Bedrock」。
    """
    docs = _shared_text_docs()
    claims = extract_claims(docs)
    budget = 10

    class _CountingClient:
        offline = False

        def __init__(self):
            self.calls: list[tuple[str, str]] = []

        def classify_stance(self, a: str, b: str) -> str:
            self.calls.append((a, b))
            return "neutral"

    client_off = _CountingClient()
    stance_fn_off = build_stance_fn(stance_client=client_off, stance_pair_budget=budget)
    score(claims, now=1.0, stance_fn=stance_fn_off, dynamic_reputation=False)

    client_on = _CountingClient()
    stance_fn_on = build_stance_fn(stance_client=client_on, stance_pair_budget=budget)
    score(claims, now=1.0, stance_fn=stance_fn_on, dynamic_reputation=True)

    assert len(client_on.calls) == len(client_off.calls), (
        f"啟用 dynamic_reputation 後真實 stance 呼叫次數變了："
        f"off={len(client_off.calls)} on={len(client_on.calls)}（應相等，$0 邊際成本）"
    )
    assert len(client_off.calls) > 0, "測試前提：至少要觸發過一次真呼叫，否則驗證沒有意義"


def test_run_agent_pipeline_bedrock_call_count_unaffected_by_dynamic_reputation():
    """整合層再驗一次：`run_agent_pipeline()`（W2 已預設開）產生的
    `bedrock.complete` 事件數，跟直接呼叫 `score(dynamic_reputation=False)`
    模擬『假設沒接線』的舊路徑一致（Step1/Step3 各一筆，不因 W2 多一筆）。
    """
    docs = _shared_text_docs()
    log = ExecutionLog(now_fn=lambda: 1.0)
    run_agent_pipeline(
        query="分析 BTC", coin="BTC", qtype=QuestionType.MULTI_SOURCE,
        docs=docs, client=BedrockClient(offline=True), log=log, now_fn=lambda: 1.0,
    )
    bedrock_events = [e for e in log.events if e.get("tool") == "bedrock.complete"]
    assert len(bedrock_events) == 2, (
        f"offline pipeline 應仍是 Step1+Step3 兩筆 bedrock.complete（regex fallback），"
        f"W2 啟用不應新增任何一筆：實際 {len(bedrock_events)}"
    )


# ---------------------------------------------------------------------------
# 4) 小樣本守門啟用後仍生效
# ---------------------------------------------------------------------------

def test_small_sample_gate_still_active_via_production_pipeline():
    """獨立佐證 < 3 時，即使生產路徑已啟用 `dynamic_reputation=True`，
    該來源信譽仍須與先驗完全相同（不可暴走/不可因啟用而鬆綁守門）。
    """
    docs = _small_sample_docs()
    _report, evidence = run_agent_pipeline(
        query="分析 BTC", coin="BTC", qtype=QuestionType.MULTI_SOURCE,
        docs=docs, client=BedrockClient(offline=True),
        log=ExecutionLog(now_fn=lambda: 1.0), now_fn=lambda: 1.0,
    )
    for ev in evidence:
        if "reputation_prior" in ev.trust_components:
            assert ev.trust_components["reputation_prior"] == ev.trust_components["reputation_final"], (
                f"{ev.source}：小樣本（<3 獨立佐證）應強制維持先驗，"
                f"實際 prior={ev.trust_components['reputation_prior']} "
                f"final={ev.trust_components['reputation_final']}"
            )
