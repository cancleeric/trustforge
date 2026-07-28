"""Comparison Analysis Contract — 結構化比較報告的資料模型。

定義 CA-01 到 CA-10 的比較分析契約（ComparisonReport, ComparisonDimension,
ComparisonRunResult）。CA-01 階段僅定義資料結構作為 golden test 的規格；
CA-02 階段實作具體 schema 驗證與序列化。

不修改 DB schema/migration。

REF: docs/plans/COMPARISON-ANALYSIS-DEVELOPMENT-PLAN-20260728.md
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from .schema import BasisItem, Evidence, Report


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

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> DimensionResult:
        return cls(**d)


# ---------------------------------------------------------------------------
# 比較報告
# ---------------------------------------------------------------------------

def _report_from_dict(d: dict) -> Report:
    """將 asdict(Report) 產生的 dict 還原為 Report，遞迴重建嵌套 dataclass。"""
    d = dict(d)
    if "key_basis" in d:
        d["key_basis"] = [BasisItem(**item) for item in d["key_basis"]]
    if d.get("insights"):
        from .trust.insights import Insight, InsightContribution
        rebuilt_insights: list[Any] = []
        for ins in d["insights"]:
            ins = dict(ins)
            if "contributions" in ins:
                ins["contributions"] = [
                    InsightContribution(**c) for c in ins["contributions"]
                ]
            rebuilt_insights.append(Insight(**ins))
        d["insights"] = rebuilt_insights
    return Report(**d)


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

    def to_dict(self) -> dict:
        payload: dict[str, Any] = asdict(self)
        # 遞迴序列化 Report / Evidence（asdict 已處理嵌套 dataclass）
        return payload

    @classmethod
    def from_dict(cls, d: dict) -> ComparisonReport:
        d = dict(d)
        dims = [DimensionResult.from_dict(dim) for dim in d.pop("dimensions", [])]
        _raw_ra = d.pop("supporting_report_a", None)
        report_a = _report_from_dict(_raw_ra) if _raw_ra else None
        _raw_rb = d.pop("supporting_report_b", None)
        report_b = _report_from_dict(_raw_rb) if _raw_rb else None
        ev_a = [Evidence(**e) for e in d.pop("supporting_evidence_a", [])]
        ev_b = [Evidence(**e) for e in d.pop("supporting_evidence_b", [])]
        return cls(
            dimensions=dims,
            supporting_report_a=report_a,
            supporting_report_b=report_b,
            supporting_evidence_a=ev_a,
            supporting_evidence_b=ev_b,
            **d,
        )

    @classmethod
    def from_a_b_reports(
        cls,
        coin_a: str,
        coin_b: str,
        query: str,
        report_a: Report,
        evidence_a: list[Evidence],
        report_b: Report,
        evidence_b: list[Evidence],
    ) -> ComparisonReport:
        """從 A/B pipeline 結果產生骨架 ComparisonReport。

        四個面向皆標為 abstain，conclusion 為佔位文字。
        CA-03 將填入實際比較內容。
        """
        dimensions = [
            DimensionResult(
                dimension=dim,
                label=DIMENSION_LABEL_MAP.get(dim, dim),
                finding="（尚待比較分析）",
                decision="abstain",
            )
            for dim in COMPARISON_DIMENSIONS
        ]
        return cls(
            coin_a=coin_a,
            coin_b=coin_b,
            query=query,
            conclusion=f"{coin_a} 與 {coin_b} 的比較分析尚待完成。",
            dimensions=dimensions,
            supporting_report_a=report_a,
            supporting_report_b=report_b,
            supporting_evidence_a=list(evidence_a),
            supporting_evidence_b=list(evidence_b),
        )


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
    log: Any = None  # ExecutionLog（CA-02 新增，向後相容）

    @property
    def has_comparison(self) -> bool:
        return self.comparison is not None

    def to_dict(self) -> dict:
        payload: dict[str, Any] = {
            "report_a": asdict(self.report_a),
            "report_b": asdict(self.report_b),
            "evidence_a": [asdict(e) for e in self.evidence_a],
            "evidence_b": [asdict(e) for e in self.evidence_b],
            "comparison": self.comparison.to_dict() if self.comparison else None,
            "log": self.log.events if self.log else None,
        }
        return payload

    @classmethod
    def from_dict(cls, d: dict) -> ComparisonRunResult:
        d = dict(d)
        report_a = _report_from_dict(d.pop("report_a"))
        report_b = _report_from_dict(d.pop("report_b"))
        ev_a = [Evidence(**e) for e in d.pop("evidence_a", [])]
        ev_b = [Evidence(**e) for e in d.pop("evidence_b", [])]
        comparison = ComparisonReport.from_dict(d["comparison"]) if d.get("comparison") else None
        # log 為 ExecutionLog，暫不反序列化（保留 events 即可）
        return cls(
            report_a=report_a,
            report_b=report_b,
            evidence_a=ev_a,
            evidence_b=ev_b,
            comparison=comparison,
            log=d.get("log"),
        )

    def __len__(self) -> int:
        return 5

    def __iter__(self):
        """允許 unpack 為 5-tuple：(report_a, ev_a, report_b, ev_b, log)。

        最後一個元素為 self.log（ExecutionLog），確保舊程式碼
        ``ra, ea, rb, eb, log = run_comparison(...)`` 仍能執行。
        """
        yield self.report_a
        yield self.evidence_a
        yield self.report_b
        yield self.evidence_b
        yield self.log


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

    # 2. 四個面向缺一不可，且不可有重複或未知面向
    present_dimensions = {d.dimension for d in comparison.dimensions}
    dim_names = [d.dimension for d in comparison.dimensions]
    for dim in COMPARISON_DIMENSIONS:
        if dim not in present_dimensions:
            violations.append(f"缺少比較面向：{dim}")
    for dim in present_dimensions:
        if dim not in COMPARISON_DIMENSIONS:
            violations.append(f"未知的比較面向：'{dim}'（須為 {COMPARISON_DIMENSIONS} 之一）")
    if len(dim_names) != len(set(dim_names)):
        violations.append("存在重複的比較面向（每個面向只能出現一次）")
    if len(dim_names) != len(COMPARISON_DIMENSIONS):
        violations.append(
            f"比較面向數量不正確：預期 {len(COMPARISON_DIMENSIONS)} 個，實際 {len(dim_names)} 個"
        )

    VALID_DECISIONS = ("abstain", "insufficient", "normal")

    # 3. 每個面向的 evidence refs 必須有效，且 confidence/decision 合規
    ev_a_count = len(comparison.supporting_evidence_a)
    ev_b_count = len(comparison.supporting_evidence_b)
    for d in comparison.dimensions:
        if not (0.0 <= d.confidence <= 1.0):
            violations.append(
                f"面向 '{d.dimension}' 的 confidence 超出範圍：{d.confidence}"
            )
        if d.decision not in VALID_DECISIONS:
            violations.append(
                f"面向 '{d.dimension}' 的 decision 無效：'{d.decision}'"
                f"（須為 {VALID_DECISIONS} 之一）"
            )
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
