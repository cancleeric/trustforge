"""校準升級執行器：讀取訓練資料 + OHLCV → 計算 hit_rate + calibration error。

Issue #335 — 從 backfill 產出的 training-data JSONL 讀取有方向預測的歷史紀錄，
對照實際 OHLCV 價格變化，計算命中率與校準誤差。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .ingestion.prices import Bar, load_ohlcv

# 預設路徑
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TRAINING_DIR = _PROJECT_ROOT / "out" / "training-data"
DEFAULT_DATA_DIR = _PROJECT_ROOT / "data" / "data"

# Direction 值域
_VALID_DIRECTIONS = {"中性", "偏多", "偏空"}

# Calibration bins
_BIN_EDGES = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)]

# 中性判定閾值
_NEUTRAL_THRESHOLD = 0.02  # 2%

# Horizons
_DEFAULT_HORIZONS = (1, 7, 14)

# Bin 最小樣本數
_MIN_BIN_SAMPLES = 5


def confidence_correctness_auc(
    scores: list[float],
    labels: list[bool],
) -> dict[str, Any]:
    """Tie-aware ROC AUC for confidence discriminating correctness.

    This is the Mann–Whitney probability that a randomly selected correct
    prediction has greater confidence than a randomly selected incorrect one,
    with ties receiving half credit. It is not market-direction AUC.
    """
    if len(scores) != len(labels):
        raise ValueError("scores and labels must have equal length")
    positives = [score for score, label in zip(scores, labels) if label]
    negatives = [score for score, label in zip(scores, labels) if not label]
    if not positives or not negatives:
        return {
            "value": None,
            "reason": "requires both correct and incorrect predictions",
            "target": "confidence_discrimination_of_correctness",
        }
    favourable = 0.0
    for positive in positives:
        for negative in negatives:
            if positive > negative:
                favourable += 1.0
            elif positive == negative:
                favourable += 0.5
    return {
        "value": round(favourable / (len(positives) * len(negatives)), 6),
        "reason": None,
        "target": "confidence_discrimination_of_correctness",
    }


def load_predictions(coin: str, training_dir: Path | str = DEFAULT_TRAINING_DIR) -> list[dict]:
    """從 JSONL 讀取有方向預測（direction != '不明'）的記錄。

    回傳 [{date, direction, confidence, trust_score}, ...]
    """
    training_dir = Path(training_dir)
    filepath = training_dir / f"{coin.upper()}.jsonl"
    if not filepath.exists():
        return []

    predictions: list[dict] = []
    with filepath.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            direction = record.get("direction")
            if direction not in _VALID_DIRECTIONS:
                continue

            predictions.append({
                "date": record.get("date", ""),
                "direction": direction,
                "confidence": float(record.get("confidence", 0.0)),
                "trust_score": float(record.get("trust_score", 0.0)),
            })

    return predictions


def _check_hit(direction: str, change_pct: float) -> bool:
    """判定預測是否命中。

    direction="中性" → |change| < 2% = hit
    direction="偏多" → change > 0 = hit
    direction="偏空" → change < 0 = hit
    """
    if direction == "中性":
        return abs(change_pct) < _NEUTRAL_THRESHOLD
    elif direction == "偏多":
        return change_pct > 0
    elif direction == "偏空":
        return change_pct < 0
    return False


def compare_predictions(
    predictions: list[dict],
    bars: list[Bar],
    horizons: tuple[int, ...] = _DEFAULT_HORIZONS,
) -> dict[str, Any]:
    """比對每筆預測 vs 實際 T+1/T+7/T+14 價格變化。

    回傳 {horizons: {"T+1": {eligible, hits, hit_rate}, ...}, details: [...]}
    """
    # 建 date→index lookup（bars 已排序）
    sorted_bars = sorted(bars, key=lambda b: b.date)
    date_to_idx: dict[str, int] = {bar.date: i for i, bar in enumerate(sorted_bars)}

    result_horizons: dict[str, dict[str, Any]] = {}
    all_details: list[dict] = []

    for horizon in horizons:
        eligible = 0
        hits = 0
        horizon_details: list[dict] = []

        for pred in predictions:
            pred_date = pred["date"]
            start_idx = date_to_idx.get(pred_date)
            if start_idx is None:
                continue
            end_idx = start_idx + horizon
            if end_idx >= len(sorted_bars):
                continue

            start_close = sorted_bars[start_idx].close
            end_close = sorted_bars[end_idx].close
            if start_close == 0:
                continue

            change_pct = (end_close - start_close) / start_close
            hit = _check_hit(pred["direction"], change_pct)

            eligible += 1
            if hit:
                hits += 1

            horizon_details.append({
                "date": pred_date,
                "direction": pred["direction"],
                "confidence": pred["confidence"],
                "horizon": horizon,
                "change_pct": round(change_pct, 6),
                "hit": hit,
            })

        hit_rate = round(hits / eligible, 4) if eligible > 0 else None
        result_horizons[f"T+{horizon}"] = {
            "eligible": eligible,
            "hits": hits,
            "hit_rate": hit_rate,
        }
        all_details.extend(horizon_details)

    return {"horizons": result_horizons, "details": all_details}


def calculate_calibration_error(
    predictions: list[dict],
    comparison_results: dict[str, Any],
) -> dict[str, Any]:
    """計算 calibration error（5 bins）。

    分 bin: [0-0.2, 0.2-0.4, 0.4-0.6, 0.6-0.8, 0.8-1.0]
    每 bin: mean_confidence, empirical_hit_rate
    error = max(|mean_conf - hit_rate|) across bins with ≥5 samples
    回傳 {calibration_error, bins: [...], reliable_bins}
    """
    # 從 details 建立 per-prediction hit lookup（以 T+1 為基準）
    details = comparison_results.get("details", [])
    # 使用 T+1 horizon 的 hit 結果
    hit_by_date: dict[str, bool] = {}
    for d in details:
        if d.get("horizon") == 1:
            hit_by_date[d["date"]] = d["hit"]

    # 分 bin
    bins_data: list[dict[str, Any]] = []
    reliable_bins = 0
    max_error = 0.0

    for low, high in _BIN_EDGES:
        bin_preds = [
            p for p in predictions
            if low <= p["confidence"] < high and p["date"] in hit_by_date
        ]
        # 最後一個 bin 包含 1.0
        if high == 1.0:
            bin_preds.extend(
                p for p in predictions
                if p["confidence"] == 1.0 and p["date"] in hit_by_date
                and p not in bin_preds
            )

        count = len(bin_preds)
        if count == 0:
            bins_data.append({
                "range": [low, high],
                "count": 0,
                "mean_confidence": None,
                "empirical_hit_rate": None,
            })
            continue

        mean_conf = sum(p["confidence"] for p in bin_preds) / count
        empirical_hits = sum(1 for p in bin_preds if hit_by_date.get(p["date"], False))
        empirical_hit_rate = empirical_hits / count

        bin_entry = {
            "range": [low, high],
            "count": count,
            "mean_confidence": round(mean_conf, 4),
            "empirical_hit_rate": round(empirical_hit_rate, 4),
        }
        bins_data.append(bin_entry)

        if count >= _MIN_BIN_SAMPLES:
            reliable_bins += 1
            error = abs(mean_conf - empirical_hit_rate)
            max_error = max(max_error, error)

    eligible_predictions = [
        prediction for prediction in predictions if prediction["date"] in hit_by_date
    ]
    discrimination = confidence_correctness_auc(
        [float(prediction["confidence"]) for prediction in eligible_predictions],
        [hit_by_date[prediction["date"]] for prediction in eligible_predictions],
    )
    return {
        "calibration_error": round(max_error, 4) if reliable_bins > 0 else None,
        "bins": bins_data,
        "reliable_bins": reliable_bins,
        "confidence_correctness_roc_auc": discrimination,
    }


def run_calibration(
    coin: str,
    data_dir: Path | str = DEFAULT_DATA_DIR,
    training_dir: Path | str = DEFAULT_TRAINING_DIR,
) -> dict[str, Any]:
    """主函式：load_predictions → compare → calculate_error。

    格式化成 calibration.replay_report 相容格式。
    """
    coin = coin.upper()
    data_dir = Path(data_dir)
    training_dir = Path(training_dir)

    # 1. 載入預測
    predictions = load_predictions(coin, training_dir)

    # 2. 載入 OHLCV bars
    bars = load_ohlcv(coin, data_dir)

    if not predictions or not bars:
        return {
            "coin": coin,
            "available_snapshot_count": len(predictions),
            "ohlcv_bar_count": len(bars),
            "horizons": {},
            "calibration": {
                "calibration_error": None,
                "bins": [],
                "reliable_bins": 0,
                "confidence_correctness_roc_auc": {
                    "value": None,
                    "reason": "requires both correct and incorrect predictions",
                    "target": "confidence_discrimination_of_correctness",
                },
            },
        }

    # 3. 比對預測 vs 實際
    comparison = compare_predictions(predictions, bars)

    # 4. 計算 calibration error
    calibration = calculate_calibration_error(predictions, comparison)

    # 5. 組裝 replay_report 相容格式
    horizons_output: dict[str, Any] = {}
    for horizon_key, horizon_stats in comparison["horizons"].items():
        # 取出此 horizon 的 details 做 reliability 分析
        horizon_days = int(horizon_key.replace("T+", ""))
        horizon_details = [
            d for d in comparison["details"] if d.get("horizon") == horizon_days
        ]

        # 計算 reliability bins (同 calibration_summary 格式)
        reliability: list[dict] = []
        for low, high in _BIN_EDGES:
            bin_items = [
                d for d in horizon_details
                if low <= next(
                    (p["confidence"] for p in predictions if p["date"] == d["date"]),
                    0.0,
                ) < high
            ]
            # 最後一個 bin 包含 1.0
            if high == 1.0:
                bin_items_extra = [
                    d for d in horizon_details
                    if next(
                        (p["confidence"] for p in predictions if p["date"] == d["date"]),
                        0.0,
                    ) == 1.0
                    and d not in bin_items
                ]
                bin_items.extend(bin_items_extra)

            if not bin_items:
                continue
            bin_count = len(bin_items)
            confs = [
                next((p["confidence"] for p in predictions if p["date"] == d["date"]), 0.0)
                for d in bin_items
            ]
            reliability.append({
                "range": [round(low, 2), round(high, 2)],
                "count": bin_count,
                "mean_information_completeness": round(sum(confs) / bin_count, 4),
                "empirical_hit_rate": round(
                    sum(1 for d in bin_items if d["hit"]) / bin_count, 4
                ),
            })

        horizons_output[horizon_key] = {
            "eligible_predictions": horizon_stats["eligible"],
            "hit_rate": horizon_stats["hit_rate"],
            "reliability": reliability,
        }

    return {
        "coin": coin,
        "available_snapshot_count": len(predictions),
        "ohlcv_bar_count": len(bars),
        "horizons": horizons_output,
        "calibration": calibration,
    }
