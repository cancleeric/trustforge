"""Attach leakage-safe T+ outcomes to completed daily replay artifacts."""
from __future__ import annotations
from typing import Any, Iterable
from .ingestion.prices import Bar

_SIGN = {"偏多": 1, "偏空": -1}

def label_replay_outcomes(replays: Iterable[dict[str, Any]], bars: list[Bar], lineage: dict[str, Any], horizons: tuple[int, ...] = (1, 7, 14)) -> list[dict[str, Any]]:
    ordered = sorted(bars, key=lambda bar: bar.date)
    positions = {bar.date: index for index, bar in enumerate(ordered)}
    labels = []
    for replay in replays:
        report = replay.get("report") or {}
        date = str(replay.get("snapshot_at", ""))[:10]
        direction = str(report.get("direction", ""))
        sign = _SIGN.get(direction)
        row = {"date": date, "coin": replay.get("coin"), "direction": direction, "calibrated_confidence": report.get("calibrated_confidence", 0.0), "ohlcv_lineage": lineage, "outcomes": {}}
        start = positions.get(date)
        for horizon in horizons:
            if sign is None or start is None or start + horizon >= len(ordered) or ordered[start].close == 0:
                row["outcomes"][f"T+{horizon}"] = {"status": "unavailable"}
                continue
            ret = (ordered[start + horizon].close - ordered[start].close) / ordered[start].close * 100
            row["outcomes"][f"T+{horizon}"] = {"status": "labeled", "return_pct": round(ret, 6), "directional_return_pct": round(ret * sign, 6), "hit": ret * sign > 0, "start_close": ordered[start].close, "end_close": ordered[start + horizon].close}
        labels.append(row)
    return labels
