"""Structural guards for the pending issue #501 decision record."""

from datetime import datetime
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
import hashlib
import json
from pathlib import Path
import re


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
    assert len(set(prediction_ids)) == len(rows) - 3
    assert prediction_ids.count("p20") == 2
    assert prediction_ids.count("p14") == 2
    assert prediction_ids.count("p23") == 2
    assert all(all(row[column] for column in headers) for row in rows)
    for row in rows:
        assert isinstance(json.loads(row["calendar_sessions"]), list)
        assert isinstance(json.loads(row["bars"]), list)
        expected = json.loads(row["expected"])
        assert {
            "maturity", "reason", "start", "target", "return", "directional",
            "abs_risk", "downside", "hit", "version",
        } <= expected.keys()


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
        "late_after_cutoff_v1",
        "late_after_cutoff_v2",
        "split_adjusted_asof",
        "dividend_price_only",
        "adjustment_future_hidden",
        "zero_start",
        "revision_v1",
        "revision_v2",
        "missing_lineage",
    } <= fixture_ids
    assert {row["horizon"] for row in rows} == {"T+1", "T+7", "T+14"}


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
        for session in json.loads(row["calendar_sessions"]):
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", session["label"])
            assert session["status"] in {"open", "closed", "unknown"}
            if session["scheduled_close_at"] is not None:
                _parse_rfc3339(session["scheduled_close_at"])
        for bar in json.loads(row["bars"]):
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", bar["session"])
            _parse_rfc3339(bar["event_at"])
            _parse_rfc3339(bar["available_at"])
            Decimal(bar["close"])


def test_session_close_mapping_is_deterministic() -> None:
    text = DOC.read_text(encoding="utf-8")
    _, rows = _table_after(text, "## 7. 人工演算與 fixture 決策表")
    for row in rows:
        sessions = {item["label"]: item for item in json.loads(row["calendar_sessions"])}
        for bar in json.loads(row["bars"]):
            session = sessions[bar["session"]]
            assert session["status"] == "open"
            assert bar["event_at"] == session["scheduled_close_at"]
        if row["calendar_id"] == "crypto:UTC:v1":
            for session in sessions.values():
                label_start = datetime.fromisoformat(session["label"] + "T00:00:00+00:00")
                close = _parse_rfc3339(session["scheduled_close_at"])
                assert (close - label_start).total_seconds() == 86400


def test_numeric_formula_and_revision_pair_contract() -> None:
    text = DOC.read_text(encoding="utf-8")
    _, rows = _table_after(text, "## 7. 人工演算與 fixture 決策表")
    by_id = {row["fixture_id"]: row for row in rows}

    after = json.loads(by_id["after_cutoff"]["expected"])["return"]
    assert abs(Decimal(after) - (Decimal(106) / Decimal(102) - 1) * 100) <= Decimal("0.00000001")
    assert by_id["cutoff_equal"]["prediction_available_at"] == "2026-01-01T23:55:00Z"

    first = by_id["revision_v1"]
    second = by_id["revision_v2"]
    assert first["prediction_id"] == second["prediction_id"] == "p20"
    assert first["horizon"] == second["horizon"] == "T+1"
    first_expected = json.loads(first["expected"])
    second_expected = json.loads(second["expected"])
    assert first_expected["outcome_id"] == first_expected["canonical"]
    assert second_expected["outcome_id"] == second_expected["canonical"]
    assert second_expected["supersedes"] == first_expected["outcome_id"]
    assert second_expected["version"] > first_expected["version"]
    for fixture_id in ("revision_v1", "revision_v2"):
        row = by_id[fixture_id]
        as_of = _parse_rfc3339(row["as_of"])
        assert all(_parse_rfc3339(bar["available_at"]) <= as_of for bar in json.loads(row["bars"]))
    first_as_of = _parse_rfc3339(first["as_of"])
    assert any(
        _parse_rfc3339(bar["available_at"]) > first_as_of
        for bar in json.loads(second["bars"])
    )

    late_first = json.loads(by_id["late_after_cutoff_v1"]["expected"])
    late_second = json.loads(by_id["late_after_cutoff_v2"]["expected"])
    assert late_first["maturity"] == "unavailable"
    assert late_second["supersedes"] == late_first["outcome_id"]
    assert late_second["version"] > late_first["version"]
    late_first_as_of = _parse_rfc3339(by_id["late_after_cutoff_v1"]["as_of"])
    assert any(
        _parse_rfc3339(bar["available_at"]) > late_first_as_of
        for bar in json.loads(by_id["late_after_cutoff_v2"]["bars"])
    )


def test_labeled_directional_fixture_formulas() -> None:
    text = DOC.read_text(encoding="utf-8")
    _, rows = _table_after(text, "## 7. 人工演算與 fixture 決策表")
    quantum = Decimal("0.00000001")
    signs = {"bullish": Decimal(1), "bearish": Decimal(-1)}
    for row in rows:
        expected = json.loads(row["expected"])
        bars = json.loads(row["bars"])
        if expected["maturity"] != "labeled" or row["direction"] not in signs:
            continue
        by_session = {bar["session"]: Decimal(bar["close"]) for bar in bars}
        if expected["start"] not in by_session or expected["target"] not in by_session:
            continue
        start = by_session[expected["start"]]
        target = by_session[expected["target"]]
        with localcontext() as context:
            context.prec = 34
            context.rounding = ROUND_HALF_EVEN
            calculated_return = (target / start - 1) * 100
            calculated_directional = calculated_return * signs[row["direction"]]
            assert Decimal(expected["return"]).as_tuple().exponent == -8
            assert Decimal(expected["directional"]).as_tuple().exponent == -8
            assert Decimal(expected["abs_risk"]).as_tuple().exponent == -8
            assert Decimal(expected["downside"]).as_tuple().exponent == -8
            assert Decimal(expected["return"]) == calculated_return.quantize(quantum)
            assert Decimal(expected["directional"]) == calculated_directional.quantize(quantum)
            assert Decimal(expected["abs_risk"]) == abs(calculated_return).quantize(quantum)
            assert Decimal(expected["downside"]) == min(calculated_return, Decimal(0)).quantize(quantum)
            assert expected["hit"] is (calculated_directional > 0)


def _canonical_hash(value: dict[str, object]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _market_revision(bars: list[dict[str, str]], start: str, target: str) -> str:
    payload = []
    stable_keys = (
        "provider", "dataset_version", "methodology_version", "event_at",
        "available_at", "content_hash",
    )
    for role, session in (("start", start), ("target", target)):
        matches = sorted(
            (bar for bar in bars if bar["session"] == session and "content_hash" in bar),
            key=lambda bar: tuple(bar[key] for key in stable_keys),
        )
        payload.extend({"role": role, "content_hash": bar["content_hash"]} for bar in matches)
    return _canonical_hash(payload)  # type: ignore[arg-type]


def test_fixture_lineage_and_outcome_identity_are_rebuildable() -> None:
    text = DOC.read_text(encoding="utf-8")
    _, rows = _table_after(text, "## 7. 人工演算與 fixture 決策表")
    for row in rows:
        bars = json.loads(row["bars"])
        expected = json.loads(row["expected"])
        for bar in bars:
            required = {
                "provider", "dataset_version", "methodology_version", "session",
                "event_at", "available_at", "close", "content_hash",
            }
            if row["fixture_id"] == "missing_lineage" and "content_hash" not in bar:
                assert expected["maturity"] == "unavailable"
                assert expected["reason"] == "PRICE_LINEAGE_MISSING"
                continue
            assert required <= bar.keys()
            assert bar["provider"] == "synthetic-fixture-only"
            canonical = "|".join(str(bar[key]) for key in (
                "provider", "dataset_version", "methodology_version", "session",
                "event_at", "available_at", "close",
            ))
            assert bar["content_hash"] == "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
        revision = _market_revision(bars, expected["start"], expected["target"])
        assert expected["market_data_revision"] == revision
        assert _market_revision(list(reversed(bars)), expected["start"], expected["target"]) == revision
        if row["fixture_id"] == "missing_lineage":
            assert expected["partial_role_set"] == ["start"]
            assert expected["missing_roles"] == ["target"]
            assert expected["market_data_revision"] != "UNAVAILABLE"
        assert expected["outcome_id"] == _canonical_hash(expected["identity_inputs"])


def test_calendar_policy_derives_start_target_and_missing_data_state() -> None:
    text = DOC.read_text(encoding="utf-8")
    _, rows = _table_after(text, "## 7. 人工演算與 fixture 決策表")
    for row in rows:
        expected = json.loads(row["expected"])
        if row["fixture_id"] in {"invalid_timeline", "calendar_gap", "missing_lineage", "zero_start"}:
            assert expected["reason"] in {
                "INVALID_PREDICTION_TIMELINE", "CALENDAR_GAP", "PRICE_LINEAGE_MISSING",
                "ZERO_START_CLOSE",
            }
            continue
        sessions = json.loads(row["calendar_sessions"])
        open_sessions = [item for item in sessions if item["status"] == "open"]
        buffer = timedelta(minutes=5 if row["calendar_id"] == "crypto:UTC:v1" else 15)
        available = _parse_rfc3339(row["prediction_available_at"])
        eligible_starts = [
            item for item in open_sessions
            if available <= _parse_rfc3339(item["scheduled_close_at"]) - buffer
        ]
        assert eligible_starts
        start = eligible_starts[0]
        start_index = open_sessions.index(start)
        horizon = int(row["horizon"].removeprefix("T+"))
        assert start_index + horizon < len(open_sessions)
        target = open_sessions[start_index + horizon]
        assert expected["start"] == start["label"]
        assert expected["target"] == target["label"]

        target_bar = next(
            (bar for bar in json.loads(row["bars"]) if bar["session"] == target["label"]),
            None,
        )
        target_close = _parse_rfc3339(target["scheduled_close_at"])
        publication_sla = timedelta(hours=1 if row["calendar_id"] == "crypto:UTC:v1" else 4)
        matures_at = target_close + publication_sla
        late_boundary = matures_at + timedelta(hours=72)
        as_of = _parse_rfc3339(row["as_of"])
        if target_bar is None:
            if as_of < target_close:
                assert expected["maturity"] == "pending"
                assert expected["reason"] == "NOT_MATURE"
            elif as_of <= late_boundary:
                assert expected["maturity"] == "pending"
                assert expected["reason"] == "WAITING_LATE_DATA_CUTOFF"
            else:
                assert expected["maturity"] == "unavailable"
                assert expected["reason"] in {"TARGET_CLOSE_MISSING", "LATE_AFTER_CUTOFF"}
        elif any(
            _parse_rfc3339(bar["available_at"]) > as_of
            for bar in json.loads(row["bars"])
            if bar["session"] in {start["label"], target["label"]}
        ):
            assert expected["maturity"] == "pending"
        else:
            assert expected["maturity"] == "labeled", row["fixture_id"]


def test_late_cutoff_is_72_elapsed_utc_hours_across_dst() -> None:
    text = DOC.read_text(encoding="utf-8")
    _, rows = _table_after(text, "## 7. 人工演算與 fixture 決策表")
    row = next(item for item in rows if item["fixture_id"] == "dst_72h_equal")
    sessions = json.loads(row["calendar_sessions"])
    target_close = _parse_rfc3339(sessions[1]["scheduled_close_at"])
    matures_at = target_close + timedelta(hours=4)
    cutoff = _parse_rfc3339(row["as_of"])
    assert cutoff - matures_at == timedelta(hours=72)
    assert json.loads(row["expected"])["maturity"] == "pending"


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
