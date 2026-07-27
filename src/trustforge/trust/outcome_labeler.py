"""Ground-truth labeler：從 OHLCV 價格序列計算 N 日方向標籤。

確定性、免 LLM；只讀 CSV 不寫 DB。
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional


def _ohlcv_path(coin: str) -> Path:
    """解析 coin 對應的 OHLCV CSV 路徑（相對於 repo 根）。"""
    # 從本模組位置往上推 4 層到 repo 根（src/trustforge/trust/ → repo/）
    root = Path(__file__).resolve().parents[3]
    return root / "data" / "data" / f"{coin.upper()}_daily_ohlcv.csv"


def label_n_day_direction(
    coin: str,
    date: str,
    n: int = 7,
    threshold: float = 0.03,
) -> Optional[str]:
    """讀取 `coin` 的日線 OHLCV，計算從 `date` 起算 N 日後的報酬率，
    依 `threshold` 門檻標為 bullish / bearish / neutral。

    Parameters
    ----------
    coin : str
        幣別代號（如 "BTC"），大小寫不拘。
    date : str
        基準日，格式 "YYYY-MM-DD"。
    n : int, default 7
        往前看幾日（含假日，即直接跳 N 行）。
    threshold : float, default 0.03
        方向門檻（絕對值）。ret > threshold → "bullish"；
        ret < -threshold → "bearish"；否則 "neutral"。

    Returns
    -------
    str | None
        "bullish" | "bearish" | "neutral"；若 `date` 不在資料內或
        `date` + N 日超出資料範圍則回傳 ``None``。
    """
    path = _ohlcv_path(coin)
    if not path.is_file():
        return None

    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))

    # 建立 date → row index 映射（確定性：CSV 已按日期遞增排序）
    date_to_idx = {row["date"]: i for i, row in enumerate(rows)}

    idx = date_to_idx.get(date)
    if idx is None:
        return None

    future_idx = idx + n
    if future_idx >= len(rows):
        return None

    close_t = float(rows[idx]["close"])
    close_tn = float(rows[future_idx]["close"])
    if close_t == 0.0:
        return None

    ret = close_tn / close_t - 1.0
    if ret > threshold:
        return "bullish"
    if ret < -threshold:
        return "bearish"
    return "neutral"


def batch_label_from_ohlcv(
    coin: str,
    dates: list[str],
    n: int = 7,
    threshold: float = 0.03,
) -> dict[str, Optional[str]]:
    """批量版：讀一次 CSV，回傳 ``{date: label | None}``。

    Parameters
    ----------
    coin : str
        幣別代號。
    dates : list[str]
        基準日清單，格式 "YYYY-MM-DD"。
    n : int, default 7
        往前看幾日。
    threshold : float, default 0.03
        方向門檻。

    Returns
    -------
    dict[str, str | None]
        每個輸入日期對應的標籤；無法計算者為 ``None``。
    """
    path = _ohlcv_path(coin)
    if not path.is_file():
        return {d: None for d in dates}

    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))

    date_to_idx = {row["date"]: i for i, row in enumerate(rows)}
    close_by_idx = {i: float(row["close"]) for i, row in enumerate(rows)}

    result: dict[str, Optional[str]] = {}
    for date in dates:
        idx = date_to_idx.get(date)
        if idx is None:
            result[date] = None
            continue
        future_idx = idx + n
        if future_idx >= len(rows):
            result[date] = None
            continue
        close_t = close_by_idx[idx]
        close_tn = close_by_idx[future_idx]
        if close_t == 0.0:
            result[date] = None
            continue
        ret = close_tn / close_t - 1.0
        if ret > threshold:
            result[date] = "bullish"
        elif ret < -threshold:
            result[date] = "bearish"
        else:
            result[date] = "neutral"

    return result
