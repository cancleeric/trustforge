"""[HIGH-2 驗收，demo 可靠性 #32 codex 對抗審] 線上 Tier2 stance 預算 + 成本入帳。

背景（修復前的臭蟲）：`agent.orchestrator.build_report()` 在 Step 2.5（跨源
`stance_pairs` 偵測）內部**另建一份**獨立的 online stance_fn，沒有共用
`trust.scoring.score()` 用的 `_StanceBudget`（配對硬上限 + `log.remaining()`
時間守門）。後果：
  1. cache miss 時，Step 2.5 可以無上限直接打 Bedrock，跟 Step2 的預算是
     兩個獨立池子——單次執行「真呼叫 Bedrock 的硬上限」實質變成兩倍。
  2. Step 2.5 發生在 Step2 的 `cost_events` 收割-清空之後，此步驟產生的成本
     永遠沒有機會進 `ExecutionLog`／成本帳本。

修復：抽出 `trust.scoring.build_stance_fn()`，`run_agent_pipeline()` 只建**一份**
共用的 budgeted stance_fn，同時傳給 `score()`（Step2）與 `build_report()`
（Step 2.5），並在 Step 2.5 之後補一段跟 Step2 對稱的 cost_events 收割。

本檔全部用 fake/monkeypatch client，不打真 AWS（比照 `test_stance_w15.py` /
`test_cost_ledger.py` 既有慣例）。
"""
from __future__ import annotations

import trustforge.agent.orchestrator as orch_mod
import trustforge.trust.scoring as scoring_mod
from trustforge.agent.orchestrator import (
    detect_cross_source_signal,
    run_agent_pipeline,
)
from trustforge.bedrock import BedrockClient, BedrockConfig
from trustforge.execlog import ExecutionLog
from trustforge.ingestion.base import Document
from trustforge.ledger import estimate_cost
from trustforge.schema import QuestionType
from trustforge.trust.scoring import Claim, build_stance_fn, score


def _doc(id: str, kind: str, source: str, ts: float = 1000.0) -> Document:
    return Document(id=id, kind=kind, source=source, text="", ts=ts)


def _opposite_direction_news_docs() -> list[Document]:
    """一組低文字重疊（避免觸發 score() 內部 corroboration 的 overlap>=0.4 閘）、
    方向明確相反、來源各異的 news 文件——確定只有 Step 2.5 stance_pairs 偵測會
    對這一對呼叫 stance_fn（已用 `score()` 實測 corroboration=0.0 驗證，見 PR 說明）。
    """
    return [
        Document(id="n1", kind="news", source="coindesk",
                  text="ETH 市場 情緒 明顯 看漲，交易員 樂觀 買盤 湧入。", ts=1000.0, meta={}),
        Document(id="n2", kind="news", source="decrypt",
                  text="ETH 市場 情緒 轉為 看跌，交易員 悲觀 賣壓 湧現。", ts=1000.0, meta={}),
    ]


# ---------------------------------------------------------------------------
# 1) 身份共用：score() 與 Step 2.5 stance_pairs 偵測必須拿到「同一個」stance_fn
# ---------------------------------------------------------------------------

def test_run_agent_pipeline_shares_one_stance_fn_between_score_and_stance_pairs(monkeypatch):
    """整合層驗證實際接線：`run_agent_pipeline()` 只建一份 budgeted stance_fn，
    同時傳給 `score()`（Step2）與 `detect_cross_source_signal()`（Step 2.5），
    而不是兩處各自另建——用 spy 攔截兩處實際收到的 `stance_fn` 引數比對 `is`。
    """
    captured: dict[str, object] = {}

    real_score = scoring_mod.score

    def _spy_score(*args, **kwargs):
        captured["score_stance_fn"] = kwargs.get("stance_fn")
        return real_score(*args, **kwargs)

    real_detect = orch_mod.detect_cross_source_signal

    def _spy_detect(*args, **kwargs):
        captured["detect_stance_fn"] = kwargs.get("stance_fn")
        return real_detect(*args, **kwargs)

    monkeypatch.setattr(scoring_mod, "score", _spy_score)
    monkeypatch.setattr(orch_mod, "detect_cross_source_signal", _spy_detect)

    docs = _opposite_direction_news_docs()
    run_agent_pipeline(
        query="分析 ETH", coin="ETH", qtype=QuestionType.MULTI_SOURCE,
        docs=docs, client=BedrockClient(offline=True),
        log=ExecutionLog(now_fn=lambda: 1000.0), now_fn=lambda: 1000.0,
    )

    assert captured.get("score_stance_fn") is not None, "score() 應收到非 None 的 stance_fn"
    assert captured.get("detect_stance_fn") is not None, (
        "detect_cross_source_signal() 應收到非 None 的 stance_fn"
    )
    assert captured["score_stance_fn"] is captured["detect_stance_fn"], (
        "Step2 score() 與 Step 2.5 stance_pairs 偵測必須共用同一個 stance_fn/"
        "_StanceBudget 實例，不可各自另建一份、讓真呼叫硬上限實質變成兩倍"
    )


# ---------------------------------------------------------------------------
# 2) 行為驗證：共用同一顆預算時，跨兩處呼叫總數仍受硬上限管控（不會被加倍）
# ---------------------------------------------------------------------------

def test_shared_stance_budget_caps_total_calls_across_score_and_stance_pairs_detection():
    """[HIGH-2 驗收] 用「同一個」`build_stance_fn()` 產生的 stance_fn，分別餵給
    `score()`（製造遠超預算的 corroboration 候選對）與 `detect_cross_source_signal()`
    （製造額外的 stance_pairs 候選對）。若兩處真的共用同一顆 `_StanceBudget`，
    合計真呼叫次數必須被同一個上限卡住；若兩處各自另建一份（修復前的臭蟲），
    合計會是「各自的上限相加」，超過這裡設的單一上限。
    """
    calls: list[tuple[str, str]] = []

    class _CountingClient:
        offline = False

        def classify_stance(self, a: str, b: str) -> str:
            calls.append((a, b))
            return "neutral"

    budget = 2
    shared_stance_fn = build_stance_fn(
        stance_client=_CountingClient(), stance_pair_budget=budget,
    )

    # (a) corroboration 候選對：1 個 target + 10 個高重疊、同方向、不同來源的變體，
    # 若無上限單這一輪就會要 10 次真呼叫（比照 test_stance_w15.py 既有手法）。
    target_text = "Traders note steady exchange inflows amid low volatility this week"
    target = Claim(id="t0", text=target_text, doc=_doc("dt", "news", "target-source"))
    other_claims = [
        Claim(
            id=f"o{i}",
            text=f"{target_text} variant number {i}",
            doc=_doc(f"do{i}", "news", f"source-{i}"),
        )
        for i in range(10)
    ]
    score([target, *other_claims], now=1000.0, stance_fn=shared_stance_fn)

    assert len(calls) == budget, (
        f"score() 單獨這輪就應被共用預算 {budget} 卡住，實際呼叫 {len(calls)} 次"
    )

    # (b) 額外的 stance_pairs 候選對（來源/方向都相反，且與 (a) 的文字完全不重疊，
    # 保證是全新的 cache miss 候選，而非命中同一把 key）。此時共用預算應已耗盡，
    # 這裡不該再產生任何新的真呼叫。
    docs = _opposite_direction_news_docs()
    from trustforge.trust.scoring import extract_claims
    extra_claims = extract_claims(docs)
    scored_extra = score(extra_claims, now=1000.0, stance_fn=shared_stance_fn)
    detect_cross_source_signal(scored_extra, stance_fn=shared_stance_fn)

    assert len(calls) == budget, (
        f"共用預算耗盡後，stance_pairs 偵測不應再產生新的真呼叫；"
        f"預算 {budget}，實際合計呼叫 {len(calls)} 次（若各自另建預算，"
        f"這裡會多出額外呼叫，證明沒有真正共用）"
    )


# ---------------------------------------------------------------------------
# 3) 時間預算耗盡 → Step 2.5 stance_pairs 偵測也要 fail-safe 降級為 neutral
# ---------------------------------------------------------------------------

def test_shared_stance_budget_time_exhausted_degrades_stance_pairs_to_neutral():
    """[HIGH-2 驗收] `stance_remaining_time_fn` 回傳耗盡（0 秒，低於
    `STANCE_TIME_RESERVE_SEC`）時，即使配對硬上限還很充裕，`detect_cross_source_signal`
    的 stance_pairs 偵測也必須 fail-safe 降級——不呼叫、不 crash、不錯判矛盾。
    """
    calls: list[tuple[str, str]] = []

    class _CountingClient:
        offline = False

        def classify_stance(self, a: str, b: str) -> str:
            calls.append((a, b))
            return "contradiction"  # 就算真的呼叫到也讓它明確可辨識為矛盾

    shared_stance_fn = build_stance_fn(
        stance_client=_CountingClient(),
        stance_pair_budget=100,
        stance_remaining_time_fn=lambda: 0.0,
    )

    docs = _opposite_direction_news_docs()
    from trustforge.trust.scoring import extract_claims
    claims = extract_claims(docs)
    scored = score(claims, now=1000.0, stance_fn=shared_stance_fn)
    pairs = detect_cross_source_signal(scored, stance_fn=shared_stance_fn)

    assert len(calls) == 0, (
        f"時間預算耗盡時 stance_pairs 偵測不應呼叫 stance_fn，實際呼叫 {len(calls)} 次"
    )
    assert pairs is None or pairs.get("type") != "divergence" or not pairs.get("stance_pairs"), (
        "時間預算耗盡 fail-safe 回 neutral，不可仍判出跨源矛盾 stance_pairs"
    )


# ---------------------------------------------------------------------------
# 4) 成本入帳：Step 2.5 的真呼叫成本必須被收割進 ExecutionLog／帳本
# ---------------------------------------------------------------------------

def test_run_agent_pipeline_step25_stance_cost_is_harvested_into_ledger(monkeypatch):
    """[HIGH-2 驗收] Step 2.5 跨源 stance_pairs 偵測若真的打了 Bedrock（cache
    miss、預算/時間都還夠），其成本必須被收割進 `ExecutionLog`（`llm.cost` 事件），
    且收割後 `client.cost_events` 要清空（避免下一步/下一輪重複計費）。
    """
    config = BedrockConfig(stance_model_id="fake-stance-model")
    client = BedrockClient(config=config, offline=False)

    class _FakeRuntime:
        def converse(self, **kwargs):
            return {
                "output": {"message": {"content": [
                    {"toolUse": {"name": "classify_stance", "input": {"label": "contradiction"}}}
                ]}},
                "usage": {"inputTokens": 42, "outputTokens": 7},
            }

    monkeypatch.setattr(client, "_stance_runtime", lambda: _FakeRuntime())

    # model_id（narrative/claim-extraction 用）刻意不設 → Step1 regex fallback、
    # Step3 `.complete()` 會因無 model_id 內部 raise 被捕捉降級，兩者都不產生成本，
    # 確保這個測試量到的 llm.cost 只可能來自 Step 2.5 的 stance 呼叫。
    docs = _opposite_direction_news_docs()
    log = ExecutionLog(now_fn=lambda: 1000.0)

    report, _evidence = run_agent_pipeline(
        query="分析 ETH", coin="ETH", qtype=QuestionType.MULTI_SOURCE,
        docs=docs, client=client, log=log, now_fn=lambda: 1000.0,
    )

    assert client.cost_events == [], "Step 2.5 收割後 client.cost_events 應清空，避免重複計費"

    cost_events = [e for e in log.events if e["tool"] == "llm.cost"]
    assert len(cost_events) == 1, (
        f"應恰好有 1 筆 Step 2.5 stance 呼叫的 llm.cost 記錄，實際 {len(cost_events)} 筆"
    )
    ev = cost_events[0]["params"]
    assert ev["model"] == "fake-stance-model"
    assert ev["tokens_in"] == 42
    assert ev["tokens_out"] == 7
    assert ev["cost_usd"] == estimate_cost("fake-stance-model", 42, 7)

    # 額外驗證：真的判出跨源矛盾（不是被吞掉了），佐證這條路徑真的有跑到底。
    assert report.cross_source_signal is not None
    assert report.cross_source_signal.get("stance_pairs"), (
        "應偵測到 stance_pairs（fake runtime 回 contradiction）"
    )
