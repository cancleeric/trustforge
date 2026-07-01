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

import math
import re
from dataclasses import dataclass, field
from typing import Callable

from ..ingestion.base import Document
from .stance_cache import cached_stance_fn

# W1.5（#15）+ CEO/codex 對抗審修正：線上 stance 呼叫預算（防 O(n²) 呼叫無上限打
# Bedrock）。單次 score() 執行最多消耗這麼多「呼叫額度」（含快取命中，保守估計）；
# 額度用完後其餘配對一律降級 "neutral"（不呼叫、不錯殺）。數字是保守預設，可視實際
# 成本調整；claims 數量在此之內時完全不受影響（既有測試場景 claims 數都遠小於此）。
DEFAULT_STANCE_PAIR_BUDGET = 40

# stance 呼叫最少要保留的剩餘執行時間（秒）。ExecutionLog.remaining() 低於這個門檻
# 時一律視為預算耗盡，就算配對數還沒到硬上限，避免官方 15 分鐘執行窗口的最後一點
# 時間被 stance 呼叫吃光、報告生不出來（命題第一號失敗模式）。
STANCE_TIME_RESERVE_SEC = 5.0


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


@dataclass
class TrustedBrief:
    query: str
    supporting: list[ScoredClaim]      # 高信任、支撐主流結論
    contrarian: list[ScoredClaim]      # 低信任 / 反方，供反方證據
    confidence: float                  # 整體信心（0–1）

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
def _source_reputation(c: Claim) -> float:
    base = KIND_REPUTATION.get(c.doc.kind, 0.5)
    # 來源層級覆寫（白名單/黑名單）
    override = c.doc.meta.get("reputation")
    return float(override) if override is not None else base


def _recency_decay(c: Claim, now: float, half_life_h: float = 12.0) -> float:
    """指數衰減；加密資訊半衰期短，預設 12 小時。ts=0 視為未知→中性 0.5。"""
    if not c.doc.ts:
        return 0.5
    age_h = max(0.0, (now - c.doc.ts) / 3600.0)
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


def _manipulation_penalty(c: Claim) -> float:
    # 否定守門:命中前 4 字內有明確否定(如「不會暴漲」)不計,避免正當新聞被誤扣
    text = c.text
    hits = 0
    for p in _MANIP_PATTERNS:
        for m in re.finditer(p, text, re.IGNORECASE):
            if _NEG_RX.search(text[max(0, m.start() - 4):m.start()]):
                continue
            hits += 1
    # 社群來源的操縱訊號加重
    weight = 1.5 if c.doc.kind == "social" else 1.0
    return min(1.0, hits * 0.4 * weight)


def _normalize(s: str) -> set[str]:
    return {t for t in re.findall(r"[\w一-鿿]+", s.lower()) if len(t) > 1}


def _direction_compatible(d1: str, d2: str) -> bool:
    """方向相容檢查。任一方為 neutral 時不擋（離線/預設安全）；兩者皆有方向時必須一致。"""
    if "neutral" in (d1, d2):
        return True
    return d1 == d2


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


def _corroboration(
    target: Claim,
    all_claims: list[Claim],
    stance_fn: Callable[[str, str], str] | None = None,
    stance_budget: "_StanceBudget | None" = None,
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
    4. 若 `stance_fn` 存在才呼叫：`stance_budget` 額度耗盡時 fail-safe 降級為
       "neutral"（不呼叫、不 raise、不錯殺）；回傳 "contradiction" 則不計入獨立佐證。

    `stance_fn=None` 時完全略過第 4 步，行為與加入 W1.5 前逐字相同（向後相容）。
    """
    tt = _normalize(target.text) - DOMAIN_STOP
    if not tt:
        return 0.0
    independent_sources: set[str] = set()
    for c in all_claims:
        if c.doc.source == target.doc.source:
            continue
        if c.doc.source in independent_sources:
            continue
        ct = _normalize(c.text) - DOMAIN_STOP
        overlap = len(tt & ct) / len(tt)
        if overlap < 0.4:
            continue
        if not _direction_compatible(target.direction, c.direction):
            continue
        if stance_fn is not None:
            if stance_budget is None or stance_budget.take():
                label = stance_fn(target.text, c.text)
            else:
                label = "neutral"  # 預算耗盡，保守降級，不呼叫、不錯殺
            if label == "contradiction":
                continue
        independent_sources.add(c.doc.source)
    # 1 個獨立佐證→0.5，2 個→0.79，飽和到 1.0
    n = len(independent_sources)
    return 1.0 - math.pow(0.5, n) if n else 0.0


# --- 主評分 --------------------------------------------------------------
def score(
    claims: list[Claim],
    now: float,
    weights: dict | None = None,
    stance_client=None,
    stance_pair_budget: int = DEFAULT_STANCE_PAIR_BUDGET,
    stance_remaining_time_fn: Callable[[], float] | None = None,
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

    `stance_pair_budget`：單次執行 stance 呼叫配對硬上限（見 `_StanceBudget`），
    預設 `DEFAULT_STANCE_PAIR_BUDGET`；`stance_remaining_time_fn`：選用的即時剩餘
    時間回呼（通常傳 `ExecutionLog.remaining` 這個 bound method），額度或時間耗盡
    時其餘配對一律 fail-safe 降級為 "neutral"，防 O(n²) 呼叫無上限打 Bedrock。
    """
    w = weights or DEFAULT_WEIGHTS
    stance_fn = (
        cached_stance_fn(stance_client)
        if stance_client is None or hasattr(stance_client, "classify_stance")
        else None
    )
    stance_budget = (
        _StanceBudget(stance_pair_budget, stance_remaining_time_fn)
        if stance_fn is not None
        else None
    )
    out: list[ScoredClaim] = []
    for c in claims:
        rep = _source_reputation(c)
        corr = _corroboration(c, claims, stance_fn=stance_fn, stance_budget=stance_budget)
        rec = _recency_decay(c, now)
        manip = _manipulation_penalty(c)
        raw = w["src"] * rep + w["corr"] * corr + w["rec"] * rec - w["manip"] * manip
        trust = max(0.0, min(1.0, raw))
        out.append(
            ScoredClaim(
                claim=c,
                trust=trust,
                components={"reputation": rep, "corroboration": corr,
                            "recency": rec, "manipulation": manip},
            )
        )
    return out


# --- 5. 聚合 -------------------------------------------------------------
def aggregate(scored: list[ScoredClaim], query: str,
              support_threshold: float = 0.50) -> TrustedBrief:
    """信任加權聚合。高於門檻→支撐證據；明顯低分→反方證據。"""
    qt = _normalize(query)
    # 與 query 相關者優先（無相關詞則全納入）
    relevant = [
        sc for sc in scored
        if not qt or (_normalize(sc.claim.text) & qt)
    ] or scored

    relevant.sort(key=lambda sc: sc.trust, reverse=True)
    supporting = [sc for sc in relevant if sc.trust >= support_threshold]
    contrarian = [sc for sc in relevant if sc.trust < support_threshold]

    confidence = (sum(sc.trust for sc in supporting) / len(supporting)) if supporting else 0.0
    return TrustedBrief(
        query=query,
        supporting=supporting[:10],
        contrarian=contrarian[:5],
        confidence=confidence,
    )
