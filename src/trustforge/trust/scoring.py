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

from ..bedrock import _STANCE_CONNECT_TIMEOUT_SEC, _STANCE_READ_TIMEOUT_SEC
from ..ingestion.base import Document, _mentions_coin
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
}


def _reputation_floor(kind: str) -> float:
    """W2：動態信譽每輪迭代 clamp 下限，防止 SR 蒸發到 0。

    取 kind 基礎信譽的 30%（social: 0.35*0.3≈0.105，符合 CEO refinement「social
    不低於 ~0.1」；price/onchain 最高 ≈0.29，依序遞減，未知 kind 保守回退 0.35 基礎
    → floor≈0.105，等同 social 下限，不給未知來源類型更高保障）。
    """
    return round(0.3 * KIND_REPUTATION.get(kind, 0.35), 4)

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
    # W2：動態來源信譽可解釋 trace。預設 None（`dynamic_reputation=False` 逐字相容，
    # 不影響既有 dataclass 相等性比較）。開啟時填入該 claim 來源的
    # {source, prior, final, agree_n, contradict_n, iterations_run}。
    # 刻意獨立於 components（後者維持 str -> number 契約，不塞巢狀 dict）。
    reputation_trace: dict | None = None


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
def _source_reputation(c: Claim, dynamic_map: dict[str, float] | None = None) -> float:
    """來源信譽。`dynamic_map=None`（預設，逐字等同現行）：純先驗，僅取決於
    `KIND_REPUTATION` 或 per-doc `meta["reputation"]` 覆寫。

    W2：傳入 `dynamic_map`（`{source: SR}`，見 `_iterate_source_reputation`）時，
    改用該來源的動態信譽；若該來源不在 map 中（理論上不會發生，防禦性寫法），
    回退為先驗值，不 raise。
    """
    base = KIND_REPUTATION.get(c.doc.kind, 0.5)
    # 來源層級覆寫（白名單/黑名單）
    override = c.doc.meta.get("reputation")
    prior = float(override) if override is not None else base
    if dynamic_map is None:
        return prior
    return dynamic_map.get(c.doc.source, prior)


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


def _corroboration_detail(
    target: Claim,
    all_claims: list[Claim],
    stance_fn: Callable[[str, str], str] | None = None,
) -> tuple[set[str], set[str]]:
    """`_corroboration()` 核心迴圈抽出版，回傳 `(independent_sources, contradicting_sources)`。

    W2（#動態信譽）新增：獨立佐證迴圈本身完全不變（逐字保留原順序/判斷，確保
    `_corroboration()` 的回傳值 byte-identical），只是額外把「W1.5 stance 判定為
    contradiction」的來源也收進第二個集合——這是既有迴圈裡本來就會算出來的資訊
    （只是舊版直接丟棄），現在同一次迴圈順便記下來，**不新增任何 stance_fn 呼叫**。

    供 `_corroboration()`（沿用原本行為）與 `_iterate_source_reputation()`（W2
    agreement 訊號）共用同一次 overlap/方向/stance 判斷結果。
    """
    tt = _normalize(target.text) - DOMAIN_STOP
    independent_sources: set[str] = set()
    contradicting_sources: set[str] = set()
    if not tt:
        return independent_sources, contradicting_sources
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
        if stance_fn is not None and stance_fn(target.text, c.text) == "contradiction":
            contradicting_sources.add(c.doc.source)
            continue
        independent_sources.add(c.doc.source)
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
    """
    return {c.id: _corroboration_detail(c, claims, stance_fn=stance_fn) for c in claims}


def _iterate_source_reputation(
    claims: list[Claim],
    now: float,
    weights: dict | None = None,
    stance_fn: Callable[[str, str], str] | None = None,
    iterations: int = DEFAULT_REPUTATION_ITERATIONS,
    alpha: float = DEFAULT_REPUTATION_ALPHA,
    evidence: dict[str, tuple[set[str], set[str]]] | None = None,
    trace_out: dict | None = None,
) -> dict[str, float]:
    """W2：bounded 迭代動態來源信譽。純函式、無隨機性 → 同輸入必同輸出。

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
    sr0: dict[str, float] = {}
    kind_of: dict[str, str] = {}
    claims_by_source: dict[str, list[Claim]] = {}
    for c in claims:
        s = c.doc.source
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
    dynamic_reputation: bool = False,
    reputation_iterations: int = DEFAULT_REPUTATION_ITERATIONS,
    stance_fn: Callable[[str, str], str] | None = None,
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

    W2（truth-discovery 動態來源信譽）：`dynamic_reputation=False`（**預設**）完全
    跳過 `_iterate_source_reputation`，`reputation` 分項與既有行為逐字相同（回歸
    鎖）。設 `True` 才啟用：來源信譽不再是固定 `KIND_REPUTATION`，而是由
    `_iterate_source_reputation` 依交叉佐證/矛盾動態調整（bounded 迭代，見該函式
    docstring）；`reputation_iterations` 控制迭代輪數上限（硬上限 5）。啟用時每筆
    `ScoredClaim.reputation_trace` 會附上該來源的
    `{source, prior, final, agree_n, contradict_n, iterations_run}`（可解釋，不塞進
    `components`，維持 `components` 的 str→number 契約）。

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

    dynamic_map: dict[str, float] | None = None
    trace_by_source: dict[str, dict] | None = None
    if dynamic_reputation and claims:
        # evidence 只算一次，`_iterate_source_reputation` 的 K 輪迭代與下面建 trace
        # 共用同一份結果，不因迭代輪數或 trace 需求重呼叫 stance_fn。
        evidence = _reputation_evidence(claims, stance_fn=stance_fn)
        trace_meta: dict = {}
        sr0_for_trace: dict[str, float] = {}
        for c in claims:
            sr0_for_trace.setdefault(c.doc.source, _source_reputation(c))
        dynamic_map = _iterate_source_reputation(
            claims,
            now,
            weights=w,
            stance_fn=stance_fn,
            iterations=reputation_iterations,
            evidence=evidence,
            trace_out=trace_meta,
        )
        iterations_run = trace_meta.get("iterations_run", 0)
        by_source: dict[str, list[Claim]] = {}
        for c in claims:
            by_source.setdefault(c.doc.source, []).append(c)
        trace_by_source = {}
        for s, s_claims in by_source.items():
            agree_sources: set[str] = set()
            contra_sources: set[str] = set()
            for c in s_claims:
                agree, contra = evidence.get(c.id, (set(), set()))
                agree_sources |= agree
                contra_sources |= contra
            trace_by_source[s] = {
                "source": s,
                "prior": round(sr0_for_trace.get(s, 0.0), 4),
                "final": round(dynamic_map.get(s, sr0_for_trace.get(s, 0.0)), 4),
                "agree_n": len(agree_sources),
                "contradict_n": len(contra_sources),
                "iterations_run": iterations_run,
            }

    out: list[ScoredClaim] = []
    for c in claims:
        rep = _source_reputation(c, dynamic_map=dynamic_map)
        corr = _corroboration(c, claims, stance_fn=stance_fn)
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
                reputation_trace=(
                    trace_by_source.get(c.doc.source) if trace_by_source is not None else None
                ),
            )
        )
    return out


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
    """
    qt = _normalize(query)
    if coin:
        # 只排序、不篩選：與既有「全納入」精神一致，只是把幣種特定證據
        # 排到全市場通用雜訊之前，讓 [:10]/[:5] 截斷優先保留前者。
        relevant = sorted(
            scored,
            key=lambda sc: (0 if _mentions_coin(sc.claim.doc, coin) else 1, -sc.trust),
        )
        # 已依 (是否幣種特定, 信任分) 排序完成，不再套用下面純信任分排序。
    else:
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
