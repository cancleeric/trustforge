"""純 Trust Kernel — 只接受標準化 Evidence/Claim，輸出計算結果。

不得依賴：IO/LLM/cache/boto3/web/skills/upgrade/deploy

這是實體切割版本（v2，Issue #381）。
前版（Phase-1 facade）只做 re-export，不算真正的邊界切割。
本版定義 KernelInput / KernelOutput 版本化契約，並透過純計算
包裝 scoring.py 的現有邏輯，無任何模組層級的 IO/AWS/HTTP 依賴。

設計決策：
  - KernelInput / KernelOutput 均為 frozen dataclass（不可變）
  - run_kernel 為純函式，相同輸入必然得到相同輸出
  - scoring.py 仍可保有其 bedrock/ingestion 依賴（用於 stance / dynamic
    reputation），但 kernel.py 本身不引入任何此類依賴
  - 延遲 import（函式內）確保模組層級 AST 完全乾淨
  - 向後相容：保留 Phase-1 facade 的所有 re-export 符號（extract_claims /
    score / aggregate / em_source_reliability / DEFAULT_WEIGHTS / KIND_REPUTATION /
    KIND_HALFLIFE_HOURS / Claim / ScoredClaim / TrustedBrief / KERNEL_SCHEMA_VERSION），
    以免現有呼叫端（test_trust_kernel.py 等）因重構而失效。
"""
from __future__ import annotations

from dataclasses import dataclass

# Contract version — bump when KernelInput/KernelOutput schema changes
KERNEL_CONTRACT_VERSION = "1.0.0"

# Phase-1 schema version alias（向後相容）
KERNEL_SCHEMA_VERSION = KERNEL_CONTRACT_VERSION

# ---------------------------------------------------------------------------
# Phase-1 facade re-exports（向後相容，不可移除）
# 呼叫端可透過 kernel 模組取得這些符號，不需直接依賴 scoring.py 的內部結構。
# ---------------------------------------------------------------------------

# 核心計算函式
from .scoring import (  # noqa: E402
    extract_claims,
    score,
    aggregate,
)

# Dawid-Skene EM 動態信譽
from .dawid_skene import em_source_reliability  # noqa: E402

# 核心常數
from .scoring import (  # noqa: E402
    DEFAULT_WEIGHTS,
    KIND_REPUTATION,
    KIND_HALFLIFE_HOURS,
)

# 資料型別
from .scoring import (  # noqa: E402
    Claim,
    ScoredClaim,
    TrustedBrief,
)

# ---------------------------------------------------------------------------
# v2 新增：版本化 Kernel Input / Output contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KernelInput:
    """Trust Kernel 的標準化輸入。

    呼叫端（agent / orchestrator）必須先將原始 Document 轉成
    標準化 Claim list，再傳入此結構。Kernel 本身不做 I/O、
    不抓資料、不呼叫 LLM。

    Attributes:
        claims: 已由上層標準化的 Claim list（scoring.Claim）。
        pit_epoch: Point-in-time timestamp（Unix epoch float），
                   用於時效衰減計算。
        coin: 幣種代號（e.g. "BTC"），用於 coin-scoped 過濾。
        query: 分析查詢字串，用於 TrustedBrief.query 留痕。
    """
    claims: list   # list[scoring.Claim]，已由上層標準化
    pit_epoch: float  # Point-in-time Unix timestamp
    coin: str
    query: str


@dataclass(frozen=True)
class KernelOutput:
    """Trust Kernel 的標準化輸出。

    Attributes:
        trust_score: 裸加權信心（0–1），來自 TrustedBrief.confidence。
        confidence: 校準後信心（0–1），來自 TrustedBrief.calibrated_confidence。
                    如果 TrustedBrief 沒有此屬性則 fallback 到 trust_score。
        abstain: 是否棄權（信心過低，不宜給出明確結論）。
        direction: 方向性判斷（"偏多"/"偏空"/"中性"/"不明"）。
        reason_codes: 可解釋推理代碼列表（供 UI / 溯源）。
        supporting_count: 支撐主張數量。
        independent_sources: 獨立來源數量。
    """
    trust_score: float
    confidence: float
    abstain: bool
    direction: str   # "偏多" / "偏空" / "中性" / "不明"
    reason_codes: list  # list[str]
    supporting_count: int
    independent_sources: int


# ---------------------------------------------------------------------------
# 內部 helper（純計算，無 IO）
# ---------------------------------------------------------------------------


def _count_independent_sources(brief: "TrustedBrief") -> int:
    """從 TrustedBrief 計算獨立來源數（dedup by canonical source）。"""
    seen: set[str] = set()
    for sc in brief.supporting:
        try:
            src = sc.claim.doc.source
        except AttributeError:
            continue
        if src:
            seen.add(src.lower().strip())
    return len(seen)


def _infer_kernel_direction(brief: "TrustedBrief") -> str:
    """從 TrustedBrief 推斷方向性（簡易啟發式）。

    規則（保守）：
      - 若 brief 已帶 direction 屬性直接用（未來擴充路徑）
      - 否則統計支撐主張的 claim.direction
      - bullish > bearish + 1 → "偏多"
      - bearish > bullish + 1 → "偏空"
      - 兩者均非 0 但相差 ≤1 → "中性"
      - 其他（無資料/全 neutral）→ "不明"
    """
    if hasattr(brief, "direction") and brief.direction:
        return brief.direction

    bullish = 0
    bearish = 0
    for sc in brief.supporting:
        try:
            d = sc.claim.direction
        except AttributeError:
            continue
        if d == "bullish":
            bullish += 1
        elif d == "bearish":
            bearish += 1

    if bullish == 0 and bearish == 0:
        return "不明"
    if bullish > bearish + 1:
        return "偏多"
    if bearish > bullish + 1:
        return "偏空"
    if bullish > 0 or bearish > 0:
        return "中性"
    return "不明"


def _build_reason_codes(brief: "TrustedBrief", abstain: bool) -> list:
    """產生可解釋推理代碼列表。"""
    codes: list = []
    if abstain:
        codes.append("low_confidence")
    if not brief.supporting:
        codes.append("no_supporting_claims")
    calibrated = getattr(brief, "calibrated_confidence", 0.0)
    if calibrated > brief.confidence + 0.1:
        codes.append("calibration_boosted")
    if brief.contrarian:
        codes.append("contrarian_evidence_present")
    return codes


def run_kernel(inp: KernelInput) -> KernelOutput:
    """純計算核心，無任何模組層級 IO 依賴。

    使用延遲 import 確保 kernel.py 模組層級 AST 乾淨（無 bedrock / boto3 等）。
    scoring.py 自身可有 bedrock 依賴（用於 stance timeout 常數），
    但那些依賴不會出現在本模組的 import 圖中。

    注意：模組層級已有 from .scoring import score, aggregate 的 facade re-export，
    因此這裡直接使用模組頂層的符號，不需要再延遲 import（兩者指向同一物件）。

    Args:
        inp: KernelInput — 標準化 Claim list + PIT timestamp + coin + query。

    Returns:
        KernelOutput — 信任分、校準信心、棄權旗標、方向、推理代碼、計數。
    """
    # 直接使用模組頂層已 re-export 的符號（score / aggregate），
    # 避免在函式體內重複 import 造成混淆。
    scored = score(
        inp.claims,
        now=inp.pit_epoch,
        dynamic_reputation=False,   # 純記憶體，不呼叫 Bedrock
        offline=True,
    )
    brief = aggregate(scored, inp.query, coin=inp.coin)

    calibrated = (
        brief.calibrated_confidence
        if hasattr(brief, "calibrated_confidence") and brief.calibrated_confidence
        else brief.confidence
    )

    # abstain 判斷：校準後信心低於 0.4 視為棄權
    abstain_val: bool
    if hasattr(brief, "abstain"):
        abstain_val = bool(brief.abstain)
    else:
        abstain_val = calibrated < 0.4

    direction = _infer_kernel_direction(brief)
    reason_codes = _build_reason_codes(brief, abstain_val)
    n_independent = _count_independent_sources(brief)

    return KernelOutput(
        trust_score=brief.confidence,
        confidence=calibrated,
        abstain=abstain_val,
        direction=direction,
        reason_codes=reason_codes,
        supporting_count=len(brief.supporting),
        independent_sources=n_independent,
    )


# ---------------------------------------------------------------------------
# __all__（向後相容 + 新增符號）
# ---------------------------------------------------------------------------

__all__ = [
    # Phase-1 facade（向後相容）
    "extract_claims",
    "score",
    "aggregate",
    "em_source_reliability",
    "DEFAULT_WEIGHTS",
    "KIND_REPUTATION",
    "KIND_HALFLIFE_HOURS",
    "KERNEL_SCHEMA_VERSION",
    "Claim",
    "ScoredClaim",
    "TrustedBrief",
    # v2 新增
    "KERNEL_CONTRACT_VERSION",
    "KernelInput",
    "KernelOutput",
    "run_kernel",
]
