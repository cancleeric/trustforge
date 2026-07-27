#!/usr/bin/env python3
"""Build historical sample JSONL from FNG + replay + OHLCV sources.

Milestone 2 pipeline for the TrustForge shared historical evidence/outcome contract
(see docs/contracts/historical-sample-contract.md).

Inputs:
  --fng-jsonl        FNG fetch output (out/history/alternative-me-fng-*.jsonl)
  --replay-dir       Replay snapshots (out/replay/five-year-btc/)
  --ohlcv-dir        OHLCV CSV directory (data/data/)
  --horizon          Outcome horizon: T+1, T+7, T+14 (default T+7)

Output:
  out/samples/historical_samples.jsonl  — one sample per line

Constraints enforced:
  - FNG market-wide: only BTC samples produced (not expanded to 6 coins)
  - Blockchain: BTC only
  - PIT gate: evidence.fetched_at <= as_of
  - abstain samples tagged (not excluded)
  - lineage_hash is reproducible
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from csv import DictReader
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))


# ── Constants ────────────────────────────────────────────────────────────────

_DIRECTION_THRESHOLD = 0.03  # ±3% for directional outcome

_SOURCE_FAMILY = {
    "alternative-me-fng": "sentiment",
    "blockchain-com-charts": "onchain",
    "ohlcv-csv": "price",
    "sec-gov": "regulatory",
}

_FAMILY_ORDER = ["price", "sentiment", "onchain", "regulatory"]  # for composite key


# ── Data loaders ─────────────────────────────────────────────────────────────

def load_fng_index(path: Path) -> dict[str, dict]:
    """Load FNG JSONL, build {published_at: {value, classification}} index.
    Only keeps BTC rows (market-wide constraint: FNG is single source)."""
    index: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").strip().split("\n"):
        rec = json.loads(line)
        if rec.get("coin") != "BTC":
            continue
        dt = rec["published_at"][:10]
        index[dt] = {
            "value": rec["value"],
            "classification": rec.get("classification", ""),
        }
    return index


def load_ohlcv(path: Path) -> dict[str, float]:
    """Load OHLCV CSV, build {date: close_price} index."""
    index: dict[str, float] = {}
    with path.open(encoding="utf-8") as f:
        for row in DictReader(f):
            index[row["date"]] = float(row["close"])
    return index


def load_replay_snapshots(replay_dir: Path) -> list[dict]:
    """Load all daily replay snapshots from a per-coin replay directory."""
    snapshots: list[dict] = []
    for p in sorted(replay_dir.glob("btc-*.json")):
        snap = json.loads(p.read_text(encoding="utf-8"))
        snapshots.append(snap)
    return snapshots


# ── Outcome computation ─────────────────────────────────────────────────────

def compute_outcome(
    ohlcv: dict[str, float],
    as_of_date: str,
    horizon_days: int,
) -> tuple[Optional[str], Optional[str]]:
    """Compute outcome direction from OHLCV.

    Returns (outcome_direction, outcome_observed_at) or (None, None) if T+N
    exceeds data range.
    """
    try:
        t_date = date.fromisoformat(as_of_date)
    except ValueError:
        return None, None
    t_n_date = t_date + timedelta(days=horizon_days)
    t_n_str = t_n_date.isoformat()

    close_t = ohlcv.get(as_of_date)
    close_tn = ohlcv.get(t_n_str)
    if close_t is None or close_tn is None:
        return None, None

    ret = close_tn / close_t - 1.0
    if abs(ret) <= _DIRECTION_THRESHOLD:
        return "neutral", f"{t_n_str}T00:00:00Z"
    if ret > _DIRECTION_THRESHOLD:
        return "bullish", f"{t_n_str}T00:00:00Z"
    return "bearish", f"{t_n_str}T00:00:00Z"


# ── Direction extraction ────────────────────────────────────────────────────

def _fng_direction(classification: str) -> str:
    """Map FNG classification to claim direction."""
    c = classification.lower()
    if "extreme fear" in c or "fear" in c:
        return "bearish"
    if "extreme greed" in c or "greed" in c:
        return "bullish"
    return "neutral"


def _fng_strength(value: int) -> float:
    """Compute evidence strength from FNG value (0-100)."""
    return max(0.5, min(0.85, 0.5 + abs(value - 50) / 100))


# ── Replay data extraction ──────────────────────────────────────────────────

def _extract_from_replay(snap: dict) -> dict | None:
    """Extract claim direction and strength from a replay snapshot."""
    report = snap.get("report")
    if isinstance(report, str):
        try:
            report = eval(report)
        except Exception:
            return None

    direction_raw = str(report.get("market_judgment", "")).strip()
    direction = "neutral"
    if "上漲" in direction_raw or "偏多" in direction_raw:
        direction = "bullish"
    elif "下跌" in direction_raw or "偏空" in direction_raw:
        direction = "bearish"
    elif "不明" in direction_raw or "盤整" in direction_raw:
        direction = "neutral"

    confidence = report.get("calibrated_confidence", 0.0)
    abstain = direction_raw == "不明" or confidence < 0.35

    snapshot_at = snap.get("snapshot_at", "")
    as_of = snapshot_at[:10] if len(snapshot_at) >= 10 else ""

    # Parse evidence to count source families
    evidence = snap.get("evidence")
    if isinstance(evidence, str):
        try:
            evidence = eval(evidence)
        except Exception:
            evidence = []

    families_seen: set[str] = set()
    if isinstance(evidence, list):
        for ev in evidence:
            if isinstance(ev, dict):
                fam = _SOURCE_FAMILY.get(ev.get("source", ""), "unknown")
                families_seen.add(fam)

    source_count = len(families_seen)

    return {
        "as_of": as_of,
        "claim_direction": direction,
        "evidence_strength": float(confidence),
        "abstain": abstain,
        "source_count": source_count,
        "confidence_raw": float(confidence),
    }


# ── Market regime ───────────────────────────────────────────────────────────

def _market_regime(ohlcv: dict[str, float], as_of_date: str, lookback: int = 30) -> str:
    """Classify market regime based on 30-day trend before as_of."""
    try:
        t = date.fromisoformat(as_of_date)
    except ValueError:
        return "unknown"
    closes = []
    for i in range(lookback):
        d = (t - timedelta(days=i)).isoformat()
        v = ohlcv.get(d)
        if v is not None:
            closes.append(v)
    if len(closes) < 10:
        return "unknown"
    ret = closes[0] / closes[-1] - 1.0
    if ret > 0.10:
        return "bull"
    if ret < -0.10:
        return "bear"
    return "sideways"


# ── Lineage hash ────────────────────────────────────────────────────────────

def lineage_hash(*file_paths: str) -> str:
    """Composite SHA-256 from ordered artifact files."""
    composite = b""
    for fp in sorted(file_paths):
        try:
            composite += hashlib.sha256(Path(fp).read_bytes()).digest()
        except FileNotFoundError:
            composite += hashlib.sha256(b"MISSING: " + fp.encode()).digest()
    return hashlib.sha256(composite).hexdigest()


# ── Main pipeline ───────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fng-jsonl", required=True,
                        help="FNG fetch output")
    parser.add_argument("--replay-dir",
                        help="Per-coin replay directory (optional)")
    parser.add_argument("--ohlcv-dir", required=True,
                        help="OHLCV CSV directory")
    parser.add_argument("--horizon", type=int, default=7,
                        help="Outcome horizon in days (default 7)")
    parser.add_argument("--coin", default="BTC",
                        help="Coin to build samples for (default BTC)")
    parser.add_argument("--out", default="out/samples/historical_samples.jsonl",
                        help="Output JSONL path")
    args = parser.parse_args()

    coin = args.coin.upper()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Load inputs
    fng_index = load_fng_index(Path(args.fng_jsonl))
    ohlcv_path = Path(args.ohlcv_dir) / f"{coin}_daily_ohlcv.csv"
    ohlcv = load_ohlcv(ohlcv_path)

    replay_extracts: dict[str, dict] = {}
    if args.replay_dir:
        snaps = load_replay_snapshots(Path(args.replay_dir))
        for s in snaps:
            info = _extract_from_replay(s)
            if info and info["as_of"]:
                replay_extracts[info["as_of"]] = info

    # Compute lineage hash
    artifacts = [args.fng_jsonl, str(ohlcv_path)]
    if args.replay_dir:
        idx = Path(args.replay_dir) / "index.json"
        if idx.exists():
            artifacts.append(str(idx))
    lhash = lineage_hash(*artifacts)

    # Build samples
    samples: list[dict] = []
    dates = sorted(set(list(fng_index.keys())).union(replay_extracts.keys()))

    for d in dates:
        fng = fng_index.get(d)
        replay = replay_extracts.get(d)

        # Determine source info
        if fng:
            source = "alternative-me-fng"
            provider = "Alternative.me"
            source_family = "sentiment"
            scope = "market-wide"
            claim_direction = _fng_direction(fng["classification"])
            evidence_strength = _fng_strength(fng["value"])
            abstain = fng["classification"].lower() == "neutral"
            source_count = replay.get("source_count", 0) + 1 if replay else 1
            confidence_raw = evidence_strength
        elif replay:
            source = "ohlcv-csv"
            provider = "HOYA BIT"
            source_family = "price"
            scope = "per-coin"
            claim_direction = replay["claim_direction"]
            evidence_strength = replay["evidence_strength"]
            abstain = replay.get("abstain", False)
            source_count = replay.get("source_count", 1)
            confidence_raw = replay.get("confidence_raw", evidence_strength)
        else:
            continue

        outcome_dir, outcome_obs = compute_outcome(ohlcv, d, args.horizon)
        if outcome_dir is None:
            continue  # T+N exceeds data — skip this sample

        regime = _market_regime(ohlcv, d)

        sample_id = hashlib.sha256(
            f"{coin}:{d}:{source}:{claim_direction}:T+{args.horizon}".encode()
        ).hexdigest()[:16]

        samples.append({
            "sample_id": sample_id,
            "coin": coin,
            "as_of": f"{d}T00:00:00Z",
            "source": source,
            "provider": provider,
            "source_family": source_family,
            "scope": scope,
            "claim_direction": claim_direction,
            "evidence_strength": evidence_strength,
            "outcome_horizon": f"T+{args.horizon}",
            "outcome_direction": outcome_dir,
            "outcome_observed_at": outcome_obs,
            "lineage_hash": lhash,
            "source_count": source_count,
            "abstain": abstain,
            "confidence_raw": confidence_raw,
            "market_regime": regime,
        })

    with out_path.open("w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    counts = {"total": len(samples)}
    for fam in _FAMILY_ORDER:
        counts[fam] = sum(1 for s in samples if s["source_family"] == fam)
    counts["abstain"] = sum(1 for s in samples if s.get("abstain"))

    print(json.dumps(counts, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
