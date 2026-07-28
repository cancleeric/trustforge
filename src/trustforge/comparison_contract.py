"""Comparison Analysis Contract — 結構化比較報告的資料模型。

定義 CA-01 到 CA-10 的比較分析契約（ComparisonReport, ComparisonDimension,
ComparisonRunResult）。CA-01 階段僅定義資料結構作為 golden test 的規格；
CA-02 階段實作具體 schema 驗證與序列化。

不修改 DB schema/migration。

REF: docs/plans/COMPARISON-ANALYSIS-DEVELOPMENT-PLAN-20260728.md
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .schema import Evidence, Report


# ---------------------------------------------------------------------------
# 比較面向
# ---------------------------------------------------------------------------

COMPARISON_DIMENSIONS = (
    "價格動能",
    "鏈上活動",
    "市場情緒",
    "生態發展",
)

DIMENSION_LABEL_MAP = {
    "價格動能": "價格動能比較",
    "鏈上活動": "鏈上活動比較",
    "市場情緒": "市場情緒比較",
    "生態發展": "生態發展比較",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 比較維度結果
# ---------------------------------------------------------------------------

@dataclass
class DimensionResult:
    """單一比較面向的分析結果。

    每個面向必須有雙邊 evidence refs（對應到 A/B 的證據索引），
    不可為單邊證據硬比較（單邊時 decision 應為 "abstain" 或 "insufficient"）。
    """
    dimension: str                           # 比較面向名稱（須在 COMPARISON_DIMENSIONS 中）
    label: str                               # 顯示標籤（如「價格動能比較」）
    finding: str                             # 本面向的比較結論文字
    a_evidence_refs: list[int] = field(default_factory=list)  # A 幣證據索引
    b_evidence_refs: list[int] = field(default_factory=list)  # B 幣證據索引
    confidence: float = 0.0                  # 本面向信心 0–1
    decision: str = "normal"                 # abstain | insufficient | normal


# ---------------------------------------------------------------------------
# 比較報告
# ---------------------------------------------------------------------------

@dataclass
class ComparisonReport:
    """結構化比較報告：取代「兩份單幣報告並排」。

    核心欄位：
    - conclusion: 綜合比較結論（共同判斷，非 A/B 各自結論的拼接）
    - dimensions: 四個比較面向的結構化結果
    - confidence: 整體比較信心（不得超過規則層 ceiling）
    - limits: 已知限制
    - could_flip: 可能推翻結論的條件
    - supporting_reports: 保留原始 A/B Report 作為支持細節
    - supporting_evidence: 保留原始 A/B Evidence 作為可追溯來源

    驗收條件（驗收函式見下方）：
    1. 四個面向缺一不可（可為 abstain，但不可缺失）
    2. 每個面向的 evidence refs 必須指向存在的 A/B evidence
    3. conclusion 不可為空
    4. 單邊證據不可硬比較（至少一邊 refs 為空且 decision != insufficient → fail）
    """
    coin_a: str
    coin_b: str
    query: str
    conclusion: str                           # 綜合比較結論
    dimensions: list[DimensionResult] = field(default_factory=list)
    confidence: float = 0.0
    limits: list[str] = field(default_factory=list)
    could_flip: list[str] = field(default_factory=list)
    generated_at: str = field(default_factory=_now_iso)
    # 支持細節：保留原始 A/B Report 與 Evidence
    supporting_report_a: Report | None = None
    supporting_report_b: Report | None = None
    supporting_evidence_a: list[Evidence] = field(default_factory=list)
    supporting_evidence_b: list[Evidence] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 比較執行結果
# ---------------------------------------------------------------------------

@dataclass
class ComparisonRunResult:
    """一次比較任務的完整執行結果。

    包含原始 A/B 分析（保持現有 pipeline 向後相容）、結構化比較報告、
    以及共用執行記錄。
    """
    report_a: Report
    report_b: Report
    evidence_a: list[Evidence]
    evidence_b: list[Evidence]
    comparison: ComparisonReport | None = None  # None 表示比較無法完成（降級）

    @property
    def has_comparison(self) -> bool:
        return self.comparison is not None


# ---------------------------------------------------------------------------
# 契約驗收函式（被 golden tests 呼叫）
# ---------------------------------------------------------------------------

def validate_comparison_report(
    comparison: ComparisonReport,
    _raise: bool = True,
) -> list[str]:
    """驗證 ComparisonReport 是否符合比較契約。

    Returns:
        list[str]: 違規訊息清單（空 list = 完全合規）
    """
    violations: list[str] = []

    # 1. 結論不可空
    if not comparison.conclusion.strip():
        violations.append("conclusion 不可為空")

    # 2. 四個面向缺一不可
    present_dimensions = {d.dimension for d in comparison.dimensions}
    for dim in COMPARISON_DIMENSIONS:
        if dim not in present_dimensions:
            violations.append(f"缺少比較面向：{dim}")

    # 3. 每個面向的 evidence refs 必須有效
    ev_a_count = len(comparison.supporting_evidence_a)
    ev_b_count = len(comparison.supporting_evidence_b)
    for d in comparison.dimensions:
        for ref in d.a_evidence_refs:
            if ref < 0 or ref >= ev_a_count:
                violations.append(
                    f"面向 '{d.dimension}' 的 a_evidence_refs[{ref}] 超出範圍"
                    f"（A evidence 共 {ev_a_count} 筆）"
                )
        for ref in d.b_evidence_refs:
            if ref < 0 or ref >= ev_b_count:
                violations.append(
                    f"面向 '{d.dimension}' 的 b_evidence_refs[{ref}] 超出範圍"
                    f"（B evidence 共 {ev_b_count} 筆）"
                )

    # 4. 單邊證據不可硬比較（兩邊 refs 都非空才算真正比較）
    for d in comparison.dimensions:
        has_a = len(d.a_evidence_refs) > 0
        has_b = len(d.b_evidence_refs) > 0
        if not has_a and not has_b:
            # 兩邊都沒證據 → 必須 abstain
            if d.decision not in ("abstain", "insufficient"):
                violations.append(
                    f"面向 '{d.dimension}' 雙邊皆無證據，decision 應為 'abstain'"
                    f" 或 'insufficient'，實際為 '{d.decision}'"
                )
        elif not has_a or not has_b:
            # 單邊有證據 → 不該正常比較
            if d.decision == "normal":
                violations.append(
                    f"面向 '{d.dimension}' 僅有單邊證據，不可標記為 'normal'"
                )

    # 5. confidence 必須在 0–1 範圍
    if not (0.0 <= comparison.confidence <= 1.0):
        violations.append(
            f"confidence 超出範圍：{comparison.confidence}"
        )

    # 6. coin_a / coin_b 不可相同
    if comparison.coin_a == comparison.coin_b:
        violations.append("coin_a 與 coin_b 不可相同")

    if _raise and violations:
        msg = "\n".join(violations)
        raise ValueError(f"ComparisonReport 契約驗證失敗 ({len(violations)} 項):\n{msg}")

    return violations


def validate_dimension_coverage(
    comparison: ComparisonReport,
) -> dict[str, bool]:
    """檢查四個面向的覆蓋狀態。

    Returns:
        dict[dimension -> bool]: True 表示該面向有足夠雙邊證據做正常比較
    """
    result: dict[str, bool] = {}
    for d in comparison.dimensions:
        has_a = len(d.a_evidence_refs) > 0
        has_b = len(d.b_evidence_refs) > 0
        normal = d.decision == "normal"
        result[d.dimension] = has_a and has_b and normal
    # 補上缺失的面向（標為未覆蓋）
    present = {d.dimension for d in comparison.dimensions}
    for dim in COMPARISON_DIMENSIONS:
        if dim not in present:
            result[dim] = False
    return result
