"""Guard the decision record required by issue #501.

These tests intentionally validate documentation completeness only.  They do
not turn recommendations into an approved product contract.
"""

from pathlib import Path


DOC = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "decisions"
    / "OUTCOME-SEMANTICS-2026-07-23.md"
)


def test_outcome_semantics_remains_explicitly_pending_ceo_disposition() -> None:
    text = DOC.read_text(encoding="utf-8")

    assert "PENDING CEO DISPOSITION — NOT APPROVED FOR IMPLEMENTATION" in text
    for decision in range(1, 9):
        assert f"D{decision}" in text
    assert text.count("| PENDING |") >= 9
    assert "exact commit SHA" in text


def test_outcome_semantics_covers_required_contract_and_edges() -> None:
    text = DOC.read_text(encoding="utf-8")

    required_terms = (
        "prediction_event_at",
        "prediction_available_at",
        "calendar_id",
        "timezone",
        "start_session",
        "target_session",
        "return_pct",
        "directional_return_pct",
        "risk_abs_move_pct",
        "risk_downside_pct",
        "maturity",
        "pending",
        "unavailable",
        "週末／假日",
        "停牌",
        "公司行動",
        "晚到",
        "行情修訂",
    )
    for term in required_terms:
        assert term in text


def test_fixture_table_has_each_required_boundary_case() -> None:
    text = DOC.read_text(encoding="utf-8")

    fixture_ids = (
        "daily_bull_up",
        "daily_bear_down",
        "flat_is_miss",
        "weekend_skip",
        "holiday_skip_t7",
        "after_cutoff",
        "suspension_no_slide",
        "split_adjusted",
        "missing_start",
        "not_mature_t14",
        "neutral_unscored",
        "late_before_cutoff",
        "revision_dual",
    )
    for fixture_id in fixture_ids:
        assert f"`{fixture_id}`" in text
