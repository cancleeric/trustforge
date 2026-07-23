"""Structural guards for the pending issue #501 decision record."""

from pathlib import Path
import re
from datetime import datetime
from decimal import Decimal


DOC = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "decisions"
    / "OUTCOME-SEMANTICS-2026-07-23.md"
)


def _table_after(text: str, heading: str) -> tuple[list[str], list[dict[str, str]]]:
    """Parse the first Markdown table after an exact heading."""
    section = text.split(heading, maxsplit=1)[1]
    lines = section.splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith("|"))
    table_lines: list[str] = []
    for line in lines[start:]:
        if not line.startswith("|"):
            break
        table_lines.append(line)
    headers = [cell.strip() for cell in table_lines[0].strip("|").split("|")]
    rows = []
    for line in table_lines[2:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        assert len(cells) == len(headers), (headers, cells)
        rows.append(dict(zip(headers, cells, strict=True)))
    return headers, rows


def test_disposition_table_has_exact_pending_decisions() -> None:
    text = DOC.read_text(encoding="utf-8")
    assert "PENDING CEO DISPOSITION — NOT APPROVED FOR IMPLEMENTATION" in text

    headers, rows = _table_after(text, "## 10. CEO / product owner disposition（必填）")
    assert headers == [
        "Decision",
        "CEO disposition（approve option / reject / revise）",
        "理由",
        "日期",
        "commit SHA",
    ]
    assert [row["Decision"] for row in rows] == [
        "D1 calendar",
        "D2 T+N endpoint",
        "D3 start price",
        "D4 neutral",
        "D5 tie",
        "D6 corporate actions",
        "D7 revisions",
        "D8 late data",
        "cutoff SLA / asset scope",
    ]
    assert all(
        row["CEO disposition（approve option / reject / revise）"] == "PENDING"
        and row["理由"] == ""
        and row["日期"] == ""
        and row["commit SHA"] == ""
        for row in rows
    )


def test_fixture_table_is_parseable_and_has_complete_expected_shape() -> None:
    text = DOC.read_text(encoding="utf-8")
    headers, rows = _table_after(text, "## 7. 人工演算與 fixture 決策表")
    assert headers == [
        "fixture_id",
        "calendar_id",
        "calendar_sessions",
        "prediction_id",
        "prediction_event_at",
        "prediction_available_at",
        "direction",
        "horizon",
        "bars",
        "as_of",
        "market_data_variant",
        "expected",
    ]
    assert len(rows) >= 20
    assert len({row["fixture_id"] for row in rows}) == len(rows)
    prediction_ids = [row["prediction_id"] for row in rows]
    assert len(set(prediction_ids)) == len(rows) - 1
    assert prediction_ids.count("p20") == 2
    assert all(all(row[column] for column in headers) for row in rows)
    assert all(len(row["expected"].split(";", 1)[0].split("/")) == 10 for row in rows)


def test_fixture_table_covers_required_adversarial_cases() -> None:
    text = DOC.read_text(encoding="utf-8")
    _, rows = _table_after(text, "## 7. 人工演算與 fixture 決策表")
    fixture_ids = {row["fixture_id"] for row in rows}
    assert {
        "bearish_miss",
        "cutoff_equal",
        "invalid_timeline",
        "dst_calendar",
        "early_close",
        "emergency_closed",
        "calendar_gap",
        "suspension_no_slide",
        "target_missing",
        "late_after_cutoff",
        "split_adjusted_asof",
        "dividend_price_only",
        "adjustment_future_hidden",
        "zero_start",
        "revision_v1",
        "revision_v2",
    } <= fixture_ids


def _parse_rfc3339(value: str) -> datetime:
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_fixture_primary_timestamps_are_rfc3339_and_pit_ordered() -> None:
    text = DOC.read_text(encoding="utf-8")
    _, rows = _table_after(text, "## 7. 人工演算與 fixture 決策表")
    for row in rows:
        event_at = _parse_rfc3339(row["prediction_event_at"])
        available_at = _parse_rfc3339(row["prediction_available_at"])
        _parse_rfc3339(row["as_of"])
        if row["fixture_id"] == "invalid_timeline":
            assert event_at > available_at
        else:
            assert event_at <= available_at


def test_numeric_formula_and_revision_pair_contract() -> None:
    text = DOC.read_text(encoding="utf-8")
    _, rows = _table_after(text, "## 7. 人工演算與 fixture 決策表")
    by_id = {row["fixture_id"]: row for row in rows}

    after = by_id["after_cutoff"]["expected"].split("/", 5)[4]
    assert abs(Decimal(after) - (Decimal(106) / Decimal(102) - 1) * 100) <= Decimal("0.00000001")
    assert by_id["cutoff_equal"]["prediction_available_at"] == "2026-01-01T23:55:00Z"

    first = by_id["revision_v1"]
    second = by_id["revision_v2"]
    assert first["prediction_id"] == second["prediction_id"] == "p20"
    assert first["horizon"] == second["horizon"] == "T+1"
    assert "outcome_id=o1" in first["expected"] and "canonical=o1" in first["expected"]
    assert "outcome_id=o2" in second["expected"] and "supersedes=o1" in second["expected"]


def test_revision_identity_and_reconciliation_are_explicit() -> None:
    text = DOC.read_text(encoding="utf-8")
    required_clauses = (
        "`market_data_variant`, `market_data_revision`, `outcome_version`",
        "`current(as_of)`",
        "`canonical(as_of, variant)`",
        "scheduled open/close",
        "instrument 停牌或缺 bar 不得改變 start/target session",
        "prediction_cutoff_buffer",
        "publication_lag_sla",
        "late_data_cutoff",
        "maturity 軸",
        "eligibility 軸",
    )
    for clause in required_clauses:
        assert clause in text
