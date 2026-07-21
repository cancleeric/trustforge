"""用 OHLCV T+N outcome 標記 ground truth 方向。"""
import json
from datetime import date, timedelta
from pathlib import Path
from .ingestion.prices import load_ohlcv


def label_outcomes(
    training_dir: Path,
    ohlcv_dir: Path,
    horizon: int = 7,
    threshold: float = 0.03,
) -> dict:
    """Read training JSONL, add ground_truth_direction + outcome_pct + split."""
    stats = {}
    for coin_file in sorted(training_dir.glob("*.jsonl")):
        coin = coin_file.stem.upper()
        bars = load_ohlcv(coin, ohlcv_dir)
        if not bars:
            continue
        date_to_close = {b.date: b.close for b in bars}

        entries = []
        for line in coin_file.read_text().strip().split("\n"):
            if line.strip():
                entries.append(json.loads(line))

        if not entries:
            continue

        # Sort by date
        entries.sort(key=lambda e: e.get("date", ""))

        # Time-based split (80/20)
        split_idx = int(len(entries) * 0.8)

        labeled = 0
        for i, entry in enumerate(entries):
            entry_date = entry.get("date", "")
            if not entry_date:
                entry["split"] = "train" if i < split_idx else "val"
                continue

            t0_close = date_to_close.get(entry_date)
            try:
                t_future = (date.fromisoformat(entry_date) + timedelta(days=horizon)).isoformat()
            except ValueError:
                entry["split"] = "train" if i < split_idx else "val"
                continue

            t_future_close = date_to_close.get(t_future)

            if t0_close and t_future_close and t0_close > 0:
                pct = (t_future_close - t0_close) / t0_close
                entry["outcome_pct"] = round(pct * 100, 2)
                if pct > threshold:
                    entry["ground_truth_direction"] = "bullish"
                elif pct < -threshold:
                    entry["ground_truth_direction"] = "bearish"
                else:
                    entry["ground_truth_direction"] = "neutral"
                labeled += 1
            else:
                entry["outcome_pct"] = None
                entry["ground_truth_direction"] = None

            entry["split"] = "train" if i < split_idx else "val"

        # Write back
        coin_file.write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n"
        )
        stats[coin] = {"total": len(entries), "labeled": labeled}

    return stats
