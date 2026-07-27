"""Source accuracy evaluation：將 analysis.direction 與 ground truth 比較，
產出 per-source 準確率報告與 Spearman rank correlation。

⛔ 不修改 scoring.py，只產報告。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..ingestion.base import Document
from .outcome_labeler import batch_label_from_ohlcv
from .scoring import _iterate_source_reputation, Claim

# 方向映射：訓練 JSONL 內的中文方向值 → 英文標準三態
_DIRECTION_MAP: dict[str, str] = {
    "偏多": "bullish",
    "偏多 ": "bullish",
    "中性": "neutral",
    "中性 ": "neutral",
    "不明": "neutral",  # 「不明」＝未做方向判斷，等同 neutral
    "不明 ": "neutral",
    "bearish": "bearish",
    "bullish": "bullish",
    "neutral": "neutral",
}


@dataclass
class SourceAccuracyReport:
    """單一來源的準確率報告。"""
    source: str
    total: int                    # 該來源參與的總記錄數
    directional: int              # 非 neutral 的方向預測數
    correct: int                  # 方向完全吻合 GT 的記錄數
    accuracy: float               # correct / total（整體準確率）
    directional_accuracy: float   # correct_directional / directional
    confusion_matrix: dict        # {"pred_?" → {"gt_?": count, …}}


def _read_training_files(
    training_dir: Path,
) -> list[dict]:
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


def _normalize_direction(raw: str) -> str:
    """將中文方向詞標準化成 bullish / bearish / neutral。"""
    return _DIRECTION_MAP.get(raw.strip(), "neutral")


def evaluate_source_accuracy(
    training_dir: Path | str | None = None,
    n_days: int = 7,
    threshold: float = 0.03,
) -> list[SourceAccuracyReport]:
    """從訓練資料計算各來源的準確率。

    對每個 training record，用 Phase 1 labeler 計算 GT 方向，
    與 analysis.direction 比對，按來源統計。

    Parameters
    ----------
    training_dir : Path | str | None
        training JSONL 目錄（預設 repo root 下的 data/training/）。
    n_days : int, default 7
        GT 計算的往前天數。
    threshold : float, default 0.03
        GT 方向門檻。

    Returns
    -------
    list[SourceAccuracyReport]
        每來源一份報告，依來源名稱排序。
    """
    if training_dir is None:
        root = Path(__file__).resolve().parents[3]
        training_dir = root / "data" / "training"
    training_dir = Path(training_dir)

    records = _read_training_files(training_dir)

    # Phase 1 labeler：預先為每個 (coin, date) 算 GT
    gt_cache: dict[tuple[str, str], Optional[str]] = {}
    coin_dates: dict[str, set[str]] = {}
    for rec in records:
        coin = rec.get("coin", "").upper()
        date = rec.get("date", "")
        if coin and date:
            coin_dates.setdefault(coin, set()).add(date)

    for coin, date_set in coin_dates.items():
        dates_list = sorted(date_set)
        if dates_list:
            labels = batch_label_from_ohlcv(
                coin, dates_list, n=n_days, threshold=threshold
            )
            for date, label in labels.items():
                gt_cache[(coin, date)] = label

    # Per-source 統計
    # source_data[source] = [(pred, gt), ...]
    source_data: dict[str, list[tuple[str, Optional[str]]]] = {}

    for rec in records:
        coin = rec.get("coin", "").upper()
        date = rec.get("date", "")
        pred_raw = rec.get("direction", "中性")
        pred = _normalize_direction(pred_raw)

        # GT：優先取 JSONL 內儲存的 GT，若為 None 則用 Phase 1 重算
        gt_stored = rec.get("ground_truth_direction")
        if gt_stored in ("bullish", "bearish", "neutral"):
            gt: Optional[str] = gt_stored
        else:
            gt = gt_cache.get((coin, date))

        if gt is None:
            # 無法確認 GT，跳過此 record
            continue

        sources = rec.get("sources", [])
        if not sources:
            continue

        for src in sources:
            source_data.setdefault(src, []).append((pred, gt))

    # 產報告
    reports: list[SourceAccuracyReport] = []
    for src in sorted(source_data.keys()):
        pairs = source_data[src]
        total = len(pairs)
        directional = sum(1 for p, _ in pairs if p in ("bullish", "bearish"))
        correct = sum(1 for p, g in pairs if p == g)
        correct_dir = sum(
            1 for p, g in pairs if p == g and p in ("bullish", "bearish")
        )

        # Confusion matrix: pred_* → gt_*
        cm: dict[str, dict[str, int]] = {}
        for pred_label in ("neutral", "bullish", "bearish"):
            cm[pred_label] = {}
            for gt_label in ("neutral", "bullish", "bearish"):
                cm[pred_label][gt_label] = 0
        for p, g in pairs:
            if p in cm and g is not None and g in cm[p]:
                cm[p][g] += 1

        reports.append(
            SourceAccuracyReport(
                source=src,
                total=total,
                directional=directional,
                correct=correct,
                accuracy=correct / total if total > 0 else 0.0,
                directional_accuracy=correct_dir / directional if directional > 0 else 0.0,
                confusion_matrix=cm,
            )
        )

    return reports


def _spearman_rank_correlation(x: list[float], y: list[float]) -> float:
    """計算 Spearman rank correlation coefficient。

    Parameters
    ----------
    x : list[float]
        第一組數值。
    y : list[float]
        第二組數值。

    Returns
    -------
    float
        Spearman ρ（-1 ~ 1）。若所有值都相同（無變異數）則回 0.0。
    """
    if len(x) != len(y) or len(x) < 2:
        return float("nan")

    # Rank (average method for ties)
    def _rank(values: list[float]) -> list[float]:
        sorted_indices = sorted(range(len(values)), key=lambda i: values[i])
        ranks = [0.0] * len(values)
        i = 0
        while i < len(sorted_indices):
            j = i
            while j < len(sorted_indices) and values[sorted_indices[j]] == values[sorted_indices[i]]:
                j += 1
            avg_rank = (i + j + 2) / 2.0  # 1-indexed average
            for k in range(i, j):
                ranks[sorted_indices[k]] = avg_rank
            i = j
        return ranks

    rx = _rank(x)
    ry = _rank(y)

    n = len(rx)
    d2_sum = sum((rx[i] - ry[i]) ** 2 for i in range(n))

    # Check for zero variance
    if all(rx[i] == rx[0] for i in range(n)) or all(ry[i] == ry[0] for i in range(n)):
        return float("nan")

    rho = 1.0 - (6.0 * d2_sum) / (n * (n * n - 1.0))
    return max(-1.0, min(1.0, rho))


def compare_with_reputation(
    reports: list[SourceAccuracyReport],
    training_dir: Path | str | None = None,
) -> dict:
    """將來源準確率排名與 ``_iterate_source_reputation()`` 的信譽排名做 Spearman 比較。

    從 training JSONL 建構 dummy claims，呼叫 ``_iterate_source_reputation()``
    （offline=True，走 DS EM fallback），取各來源的動態信譽，與來源準確率
    （directional_accuracy）算 Spearman rank correlation。

    Parameters
    ----------
    reports : list[SourceAccuracyReport]
        ``evaluate_source_accuracy()`` 的輸出。
    training_dir : Path | str | None
        training JSONL 目錄。

    Returns
    -------
    dict
        ``{"accuracy_ranks": {source: rank}, "reputation_ranks": {source: rank},
        "spearman_rho": float, "sources_compared": [...],
        "ds_mode": bool}``
    """
    if training_dir is None:
        root = Path(__file__).resolve().parents[3]
        training_dir = root / "data" / "training"
    training_dir = Path(training_dir)

    records = _read_training_files(training_dir)

    # 建構 dummy claims
    claims: list[Claim] = []
    for i, rec in enumerate(records):
        coin = rec.get("coin", "").upper()
        date = rec.get("date", "")
        sources = rec.get("sources", [])
        pred_raw = rec.get("direction", "中性")
        direction = _normalize_direction(pred_raw)

        for j, src in enumerate(sources):
            claim_id = f"dummy-{coin}-{date}-{j}"
            try:
                ts = float(date.replace("-", "")) if date else 0.0
            except ValueError:
                ts = 0.0
            claim = Claim(
                id=claim_id,
                text=f"{direction} analysis for {coin} on {date}",
                doc=Document(
                    id=f"doc-{coin}-{date}-{j}",
                    kind="price",
                    source=src,
                    text=f"{direction} analysis for {coin} on {date}",
                    ts=ts,
                    url="",
                    meta={"coin": coin},
                ),
                direction=direction,
            )
            claims.append(claim)

    if not claims:
        return {
            "accuracy_ranks": {},
            "reputation_ranks": {},
            "spearman_rho": float("nan"),
            "sources_compared": [],
            "ds_mode": False,
        }

    # 呼叫 _iterate_source_reputation（offline=True → DS EM）
    dynamic_map = _iterate_source_reputation(
        claims,
        now=0.0,
        iterations=3,
        offline=True,
    )

    # 建立 reputation map：canonical source → reputation
    rep_map: dict[str, float] = {}
    for claim in claims:
        src_raw = claim.doc.source
        # 需要 canonical source（與 _iterate_source_reputation 內部一致）
        from .scoring import _canonical_source
        canonical = _canonical_source(src_raw)
        if canonical not in rep_map:
            rep_map[canonical] = dynamic_map.get(canonical, 0.5)

    # 建立 accuracy map
    acc_map: dict[str, float] = {}
    for report in reports:
        acc_map[report.source] = report.directional_accuracy if report.directional > 0 else report.accuracy

    # 交集
    common_sources = sorted(set(acc_map.keys()) & set(rep_map.keys()))
    if len(common_sources) < 3:
        return {
            "accuracy_ranks": {s: acc_map.get(s, 0.0) for s in common_sources},
            "reputation_ranks": {s: rep_map.get(s, 0.5) for s in common_sources},
            "spearman_rho": float("nan"),
            "sources_compared": common_sources,
            "ds_mode": True,
            "note": f"Need >=3 common sources for meaningful Spearman, got {len(common_sources)}",
        }

    acc_vals = [acc_map[s] for s in common_sources]
    rep_vals = [rep_map[s] for s in common_sources]
    rho = _spearman_rank_correlation(acc_vals, rep_vals)

    return {
        "accuracy_ranks": {s: acc_map[s] for s in common_sources},
        "reputation_ranks": {s: rep_map[s] for s in common_sources},
        "spearman_rho": round(rho, 4),
        "sources_compared": common_sources,
        "ds_mode": True,
    }
