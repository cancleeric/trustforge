#!/usr/bin/env python3
"""Retrain calibration model from training data (standalone).

Reads data/training/*.jsonl, filters records with valid direction + ground_truth,
trains isotonic regression, outputs data/model-artifacts/calibration-model.json.

Usage:
    python scripts/retrain_calibrator.py
    python scripts/retrain_calibrator.py --coins BTC ETH
    python scripts/retrain_calibrator.py --min-samples 50
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from trustforge.schema import COIN_POOL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("retrain_calibrator")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--coins", nargs="+", default=list(COIN_POOL),
        help="Coins to include in training",
    )
    parser.add_argument(
        "--min-samples", type=int, default=10,
        help="Minimum samples required for training (default 10)",
    )
    args = parser.parse_args(argv)

    # Import from the backfill runner (shares the retrain logic)
    from run_semantic_backfill import retrain_calibrator

    coins = [c.upper() for c in args.coins]
    model_path = retrain_calibrator(coins)

    if model_path.exists():
        logger.info("Model saved: %s", model_path)
        return 0
    else:
        logger.error("Model was not saved (insufficient data?)")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
