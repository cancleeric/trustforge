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

from ..ingestion.base import Document

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
    # 方向性通用詞（過於籠統；單字母已被 _normalize 過濾，這裡補雙字）
    "漲跌", "漲", "跌",
    # 高頻語法詞（_normalize 已過濾單字，這裡補雙字）
    "目前", "近期", "顯示", "表示", "預計", "預測", "可能",
    "目標", "支撐", "阻力",
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
            claims.append(Claim(id=f"{d.id}#{i}", text=s, doc=d))
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


def _corroboration(target: Claim, all_claims: list[Claim]) -> float:
    """有多少**獨立來源**（不同 source）提到相似主張。回音室（同源轉發）不加分。

    改進（M1-M3）：
    - 停用詞過濾：從 overlap 計算排除域內通用詞（幣名/市場詞），只計具體詞重疊。
    - 方向閘：若兩條主張方向明確且相反（bullish vs bearish），略過，不算佐證。
    """
    tt = _normalize(target.text) - DOMAIN_STOP
    if not tt:
        return 0.0
    independent_sources: set[str] = set()
    for c in all_claims:
        if c.doc.source == target.doc.source:
            continue
        if not _direction_compatible(target.direction, c.direction):
            continue
        ct = _normalize(c.text) - DOMAIN_STOP
        overlap = len(tt & ct) / len(tt)
        if overlap >= 0.4:
            independent_sources.add(c.doc.source)
    # 1 個獨立佐證→0.5，2 個→0.79，飽和到 1.0
    n = len(independent_sources)
    return 1.0 - math.pow(0.5, n) if n else 0.0


# --- 主評分 --------------------------------------------------------------
def score(claims: list[Claim], now: float, weights: dict | None = None) -> list[ScoredClaim]:
    w = weights or DEFAULT_WEIGHTS
    out: list[ScoredClaim] = []
    for c in claims:
        rep = _source_reputation(c)
        corr = _corroboration(c, claims)
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
