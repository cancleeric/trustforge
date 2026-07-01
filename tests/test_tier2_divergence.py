"""Tier2（跨源 stance_pairs 背離偵測機制）驗收測試。

CEO 派工規格（原始）：
  - ETH multi_source 分析 → cross_source_signal.type == "divergence"（或含「背離」）
    且含 stance_pairs 兩筆方向相反。
  - `stance_fn=None`（未提供）時 detect_cross_source_signal 行為逐字不變
    （回歸鎖，見 test_cross_source_signal.py 既有 T1-T8）。
  - ETH 既有確定性測試（evidence/facts 數量）同步鎖定，避免未來改動悄悄回歸。

老闆校準（demo 可靠性 #32 第五輪，最終定案）：**這是要上線賣的真產品，不是
demo**——`demo/sample_data/news.json` 裡強塞「ETH ETF 資金流分歧」樣本來觸發
stance_pairs 機制，即使內容改成不可證偽的定性描述、來源改成「示範來源·XXX」
合成標籤，本質上仍是「為了展示功能而在產品資料裡塞假資料」，不可接受。

**已移除**：`demo/sample_data/news.json` 的 `news-eth-etf-inflow`/
`news-eth-etf-outflow` 兩筆樣本、`demo/sample_data/stance_cache.json` 對應的
contradiction 快取條目。ETH 一般 demo 分析現在誠實反映真實（示範）資料——
沒有真的跨源矛盾就回 `None`，不強行製造背離。

**保留**（都是真產品邏輯，不因移除假樣本而受影響）：
  - `_detect_stance_pairs` / `detect_cross_source_signal` 的 stance_pairs 分支
    （純演算法，本檔下方單元測試用合成 fixture 直接驗證邏輯正確性）。
  - `aggregate(coin=...)` coin-filter 主導修正——讓「明確提及該幣」的主張不受
    query 文字措辭影響去留；對真實資料一樣有效，用真實 demo 資料驗證查詢
    措辭不影響結果（見下方 stability 測試，改為驗證「結果穩定」而非「必觸發
    背離」）。
  - `build_stance_fn` 共用預算 + 成本入帳（見 `tests/test_stance_budget_sharing.py`）。
  - `cross_source_signal.stance_pairs` 欄位機制本身——用**注入的合成矛盾
    claim**（非 demo 檔案）驗證端到端正確觸發（見
    `test_stance_pairs_mechanism_triggers_divergence_with_injected_synthetic_claims`），
    比照 `test_stance_budget_sharing.py` 既有慣例：測試裡的合成資料是測試替身，
    不是塞進產品 demo 資料庫的假樣本，兩者性質不同。
  - 其他幣（BTC/SOL/BNB/XRP）不得誤觸假背離（維持既有測試）。
"""
from __future__ import annotations

import pytest

from trustforge.agent.orchestrator import (
    _STANCE_PAIR_MIN_TRUST,
    _detect_stance_pairs,
    build_report,
    detect_cross_source_signal,
    run_agent_pipeline,
)
from trustforge.bedrock import BedrockClient, BedrockConfig
from trustforge.execlog import ExecutionLog
from trustforge.ingestion.base import Document
from trustforge.pipeline import run
from trustforge.schema import QuestionType
from trustforge.trust.scoring import Claim, ScoredClaim, aggregate


# ---------------------------------------------------------------------------
# 輔助工廠（同 test_cross_source_signal.py 慣例）
# ---------------------------------------------------------------------------

def _doc(id_: str, kind: str, source: str) -> Document:
    return Document(id=id_, kind=kind, source=source, text="", ts=1.0)


def _sc(id_: str, kind: str, source: str, direction: str, trust: float, text: str = "") -> ScoredClaim:
    doc = _doc(id_, kind, source)
    claim = Claim(id=id_, text=text or f"claim-{id_}", doc=doc, direction=direction)
    return ScoredClaim(claim=claim, trust=trust)


def _contradiction_stance_fn(a: str, b: str) -> str:
    """測試用假 stance_fn：固定回 contradiction（模擬快取命中矛盾）。"""
    return "contradiction"


def _neutral_stance_fn(a: str, b: str) -> str:
    return "neutral"


# ---------------------------------------------------------------------------
# 單元測試：_detect_stance_pairs / detect_cross_source_signal 的 stance_pairs 分支
# ---------------------------------------------------------------------------

def test_stance_fn_none_keeps_existing_behavior_unchanged():
    """stance_fn 未提供（預設 None）→ 逐字沿用既有行為，not 新增 stance_pairs。"""
    scored = [
        _sc("obj1", "onchain", "glassnode", "bullish", 0.80),
        _sc("obj2", "price", "binance", "bullish", 0.75),
        _sc("sen1", "news", "coindesk", "bearish", 0.65),
        _sc("sen2", "social", "twitter-a", "bearish", 0.55),
    ]
    result = detect_cross_source_signal(scored)
    assert result is not None
    assert "stance_pairs" not in result, "stance_fn=None 時不應出現 stance_pairs key"


def test_detect_stance_pairs_empty_without_stance_fn():
    """_detect_stance_pairs 在 stance_fn=None 時直接回空 list。"""
    scored = [
        _sc("a", "news", "coindesk", "bullish", 0.44, "同議題方向相反 A"),
        _sc("b", "news", "decrypt", "bearish", 0.44, "同議題方向相反 B"),
    ]
    assert _detect_stance_pairs(scored, None) == []


def test_detect_stance_pairs_finds_contradiction_pair():
    """不同來源 + 方向相反 + stance_fn 判定 contradiction → 配對成立。"""
    scored = [
        _sc("a", "news", "coindesk", "bullish", 0.44, "ETF 淨流入方向 A"),
        _sc("b", "news", "decrypt", "bearish", 0.44, "ETF 淨流出方向 B"),
    ]
    pairs = _detect_stance_pairs(scored, _contradiction_stance_fn)
    assert len(pairs) == 2
    sources = {p["source"] for p in pairs}
    assert sources == {"coindesk", "decrypt"}
    stances = {p["stance"] for p in pairs}
    assert stances == {"bullish", "bearish"}
    for p in pairs:
        assert set(p.keys()) == {"source", "stance", "claim_id", "text"}


def test_detect_stance_pairs_below_min_trust_excluded():
    """trust 低於 _STANCE_PAIR_MIN_TRUST 的主張不進入掃描池。"""
    scored = [
        _sc("a", "news", "coindesk", "bullish", _STANCE_PAIR_MIN_TRUST - 0.01, "A"),
        _sc("b", "news", "decrypt", "bearish", 0.9, "B"),
    ]
    assert _detect_stance_pairs(scored, _contradiction_stance_fn) == []


def test_detect_stance_pairs_same_source_excluded():
    """同來源不算跨源矛盾，即使方向相反 + stance_fn 判 contradiction。"""
    scored = [
        _sc("a", "news", "coindesk", "bullish", 0.6, "A"),
        _sc("b", "news", "coindesk", "bearish", 0.6, "B"),
    ]
    assert _detect_stance_pairs(scored, _contradiction_stance_fn) == []


def test_detect_stance_pairs_same_direction_excluded():
    """方向相同不算矛盾（即使 stance_fn 誤判也不觸發，方向閘先擋）。"""
    scored = [
        _sc("a", "news", "coindesk", "bullish", 0.6, "A"),
        _sc("b", "news", "decrypt", "bullish", 0.6, "B"),
    ]
    assert _detect_stance_pairs(scored, _contradiction_stance_fn) == []


def test_detect_stance_pairs_neutral_stance_fn_excluded():
    """stance_fn 回 neutral（非 contradiction）→ 不成立配對。"""
    scored = [
        _sc("a", "news", "coindesk", "bullish", 0.6, "A"),
        _sc("b", "news", "decrypt", "bearish", 0.6, "B"),
    ]
    assert _detect_stance_pairs(scored, _neutral_stance_fn) == []


def test_cross_source_signal_stance_pairs_fallback_when_aggregate_inconclusive():
    """聚合層級（客觀/情緒）判不出結論（此例情緒類 0 筆）時，
    仍可靠 stance_pairs 備援產出 divergence 訊號（覆蓋 T3 的 None 分支）。
    """
    scored = [
        _sc("obj1", "onchain", "glassnode", "bullish", 0.80, "客觀 A"),
        _sc("sen1", "news", "coindesk", "bullish", 0.44, "ETF 淨流入 A"),
        _sc("sen2", "news", "decrypt", "bearish", 0.44, "ETF 淨流出 B"),
    ]
    # sen1/sen2 trust < 0.5 → 聚合層級「情緒類 0 筆（trust>=0.5）」→ 原行為回 None
    baseline = detect_cross_source_signal(scored)
    assert baseline is None, "回歸鎖：不給 stance_fn 時，此 fixture 應仍回 None"

    result = detect_cross_source_signal(scored, stance_fn=_contradiction_stance_fn)
    assert result is not None
    assert result["type"] == "divergence"
    assert "背離" in result["summary"]
    assert len(result["stance_pairs"]) == 2
    assert {p["stance"] for p in result["stance_pairs"]} == {"bullish", "bearish"}
    forbidden = ("買", "賣", "進場", "出場")
    for word in forbidden:
        assert word not in result["summary"], f"summary 嚴禁決策字眼「{word}」"


def test_cross_source_signal_merges_stance_pairs_into_aggregate_result():
    """聚合層級已能判定 divergence 時，stance_pairs（若有）以選填 key 附加，
    不覆蓋既有 objective_direction/sentiment_direction/summary 語意。
    """
    scored = [
        _sc("obj1", "onchain", "glassnode", "bullish", 0.80, "客觀 A"),
        _sc("obj2", "price", "binance", "bullish", 0.75, "客觀 B"),
        _sc("sen1", "news", "coindesk", "bearish", 0.70, "ETF 淨流出 A"),
        _sc("sen2", "social", "twitter-a", "bullish", 0.50, "ETF 淨流入 B"),
    ]
    result = detect_cross_source_signal(scored, stance_fn=_contradiction_stance_fn)
    assert result["type"] == "divergence"
    assert result["objective_direction"] == "bullish"
    assert result["sentiment_direction"] == "bearish"
    assert "stance_pairs" in result
    assert len(result["stance_pairs"]) == 2


# ---------------------------------------------------------------------------
# 機制端到端測試：用「注入的合成矛盾 claim」（非 demo 檔案）驗證
# stance_pairs 偵測 + run_agent_pipeline 完整接線正確運作。
#
# 與舊版（已移除）的差異：舊版靠 demo/sample_data/news.json 裡強塞的「ETH ETF
# 分歧」樣本觸發，即使內容/來源都改成不可證偽的合成標籤，本質仍是「產品資料
# 裡塞假資料」（老闆校準：真產品不是 demo，禁止）。這裡改用測試檔案內部建構
# 的合成 Document（比照 tests/test_stance_budget_sharing.py 既有慣例），純粹
# 是測試替身，不落地進 demo/sample_data/，不影響任何真實使用者看到的資料。
# ---------------------------------------------------------------------------

def test_stance_pairs_mechanism_triggers_divergence_with_injected_synthetic_claims(monkeypatch):
    """端到端驗證：兩則不同來源、方向明確相反、語意矛盾的合成 claim，經
    `run_agent_pipeline()` 完整跑過 Step1~Step2.5，應正確在
    `report.cross_source_signal` 產出 divergence + 2 筆 stance_pairs。

    用 `BedrockClient(offline=False)` + monkeypatch `_stance_runtime()` 模擬
    「語意分類器判定矛盾」（比照 test_cost_ledger.py／test_stance_budget_sharing.py
    既有作法），不打真 AWS。
    """
    docs = [
        Document(id="syn-n1", kind="news", source="test-source-institutional",
                  text="ETH 市場 情緒 明顯 看漲，交易員 樂觀 買盤 湧入。", ts=1000.0, meta={}),
        Document(id="syn-n2", kind="news", source="test-source-cautious",
                  text="ETH 市場 情緒 轉為 看跌，交易員 悲觀 賣壓 湧現。", ts=1000.0, meta={}),
    ]

    config = BedrockConfig(stance_model_id="fake-stance-model")
    client = BedrockClient(config=config, offline=False)

    class _FakeRuntime:
        def converse(self, **kwargs):
            return {
                "output": {"message": {"content": [
                    {"toolUse": {"name": "classify_stance", "input": {"label": "contradiction"}}}
                ]}},
                "usage": {"inputTokens": 10, "outputTokens": 2},
            }

    monkeypatch.setattr(client, "_stance_runtime", lambda: _FakeRuntime())

    report, evidence = run_agent_pipeline(
        query="分析 ETH", coin="ETH", qtype=QuestionType.MULTI_SOURCE,
        docs=docs, client=client, log=ExecutionLog(now_fn=lambda: 1000.0),
        now_fn=lambda: 1000.0,
    )

    sig = report.cross_source_signal
    assert sig is not None, "注入合成矛盾 claim 後應偵測到跨源訊號"
    assert sig["type"] == "divergence" or "背離" in sig["summary"]

    assert "stance_pairs" in sig
    pairs = sig["stance_pairs"]
    assert len(pairs) == 2
    stances = {p["stance"] for p in pairs}
    assert stances == {"bullish", "bearish"}, f"應為方向相反兩筆，實得 {stances}"
    sources = {p["source"] for p in pairs}
    assert sources == {"test-source-institutional", "test-source-cautious"}

    # 守 HOYA 不代客決策：summary 不得含決策字眼
    for word in ("買", "賣", "進場", "出場"):
        assert word not in sig["summary"]


def test_build_report_stance_pairs_survive_aggregate_truncation_overflow():
    """[HIGH 回歸] codex 抓出：`aggregate()` 把 `supporting[:10]`/`contrarian[:5]`
    截斷後才交給 `build_report()`；若 `build_report()` 改用截斷後的
    `brief.supporting + brief.contrarian` 做跨源訊號偵測，真資料上兩則信任分
    皆 <0.5（因而落入 contrarian）的真矛盾配對，只要同時存在 >=5 筆信任分更高
    的其他 contrarian 主張，就會被 `[:5]` 截斷擠出去、偵測不到——且是否命中純
    看資料量與分數分布，不可預期、不可重現。

    這裡刻意構造「overflow」情境：5 筆信任分更高（0.45~0.49，皆 <0.5）的填充
    contrarian 主張，把目標矛盾配對（trust 0.38/0.40）擠到 contrarian 的第
    6、7 名，確認：
      1. 截斷後的 `brief.contrarian` 確實已不含這組矛盾配對（證明 overflow
         情境真的成立，不是測了個假案例）；
      2. `build_report(..., scored=<完整未截斷全集>)` 仍能正確偵測到
         divergence + 2 筆 stance_pairs（證明修法生效：改用完整 scored，不
         再依賴 brief 截斷後的 supporting/contrarian）。
    """
    pair_a = _sc("pair-a", "news", "src-a", "bullish", 0.40, "看漲敘述 A")
    pair_b = _sc("pair-b", "news", "src-b", "bearish", 0.38, "看跌敘述 B")
    fillers = [
        _sc(f"filler-{i}", "news", f"filler-src-{i}", "neutral", trust, f"填充主張 {i}")
        for i, trust in enumerate([0.49, 0.48, 0.47, 0.46, 0.45], start=1)
    ]
    scored = [*fillers, pair_a, pair_b]

    brief = aggregate(scored, query="分析 ETH", coin="ETH")

    # 前提檢查：矛盾配對確實已被截斷擠出 brief.contrarian。
    contrarian_ids = {sc.claim.id for sc in brief.contrarian}
    assert not ({"pair-a", "pair-b"} & contrarian_ids), (
        f"測試前提失敗：overflow 情境未成立，矛盾配對仍在截斷後的 contrarian "
        f"（{contrarian_ids}），請調整填充主張的信任分"
    )
    assert len(brief.contrarian) == 5  # 截斷確實生效

    report, evidence = build_report(
        query="分析 ETH", coin="ETH", qtype=QuestionType.MULTI_SOURCE,
        brief=brief,
        client=BedrockClient(offline=True),
        log=ExecutionLog(now_fn=lambda: 1000.0),
        now_fn=lambda: 1000.0,
        stance_fn=_contradiction_stance_fn,
        scored=scored,  # 修法核心：傳完整未截斷全集，不是 brief 截斷後的結果
    )

    sig = report.cross_source_signal
    assert sig is not None, "矛盾配對雖被 aggregate() 截斷擠出 brief，仍應被完整 scored 偵測到"
    assert "stance_pairs" in sig
    pairs = sig["stance_pairs"]
    claim_ids = {p["claim_id"] for p in pairs}
    assert claim_ids == {"pair-a", "pair-b"}, f"應偵測到被截斷的矛盾配對，實得 {claim_ids}"


def test_build_report_without_scored_falls_back_to_brief_supporting_contrarian():
    """向後相容鎖：既有測試/呼叫端若不傳 `scored`（例如直接呼叫
    `build_report(..., brief=brief)`），行為逐字不變——退回用
    `brief.supporting + brief.contrarian` 做跨源偵測（截斷後的結果）。
    """
    supporting = [_sc("s1", "onchain", "glassnode", "bullish", 0.80)]
    contrarian = [
        _sc("pair-a", "news", "src-a", "bullish", 0.40, "看漲敘述 A"),
        _sc("pair-b", "news", "src-b", "bearish", 0.38, "看跌敘述 B"),
    ]
    from trustforge.trust.scoring import TrustedBrief
    brief = TrustedBrief(query="分析 ETH", supporting=supporting, contrarian=contrarian, confidence=0.8)

    report, evidence = build_report(
        query="分析 ETH", coin="ETH", qtype=QuestionType.MULTI_SOURCE,
        brief=brief,
        client=BedrockClient(offline=True),
        log=ExecutionLog(now_fn=lambda: 1000.0),
        now_fn=lambda: 1000.0,
        stance_fn=_contradiction_stance_fn,
        # 不傳 scored → 應退回 brief.supporting + brief.contrarian
    )

    sig = report.cross_source_signal
    assert sig is not None
    pairs = sig.get("stance_pairs", [])
    claim_ids = {p["claim_id"] for p in pairs}
    assert claim_ids == {"pair-a", "pair-b"}


def test_eth_multi_source_evidence_facts_count_pinned():
    """ETH 既有確定性測試（回歸鎖，避免未來改動悄悄改變證據/事實輸出規模）。

    老闆校準（demo 可靠性 #32 第五輪）：移除強塞的 ETF 分歧樣本後，數量改回
    移除後的真實觀測值（本測試不再假設任何特定分歧樣本存在）。
    """
    report, evidence, log = run("ETH", "ETH 現況", QuestionType.MULTI_SOURCE, offline=True)

    assert len(report.facts) == 7
    assert len(evidence) == 13
    # 誠實反映真實（示範）資料：目前 demo 資料集裡 ETH 沒有真實跨源矛盾樣本，
    # 不應再出現任何背離訊號（無背離就是無，不強行製造）。
    assert report.cross_source_signal is None


# ---------------------------------------------------------------------------
# demo 可靠性 #32 追加：跨源背離不得依查詢字串措辭而定
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "query",
    [
        "ETH 現況",           # 原本已可（有空格、英文）
        "分析 ETH 市場",
        "ETH staking",
        "ETH ETF 資金流",
        "評估 ETH 是否值得買進",
        "以太坊分析",           # CEO 回報：中文幣名、無 ETH token → 原本 None
        "ETH現況",             # CEO 回報：無空格 → 原本 None
        "分析以太坊",
        "分析ETH市場",         # 無空格中英混排
        "以太坊",              # 純中文幣名、無任何動詞
        "ETH",                # 純幣代碼
    ],
)
def test_eth_analysis_stable_across_query_wording(query: str):
    """coin-filter 主導修正：ETH 分析結果（facts/evidence 數量、跨源訊號）不得
    因查詢字串措辭（中/英文、有無空格）而忽多忽少、忽有忽無——只要 coin=ETH，
    無論怎麼問，結果必須一致。

    老闆校準（demo 可靠性 #32 第五輪）：不再斷言「必觸發背離」——demo 資料集
    裡目前沒有真實跨源矛盾樣本，誠實的結果是 `cross_source_signal is None`；
    這裡驗證的是「無論問法為何，這個誠實結果都穩定一致」，而非靠強塞樣本製造
    背離。stance_pairs 機制本身的正確性見上方
    `test_stance_pairs_mechanism_triggers_divergence_with_injected_synthetic_claims`
    （用注入的合成矛盾 claim 驗證）。
    """
    report, evidence, log = run("ETH", query, QuestionType.MULTI_SOURCE, offline=True)
    assert len(report.facts) == 7, f"query={query!r} facts 數應穩定為 7，實得 {len(report.facts)}"
    assert len(evidence) == 13, f"query={query!r} evidence 數應穩定為 13，實得 {len(evidence)}"
    assert report.cross_source_signal is None, (
        f"query={query!r} 應誠實反映真實資料無背離，實得 {report.cross_source_signal}"
    )


@pytest.mark.parametrize(
    ("coin", "query"),
    [
        ("BTC", "BTC 現況"),
        ("BTC", "比特幣分析"),
        ("BTC", "BTC現況"),
        ("BTC", "分析BTC市場"),
        ("BTC", "BTC ETF 資金流"),
        ("SOL", "SOL 現況"),
        ("SOL", "索拉納分析"),
        ("SOL", "SOL現況"),
        ("SOL", "分析SOL市場"),
        ("BNB", "BNB 現況"),
        ("BNB", "幣安幣分析"),
        ("BNB", "BNB現況"),
        ("XRP", "XRP 現況"),
        ("XRP", "瑞波幣分析"),
        ("XRP", "XRP現況"),
    ],
)
def test_other_coins_no_false_divergence_after_coin_filter_fix(coin: str, query: str):
    """coin-filter 主導修正不得讓 BTC/SOL/BNB/XRP 誤觸假背離——這些幣目前沒有
    真實分歧樣本／stance_cache 矛盾配對，修正後仍應維持 cross_source_signal
    為 None（或至少不含 stance_pairs），不可因排序調整而意外浮現假訊號。
    """
    report, evidence, log = run(coin, query, QuestionType.MULTI_SOURCE, offline=True)
    sig = report.cross_source_signal
    if sig is not None:
        assert "stance_pairs" not in sig or not sig["stance_pairs"], (
            f"{coin} query={query!r} 不應出現 stance_pairs 假背離，實得 {sig}"
        )
