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
    OBJECTIVE_KINDS,
    _SENTIMENT_KINDS,
    _STANCE_PAIR_MIN_TRUST,
    _dedup_stance_pairs_by_source,
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
from trustforge.trust.scoring import Claim, ScoredClaim, aggregate, extract_claims, score


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


# ---------------------------------------------------------------------------
# #13：跨源分歧「來源數」按 source 去重（gray plan §1，P0）
# `_dedup_stance_pairs_by_source` 單元測試 + `detect_cross_source_signal`
# 端到端 `distinct_sources` 欄位驗收。
# ---------------------------------------------------------------------------

def test_dedup_stance_pairs_by_source_collapses_same_source_same_stance():
    """同一來源、同一陣營的兩筆 pair → 去重後只留第一筆代表。"""
    pairs = [
        {"source": "coindesk", "stance": "bullish", "claim_id": "a1", "text": "A1"},
        {"source": "coindesk", "stance": "bullish", "claim_id": "a2", "text": "A2"},
        {"source": "decrypt", "stance": "bearish", "claim_id": "b1", "text": "B1"},
    ]
    result = _dedup_stance_pairs_by_source(pairs)
    assert set(result.keys()) == {"bullish", "bearish"}
    assert len(result["bullish"]) == 1
    assert result["bullish"][0]["claim_id"] == "a1", "保留同陣營同來源中第一筆出現的代表"
    assert len(result["bearish"]) == 1
    assert result["bearish"][0]["claim_id"] == "b1"


def test_dedup_stance_pairs_by_source_keeps_different_sources_unchanged():
    """不同來源各一則 → 去重前後筆數不變（回歸鎖，行為逐字相容）。"""
    pairs = [
        {"source": "coindesk", "stance": "bullish", "claim_id": "a", "text": "A"},
        {"source": "decrypt", "stance": "bearish", "claim_id": "b", "text": "B"},
    ]
    result = _dedup_stance_pairs_by_source(pairs)
    assert result == {"bullish": [pairs[0]], "bearish": [pairs[1]]}


def test_dedup_stance_pairs_by_source_does_not_dedup_across_camps():
    """同一來源分屬 bullish/bearish（來源自我矛盾）→ 跨陣營不去重，
    兩邊各自保留，不誤刪（本輪範圍聲明：只修同陣營重複）。"""
    pairs = [
        {"source": "coindesk", "stance": "bullish", "claim_id": "x1", "text": "X1"},
        {"source": "coindesk", "stance": "bearish", "claim_id": "x2", "text": "X2"},
    ]
    result = _dedup_stance_pairs_by_source(pairs)
    assert len(result["bullish"]) == 1
    assert len(result["bearish"]) == 1
    assert result["bullish"][0]["source"] == "coindesk"
    assert result["bearish"][0]["source"] == "coindesk"


def test_dedup_stance_pairs_by_source_empty_input():
    assert _dedup_stance_pairs_by_source([]) == {"bullish": [], "bearish": []}


def test_dedup_stance_pairs_by_source_normalizes_case_and_whitespace():
    """codex 追加修正：去重 key 用 `source.strip().casefold()`，同一來源的
    大小寫/前後空白變體（" CoinDesk " / "coindesk" / "COINDESK"）視為同一
    來源，收斂成 1 筆——顯示仍用原始（第一筆出現時）的 source 字串，不
    改寫使用者看到的來源名稱。真實不同來源（decrypt）不受影響，仍分開。"""
    pairs = [
        {"source": " CoinDesk ", "stance": "bullish", "claim_id": "a1", "text": "A1"},
        {"source": "coindesk", "stance": "bullish", "claim_id": "a2", "text": "A2"},
        {"source": "COINDESK", "stance": "bullish", "claim_id": "a3", "text": "A3"},
        {"source": "decrypt", "stance": "bearish", "claim_id": "b1", "text": "B1"},
    ]
    result = _dedup_stance_pairs_by_source(pairs)
    assert len(result["bullish"]) == 1, "三個大小寫/空白變體應收斂成 1 個獨立來源"
    assert result["bullish"][0]["source"] == " CoinDesk ", "顯示保留第一筆出現的原始字串，不做正規化改寫"
    assert len(result["bearish"]) == 1
    assert result["bearish"][0]["source"] == "decrypt"


def test_stance_pairs_same_source_same_stance_deduped_in_distinct_sources():
    """端到端：同一來源兩筆不同 claim、各自與不同對手配對成功、同陣營
    （bullish）→ `stance_pairs` 原始明細仍列出兩筆（逐字不變，供展開查看），
    但 `distinct_sources.bullish` 去重後只剩一筆代表；`bearish` 陣營本來
    就是兩個不同來源，去重後仍是 2（regression：不誤刪不同來源）。
    """
    scored = [
        _sc("a1", "news", "coindesk", "bullish", 0.44, "看漲敘述 A1"),
        _sc("a2", "news", "coindesk", "bullish", 0.44, "看漲敘述 A2"),
        _sc("b1", "news", "decrypt", "bearish", 0.44, "看跌敘述 B1"),
        _sc("b2", "news", "cryptoslate", "bearish", 0.44, "看跌敘述 B2"),
    ]
    result = detect_cross_source_signal(scored, stance_fn=_contradiction_stance_fn)
    assert result is not None
    assert result["type"] == "divergence"

    # 回歸鎖：stance_pairs 本身不去重，逐筆明細照舊（同來源出現兩次）
    assert len(result["stance_pairs"]) == 4
    raw_bullish_sources = [p["source"] for p in result["stance_pairs"] if p["stance"] == "bullish"]
    assert raw_bullish_sources == ["coindesk", "coindesk"]

    # 去重後：bullish 陣營同一來源只算 1 個獨立來源（修正虛高）
    assert "distinct_sources" in result
    assert len(result["distinct_sources"]["bullish"]) == 1
    assert result["distinct_sources"]["bullish"][0]["source"] == "coindesk"
    # bearish 陣營本來就是兩個不同來源 → 去重後仍是 2（不誤刪）
    assert len(result["distinct_sources"]["bearish"]) == 2
    assert {p["source"] for p in result["distinct_sources"]["bearish"]} == {
        "decrypt", "cryptoslate",
    }


def test_stance_pairs_self_contradiction_across_camps_not_deduped_in_distinct_sources():
    """同一來源兩則不同 claim、分屬不同陣營（bullish vs bearish，來源自我
    矛盾）→ 跨陣營不去重，`distinct_sources` 的 bullish/bearish 各自都
    看得到該來源（不誤刪，符合 #13 明確排除的範圍聲明）。
    """
    scored = [
        _sc("x1", "news", "coindesk", "bullish", 0.44, "看漲敘述 X1"),
        _sc("x2", "news", "coindesk", "bearish", 0.44, "看跌敘述 X2"),
        _sc("y1", "news", "decrypt", "bearish", 0.44, "看跌敘述 Y1"),
        _sc("y2", "news", "decrypt", "bullish", 0.44, "看漲敘述 Y2"),
    ]
    result = detect_cross_source_signal(scored, stance_fn=_contradiction_stance_fn)
    assert result is not None
    distinct = result["distinct_sources"]
    assert "coindesk" in {p["source"] for p in distinct["bullish"]}
    assert "coindesk" in {p["source"] for p in distinct["bearish"]}


def test_stance_pairs_distinct_sources_regression_lock_different_sources_each():
    """既有「不同來源各一則」案例 → distinct_sources 與 stance_pairs 等長，
    行為逐字不變（回歸鎖）。"""
    scored = [
        _sc("a", "news", "coindesk", "bullish", 0.44, "ETF 淨流入方向 A"),
        _sc("b", "news", "decrypt", "bearish", 0.44, "ETF 淨流出方向 B"),
    ]
    result = detect_cross_source_signal(scored, stance_fn=_contradiction_stance_fn)
    assert result is not None
    assert len(result["stance_pairs"]) == 2
    distinct = result["distinct_sources"]
    assert len(distinct["bullish"]) + len(distinct["bearish"]) == 2


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


def test_cross_source_signal_stance_pairs_override_consensus_when_contradiction_confirmed():
    """[correctness HIGH 修正] codex 對抗審：客觀主導方向 == 情緒主導方向時，
    原邏輯一律判 `type="consensus"`；但若情緒來源內部藏著已確認的跨源矛盾配對
    （stance_pairs 非空），繼續顯示「訊號一致」會把真矛盾蓋掉，誤導使用者，
    違反本功能「呈現真實背離」的目的。

    矛盾優先於一致：只要 stance_pairs 非空，一律 type="divergence"，不論聚合
    層級算出的方向是否剛好同向。

    情境構造：
      - 客觀類 1 筆 bullish、trust=0.80（納入聚合方向計算）。
      - 情緒類「多數」2 筆 bullish、trust=0.80（若無 stance_pairs，聚合層級
        會判 consensus：obj_dir == sent_dir == bullish）。
      - 情緒類另有 1 組矛盾配對，trust 皆 <0.5（被排除在聚合方向計算之外，
        不影響 obj_dir/sent_dir 的計算結果），但仍在 `_detect_stance_pairs`
        的 0.35 門檻內、會被偵測到——用只認得這組特定文字配對的 stance_fn，
        避免高信任的「多數」claim 被誤判進矛盾配對（假 stance_fn 若無差別
        對任何方向相反的 pair 都回 contradiction，會把多數 claim 也一起
        掃進來，污染測試情境）。
    """
    def _pair_only_stance_fn(a: str, b: str) -> str:
        if {a, b} == {"看漲敘述 C", "看跌敘述 D"}:
            return "contradiction"
        return "neutral"

    scored = [
        _sc("obj1", "onchain", "glassnode", "bullish", 0.80, "客觀看漲"),
        _sc("sent-major-1", "news", "src-a", "bullish", 0.80, "看漲多數 A"),
        _sc("sent-major-2", "social", "src-b", "bullish", 0.80, "看漲多數 B"),
        _sc("pair-a", "news", "src-c", "bullish", 0.40, "看漲敘述 C"),
        _sc("pair-b", "news", "src-d", "bearish", 0.38, "看跌敘述 D"),
    ]

    # 前提檢查：若忽略矛盾配對，聚合層級單獨算出的方向確實同向（會判 consensus）。
    eligible = [sc for sc in scored if sc.trust >= 0.5]
    obj_only = [sc for sc in eligible if sc.claim.doc.kind in OBJECTIVE_KINDS]
    sent_only = [sc for sc in eligible if sc.claim.doc.kind in _SENTIMENT_KINDS]
    assert {sc.claim.direction for sc in obj_only} == {"bullish"}
    assert {sc.claim.direction for sc in sent_only} == {"bullish"}

    sig = detect_cross_source_signal(scored, stance_fn=_pair_only_stance_fn)

    assert sig is not None
    assert sig["type"] == "divergence", (
        f"情緒來源內部已確認矛盾，不應被聚合層級的同向多數蓋成 consensus，"
        f"實得 type={sig['type']!r}"
    )
    assert "背離" in sig["summary"] or "矛盾" in sig["summary"]
    for word in ("買", "賣", "進場", "出場"):
        assert word not in sig["summary"]  # 守 HOYA 不代客決策

    assert "stance_pairs" in sig
    pair_claim_ids = {p["claim_id"] for p in sig["stance_pairs"]}
    assert pair_claim_ids == {"pair-a", "pair-b"}, (
        f"只有 pair-a/pair-b 應被判定矛盾，實得 {pair_claim_ids}"
    )
    assert pair_claim_ids <= set(sig["supporting_claim_ids"]), (
        "矛盾配對的 claim_id 須併入 supporting_claim_ids，"
        "即使 renderer 只讀這個欄位也不會漏顯示矛盾證據"
    )


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


# ---------------------------------------------------------------------------
# W-coingecko：新 kind（price_live/sentiment）接線進客觀/情緒分類（gray 計劃）。
# `dev_activity` 刻意不歸類，見 orchestrator.OBJECTIVE_KINDS/_SENTIMENT_KINDS
# 旁的說明——不進入任何一組，永遠不參與跨源背離/共識計算。
# ---------------------------------------------------------------------------

def test_coingecko_kinds_registered_in_objective_and_sentiment_sets():
    """price_live 歸客觀類、sentiment 歸情緒類；dev_activity 兩邊都不進。"""
    assert "price_live" in OBJECTIVE_KINDS
    assert "sentiment" in _SENTIMENT_KINDS
    assert "dev_activity" not in OBJECTIVE_KINDS
    assert "dev_activity" not in _SENTIMENT_KINDS


def test_price_live_bullish_sentiment_bearish_is_divergence():
    """price_live 看漲、sentiment 看跌 → 客觀/情緒方向相反，判定 divergence。"""
    scored = [
        _sc("obj1", "price_live", "coingecko-price", "bullish", 0.80, "ETH 現價上漲"),
        _sc("sen1", "sentiment", "coingecko-sentiment", "bearish", 0.55, "ETH 社群看跌"),
    ]
    result = detect_cross_source_signal(scored)
    assert result is not None
    assert result["type"] == "divergence"
    assert result["objective_direction"] == "bullish"
    assert result["sentiment_direction"] == "bearish"
    assert "背離" in result["summary"]


def test_price_live_and_sentiment_same_direction_is_consensus():
    """price_live、sentiment 同向（皆看漲）→ 判定 consensus，非背離。"""
    scored = [
        _sc("obj1", "price_live", "coingecko-price", "bullish", 0.80, "ETH 現價上漲"),
        _sc("sen1", "sentiment", "coingecko-sentiment", "bullish", 0.55, "ETH 社群看漲"),
    ]
    result = detect_cross_source_signal(scored)
    assert result is not None
    assert result["type"] == "consensus"
    assert "stance_pairs" not in result


def test_dev_activity_not_counted_in_objective_or_sentiment_grouping():
    """dev_activity 不歸類：即使方向與 sentiment 相反，也不觸發假背離
    （因為只有 dev_activity + sentiment 兩筆，客觀類 0 筆 → 回 None）。"""
    scored = [
        _sc("dev1", "dev_activity", "coingecko-dev", "bearish", 0.55, "ETH 開發活動下滑"),
        _sc("sen1", "sentiment", "coingecko-sentiment", "bullish", 0.55, "ETH 社群看漲"),
    ]
    result = detect_cross_source_signal(scored)
    assert result is None, f"dev_activity 不應被計入客觀類，實得 {result}"


# ---------------------------------------------------------------------------
# codex HIGH（Tier2 最後一根）：上面 4 個測試用手造 ScoredClaim(direction=...) 直接
# 指定方向，繞過了真實 production 路徑——測不出
# `CoinGeckoSentimentSource` 產出的文字本身是否真的能讓
# `trust.scoring._infer_direction` 推出正確方向。根因：原文字固定寫
# 「看漲 X%，看跌 Y%」，兩個方向詞都出現、計數打平 → 永遠 neutral →
# `detect_cross_source_signal` 拒收 → kind 接線形同虛設。
#
# 下面改用**真實** `CoinGeckoSentimentSource`/`CoinGeckoPriceSource`（只 monkeypatch
# 最底層 `_fetch_url` 固定回應 JSON，其餘一路走真代碼）→ `extract_claims` →
# `score` → `detect_cross_source_signal`，全程不手造 ScoredClaim/direction。
# ---------------------------------------------------------------------------

def _fake_sentiment_json(up: float, down: float) -> bytes:
    import json
    return json.dumps({
        "sentiment_votes_up_percentage": up,
        "sentiment_votes_down_percentage": down,
    }).encode()


def _fake_price_json(coingecko_id: str, price: float, change_24h: float) -> bytes:
    import json
    return json.dumps({
        coingecko_id: {
            "usd": price,
            "usd_24h_change": change_24h,
            "usd_market_cap": 4e11,
            "last_updated_at": 1_700_000_000,
        },
    }).encode()


def test_coingecko_sentiment_up_dominant_infers_bullish_via_real_pipeline(monkeypatch):
    """真實 CoinGeckoSentimentSource（up 明顯 > down）→ 真實 extract_claims，
    斷言推出的 direction 是 bullish（不手造 direction，走真代碼）。"""
    from trustforge.ingestion import coingecko

    coingecko.reset_process_cache()
    monkeypatch.setattr(
        coingecko, "_fetch_url",
        lambda url, extra_headers=None: _fake_sentiment_json(58.6, 41.4),
    )
    docs = coingecko.CoinGeckoSentimentSource().fetch("", coin="BTC")
    assert len(docs) == 1
    claims = extract_claims(docs)
    assert len(claims) == 1
    assert claims[0].direction == "bullish", (
        f"up-dominant 應推出 bullish，實得 {claims[0].direction}（text={claims[0].text!r}）"
    )


def test_coingecko_sentiment_down_dominant_infers_bearish_via_real_pipeline(monkeypatch):
    """真實 CoinGeckoSentimentSource（down 明顯 > up）→ 真實 extract_claims，
    斷言推出的 direction 是 bearish（不手造 direction，走真代碼）。"""
    from trustforge.ingestion import coingecko

    coingecko.reset_process_cache()
    monkeypatch.setattr(
        coingecko, "_fetch_url",
        lambda url, extra_headers=None: _fake_sentiment_json(30.0, 70.0),
    )
    docs = coingecko.CoinGeckoSentimentSource().fetch("", coin="ETH")
    assert len(docs) == 1
    claims = extract_claims(docs)
    assert len(claims) == 1
    assert claims[0].direction == "bearish", (
        f"down-dominant 應推出 bearish，實得 {claims[0].direction}（text={claims[0].text!r}）"
    )


def test_coingecko_sentiment_near_tie_stays_neutral_via_real_pipeline(monkeypatch):
    """差距 < 門檻（五五波）時維持中性語意，不強行分邊（回歸鎖，同批修正的
    邊界情況：避免為了「必須有方向」而把雜訊也判成有方向）。"""
    from trustforge.ingestion import coingecko

    coingecko.reset_process_cache()
    monkeypatch.setattr(
        coingecko, "_fetch_url",
        lambda url, extra_headers=None: _fake_sentiment_json(51.0, 49.0),
    )
    docs = coingecko.CoinGeckoSentimentSource().fetch("", coin="SOL")
    claims = extract_claims(docs)
    assert claims[0].direction == "neutral"


def test_coingecko_price_live_direction_reflects_24h_change_sign_via_real_pipeline(monkeypatch):
    """同批發現的連帶問題：`CoinGeckoPriceSource` 文字原本完全不含方向詞
    （「24h 變動 +8.20%」沒有「上漲」/「下跌」字樣），導致 price_live 主張
    永遠被 `_infer_direction` 判成 neutral——即使加進 OBJECTIVE_KINDS，客觀類
    的主導方向也恆為 neutral，跨源背離/共識同樣永遠拒收。修法已在
    `coingecko.py` 依漲跌幅正負附上明確方向詞；這裡走真代碼驗證。"""
    from trustforge.ingestion import coingecko

    coingecko.reset_process_cache()
    monkeypatch.setattr(
        coingecko, "_fetch_url",
        lambda url, extra_headers=None: _fake_price_json("ethereum", 3500.0, 8.2),
    )
    up_docs = coingecko.CoinGeckoPriceSource().fetch("", coin="ETH")
    assert extract_claims(up_docs)[0].direction == "bullish"

    coingecko.reset_process_cache()
    monkeypatch.setattr(
        coingecko, "_fetch_url",
        lambda url, extra_headers=None: _fake_price_json("ethereum", 3200.0, -6.5),
    )
    down_docs = coingecko.CoinGeckoPriceSource().fetch("", coin="ETH")
    assert extract_claims(down_docs)[0].direction == "bearish"


def test_coingecko_real_pipeline_objective_reverse_sentiment_produces_real_divergence(monkeypatch):
    """端到端最終驗收：真實 `CoinGeckoPriceSource`（看多）+ 真實
    `CoinGeckoSentimentSource`（看空，down-dominant）一路走 extract_claims →
    score → detect_cross_source_signal，斷言真的產出 divergence，
    objective_direction/sentiment_direction 皆來自真代碼推斷，非手造。

    情緒類單一來源信任分結構性地到不了 detect_cross_source_signal 的 0.5
    合格門檻（見 orchestrator.py `_STANCE_PAIR_MIN_TRUST` 旁的說明：即使
    news(0.65 信譽) 單來源無佐證也只有 ~0.475）——這是既有、刻意的設計
    （單一匿名/聚合來源不該單獨觸發高信心跨源訊號），不是本輪修的範圍。
    比照 `test_trust_scoring.py::test_independent_corroboration_raises_trust`
    既有慣例，補一則同議題、不同來源的真實佐證文本，模擬「CoinGecko 情緒
    投票結果被社群另一來源同步報導」的真實情境，讓情緒類信任分透過交叉
    佐證機制（非手造 trust 數字）合法跨過門檻。"""
    from trustforge.ingestion import coingecko

    coingecko.reset_process_cache()
    monkeypatch.setattr(
        coingecko, "_fetch_url",
        lambda url, extra_headers=None: _fake_price_json("ethereum", 3500.0, 8.2),
    )
    price_docs = coingecko.CoinGeckoPriceSource().fetch("", coin="ETH")

    coingecko.reset_process_cache()
    monkeypatch.setattr(
        coingecko, "_fetch_url",
        lambda url, extra_headers=None: _fake_sentiment_json(30.0, 70.0),
    )
    # 情緒投票 API 本身無時間戳欄位，`CoinGeckoSentimentSource.fetch()` 用
    # `time.time()`（呼叫當下真實牆鐘時間）當 ts——固定住它，讓這筆 doc 跟
    # 下面其餘 doc／`score(now=...)` 落在同一個測試時間軸（~1_700_000_000
    # 附近），而非真實「現在」（#12 recency 全域防禦修正後，若不固定，這筆
    # doc 相對 `now=1_700_000_200.0` 會變成「未來時間戳」，recency 降為中性
    # 0.5，不再是舊版 bug 誤灌的滿分 1.0，會讓本測試的跨源訊號斷言失真）。
    monkeypatch.setattr(coingecko.time, "time", lambda: 1_700_000_150.0)
    sentiment_docs = coingecko.CoinGeckoSentimentSource().fetch("", coin="ETH")

    # 佐證來源：與 CoinGecko 情緒投票同議題、方向相同，文字部分重疊（同
    # `test_independent_corroboration_raises_trust` 用 `shared` 字串佐證的慣例）。
    corroborating_doc = Document(
        id="social-eth-echo-1", kind="social", source="reddit-eth",
        text=f"{sentiment_docs[0].text}，Reddit 版討論同步轉向謹慎",
        ts=1_700_000_100.0,
    )

    claims = extract_claims(price_docs + sentiment_docs + [corroborating_doc])
    scored = score(claims, now=1_700_000_200.0)

    result = detect_cross_source_signal(scored)
    assert result is not None, "真實 CoinGecko 資料應能產出跨源訊號，實得 None"
    assert result["type"] == "divergence"
    assert result["objective_direction"] == "bullish"
    assert result["sentiment_direction"] == "bearish"
    assert "背離" in result["summary"]
