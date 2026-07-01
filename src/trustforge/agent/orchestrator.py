"""Agent 編排：信任加權 brief → 結構化 Report + Evidence List + Execution Log。

反作弊鐵則：**判斷結構、證據整合、信任評分由本 pipeline 產生**，
Bedrock 只負責把推理「行文」成可讀敘述，不得把第三方現成結論當主要結果。

顯式 3 步驟推理鏈（P0-3）：
  Step 1 — Claim 抽取（Bedrock #1）：extract_claims_with_llm → 結構化主張
  Step 2 — 判斷形成（純 pipeline）：score + aggregate → TrustedBrief
  Step 3 — 帶溯源行文（Bedrock #2）：narrative 強制引用 claim_id
  Step 4 — 限制複審（Bedrock #3，選用，預算允許才執行）
"""
from __future__ import annotations

import time
from typing import Callable

from ..bedrock import BedrockClient
from ..execlog import ExecutionLog
from ..ingestion.base import Document
from ..ledger import append_run, estimate_cost
from ..schema import BasisItem, Evidence, QuestionType, Report, iso_utc
from ..trust.scoring import ScoredClaim, TrustedBrief

# Step 4 最低剩餘預算門檻（秒）：低於此值直接跳過，確保在 15 分鐘內完成
_STEP4_MIN_BUDGET_SEC = 60.0

_STEP4_SYSTEM = (
    "你是加密市場分析審查員。只能依據提供的報告文字審查，不引入外部知識。"
)
_STEP4_LIMIT_SENTINEL = "LIMITS_OK"

OBJECTIVE_KINDS = {"price", "price_live", "onchain", "regulatory", "hoyabit"}
_SENTIMENT_KINDS: set[str] = {"news", "social", "sentiment"}
# CoinGecko `dev_activity`（GitHub stars/forks/commits）刻意不歸入任一類：既非
# 客觀「市場」事實（開發活躍度與價格走勢無直接因果），也非情緒訊號，若強行
# 分類容易在跨源背離偵測中製造假背離（例如開發活躍度下降 vs 現價上漲被誤判
# 為客觀類內部矛盾）。不歸類 = 該 kind 的主張永遠不進入
# `detect_cross_source_signal` 的 objective/sentiment 分組計算。

# Tier2（真實分歧樣本）：stance_pairs 專用的最低信任門檻。刻意低於 detect_cross_
# _source_signal 主流程的 0.5「合格」門檻——真實的雙來源直接矛盾（如同議題 ETF
# 資金流向因結算時區/資料商方法論不同，當日方向相反）本質上**拿不到跨源佐證加分**
# （_corroboration 的方向閘會擋掉互相佐證，兩造各自單打獨鬥），信任分結構性地封頂在
# 「來源基礎信譽 + 滿分時效」附近（news：0.5*0.65+0.15*1.0=0.475 都到不了 0.5）。
# 若沿用 0.5 門檻，真實的兩方矛盾樣本幾乎不可能被納入 stance_pairs 掃描池，等於
# 這個偵測機制對其設計目標（呈現真實背離）永遠失效。0.35 仍濾掉低信任雜訊／被
# manipulation penalty 命中的主張，precision 由下方 stance_fn 的語意矛盾分類把關
# （非快取命中一律 fail-safe 為 "neutral"，不會誤判），不靠這個門檻把關準確度。
_STANCE_PAIR_MIN_TRUST = 0.35

SYSTEM = (
    "你是加密市場分析助理。只能依據提供的『已信任加權證據』作答，"
    "區分事實/推論/結論，標註信心與限制，不提供投資建議。"
    "你的任務是把證據行文成可讀推理，不得引入未提供的外部結論。"
)


def _scored_to_evidence(sc: ScoredClaim, related: str) -> Evidence:
    doc = sc.claim.doc
    ref = doc.meta.get("content_reference") or sc.claim.text[:120]
    return Evidence(
        source=doc.source,
        fetched_at=iso_utc(doc.ts),
        content_reference=ref,
        related_claim=related,
        source_url=doc.url,
        kind=doc.kind,
        trust=round(sc.trust, 3),
        trust_components={k: round(v, 3) for k, v in sc.components.items()},
        # Tier2 可解釋 UX：操縱關鍵詞命中原文回填，供 web.py 渲染紅旗
        # （見 trust.scoring._manipulation_flags；空 list 時等同未命中）。
        flags=list(sc.manip_flags),
        # W3：文字相似度透明化 flag，informational-only、不影響 trust 分數，
        # 供 web.py 用中性樣式渲染（見 trust.scoring._coordination_signals；
        # 空 list 時等同未命中）。
        info_flags=list(sc.info_flags),
    )


def _direction(brief: TrustedBrief) -> str:
    """從高信任價格事實判方向（我方判斷，非外部結論）。"""
    for sc in brief.supporting:
        if sc.claim.doc.kind == "price":
            t = sc.claim.text
            if "上漲" in t:
                return "偏多"
            if "下跌" in t:
                return "偏空"
            if "盤整" in t:
                return "中性"
    return "中性"


def _derive_limits(brief: TrustedBrief) -> tuple[list[str], list[str]]:
    limits: list[str] = []
    flips: list[str] = []
    kinds = {sc.claim.doc.kind for sc in brief.supporting}
    if len(kinds) < 3:
        limits.append(f"資料來源類型僅 {len(kinds)} 類（<3），多源整合度有限，結論不確定性較高。")
    if brief.confidence < 0.5:
        limits.append("整體信心偏低，支撐證據不足以形成強判斷。")
    if brief.contrarian:
        limits.append(f"存在 {len(brief.contrarian)} 條反方／低信任訊號，已標記但未納入主結論。")
        flips.append("若反方訊號獲得高信任獨立來源佐證，結論可能反轉。")
    flips.append("出現高信任的反向鏈上大額流動或監管事件時，須重評。")
    return limits, flips


def _harvest_stance_cost_events(client: BedrockClient, log: ExecutionLog) -> None:
    """收割 `client.cost_events`（stance 語意分類真呼叫的成本）進 `log`，並清空。

    `classify_stance` 的成本無法在深層 O(n²) 迴圈中方便帶入 log，改由
    `BedrockClient` 自己在真呼叫（cache-miss）成功後累積在 `client.cost_events`；
    呼叫端在每個可能觸發 stance 呼叫的階段（Step2 `score()`、Step2.5
    `detect_cross_source_signal()`）結束後都必須呼叫本函式收割，確保：
    (1) 成本確實進帳本；(2) `client.cost_events` 歸零，不殘留到下一階段/
    下一輪 run（demo 可靠性 #32 追加 cost-integrity HIGH 修正——同一個
    client 物件常見於 comparison 模式兩幣共用同一輪，殘留未清空的
    cost_events 會被下一輪誤記到別的幣頭上）。

    `getattr` 防禦：測試用的假 client（如 FakeBedrockClient）可能沒有此屬性。
    """
    cost_events = getattr(client, "cost_events", None)
    if not cost_events:
        return
    for ev in cost_events:
        log.record_llm_cost(ev["model"], ev["tokens_in"], ev["tokens_out"], ev["cost_usd"])
    cost_events.clear()


def _detect_stance_pairs(
    scored: list[ScoredClaim],
    stance_fn: Callable[[str, str], str] | None,
) -> list[dict]:
    """掃描情緒類（news/social）主張中「不同來源 + 方向明確相反」的候選配對，
    交給 stance_fn 做語意矛盾分類，過濾掉純方向詞表面相反、實質無關的假陽性。

    真實案例（Tier2）：同議題（如 ETH 現貨 ETF 資金流向）因結算時區/資料商方法論
    不同，不同來源當日報導方向相反——兩則新聞遣詞完全不同、關鍵字重疊低，純文字
    overlap 比對（見 trust.scoring._corroboration）抓不到，需靠語意 stance 分類
    （trust/stance_cache.py，離線走持久化快取，不即時打 Bedrock）才辨識得出。

    `stance_fn` 為 None（未提供）時直接回空 list：向後相容，不改變
    `detect_cross_source_signal` 既有行為，也不會意外觸發任何 stance 呼叫。

    回傳：扁平 list，內含所有涉及至少一組矛盾配對的主張（跨配對去重），每筆
    `{"source", "stance", "claim_id", "text"}`，供未來 UI 渲染跨源矛盾對照。
    """
    if stance_fn is None:
        return []

    eligible = [
        sc for sc in scored
        if sc.trust >= _STANCE_PAIR_MIN_TRUST and sc.claim.doc.kind in _SENTIMENT_KINDS
    ]

    seen_claim_ids: set[str] = set()
    pairs: list[dict] = []
    for i, a in enumerate(eligible):
        for b in eligible[i + 1:]:
            if a.claim.doc.source == b.claim.doc.source:
                continue  # 同源不算跨源矛盾
            da, db = a.claim.direction, b.claim.direction
            if "neutral" in (da, db) or da == db:
                continue  # 需方向明確且相反；方向相同或不明不算矛盾
            if stance_fn(a.claim.text, b.claim.text) != "contradiction":
                continue
            for sc in (a, b):
                if sc.claim.id in seen_claim_ids:
                    continue
                seen_claim_ids.add(sc.claim.id)
                pairs.append({
                    "source": sc.claim.doc.source,
                    "stance": sc.claim.direction,
                    "claim_id": sc.claim.id,
                    "text": sc.claim.text,
                })
    return pairs


def detect_cross_source_signal(
    scored: list[ScoredClaim],
    stance_fn: Callable[[str, str], str] | None = None,
) -> dict | None:
    """跨源訊號偵測：判斷客觀類與情緒類訊號是否背離或共識（純函式，無副作用）。

    入參 scored = list[ScoredClaim]，包含所有可用主張（trust 任意）。
    函式內部只採用 trust >= 0.5 的主張進行計算。

    規格：
    - 客觀類 = OBJECTIVE_KINDS；情緒類 = {"news", "social"}
    - 各類信任加權方向投票：weight[dir] = sum(trust for dir)，最高者為主導；
      若最高 weight < 0.3 × 該類總 trust → 主導視為 "neutral"
    - 背離：客觀主導 ≠ neutral 且情緒主導 ≠ neutral 且兩者相反
    - 共識：兩類主導相同（非 neutral）且兩類各有 ≥1 source
    - None：任一類 0 筆 / 任一主導 neutral / 兩類 source 合計 < 2

    `stance_fn`（選用，預設 None）：Tier2 新增。None 時完全不影響上述既有規格
    （逐字相容，回歸鎖）。提供時，額外掃描情緒類主張中「同議題語意矛盾」的
    跨源配對（見 `_detect_stance_pairs`）：
    - 找到配對時：矛盾優先於聚合層級的判定（demo 可靠性 #32 追加
      correctness HIGH 修正）——
        - 若聚合層級已判定 divergence：維持 divergence，`stance_pairs` 併入
          dict，配對 claim_id 併入 `supporting_claim_ids`。
        - 若聚合層級判定 consensus（客觀/情緒多數方向剛好同向）：**改判為
          divergence**，summary 改寫為反映矛盾的背離敘述——共識底下藏著
          「已確認」的跨源矛盾時，不得繼續顯示「訊號一致」把矛盾蓋掉。
        - 若聚合層級判不出結論（回 None 的任一分支）：改回傳一個以
          stance_pairs 為主體的 `type="divergence"` 訊號——因為客觀類趨勢
          與「兩則新聞互相矛盾」是兩件獨立可觀察到的事，後者不該被前者的
          聚合結果蓋掉。
    - 找不到配對時：完全不影響既有回傳值（含 None、consensus）。

    守 HOYA「不代客決策」：summary 使用中性提醒措辭，嚴禁決策字眼。
    """
    # 只取 trust >= 0.5 的主張
    eligible = [sc for sc in scored if sc.trust >= 0.5]

    objective = [sc for sc in eligible if sc.claim.doc.kind in OBJECTIVE_KINDS]
    sentiment = [sc for sc in eligible if sc.claim.doc.kind in _SENTIMENT_KINDS]

    stance_pairs = _detect_stance_pairs(scored, stance_fn)

    def _stance_pair_signal() -> dict | None:
        """聚合層級判不出背離/共識時的備援：若仍偵測到同議題語意矛盾配對，
        產出以 stance_pairs 為主體的 divergence 訊號。stance_pairs 為空則回 None
        （逐字等同未提供 stance_fn 時的既有行為）。"""
        if not stance_pairs:
            return None
        sources = sorted({p["source"] for p in stance_pairs})
        return {
            "type": "divergence",
            "objective_direction": None,
            "sentiment_direction": None,
            "summary": (
                f"來源 {'、'.join(sources)} 對同一議題方向相反，"
                "呈背離，建議交叉驗證、留意轉折。"
            ),
            "supporting_claim_ids": [p["claim_id"] for p in stance_pairs],
            "stance_pairs": stance_pairs,
        }

    # 任一類 0 筆 → None（除非有 stance_pairs 備援）
    if not objective or not sentiment:
        return _stance_pair_signal()

    def _dominant(group: list[ScoredClaim]) -> str:
        """回傳信任加權後的主導方向；若最高票 < 0.3×total 則回 'neutral'。"""
        weights: dict[str, float] = {}
        total = 0.0
        for sc in group:
            d = sc.claim.direction
            weights[d] = weights.get(d, 0.0) + sc.trust
            total += sc.trust
        if not total:
            return "neutral"
        best_dir = max(weights, key=lambda k: weights[k])
        return best_dir if weights[best_dir] >= 0.3 * total else "neutral"

    obj_dir = _dominant(objective)
    sent_dir = _dominant(sentiment)

    # 任一主導 neutral → None（除非有 stance_pairs 備援）
    if obj_dir == "neutral" or sent_dir == "neutral":
        return _stance_pair_signal()

    # 兩類 source 合計 < 2 → None（除非有 stance_pairs 備援）
    obj_sources = {sc.claim.doc.source for sc in objective}
    sent_sources = {sc.claim.doc.source for sc in sentiment}
    if len(obj_sources | sent_sources) < 2:
        return _stance_pair_signal()

    # 判定訊號類型（矛盾優先於一致，demo 可靠性 #32 追加 correctness HIGH 修正）：
    # 客觀/情緒兩類的信任加權多數方向可能剛好同向（聚合層級判定 consensus），
    # 但若 `_detect_stance_pairs` 已在情緒來源內部抓到「已確認」的跨源語意
    # 矛盾配對（stance_pairs 非空），代表這個共識底下其實藏著真矛盾——繼續
    # 顯示「訊號一致」會把矛盾蓋掉、誤導使用者，違反本功能「呈現真實背離」
    # 的目的。修法：只要 stance_pairs 非空，一律 type="divergence"，不論聚合
    # 層級算出的 obj_dir/sent_dir 是否同向；沒有 stance_pairs 時維持既有
    # divergence/consensus 判定不變（回歸鎖）。
    if obj_dir != sent_dir:
        signal_type = "divergence"
        collision = False
    elif stance_pairs:
        signal_type = "divergence"
        collision = True
    else:
        signal_type = "consensus"
        collision = False

    # 中文方向標籤（守不代客決策）
    _label = {"bullish": "偏多", "bearish": "偏空"}
    obj_label = _label.get(obj_dir, obj_dir)
    sent_label = _label.get(sent_dir, sent_dir)

    if collision:
        # obj_dir == sent_dir（聚合層級同向），但情緒來源內部已測出矛盾——
        # 矛盾優先，摘要必須反映真背離，不得沿用「訊號一致」敘述。
        stance_sources = sorted({p["source"] for p in stance_pairs})
        summary = (
            f"客觀與情緒多數方向雖同為{obj_label}，"
            f"但來源 {'、'.join(stance_sources)} 對同一議題方向相反，"
            "情緒面存在矛盾，呈背離，建議交叉驗證、留意轉折。"
        )
    elif signal_type == "divergence":
        summary = (
            f"客觀數據{obj_label}、情緒類{sent_label}，"
            "呈背離，建議交叉驗證、留意轉折。"
        )
    else:
        summary = f"客觀與情緒同向{obj_label}，訊號一致。"

    # 佐證 claim_ids：各類中方向符合主導的主張 + 已確認矛盾配對涉及的主張
    # （即使 renderer 只讀 supporting_claim_ids，也能指向矛盾證據，不會漏顯示）。
    supporting_ids = (
        [sc.claim.id for sc in objective if sc.claim.direction == obj_dir]
        + [sc.claim.id for sc in sentiment if sc.claim.direction == sent_dir]
    )
    for _p in stance_pairs:
        if _p["claim_id"] not in supporting_ids:
            supporting_ids.append(_p["claim_id"])

    result = {
        "type": signal_type,
        "objective_direction": obj_dir,
        "sentiment_direction": sent_dir,
        "summary": summary,
        "supporting_claim_ids": supporting_ids,
    }
    if stance_pairs:
        result["stance_pairs"] = stance_pairs
    return result


def build_report(query: str, coin: str, qtype: QuestionType, brief: TrustedBrief,
                 client: BedrockClient | None = None,
                 log: ExecutionLog | None = None,
                 now_fn=time.time,
                 stance_fn: Callable[[str, str], str] | None = None,
                 scored: list[ScoredClaim] | None = None) -> tuple[Report, list[Evidence]]:
    """`stance_fn`：選填。供跨源 stance_pairs 偵測（Step 2.5）使用；未提供時
    （例如直接呼叫 `build_report` 的測試）會自建一份**有預算上限**的
    stance_fn（`build_stance_fn`，綁 `log.remaining()`），不會無上限直接打
    Bedrock（demo 可靠性 #32 追加 HIGH-2 修正）。`run_agent_pipeline` 會傳入
    與 Step2 `score()` **共用同一個 `_StanceBudget` 實例**的 stance_fn，
    避免兩處各自另建一份預算讓真呼叫上限實質變成兩倍。

    `scored`：選填。供跨源訊號偵測（`detect_cross_source_signal`/
    `_detect_stance_pairs`）使用的**完整、未截斷**主張全集——即 `aggregate()`
    把 `supporting`/`contrarian` 分別截斷成 `[:10]`/`[:5]` **之前**的原始
    `scored` 清單（demo 可靠性 #32 追加 HIGH 修正）。

    背景：真資料上，兩則真矛盾的情緒類新聞常因方向鮮明而落在 contrarian
    （trust < 0.5）；只要同一輪還有 >5 筆信任分更高的 contrarian（不論是否與
    該矛盾對相關），這兩則就會被 `aggregate()` 的 `[:5]` 截斷擠出
    `brief.contrarian`，若這裡改用 `brief.supporting + brief.contrarian` 偵測，
    真背離就會漏抓——且截斷是否命中純看資料量與分數分布，不是可預期、可重現
    的行為。修法：一律優先用呼叫端傳入的完整 `scored`（`aggregate()` 只重排
    不刪項，`_detect_stance_pairs` 內部本就自行以 `_STANCE_PAIR_MIN_TRUST`/
    `_SENTIMENT_KINDS` 篩選，不依賴 supporting/contrarian 的截斷結果）。
    stance 預算（`_StanceBudget`）不因輸入變大而失守——候選對變多只會讓更多
    次落入持久化快取命中（免費）或提早耗盡預算改 fail-safe 回 neutral，
    真呼叫次數上限不變。

    未提供時（例如既有測試直接呼叫 `build_report(..., brief=brief)` 不給
    `scored`）退回 `brief.supporting + brief.contrarian`（既有行為，逐字向後
    相容，不影響既有合成 fixture 單元測試）。
    """
    client = client or BedrockClient(offline=True)
    log = log or ExecutionLog(now_fn=now_fn)

    # 1. 證據清單（支撐 + 反方）
    log.record("evidence.build", summary=f"supporting={len(brief.supporting)} contrarian={len(brief.contrarian)}")
    evidence: list[Evidence] = []
    key_basis: list[BasisItem] = []
    ev_index: dict[tuple, int] = {}   # (source, content_reference) → 去重,保留最高 trust
    judgment_tag = f"{coin} 市場判斷"

    def _add_evidence(sc: ScoredClaim, related: str) -> int:
        ev = _scored_to_evidence(sc, related)
        # key 含角色(related):支撐與反方即使同來源同引用也不共用 bucket,避免 silent drop
        key = (ev.source, ev.content_reference, related)
        if key in ev_index:
            idx = ev_index[key]
            if ev.trust > evidence[idx].trust:   # 同來源同引用 → 留最高信任那筆
                evidence[idx] = ev
            return idx
        idx = len(evidence)
        evidence.append(ev)
        ev_index[key] = idx
        return idx

    for sc in brief.supporting:
        idx = _add_evidence(sc, judgment_tag)
        key_basis.append(BasisItem(
            claim=sc.claim.text,
            explanation=f"來源 {sc.claim.doc.source}（{sc.claim.doc.kind}），信任 {sc.trust:.2f}。",
            evidence_idx=[idx],
        ))
    for sc in brief.contrarian:
        _add_evidence(sc, "反方／低信任訊號")

    # 2. 我方判斷（pipeline 產生，非外部結論）
    direction = _direction(brief)
    facts = [sc.claim.text for sc in brief.supporting if sc.claim.doc.kind in OBJECTIVE_KINDS]
    n_indep = len({sc.claim.doc.source for sc in brief.supporting})

    if qtype == QuestionType.HYPOTHESIS:
        head = f"針對假設「{query}」：依現有證據，{coin} 短期傾向{direction}。"
    elif qtype == QuestionType.COMPARISON:
        head = f"{coin} 當前市場位置：{direction}。（比較分析需對每個幣種各跑一次 pipeline 後並列）"
    else:
        head = f"{coin} 當前市場狀態判斷：{direction}。"
    market_judgment = head + f"（{n_indep} 個獨立來源支撐，整體信心 {brief.confidence:.2f}）"
    log.record("judgment.derive", params={"direction": direction, "indep_sources": n_indep})

    # 2.5 跨源訊號偵測（純演算法，在 Bedrock 行文前完成）
    # stance_fn：未由呼叫端提供時（例如測試直接呼叫 build_report），自建一份
    # 有預算上限的 stance_fn（demo 可靠性 #32 追加 HIGH-2 修正——原本這裡另建
    # 一份「無預算」的 cached_stance_fn，線上模式 cache miss 會無上限直接打
    # Bedrock，且發生在 Step2 成本收割之後，成本永遠沒進帳本）。離線一律傳
    # None 給 build_stance_fn，讓底層 cached_stance_fn 只讀持久化快取
    # （demo/sample_data/stance_cache.json），快取 miss 時 fail-safe 回
    # "neutral"，不即時打 Bedrock、不 raise。
    if stance_fn is None:
        from ..trust.scoring import build_stance_fn  # 延遲匯入避免頂層循環
        stance_fn = build_stance_fn(
            stance_client=None if client.offline else client,
            stance_remaining_time_fn=log.remaining,
        )
    cross_signal = detect_cross_source_signal(
        scored if scored is not None else brief.supporting + brief.contrarian,
        stance_fn=stance_fn,
    )
    # 收割本步驟（Step2.5）可能產生的 stance 呼叫成本，避免漏記帳／殘留到下一輪。
    _harvest_stance_cost_events(client, log)

    # 3. Bedrock 行文（Step 3：帶 claim_id 溯源；離線為佔位，結構不依賴它）
    # 建立 claim_id → 摘要對照，供 prompt 強制引用
    claim_refs = "\n".join(
        f"- [{sc.claim.id}] {sc.claim.text[:100]}"
        for sc in brief.supporting[:8]
    )
    # 若有跨源訊號，指示 LLM 只敘述已算好的 summary，不得自行判斷背離/共識
    _cross_note = ""
    if cross_signal:
        _cross_note = (
            f"\n跨源訊號（已由 pipeline 算好）：{cross_signal['summary']}\n"
            "請在行文中僅敘述此跨源訊號摘要，不得自行判斷背離/共識。"
        )
    prompt = (
        f"幣種：{coin}\n題型：{qtype.value}\n問題：{query}\n"
        f"我方判斷：{market_judgment}\n"
        f"事實（含 claim_id）：\n{claim_refs}\n"
        f"{_cross_note}"
        "\n請用 2-3 句把上述事實串成事實→推論→結論的推理，"
        "每個判斷必須引用對應 claim_id（格式：[claim_id]），僅依事實，勿引入外部結論。"
    )
    _t_step3 = log._now()
    try:
        _result_step3 = client.complete(system=SYSTEM, prompt=prompt)
        narrative = _result_step3.text
        # 離線也會走到這（complete() 離線回傳 token=0 的佔位結果），故這裡永遠
        # 記一筆：線上是真花費，離線是 $0 ——帳本兩種情況都看得到本次 Step3 呼叫。
        log.record_llm_cost(
            _result_step3.model_id,
            _result_step3.input_tokens,
            _result_step3.output_tokens,
            estimate_cost(
                _result_step3.model_id, _result_step3.input_tokens, _result_step3.output_tokens
            ),
        )
    except Exception:
        # Bedrock 失敗 → 用結構化判斷當行文降級,不中斷管線(且仍記錄此步 log)
        # 呼叫未成功、無 usage 數字 → 不記成本
        narrative = f"[行文服務暫時無法使用,以下為結構化判斷] {market_judgment}"
    _step3_elapsed = round(log._now() - _t_step3, 2)
    log.record(
        "bedrock.complete",
        params={"step": 3, "task": "narrative_with_citations",
                "model": client.config.model_id or "offline",
                "step_elapsed_sec": _step3_elapsed},
        summary=f"帶 claim_id 溯源行文；耗時 {_step3_elapsed}s；輸入 {len(brief.supporting)} 條主張",
    )
    inferences = [
        f"客觀價格事實指向{direction}；由 {n_indep} 個獨立來源交叉佐證。",
        narrative.strip(),
    ]

    limits, flips = _derive_limits(brief)
    report = Report(
        coin=coin, question_type=qtype.value, question=query,
        market_judgment=market_judgment, facts=facts, inferences=inferences,
        key_basis=key_basis, confidence=brief.confidence,
        limits=limits, could_flip=flips,
        contrarian=[sc.claim.text for sc in brief.contrarian],
        generated_at=iso_utc(now_fn()),
        direction=direction,
        cross_source_signal=cross_signal,
    )
    log.record("report.done", summary=f"facts={len(facts)} basis={len(key_basis)} evidence={len(evidence)}")
    return report, evidence


# ---------------------------------------------------------------------------
# 顯式 3 步驟 Agent Pipeline（P0-3）
# ---------------------------------------------------------------------------

def run_agent_pipeline(
    query: str,
    coin: str,
    qtype: QuestionType,
    docs: list[Document],
    client: BedrockClient | None = None,
    log: ExecutionLog | None = None,
    now_fn=time.time,
) -> tuple[Report, list[Evidence]]:
    """三步驟顯式推理鏈。

    Step 1 — Claim 抽取（Bedrock #1 或 regex fallback）
    Step 2 — pipeline 評分聚合（反作弊：純演算法，不呼叫 Bedrock）
    Step 3 — 帶 claim_id 溯源行文（Bedrock #2，by build_report 內部執行）
    Step 4 — 限制複審（Bedrock #3，選用，預算剩餘 > 60s 才執行）

    Execution Log 保證 ≥2 筆 bedrock.complete 記錄（Step1 + Step3）。
    """
    from ..trust.scoring import aggregate, build_stance_fn, score  # 延遲匯入避免頂層循環

    client = client or BedrockClient(offline=True)
    log = log or ExecutionLog(now_fn=now_fn)
    # comparison 兩幣共用同一個 log（見 pipeline.run_comparison）：若帳本收尾直接
    # 掃「log.events 全部」的 llm.cost，第二輪會把第一輪已寫過的成本又算一次
    # （帳本累計變成 2A+B）。記下本輪開始時的事件數，收尾只彙總「本輪新增」的部分。
    _log_events_start_idx = len(log.events)

    # ------------------------------------------------------------------
    # Step 1: Claim 抽取（Bedrock #1 / regex fallback）
    # ------------------------------------------------------------------
    log.record("pipeline.step1.start", summary=f"docs={len(docs)}；準備 LLM claim 抽取")
    _t1 = log._now()
    claims = client.extract_claims_with_llm(docs, log=log)
    _step1_elapsed = round(log._now() - _t1, 2)

    # 區分是否真正走了 LLM（offline / 未設模型 → regex fallback）
    _is_llm_step1 = not (client.offline or not client.config.model_id)
    log.record(
        "bedrock.complete",
        params={
            "step": 1,
            "task": "claim_extraction",
            "model": client.config.model_id or "offline/regex-fallback",
            "step_elapsed_sec": _step1_elapsed,
            "llm_active": _is_llm_step1,
        },
        summary=(
            f"Step1 抽取 {len(claims)} 條主張；"
            f"輸入 {len(docs)} 份文件；"
            f"耗時 {_step1_elapsed}s；"
            f"{'LLM 模式' if _is_llm_step1 else 'regex fallback'}"
        ),
    )

    # ------------------------------------------------------------------
    # Step 2: 判斷形成（純 pipeline，不呼叫 Bedrock）
    # ------------------------------------------------------------------
    log.record("pipeline.step2.start", summary="pipeline 評分 + 聚合（反作弊純演算法）")
    now_ts = max((d.ts for d in docs), default=now_fn())
    # W1.5（#15）：線上帶真 client（cache miss 才即時呼叫 Bedrock）；離線／未設模型帶
    # None（CEO+codex 對抗審修正：None 不代表關掉語意矛盾閘，score() 仍會建立
    # cached_stance_fn(None) 去讀持久化快取 demo/sample_data/stance_cache.json，
    # 只有在快取也 miss 時才 fail-safe 回 neutral——離線 demo 才看得到 #15 的修復）。
    # 第二輪對抗審修正：接 log.remaining()（即時剩餘的官方 15 分鐘執行時間）當
    # stance 呼叫的時間預算，跟配對硬上限一起防 O(n²) 呼叫吃光整條執行窗口。
    # 第三輪對抗審修正（demo 可靠性 #32 追加 HIGH-2）：先建「一份」共用的
    # budgeted stance_fn，Step2 的交叉佐證矛盾閘與 Step3（build_report 內的
    # 跨源 stance_pairs 偵測）共用同一個 `_StanceBudget` 實例——避免兩處各自
    # 另建一份預算，讓「單次執行真呼叫 Bedrock 的配對硬上限」實質變成兩倍、
    # 也讓兩處共用同一份即時剩餘時間（`log.remaining()`）判斷。
    shared_stance_fn = build_stance_fn(
        stance_client=None if client.offline else client,
        stance_remaining_time_fn=log.remaining,
    )
    scored = score(
        claims,
        now=now_ts,
        stance_fn=shared_stance_fn,
    )
    # score() 跑完、Step2 交叉佐證矛盾閘可能觸發的 stance 呼叫都已發生，
    # 這裡統一收割進 log、並清空 client.cost_events，避免下個 run 重複計費。
    _harvest_stance_cost_events(client, log)
    # coin=coin：「coin-filter 主導」（demo 可靠性 #32 追加）——見 aggregate() docstring，
    # 讓明確提及該幣的證據不因 query 措辭（如中文複合詞、無空格）忽窄忽寬地被截斷擠出。
    brief = aggregate(scored, query=query, coin=coin)
    log.record(
        "judgment.derive",
        params={"supporting": len(brief.supporting), "contrarian": len(brief.contrarian),
                "confidence": round(brief.confidence, 3)},
        summary="Step2 純 pipeline 完成；判斷由演算法產生，非 LLM",
    )

    # ------------------------------------------------------------------
    # Step 3: 帶溯源行文（Bedrock #2，由 build_report 執行並記錄 log）
    # ------------------------------------------------------------------
    log.record("pipeline.step3.start", summary="準備 Bedrock 行文（Step3）")
    report, evidence = build_report(
        query=query, coin=coin, qtype=qtype, brief=brief,
        client=client, log=log, now_fn=now_fn,
        stance_fn=shared_stance_fn,
        # demo 可靠性 #32 追加 HIGH 修正：傳完整（未截斷）scored 全集做跨源訊號
        # 偵測，避免 aggregate() 的 supporting[:10]/contrarian[:5] 截斷把真矛盾
        # 配對擠出偵測範圍（見 build_report docstring）。
        scored=scored,
    )
    # 防禦性再收割一次（demo 可靠性 #32 追加 cost-integrity HIGH 修正）：
    # build_report() 內部已在 Step2.5 偵測後自行收割一次，這裡屬於
    # belt-and-suspenders——`run_agent_pipeline()` 自身保證每輪結束時
    # client.cost_events 必為空，不依賴 build_report() 未來重構仍維持
    # 內部收割時機正確；目前正常路徑下這裡永遠是 no-op（cost_events 已空）。
    _harvest_stance_cost_events(client, log)

    # ------------------------------------------------------------------
    # Step 4: 限制複審（Bedrock #3，選用）
    # ------------------------------------------------------------------
    _should_step4 = (
        not client.offline
        and client.config.model_id
        and log.remaining() > _STEP4_MIN_BUDGET_SEC
    )
    if _should_step4:
        log.record("pipeline.step4.start", summary=f"準備限制複審；剩餘預算 {log.remaining():.0f}s")
        _review_prompt = (
            f"以下是一份加密市場分析報告的「已知限制」段落：\n"
            f"{chr(10).join('- ' + x for x in report.limits)}\n\n"
            f"市場判斷：{report.market_judgment}\n"
            f"信心：{report.confidence:.2f}\n\n"
            f"請審查限制說明是否完整。若有遺漏，補充 JSON list of strings；"
            f"若已充分，輸出 JSON list 包含單一元素 \"{_STEP4_LIMIT_SENTINEL}\"。"
            f"只輸出 JSON list，不要其他文字。"
        )
        _t4 = log._now()      # 先賦值,確保 except 路徑也能算耗時(不依賴 dir() 探測)
        _additions: list[str] = []
        try:
            _result_step4 = client.complete(system=_STEP4_SYSTEM, prompt=_review_prompt)
            log.record_llm_cost(
                _result_step4.model_id,
                _result_step4.input_tokens,
                _result_step4.output_tokens,
                estimate_cost(
                    _result_step4.model_id, _result_step4.input_tokens, _result_step4.output_tokens
                ),
            )
            _review_raw = _result_step4.text
            _s = _review_raw.find("[")
            _e = _review_raw.rfind("]") + 1
            if _s != -1 and _e > 0:
                import json as _json
                _parsed = _json.loads(_review_raw[_s:_e])
                _additions = [
                    str(x) for x in _parsed
                    if isinstance(x, str) and x != _STEP4_LIMIT_SENTINEL and x.strip()
                ]
                if _additions:
                    report.limits.extend(_additions)
        except Exception:
            _additions = []
            # 呼叫未成功、無 usage 數字 → 不記成本
        _step4_elapsed = round(log._now() - _t4, 2)
        log.record(
            "bedrock.complete",
            params={"step": 4, "task": "limitation_review",
                    "model": client.config.model_id or "offline",
                    "step_elapsed_sec": _step4_elapsed},
            summary=f"Step4 限制複審；補充 {len(_additions)} 條；耗時 {_step4_elapsed}s",
        )

    # ------------------------------------------------------------------
    # 帳本：run 收尾寫一筆跨 run 持久化成本紀錄（append-only，不影響 report/evidence）
    # 只彙總「本輪開始後新增」的 llm.cost 事件（見 _log_events_start_idx），
    # 避免 comparison 兩幣共用同一 log 時，第二輪把第一輪已寫過的成本重複計入。
    # ------------------------------------------------------------------
    _llm_calls = [
        {
            "model": e["params"].get("model"),
            "tokens_in": e["params"].get("tokens_in", 0),
            "tokens_out": e["params"].get("tokens_out", 0),
            "cost_usd": e["params"].get("cost_usd", 0.0),
        }
        for e in log.events[_log_events_start_idx:]
        if e["tool"] == "llm.cost"
    ]
    append_run({
        "ts": iso_utc(now_fn()),
        "question_type": qtype.value,
        "coin": coin,
        "offline": client.offline,
        "calls": _llm_calls,
        "total_cost_usd": round(sum(c["cost_usd"] for c in _llm_calls), 6),
    })

    return report, evidence
