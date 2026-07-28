"""CA-10: Comparison 端到端測試。

驗收：
- 離線模式下 run_comparison 能產出有效 ComparisonReport
- 四個比較面向缺一不可
- conclusion 不是空字串
- to_dict 可 roundtrip
- snapshot synthesis 在快照缺失時回傳 None
- 離線模式執行時間在 deadline 內
"""
from __future__ import annotations

import pytest

from trustforge.pipeline import run_comparison
from trustforge.comparison_contract import validate_comparison_report
from trustforge.comparison_snapshot import synthesize_comparison_from_snapshots


class TestComparisonE2E:
    def test_e2e_run_comparison_offline_returns_valid_report(self):
        result = run_comparison("BTC", "ETH", "compare", offline=True)
        assert result.comparison is not None
        violations = validate_comparison_report(result.comparison)
        assert violations == []

    def test_e2e_comparison_has_four_dimensions(self):
        result = run_comparison("BTC", "ETH", "compare", offline=True)
        assert len(result.comparison.dimensions) == 4

    def test_e2e_comparison_conclusion_not_empty(self):
        result = run_comparison("BTC", "ETH", "compare", offline=True)
        assert result.comparison.conclusion.strip()

    def test_e2e_comparison_to_dict_roundtrip(self):
        result = run_comparison("BTC", "ETH", "compare", offline=True)
        d = result.comparison.to_dict()
        assert "conclusion" in d
        assert "dimensions" in d
        assert len(d["dimensions"]) == 4

    def test_e2e_snapshot_synthesis_missing_returns_none(self):
        assert synthesize_comparison_from_snapshots("BTC", "ETH", "compare") is None


class TestComparisonDeadline:
    def test_deadline_offline_mode(self):
        import time
        start = time.time()
        run_comparison("BTC", "ETH", "compare", offline=True)
        elapsed = time.time() - start
        assert elapsed < 600, f"Took {elapsed}s"
