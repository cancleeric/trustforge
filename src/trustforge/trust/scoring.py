"""信任提煉引擎。

對多源 Document 抽出 Claim（主張），對每條主張計算 TrustScore：

    TrustScore = w_src · SourceReputation
               + w_corr · CrossSourceCorroboration
               + w_rec · RecencyDecay
               − w_manip · ManipulationPenalty

最後對 query 相關主張做信任加權聚合，產出 TrustedBrief（含支撐 / 反方證據）。

注意：本檔為「演算法骨架 + 可運作的啟發式」。claim 抽取與操縱偵測在競賽期間
可改用 Bedrock judge 強化（見 agent 層）；但評分核心刻意保持**可解釋、可審查**，
不全交給 LLM 黑箱——這正是「信任提煉」相對於「LLM 直接摘要」的價值所在。
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Callable

from ..bedrock import _STANCE_CONNECT_TIMEOUT_SEC, _STANCE_READ_TIMEOUT_SEC
from ..ingestion.base import Document, _coins_mentioned, _matches_coin, _mentions_coin
from .dawid_skene import LABELS as _DS_LABELS, em_source_reliability
from .stance_cache import cached_stance_fn

# W1.5（#15）+ CEO/codex 對抗審修正：線上 stance 呼叫預算（防 O(n²) 呼叫無上限打
# Bedrock）。這是「真正呼叫 Bedrock」的配對硬上限——只在 stance_cache.py 的
# `cached_stance_fn` 確認 cache miss、即將發起真呼叫時才消耗；免費的 cache-hit
# 不吃這個額度（第 3 輪對抗審修正：預算若在查快取前就先扣，會讓大量 cache-hit
# 吃光全域預算，導致快取裡真正的 contradiction 也被跳過檢查、錯判為佐證）。
# 額度用完後其餘「需要新呼叫」的配對一律降級 "neutral"（不呼叫、不錯殺）。數字是
# 保守預設，可視實際成本調整；claims 數量在此之內時完全不受影響。
DEFAULT_STANCE_PAIR_BUDGET = 40

# stance 呼叫最少要保留的剩餘執行時間（秒）。ExecutionLog.remaining() 低於這個門檻
# 時一律視為預算耗盡，不再發起新的真呼叫，就算配對數還沒到硬上限，避免官方 15
# 分鐘執行窗口的最後一點時間被 stance 呼叫吃光、報告生不出來（命題第一號失敗模式）。
#
# 第 3 輪對抗審修正：門檻必須 >= 單次呼叫最壞總耗時（connect_timeout + read_timeout，
# 兩者皆設 `total_max_attempts=1` 不重試，見 bedrock.py），否則「剩餘時間看起來還夠」
# 但呼叫真正開始執行後仍可能把僅存的一點時間吃光、讓報告生成階段越過 15 分鐘。
# 直接算自 bedrock.py 的 timeout 常數（不在這裡重複寫死數字），避免兩邊之後各自
# 調整 timeout 卻忘記同步更新這裡，數字對不上。
_STANCE_REPORT_MARGIN_SEC = 14.0  # 呼叫結束後留給報告產生/收尾階段的裕量
STANCE_TIME_RESERVE_SEC = (
    _STANCE_CONNECT_TIMEOUT_SEC + _STANCE_READ_TIMEOUT_SEC + _STANCE_REPORT_MARGIN_SEC
)  # = 3 + 8 + 14 = 25.0s


# --- 權重（可調）---------------------------------------------------------
DEFAULT_WEIGHTS = {
    "src": 0.50,    # 來源信譽（客觀來源即使無佐證也應有基本信任）
    "corr": 0.25,   # 交叉佐證（獨立來源越多越加分）
    "rec": 0.15,    # 時效
    "manip": 0.40,  # 操縱懲罰（扣分項，足以把喊單壓到 0）
}

# 來源類型基礎信譽（0–1）。客觀數據（價格/鏈上）最高，匿名社群最低。
KIND_REPUTATION = {
    "price": 0.95,     # 官方提供 OHLCV，客觀事實
    "onchain": 0.95,
    "regulatory": 0.90,
    "hoyabit": 0.85,   # 交易所一手行情數據
    "news": 0.65,
    "social": 0.35,
    # CoinGecko（W-coingecko，CEO 審核 gray 計劃）：現價客觀事實，但為
    # 第三方彙整（非交易所一手數據），信譽略低於 hoyabit/onchain；情緒投票
    # 與 GitHub 開發活動皆為輔助訊號，信譽落在 news 與 social 之間。
    "price_live": 0.90,
    "sentiment": 0.50,
    "dev_activity": 0.50,
    # 鯨魚/名人交易信號（celebrity-whale-trades spec）：
    # - whale_onchain：鏈上可驗證的大額轉帳，客觀事實但非一手交易所數據
    # - celebrity_trade：已標記錢包/名人公開交易，意見型需佐證（未驗證者
    #   在 _source_reputation 中動態降級至 social 等級 0.35）
    "whale_onchain": 0.88,
    "celebrity_trade": 0.50,
}


def _reputation_floor(kind: str) -> float:
    """W2：動態信譽每輪迭代 clamp 下限，防止 SR 蒸發到 0。

    取 kind 基礎信譽的 30%（social: 0.35*0.3≈0.105，符合 CEO refinement「social
    不低於 ~0.1」；price/onchain 最高 ≈0.29，依序遞減，未知 kind 保守回退 0.35 基礎
    → floor≈0.105，等同 social 下限，不給未知來源類型更高保障）。
    """
    return round(0.3 * KIND_REPUTATION.get(kind, 0.35), 4)


# 各 kind 的 recency 半衰期（小時）。鯨魚/名人交易信號時效性極強（市場秒級反應），
# 使用 2 小時半衰期；一般來源沿用預設 12 小時（不列入此 map，走 _recency_decay 預設）。
KIND_HALFLIFE_HOURS: dict[str, float] = {
    "whale_onchain": 2.0,       # 鯨魚鏈上轉帳：市場反應極快
    "celebrity_trade": 2.0,     # 名人交易宣告：時效同鯨魚
}

# 域內停用詞（Domain Stopwords）：加密市場每篇分析都有、對「是否在說同一件事」無鑑別力的詞。
# 這些詞從 overlap 計算中完全排除，讓佐證判斷只依賴具體/稀有的內容詞。
DOMAIN_STOP: set[str] = {
    # 幣名（太普遍，任何 BTC 分析都有）
    "btc", "eth", "sol", "bnb", "xrp",
    "bitcoin", "ethereum", "solana",
    "比特幣", "比特", "以太坊", "以太", "幣",
    # 超高頻市場通用詞
    "市場", "價格", "成交量", "交易所", "交易",
    "行情", "數據", "分析", "資料", "報告",
    # 方向性通用詞（過於籠統；「漲」/「跌」單字被 _normalize 過濾不到，改用整詞）
    "漲跌", "上漲", "下跌", "看漲", "看跌", "走低", "走高",
    # 高頻語法詞（_normalize 已過濾單字，這裡補雙字）
    # 注意：支撐/阻力是具體 TA 訊號詞，已從 DOMAIN_STOP 移除（見 codex #fix-[Low]）
    "目前", "近期", "顯示", "表示", "預計", "預測", "可能",
    "目標",
}

# 操縱訊號關鍵詞（啟發式；正式版可換 Bedrock 分類器）。
# ⚠️ 誠實聲明（issue #177-A）：這是**關鍵詞層級表面比對**，非行為/統計/協同
# 操縱偵測；只要換詞（如把「暴漲」改成「大漲」）即可繞過。僅作「可疑用語標記」
# 併入有限信任扣分（components["manipulation"]），**不代表「已判定操縱」**。
_MANIP_PATTERNS = [
    r"to the moon", r"暴漲", r"翻倍", r"\bshill\b", r"喊單", r"穩賺",
    r"financial advice", r"\bpump\b", r"快上車", r"百倍",
]


@dataclass
class Claim:
    id: str
    text: str
    doc: Document
    claim_type: str = "inference"   # fact | inference | opinion
    direction: str = "neutral"      # bullish | bearish | neutral


@dataclass
class ScoredClaim:
    claim: Claim
    trust: float                       # 0–1
    components: dict = field(default_factory=dict)   # 各分項，供溯源/解釋
    # W2：動態來源信譽可解釋 trace。預設啟用（`dynamic_reputation=True`）；
    # 設 False 時為 None。開啟時填入該 claim 來源的
    # {source, prior, final, agree_n, contradict_n, iterations_run}。
    # 刻意獨立於 components（後者維持 str -> number 契約，不塞巢狀 dict）。
    reputation_trace: dict | None = None
    # Tier2 可解釋 UX：操縱關鍵詞命中原文清單（由 `_manipulation_flags` 填入，
    # 供 `agent.orchestrator._scored_to_evidence` 回填 `Evidence.flags`）。
    # 預設空 list，不影響既有以 keyword 建構 ScoredClaim 的呼叫點/相等性比較。
    # 這是「關鍵詞層級可疑用語標記」（非操縱判定），會反映在
    # `components["manipulation"]`。
    manip_flags: list[str] = field(default_factory=list)
    # W3：informational-only 透明化 flag（由 `_coordination_signals` 填入，供
    # `agent.orchestrator._scored_to_evidence` 回填 `Evidence.info_flags`）。
    # 與 `manip_flags` 不同：這裡的訊號（如多源文字高度相似）不代表已判定操縱、
    # **不併入 `components["manipulation"]`**，純粹供人工判讀。CEO 定案：文字
    # 相似度單獨無法證明協同操縱，自動扣分必然誤傷合法聯播/引用。預設空 list。
    info_flags: list[str] = field(default_factory=list)


@dataclass
class TrustedBrief:
    query: str
    supporting: list[ScoredClaim]      # 高信任、支撐主流結論
    contrarian: list[ScoredClaim]      # 低信任 / 反方，供反方證據
    confidence: float                  # 整體信心（0–1，支撐主張 trust 的裸加權均值）
    # W4：校準後信心（0–1）。由 `aggregate()` 用 `_evidence_strength()`
    # （綜合獨立來源數/kind 多元度/佐證對反方優勢比例/裸信心，見該函式上方
    # 模組註解的設計說明——codex 對抗審 [HIGH] 修正：不能只校準裸均值，
    # 因為裸均值恆為 0 或 >=support_threshold，永遠進不了中段「低信心」帶）
    # 算出能真正跨越 [0, 1] 的證據強度指標，再用 `_calibrate_confidence()`
    # （硬編分位數映射表，見該函式上方誠實聲明）做最後一層保守修正。供
    # `agent.orchestrator` 的三態 abstain 判斷使用。**保留** `confidence`
    # 裸值供對照/既有呼叫端相容——不砍舊欄位。預設 0.0：只有透過
    # `aggregate()` 產生的 brief 才會是真正校準值；測試直接手動建構
    # `TrustedBrief(...)` 不傳此欄位時維持逐字向後相容（未校準 -> 0.0，
    # 呼叫端若讀取此欄位務必經由 aggregate() 取得有意義的值）。
    calibrated_confidence: float = 0.0
    # W4 codex 對抗審第 6～8 輪（coin-relevance 收斂史，見 `aggregate()`
    # docstring 完整說明）：第 6/7 輪先後用「額外欄位」（`coin_scoped_
    # supporting`）＋「呼叫端各自重新過濾」（`build_report` 的
    # `cross_signal_input`）修補 facts/`_direction()`/cross_source_signal
    # 等單點漏洞，但這種「piecemeal」修法本質上治標不治本——只要還有一個
    # report-facing 欄位是從「未過濾的 supporting/contrarian」算出來的
    # （例如 `contrarian` 輸出、裸 `confidence` 顯示、`_derive_limits`），
    # 就會被抓出下一個漏洞。第 8 輪根治：`aggregate(coin=)` 直接讓
    # `supporting`／`contrarian`／`confidence` 三者本身就是 coin-scoped
    # 的（`_matches_coin` 篩過，保留本幣相關＋全市場通用，只排除明確他幣）
    # ——不再需要額外欄位；已移除 `coin_scoped_supporting`（第 6/7 輪引入，
    # 第 8 輪起併入 `supporting` 本身，不再單獨存在）。`agent.orchestrator.
    # build_report` 現在直接讀 `brief.supporting`/`brief.contrarian`/
    # `brief.confidence` 即可拿到已 coin-scoped 的資料，不必再各自過濾。

    def provenance(self) -> list[dict]:
        """溯源鏈：每個被採用主張的來源與分數。"""
        return [
            {
                "claim_id": sc.claim.id,
                "source": sc.claim.doc.source,
                "kind": sc.claim.doc.kind,
                "url": sc.claim.doc.url,
                "trust": round(sc.trust, 3),
                "components": {k: round(v, 3) for k, v in sc.components.items()},
            }
            for sc in self.supporting
        ]


# --- 1. 主張抽取 ---------------------------------------------------------
def extract_claims(docs: list[Document]) -> list[Claim]:
    """把 Document 切成句級主張。骨架版用句界切分；正式版可用 Bedrock 抽取結構化主張。"""
    claims: list[Claim] = []
    # 只在真正的句末切分：中文標點 / 換行 / 後接空白或結尾的 ASCII .!?。
    # 避免把 46637.08、-7.4% 這類小數拆斷而丟失語義。
    _SENT = re.compile(r"[。！？\n]+|[.!?]+(?=\s|$)")
    for d in docs:
        sentences = [s.strip() for s in _SENT.split(d.text) if s.strip()]
        for i, s in enumerate(sentences):
            claims.append(Claim(id=f"{d.id}#{i}", text=s, doc=d, direction=_infer_direction(s)))
    return claims


# --- 2~4. 分項 -----------------------------------------------------------
def _source_reputation(c: Claim, dynamic_map: dict[str, float] | None = None) -> float:
    """來源信譽。`dynamic_map=None`（預設，逐字等同現行）：純先驗，僅取決於
    `KIND_REPUTATION` 或 per-doc `meta["reputation"]` 覆寫。

    W2：傳入 `dynamic_map`（`{source: SR}`，見 `_iterate_source_reputation`）時，
    改用該來源的動態信譽；若該來源不在 map 中（理論上不會發生，防禦性寫法），
    回退為先驗值，不 raise。

    名人交易降級（celebrity-whale-trades spec R3）：celebrity_trade kind 中，
    meta["verified_onchain"]=False 的未驗證宣告自動降級至 social 等級 0.35，
    防止未經鏈上驗證的名人喊單獲得過高信任。
    """
    base = KIND_REPUTATION.get(c.doc.kind, 0.5)
    # 名人交易動態降級：未經鏈上驗證者降至 social 等級
    if c.doc.kind == "celebrity_trade" and not c.doc.meta.get("verified_onchain", False):
        base = KIND_REPUTATION.get("social", 0.35)
    # 來源層級覆寫（白名單/黑名單）
    override = c.doc.meta.get("reputation")
    prior = float(override) if override is not None else base
    if dynamic_map is None:
        return prior
    # issue #72/#132：dynamic_map 的 key 是 canonical source（見
    # `_iterate_source_reputation` 分組口徑），查表也走 canonical，避免同源
    # 大小寫/空白變體查不到先驗值、被誤當成未知來源。
    dynamic_sr = dynamic_map.get(_canonical_source(c.doc.source), prior)
    # 名人交易動態降級：即使 dynamic_map 給了較高信譽，未驗證者仍不得超過 social 等級
    if c.doc.kind == "celebrity_trade" and not c.doc.meta.get("verified_onchain", False):
        return min(dynamic_sr, KIND_REPUTATION.get("social", 0.35))
    return dynamic_sr


def _recency_decay(c: Claim, now: float, half_life_h: float | None = None) -> float:
    """指數衰減；加密資訊半衰期短，預設 12 小時。ts=0 視為未知→中性 0.5。

    `half_life_h=None`（預設）：自動依 `c.doc.kind` 查 `KIND_HALFLIFE_HOURS`，
    未列入的 kind 使用 12.0 小時預設值。鯨魚/名人交易信號使用 2 小時的加速
    衰減（市場秒級反應，見 celebrity-whale-trades spec R4）。

    #12（全域防禦，呼應 #24 不虛增）：`age_h = (now - ts) / 3600` 未來時間戳
    （`ts > now`，如壞資料/時鐘偏差/偽造 pubDate）會算出負值。舊實作用
    `max(0.0, age_h)` 把負齡 clamp 成 0 齡 → `decay = 0.5**0 = 1.0`，等於把
    「不可能存在的未來資訊」捏造成「剛剛發生、最高信任」的觀測——這個 clamp
    在 `_recency_decay` 這一層對**所有來源**都成立，不只 CoinGecko（該連接器
    已在自己的 ingestion 層另外 clamp 了 `last_updated_at`，但那只保護
    CoinGecko 自己的欄位；news RSS `pubDate`／社群／鏈上等其他來源若出現
    未來時間戳，仍會流進這裡被灌成滿分）。

    修法：age_h < 0（未來時間戳）視為異常，不給滿分、也不當成「已知的最舊」
    （因為真實年齡未知，不該用 0.0 分懲罰它可能只是輕微時鐘漂移），比照
    ts=0（未知 ts）的既有中性語意，回傳 0.5——全來源、scoring 層統一生效，
    不必逐連接器各自補防禦。

    codex 對抗審 HIGH（PR #48 third-round，呼應 #12/#24）：`float('nan')` 能
    通過既有 ts 解析（cached docs、on-chain 等來源皆可能夾帶壞資料）。舊版
    `age_h < 0` 對 NaN 一律回傳 `False`（NaN 與任何數比較恆假）→ 不會走進
    上面的未來戳分支，而是落到 `math.pow(0.5, nan / half_life_h) == nan`，
    NaN 一路傳播到 `score()` 最後的 `max(0.0, min(1.0, raw))`——NaN 與 0.0/
    1.0 比較同樣恆假，CPython 的 `min`/`max` 在此情況下會回傳**第一個比較到
    的引數**，實測 `max(0.0, min(1.0, nan)) == 1.0`，等於让含 NaN（或 ±inf）
    的時間戳拿到**滿分信任**——比未來戳問題更嚴重（未來戳只降到中性 0.5，
    NaN 卻反而衝到滿分）。

    修法：`ts`/`now`/算出的 `age_h` 任一非有限值（NaN、+inf、-inf，用
    `math.isfinite` 判斷）一律視為「真實年齡未知」，比照 ts=0／未來戳的既有
    中性語意回傳 0.5，阻斷 NaN/inf 往下游 `min`/`max` 傳播（沿用
    `ingestion/coingecko.py` 的 `_finite_num` 同款 `math.isfinite` 防禦慣例，
    保持一致）。
    """
    if half_life_h is None:
        half_life_h = KIND_HALFLIFE_HOURS.get(c.doc.kind, 12.0)
    if not c.doc.ts:
        return 0.5
    if not math.isfinite(c.doc.ts) or not math.isfinite(now):
        return 0.5
    age_h = (now - c.doc.ts) / 3600.0
    if not math.isfinite(age_h) or age_h < 0:
        return 0.5
    return math.pow(0.5, age_h / half_life_h)


# 明確否定結構(不吃「不僅/不斷/不只」這類肯定副詞)。命中前 4 字內出現才視為否定。
_NEG_RX = re.compile(r"不會|不太|不致|不至|不再|沒有|沒|尚未|未|無法|別|勿|非")

# --- 方向推斷關鍵詞（離線/regex 路徑用）---------------------------------
_BULLISH_WORDS: list[str] = [
    "上漲", "漲", "看漲", "看多", "買入", "買盤", "累積", "增持", "突破",
    "流入", "利多", "走高", "反彈", "上揚", "攀升",
]
_BEARISH_WORDS: list[str] = [
    "下跌", "跌", "看跌", "看空", "賣壓", "拋壓", "拋售", "流出",
    "利空", "走低", "暴跌", "崩", "恐慌", "清算", "賣盤", "下挫",
]


def _infer_direction(text: str) -> str:
    """從文字關鍵詞推斷方向（純函式，離線/regex 路徑用）。

    最長詞優先非重疊匹配：先處理長詞，短詞若落在已消耗區間則不計
    （避免「上漲」被其子字串「漲」重複計數、「看漲 看空」誤判 bullish）。
    否定守門：若方向詞前 4 字元內出現 _NEG_RX 否定詞，該詞不計入計數
    （但仍標記區間已消耗，防短詞補計）。
    bullish 命中 > bearish → "bullish"；反之 "bearish"；平手或都 0 → "neutral"。
    """
    # 收集所有候選配對：(start, end, word, direction)
    candidates: list[tuple[int, int, str, str]] = []
    for w in _BULLISH_WORDS:
        for m in re.finditer(re.escape(w), text):
            candidates.append((m.start(), m.end(), w, "bullish"))
    for w in _BEARISH_WORDS:
        for m in re.finditer(re.escape(w), text):
            candidates.append((m.start(), m.end(), w, "bearish"))

    # 依詞長降序（最長優先），同長度依位置升序
    candidates.sort(key=lambda x: (-(x[1] - x[0]), x[0]))

    consumed: list[tuple[int, int]] = []  # 已消耗的 (start, end) 區間

    def _overlaps(s: int, e: int) -> bool:
        return any(s < ce and e > cs for cs, ce in consumed)

    bullish = 0
    bearish = 0
    for start, end, _word, direction in candidates:
        if _overlaps(start, end):
            continue
        consumed.append((start, end))
        # 否定守門
        if _NEG_RX.search(text[max(0, start - 4): start]):
            continue  # 消耗區間但不計分
        if direction == "bullish":
            bullish += 1
        else:
            bearish += 1

    if bullish > bearish:
        return "bullish"
    if bearish > bullish:
        return "bearish"
    return "neutral"


def _manip_hits(text: str) -> list[str]:
    """回傳文字中所有通過否定守門（見 `_manipulation_penalty` 註解）的操縱關鍵詞
    命中原文字串，依出現順序、未去重。`_manipulation_penalty`／`_manipulation_flags`
    共用此清單，確保兩者對「命中什麼」的認定逐字一致，只是用途不同（前者算分數，
    後者回原文供 UI 回溯）。"""
    hits: list[str] = []
    for p in _MANIP_PATTERNS:
        for m in re.finditer(p, text, re.IGNORECASE):
            if _NEG_RX.search(text[max(0, m.start() - 4):m.start()]):
                continue
            hits.append(m.group(0))
    return hits


def _manipulation_penalty(c: Claim, extra_hits: int = 0) -> float:
    # ⚠️ 誠實聲明（issue #177-A）：關鍵詞層級啟發式；非行為/統計操縱偵測。
    # 否定守門:命中前 4 字內有明確否定(如「不會暴漲」)不計,避免正當新聞被誤扣
    hits = _manip_hits(c.text)
    # 社群來源的操縱訊號加重
    weight = 1.5 if c.doc.kind == "social" else 1.0
    # `extra_hits`：預留給「確定判定為操縱、需要真正扣分」的額外命中數量，
    # 併入同一套關鍵詞計分公式（沿用既有 0.4/hit、social 加重 1.5 倍），不新增
    # 權重項、不動 `raw = ... - w["manip"] * manip` 既有公式結構。預設 0。
    #
    # CEO 定案（codex 對抗審確認根本限制）：W3 模板相似指標
    # （`_coordination_template_flags`）**不再**餵入這裡——文字相似度單獨無法
    # 區分「協同操縱」vs「合法聯播/引用」，自動扣分必然誤傷合法聯播；改為
    # informational-only（見 `_coordination_signals` docstring），只產生
    # `ScoredClaim.info_flags`，不影響這個函式的分數。目前 `score()` 呼叫本
    # 函式一律不傳 `extra_hits`（沿用預設 0），此參數保留供未來若有「確定性
    # 且經證實有效」的扣分型協同指標時使用。
    return min(1.0, (len(hits) + extra_hits) * 0.4 * weight)


def _manipulation_flags(c: Claim) -> list[str]:
    """Tier2 可解釋 UX：回傳命中的操縱關鍵詞原文（去重、保留原文大小寫），
    供 `Evidence.flags` 回溯——使用者可從 flags 直接對照原文出現的可疑字眼。

    刻意獨立於 `_manipulation_penalty`：純粹列出「命中什麼」，不含權重/分數
    計算，不動 `_manipulation_penalty` 既有 float 簽名與既有測試鎖定的分數
    行為（兩者底層共用 `_manip_hits`，命中判定邏輯不會分岔）。
    """
    seen: list[str] = []
    for h in _manip_hits(c.text):
        if h not in seen:
            seen.append(h)
    return seen


def _normalize(s: str) -> set[str]:
    return {t for t in re.findall(r"[\w一-鿿]+", s.lower()) if len(t) > 1}


# --- 來源身分正規化（issue #72：repo-wide canonical source identity）------
# 全倉「同一來源只算一個獨立聲音」不變量的唯一真相來源。`_corroboration_detail`/
# `_evidence_strength`/`_iterate_source_reputation` 與 `agent.orchestrator` 三處
# 去重口徑都必須走這裡，不允許各自再發明一套（見 issue #106 收口與本 PR #72）。
#
# 正規化分兩層：
#   1. 零成本層：`strip().casefold()`——治大小寫/前後空白變體
#     （`"CoinDesk"` / `" coindesk "` / `"COINDESK"` 收斂成 `coindesk`）。
#   2. 別名層（#72 本輪實作）：同一發布實體的不同呈現（域名形式、平台更名、
#      帳號別名）收斂到同一個 canonical key。
#
# ⚠️ 保守白名單原則：只有「確定是同一發布實體」的變體才收斂，絕不反向——不
# 能把真正不同的來源併成一個，否則反而會「虛減」獨立來源數、讓回音室被誤判
# 成跨源互證。新增別名請附註為何是同一實體。
_SOURCE_ALIASES: dict[str, str] = {
    # 域名形式 → 裸發布者名（同一家媒體的 RSS/網站/APP 可能帶不同後綴）
    "coindesk.com": "coindesk",
    "cointelegraph.com": "cointelegraph",
    "theblock.co": "theblock",
    "theblock": "theblock",
    "reuters.com": "reuters",
    "bloomberg.com": "bloomberg",
    "bitcoinmagazine.com": "bitcoinmagazine",
    "newsbtc.com": "newsbtc",
    "cryptoslate.com": "cryptoslate",
    "decrypt.co": "decrypt",
    "utoday.com": "utoday",
    # 平台更名 / 帳號別名：Twitter → X 是同一平臺的更名，視為同一來源。
    "twitter": "x",
    "x.com": "x",
    # 監管機關官方單一源（regulatory.py 固定 `sec-gov`；其他呈現視為同一機關）。
    "sec edgar": "sec-gov",
    "sec": "sec-gov",
    "sec.gov": "sec-gov",
}


def _canonical_source(source: str | None) -> str:
    """repo-wide 唯一來源身分正規化（issue #72 收口）。

    回傳 canonical key：先 `strip().casefold()`，再套 `_SOURCE_ALIASES` 別名
    收斂。falsy（None / 空字串 / 純空白經 strip 後變空）直接回傳原樣（空字串），
    呼叫端負責決定是否視為幽靈來源——本函式只做「身分正規化」，不做「是否
    計入」的判斷（後者由 `_independent_source_keys` 等聚合層決定），職責單一、
    便於單元測試。

    只用於「比對/去重/計數」，顯示一律保留原始 `source` 字串（見
    `_normalize_source_key` 既有約定）。
    """
    if not source:
        return ""
    key = source.strip().casefold()
    if not key:
        return ""
    return _SOURCE_ALIASES.get(key, key)


def _direction_compatible(d1: str, d2: str) -> bool:
    """方向相容檢查。任一方為 neutral 時不擋（離線/預設安全）；兩者皆有方向時必須一致。"""
    if "neutral" in (d1, d2):
        return True
    return d1 == d2


def _claim_coin(c: Claim) -> str:
    """派生 claim 所屬幣別：優先 `doc.meta["coin"]`（見 schema `Document` 註解——
    幣別在 `doc.meta["coin"]` 非 `doc.coin`），退 `_coins_mentioned(doc.id+text)`。
    兩者皆缺則回空字串；空字串下 DS 的 `(coin, window)` key 仍成立，只是 coin
    維度退化成單一 bucket——不影響 EM 收斂，只是失去跨幣分群。

    確定性：提及多幣時取 `sorted()` 第一個，不受 set 迭代順序影響。
    """
    explicit = c.doc.meta.get("coin")
    if explicit:
        return str(explicit).strip().upper()
    mentioned = _coins_mentioned(c.doc.id + " " + c.doc.text)
    if mentioned:
        return sorted(mentioned)[0]
    return ""


# --- W3：確定性協同操縱偵測（免 LLM，見 scoring.py 頂部 docstring 分項公式）------
# 現況操縱偵測（`_MANIP_PATTERNS`）是關鍵詞比對，換詞即可繞過。W3 補兩個確定性、
# 可回溯的協同/異常指標，只用既有 evidence pool 的 source/text/ts（不需真社群圖、
# 不呼叫 Bedrock）。命中時併入既有 `w["manip"]`（0.40）懲罰——不新增權重項、不動
# `raw = w["src"]*rep + w["corr"]*corr + w["rec"]*rec - w["manip"]*manip` 聚合公式，
# 只是 `manip` 這一項的計算多算一種訊號（見 `_manipulation_penalty` 的 `extra_hits`）。

# 指標 A 專用：只有社群類 kind（social/sentiment）才納入模板相似度比對，見下方
# codex 對抗審修正說明。客觀事實類（price/onchain/...）本來就該長得像、新聞聯播
# 同一通稿也本來就該長得像，皆非協同操縱訊號，一律不比對。
_TEMPLATE_ELIGIBLE_KINDS = frozenset({"social", "sentiment"})
# codex 對抗審 [HIGH]：3 家新聞逐字/近似轉載同一通稿（kind=news，先前非豁免）
# 曾被誤標協同操縱——確定性相似度分數本身分不清「合法通稿聯播」與「協同
# 灌水」，只能靠 kind 收斂。協同操縱灌水本質上是社群現象（telegram/reddit/
# twitter 群），新聞聯播、官方/監管公告高相似是正常。改用**允許清單**（而非
# 持續加長的豁免清單）：只有 social/sentiment 才納入模板比對，news/
# regulatory/price/price_live/onchain/hoyabit 全數不比對，未來新增的 kind
# 預設也不納入（更安全，不必每次都記得補豁免清單）。

_TEMPLATE_JACCARD_THRESHOLD = 0.8  # 指標 A：模板相似度門檻（比 _corroboration 的 0.4 嚴格得多）
_TEMPLATE_MIN_SOURCES = 3          # 指標 A：至少涉及幾個不同來源才觸發

_BURST_WINDOW_SEC = 3600.0         # 指標 B：爆量偵測窗口（60 分鐘）
_BURST_RATIO = 3.0                 # 指標 B：單源窗口內主張數 > 全池同窗口中位數的幾倍才觸發


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard 相似度：|交集| / |聯集|。任一邊為空集合視為不相似（0.0），避免除以 0。"""
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def _coordination_template_flags(all_claims: list[Claim]) -> dict[str, list[str]]:
    """W3 指標 A：模板化文字相似偵測（informational-only，不扣信任分）。

    同議題跨**不同來源**兩兩比對（沿用既有 `_normalize` 去 `DOMAIN_STOP` 後的
    token 集），門檻拉高到 `_TEMPLATE_JACCARD_THRESHOLD`（0.8）——遠比
    `_corroboration` 判斷「同主題可佐證」用的 0.4 嚴格，0.8 代表「近乎逐字/
    近義詞置換」的模板化文字，不是單純同主題。命中且涉及
    `_TEMPLATE_MIN_SOURCES`（3）個以上獨立來源才觸發，回傳可回溯 flag
    （含涉入來源清單與最高 Jaccard 值，供 `Evidence.info_flags` 顯示）。

    **codex 對抗審確認的根本限制、CEO 定案**：文字相似度在任何 kind 下都無法
    單獨區分「協同操縱」vs「合法聯播/引用」（3 家新聞轉載同一通稿也會是高
    Jaccard），自動扣信任分必然誤傷合法聯播。因此本指標**只做透明化的
    informational flag、不再併入 `_manipulation_penalty` 的 `extra_hits`**
    （見 `_coordination_signals` docstring），措辭刻意用中性的「資訊:」前綴，
    不用「協同:」等指控字眼——單靠相似度分數不足以自動判定操縱，留給人工
    判讀。

    **#16 修正（CEO 定案：相似簇 flag 須傳播到全體成員，不能只標 hub）**：
    高於門檻的「相似邊」（不同來源、Jaccard ≥ 0.8）先用 union-find 併成
    「相似簇」（一個連通分量），涉入來源數以**整個簇**計算，只要簇達
    `_TEMPLATE_MIN_SOURCES` 就對**簇內每一個成員**都掛 flag，而不是各自只看
    「自己」直接相鄰的來源數。修正前的缺陷（星狀相似拓樸）：hub 對 3 個
    spoke 都高度相似（各自來源都不同），但 spoke 彼此互不相似（低於門檻）
    ——hub 局部視角能看到 hub+3 spoke＝4 個來源、達標；每個 spoke 局部只看
    得到「自己＋hub」＝2 個來源、未達 `_TEMPLATE_MIN_SOURCES`，導致同一個
    貨真價實橫跨 4 個來源的相似簇裡，只有 hub 被標記、3 個 spoke 全部漏標，
    判審看不到完整的簇。改用連通分量後，簇內每個成員都用**同一份、涵蓋
    整簇的來源清單**顯示（讓判審看到整個簇的全貌），但 Jaccard 數值維持
    **各自誠實回報**——每個成員仍顯示「自己」跟簇內某個直接相鄰成員的最高
    Jaccard，不是隨便套用一個簇級平均值或別人的數字。

    防呆：
    - **只有 `_TEMPLATE_ELIGIBLE_KINDS`（social/sentiment）納入比對**——
      news/regulatory/price/price_live/onchain/hoyabit 全數跳過。理由同上：
      新聞聯播同一份通稿、官方/監管公告本來就該高度相似，此防呆進一步降低
      informational flag 的雜訊量（即使不扣分，也不該對合法聯播灌一堆
      無意義的相似度提示）。
    - 需 ≥3 個獨立來源才觸發：2 家媒體/帳號逐字轉載同一份文本（只有 2 個
      來源）不觸發，避免雜訊。
    """
    eligible = [c for c in all_claims if c.doc.kind in _TEMPLATE_ELIGIBLE_KINDS]
    tokens = {c.id: (_normalize(c.text) - DOMAIN_STOP) for c in eligible}
    n = len(eligible)

    # Union-find：把「兩兩 Jaccard ≥ 門檻」的邊併成相似簇，讓「涉入來源數」
    # 以整簇計算，而非各自局部相鄰來源數（見上方 docstring #16 修正說明）。
    parent = list(range(n))

    def _find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(a: int, b: int) -> None:
        ra, rb = _find(a), _find(b)
        if ra != rb:
            # 無 rank/size 優化的最簡單合併規則（固定把較大 root 併入較小
            # root）：資料規模與既有 O(n²) 比對同級，不需要額外優化，維持
            # 邏輯最簡單、行為與輸入順序無關（確定性）。
            if ra < rb:
                parent[rb] = ra
            else:
                parent[ra] = rb

    # 每個節點自己的「跟哪個其他來源最像、像多少」——維持既有語意：各自
    # 誠實回報自己跟簇內某個直接相鄰成員的最高相似度，不套用簇級數字。
    best_by_node: list[dict[str, float]] = [dict() for _ in range(n)]

    for i in range(n):
        t1 = tokens[eligible[i].id]
        if not t1:
            continue
        for j in range(i + 1, n):
            if eligible[i].doc.source == eligible[j].doc.source:
                continue
            t2 = tokens[eligible[j].id]
            if not t2:
                continue
            j_score = _jaccard(t1, t2)
            if j_score < _TEMPLATE_JACCARD_THRESHOLD:
                continue
            _union(i, j)
            src_i, src_j = eligible[i].doc.source, eligible[j].doc.source
            if j_score > best_by_node[i].get(src_j, 0.0):
                best_by_node[i][src_j] = j_score
            if j_score > best_by_node[j].get(src_i, 0.0):
                best_by_node[j][src_i] = j_score

    # 依 root 分組成相似簇，每簇算出涉入的所有來源（整簇範圍，非局部鄰居）。
    components: dict[int, list[int]] = {}
    for i in range(n):
        components.setdefault(_find(i), []).append(i)

    flags: dict[str, list[str]] = {}
    for idxs in components.values():
        sources = {eligible[i].doc.source for i in idxs}
        if len(sources) < _TEMPLATE_MIN_SOURCES:
            continue
        source_list = ",".join(sorted(sources))
        for i in idxs:
            if not best_by_node[i]:
                # 理論上不會發生：能進入 size ≥ _TEMPLATE_MIN_SOURCES 的簇，
                # 代表該節點至少有一條滿足門檻的邊，best_by_node 必非空。
                continue
            best_j = max(best_by_node[i].values())
            flags.setdefault(eligible[i].id, []).append(
                f"資訊:多源文字高度相似(來源{source_list};Jaccard {best_j:.2f})"
                "—可能協同或聯播,供判讀"
            )
    return flags


def _max_distinct_in_rolling_window(
    ordered: list[Claim], window_sec: float
) -> tuple[int, list[Claim]]:
    """在依 `(ts, id)` 遞增排序好的同一來源 claims 上，用雙指標(two-pointer)找出
    **任一**長度 `window_sec` 的滾動視窗內，相異 `c.text` 數的最大值，以及達成
    該最大值的其中一組 claims（原始 claim 物件，含重複文本，供回溯/flag 用）。

    真正的滑動窗（視窗邊界＝實際 ts 差值 < window_sec），不是固定牆鐘分桶——
    避免爆量橫跨整點（如 xx:59~xx+1:01）被切成兩個低於門檻的子群、給攻擊者
    鑽空子（codex 對抗審 [MEDIUM] 修正）。

    確定性：多個視窗打平時取「最早出現」的那個（`right` 由左至右遞增掃描、
    `distinct > best` 用嚴格大於，先找到的視窗不會被同分的後來者覆蓋）；
    呼叫端已用 `(ts, id)` 排序保證輸入順序本身不受 `PYTHONHASHSEED` 影響。
    """
    n = len(ordered)
    if n == 0:
        return 0, []
    freq: dict[str, int] = {}
    distinct = 0
    best = 0
    best_range = (0, 0)
    left = 0
    for right in range(n):
        t = ordered[right].text
        if freq.get(t, 0) == 0:
            distinct += 1
        freq[t] = freq.get(t, 0) + 1
        while ordered[right].doc.ts - ordered[left].doc.ts >= window_sec:
            lt = ordered[left].text
            freq[lt] -= 1
            if freq[lt] == 0:
                distinct -= 1
            left += 1
        if distinct > best:
            best = distinct
            best_range = (left, right + 1)
    return best, ordered[best_range[0]:best_range[1]]


def _distinct_text_count_in_range(
    ordered: list[Claim], start_ts: float, end_ts: float
) -> int:
    """在依 ts 排序好的 claims 上，數出 `doc.ts ∈ [start_ts, end_ts)` 區間內的
    相異 `c.text` 數（逐字去重）。與 `_max_distinct_in_rolling_window` 的視窗
    定義一致（左閉右開），用於把「候選源爆量的那個具體時段」對齊到其他來源，
    數他們在**同一段時間**內各自發了幾則——而不是他們自己歷史上不相干時段
    的最大值。
    """
    return len({c.text for c in ordered if start_ts <= c.doc.ts < end_ts})


def _coordination_burst_flags(all_claims: list[Claim]) -> dict[str, list[str]]:
    """W3 指標 B：單源爆量偵測（確定性滾動 60 分鐘視窗，同窗對齊比較基準）。

    對每個來源，先各自獨立在其依 ts 排序後的 claims 上用
    `_max_distinct_in_rolling_window` 找出**任一** `_BURST_WINDOW_SEC`（60
    分鐘）滾動視窗內的最大相異文本數（`c.text` 逐字去重——同一段文本重貼
    不算多筆主張，見下方防呆），記為該來源的候選爆量計數 `cnt` 與對應的
    絕對時間區間 `[window_start, window_start + 60min)`。

    判定基準（baseline）：**把候選來源爆量的那個具體時間區間，對齊套用到
    每個其他來源**，各自數他們在同一段時間內發了幾則相異主張——取這些
    「同窗計數」的中位數（leave-one-out：候選自己不計入）× `_BURST_RATIO`
    （3）當門檻，`cnt` 超過才觸發。

    （codex 對抗審 [第 3 個 HIGH] 修正：先前 baseline 誤用「每個來源自己
    歷史上的最大滾動窗計數」，即使該最大值發生在跟候選完全不相干的時段。
    例：候選現在爆 8 則，另一來源 3 小時前也曾自己爆過 8 則、但在候選爆量
    的當下窗口內其實只發了 0～1 則──用「別人歷史最大」當分母會讓
    `8 ≤ 3×8` 不觸發，即使其他來源在候選爆量當下其實毫無動靜。改為
    「同一時段大家多活躍」而非「別人歷史多活躍」，才是正確的協同異常
    比較基準。）

    （codex 對抗審 [HIGH #1] 修正仍保留：中位數排除候選自己再算——2 來源
    灌水/正常情境下，若中位數誤含候選自己會造成數學上無法觸發。）

    防呆：
    - 以 `c.text` 逐字去重後才計數。
    - 整個資料池只有 1 個來源時（無其他來源可比較）整批跳過。
    - 同窗中位數 ≤ 0（其餘來源在候選爆量當下同窗內完全沒有主張）時保守
      跳過不觸發——刻意不讓「基準為 0」讓任何 ≥1 則主張都被判定爆量
      （避免把單一正常來源在安靜窗口內僅發 1 則就誤判為爆量；已知
      的保守取捨，若候選源同時真的爆量且其餘來源在同窗內至少有
      ≥1 則活動，中位數 ≥1 即可正常觸發）。
    """
    claims_by_source: dict[str, list[Claim]] = {}
    for c in all_claims:
        claims_by_source.setdefault(c.doc.source, []).append(c)

    if len(claims_by_source) < 2:
        return {}

    ordered_by_source: dict[str, list[Claim]] = {
        source: sorted(s_claims, key=lambda c: (c.doc.ts, c.id))
        for source, s_claims in claims_by_source.items()
    }

    max_count: dict[str, int] = {}
    max_window: dict[str, list[Claim]] = {}
    window_bounds: dict[str, tuple[float, float]] = {}
    for source, ordered in ordered_by_source.items():
        best, window_claims = _max_distinct_in_rolling_window(ordered, _BURST_WINDOW_SEC)
        max_count[source] = best
        max_window[source] = window_claims
        start_ts = window_claims[0].doc.ts if window_claims else ordered[0].doc.ts
        window_bounds[source] = (start_ts, start_ts + _BURST_WINDOW_SEC)

    flags: dict[str, list[str]] = {}
    window_min = int(_BURST_WINDOW_SEC // 60)
    for source, cnt in max_count.items():
        if cnt <= 0:
            continue
        start_ts, end_ts = window_bounds[source]
        aligned = sorted(
            _distinct_text_count_in_range(ordered, start_ts, end_ts)
            for other_source, ordered in ordered_by_source.items()
            if other_source != source
        )
        mid = len(aligned) // 2
        median = aligned[mid] if len(aligned) % 2 else (aligned[mid - 1] + aligned[mid]) / 2.0
        if median <= 0 or cnt <= median * _BURST_RATIO:
            continue
        for c in max_window[source]:
            flags.setdefault(c.id, []).append(
                f"協同:單源爆量(來源{source};{window_min}分鐘內{cnt}則相異主張,"
                f"同窗對照其餘來源中位數{median:g}的{cnt / median:.1f}倍)"
            )
    return flags


def _coordination_signals(all_claims: list[Claim]) -> dict[str, list[str]]:
    """W3：確定性、免 LLM 的協同操縱偵測總入口。

    目前只整合指標 A（模板相似，`_coordination_template_flags`），對
    `all_claims` 只算一次——O(n²)，量級同 `_corroboration`（`score()` 對每個
    claim 都要跟其他所有 claims 比對一次），不新增預算風險。

    指標 B（單源爆量，`_coordination_burst_flags`）**降級為 follow-up
    #15，目前不啟用**：經 4 輪 codex 對抗審（中位數自含候選自己、固定牆鐘
    分桶可繞、baseline 未對齊候選窗口、只評估各源「最大窗」漏掉後續同窗
    baseline 偏低的小爆量），仍持續挖出新的 subtle 檢測缺陷，屬於
    per-window anomaly 統計上需要更嚴謹重新設計的問題，不宜無限打磨後
    倉促上線。`_coordination_burst_flags` / `_max_distinct_in_rolling_window`
    / `_distinct_text_count_in_range` 程式碼保留（不刪），供 #15 重新設計時
    直接沿用/參考，但呼叫端刻意不接。

    **CTO 複查（#16 同批任務，重新 grep + 實跑驗證）**：前 3 個已修正的缺陷
    （中位數自含候選、固定牆鐘分桶、baseline 未對齊）在目前程式碼中確認
    已修好（見 `_max_distinct_in_rolling_window`／`_coordination_burst_flags`
    docstring 內的修正說明與對應測試）。但第 4 個「只評估各源『最大窗』漏
    掉後續同窗 baseline 偏低的小爆量」**仍未修**，並已用可執行的回歸測試
    重新複現（`test_w3_b_max_absolute_window_can_miss_smaller_higher_ratio_
    window`）：`_max_distinct_in_rolling_window` 只在「絕對數量最大」的單一
    視窗上算 ratio，若某個絕對數量較小、但相對當下 baseline 更異常的視窗
    沒被選中，就完全不會被評估到。要修好需要對每個來源在**所有**候選視窗
    位置（而非單一絕對最大視窗）都評估 ratio、取全域最大 ratio——這是比
    現有「先選單一視窗、後算 baseline」更大幅度的多來源同步滑動視窗演算法
    重新設計，還需要額外的最小絕對數量下限（避免極小樣本數的極端比值誤
    觸發）與對應的新一輪對抗測試，不是本次「改動最小」範圍內能完成、也
    不宜為了上線而端出一個已知仍有缺口的假指標（見 #24：資訊訊號寧可少、
    不可濫）。**維持停用**，本輪不啟用、不動 `_coordination_signals` 呼叫端。

    回傳 `{claim_id: [flag, ...]}`；未命中的 claim 不會出現在 dict 中，呼叫端
    用 `.get(claim.id, [])` 取用。

    **CEO 定案（codex 對抗審確認根本限制）：informational-only，不扣信任
    分。** 文字相似度在任何 kind 下都無法單獨區分「協同操縱」vs「合法聯播/
    引用」（3 家新聞轉載同一份官方通稿也會是高 Jaccard），自動扣分必然誤傷
    合法聯播。命中結果**不**併入 `_manipulation_penalty` 的 `extra_hits`、
    **不**降低 trust、**不**影響動態信譽（`_iterate_source_reputation`）；
    純粹併入 `ScoredClaim.info_flags` → `Evidence.info_flags`，供人工判讀
    （如「資訊:多源文字高度相似(來源a,b,c;Jaccard 0.85)—可能協同或聯播,
    供判讀」，措辭刻意中性，不用「協同:」等指控字眼）。

    與此互斥、維持原行為不變的是 `_manipulation_flags`（regex 關鍵詞命中）：
    那是既有的、獨立的操縱偵測機制，仍正常扣分 + 紅旗🚩，不受本次
    informational-only 改動影響。
    """
    signals: dict[str, list[str]] = {}
    for cid, fl in _coordination_template_flags(all_claims).items():
        signals.setdefault(cid, []).extend(fl)
    # W3 burst 指標降級 follow-up #15：per-window anomaly 需正確重設計，暫不啟用。
    # for cid, fl in _coordination_burst_flags(all_claims).items():
    #     signals.setdefault(cid, []).extend(fl)
    return signals


class _StanceBudget:
    """CEO/codex 對抗審修正：單次 `score()` 執行共用的 stance 呼叫預算。

    O(n²) 迴圈（每個 claim 都要跟其他所有 claims 比對）在高重疊 claims 的情境下，
    最壞會產生 n(n-1)/2 次 stance 呼叫；沒有上限會爆 credit、也可能吃光官方 15
    分鐘執行窗口（命題第一號失敗模式：報告生不出來）。

    這是一個跨所有 `_corroboration()` 呼叫共享的可變計數器（`score()` 建立一個
    實例，傳給每一次 `_corroboration()`），同時檢查：
    1. 配對硬上限（`max_pairs`，用完就不再呼叫）。
    2. 選用的即時剩餘時間（`remaining_time_fn`，通常是 `ExecutionLog.remaining`
       的 bound method）——低於 `STANCE_TIME_RESERVE_SEC` 秒也視為耗盡。

    額度/時間耗盡時，呼叫端（`_corroboration`）應 fail-safe 降級為 "neutral"
    （不呼叫、不 raise、不錯殺既有佐證）。
    """

    def __init__(
        self,
        max_pairs: int = DEFAULT_STANCE_PAIR_BUDGET,
        remaining_time_fn: Callable[[], float] | None = None,
    ):
        self._remaining_pairs = max_pairs
        self._remaining_time_fn = remaining_time_fn

    def take(self) -> bool:
        """嘗試消耗一次配額。回 False 代表額度或時間已耗盡，呼叫端不應再呼叫
        stance_fn，應直接降級為 "neutral"。"""
        if self._remaining_pairs <= 0:
            return False
        if (
            self._remaining_time_fn is not None
            and self._remaining_time_fn() <= STANCE_TIME_RESERVE_SEC
        ):
            return False
        self._remaining_pairs -= 1
        return True


def _directional_word_polarities(text: str) -> tuple[set[str], set[str]]:
    """回傳 (asserted, negated) 兩組方向詞，供交叉佐證的否定閘（#4）使用。

    - asserted：出現且「未被否定」的方向詞（如「上漲」）。
    - negated：出現且「被否定」的方向詞（如「不會上漲」→ 上漲 入 negated）。

    用途：同一方向詞一方 asserted、另一方 negated → 語意對立（「X 上漲」vs
    「X 不會上漲」），即便 `_infer_direction` 因否定把被否定那方判成 neutral、
    通過 `_direction_compatible` 快路徑，也不該被誤計為獨立佐證。

    最長優先去重（複用 `_infer_direction` 邏輯，issue #142）：先收集所有候選
    (start, end, word)，依詞長降序、同長度依位置升序排序，短詞若落在已消耗區間
    （即它是某個更長方向詞的子字串，如「漲」⊂「上漲」）則不計——避免子串交叉
    誤殺真正同向佐證（under-corroboration）。否定閘仍逐詞判定。
    """
    candidates: list[tuple[int, int, str]] = []
    for w in _BULLISH_WORDS + _BEARISH_WORDS:
        for m in re.finditer(re.escape(w), text):
            candidates.append((m.start(), m.end(), w))

    # 最長優先（與 `_infer_direction` 同款排序），短詞子串不重複計
    candidates.sort(key=lambda x: (-(x[1] - x[0]), x[0]))

    consumed: list[tuple[int, int]] = []
    def _overlaps(s: int, e: int) -> bool:
        return any(s < ce and e > cs for cs, ce in consumed)

    kept: list[tuple[int, int, str]] = []
    for start, end, w in candidates:
        if _overlaps(start, end):
            continue
        consumed.append((start, end))
        kept.append((start, end, w))

    asserted: set[str] = set()
    negated: set[str] = set()
    for start, end, w in kept:
        if _NEG_RX.search(text[max(0, start - 4): start]):
            negated.add(w)
        else:
            asserted.add(w)
    return asserted, negated


def _corroboration_detail(
    target: Claim,
    all_claims: list[Claim],
    stance_fn: Callable[[str, str], str] | None = None,
    require_entailment: bool = False,
) -> tuple[set[str], set[str]]:
    """`_corroboration()` 核心迴圈抽出版，回傳 `(independent_sources, contradicting_sources)`。

    W2（#動態信譽）新增：獨立佐證迴圈本身完全不變（逐字保留原順序/判斷，確保
    `_corroboration()` 的回傳值 byte-identical），只是額外把「W1.5 stance 判定為
    contradiction」的來源也收進第二個集合——這是既有迴圈裡本來就會算出來的資訊
    （只是舊版直接丟棄），現在同一次迴圈順便記下來，**不新增任何 stance_fn 呼叫**。

    供 `_corroboration()`（沿用原本行為）與 `_iterate_source_reputation()`（W2
    agreement 訊號）共用同一次 overlap/方向/stance 判斷結果。

    codex 對抗審 [HIGH，#24]：`require_entailment=False`（**預設**，`_corroboration()`
    既有分項專用）沿用原本語意——`stance_fn` 只用來「排除」明確矛盾，overlap+方向
    閘通過、且非 "contradiction" 的一律算獨立佐證（`entailment` 或 `neutral` 皆算，
    這是既有純文字重疊式「corroboration 分項」一路以來的設計，非本次修正範圍）。

    `require_entailment=True`（**W2 動態信譽專用**，`_reputation_evidence()` 呼叫）：
    truth-discovery 的「動態信譽」語意上要求真語意驗證，"neutral" 在這裡**不是**
    「經查證為中立」，而是 `cached_stance_fn` 的萬用 fail-safe 值——離線 / 未設模型
    / timeout / malformed 回應 / cache miss 又無可用 client / stance 預算或時間
    耗盡，全部無差別回傳 "neutral"（見 `stance_cache.py`/`bedrock.py` 對應
    docstring）。若沿用 `require_entailment=False` 的「非 contradiction 即佐證」，
    生產預設 `llm_mode=off` 時幾乎所有配對都會落在這個 fail-safe neutral，等同
    「沒有真的做過語意驗證，就把信譽當作已驗證佐證來調整」——直接違反 #24（假訊號
    當真）。修法：`require_entailment=True` 時，只有 `stance_fn` 明確回傳
    `"entailment"`（真的呼叫了分類器且判定為真語意蘊含）才計入
    `independent_sources`；`"neutral"`（不論是分類器真判定為中立，或任何一種
    fail-safe 降級——回傳值層級無法區分兩者，一律保守排除，不猜測）與
    `stance_fn is None`（完全沒有可用的分類器）**都不計入任何一個集合**，也
    不會進入 `_iterate_source_reputation` 的 `MIN_INDEPENDENT_EVIDENCE` 小樣本
    守門分母——不採信，也不當它「什麼都沒發生」去湊樣本數。`"contradiction"`
    不受影響：`cached_stance_fn` 的 fail-safe 只會降級成 `"neutral"`，絕不會
    無中生有出一個 `"contradiction"`（見該函式 docstring），因此
    `"contradiction"` 只可能來自真正跑成功（或先前持久化快取過）的分類結果，
    是已驗證的真訊號，兩種模式下都照樣計入 `contradicting_sources`。
    """
    tt = _normalize(target.text) - DOMAIN_STOP
    independent_sources: set[str] = set()
    contradicting_sources: set[str] = set()
    if not tt:
        return independent_sources, contradicting_sources
    # issue #72 / #132：同源排除與「已計入來源」去重都用 canonical key，
    # 否則同一來源的大小寫/空白變體（如 `"CoinDesk"` vs `" coindesk "`）會被
    # 誤判成不同來源，讓同源轉發/重複發文灌水成多個「獨立佐證」。
    target_key = _canonical_source(target.doc.source)
    for c in all_claims:
        c_key = _canonical_source(c.doc.source)
        if c_key == target_key:
            continue
        if c_key in independent_sources:
            continue
        ct = _normalize(c.text) - DOMAIN_STOP
        inter = len(tt & ct)
        if not inter:
            continue
        if inter / len(tt) < 0.4:
            continue
        if not _direction_compatible(target.direction, c.direction):
            continue
        # issue #4 否定詞語意偵測：同一方向詞一方 asserted、另一方 negated →
        # 語意對立（如「BTC 上漲」vs「BTC 不會上漲」），即使被否定方因
        # `_infer_direction` 判成 neutral、通過上方方向閘，也不計為獨立佐證。
        tgt_asserted, tgt_negated = _directional_word_polarities(target.text)
        cand_asserted, cand_negated = _directional_word_polarities(c.text)
        if (tgt_asserted & cand_negated) or (cand_asserted & tgt_negated):
            continue
        if stance_fn is None:
            if require_entailment:
                # W2：沒有可用的分類器，無法驗證語意——保守排除，不當佐證。
                continue
            independent_sources.add(c_key)
            continue
        label = stance_fn(target.text, c.text)
        if label == "contradiction":
            contradicting_sources.add(c_key)
            continue
        if require_entailment:
            if label == "entailment":
                independent_sources.add(c_key)
            # "neutral"（genuine 或 fail-safe，無法區分）：W2 不採信，兩個集合都不進。
            continue
        independent_sources.add(c_key)
    return independent_sources, contradicting_sources


def _corroboration(
    target: Claim,
    all_claims: list[Claim],
    stance_fn: Callable[[str, str], str] | None = None,
) -> float:
    """有多少**獨立來源**（不同 source）提到相似主張。回音室（同源轉發）不加分。

    改進（M1-M3）：
    - 停用詞過濾：從 overlap 計算排除域內通用詞（幣名/市場詞），只計具體詞重疊。
    - 方向閘：若兩條主張方向明確且相反（bullish vs bearish），略過，不算佐證。

    W1.5（#15）：加選用 stance_fn，對通過 overlap 前置閘 + 方向閘的候選再做一次
    語意 stance 分類，偵測「表面詞重疊但實質矛盾」（例如 regulatory clarity/adoption
    vs regulatory scrutiny/caution：方向詞未必明確相反，但語意明確對立）。

    順序（控成本，越前面越便宜）：
    0. 該來源已計入 independent_sources → 直接跳過（CEO/codex 對抗審修正：同一
       來源已經算過，再花一次 overlap/方向/stance 判斷不會改變結果，純屬冗餘
       呼叫，尤其在高重疊 claims 情境下能砍掉大量 stance 呼叫）。
    1. 同源排除（不變）。
    2. overlap>=0.4 前置閘（不變）——先過最便宜的集合運算。
    3. `_direction_compatible` 明確衝突快路徑（不變）——省下不必要的 stance 呼叫。
    4. 若 `stance_fn` 存在才呼叫（走快取）；回傳 "contradiction" 則不計入獨立佐證。

    第 3 輪對抗審修正：呼叫預算（配對硬上限 + 時間預算）**不在這裡管**，而是移進
    `stance_cache.py::cached_stance_fn` 內部——只在確認 cache miss、即將真正打
    Bedrock 時才消耗預算；免費的 cache-hit 不吃預算（否則大量 cache-hit 會把全域
    預算耗盡，讓快取裡真正的 contradiction 反而被跳過檢查、錯判為佐證）。這裡只
    單純呼叫 `stance_fn`，預算耗盡與否對這層完全透明。

    `stance_fn=None` 時完全略過第 4 步，行為與加入 W1.5 前逐字相同（向後相容）。

    W2：內部改呼叫 `_corroboration_detail()`，迴圈邏輯逐字不變，僅為 W2 動態信譽
    抽出共用；本函式回傳值不受影響（見 `_corroboration_detail` docstring）。
    """
    independent_sources, _contradicting = _corroboration_detail(
        target, all_claims, stance_fn=stance_fn
    )
    # 1 個獨立佐證→0.5，2 個→0.79，飽和到 1.0
    n = len(independent_sources)
    return 1.0 - math.pow(0.5, n) if n else 0.0


# --- W2：truth-discovery 動態來源信譽 -------------------------------------
# CEO 核准 gray 計劃 + 3 輪 refinement：bounded 迭代、無隨機性、成本不放大（不得因
# 迭代輪數重呼叫 stance_fn）、小樣本守門、每輪 clamp 防蒸發。
DEFAULT_REPUTATION_ITERATIONS = 3
MAX_REPUTATION_ITERATIONS = 5           # 硬上限，即使呼叫端傳更大值也不放行
REPUTATION_CONVERGENCE_EPS = 0.01       # max|SR^t - SR^(t-1)| < eps 提早停
DEFAULT_REPUTATION_ALPHA = 0.55         # SR^t = α·SR⁰ + (1-α)·agreement_score
MIN_INDEPENDENT_EVIDENCE = 3            # 獨立佐證+矛盾來源聯集 < 3 → 該 source 強制 α=1
DS_MIN_RATERS_PER_ITEM = 2               # DS EM：每 item 至少幾個 rater 才納入可靠度估計


def _stable_sigmoid(x: float, clamp: float = 30.0) -> float:
    """數值穩定 sigmoid：呼叫 `math.exp` 前把 logit clamp 到 `[-clamp, clamp]`。

    codex 對抗審 [HIGH-2] 修正：`net`（agreement 淨值）理論上可能被推到極端值，
    若不設防，`math.exp(-net)` 在 `|net|` 超過浮點指數上限（約 709）時會丟出
    `OverflowError`，讓 `score(dynamic_reputation=True)` 直接當掉。clamp=30 時
    sigmoid 早已飽和到 1e-13 等級、精度損失可忽略不計，純粹是防禦性上限——
    搭配 [HIGH-1]（agreement 按唯一佐證/矛盾來源去重後才加總，見
    `_iterate_source_reputation` 內的 `agree_union_of`/`contra_union_of`）雙重
    保險：正常情境下去重後的 net 本身就有界，這裡的 clamp 是最後一道防線。
    """
    xc = max(-clamp, min(clamp, x))
    return 1.0 / (1.0 + math.exp(-xc))


def _reputation_evidence(
    claims: list[Claim],
    stance_fn: Callable[[str, str], str] | None = None,
) -> dict[str, tuple[set[str], set[str]]]:
    """`{claim.id: (agree_sources, contradict_sources)}`，只算一次（每個 claim 各跑一次
    `_corroboration_detail`），供 `_iterate_source_reputation` 的每一輪迭代與
    `score()` 的 `reputation_trace` 共用——**迭代輪數 K 不會讓這裡的 stance_fn 呼叫變多**
    （K 輪只重算 SR 混合權重，不重跑 overlap/方向/stance 判斷）。

    codex 對抗審 [HIGH，#24] 修正：`require_entailment=True`——動態信譽只認真語意
    `entailment`，`"neutral"`（含離線/timeout/malformed/cache miss/預算耗盡等
    fail-safe 降級，回傳值層級無法區分）與無可用分類器一律不計入 `agree_sources`
    （也不進 `contradict_sources`），見 `_corroboration_detail` docstring 完整
    理由。`_corroboration()`（既有非 W2 分項）不受影響，仍用預設
    `require_entailment=False`。
    """
    return {
        c.id: _corroboration_detail(c, claims, stance_fn=stance_fn, require_entailment=True)
        for c in claims
    }


def _iterate_source_reputation(
    claims: list[Claim],
    now: float,
    weights: dict | None = None,
    stance_fn: Callable[[str, str], str] | None = None,
    iterations: int = DEFAULT_REPUTATION_ITERATIONS,
    alpha: float = DEFAULT_REPUTATION_ALPHA,
    evidence: dict[str, tuple[set[str], set[str]]] | None = None,
    trace_out: dict | None = None,
    offline: bool = False,
) -> dict[str, float]:
    """W2：bounded 迭代動態來源信譽。純函式、無隨機性 → 同輸入必同輸出。

    W3 協同操縱指標（`_coordination_signals`）**不參與這裡的 manip 計算**：
    CEO 定案（codex 對抗審確認根本限制）改為 informational-only，只產生
    `ScoredClaim.info_flags`，不併入任何 `_manipulation_penalty` 的
    `extra_hits`，因此動態信譽的 `static_manip` 也不受 W3 訊號影響（純粹
    只看 `_manip_hits` 既有 regex 關鍵詞命中）。

    實作偏離 gray 計劃字面簽章之處（皆為必要、非隱藏的工程判斷，詳見 PR 說明）：
    - 加了 `now`：`_recency_decay` 需要，計劃描述省略。
    - 加了選用的 `evidence`/`trace_out`：避免 `score()` 為了 trace 再重跑一次
      `_corroboration_detail`（見下方 `evidence` 說明）；不影響核心演算法語意。

    先驗 SR⁰(source)：沿用現行 `_source_reputation()`（KIND_REPUTATION / doc 覆寫），
    逐 source 取 `claims` 出現順序第一筆（同一來源理論上 kind 一致，deterministic）。

    每輪 t = 1..K：
      Step A：用「當前」SR（t-1 輪結果，第一輪即 SR⁰）取代固定 kind 權重，重算每條
              claim 的暫時 trust（`_source_reputation(c, dynamic_map=prev)` + 既有
              `_corroboration`/`_recency_decay`/`_manipulation_penalty` 分項——後三者
              是靜態值，全程只算一次、跨輪重用，不因迭代重複計算）。
      Step B：`SR^t(source) = α·SR⁰(source) + (1-α)·agreement_score(source)`。
              `agreement_score` 由該 source 名下所有 claim 的獨立佐證/矛盾來源
              **聯集去重後**（`agree_union_of`/`contra_union_of`，全程只算一次）
              按其「暫時 trust」加權：一致來源 +其暫時 trust、W1.5 stance 判矛盾
              來源 -其暫時 trust，加總後以 `_stable_sigmoid` 正規化到 0–1（無任何
              佐證/矛盾時 net=0 → 0.5，中性，不偏袒也不懲罰）。
              自家來源的 claim 不會給自己投票（`_corroboration_detail` 本就排除
              同源），單一來源灌水灌再多自家 claims 也不會自抬信譽（需要「其他」
              獨立來源真的來佐證才有效——複用既有反回音室設計）。

    codex 對抗審修正（HIGH-1/HIGH-2/第 2 輪 HIGH，PR #29 review）：
    - **[HIGH-1]** agreement 投票按「唯一佐證/矛盾來源」聯集去重後才加總一次，
      不隨該 source 名下 claim 數量重複計票——原本若把「已有 3 個外部佐證的
      claim」重複貼 N 次，會重用同一批佐證來源疊加 N 次、把 `agreement_score`
      推向飽和，繞過反暴走。去重後，claim 重複次數**不能**放大票數。
    - **[HIGH-2]** `_stable_sigmoid` 在 `math.exp` 前把 logit clamp 到安全範圍
      （預設 ±30），杜絕 net 極端值時的 `OverflowError`；配合 HIGH-1 去重後
      net 本身已有界，這是雙保險而非唯一防線。
    - **[第 2 輪 HIGH]** HIGH-1 去重的是「佐證/矛盾來源的身分」，但 `avg_temp_by_source`
      （某來源投給其他來源的「票權」）原本仍對該來源**全部 claims**（含重複）取
      平均——攻擊者可重複貼自己「最高 trust」的那條 claim 拉高自己的平均票權，
      再透過跨輪互證回饋間接墊高自己或共謀來源的信譽（同文本、同 trust 的重複
      測試測不出，需異質 trust 的 claim 才會暴露）。修正：`unique_claims_by_source`
      先以 `claim.text` 逐字去重（同文本只留第一次出現那筆），`avg_temp_by_source`
      只對這份去重後的「內容不同的主張種類」取平均——不變量：對任一來源重複其
      任意 claim（含高/低 trust 混合）N 次，**所有來源在第 1–5 輪的最終 SR 完全
      不變**（見對應測試）。

    小樣本守門（CEO refinement #1）：某 source 名下所有 claim 的獨立佐證+矛盾來源
    **聯集（去重後）** < `MIN_INDEPENDENT_EVIDENCE`（3）時，該 source 強制 α=1
    （等同純先驗、完全不受 agreement 影響），避免少樣本佐證/矛盾把信譽炒到失真。

    每輪 clamp 到 `[_reputation_floor(kind), 1.0]`，防止信譽蒸發到 0（見
    `_reputation_floor`）。收斂：`max|SR^t - SR^(t-1)| < REPUTATION_CONVERGENCE_EPS`
    提早停；`iterations` 內部 clamp 到 `[1, MAX_REPUTATION_ITERATIONS]`，即使呼叫端
    傳更大值也不會真的多跑。

    已知限制（#17 同源別名，本 W2 不解）：獨立性 key 沿用 `doc.source` 字面值，
    同一實體用不同帳號/別名發文會被當成多個「獨立」來源，可能被灌水墊高
    agreement_score——與既有 `_corroboration` 的既有限制一致，非 W2 新引入。
    """
    n_iter = max(1, min(int(iterations), MAX_REPUTATION_ITERATIONS))
    w = weights or DEFAULT_WEIGHTS

    if not claims:
        if trace_out is not None:
            trace_out["iterations_run"] = 0
        return {}

    # SR⁰ 與每 source 的代表 kind（deterministic：claims 出現順序第一筆）
    # issue #72/#132：分組 key 走 canonical source，與 `_corroboration_detail`
    # 回傳的 agree/contra union 口徑一致——否則同源大小寫/空白變體會讓 W2 投票
    # 對不上、動態信譽靜默失效。
    sr0: dict[str, float] = {}
    kind_of: dict[str, str] = {}
    claims_by_source: dict[str, list[Claim]] = {}
    for c in claims:
        s = _canonical_source(c.doc.source)
        if s not in sr0:
            sr0[s] = _source_reputation(c)
            kind_of[s] = c.doc.kind
        claims_by_source.setdefault(s, []).append(c)

    # 靜態分項（不受 SR 影響，全程只算一次；agree/contra 來源集合全程共用，
    # 不因迭代輪數重呼叫 stance_fn）
    ev = evidence if evidence is not None else _reputation_evidence(claims, stance_fn=stance_fn)
    static_corr: dict[str, float] = {}
    static_rec: dict[str, float] = {}
    static_manip: dict[str, float] = {}
    for c in claims:
        agree, _contra = ev.get(c.id, (set(), set()))
        nn = len(agree)
        static_corr[c.id] = 1.0 - math.pow(0.5, nn) if nn else 0.0
        static_rec[c.id] = _recency_decay(c, now)
        static_manip[c.id] = _manipulation_penalty(c)

    # codex 對抗審 [HIGH-1] 修正：先把每個 source 名下所有 claim 的 agree/contra
    # 來源做「聯集去重」（agree_union_of / contra_union_of），後續小樣本守門與
    # Step B 的 agreement 投票都只吃這份去重後的資料——同一來源把「已有 3 個外部
    # 佐證的 claim」重複貼 N 次，去重後對 net 沒有任何額外貢獻（不能靠重複貼文
    # 繞過反暴走、把 logistic 推向飽和）。
    agree_union_of: dict[str, set[str]] = {}
    contra_union_of: dict[str, set[str]] = {}
    for s, s_claims in claims_by_source.items():
        au: set[str] = set()
        cu: set[str] = set()
        for c in s_claims:
            agree, contra = ev.get(c.id, (set(), set()))
            au |= agree
            cu |= contra
        agree_union_of[s] = au
        contra_union_of[s] = cu

    # 小樣本守門：獨立佐證+矛盾來源聯集（去重後）< 3 → 強制 α=1
    alpha_of: dict[str, float] = {
        s: (1.0 if len(agree_union_of[s] | contra_union_of[s]) < MIN_INDEPENDENT_EVIDENCE else alpha)
        for s in claims_by_source
    }

    # -------------------------------------------------------------------------
    # #182 DS EM 離線 fallback 分支
    # -------------------------------------------------------------------------
    # 真實觸發條件（只認「離線 / 語意未驗證」）：`offline=True` **且** 所有 source
    # 的有效 entailment 佐證聯集皆為空（即無任何一筆真語意 `entailment` 流進 W2）。
    # 這與「線上模型真跑但判定 neutral」/`budget 耗盡 fail-safe neutral` 嚴格區分：
    # 後兩者 `offline=False`（有真分類器在），不應降級到 DS、仍維持先驗（回歸鎖）。
    # 只有「根本沒有語意驗證能力」的離線路徑，才改用 DS 對「多源方向標籤的統計
    # 共識」估算可靠度——這是誠實的替代，不是預測力（見 dawid_skene.py 紅線）。
    #
    # DS 分支**不偽造** agree/contra 聯集、不重跑 overlap/方向/stance；直接把每
    # 來源可靠度 r(source) 當作 Step B 的 `agreement_score` 直喂。DS 自備小樣本
    # 守門（來源數<3 或某 item rater<min → 該來源 r=0.5），因此**繞過**線上
    # `MIN_INDEPENDENT_EVIDENCE` 閘（否則離線 DS 會再製 no-op），但仍保留 alpha
    # 預設（不強制 α=1）。
    any_entailment = any(
        (agree_union_of[s] or contra_union_of[s]) for s in claims_by_source
    )
    ds_mode = bool(offline) and not any_entailment
    ds_agree_n: dict[str, int] = {}
    ds_contradict_n: dict[str, int] = {}
    agreement_override: dict[str, float] | None = None
    ds_meta: dict = {}
    if ds_mode:
        # 構造 votes：key=(coin, window)，value={canonical source: direction}
        votes: dict[tuple, dict[str, str]] = {}
        for c in claims:
            coin = _claim_coin(c)
            # 非有限 ts（NaN/inf/0，見 `_recency_decay` 防禦）視為同一 window 0，
            # 不讓 math.floor 對 NaN/inf 炸 OverflowError。
            window = math.floor(c.doc.ts / 86400.0) if (c.doc.ts and math.isfinite(c.doc.ts)) else 0
            key = (coin, window)
            s = _canonical_source(c.doc.source)
            votes.setdefault(key, {})[s] = c.direction
        reliability, _cm, _post, ds_meta = em_source_reliability(
            votes, min_raters_per_item=DS_MIN_RATERS_PER_ITEM
        )
        # 每 item 的參與來源數與多數票方向（確定性：sorted + LABELS 順序平手）
        item_raters: dict[tuple, list[str]] = {}
        item_majority: dict[tuple, str] = {}
        for key, sv in votes.items():
            raters = sorted(sv.keys())
            item_raters[key] = raters
            counts = {lab: 0 for lab in _DS_LABELS}
            for lab in sv.values():
                if lab in counts:
                    counts[lab] += 1
            item_majority[key] = max(_DS_LABELS, key=lambda l: (counts[l], -_DS_LABELS.index(l)))
        # 每 source 的 DS trace 指標：
        #   agree_n       = 該 source 參與的「達標 item」（rater≥min）數
        #   contradict_n  = 該 source 與所屬 item 多數票方向不一致的次數
        for s in claims_by_source:
            ds_agree_n[s] = 0
            ds_contradict_n[s] = 0
        for key, sv in votes.items():
            raters = item_raters[key]
            majority = item_majority[key]
            well_rated = len(raters) >= DS_MIN_RATERS_PER_ITEM
            for s, lab in sv.items():
                if well_rated:
                    ds_agree_n[s] += 1
                if lab != majority:
                    ds_contradict_n[s] += 1
        # r(source) 直喂 Step B 的 agreement_score；退化來源（r=0.5）由下方強制
        # α=1 處理（r=0.5 等價先驗，不動信譽）。
        agreement_override = {s: reliability.get(s, 0.5) for s in claims_by_source}
        ds_fallback = set(ds_meta.get("fallback_sources", []))

    # codex 對抗審修正（第 2 輪 HIGH，PR #29）：`avg_temp_by_source`（該來源投給
    # 其他來源的「票權」）必須以「內容不同的主張種類」為單位平均，不能被同一來源
    # 重複貼同一條 claim（尤其是刻意重貼「自己最高 trust」那條）拉抬——否則即使
    # HIGH-1 已把 agreement 的佐證來源計數去重，攻擊者仍可靠重複高分 claim 墊高
    # 自己的平均票權，透過「先抬互相佐證來源 SR、下輪再回抬自己」的跨輪回饋間接
    # 灌水。以 `claim.text` 逐字去重，同文本只留該來源 claims 中第一次出現那筆
    # （deterministic），使「重複任意 claim N 次」對每輪 SR 完全無影響。
    unique_claims_by_source: dict[str, list[Claim]] = {}
    for s, s_claims in claims_by_source.items():
        seen_text: set[str] = set()
        uniq: list[Claim] = []
        for c in s_claims:
            if c.text in seen_text:
                continue
            seen_text.add(c.text)
            uniq.append(c)
        unique_claims_by_source[s] = uniq

    sr = dict(sr0)
    iterations_run = 0
    for _t in range(n_iter):
        prev = sr
        iterations_run += 1

        # Step A：用當前 SR 取代固定 kind 權重，重算每條 claim 的暫時 trust
        temp_trust: dict[str, float] = {}
        for c in claims:
            rep = _source_reputation(c, dynamic_map=prev)
            raw = (
                w["src"] * rep
                + w["corr"] * static_corr[c.id]
                + w["rec"] * static_rec[c.id]
                - w["manip"] * static_manip[c.id]
            )
            temp_trust[c.id] = max(0.0, min(1.0, raw))

        # 每 source 的平均暫時 trust，供 Step B 當「投票權重」——只取「內容不同的
        # 主張種類」（`unique_claims_by_source`，見上方去重說明），重複貼同一條
        # claim 對這個平均值沒有任何影響。
        avg_temp_by_source: dict[str, float] = {}
        for s, u_claims in unique_claims_by_source.items():
            vals = [temp_trust[c.id] for c in u_claims]
            avg_temp_by_source[s] = sum(vals) / len(vals)

        # Step B：agreement_score → SR^t
        # [HIGH-1] net 只按「唯一佐證/矛盾來源」（agree_union_of/contra_union_of）
        # 加總一次，不隨該 source 名下 claim 數量重複計票；[HIGH-2] `_stable_sigmoid`
        # 對 net 做 clamp，杜絕極端情境下 `math.exp` 溢位崩潰（雙保險：去重後 net
        # 本身也已有界，clamp 是額外防線）。
        #
        # [第 4 輪 HIGH，codex 對抗審] `agree_union_of[s]`/`contra_union_of[s]` 是
        # **set**，其迭代順序取決於元素（字串）的 hash 值，而字串 hash 在不同
        # process 間會被 `PYTHONHASHSEED` 隨機化；加上浮點加法不滿足結合律，同一
        # 輸入在不同 process 可能得到不同的 net（甚至跨過
        # `REPUTATION_CONVERGENCE_EPS`、導致收斂輪數/最終 SR 不同）。修正：
        # 對這兩個 set 一律先 `sorted()` 固定成確定性順序，再用 `math.fsum`
        # （不受加總順序影響的精確加總）取代一般 `sum()`，確保同一輸入在任何
        # process / PYTHONHASHSEED 下都得到逐位元相同的結果。
        new_sr: dict[str, float] = {}
        for s in claims_by_source:
            if agreement_override is not None:
                # DS EM 分支：r(source) 直接當 agreement_score。**任何 r=0.5
                # 的來源**（退化或 balanced 等非退化）等價先驗 → 強制 α=1 不
                # 動信譽；只有 r > 0.5 保留 alpha 預設（繞過線上
                # MIN_INDEPENDENT_EVIDENCE 閘，DS 自備小樣本）。
                #
                # 保守原則（#24 誠實 + 離線 fallback 語意）：離線 DS 只擁有「多源
                # 方向標籤的統計共識」，沒有真語意驗證，因此**只能共識上調**信譽
                # （一致性越高的來源越可信），**絕不因無驗證就下調**來源——下調必須
                # 有真 entailment 佐證（線上路径）。若 r 會把 final 拉低於 SR⁰，
                # 維持 SR⁰（等同不動該來源）。這讓離線 DS 成為「在無驗證下給一個比
                # 先驗略好的共識排序上調」，而不會把既有離線行為（final==prior）惡化。
                agreement_score = agreement_override[s]
                # A（codex 對抗審 High 修正）：「r=0.5 必須真正先驗等價」——
                # 只要該來源的 DS 自評 `r(source) == 0.5`（**無論是否為 DS 退化
                # 來源**，例如 balanced 來源也會得到 r=0.5），就強制 α=1.0，
                # 信譽完全維持先驗 SR⁰、不被 DS 影響（「DS 說中性 = 信譽不動」）。
                # 只有 `r > 0.5`（真有共識技能）才按原 `alpha` 做上調混合；
                # `r < 0.5` 的來源在下方 `max(blended_raw, sr0[s])` 維持先驗
                # （保守原則：離線 DS 無真語意驗證，絕不因無驗證就下調信譽）。
                # 這取代原本「只有 ds_fallback_sources 才 α=1」的過窄守門——
                # 舊邏輯會讓 r=0.5 的非退化來源仍以 α=0.55 混入、把低先驗來源
                # 上調（如 social 0.35→0.4175），違反「r=0.5=先驗等價」。
                a = 1.0 if agreement_score == 0.5 else alpha
                blended_raw = a * sr0[s] + (1.0 - a) * agreement_score
                blended = max(blended_raw, sr0[s])
            else:
                net = math.fsum(
                    avg_temp_by_source.get(s2, 0.5) for s2 in sorted(agree_union_of[s])
                ) - math.fsum(
                    avg_temp_by_source.get(s2, 0.5) for s2 in sorted(contra_union_of[s])
                )
                agreement_score = _stable_sigmoid(net)
                a = alpha_of[s]
                blended = a * sr0[s] + (1.0 - a) * agreement_score
            floor = _reputation_floor(kind_of[s])
            new_sr[s] = max(floor, min(1.0, blended))

        sr = new_sr
        delta = max(abs(sr[s] - prev.get(s, sr0[s])) for s in sr)
        if delta < REPUTATION_CONVERGENCE_EPS:
            break

    if trace_out is not None:
        trace_out["iterations_run"] = iterations_run
        trace_out["mode"] = "ds_em" if ds_mode else "entailment"
        if ds_mode:
            trace_out["ds_agree_n"] = ds_agree_n
            trace_out["ds_contradict_n"] = ds_contradict_n
            trace_out["ds_fallback_sources"] = sorted(ds_meta.get("fallback_sources", []))
    return sr


def build_stance_fn(
    stance_client=None,
    stance_pair_budget: int = DEFAULT_STANCE_PAIR_BUDGET,
    stance_remaining_time_fn: Callable[[], float] | None = None,
) -> Callable[[str, str], str] | None:
    """建立 W1.5 stance 判定函式（語意見 `score()` docstring 對
    `stance_client`/`stance_pair_budget`/`stance_remaining_time_fn` 的完整說明）。

    抽出成獨立函式（demo 可靠性 #32 追加）：讓 `score()` 內部的矛盾閘與
    `agent.orchestrator` 的跨源 stance_pairs 偵測能**共用同一個
    `_StanceBudget` 實例**——同一次 pipeline 執行內，真正呼叫 Bedrock 的
    配對硬上限與 `ExecutionLog.remaining()` 剩餘時間預算是同一個池子，
    不會因為分兩處（`score()` 的交叉佐證 vs. `detect_cross_source_signal`
    的 stance_pairs 偵測）各自另建一份預算，讓「單次執行真呼叫上限」實質
    變成兩倍、失去原本的防護意義。
    """
    if stance_client is None or hasattr(stance_client, "classify_stance"):
        stance_budget = _StanceBudget(stance_pair_budget, stance_remaining_time_fn)
        return cached_stance_fn(stance_client, budget=stance_budget)
    return None


# --- 主評分 --------------------------------------------------------------
def score(
    claims: list[Claim],
    now: float,
    weights: dict | None = None,
    stance_client=None,
    stance_pair_budget: int = DEFAULT_STANCE_PAIR_BUDGET,
    stance_remaining_time_fn: Callable[[], float] | None = None,
    dynamic_reputation: bool = True,
    reputation_iterations: int = DEFAULT_REPUTATION_ITERATIONS,
    stance_fn: Callable[[str, str], str] | None = None,
    offline: bool = False,
) -> list[ScoredClaim]:
    """`stance_client`：具備 `classify_stance(a, b) -> str` 方法的物件（如 BedrockClient），
    或 None。

    CEO+codex 對抗審修正：`stance_client=None` **不代表關掉 W1.5**，而是「沒有可用的
    線上模型（離線 / 未設模型）」——仍會建立 `cached_stance_fn(None)`，讓持久化快取
    （`demo/sample_data/stance_cache.json`）在離線路徑也能生效；快取 miss 時
    `cached_stance_fn` 內部 fail-safe 回 "neutral"，不呼叫任何 Bedrock、不 crash。
    只有當 `stance_client` 是「非 None 但沒有 `classify_stance` 方法」的物件（例如
    舊版測試用的 stub）時，才視為不相容物件、完全跳過矛盾閘（`stance_fn=None`，
    等同 W1.5 加入前的行為）。

    `stance_pair_budget`：單次執行「真正呼叫 Bedrock」的配對硬上限（見
    `_StanceBudget`），預設 `DEFAULT_STANCE_PAIR_BUDGET`；`stance_remaining_time_fn`：
    選用的即時剩餘時間回呼（通常傳 `ExecutionLog.remaining` 這個 bound method）。
    額度或時間耗盡時，其餘**需要新呼叫**的配對一律 fail-safe 降級為 "neutral"，
    防 O(n²) 呼叫無上限打 Bedrock；免費的 cache-hit 不受影響、不消耗這個預算
    （第 3 輪對抗審修正：預算消耗點在 `cached_stance_fn` 內部，只在確認 cache
    miss 後才扣，見該函式 docstring）。

    W2（truth-discovery 動態來源信譽）：`dynamic_reputation=True`（**預設**）啟用
    `_iterate_source_reputation`，來源信譽不再是固定 `KIND_REPUTATION`，而是由
    交叉佐證/矛盾動態調整（bounded 迭代，見該函式 docstring）。設 `False` 可關閉：
    `reputation` 分項回退到靜態行為。`reputation_iterations` 控制迭代輪數上限
    （硬上限 5）。啟用時每筆 `ScoredClaim.reputation_trace` 會附上該來源的
    `{source, prior, final, agree_n, contradict_n, iterations_run}`（可解釋，不塞進
    `components`，維持 `components` 的 str→number 契約）。如果動態信譽計算過程發生
    異常（EM 不收斂/溢位/斷言違反），自動 fail-safe fallback 到靜態信譽。

    codex 對抗審 [HIGH，#24]：`agree_n`/`prior→final` 的上調**只認真語意
    `entailment`**（見 `_reputation_evidence`/`_corroboration_detail`
    `require_entailment` 說明）——`stance_fn` 回傳 `"neutral"`（含離線/未設
    模型/timeout/malformed/cache miss/預算耗盡等 fail-safe，回傳值層級無法
    區分「真中立」與「沒驗證成功」）一律不採信、不計入樣本。生產預設
    `llm_mode=off` 時幾乎所有配對都是 fail-safe neutral，線上 W2 因此在離線模式
    下對信譽是 no-op（`agree_n`/`contradict_n` 皆 0，`final == prior`）——
    這是刻意、誠實的行為：沒有真的做過語意驗證，就不動信譽，只有真連上
    Bedrock/W1.5 語意分類且判定為 `entailment` 時才會上調；`"contradiction"`
    不受影響（fail-safe 絕不會產生 `"contradiction"`，見上述函式）。

    **#182 DS EM 離線 fallback**：當 `offline=True` **且** 沒有任何一筆真
    `entailment` 流進 W2（所有 source 的佐證聯集皆空）時，線上 truth-discovery
    無法計分，改由 `dawid_skene.em_source_reliability` 對「多源方向標籤的統計
    共識」估算每來源可靠度 `r(source)`，直接當 `agreement_score` 餵進 Step B
    混合公式（見 `_iterate_source_reputation` 分支 docstring）。這是離線路徑的
    誠實替代——**不是預測力、未解決 #167 AUC**，UI 標註「DS 共識收斂」而非
    「互證」。線上有 entailment 佐證時完全不走這條分支，行為不變。

    `offline`：選填，預設 `False`。由 `agent.orchestrator` 傳入 `client.offline`
    （離線敘事 = 無語意驗證能力）。僅在 `offline=True` 且無 entailment 時觸發
    DS fallback；`offline=False`（有真分類器，無論判定 neutral 或 budget 耗盡）
    仍維持先驗、不觸發 DS（回歸鎖）。

    `stance_fn`：選填。若提供，直接使用此函式（跳過用 `stance_client`/
    `stance_pair_budget`/`stance_remaining_time_fn` 另建一份），供呼叫端
    （如 `agent.orchestrator.run_agent_pipeline`）用 `build_stance_fn()`
    先建好、跟其他步驟（如跨源 stance_pairs 偵測）共用同一個
    `_StanceBudget` 實例（demo 可靠性 #32 追加，見 `build_stance_fn`
    docstring）。不提供時（預設）行為與之前逐字相同——用 `stance_client`
    等參數自建一份專屬本次 `score()` 呼叫的 stance_fn。
    """
    w = weights or DEFAULT_WEIGHTS
    if stance_fn is None:
        stance_fn = build_stance_fn(stance_client, stance_pair_budget, stance_remaining_time_fn)

    # W3：確定性、informational-only 文字相似度透明化訊號（模板相似），對本次
    # `score()` 的整個 claims 池只算一次（O(n²)，量級同 `_corroboration`，見
    # `_coordination_signals` docstring）。**不參與 manip 計算**——CEO 定案：
    # 文字相似度單獨無法證明協同操縱，只回填 `ScoredClaim.info_flags` 供人工
    # 判讀，`_iterate_source_reputation` 的 static_manip 也不吃這份結果。
    info_flags_by_id = _coordination_signals(claims) if claims else {}

    dynamic_map: dict[str, float] | None = None
    trace_by_source: dict[str, dict] | None = None
    if dynamic_reputation and claims:
        try:
            # evidence 只算一次，`_iterate_source_reputation` 的 K 輪迭代與下面建 trace
            # 共用同一份結果，不因迭代輪數或 trace 需求重呼叫 stance_fn。
            evidence = _reputation_evidence(claims, stance_fn=stance_fn)
            trace_meta: dict = {}
            sr0_for_trace: dict[str, float] = {}
            raw_source_of: dict[str, str] = {}
            for c in claims:
                s = _canonical_source(c.doc.source)
                if s not in sr0_for_trace:
                    sr0_for_trace[s] = _source_reputation(c)
                    raw_source_of[s] = c.doc.source
            dynamic_map = _iterate_source_reputation(
                claims,
                now,
                weights=w,
                stance_fn=stance_fn,
                iterations=reputation_iterations,
                evidence=evidence,
                trace_out=trace_meta,
                offline=offline,
            )
            iterations_run = trace_meta.get("iterations_run", 0)
            ds_mode = trace_meta.get("mode") == "ds_em"
            by_source: dict[str, list[Claim]] = {}
            for c in claims:
                by_source.setdefault(_canonical_source(c.doc.source), []).append(c)
            trace_by_source = {}
            for s, s_claims in by_source.items():
                if ds_mode:
                    # DS EM 模式：agree_n=參與的達標 item 數、contradict_n=與所屬
                    # item 多數票方向不一致次數（不偽造 agree/contra 聯集）。
                    agree_n = trace_meta.get("ds_agree_n", {}).get(s, 0)
                    contradict_n = trace_meta.get("ds_contradict_n", {}).get(s, 0)
                else:
                    agree_sources: set[str] = set()
                    contra_sources: set[str] = set()
                    for c in s_claims:
                        agree, contra = evidence.get(c.id, (set(), set()))
                        agree_sources |= agree
                        contra_sources |= contra
                    agree_n = len(agree_sources)
                    contradict_n = len(contra_sources)
                trace_by_source[s] = {
                    "source": raw_source_of.get(s, s),
                    "prior": round(sr0_for_trace.get(s, 0.0), 4),
                    "final": round(dynamic_map.get(s, sr0_for_trace.get(s, 0.0)), 4),
                    "agree_n": agree_n,
                    "contradict_n": contradict_n,
                    "iterations_run": iterations_run,
                    "mode": trace_meta.get("mode", "entailment"),
                }
        except Exception:
            # fail-safe：EM 失敗（不收斂/溢位/斷言違反）→ 靜默 fallback 到靜態信譽
            logging.getLogger(__name__).warning(
                "dynamic_reputation failed, falling back to static reputation",
                exc_info=True,
            )
            dynamic_map = None
            trace_by_source = None

    out: list[ScoredClaim] = []
    for c in claims:
        rep = _source_reputation(c, dynamic_map=dynamic_map)
        corr = _corroboration(c, claims, stance_fn=stance_fn)
        rec = _recency_decay(c, now)
        c_info_flags = info_flags_by_id.get(c.id, [])
        manip = _manipulation_penalty(c)
        raw = w["src"] * rep + w["corr"] * corr + w["rec"] * rec - w["manip"] * manip
        trust = max(0.0, min(1.0, raw))
        out.append(
            ScoredClaim(
                claim=c,
                trust=trust,
                components={"reputation": rep, "corroboration": corr,
                            "recency": rec, "manipulation": manip},
                reputation_trace=(
                    trace_by_source.get(_canonical_source(c.doc.source)) if trace_by_source is not None else None
                ),
                manip_flags=_manipulation_flags(c),
                # W3：文字相似度透明化 flag，informational-only，回填
                # `Evidence.info_flags`（見 `_coordination_signals` docstring）。
                # 不併入 `manip_flags`／`components["manipulation"]`。
                info_flags=c_info_flags,
            )
        )
    return out


# --- W4：信心校準（確定性、免 LLM）---------------------------------------
# codex 對抗審第 1 輪 [HIGH] 修正：原第一版直接把「裸加權均值 confidence」
# 塞進分位數映射表——但 `confidence` 定義上只取 `trust >= support_threshold`
# （預設 0.50）的 supporting 均值，數學上**恆為 0（無 supporting）或
# >=0.50（有 supporting）**，永遠不可能落在 (0, 0.50) 之間。若映射表在
# >=0.40 是 identity，校準值就永遠進不了 [0.35, 0.5) 的「低信心」帶——
# 三態在真實 `aggregate()` 輸出下只剩「空支撐 abstain」與「正常」兩態，
# 低信心態不可達，是假的三態。
#
# 修法：不要直接校準「裸均值」，改為先用既有 aggregate 資料算一個**能真正
# 跨越 [0, 1] 的證據強度綜合指標**（`_evidence_strength`），確定性、免
# LLM、純用已算好的 supporting/contrarian 清單，不新增資料源、不呼叫模型：
#   - trust：supporting 的裸加權均值（原 `confidence`）——證據本身的品質。
#   - indep：獨立來源數。只有 1 個來源＝完全沒有交叉佐證，給 0 分；
#     達到 `_INDEP_SOURCE_SATURATION`（4）個以上獨立來源給滿分，中間線性
#     內插。1 源 vs 6 源佐證的信心本該天差地遠，這項讓它反映在數字上。
#   - diversity：supporting 涵蓋的來源類型（kind）數。同理，只有 1 種
#     kind（如全部都是 news）給 0 分，達到 `_KIND_DIVERSITY_SATURATION`
#     （3）種以上給滿分。
#   - dominance：supporting 相對 contrarian 的證據優勢比例
#     `n_supporting / (n_supporting + n_contrarian)`——佐證證據被反方
#     證據夾擊得越兇，這項越低。
# 四項各自 clamp 在 [0, 1]，以 `_STRENGTH_WEIGHTS`（加總為 1.0）做加權
# 平均得到 `evidence_strength`，本身已是能自然分布在整個 [0, 1] 的指標
# （單源、無佐證、被反方夾擊的弱證據會落在低段；多源、多元 kind、佐證
# 壓倒反方的強證據會落在高段）。
#
# 再用 `_CALIBRATION_TABLE`（硬編分位數映射表，比照 `_MANIP_PATTERNS`
# 寫死在程式碼、可版控可審，不是訓練出來的黑箱模型）對這個綜合指標做
# 最後一層保守修正，得到 `calibrated_confidence`。
#
# ⚠️ 誠實聲明：這整套（`evidence_strength` 加權平均 + 分位數映射表）是
# **簡化的工程啟發式，不是嚴謹的 conformal prediction**——沒有 hold-out
# calibration set、沒有 exchangeability 假設驗證、不提供 conformal
# coverage 保證（如「90% 校準區間實際涵蓋 90% 真值」），權重與飽和點也
# 是工程判斷的固定常數而非統計估計出來的參數。目的只是讓「校準後信心」
# 是一個真正反映證據強度、可跨三態的確定性指標，供下游 abstain 判斷用；
# 不對外呈現為論文級統計保證。
_STRENGTH_WEIGHTS = {
    "trust": 0.35,       # supporting 裸加權均值（證據本身品質）
    "indep": 0.30,       # 獨立來源數（交叉佐證廣度）
    "diversity": 0.15,   # 來源類型（kind）多元度
    "dominance": 0.20,   # 佐證 vs 反方證據的優勢比例
}
_INDEP_SOURCE_SATURATION = 4  # 達到此獨立來源數即給滿分，之後不再加分
_KIND_DIVERSITY_SATURATION = 3  # 達到此來源類型數即給滿分，之後不再加分

# 分位數映射表錨點依輸入（x＝evidence_strength）遞增排序，(x, 校準後信心)。
# 中低段（<0.40）刻意壓得比原值低——這段最容易是「勉強及格但證據結構
# 薄弱」的情境；高段（>=0.55）貼近原值，因為 evidence_strength 本身在
# 高段已經隱含多源、多元 kind、佐證壓倒反方，不需要再額外壓縮。
_CALIBRATION_TABLE: list[tuple[float, float]] = [
    (0.00, 0.00),
    (0.10, 0.03),
    (0.20, 0.08),
    (0.30, 0.20),
    (0.40, 0.40),
    (0.55, 0.55),
    (0.70, 0.70),
    (0.85, 0.85),
    (1.00, 1.00),
]


def _calibrate_confidence(raw: float) -> float:
    """用 `_CALIBRATION_TABLE`（硬編分位數映射表）校準一個 [0, 1] 指標。

    確定性、免 LLM：純查表 + 分段線性插值，同輸入必同輸出，不呼叫任何
    模型。**簡化版分位數校準，非嚴謹 conformal coverage 保證**（見
    `_CALIBRATION_TABLE` 上方誠實聲明）。輸入超出 [0, 1] 時 clamp 到邊界。
    """
    x = max(0.0, min(1.0, raw))
    table = _CALIBRATION_TABLE
    if x <= table[0][0]:
        return table[0][1]
    if x >= table[-1][0]:
        return table[-1][1]
    for (x0, y0), (x1, y1) in zip(table, table[1:]):
        if x0 <= x <= x1:
            if x1 == x0:  # 防禦性：表若有重複 x 值不除以 0
                return y0
            ratio = (x - x0) / (x1 - x0)
            return round(y0 + ratio * (y1 - y0), 4)
    return round(x, 4)  # 理論上不會到這（表已覆蓋 [0, 1]，防禦性寫法）


def _evidence_strength(
    supporting: list[ScoredClaim], contrarian: list[ScoredClaim], confidence: float
) -> float:
    """算 `_calibrate_confidence()` 的輸入指標（見上方模組註解的設計說明）。

    確定性、免 LLM：只用呼叫端已算好的 supporting/contrarian 清單與裸
    `confidence`，不重新計算 trust、不新增資料源。回傳值 clamp 在
    [0, 1]（四個子指標各自已是 [0, 1]，加權平均理論上不會超界，clamp
    是防禦性寫法）。

    W4 codex 對抗審第 4 輪 [HIGH] robustness 修正：`dominance`（佐證 vs
    反方的證據優勢比例）改用**去重後的獨立來源數**，不用原始 claim（逐句）
    計數——`extract_claims()` 是句級切分，同一個來源囉嗦寫一大段會被切成
    多筆 claim；若 dominance 直接數 claim 筆數，單一囉嗦來源（無論支撐或
    反方）就能用「句數」灌爆／稀釋 dominance，等同讓「決策態隨 ingestion
    量而變、單一冗長來源能壓制方向結論」——這正是 codex 抓到的可操縱面。
    修法：dominance 的分子分母都改用「該側涉及的獨立來源數」（同一來源
    無論產生幾句 claim，只算一份），跟 `indep_factor` 既有的去重口徑一致
    （`n_indep` 本就已是去重來源數，直接複用）。"""
    n_indep = len({_canonical_source(sc.claim.doc.source) for sc in supporting})
    n_kinds = len({sc.claim.doc.kind for sc in supporting})

    indep_factor = max(0.0, min(
        (n_indep - 1) / (_INDEP_SOURCE_SATURATION - 1), 1.0
    ))
    diversity_factor = max(0.0, min(
        (n_kinds - 1) / (_KIND_DIVERSITY_SATURATION - 1), 1.0
    ))
    n_contrarian_sources = len({_canonical_source(sc.claim.doc.source) for sc in contrarian})
    total_sources = n_indep + n_contrarian_sources
    dominance = (n_indep / total_sources) if total_sources > 0 else 0.0

    w = _STRENGTH_WEIGHTS
    strength = (
        w["trust"] * confidence
        + w["indep"] * indep_factor
        + w["diversity"] * diversity_factor
        + w["dominance"] * dominance
    )
    return max(0.0, min(1.0, strength))


# --- 5. 聚合 -------------------------------------------------------------
def aggregate(scored: list[ScoredClaim], query: str,
              support_threshold: float = 0.50,
              coin: str | None = None) -> TrustedBrief:
    """信任加權聚合。高於門檻→支撐證據；明顯低分→反方證據。

    coin：選填。「coin-filter 主導」修正（demo 可靠性 #32 追加）——
    背景：`_normalize(query)` 對無空格的中/英混排（如「以太坊分析」
    「ETH現況」）會併成單一複合 token，與樣本文字的斷詞完全對不上，
    導致「與 query 相關者」篩選結果隨查詢措辭忽窄忽寬——即使某次問法
    「恰好」文字命中而把泛用雜訊（如「多家交易所遭 SEC 警告」這類未提及
    任何幣別的通用監管新聞）擠出候選池，也純屬巧合，換一種問法就可能
    連該幣「明確提及」的真實證據（例如 ETF 資金流背離樣本）一起被泛用
    雜訊擠出 contrarian 的截斷上限（`[:5]`）——同一份資料、不同問法卻
    得到不同的跨源訊號結果，不穩定、不可預期。
    修法：只要有指定 coin，排序時一律把「明確提及該幣」的主張
    （`_mentions_coin`）排在「全市場通用」主張之前（各自內部仍照信任分
    由高到低），使截斷上限優先保留該幣的特定證據，且完全不受 query
    文字措辭影響——查詢字串仍照樣傳入供其他用途（如 `TrustedBrief.query`
    留痕），但不再左右候選池的去留或排序。不傳 coin 時（既有呼叫端）
    行為完全不變。

    W4 codex 對抗審第 4～8 輪（coin-relevance 收斂史）：`relevant`（決定
    `supporting`/`contrarian`/`confidence`——報表 facts/evidence/
    calibration 共用的唯一一份資料）在傳 `coin` 時，用 `_matches_coin`
    （幣種相關或全市場通用，見 `ingestion.base._matches_coin` docstring）
    篩過，排除「明確提及其他幣、與本次分析目標幣無關」的雜訊主張。

    這段修法史本身就是「piecemeal 修法會一直漏」的活教材，完整記錄於此
    供之後維護者理解為什麼最終長這樣（而不是重蹈覆轍）：
      - 第 4 輪最初只讓 calibration（`_evidence_strength()` 的輸入）用
        `_matches_coin` 篩過的子集，`supporting`/`contrarian`/`confidence`
        仍是「全納入、只排序」——calibration 乾淨了，但報表本身（facts/
        `_direction()`/key_basis）還是會混進他幣證據。
      - 第 6 輪加了 `coin_scoped_supporting` 額外欄位，把同一份 coin-scoped
        子集帶給 `agent.orchestrator.build_report` 貫穿 facts/`_direction()`/
        key_basis/n_indep 門檻——但只補了 supporting 側，`contrarian`/
        `confidence` 仍未過濾。
      - 第 7 輪修了 `detect_cross_source_signal` 的輸入（呼叫端另外用
        `_matches_coin` 重新篩一次）——但那是在 `build_report` 裡「各自重新
        過濾一次」，不是共用同一份資料，第 8 輪才發現 `contrarian` 輸出／
        裸 `confidence` 顯示／`_derive_limits` 這些「report-facing」欄位還是
        沒過濾。
      - 第 8 輪根治：不再區分「relevant（報表用，全納入）」跟「calib_pool
        （calibration 用，篩過）」兩份資料——傳 `coin` 時，`relevant` 本身
        就是 `_matches_coin` 篩過的子集，`supporting`/`contrarian`/
        `confidence`/`calibrated_confidence` 全部從這唯一一份算出來，
        `TrustedBrief` 回傳後任何欄位天生就是 coin-scoped，不需要呼叫端
        （`build_report`）再各自過濾一次，也不需要額外欄位。已移除
        `coin_scoped_supporting`（第 6/7 輪引入，現已併入 `supporting`
        本身）。
    不傳 coin 時（既有呼叫端）行為完全不變：`relevant` 沿用 `_normalize
    (query)` 相關性排序、全納入（不新增篩選），維持 #32 修正前就存在的
    既有語意。
    """
    qt = _normalize(query)
    if coin:
        # 先依 (是否幣種特定, 信任分) 排序——把「明確提及該幣」的主張排在
        # 「全市場通用」主張之前，使下面的 [:10]/[:5] 截斷優先保留前者
        # （demo 可靠性 #32 追加的既有精神，見上方 docstring）。
        relevant = sorted(
            scored,
            key=lambda sc: (0 if _mentions_coin(sc.claim.doc, coin) else 1, -sc.trust),
        )
        # W4 codex 對抗審第 8 輪根治：排序後直接用 `_matches_coin` 過濾
        # （保留本幣相關 + 全市場通用，只排除明確他幣）——`supporting`/
        # `contrarian`/`confidence` 全部從這份已過濾的 `relevant` 算，
        # 不再是「全納入、只排序」。`_matches_coin` 是幣別別名比對，不是
        # #32 當年那種脆弱的 `_normalize(query)` 文字比對，不會重蹈覆轍。
        relevant = [sc for sc in relevant if _matches_coin(sc.claim.doc, coin)]
    else:
        # 與 query 相關者優先（無相關詞則全納入）——未指定 coin 時無獨立的
        # 幣種相關性判準可用，行為逐字向後相容，不引入新篩選。
        relevant = [
            sc for sc in scored
            if not qt or (_normalize(sc.claim.text) & qt)
        ] or scored
        relevant.sort(key=lambda sc: sc.trust, reverse=True)
    supporting = [sc for sc in relevant if sc.trust >= support_threshold]
    contrarian = [sc for sc in relevant if sc.trust < support_threshold]

    confidence = (sum(sc.trust for sc in supporting) / len(supporting)) if supporting else 0.0
    # W4：用「校準前」的完整 supporting/contrarian（截斷前，與 confidence 同一份
    # 基礎資料，見上方 `_evidence_strength` 對「不重新計算 trust、不新增資料源」
    # 的承諾）算證據強度綜合指標，再校準——不用截斷後的 [:10]/[:5]，避免評分
    # 結果隨截斷上限漂移（跟 `confidence` 本身的計算基礎保持一致）。
    evidence_strength = _evidence_strength(supporting, contrarian, confidence)
    return TrustedBrief(
        query=query,
        supporting=supporting[:10],
        contrarian=contrarian[:5],
        confidence=confidence,
        calibrated_confidence=_calibrate_confidence(evidence_strength),
    )
