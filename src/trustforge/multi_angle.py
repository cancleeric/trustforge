"""Multi-angle Analysis Orchestrator — 五角度綜合分析確定性演算法。

將 TrustForge 五個分析視角（risk / sentiment / fundamentals / news / catalyst）
正規化為統一契約，並提供確定性交叉比對演算法產出綜合報告。

核心原則：
- synthesis 結論 100% 由確定性公式產出，零 LLM 依賴
- LLM 只能負責後續敘事（選填，見 #811），不可自行發明交叉訊號
- 任一角度 abstain 時，綜合不得硬拉 normal
- 每個結論可追溯到具體角度 → 具體數值

Issue: #808
"""
from __future__ import annotations

import json
import math
import time
from collections import Counter
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

from .schema import QuestionType, iso_utc


# ---------------------------------------------------------------------------
# 常數
# ---------------------------------------------------------------------------

MODE_LABELS: dict[str, str] = {
    "risk": "風險評估",
    "sentiment": "市場情緒",
    "fundamentals": "基本面",
    "news": "新聞驗證",
    "catalyst": "催化因素",
}

_OPPOSING_PAIRS: set[tuple[str, str]] = {("偏多", "偏空"), ("偏空", "偏多")}

# 信心差距門檻：兩角度 calibrated_confidence 差距超過此值視為 confidence_gap
CONFIDENCE_GAP_THRESHOLD: float = 0.3

# 證據獨立性警示門檻：所有角度共用來源比例超過此值視為獨立性不足
EVIDENCE_OVERLAP_WARNING_THRESHOLD: float = 0.7


# ---------------------------------------------------------------------------
# 資料契約
# ---------------------------------------------------------------------------

@dataclass
class AngleResult:
    """單角度分析結果正規化結構。

    從既有 Report + Evidence payload 投影而來，不修改原 dataclass。
    """
    angle: str                          # "risk" | "sentiment" | "fundamentals" | "news" | "catalyst"
    qtype: QuestionType                 # MULTI_SOURCE | HYPOTHESIS
    direction: str                      # "偏多" | "偏空" | "中性" | "不明"
    calibrated_confidence: float        # 0.0 ~ 1.0
    decision_state: str                 # "normal" | "low_confidence" | "abstain"
    key_basis: list[str]                # 關鍵依據摘要（前 3 條）
    evidence_sources: set[str]          # 去重的 evidence source 集合
    evidence_count: int                 # evidence 總數
    market_judgment: str                # 完整 market_judgment 原文
    snapshot_id: str                    # 追溯用
    job_id: str | None = None           # 追溯用
    question: str = ""                  # 同 snapshot 明細導覽用

    def to_dict(self) -> dict[str, Any]:
        """序列化為 JSON-safe dict。"""
        return {
            "angle": self.angle,
            "qtype": self.qtype.value,
            "direction": self.direction,
            "calibrated_confidence": self.calibrated_confidence,
            "decision_state": self.decision_state,
            "key_basis": self.key_basis,
            "evidence_sources": sorted(self.evidence_sources),
            "evidence_count": self.evidence_count,
            "market_judgment": self.market_judgment,
            "snapshot_id": self.snapshot_id,
            "job_id": self.job_id,
            "question": self.question,
        }


@dataclass
class AngleConflict:
    """角度間衝突描述。

    conflict_type:
      - "direction_divergence": 方向背離（偏多 vs 偏空）
      - "confidence_gap": 信心差距（差 > CONFIDENCE_GAP_THRESHOLD）
      - "evidence_overlap": 證據高度重疊（獨立性不足）
    """
    angle_a: str
    angle_b: str
    conflict_type: str
    detail: dict[str, Any]              # 數值細節，可追溯
    summary: str                        # 確定性模板文字

    def to_dict(self) -> dict[str, Any]:
        return {
            "angle_a": self.angle_a,
            "angle_b": self.angle_b,
            "conflict_type": self.conflict_type,
            "detail": self.detail,
            "summary": self.summary,
        }


@dataclass
class MultiAngleReport:
    """五角度綜合分析報告。"""
    coin: str
    snapshot_id: str
    angles: list[AngleResult]
    consensus: str                      # "偏多" | "偏空" | "中性" | "分歧" | "partial_abstain" | "full_abstain"
    consensus_confidence: float         # 加權信心
    conflicts: list[AngleConflict]
    agreement_matrix: dict[str, dict[str, str]]  # {angle_a: {angle_b: "agree"|"disagree"|"one_abstain"}}
    synthesis_summary: str              # 確定性模板組裝的摘要文字
    evidence_independence: float        # 獨立來源比例 0.0 ~ 1.0
    decision_state: str = "normal"      # normal | partial_abstain | full_abstain
    limits: list[str] = field(default_factory=list)
    generated_at: str = ""

    def __post_init__(self):
        if not self.generated_at:
            self.generated_at = iso_utc(time.time())

    def to_dict(self) -> dict[str, Any]:
        return {
            "coin": self.coin,
            "snapshot_id": self.snapshot_id,
            "angles": [a.to_dict() for a in self.angles],
            "consensus": self.consensus,
            "decision_state": self.decision_state,
            "consensus_confidence": self.consensus_confidence,
            "conflicts": [c.to_dict() for c in self.conflicts],
            "agreement_matrix": self.agreement_matrix,
            "synthesis_summary": self.synthesis_summary,
            "evidence_independence": self.evidence_independence,
            "limits": self.limits,
            "generated_at": self.generated_at,
        }


# ---------------------------------------------------------------------------
# 反序列化
# ---------------------------------------------------------------------------

def angle_result_from_payload(mode: str, payload_json: str, *,
                              job_id: str | None = None,
                              question: str = "") -> AngleResult:
    """從 analysis_results.payload_json 反序列化為 AngleResult。

    容錯處理舊格式：缺欄位時用安全預設值，不 raise。
    """
    try:
        data = json.loads(payload_json) if isinstance(payload_json, str) else payload_json
    except (TypeError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    report = data.get("report", {})
    evidence_list = data.get("evidence", [])
    if not isinstance(report, dict):
        report = {}
    if not isinstance(evidence_list, list):
        evidence_list = []

    # key_basis 取前 3 條 claim 文字
    key_basis_raw = report.get("key_basis", [])
    key_basis = []
    for item in key_basis_raw[:3]:
        if isinstance(item, dict):
            key_basis.append(item.get("claim", ""))
        elif isinstance(item, str):
            key_basis.append(item)

    # evidence sources 去重
    evidence_sources: set[str] = set()
    for ev in evidence_list:
        src = ev.get("source", "") if isinstance(ev, dict) else ""
        if src:
            evidence_sources.add(src)

    # qtype 容錯
    qtype_raw = report.get("question_type", "multi_source")
    try:
        qtype = QuestionType(qtype_raw)
    except ValueError:
        qtype = QuestionType.MULTI_SOURCE

    try:
        confidence = float(report.get("calibrated_confidence", 0.0))
    except (TypeError, ValueError, OverflowError):
        confidence = 0.0
    if not math.isfinite(confidence):
        confidence = 0.0
    confidence = min(1.0, max(0.0, confidence))

    return AngleResult(
        angle=mode,
        qtype=qtype,
        direction=report.get("direction", "不明"),
        calibrated_confidence=confidence,
        decision_state=report.get("decision_state", "normal"),
        key_basis=key_basis,
        evidence_sources=evidence_sources,
        evidence_count=len(evidence_list),
        market_judgment=report.get("market_judgment", ""),
        snapshot_id=data.get("snapshot_id", ""),
        job_id=job_id,
        question=question,
    )


# ---------------------------------------------------------------------------
# 工具函式
# ---------------------------------------------------------------------------

def _is_opposing(d1: str, d2: str) -> bool:
    """判斷兩個方向是否相反（偏多 vs 偏空）。"""
    return (d1, d2) in _OPPOSING_PAIRS


def _weighted_confidence(angles: list[AngleResult]) -> float:
    """以角度間來源獨立性加權 calibrated confidence。

    每角度權重為 ``max(0.1, 1 - 與其他可評估角度的平均 Jaccard overlap)``。
    空來源 pair 無法評估，故不納入該角度平均；若完全沒有可評估 pair，
    權重退回 1。重複堆疊相同 evidence 不會提高權重。
    """
    if not angles:
        return 0.0
    weights: list[float] = []
    for index, angle in enumerate(angles):
        overlaps: list[float] = []
        for other_index, other in enumerate(angles):
            if index == other_index or not angle.evidence_sources or not other.evidence_sources:
                continue
            union = angle.evidence_sources | other.evidence_sources
            overlaps.append(len(angle.evidence_sources & other.evidence_sources) / len(union))
        weights.append(max(0.1, 1.0 - sum(overlaps) / len(overlaps)) if overlaps else 1.0)
    total_weight = sum(weights)
    return round(
        sum(a.calibrated_confidence * weight for a, weight in zip(angles, weights))
        / total_weight,
        4,
    )


# ---------------------------------------------------------------------------
# Synthesis 演算法
# ---------------------------------------------------------------------------

def synthesize_angles(angles: list[AngleResult], coin: str, snapshot_id: str) -> MultiAngleReport:
    """確定性五角度綜合分析。

    Phase 1: 分類 active / abstained
    Phase 2: 全 abstain 快速路徑
    Phase 3: 方向統計
    Phase 4: 衝突偵測（direction_divergence + confidence_gap）
    Phase 5: 證據獨立性評估
    Phase 6: 共識推導
    Phase 7: agreement_matrix
    Phase 8: 報告組裝 + synthesis_summary 模板
    """
    # Phase 1: 分類
    active = [a for a in angles if a.decision_state != "abstain"]
    abstained = [a for a in angles if a.decision_state == "abstain"]

    # Phase 2: 全角度 abstain 快速路徑
    if not active:
        return _full_abstain_report(angles, coin, snapshot_id)

    # Phase 3: 方向統計（只計 active 角度中有明確方向的）
    direction_counts: Counter[str] = Counter()
    for a in active:
        if a.direction in ("偏多", "偏空", "中性"):
            direction_counts[a.direction] += 1

    # Phase 4: 衝突偵測
    conflicts: list[AngleConflict] = []
    for a1, a2 in combinations(active, 2):
        # 4a. 方向背離
        if _is_opposing(a1.direction, a2.direction):
            conflicts.append(AngleConflict(
                angle_a=a1.angle,
                angle_b=a2.angle,
                conflict_type="direction_divergence",
                detail={"a_direction": a1.direction, "b_direction": a2.direction},
                summary=(
                    f"{MODE_LABELS.get(a1.angle, a1.angle)}{a1.direction}，"
                    f"{MODE_LABELS.get(a2.angle, a2.angle)}{a2.direction}。"
                ),
            ))
        # 4b. 信心差距
        gap = abs(a1.calibrated_confidence - a2.calibrated_confidence)
        if (gap > CONFIDENCE_GAP_THRESHOLD
                and a1.decision_state == "normal"
                and a2.decision_state == "normal"):
            conflicts.append(AngleConflict(
                angle_a=a1.angle,
                angle_b=a2.angle,
                conflict_type="confidence_gap",
                detail={
                    "gap": round(gap, 4),
                    "a_confidence": a1.calibrated_confidence,
                    "b_confidence": a2.calibrated_confidence,
                },
                summary=(
                    f"{MODE_LABELS.get(a1.angle, a1.angle)}信心 {a1.calibrated_confidence:.2f}，"
                    f"{MODE_LABELS.get(a2.angle, a2.angle)}信心 {a2.calibrated_confidence:.2f}，"
                    f"差距 {gap:.2f}。"
                ),
            ))

    # Phase 5: 證據獨立性評估
    per_angle_sources: list[set[str]] = []
    for a in angles:
        per_angle_sources.append(a.evidence_sources)

    # 驗收定義：逐對計算 Jaccard overlap，再取平均。空集合對不提供
    # 「獨立」證據，故記為 overlap=1（independence=0），避免資料缺失反而
    # 被獎勵成 100% 獨立。
    source_pairs = list(combinations(per_angle_sources, 2))
    if source_pairs:
        overlaps = []
        for (left, right), (left_angle, right_angle) in zip(
            source_pairs, combinations(angles, 2)
        ):
            overlap = (len(left & right) / len(left | right)) if (left | right) else 1.0
            overlaps.append(overlap)
            if overlap > EVIDENCE_OVERLAP_WARNING_THRESHOLD:
                conflicts.append(AngleConflict(
                    angle_a=left_angle.angle,
                    angle_b=right_angle.angle,
                    conflict_type="evidence_overlap",
                    detail={"jaccard_overlap": round(overlap, 4)},
                    summary=(
                        f"{MODE_LABELS.get(left_angle.angle, left_angle.angle)}與"
                        f"{MODE_LABELS.get(right_angle.angle, right_angle.angle)}"
                        f"證據來源重疊 {overlap:.0%}。"
                    ),
                ))
        overlap_ratio = sum(overlaps) / len(overlaps)
        evidence_independence = round(1.0 - overlap_ratio, 4)
    else:
        evidence_independence = 0.0

    limits: list[str] = []
    if evidence_independence < (1.0 - EVIDENCE_OVERLAP_WARNING_THRESHOLD):
        limits.append(
            f"五角度證據獨立性偏低（{evidence_independence:.0%}），"
            f"多數角度引用相同來源，交叉佐證力有限。"
        )

    # Phase 6: 共識推導
    consensus, consensus_confidence = _derive_consensus(
        active, abstained, conflicts, direction_counts,
    )

    # abstain 角度的 limit
    if abstained:
        abstain_names = "、".join(MODE_LABELS.get(a.angle, a.angle) for a in abstained)
        limits.append(f"以下角度因資料不足棄權：{abstain_names}。綜合結論不含這些角度。")

    # Phase 7: agreement_matrix
    agreement_matrix = _build_agreement_matrix(angles)

    # Phase 8: 報告組裝 + synthesis_summary
    synthesis_summary = _build_synthesis_summary(
        consensus, consensus_confidence, active, abstained, conflicts, evidence_independence,
    )

    return MultiAngleReport(
        coin=coin,
        snapshot_id=snapshot_id,
        angles=angles,
        consensus=consensus,
        consensus_confidence=consensus_confidence,
        conflicts=conflicts,
        agreement_matrix=agreement_matrix,
        synthesis_summary=synthesis_summary,
        evidence_independence=evidence_independence,
        decision_state="partial_abstain" if abstained else "normal",
        limits=limits,
    )


# ---------------------------------------------------------------------------
# 內部函式
# ---------------------------------------------------------------------------

def _full_abstain_report(angles: list[AngleResult], coin: str, snapshot_id: str) -> MultiAngleReport:
    """全角度 abstain 時的快速路徑。"""
    return MultiAngleReport(
        coin=coin,
        snapshot_id=snapshot_id,
        angles=angles,
        consensus="不明",
        consensus_confidence=0.0,
        conflicts=[],
        agreement_matrix=_build_agreement_matrix(angles),
        synthesis_summary="五角度均因資料不足棄權，無法產出綜合結論。",
        evidence_independence=0.0,
        decision_state="full_abstain",
        limits=["所有分析角度均因證據不足而棄權，目前無法給出任何方向性結論。"],
    )


def _derive_consensus(
    active: list[AngleResult],
    abstained: list[AngleResult],
    conflicts: list[AngleConflict],
    direction_counts: Counter[str],
) -> tuple[str, float]:
    """確定性共識推導。

    規則：
    1. 有嚴格多數時，以多數決決定方向（即使少數角度反向）
    2. 無嚴格多數且同時有偏多/偏空 → "分歧"
    3. 無方向分歧但有 abstain → "partial_abstain"
    4. 其餘平手或中性最多 → "中性"
    """
    confidence = _weighted_confidence(active)

    bullish = direction_counts.get("偏多", 0)
    bearish = direction_counts.get("偏空", 0)
    neutral = direction_counts.get("中性", 0)
    directional_total = bullish + bearish + neutral

    if bullish > directional_total / 2:
        return "偏多", confidence
    if bearish > directional_total / 2:
        return "偏空", confidence
    if neutral > directional_total / 2:
        return "中性", confidence
    if bullish and bearish:
        return "分歧", confidence
    if abstained:
        return "partial_abstain", confidence
    return "中性", confidence


def _build_agreement_matrix(angles: list[AngleResult]) -> dict[str, dict[str, str]]:
    """建立角度兩兩關係矩陣。

    值：
    - "agree": 同方向（含雙方都是中性/不明）
    - "disagree": 方向相反
    - "one_abstain": 其中一方 abstain
    - "both_abstain": 雙方都 abstain
    - "self": 對角線
    """
    matrix: dict[str, dict[str, str]] = {}
    for a in angles:
        matrix[a.angle] = {}
        for b in angles:
            if a.angle == b.angle:
                matrix[a.angle][b.angle] = "self"
            elif a.decision_state == "abstain" and b.decision_state == "abstain":
                matrix[a.angle][b.angle] = "both_abstain"
            elif a.decision_state == "abstain" or b.decision_state == "abstain":
                matrix[a.angle][b.angle] = "one_abstain"
            elif _is_opposing(a.direction, b.direction):
                matrix[a.angle][b.angle] = "disagree"
            else:
                matrix[a.angle][b.angle] = "agree"
    return matrix


def _build_synthesis_summary(
    consensus: str,
    consensus_confidence: float,
    active: list[AngleResult],
    abstained: list[AngleResult],
    conflicts: list[AngleConflict],
    evidence_independence: float,
) -> str:
    """確定性模板組裝的摘要文字。不呼叫 LLM。"""
    parts: list[str] = []

    # 總覽
    total = len(active) + len(abstained)
    parts.append(f"五角度綜合（{len(active)}/{total} 角度有效）：")

    # 共識
    consensus_labels = {
        "偏多": "多數角度偏多",
        "偏空": "多數角度偏空",
        "中性": "整體中性",
        "分歧": "角度間出現方向分歧",
        "partial_abstain": "部分角度棄權，有效角度共識待確認",
        "full_abstain": "全部角度棄權，無法判定",
    }
    parts.append(consensus_labels.get(consensus, consensus))

    # 信心
    if consensus not in ("full_abstain",):
        parts.append(f"（加權信心 {consensus_confidence:.2f}）")

    # 衝突摘要
    divergences = [c for c in conflicts if c.conflict_type == "direction_divergence"]
    if divergences:
        pairs = "；".join(c.summary for c in divergences[:3])
        parts.append(f"。方向分歧：{pairs}")

    # 證據獨立性
    parts.append(f"。證據獨立性 {evidence_independence:.0%}。")

    # abstain 角度
    if abstained:
        names = "、".join(MODE_LABELS.get(a.angle, a.angle) for a in abstained)
        parts.append(f"棄權角度：{names}。")

    return "".join(parts)


# ---------------------------------------------------------------------------
# LLM Narration（選填，#811）
# ---------------------------------------------------------------------------

_NARRATION_SYSTEM = (
    "你是 TrustForge 的分析敘事助手。"
    "你的唯一工作是把已經算好的結構化分析結果用流暢的中文摘要描述。"
    "你不可以自行發明任何交叉訊號、方向判斷或結論。"
    "你只能敘述以下 JSON 資料中已存在的結論和數值。"
)

_NARRATION_TEMPLATE = """{coin} 的五角度綜合分析結構化結果：

共識：{consensus}
加權信心：{consensus_confidence:.2f}
證據獨立性：{evidence_independence:.0%}

角度結果：
{angles_summary}

衝突清單：
{conflicts_summary}

限制：
{limits}

請用 2-3 句話摘要上述結果，語氣中性專業。不可添加任何原始資料中沒有的判斷。"""


def narrate_synthesis(
    report: MultiAngleReport,
    client: "Any",
    log: "Any | None" = None,
) -> str:
    """用 Bedrock 把 MultiAngleReport 改寫成人類可讀摘要（#811）。

    硬約束：LLM 不可自行發明交叉訊號。
    空輸出降級：回傳 report.synthesis_summary。Provider 例外向外傳給
    accounting context 記保守成本，再由 synthesis caller 做結構化降級。
    離線 / client.offline → 直接回傳 synthesis_summary。

    由共享的 Admin/env/default resolver 控制是否啟用；預設開啟，env 可
    fail-closed 緊急阻斷。
    """
    from .admin_config import multi_angle_narration_enabled_resolved
    enabled, _ = multi_angle_narration_enabled_resolved()
    if not enabled:
        return report.synthesis_summary

    # 離線直接降級
    if getattr(client, "offline", True):
        return report.synthesis_summary

    # 組裝 prompt
    angles_summary = "\n".join(
        f"- {MODE_LABELS.get(a.angle, a.angle)}：{a.direction}（信心 {a.calibrated_confidence:.2f}，{a.decision_state}）"
        for a in report.angles
    )
    conflicts_summary = "\n".join(
        f"- {c.summary}" for c in report.conflicts
    ) or "無衝突"
    limits_text = "\n".join(f"- {lim}" for lim in report.limits) or "無"

    prompt = _NARRATION_TEMPLATE.format(
        coin=report.coin,
        consensus=report.consensus,
        consensus_confidence=report.consensus_confidence,
        evidence_independence=report.evidence_independence,
        angles_summary=angles_summary,
        conflicts_summary=conflicts_summary,
        limits=limits_text,
    )

    # Provider exceptions must escape into `_bedrock_live_attempt`: a timeout can
    # mean the provider accepted work even though no usage response arrived.
    # The accounting context records the conservative reservation before release;
    # `_complete_claimed_synthesis` owns the structural-summary fallback.
    result = client.complete(system=_NARRATION_SYSTEM, prompt=prompt)
    if log is not None and getattr(result, "model_id", None):
        from .ledger import estimate_cost
        log.record_llm_cost(
            result.model_id,
            result.input_tokens,
            result.output_tokens,
            estimate_cost(
                result.model_id,
                result.input_tokens,
                result.output_tokens,
            ),
        )
    narration = result.text.strip() if hasattr(result, "text") else str(result).strip()
    if narration and len(narration) > 10:
        return narration

    # 降級
    return report.synthesis_summary
