"""Leakage-safe historical outcome lab for TrustForge confidence research.

This module evaluates stored daily decisions against later official OHLCV bars.
It intentionally does not fit an LLM or relabel today's information-completeness
score as a forecast probability.  The resulting reliability table tells us
whether there is enough point-in-time data to train a small calibrator later.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Iterable

from .ingestion.prices import Bar


_DIRECTION_SIGN = {"偏多": 1, "偏空": -1}


@dataclass(frozen=True)
class Outcome:
    date: str
    horizon_days: int
    direction: str
    confidence: float
    return_pct: float
    directional_return_pct: float
    hit: bool


def _clamp_probability(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _confidence(snapshot: dict[str, Any]) -> float:
    if snapshot.get("confidence") is not None:
        return _clamp_probability(snapshot.get("confidence"))
    return _clamp_probability(snapshot.get("calibrated_confidence"))


def load_training_snapshots(training_dir: str | Path, *, coin: str | None = None) -> list[dict[str, Any]]:
    """Read JSONL training archives that contain directional predictions."""
    root = Path(training_dir)
    wanted_coin = coin.upper() if coin else None
    snapshots: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.jsonl")):
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no} is not valid JSONL") from exc
            if not isinstance(row, dict):
                continue
            row_coin = str(row.get("coin", "")).upper()
            if wanted_coin and row_coin != wanted_coin:
                continue
            if _DIRECTION_SIGN.get(str(row.get("direction", ""))) is None:
                continue
            if not row.get("date"):
                continue
            snapshots.append(row)
    return snapshots


def outcomes_for_horizon(
    snapshots: Iterable[dict[str, Any]], bars: Iterable[Bar], horizon_days: int,
) -> list[Outcome]:
    """Join historical decisions to later closes, without filling missing days.

    ``horizon_days`` means the index distance in supplied daily OHLCV.  A date
    is skipped when its future bar is not in the dataset; no synthetic outcome
    is created.  Neutral/abstain decisions are also skipped because they make
    no directional prediction to score.
    """
    if horizon_days < 1:
        raise ValueError("horizon_days must be >= 1")
    ordered = sorted(bars, key=lambda bar: bar.date)
    by_date = {bar.date: index for index, bar in enumerate(ordered)}
    outcomes: list[Outcome] = []
    for snapshot in sorted(snapshots, key=lambda item: str(item.get("date", ""))):
        direction = str(snapshot.get("direction", ""))
        sign = _DIRECTION_SIGN.get(direction)
        start = by_date.get(str(snapshot.get("date", "")))
        if sign is None or start is None or start + horizon_days >= len(ordered):
            continue
        start_close = ordered[start].close
        end_close = ordered[start + horizon_days].close
        if start_close == 0:
            continue
        return_pct = (end_close - start_close) / start_close * 100.0
        directional_return = return_pct * sign
        outcomes.append(Outcome(
            date=ordered[start].date,
            horizon_days=horizon_days,
            direction=direction,
            confidence=_confidence(snapshot),
            return_pct=return_pct,
            directional_return_pct=directional_return,
            hit=directional_return > 0,
        ))
    return outcomes


def _max_drawdown(returns: Iterable[float]) -> float:
    equity = peak = 1.0
    worst = 0.0
    for percent in returns:
        equity *= 1.0 + percent / 100.0
        peak = max(peak, equity)
        if peak:
            worst = min(worst, (equity / peak - 1.0) * 100.0)
    return worst


def calibration_summary(outcomes: Iterable[Outcome], *, bins: int = 5) -> dict[str, Any]:
    """Produce transparent reliability diagnostics for one forecast horizon."""
    if bins < 1:
        raise ValueError("bins must be >= 1")
    rows = list(outcomes)
    count = len(rows)
    correct = sum(row.hit for row in rows)
    brier = sum((row.confidence - float(row.hit)) ** 2 for row in rows) / count if count else None
    groups: dict[int, list[Outcome]] = defaultdict(list)
    for row in rows:
        groups[min(bins - 1, int(row.confidence * bins))].append(row)
    reliability = []
    calibration_error = 0.0
    for index in range(bins):
        group = groups[index]
        if not group:
            continue
        mean_confidence = sum(row.confidence for row in group) / len(group)
        empirical_hit_rate = sum(row.hit for row in group) / len(group)
        calibration_error = max(calibration_error, abs(mean_confidence - empirical_hit_rate))
        reliability.append({
            "range": [round(index / bins, 2), round((index + 1) / bins, 2)],
            "count": len(group),
            "mean_confidence": round(mean_confidence, 4),
            "mean_information_completeness": round(mean_confidence, 4),
            "empirical_hit_rate": round(empirical_hit_rate, 4),
        })
    return {
        "eligible_predictions": count,
        "hit_rate": round(correct / count, 4) if count else None,
        "calibration_error": round(calibration_error, 4) if count else None,
        "mean_directional_return_pct": round(sum(row.directional_return_pct for row in rows) / count, 4) if count else None,
        "max_drawdown_pct": round(_max_drawdown(row.directional_return_pct for row in rows), 4) if count else None,
        "brier_score_proxy": round(brier, 4) if brier is not None else None,
        "reliability": reliability,
    }


def replay_report(
    coin: str, snapshots: Iterable[dict[str, Any]], bars: Iterable[Bar], *, horizons: Iterable[int] = (1, 7, 14),
) -> dict[str, Any]:
    """Build an honest historical replay report from existing PIT snapshots."""
    snapshots_list = list(snapshots)
    bars_list = list(bars)
    evaluated: dict[str, Any] = {}
    for horizon in horizons:
        outcomes = outcomes_for_horizon(snapshots_list, bars_list, horizon)
        evaluated[f"T+{horizon}"] = {
            **calibration_summary(outcomes),
            "outcomes": [asdict(outcome) for outcome in outcomes],
        }
    return {
        "coin": coin.upper(),
        "method": "point-in-time daily trust snapshots joined only to later official OHLCV closes",
        "important_limit": (
            "calibrated_confidence is currently information completeness, not a validated forecast probability; "
            "brier_score_proxy is diagnostic only until a separately trained calibrator is approved."
        ),
        "source_archive_rule": (
            "raw source replay is available only for source snapshots captured at or before the formal run boundary; "
            "missing history is reported, never reconstructed from current cache."
        ),
        "available_snapshot_count": len(snapshots_list),
        "ohlcv_bar_count": len(bars_list),
        "horizons": evaluated,
    }
