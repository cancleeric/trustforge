#!/usr/bin/env python3
"""Audit five-coin replay artifacts and outcome lineage without live fetches."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

COINS = ("BTC", "ETH", "SOL", "BNB", "XRP", "ARB")
REQUIRED_EVENTS = {"historical_replay.start", "historical_replay.done"}


def audit(replay_root: Path) -> dict:
    coins = {}
    for coin in COINS:
        directory = replay_root / f"five-year-{coin.lower()}"
        index = json.loads((directory / "index.json").read_text(encoding="utf-8"))
        digest = hashlib.sha256()
        invalid = []
        artifacts = sorted(directory.glob(f"{coin.lower()}-*.json"))
        for path in artifacts:
            raw = path.read_bytes()
            digest.update(path.name.encode())
            digest.update(hashlib.sha256(raw).digest())
            payload = json.loads(raw)
            events = {
                json.loads(line).get("tool")
                for line in str(payload.get("execution_log_jsonl", "")).splitlines()
                if line.strip()
            }
            if payload.get("archive_type") != "backfilled_archive" or not REQUIRED_EVENTS <= events:
                invalid.append(path.name)
        outcomes_path = replay_root / f"five-year-{coin.lower()}-outcomes.json"
        outcomes = json.loads(outcomes_path.read_text(encoding="utf-8"))
        labels = outcomes.get("labels") or []
        eligible = {
            horizon: sum(
                1 for label in labels
                if ((label.get("outcomes") or {}).get(horizon) or {}).get("status") == "labeled"
            )
            for horizon in ("T+1", "T+7", "T+14")
        }
        coins[coin] = {
            "replayed": index.get("replayed"), "skipped": index.get("skipped") or [],
            "artifact_count": len(artifacts), "invalid_artifacts": invalid,
            "artifact_manifest_sha256": digest.hexdigest(),
            "outcome_labels": len(labels), "eligible_outcomes": eligible,
            "outcomes_sha256": hashlib.sha256(outcomes_path.read_bytes()).hexdigest(),
        }
    return {
        "kind": "historical_replay_batch_audit", "network_action": "none",
        "required_execution_events": sorted(REQUIRED_EVENTS), "coins": coins,
        "complete": all(
            row["replayed"] == row["artifact_count"] and not row["invalid_artifacts"]
            for row in coins.values()
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    report = audit(args.replay_root)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "complete": report["complete"],
        "replayed": sum(row["replayed"] for row in report["coins"].values()),
        "invalid": sum(len(row["invalid_artifacts"]) for row in report["coins"].values()),
        "out": str(args.out),
    }, ensure_ascii=False))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
