#!/usr/bin/env python3
"""Issue #393: LLM 語意分析重跑五年回填。

用 Bedrock LLM 重跑歷史回填，讓每天的分析有真實語意方向（bullish/bearish/neutral），
產出有方向性的訓練資料供 calibrator 訓練。

使用方式：
    # Dry-run（不呼叫 LLM，驗證流程）
    python scripts/run_semantic_backfill.py --dry-run --sample 5 --coins BTC

    # 正式跑（需 AWS 憑證 + BEDROCK_MODEL_ID）
    python scripts/run_semantic_backfill.py --sample 200 --batch-size 10

    # 指定模型
    python scripts/run_semantic_backfill.py --model-id us.anthropic.claude-haiku-4-5-20251001-v1:0 --sample 200

環境變數：
    BEDROCK_MODEL_ID  — Bedrock 模型 ID（必須；或用 --model-id 指定）
    AWS_REGION        — AWS region（預設 ap-southeast-2）
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from trustforge.backfill import BackfillWorker, set_backfill_enabled
from trustforge.ingestion.prices import load_ohlcv
from trustforge.schema import COIN_POOL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("semantic_backfill")

# 預設配置
DEFAULT_SAMPLE = 200  # 每幣 200 天
DEFAULT_BATCH_SIZE = 10
DEFAULT_START_DATE = "2021-07-01"
LOOKFORWARD_DAYS = 14  # outcome 判定視窗


def compute_ground_truth(
    coin: str,
    date_str: str,
    data_dir: Path,
    lookforward: int = LOOKFORWARD_DAYS,
) -> tuple[float | None, str | None]:
    """計算 ground truth：N+lookforward 日報酬率與方向。

    Returns
    -------
    (outcome_pct, ground_truth_direction)
        outcome_pct: 百分比報酬率（如 5.2 代表 +5.2%）
        ground_truth_direction: "bullish" (>3%) / "bearish" (<-3%) / "neutral"
        若未來資料不足回傳 (None, None)
    """
    bars = load_ohlcv(coin, data_dir)
    if not bars:
        return None, None

    bar_map: dict[str, float] = {b.date: b.close for b in bars}

    base_close = bar_map.get(date_str)
    if base_close is None or base_close <= 0:
        return None, None

    # 找 lookforward 天後的收盤價（±2 天容忍非交易日）
    target_date = date.fromisoformat(date_str) + timedelta(days=lookforward)
    future_close = None
    for offset in range(3):  # target_date, +1, +2
        candidate = (target_date + timedelta(days=offset)).isoformat()
        if candidate in bar_map:
            future_close = bar_map[candidate]
            break

    if future_close is None:
        return None, None

    outcome_pct = round((future_close - base_close) / base_close * 100, 2)

    if outcome_pct > 3.0:
        gt_dir = "bullish"
    elif outcome_pct < -3.0:
        gt_dir = "bearish"
    else:
        gt_dir = "neutral"

    return outcome_pct, gt_dir


def enrich_training_data_with_ground_truth(
    training_dir: Path,
    data_dir: Path,
    coins: list[str],
) -> dict[str, int]:
    """對已有的 training data 補上 ground_truth_direction 和 outcome_pct。

    只更新缺少這兩個欄位的記錄。回傳每幣更新筆數。
    """
    stats: dict[str, int] = {}
    for coin in coins:
        jsonl_path = training_dir / f"{coin.upper()}.jsonl"
        if not jsonl_path.is_file():
            stats[coin] = 0
            continue

        lines = jsonl_path.read_text(encoding="utf-8").strip().split("\n")
        updated_lines: list[str] = []
        updated_count = 0

        for line in lines:
            if not line.strip():
                updated_lines.append(line)
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                updated_lines.append(line)
                continue

            # 跳過已有 ground_truth 的記錄
            if record.get("ground_truth_direction") and record.get("outcome_pct") is not None:
                updated_lines.append(line)
                continue

            record_date = record.get("date")
            record_coin = record.get("coin", coin)
            if not record_date:
                updated_lines.append(line)
                continue

            outcome_pct, gt_dir = compute_ground_truth(
                record_coin, record_date, data_dir,
            )
            if outcome_pct is not None:
                record["outcome_pct"] = outcome_pct
                record["ground_truth_direction"] = gt_dir
                # 設定 train/val split（最後 20% 為 val）
                if "split" not in record:
                    record["split"] = "train"
                updated_count += 1

            updated_lines.append(json.dumps(record, ensure_ascii=False))

        # 寫回
        jsonl_path.write_text(
            "\n".join(updated_lines) + "\n", encoding="utf-8",
        )
        stats[coin] = updated_count

    return stats


def run_backfill(
    coins: list[str],
    sample: int,
    batch_size: int,
    model_id: str,
    dry_run: bool = False,
    start_date: str = DEFAULT_START_DATE,
    end_date: str | None = None,
) -> dict[str, any]:
    """執行 LLM 語意回填主流程。

    Returns
    -------
    dict 包含執行統計
    """
    if end_date is None:
        end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 設定 model ID 環境變數（BackfillWorker 的 live pipeline 從 env 讀）
    if model_id:
        os.environ["BEDROCK_MODEL_ID"] = model_id
    elif not os.environ.get("BEDROCK_MODEL_ID"):
        if not dry_run:
            raise RuntimeError(
                "BEDROCK_MODEL_ID 未設定。請用 --model-id 指定或設定環境變數。"
            )
        os.environ["BEDROCK_MODEL_ID"] = "dry-run-placeholder"

    mode = "offline" if dry_run else "live"
    data_dir = REPO / "data" / "data"
    training_dir = REPO / "data" / "training"

    logger.info(
        "Starting semantic backfill: coins=%s, sample=%d/coin, mode=%s, model=%s",
        coins, sample, mode, os.environ.get("BEDROCK_MODEL_ID", "?"),
    )

    # 用獨立的 DB 避免汙染正常 backfill 進度
    db_path = REPO / "out" / "semantic-backfill.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # 啟用 backfill（覆寫 state file）
    set_backfill_enabled(True, reason="semantic_backfill_script", actor="script")

    worker = BackfillWorker(
        db_path=db_path,
        data_dir=data_dir,
        coins=coins,
        start_date=start_date,
        end_date=end_date,
        batch_size=batch_size,
        interval_sec=1.0,
        mode=mode,
        sample=sample,
        training_data_dir=None,  # 用預設 data/training/
    )

    # Seed tasks
    seeded = worker.seed_tasks()
    logger.info("Seeded %d tasks (sample=%d per coin × %d coins)", seeded, sample, len(coins))

    # 主迴圈：逐 batch 跑
    total_completed = 0
    total_failed = 0
    total_skipped = 0
    batch_count = 0
    t0 = time.time()

    while True:
        results = worker.run_batch()
        if not results:
            break

        batch_count += 1
        completed = sum(1 for r in results if r.state == "completed")
        failed = sum(1 for r in results if r.state == "failed")
        skipped = sum(1 for r in results if r.state == "skipped")
        total_completed += completed
        total_failed += failed
        total_skipped += skipped

        elapsed = time.time() - t0
        rate = total_completed / elapsed if elapsed > 0 else 0
        remaining = seeded - total_completed - total_failed - total_skipped
        eta_sec = remaining / rate if rate > 0 else 0

        logger.info(
            "Batch #%d done: %d/%d/%d (ok/fail/skip) | "
            "Total: %d/%d completed | ETA: %.0fm",
            batch_count, completed, failed, skipped,
            total_completed, seeded, eta_sec / 60,
        )

        # 每 5 batches 暫停 2 秒（rate limiting）
        if not dry_run and batch_count % 5 == 0:
            time.sleep(2)

    elapsed_total = time.time() - t0
    worker.close()

    # 補充 ground truth
    logger.info("Enriching training data with ground truth labels...")
    gt_stats = enrich_training_data_with_ground_truth(training_dir, data_dir, coins)
    logger.info("Ground truth enrichment: %s", gt_stats)

    # 停用 backfill
    set_backfill_enabled(False, reason="semantic_backfill_complete", actor="script")

    summary = {
        "mode": mode,
        "model_id": os.environ.get("BEDROCK_MODEL_ID"),
        "coins": coins,
        "sample_per_coin": sample,
        "seeded": seeded,
        "completed": total_completed,
        "failed": total_failed,
        "skipped": total_skipped,
        "batches": batch_count,
        "elapsed_sec": round(elapsed_total, 1),
        "ground_truth_enriched": gt_stats,
    }
    logger.info("Backfill complete: %s", json.dumps(summary, ensure_ascii=False))
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="LLM 語意分析重跑五年回填（Issue #393）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--coins", nargs="+", default=list(COIN_POOL),
        help=f"要回填的幣種（預設全部：{', '.join(COIN_POOL)}）",
    )
    parser.add_argument(
        "--sample", type=int, default=DEFAULT_SAMPLE,
        help=f"每幣種抽樣天數（預設 {DEFAULT_SAMPLE}）",
    )
    parser.add_argument(
        "--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
        help=f"每 batch 處理天數（預設 {DEFAULT_BATCH_SIZE}）",
    )
    parser.add_argument(
        "--model-id", default="",
        help="Bedrock model ID（覆寫 BEDROCK_MODEL_ID 環境變數）",
    )
    parser.add_argument(
        "--start-date", default=DEFAULT_START_DATE,
        help=f"回填起始日期（預設 {DEFAULT_START_DATE}）",
    )
    parser.add_argument(
        "--end-date", default=None,
        help="回填結束日期（預設今天）",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="乾跑模式：不呼叫 LLM，用 offline 管線驗證流程",
    )
    parser.add_argument(
        "--retrain", action="store_true",
        help="回填完成後自動重訓 calibration model",
    )
    args = parser.parse_args(argv)

    coins = [c.upper() for c in args.coins]

    summary = run_backfill(
        coins=coins,
        sample=args.sample,
        batch_size=args.batch_size,
        model_id=args.model_id,
        dry_run=args.dry_run,
        start_date=args.start_date,
        end_date=args.end_date,
    )

    if args.retrain:
        logger.info("Retraining calibration model...")
        retrain_calibrator(coins)

    # 寫出執行摘要
    summary_path = REPO / "out" / "semantic-backfill-summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("Summary written to %s", summary_path)

    return 0 if summary["failed"] == 0 else 1


def retrain_calibrator(coins: list[str] | None = None) -> Path:
    """從 data/training/ 讀取訓練資料，重訓 calibration model。

    Returns
    -------
    Path: 產出的 calibration-model.json 路徑
    """
    from trustforge.calibration_model import save_calibration_model, train_isotonic

    training_dir = REPO / "data" / "training"
    model_path = REPO / "data" / "model-artifacts" / "calibration-model.json"

    coins = coins or list(COIN_POOL)
    confidences: list[float] = []
    hit_flags: list[bool] = []

    for coin in coins:
        jsonl_path = training_dir / f"{coin.upper()}.jsonl"
        if not jsonl_path.is_file():
            continue
        for line in jsonl_path.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            # 需要有 direction（非"不明"）和 ground_truth_direction
            direction = record.get("direction", "")
            gt_dir = record.get("ground_truth_direction")
            trust_score = record.get("trust_score")
            if not gt_dir or not direction or direction == "不明":
                continue
            if trust_score is None:
                continue

            # direction 正規化為 bullish/bearish/neutral
            dir_map = {
                "偏多": "bullish", "偏空": "bearish", "中性": "neutral",
                "bullish": "bullish", "bearish": "bearish", "neutral": "neutral",
            }
            norm_dir = dir_map.get(direction)
            if not norm_dir:
                continue

            # hit = 預測方向與 ground truth 一致
            hit = norm_dir == gt_dir
            confidences.append(float(trust_score))
            hit_flags.append(hit)

    if len(confidences) < 10:
        logger.warning(
            "Insufficient training samples (%d) for calibration. Need ≥10.",
            len(confidences),
        )
        return model_path

    points = train_isotonic(confidences, hit_flags)
    save_calibration_model(points, model_path, sample_count=len(confidences))
    logger.info(
        "Calibration model retrained: %d samples, %d calibration points → %s",
        len(confidences), len(points), model_path,
    )
    return model_path


if __name__ == "__main__":
    raise SystemExit(main())
