"""Structural guards for the pending issue #501 decision record."""

from pathlib import Path


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
    assert len({row["prediction_id"] for row in rows}) == len(rows)
    assert all(all(row[column] for column in headers) for row in rows)
    assert all(len(row["expected"].split("/")) == 10 for row in rows)


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
        "revision_dual",
    } <= fixture_ids


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
