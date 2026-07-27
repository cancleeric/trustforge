"""校準報告產生器：AUC / Brier score / reliability diagram。

Binary target: analysis.direction == GT → correct (1), else incorrect (0).
從 training JSONL 讀取 analysis，計算模型的校準品質指標，
輸出 ``data/model-artifacts/calibration_report.json``。

⛔ 不修改 scoring.py。
⛔ 不宣稱 TrustScore 預測市場方向。
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional


def _read_training_records(training_dir: Path) -> list[dict]:
    """讀取所有 training JSONL 檔，回傳 record 清單。"""
    records: list[dict] = []
    for fp in sorted(training_dir.glob("*.jsonl")):
        with fp.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                records.append(json.loads(line))
    return records


# 方向映射
_DIRECTION_MAP: dict[str, str] = {
    "偏多": "bullish",
    "中性": "neutral",
    "不明": "neutral",
    "bearish": "bearish",
    "bullish": "bullish",
    "neutral": "neutral",
}


def _norm(d: str) -> str:
    return _DIRECTION_MAP.get(d.strip(), "neutral")


def _trapezoidal_auc(
    scores: list[float],
    labels: list[int],
) -> float:
    """計算 AUC (Area Under the ROC Curve) 用梯形法。

    需至少一個 positive 和一個 negative 才可算；否則回 NaN。

    Parameters
    ----------
    scores : list[float]
        模型分數（越高越傾向 positive）。
    labels : list[int]
        實際標籤（0 或 1）。

    Returns
    -------
    float
        AUC 值，[0, 1]。若無法計算則回 ``float("nan")``。
    """
    if len(scores) != len(labels) or len(scores) < 2:
        return float("nan")

    # 檢查是否有至少一個 positive 和一個 negative
    n_pos = sum(labels)
    n_neg = len(labels) - n_pos
    if n_pos == 0 or n_neg == 0:
        return float("nan")

    # (score, label) 依 score 降序排序
    pairs = sorted(zip(scores, labels), key=lambda x: (-x[0], x[1]))
    total_pos = float(n_pos)
    total_neg = float(n_neg)

    auc = 0.0
    tp = 0.0
    fp = 0.0
    prev_fpr = 0.0

    for i, (score, label) in enumerate(pairs):
        tp_prev = tp
        fp_prev = fp

        if label == 1:
            tp += 1.0
        else:
            fp += 1.0

        # 只在分數真正改變時累加梯形面積
        if i == len(pairs) - 1 or pairs[i + 1][0] < score:
            tpr = tp / total_pos
            fpr = fp / total_neg
            # Trapezoid: (fpr - prev_fpr) * avg_tpr
            auc += (fpr - prev_fpr) * (tpr + (tp_prev / total_pos if total_pos > 0 else 0)) / 2.0
            prev_fpr = fpr

    # 補上最後一段到 (1, 1) 的梯形（若尚未到邊界）
    # 其實梯形法在通過所有點後，fpr 自然到 1.0，不需額外處理

    return max(0.0, min(1.0, auc))


def _brier_score(
    scores: list[float],
    labels: list[int],
) -> float:
    """計算 Brier score: mean((score - label)^2)。

    Parameters
    ----------
    scores : list[float]
        預測機率（需在 [0, 1] 內）。
    labels : list[int]
        實際標籤（0 或 1）。

    Returns
    -------
    float
        Brier score（0 = 完美，0.25 = 最糟的均勻猜測）。
    """
    if len(scores) != len(labels) or len(scores) == 0:
        return float("nan")
    n = len(scores)
    return sum((s - l) ** 2 for s, l in zip(scores, labels)) / n


def _reliability_bins(
    scores: list[float],
    labels: list[int],
    n_bins: int = 10,
) -> list[dict]:
    """將預測分數分成 ``n_bins`` 個等距桶，計算每桶的校準統計。

    Parameters
    ----------
    scores : list[float]
        預測分數。
    labels : list[int]
        實際標籤。
    n_bins : int, default 10
        分桶數（等距，0–1 之間）。

    Returns
    -------
    list[dict]
        每桶 ``{"count", "mean_score", "fraction_correct"}``。
        未落入任何樣本的桶不出現在結果中（不產生空桶）。
    """
    if len(scores) != len(labels) or len(scores) == 0:
        return []

    bins: list[list[tuple[float, int]]] = [[] for _ in range(n_bins)]
    for s, l in zip(scores, labels):
        idx = min(n_bins - 1, max(0, int(s * n_bins)))
        bins[idx].append((s, l))

    result: list[dict] = []
    for i, bucket in enumerate(bins):
        if not bucket:
            continue
        n = len(bucket)
        mean_score = sum(s for s, _ in bucket) / n
        frac_correct = sum(l for _, l in bucket) / n
        bin_start = i / n_bins
        result.append({
            "bin": i,
            "range": [round(bin_start, 2), round(bin_start + 1.0 / n_bins, 2)],
            "count": n,
            "mean_score": round(mean_score, 4),
            "fraction_correct": round(frac_correct, 4),
        })

    return result


def generate_calibration_report(
    training_dir: Path | str | None = None,
    output_path: Path | str | None = None,
) -> dict:
    """從 training JSONL 產生校準報告。

    Parameters
    ----------
    training_dir : Path | str | None
        training JSONL 目錄。預設 repo root 下的 data/training/。
    output_path : Path | str | None
        輸出 JSON 路徑。預設 repo root 下的
        data/model-artifacts/calibration_report.json。

    Returns
    -------
    dict
        完整報告內容（與寫入 output_path 的 JSON 一致）。
    """
    root = Path(__file__).resolve().parents[3]
    if training_dir is None:
        training_dir = root / "data" / "training"
    training_dir = Path(training_dir)

    if output_path is None:
        output_path = root / "data" / "model-artifacts" / "calibration_report.json"
    output_path = Path(output_path)

    records = _read_training_records(training_dir)

    # 收集有完整 GT 的記錄
    scored: list[dict] = []
    for rec in records:
        gt = rec.get("ground_truth_direction")
        if gt not in ("bullish", "bearish", "neutral"):
            continue
        pred = _norm(rec.get("direction", "中性"))
        trust_score = rec.get("trust_score", 0.5)
        if trust_score is None:
            continue
        scored.append({
            "gt": gt,
            "pred": pred,
            "trust_score": float(trust_score),
            "coin": rec.get("coin", ""),
        })

    if not scored:
        report = {
            "disclaimer": "TrustScore does not predict market direction. This report evaluates calibration of TrustScore relative to ground-truth direction labels.",
            "total_records": 0,
            "overall": {},
            "bullish_subset": {},
            "bearish_subset": {},
            "reliability_diagram": [],
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
        return report

    # --- Overall ---
    all_scores = [r["trust_score"] for r in scored]
    all_labels = [1 if r["pred"] == r["gt"] else 0 for r in scored]
    all_n = len(all_labels)
    all_correct = sum(all_labels)
    all_accuracy = all_correct / all_n if all_n > 0 else 0.0

    overall_auc = _trapezoidal_auc(all_scores, all_labels)
    overall_brier = _brier_score(all_scores, all_labels)

    # --- Bullish subset (GT = bullish) ---
    bullish_scored = [r for r in scored if r["gt"] == "bullish"]
    if bullish_scored:
        b_scores = [r["trust_score"] for r in bullish_scored]
        b_labels = [1 if r["pred"] == "bullish" else 0 for r in bullish_scored]
        bulls_auc = _trapezoidal_auc(b_scores, b_labels)
        bulls_brier = _brier_score(b_scores, b_labels)
        bulls_n = len(b_labels)
        bulls_correct = sum(b_labels)
        bulls_accuracy = bulls_correct / bulls_n if bulls_n > 0 else 0.0
    else:
        bulls_auc = float("nan")
        bulls_brier = float("nan")
        bulls_n = 0
        bulls_correct = 0
        bulls_accuracy = float("nan")

    # --- Bearish subset (GT = bearish) ---
    bearish_scored = [r for r in scored if r["gt"] == "bearish"]
    if bearish_scored:
        be_scores = [r["trust_score"] for r in bearish_scored]
        be_labels = [1 if r["pred"] == "bearish" else 0 for r in bearish_scored]
        bears_auc = _trapezoidal_auc(be_scores, be_labels)
        bears_brier = _brier_score(be_scores, be_labels)
        bears_n = len(be_labels)
        bears_correct = sum(be_labels)
        bears_accuracy = bears_correct / bears_n if bears_n > 0 else 0.0
    else:
        bears_auc = float("nan")
        bears_brier = float("nan")
        bears_n = 0
        bears_correct = 0
        bears_accuracy = float("nan")

    # --- Reliability diagram ---
    reliability = _reliability_bins(all_scores, all_labels, n_bins=10)

    report = {
        "disclaimer": (
            "TrustScore does not predict market direction. "
            "This report evaluates calibration of TrustScore relative to ground-truth direction labels."
        ),
        "total_records": all_n,
        "overall": {
            "accuracy": round(all_accuracy, 4),
            "auc": round(overall_auc, 4) if not math.isnan(overall_auc) else None,
            "brier_score": round(overall_brier, 4),
            "correct": all_correct,
            "total": all_n,
        },
        "bullish_subset": {
            "target": "GT=bullish; positive=prediction is bullish",
            "accuracy": round(bulls_accuracy, 4) if bulls_n > 0 else None,
            "auc": round(bulls_auc, 4) if not (isinstance(bulls_auc, float) and math.isnan(bulls_auc)) else None,
            "brier_score": round(bulls_brier, 4) if bulls_n > 0 else None,
            "correct": bulls_correct,
            "total": bulls_n,
        },
        "bearish_subset": {
            "target": "GT=bearish; positive=prediction is bearish",
            "accuracy": round(bears_accuracy, 4) if bears_n > 0 else None,
            "auc": round(bears_auc, 4) if not (isinstance(bears_auc, float) and math.isnan(bears_auc)) else None,
            "brier_score": round(bears_brier, 4) if bears_n > 0 else None,
            "correct": bears_correct,
            "total": bears_n,
        },
        "reliability_diagram": {
            "bins": reliability,
            "note": "10 equal-width bins of trust_score; fraction_correct is the empirical accuracy within each bin.",
        },
    }

    # 寫入 output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    return report
