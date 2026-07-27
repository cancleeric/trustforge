#!/usr/bin/env python3
"""Build deterministic, point-in-time-safe historical samples.

Only JSON objects matching the documented input shapes are accepted.  Invalid,
missing, or future evidence timestamps are excluded and reported in the CLI
summary; strings are never evaluated as Python.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from collections import Counter
from csv import DictReader
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

_DIRECTION_THRESHOLD = 0.03
_DIRECTIONS = {"bullish", "bearish", "neutral"}
_COINS = {"BTC", "ETH", "SOL", "BNB", "XRP"}
_MAX_INPUT_BYTES = 32 * 1024 * 1024
_MAX_JSON_LINE_BYTES = 1024 * 1024
_MAX_REPLAY_FILES = 10_000
_CONTRACT_PATH = Path(__file__).resolve().parents[1] / "docs/contracts/historical-sample-contract.md"
_SOURCE_FAMILY = {
    "alternative-me-fng": "sentiment",
    "blockchain-com-charts": "onchain",
    "ohlcv-csv": "price",
    "sec-gov": "regulatory",
}


def _utc_datetime(value: object) -> datetime | None:
    """Parse an aware ISO-8601 timestamp and normalize it to UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _date_cutoff(value: str) -> date:
    """Argparse converter for an exact UTC YYYY-MM-DD cutoff."""
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("cutoff must be UTC YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("cutoff must be UTC YYYY-MM-DD")
    return parsed


def load_fng_records(path: Path, counters: Counter[str]) -> list[dict[str, Any]]:
    """Load one market-wide FNG record per UTC date.

    ``published_at`` is the required evidence visibility timestamp. Duplicate
    coin-expanded rows are deliberately collapsed without increasing source
    count. BTC is preferred when present.
    """
    by_day: dict[str, dict[str, Any]] = {}
    try:
        if path.stat().st_size > _MAX_INPUT_BYTES:
            counters["input_too_large"] += 1
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        counters["malformed_input"] += 1
        return []
    for line in lines:
        if not line.strip():
            continue
        if len(line.encode("utf-8")) > _MAX_JSON_LINE_BYTES:
            counters["input_too_large"] += 1
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, RecursionError):
            counters["malformed_input"] += 1
            continue
        if not isinstance(rec, dict):
            counters["malformed_input"] += 1
            continue
        visible_at = _utc_datetime(rec.get("published_at"))
        if visible_at is None:
            counters["missing_or_invalid_timestamp"] += 1
            continue
        value = rec.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            counters["malformed_input"] += 1
            continue
        day = visible_at.date().isoformat()
        candidate = {
            "visible_at": visible_at,
            "value": float(value),
            "classification": str(rec.get("classification", "")),
            "coin": str(rec.get("coin", "")).upper(),
        }
        current = by_day.get(day)
        if current is not None:
            counters["fng_duplicate_expansion"] += 1
        if current is None or (candidate["coin"] == "BTC" and current["coin"] != "BTC"):
            by_day[day] = candidate
    return [by_day[key] for key in sorted(by_day)]


def load_fng_index(path: Path) -> dict[str, dict]:
    """Backward-compatible date index for existing research callers."""
    records = load_fng_records(path, Counter())
    return {
        record["visible_at"].date().isoformat(): {
            "value": record["value"],
            "classification": record["classification"],
        }
        for record in records
    }


def load_ohlcv(path: Path) -> dict[str, float]:
    index: dict[str, float] = {}
    with path.open(encoding="utf-8") as stream:
        for row in DictReader(stream):
            try:
                value = float(row["close"])
                date.fromisoformat(row["date"])
            except (KeyError, TypeError, ValueError):
                continue
            if math.isfinite(value) and value > 0:
                index[row["date"]] = value
    return index


def _bounded_replay_paths(
    replay_dir: Path, counters: Counter[str]
) -> list[Path]:
    paths = sorted(replay_dir.glob("*.json"))
    if len(paths) > _MAX_REPLAY_FILES:
        counters["too_many_replay_files"] += len(paths) - _MAX_REPLAY_FILES
        paths = paths[:_MAX_REPLAY_FILES]
    accepted: list[Path] = []
    for path in paths:
        try:
            if path.stat().st_size > _MAX_INPUT_BYTES:
                counters["input_too_large"] += 1
                continue
        except OSError:
            counters["malformed_input"] += 1
            continue
        accepted.append(path)
    return accepted


def _load_replay_snapshots(
    replay_dir: Path, counters: Counter[str], *, paths: list[Path] | None = None
) -> list[dict]:
    snapshots: list[dict[str, Any]] = []
    for path in paths if paths is not None else _bounded_replay_paths(replay_dir, counters):
        if path.name == "index.json":
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
            counters["malformed_input"] += 1
            continue
        if not isinstance(value, dict):
            counters["malformed_input"] += 1
            continue
        snapshots.append(value)
    return snapshots


def load_replay_snapshots(replay_dir: Path) -> list[dict]:
    """Backward-compatible replay loader for existing research callers."""
    return _load_replay_snapshots(replay_dir, Counter())


def compute_outcome(
    ohlcv: dict[str, float], as_of_date: str, horizon_days: int
) -> tuple[Optional[str], Optional[str]]:
    try:
        observed = date.fromisoformat(as_of_date) + timedelta(days=horizon_days)
    except ValueError:
        return None, None
    close_t = ohlcv.get(as_of_date)
    close_tn = ohlcv.get(observed.isoformat())
    if close_t is None or close_tn is None:
        return None, None
    change = close_tn / close_t - 1.0
    direction = (
        "neutral"
        if abs(change) <= _DIRECTION_THRESHOLD
        else "bullish"
        if change > 0
        else "bearish"
    )
    return direction, f"{observed.isoformat()}T00:00:00Z"


def _fng_direction(classification: str) -> str:
    normalized = classification.lower()
    if "fear" in normalized:
        return "bearish"
    if "greed" in normalized:
        return "bullish"
    return "neutral"


def _fng_strength(value: float) -> float:
    return max(0.5, min(0.85, 0.5 + abs(value - 50) / 100))


def _report_fields(report: object) -> tuple[str, float, bool] | None:
    if not isinstance(report, dict):
        return None
    raw = report.get("market_judgment", report.get("direction", ""))
    if not isinstance(raw, str):
        return None
    direct = raw.strip().lower()
    if direct in _DIRECTIONS:
        direction = direct
    elif "上漲" in raw or "偏多" in raw:
        direction = "bullish"
    elif "下跌" in raw or "偏空" in raw:
        direction = "bearish"
    else:
        direction = "neutral"
    confidence = report.get("calibrated_confidence", 0.0)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return None
    confidence = float(confidence)
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        return None
    return direction, confidence, direction == "neutral" or confidence < 0.35


def extract_replay_evidence(
    snapshot: dict[str, Any], counters: Counter[str], requested_coin: str
) -> list[dict[str, Any]]:
    """Return records only when the entire replay snapshot passes all gates."""
    as_of = _utc_datetime(snapshot.get("snapshot_at"))
    report = _report_fields(snapshot.get("report"))
    evidence = snapshot.get("evidence")
    snapshot_coin = snapshot.get("coin")
    if as_of is None:
        counters["missing_or_invalid_timestamp"] += 1
        return []
    if report is None or not isinstance(evidence, list):
        counters["malformed_input"] += 1
        return []
    if (
        not isinstance(snapshot_coin, str)
        or snapshot_coin.upper() not in _COINS
        or snapshot_coin.upper() != requested_coin
    ):
        counters["snapshot_coin_mismatch"] += 1
        return []
    direction, confidence, abstain = report
    result: dict[tuple[str, str], dict[str, Any]] = {}
    rejected = False
    for item in evidence:
        if not isinstance(item, dict):
            counters["malformed_input"] += 1
            rejected = True
            continue
        raw_timestamp = item.get("visible_at", item.get("published_at", item.get("fetched_at")))
        visible_at = _utc_datetime(raw_timestamp)
        if visible_at is None:
            counters["missing_or_invalid_timestamp"] += 1
            rejected = True
            continue
        if visible_at > as_of:
            counters["future_evidence"] += 1
            rejected = True
            continue
        source = item.get("source")
        if not isinstance(source, str) or not source.strip():
            counters["malformed_input"] += 1
            rejected = True
            continue
        family = item.get("source_family") or item.get("kind") or _SOURCE_FAMILY.get(source)
        if family not in {"sentiment", "onchain", "price", "regulatory"}:
            counters["malformed_input"] += 1
            rejected = True
            continue
        if source == "blockchain-com-charts" and str(snapshot.get("coin", "BTC")).upper() != "BTC":
            counters["blockchain_non_btc"] += 1
            rejected = True
            continue
        key = (source, family)
        result[key] = {
            "as_of": as_of,
            "source": source,
            "provider": str(item.get("provider") or source),
            "source_family": family,
            "scope": str(item.get("scope") or item.get("meta", {}).get("scope", "per-coin"))
            if isinstance(item.get("meta", {}), dict)
            else "per-coin",
            "claim_direction": direction,
            "evidence_strength": confidence,
            "abstain": abstain,
        }
    if rejected:
        counters["rejected_snapshots"] += 1
        return []
    family_count = len({family for _, family in result})
    records = [result[key] for key in sorted(result)]
    for record in records:
        record["source_count"] = family_count
    return records


def lineage_hash(*file_paths: str) -> str:
    composite = b""
    for file_path in sorted(file_paths):
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"lineage artifact does not exist: {path}")
        composite += hashlib.sha256(path.read_bytes()).digest()
    return hashlib.sha256(composite).hexdigest()


def _sample_id(sample: dict[str, Any]) -> str:
    identity = ":".join(
        str(sample[key])
        for key in ("coin", "as_of", "source", "source_family", "claim_direction", "outcome_horizon")
    )
    return hashlib.sha256(identity.encode()).hexdigest()[:16]


def build_samples(
    *, fng_path: Path, replay_dir: Path | None, ohlcv_path: Path,
    coin: str, horizon: int, cutoff: date
) -> tuple[list[dict[str, Any]], Counter[str]]:
    counters: Counter[str] = Counter()
    ohlcv = load_ohlcv(ohlcv_path)
    artifacts = [str(_CONTRACT_PATH), str(fng_path), str(ohlcv_path)]
    replay_paths: list[Path] = []
    if replay_dir is not None:
        replay_paths = _bounded_replay_paths(replay_dir, counters)
        artifacts.extend(str(path) for path in replay_paths)
    candidates: list[dict[str, Any]] = []

    fng_records = load_fng_records(fng_path, counters)
    if counters["input_too_large"] and not fng_records:
        return [], counters
    lhash = lineage_hash(*artifacts)
    if coin == "BTC":
        for fng in fng_records:
            candidates.append({
                "as_of": fng["visible_at"],
                "source": "alternative-me-fng",
                "provider": "Alternative.me",
                "source_family": "sentiment",
                "scope": "market-wide",
                "claim_direction": _fng_direction(fng["classification"]),
                "evidence_strength": _fng_strength(fng["value"]),
                "abstain": _fng_direction(fng["classification"]) == "neutral",
                "source_count": 1,
            })
    else:
        counters["fng_non_btc"] += len(fng_records)
    if replay_dir is not None:
        for snapshot in _load_replay_snapshots(replay_dir, counters, paths=replay_paths):
            candidates.extend(extract_replay_evidence(snapshot, counters, coin))

    samples: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate["source"] == "blockchain-com-charts" and coin != "BTC":
            counters["blockchain_non_btc"] += 1
            continue
        as_of: datetime = candidate["as_of"]
        if as_of.date() > cutoff:
            counters["after_cutoff"] += 1
            continue
        day = as_of.date().isoformat()
        outcome, observed_at = compute_outcome(ohlcv, day, horizon)
        if outcome is None:
            counters["missing_outcome"] += 1
            continue
        sample = {
            "coin": coin,
            "as_of": as_of.isoformat().replace("+00:00", "Z"),
            **{key: value for key, value in candidate.items() if key != "as_of"},
            "outcome_horizon": f"T+{horizon}",
            "outcome_direction": outcome,
            "outcome_observed_at": observed_at,
            "lineage_hash": lhash,
            "training_cutoff": cutoff.isoformat(),
        }
        sample["sample_id"] = _sample_id(sample)
        samples.append(sample)
    samples.sort(key=lambda row: (
        row["as_of"], row["coin"], row["source_family"], row["source"], row["sample_id"]
    ))
    counters["included"] = len(samples)
    return samples, counters


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fng-jsonl", required=True)
    parser.add_argument("--replay-dir")
    parser.add_argument("--ohlcv-dir", required=True)
    parser.add_argument("--horizon", type=int, choices=(1, 7, 14), default=7)
    parser.add_argument("--coin", default="BTC")
    parser.add_argument("--cutoff", type=_date_cutoff, default=datetime.now(timezone.utc).date())
    parser.add_argument("--out", default="out/samples/historical_samples.jsonl")
    args = parser.parse_args(argv)
    coin = args.coin.upper()
    ohlcv_path = Path(args.ohlcv_dir) / f"{coin}_daily_ohlcv.csv"
    samples, counters = build_samples(
        fng_path=Path(args.fng_jsonl),
        replay_dir=Path(args.replay_dir) if args.replay_dir else None,
        ohlcv_path=ohlcv_path,
        coin=coin,
        horizon=args.horizon,
        cutoff=args.cutoff,
    )
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output_bytes = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in samples
    )
    descriptor, temporary_name = tempfile.mkstemp(
        dir=output.parent, prefix=f".{output.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(output_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, output)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
    print(json.dumps(dict(sorted(counters.items())), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
