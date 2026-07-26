"""台灣官方來源的日期／時間解析與 PIT 可見時間判定（issue #385）。

兩類來源的時間表述完全不同，各自有坑：

1. FSC RSS `<pubDate>`：RFC822 GMT，但**只有日精度**——實測全部為
   `Tue, 21 Jul 2026 00:00:00 GMT`。這個 `00:00:00 GMT` 是**日期標籤**，
   不是真實發布時刻（換算台北為同日 08:00，日曆日與台北一致）。
   無從得知該日幾點上線，因此 fail-closed 把可見時間視為
   **台北該日結束**（`23:59:59+08:00`）——寧可認定「較晚才看得到」，
   也不要宣稱比實際更早可見而造成未來資訊洩漏。

2. MOPS / TWSE OpenAPI：民國年 `"1150725"` + `發言時間` `"70003"`。
   時間欄位**無前導零**（`"70003"` ＝ 07:00:03），直接切字串會解成 70 時。
   這類有到秒的精度，依實際時刻處理，不套用日結束規則。

PIT 可見時間採 `max(發文日期, 上架日期)`：實測某裁罰案
`pubDate` 為 7/21（發文日期）但 `dataserno=202607220001`（7/22 才上架），
取較晚者才是資料真正對外可見的時間。見
`docs/audit/TAIWAN-REGULATORY-SOURCE-DISCOVERY-385.md` 第八節。

本模組所有 parse 函式一律 **fail-closed 回 None、絕不拋例外**——
政府資料集偶有髒值，單筆解析失敗應由呼叫端跳過該筆並記錄，
不該讓整批擷取炸掉（沿用 `regulatory.py` 的單筆失敗隔離精神）。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime

# 台北時區。台灣自 1980 年起無日光節約時間，固定 +08:00，
# 不需 zoneinfo（也避免容器缺 tzdata 時炸掉）。
TAIPEI = timezone(timedelta(hours=8), "Asia/Taipei")

# 民國年與西元年的差。民國 115 年 ＝ 西元 2026 年。
_ROC_YEAR_OFFSET = 1911


def roc_date_to_date(value: object) -> date | None:
    """民國年日期字串 → `date`。

    接受 7 碼（`"1150725"` ＝ 民國 115 年 7 月 25 日）與 6 碼
    （`"990725"` ＝ 民國 99 年）兩種寬度：民國 100 年前後位數不同，
    政府資料集兩種都出現過。

    無法解析（空值、非數字、月日越界、民國年 <= 0）一律回 None。
    """
    text = _clean(value)
    if text is None or not text.isdigit() or len(text) not in (6, 7):
        return None
    roc_year = int(text[:-4])
    if roc_year <= 0:
        return None
    try:
        return date(roc_year + _ROC_YEAR_OFFSET, int(text[-4:-2]), int(text[-2:]))
    except ValueError:
        # 月日越界（如 1151332）。
        return None


def roc_time_to_time(value: object) -> time | None:
    """MOPS `發言時間` → `time`。

    **無前導零**：`"70003"` ＝ 07:00:03，不是 70 時 00 分 03 秒。
    先補零到 6 碼再切，才不會把 5 碼值解錯。

    接受 5〜6 碼（HHMMSS）與 3〜4 碼（HHMM）。其餘回 None。
    """
    text = _clean(value)
    if text is None or not text.isdigit():
        return None
    if len(text) in (5, 6):
        padded = text.zfill(6)
        hour, minute, second = padded[:2], padded[2:4], padded[4:6]
    elif len(text) in (3, 4):
        padded = text.zfill(4)
        hour, minute, second = padded[:2], padded[2:4], "00"
    else:
        return None
    try:
        return time(int(hour), int(minute), int(second))
    except ValueError:
        return None


def roc_datetime_to_taipei(
    date_value: object, time_value: object = None
) -> datetime | None:
    """民國年日期（＋選填時間）→ 帶台北時區的 `datetime`。

    時間欄位缺漏或解析失敗時退回當日 00:00:00——這是**保守側**：
    對 PIT 而言較早的時刻只會讓資料更晚才被視為可見。
    """
    day = roc_date_to_date(date_value)
    if day is None:
        return None
    moment = roc_time_to_time(time_value) if time_value is not None else None
    return datetime.combine(day, moment or time(0, 0, 0), tzinfo=TAIPEI)


def parse_rfc822(value: object) -> datetime | None:
    """FSC RSS `<pubDate>` → 帶時區的 `datetime`（UTC 正規化）。

    無時區資訊者視為 UTC（RSS 慣例）。無法解析回 None。
    """
    text = _clean(value)
    if text is None:
        return None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def taipei_date(moment: datetime) -> date:
    """任一 aware/naive `datetime` 落在台北的哪一個日曆日。

    naive 視為 UTC（與 `parse_rfc822` 一致）。
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(TAIPEI).date()


def end_of_taipei_day(day: date) -> datetime:
    """台北該日的最後一刻（`23:59:59+08:00`）。

    日精度來源的 fail-closed 可見時間：只知道「那天」上線，
    就當作「那天結束才看得到」。
    """
    return datetime.combine(day, time(23, 59, 59), tzinfo=TAIPEI)


def day_precision_visible_at(value: object) -> datetime | None:
    """FSC `pubDate`（日精度）→ fail-closed 可見時間。

    先算出它落在台北的哪一日，再取該日結束。
    """
    parsed = parse_rfc822(value)
    if parsed is None:
        return None
    return end_of_taipei_day(taipei_date(parsed))


def pit_visible_at(*candidates: datetime | None) -> datetime | None:
    """多個時間候選 → 取**最晚**者作為 PIT 可見時間。

    發文日期與上架日期不一致時（實測相差一日），取較晚者才是
    資料真正對外可見的時刻。全部為 None 時回 None，
    呼叫端應據此跳過該筆（無法判定可見時間 ＝ 不可用）。
    """
    known = [c for c in candidates if c is not None]
    if not known:
        return None
    return max(_as_aware(c) for c in known)


def is_visible_at(visible_at: datetime | None, as_of: datetime | None) -> bool:
    """PIT 閘門：`visible_at` 是否在分析時間 `as_of` 當下已經可見。

    fail-closed：`visible_at` 為 None（無法判定）一律回 False。
    `as_of` 為 None 代表不做 PIT 限制，回 True。
    """
    if visible_at is None:
        return False
    if as_of is None:
        return True
    return _as_aware(visible_at) <= _as_aware(as_of)


def _as_aware(moment: datetime) -> datetime:
    """naive `datetime` 視為 UTC，供跨時區比較。"""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def _clean(value: object) -> str | None:
    """取出可用的字串內容。

    政府資料集的鍵與值都可能帶前後空白（實測 TWSE 欄位名為 `'主旨 '`），
    非字串型別（None／數字）一律視為不可用。
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None
