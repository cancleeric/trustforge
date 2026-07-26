"""台灣官方來源日期／時間解析與 PIT 判定測試（issue #385）。

重點鎖住三件實測踩到的事：
- MOPS `發言時間` 無前導零（`"70003"` ＝ 07:00:03，不是 70 時）
- FSC `pubDate` 只有日精度，fail-closed 須取台北該日結束
- 發文日期與上架日期不一致時取較晚者
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest

from trustforge.ingestion.tw_datetime import (
    TAIPEI,
    day_precision_visible_at,
    end_of_taipei_day,
    is_visible_at,
    parse_rfc822,
    pit_visible_at,
    roc_date_to_date,
    roc_datetime_to_taipei,
    roc_time_to_time,
    taipei_date,
)

FIXTURES = Path(__file__).parent / "fixtures" / "taiwan"


# ── 民國年日期 ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1150725", date(2026, 7, 25)),   # 實測 MOPS 發言日期
        ("1150726", date(2026, 7, 26)),   # 實測 MOPS 出表日期
        ("1150105", date(2026, 1, 5)),    # 實測 TWSE 裁罰最早一筆
        ("990725", date(2010, 7, 25)),    # 民國 100 年前為 6 碼
        (" 1150725 ", date(2026, 7, 25)),  # 政府資料常帶前後空白
    ],
)
def test_roc_date_parses(raw: str, expected: date) -> None:
    assert roc_date_to_date(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None, "", "   ", "abc", 1150725,      # 型別／空值
        "115072", "11507255",                  # 位數不合
        "1151332",                             # 月日越界
        "0000725",                             # 民國年 0
    ],
)
def test_roc_date_fail_closed(raw: object) -> None:
    """髒值一律回 None，不拋例外——單筆壞掉不該炸掉整批。"""
    assert roc_date_to_date(raw) is None


# ── 發言時間（無前導零）────────────────────────────────────────────────────

def test_roc_time_no_leading_zero_is_the_whole_point() -> None:
    """實測值 "70003" ＝ 07:00:03。直接切字串會誤解成 70 時。"""
    assert roc_time_to_time("70003") == time(7, 0, 3)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("70003", time(7, 0, 3)),
        ("070003", time(7, 0, 3)),
        ("133045", time(13, 30, 45)),
        ("235959", time(23, 59, 59)),
        ("900", time(9, 0, 0)),      # 4 碼以下視為 HHMM
        ("1430", time(14, 30, 0)),
    ],
)
def test_roc_time_parses(raw: str, expected: time) -> None:
    assert roc_time_to_time(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None, "", "xx", 70003,   # 型別／空值
        "1", "1234567",           # 位數不合
        "996060",                 # 時分秒越界
    ],
)
def test_roc_time_fail_closed(raw: object) -> None:
    assert roc_time_to_time(raw) is None


def test_roc_datetime_combines_with_taipei_tz() -> None:
    moment = roc_datetime_to_taipei("1150725", "70003")
    assert moment == datetime(2026, 7, 25, 7, 0, 3, tzinfo=TAIPEI)


def test_roc_datetime_missing_time_falls_back_to_midnight() -> None:
    """時間缺漏退回 00:00 是保守側：只會讓資料更晚才被視為可見。"""
    assert roc_datetime_to_taipei("1150725") == datetime(
        2026, 7, 25, 0, 0, 0, tzinfo=TAIPEI
    )
    assert roc_datetime_to_taipei("1150725", "壞值") == datetime(
        2026, 7, 25, 0, 0, 0, tzinfo=TAIPEI
    )


def test_roc_datetime_bad_date_returns_none() -> None:
    assert roc_datetime_to_taipei("壞值", "70003") is None


# ── FSC RSS pubDate（日精度）────────────────────────────────────────────────

def test_pubdate_00_gmt_is_a_date_label_not_a_moment() -> None:
    """實測 pubDate 全為 00:00:00 GMT，換算台北為同日 08:00，日曆日一致。"""
    parsed = parse_rfc822("Tue, 21 Jul 2026 00:00:00 GMT")
    assert parsed == datetime(2026, 7, 21, 0, 0, tzinfo=timezone.utc)
    assert parsed.astimezone(TAIPEI).hour == 8
    assert taipei_date(parsed) == date(2026, 7, 21)


def test_day_precision_visible_at_is_end_of_taipei_day() -> None:
    """fail-closed：只知道「那天」，就當「那天結束」才看得到。"""
    visible = day_precision_visible_at("Tue, 21 Jul 2026 00:00:00 GMT")
    assert visible == datetime(2026, 7, 21, 23, 59, 59, tzinfo=TAIPEI)


@pytest.mark.parametrize("raw", [None, "", "not a date", 12345])
def test_pubdate_fail_closed(raw: object) -> None:
    assert parse_rfc822(raw) is None
    assert day_precision_visible_at(raw) is None


def test_end_of_taipei_day() -> None:
    assert end_of_taipei_day(date(2026, 7, 21)) == datetime(
        2026, 7, 21, 23, 59, 59, tzinfo=TAIPEI
    )


# ── PIT 可見時間 ──────────────────────────────────────────────────────────

def test_pit_takes_the_later_of_issue_and_listing_date() -> None:
    """實測案例：發文 7/21，dataserno 顯示 7/22 才上架 → 取 7/22。"""
    issued = end_of_taipei_day(date(2026, 7, 21))
    listed = end_of_taipei_day(date(2026, 7, 22))
    assert pit_visible_at(issued, listed) == listed


def test_pit_ignores_none_candidates() -> None:
    listed = end_of_taipei_day(date(2026, 7, 22))
    assert pit_visible_at(None, listed, None) == listed


def test_pit_all_none_returns_none() -> None:
    """無法判定可見時間 ＝ 該筆不可用，呼叫端應跳過。"""
    assert pit_visible_at(None, None) is None


def test_pit_compares_across_timezones() -> None:
    utc_later = datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc)
    taipei_earlier = datetime(2026, 7, 22, 1, 0, tzinfo=TAIPEI)  # ＝ 7/21 17:00 UTC
    assert pit_visible_at(taipei_earlier, utc_later) == utc_later


# ── PIT 閘門 ─────────────────────────────────────────────────────────────

def test_gate_excludes_data_published_after_analysis_time() -> None:
    """#385 驗收條件：PIT 測試排除分析時間後發布資料。"""
    visible = end_of_taipei_day(date(2026, 7, 22))
    as_of = datetime(2026, 7, 22, 9, 0, tzinfo=TAIPEI)  # 分析當下該日尚未結束
    assert is_visible_at(visible, as_of) is False


def test_gate_admits_data_already_visible() -> None:
    visible = end_of_taipei_day(date(2026, 7, 21))
    as_of = datetime(2026, 7, 22, 9, 0, tzinfo=TAIPEI)
    assert is_visible_at(visible, as_of) is True


def test_gate_boundary_is_inclusive() -> None:
    visible = end_of_taipei_day(date(2026, 7, 21))
    assert is_visible_at(visible, visible) is True
    assert is_visible_at(visible, visible - timedelta(seconds=1)) is False


def test_gate_fail_closed_on_unknown_visibility() -> None:
    assert is_visible_at(None, datetime.now(timezone.utc)) is False


def test_gate_without_as_of_does_not_restrict() -> None:
    assert is_visible_at(end_of_taipei_day(date(2026, 7, 21)), None) is True


# ── 對真實 fixture 跑一遍（contract test）──────────────────────────────────

def test_every_mops_record_in_fixture_parses() -> None:
    """實測 fixture 的每一筆日期時間都要解得出來，否則就是解析器有洞。"""
    for name in ("mops_twse.json", "mops_tpex.json"):
        records = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        assert records, f"{name} fixture 不應為空"
        for record in records:
            fields = {key.strip(): value for key, value in record.items()}
            moment = roc_datetime_to_taipei(fields["發言日期"], fields["發言時間"])
            assert moment is not None, f"{name} 解析失敗：{fields['發言日期']}"
            assert moment.tzinfo is not None


def test_twse_punish_fixture_dates_parse_and_span_history() -> None:
    """裁罰專區有年度歷史（與重大訊息的當日 snapshot 不同）。"""
    records = json.loads((FIXTURES / "twse_punish.json").read_text(encoding="utf-8"))
    days = {roc_date_to_date(r["發函日期"]) for r in records}
    assert None not in days
    assert len(days) > 1, "裁罰專區應橫跨多個日期"


def test_fsc_fixture_pubdates_parse() -> None:
    import re

    raw = (FIXTURES / "fsc_penalty.xml").read_text(encoding="utf-8")
    pubdates = re.findall(r"<pubDate>(.*?)</pubDate>", raw)
    assert pubdates, "fixture 應含 pubDate"
    for value in pubdates:
        assert day_precision_visible_at(value) is not None
