"""W2 truth-discovery 動態來源信譽 — 啟用驗收（gray `docs/archive/plans/PLAN-w2-enable-final.md`）。

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

def _ds_trigger_docs() -> list[Document]:
    """觸發 DS EM 離線 fallback 的合成 docs（#182）。

    4+ 來源、coin=BTC、跨多個 window 且涵蓋 bullish/bearish/neutral 三類方向，
    讓 Dawid-Skene EM 能在「無真 entailment」的離線路徑下，從多源方向標籤的
    統計共識估算每來源可靠度（而非 no-op）。各來源與「多數票共識」的一致率
    不同（A 全對 / B 錯 1 窗 / C 錯 2 窗），預期 DS 可靠度 r(A)>r(B)>r(C)、
    final 隨投票分布變化。

    全合成、禁捏造歷史（#24）：方向由關鍵詞決定，投票分布由本函式控制。
    """
    txt = {
        "bullish": "BTC 上漲突破阻力",
        "bearish": "BTC 下跌跌破支撐",
        "neutral": "BTC 區間盤整觀望",
    }
    true_by_window = {0: "bullish", 1: "bearish", 2: "neutral",
                      3: "bullish", 4: "bearish", 5: "neutral"}
    # 每來源對第 w 窗投出的方向：A 全對；B 第 3 窗錯；C 第 0、1 窗錯。
    vote = {
        "glassnode": lambda w: true_by_window[w],
        "coindesk": lambda w: "bearish" if w == 3 else true_by_window[w],
        "x-analyst": lambda w: ("bearish" if w == 0 else
                                "bullish" if w == 1 else true_by_window[w]),
    }
    kind_of = {"glassnode": "onchain", "coindesk": "news", "x-analyst": "social"}
    docs = []
    for w in range(6):
        for src, fn in vote.items():
            docs.append(_doc(f"{src}-{w}", kind_of[src], src, txt[fn(w)],
                             ts=w * 86400.0))
    return docs


def test_run_agent_pipeline_dynamic_reputation_offline_triggers_ds_em():
    """#182 反轉舊版 no-op 語意：生產路徑預設離線（`client=BedrockClient(offline=True)`）
    且無任何真 `entailment` 流進 W2 時，W2 不再是誠實 no-op，而是觸發 **Dawid-Skene
    EM 離線 fallback**（DS 共識收斂）來估每來源可靠度，並餵進 Step B 混合公式。

    斷言：
    - `reputation_mode == "ds_em"`（trace 標註走 DS 分支，非 entailment 路徑）。
    - `reputation_agree_n > 0`（DS 模式下 agree_n = 該 source 參與的達標 item 數）。
    - `reputation_final` 隨投票分布變化（最一致來源 glassnode 的 final 明顯高於其
      prior；各來源 final 不全相等）。

    ⚠️ 誠實紅線：本測試驗證的是「離線觸發 DS、信譽被 DS 共識調整」，**不**宣稱 DS
    具備預測力或解決 #167 AUC——DS 產出是「多源方向標籤的統計共識信心」，UI 標註
    「DS 共識收斂」。線上有 entailment 佐證時仍走舊路（見其他回歸鎖）。
    """
    docs = _ds_trigger_docs()
    _report, evidence = run_agent_pipeline(
        query="分析 BTC", coin="BTC", qtype=QuestionType.MULTI_SOURCE,
        docs=docs, client=BedrockClient(offline=True),
        log=ExecutionLog(now_fn=lambda: 1.0), now_fn=lambda: 1.0,
        run_scope_id="test-w2-ds-trigger",
    )
    ds_evs = [e for e in evidence if e.reputation_mode == "ds_em"]
    assert ds_evs, "reputation_mode 未接線進 Evidence（W2 未真正啟用 DS EM 分支）"
    for ev in ds_evs:
        # `reputation_mode` 是 `trust_components` 的同層兄弟欄位（字串標註），
        # 不應出現在純數值的 trust_components 內（codex 對抗審 Medium 修正）。
        assert "reputation_mode" not in ev.trust_components, (
            f"{ev.source}：reputation_mode 不應污染 trust_components（須純數值）"
        )
        assert all(isinstance(v, (int, float)) for v in ev.trust_components.values()), (
            f"{ev.source}：trust_components 內存在非數值欄位"
        )
        assert ev.reputation_mode == "ds_em", (
            f"{ev.source}：離線無 entailment 應走 DS EM 分支，實際 mode={ev.reputation_mode}"
        )
        tc = ev.trust_components
        assert tc["reputation_agree_n"] > 0, (
            f"{ev.source}：DS 模式下 agree_n（達標 item 參與數）應 > 0，實際 {tc['reputation_agree_n']}"
        )

    glass = next(e for e in ds_evs if e.source == "glassnode")
    gtc = glass.trust_components
    assert gtc["reputation_final"] > gtc["reputation_prior"], (
        f"最一致來源 glassnode 應被 DS 共識上調：prior={gtc['reputation_prior']} "
        f"final={gtc['reputation_final']}"
    )
    finals = {e.source: e.trust_components["reputation_final"] for e in ds_evs}
    assert len(set(round(v, 4) for v in finals.values())) > 1, (
        f"DS 可靠度應隨投票分布分化（final 不全相等），實際 {finals}"
    )


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


def test_scored_to_evidence_carries_author_from_doc_meta():
    """W3 前置（資料累積，非偵測）：Document.meta["author"]（見 ingestion.
    social/news）原文帶到 Evidence.author，不影響其餘欄位。"""
    doc = Document(
        id="d1", kind="social", source="reddit-bitcoin", text="t", ts=1.0,
        meta={"content_reference": "ref", "author": "/u/crypto_trader_99"},
    )
    claim = Claim(id="c1", text="t", doc=doc)
    sc = ScoredClaim(claim=claim, trust=0.5, components={"reputation": 0.5})
    ev = _scored_to_evidence(sc, related="測試")
    assert ev.author == "/u/crypto_trader_99"


def test_scored_to_evidence_author_empty_when_doc_meta_has_no_author():
    """optional 欄位：無 author 概念的來源（多數 news/onchain/regulatory）
    `doc.meta` 沒有 "author" 鍵時，`Evidence.author` 落到 `None`（codex
    vp-engineering 終審 MEDIUM，PR #107：型別已改 `str | None = None`，
    缺鍵=未知，不再用空字串冒充）。"""
    doc = Document(id="d1", kind="news", source="coindesk", text="t", ts=1.0,
                    meta={"content_reference": "ref"})
    claim = Claim(id="c1", text="t", doc=doc)
    sc = ScoredClaim(claim=claim, trust=0.5, components={"reputation": 0.5})
    ev = _scored_to_evidence(sc, related="測試")
    assert ev.author is None


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
        run_scope_id="test-w2-two-bedrock-events",
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
        run_scope_id="test-w2-small-sample",
    )
    for ev in evidence:
        if "reputation_prior" in ev.trust_components:
            assert ev.trust_components["reputation_prior"] == ev.trust_components["reputation_final"], (
                f"{ev.source}：小樣本（<3 獨立佐證）應強制維持先驗，"
                f"實際 prior={ev.trust_components['reputation_prior']} "
                f"final={ev.trust_components['reputation_final']}"
            )
