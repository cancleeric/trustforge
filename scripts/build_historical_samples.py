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
import stat
import tempfile
from collections import Counter
from csv import DictReader
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from collections.abc import Iterator
from typing import Any, Optional

_DIRECTION_THRESHOLD = 0.03
_DIRECTIONS = {"bullish", "bearish", "neutral"}
_COINS = {"BTC", "ETH", "SOL", "BNB", "XRP"}
_MAX_INPUT_BYTES = 32 * 1024 * 1024
_MAX_JSON_LINE_BYTES = 1024 * 1024
_MAX_REPLAY_FILES = 10_000
_MAX_REPLAY_TOTAL_BYTES = 256 * 1024 * 1024
_CONTRACT_PATH = Path(__file__).resolve().parents[1] / "docs/contracts/historical-sample-contract.md"
_SOURCE_IDENTITY = {
    "alternative-me-fng": ("sentiment", "Alternative.me", "market-wide"),
    "blockchain-com-charts": ("onchain", "Blockchain.com", "per-coin"),
    "ohlcv-csv": ("price", "HOYA BIT", "per-coin"),
    "sec-gov": ("regulatory", "SEC", "market-wide"),
}


class ReplayInputError(ValueError):
    """The replay corpus violates a batch-level safety boundary."""


def _read_stable_bytes(path: Path, *, limit: int | None = None) -> bytes:
    """Read one regular, non-symlink input once and reject concurrent mutation."""
    if limit is None:
        limit = _MAX_INPUT_BYTES
    try:
        before_path = path.lstat()
        if stat.S_ISLNK(before_path.st_mode) or not stat.S_ISREG(before_path.st_mode):
            raise ReplayInputError(f"input is not a regular file: {path}")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            before_fd = os.fstat(descriptor)
            identity = (before_fd.st_dev, before_fd.st_ino)
            if identity != (before_path.st_dev, before_path.st_ino):
                raise ReplayInputError(f"input identity changed before read: {path}")
            if before_fd.st_size > limit:
                raise ReplayInputError(f"input exceeds safety limit: {path}")
            chunks: list[bytes] = []
            remaining = limit + 1
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            after_fd = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        after_path = path.lstat()
    except ReplayInputError:
        raise
    except OSError as exc:
        raise ReplayInputError(f"cannot read stable input: {path}") from exc
    if len(data) > limit:
        raise ReplayInputError(f"input exceeds safety limit: {path}")
    stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if (
        len(data) != before_fd.st_size
        or any(getattr(before_fd, key) != getattr(after_fd, key) for key in stable_fields)
        or any(getattr(after_fd, key) != getattr(after_path, key) for key in stable_fields)
    ):
        raise ReplayInputError(f"input changed while being read: {path}")
    return data


def _decode_utf8(data: bytes, counters: Counter[str]) -> str | None:
    try:
        return data.decode("utf-8")
    except UnicodeError:
        counters["malformed_input"] += 1
        return None


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


def _parse_fng_records(data: bytes, counters: Counter[str]) -> list[dict[str, Any]]:
    """Load one market-wide FNG record per UTC date.

    ``published_at`` is the required evidence visibility timestamp. Duplicate
    coin-expanded rows are deliberately collapsed without increasing source
    count. BTC is preferred when present.
    """
    by_day: dict[str, dict[str, Any]] = {}
    text = _decode_utf8(data, counters)
    if text is None:
        return []
    lines = text.splitlines()
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


def load_fng_records(path: Path, counters: Counter[str]) -> list[dict[str, Any]]:
    """Backward-compatible loader using one stable byte snapshot."""
    try:
        data = _read_stable_bytes(path)
    except ReplayInputError as exc:
        if "exceeds safety limit" in str(exc) or "input exceeds" in str(exc):
            counters["input_too_large"] += 1
            return []
        counters["malformed_input"] += 1
        return []
    return _parse_fng_records(data, counters)


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


def _parse_ohlcv(data: bytes, counters: Counter[str]) -> dict[str, float]:
    index: dict[str, float] = {}
    text = _decode_utf8(data, counters)
    if text is None:
        return index
    for row in DictReader(StringIO(text)):
        try:
            value = float(row["close"])
            date.fromisoformat(row["date"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            index[row["date"]] = value
    return index


def load_ohlcv(path: Path) -> dict[str, float]:
    return _parse_ohlcv(_read_stable_bytes(path), Counter())


def _bounded_replay_paths(
    replay_dir: Path, counters: Counter[str]
) -> list[Path]:
    paths = [path for path in sorted(replay_dir.glob("*.json")) if path.name != "index.json"]
    if len(paths) > _MAX_REPLAY_FILES:
        counters["too_many_replay_files"] += len(paths) - _MAX_REPLAY_FILES
        raise ReplayInputError("replay file count exceeds safety limit")
    accepted: list[Path] = []
    total_bytes = 0
    for path in paths:
        try:
            size = path.stat().st_size
            if size > _MAX_INPUT_BYTES:
                counters["input_too_large"] += 1
                raise ReplayInputError(f"replay file exceeds safety limit: {path.name}")
            total_bytes += size
            if total_bytes > _MAX_REPLAY_TOTAL_BYTES:
                counters["replay_total_too_large"] += 1
                raise ReplayInputError("replay corpus exceeds aggregate safety limit")
        except OSError:
            counters["malformed_input"] += 1
            raise ReplayInputError(f"cannot stat replay file: {path.name}")
        accepted.append(path)
    return accepted


def _load_replay_snapshots(
    replay_dir: Path, counters: Counter[str], *, paths: list[Path] | None = None,
    blobs: list[tuple[Path, bytes]] | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield validated JSON objects from already-fixed byte snapshots."""
    selected = blobs
    if selected is None:
        selected_paths = (
            paths if paths is not None else _bounded_replay_paths(replay_dir, counters)
        )
        selected = [
            (path, _read_stable_bytes(path))
            for path in selected_paths
        ]
    for path, data in selected:
        try:
            value = json.loads(data)
        except (UnicodeError, json.JSONDecodeError, RecursionError):
            counters["malformed_input"] += 1
            continue
        if not isinstance(value, dict):
            counters["malformed_input"] += 1
            continue
        yield value


def load_replay_snapshots(replay_dir: Path) -> list[dict]:
    """Backward-compatible replay loader for existing research callers."""
    return list(_load_replay_snapshots(replay_dir, Counter()))


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
        canonical = _SOURCE_IDENTITY.get(source)
        if canonical is None:
            # Replay is a local trusted boundary, not a source-registration
            # mechanism. Unknown identities must first be added to this
            # reviewed allowlist.
            counters["unknown_source"] += 1
            rejected = True
            continue
        family, provider, scope = canonical
        supplied_family = item.get("source_family", item.get("kind"))
        supplied_provider = item.get("provider")
        meta = item.get("meta", {})
        supplied_scope = item.get("scope", meta.get("scope") if isinstance(meta, dict) else None)
        if (
            (supplied_family is not None and supplied_family != family)
            or (supplied_provider is not None and supplied_provider != provider)
            or (supplied_scope is not None and supplied_scope != scope)
        ):
            counters["source_identity_conflict"] += 1
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
            "provider": provider,
            "source_family": family,
            "scope": scope,
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


def _lineage_hash_bytes(blobs: list[tuple[Path, bytes]]) -> str:
    composite = b""
    for _, data in sorted(blobs, key=lambda item: str(item[0])):
        composite += hashlib.sha256(data).digest()
    return hashlib.sha256(composite).hexdigest()


def lineage_hash(*file_paths: str) -> str:
    return _lineage_hash_bytes([
        (Path(file_path), _read_stable_bytes(Path(file_path)))
        for file_path in file_paths
    ])


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
    contract_blob = _read_stable_bytes(_CONTRACT_PATH)
    fng_blob = _read_stable_bytes(fng_path)
    ohlcv_blob = _read_stable_bytes(ohlcv_path)
    ohlcv = _parse_ohlcv(ohlcv_blob, counters)
    artifacts = [(_CONTRACT_PATH, contract_blob), (fng_path, fng_blob), (ohlcv_path, ohlcv_blob)]
    replay_paths: list[Path] = []
    replay_blobs: list[tuple[Path, bytes]] = []
    if replay_dir is not None:
        replay_paths = _bounded_replay_paths(replay_dir, counters)
        replay_blobs = [(path, _read_stable_bytes(path)) for path in replay_paths]
        if sum(len(data) for _, data in replay_blobs) > _MAX_REPLAY_TOTAL_BYTES:
            raise ReplayInputError("replay corpus exceeds aggregate safety limit")
        artifacts.extend(replay_blobs)
    candidates: list[dict[str, Any]] = []

    fng_records = _parse_fng_records(fng_blob, counters)
    if counters["input_too_large"] and not fng_records:
        return [], counters
    lhash = _lineage_hash_bytes(artifacts)
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
        for snapshot in _load_replay_snapshots(
            replay_dir, counters, paths=replay_paths, blobs=replay_blobs
        ):
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
