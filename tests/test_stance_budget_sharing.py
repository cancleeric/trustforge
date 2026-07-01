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

demo 可靠性 #32 追加 cost-integrity HIGH（第二次對抗審）：codex 複審時指出
`run_agent_pipeline()` 內 Step2 後的收割「之後沒有再 harvest」，擔心 Step2.5
成本漏記、`client.cost_events` 殘留誤記到下一輪別的幣。經查證：`build_report()`
內部**早已**在 Step2.5 `detect_cross_source_signal()` 呼叫後緊接著收割一次
（即上述修復的一部分），非空測——用 `git stash`/暫時移除收割程式碼實測驗證過
（見 `test_run_agent_pipeline_step25_stance_cost_is_harvested_into_ledger` 移除
收割後會真的 FAIL）。這輪仍依 codex 建議做防禦性強化：
  1. 把原本重複貼兩份的 6 行收割樣板抽成共用 helper
     `agent.orchestrator._harvest_stance_cost_events(client, log)`。
  2. `run_agent_pipeline()` 在 `build_report()` 回傳「之後」也額外呼叫一次
     （belt-and-suspenders——正常路徑下永遠是 no-op，因為 `build_report()`
     內部已收割乾淨；防的是未來重構不慎在收割點之後又加了新的 stance 呼叫）。
新增 `test_stance_cost_does_not_leak_across_runs_with_same_client`（同一 client
物件跑 ETH 再跑 BTC，確認 BTC 那輪的帳本不含 ETH 殘留成本）與
`test_step25_offline_no_bedrock_produces_zero_stance_cost_without_crashing`
（offline 時零 stance 成本、不炸）。
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


def test_stance_cost_does_not_leak_across_runs_with_same_client(monkeypatch):
    """[cost-integrity HIGH 驗收，demo 可靠性 #32 追加] codex 對抗審：若
    Step 2.5 的 stance 成本沒被收割乾淨，殘留在 `client.cost_events` 上的舊
    事件會被下一輪 `run_agent_pipeline()`（常見於 comparison 模式兩幣共用
    同一個 client）誤記到別的幣頭上。

    這裡用**同一個** client 物件跑兩輪（模擬 ETH → BTC），兩輪各自觸發一次
    Step 2.5 stance 真呼叫，斷言：
      1. 兩輪各自的 log 都恰好記到「屬於自己這一輪」的 1 筆 llm.cost；
      2. 第二輪的 log 不含第一輪殘留的成本事件（不誤記到別的幣）；
      3. 每輪跑完 `client.cost_events` 都歸零。
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

    docs_eth = _opposite_direction_news_docs()
    docs_btc = [
        Document(id="b1", kind="news", source="theblock",
                  text="BTC 市場 情緒 明顯 看漲，交易員 樂觀 買盤 湧入。", ts=1000.0, meta={}),
        Document(id="b2", kind="news", source="cointelegraph",
                  text="BTC 市場 情緒 轉為 看跌，交易員 悲觀 賣壓 湧現。", ts=1000.0, meta={}),
    ]

    log_eth = ExecutionLog(now_fn=lambda: 1000.0)
    run_agent_pipeline(
        query="分析 ETH", coin="ETH", qtype=QuestionType.MULTI_SOURCE,
        docs=docs_eth, client=client, log=log_eth, now_fn=lambda: 1000.0,
    )
    assert client.cost_events == [], "第一輪（ETH）跑完 cost_events 應歸零"
    eth_cost_events = [e for e in log_eth.events if e["tool"] == "llm.cost"]
    assert len(eth_cost_events) == 1

    log_btc = ExecutionLog(now_fn=lambda: 2000.0)
    run_agent_pipeline(
        query="分析 BTC", coin="BTC", qtype=QuestionType.MULTI_SOURCE,
        docs=docs_btc, client=client, log=log_btc, now_fn=lambda: 2000.0,
    )
    assert client.cost_events == [], "第二輪（BTC）跑完 cost_events 應歸零"
    btc_cost_events = [e for e in log_btc.events if e["tool"] == "llm.cost"]
    assert len(btc_cost_events) == 1, (
        f"BTC 這輪的 log 應恰好有 1 筆屬於自己的 llm.cost，實得 {len(btc_cost_events)} "
        "（若 >1 代表 ETH 那輪的成本殘留、誤記到 BTC 頭上）"
    )
    # log 物件本身互相獨立，直接用物件身份確認 ETH 那筆事件沒有出現在 BTC 的 log 裡。
    assert eth_cost_events[0] is not btc_cost_events[0]


def test_step25_offline_no_bedrock_produces_zero_stance_cost_without_crashing():
    """[cost-integrity HIGH 驗收，demo 可靠性 #32 追加] offline（無 Bedrock）
    情境：`_harvest_stance_cost_events` 面對 `getattr(client, "cost_events", None)`
    可能是 `None`／空列表／甚至假 client 沒有此屬性，都必須安全跳過、不拋例外，
    且不得產生任何「歸因於 stance 呼叫」的 llm.cost 事件（fail-safe，不誤記
    成本；offline 時 stance 走持久化快取/fail-safe 回 neutral，不打真 Bedrock）。

    注意：Step3（narrative 行文）本身會固定記一筆 model="offline"、成本 0 的
    佔位 llm.cost 事件（既有行為，與 stance 無關）——這裡只驗證「沒有額外的
    stance 呼叫成本事件」，不是斷言整個 log 空無一筆 llm.cost。
    """
    log = ExecutionLog(now_fn=lambda: 1000.0)
    client = BedrockClient(offline=True)  # 預設離線，無真 Bedrock
    report, evidence = run_agent_pipeline(
        query="分析 ETH", coin="ETH", qtype=QuestionType.MULTI_SOURCE,
        docs=_opposite_direction_news_docs(),
        client=client, log=log, now_fn=lambda: 1000.0,
    )
    assert client.cost_events == []
    cost_events = [e for e in log.events if e["tool"] == "llm.cost"]
    stance_cost_events = [e for e in cost_events if e["params"]["model"] != "offline"]
    assert stance_cost_events == [], (
        f"offline 情境不應產生任何額外的 stance 呼叫成本，實得 {stance_cost_events}"
    )
