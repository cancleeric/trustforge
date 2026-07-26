"""Read-only, fail-closed integrity checks for the official OHLCV dataset."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .safe_fs import read_regular_file


COINS = ("BTC", "ETH", "SOL", "BNB", "XRP", "ARB")
OHLCV_COLUMNS = ("date", "open", "high", "low", "close", "volume")
EXPECTED_OHLCV_ROWS = 1826
CHECKSUM_SCHEMA_VERSION = "1.0.0"
MANIFEST_MAX_BYTES = 64 * 1024
METADATA_MAX_BYTES = 256 * 1024
CSV_MAX_BYTES = 2 * 1024 * 1024
DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
TIMESTAMP_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})\Z"
)
EXPECTED_FILES = {f"data/{coin}_daily_ohlcv.csv": coin for coin in COINS}


class DataIntegrityError(ValueError):
    """Raised when dataset bytes, metadata, schema, or market invariants fail."""


def _mapping(value: Any, *, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DataIntegrityError(f"{location} must be an object")
    return value


def _list(value: Any, *, location: str) -> list[Any]:
    if not isinstance(value, list):
        raise DataIntegrityError(f"{location} must be an array")
    return value


def _string(value: Any, *, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataIntegrityError(f"{location} must be a non-empty string")
    return value


def _integer(value: Any, *, location: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DataIntegrityError(f"{location} must be an integer")
    return value


def _iso_date(value: Any, *, location: str) -> date:
    text = _string(value, location=location)
    if DATE_PATTERN.fullmatch(text) is None:
        raise DataIntegrityError(f"{location} must use YYYY-MM-DD")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise DataIntegrityError(f"{location} is not a valid calendar date") from exc


def _aware_iso_timestamp(value: Any, *, location: str) -> datetime:
    text = _string(value, location=location)
    if TIMESTAMP_PATTERN.fullmatch(text) is None:
        raise DataIntegrityError(f"{location} must be a timezone-aware ISO8601 timestamp")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DataIntegrityError(f"{location} must be a valid ISO8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DataIntegrityError(f"{location} must include a timezone offset")
    return parsed


def _verify_checksums(root: Path) -> dict[str, bytes]:
    manifest_path = root / "data" / "ohlcv_checksums.json"
    manifest_payload, _ = read_regular_file(manifest_path, maximum_bytes=MANIFEST_MAX_BYTES)
    manifest = _mapping(json.loads(manifest_payload.decode("utf-8", errors="strict")), location="checksum manifest")
    if _string(manifest.get("schema_version"), location="checksum schema_version") != CHECKSUM_SCHEMA_VERSION:
        raise DataIntegrityError(f"checksum schema_version must be {CHECKSUM_SCHEMA_VERSION}")
    if _string(manifest.get("algorithm"), location="checksum algorithm") != "sha256":
        raise DataIntegrityError("checksum algorithm must be sha256")
    files = _mapping(manifest.get("files"), location="checksum files")
    if set(files) != set(EXPECTED_FILES):
        raise DataIntegrityError("checksum manifest must contain exactly the six official OHLCV files")

    verified: dict[str, bytes] = {}
    for relative_path in EXPECTED_FILES:
        expected_digest = _string(files[relative_path], location=f"checksum {relative_path}")
        if SHA256_PATTERN.fullmatch(expected_digest) is None:
            raise DataIntegrityError(f"checksum {relative_path} must be a lowercase SHA-256 digest")
        path = root / "data" / relative_path
        payload, _ = read_regular_file(path, maximum_bytes=CSV_MAX_BYTES)
        actual_digest = hashlib.sha256(payload).hexdigest()
        if actual_digest != expected_digest:
            raise DataIntegrityError(f"checksum mismatch: {relative_path}")
        verified[relative_path] = payload
    return verified


def _decimal(value: str, *, field: str, location: str) -> Decimal:
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise DataIntegrityError(f"{location}: {field} is not a valid decimal") from exc
    if not number.is_finite():
        raise DataIntegrityError(f"{location}: {field} must be finite")
    return number


def _read_metadata(root: Path, *, expected_rows: int) -> tuple[date, date, dict[str, dict[str, Any]]]:
    metadata_path = root / "data" / "dataset_metadata.json"
    metadata_payload, _ = read_regular_file(metadata_path, maximum_bytes=METADATA_MAX_BYTES)
    metadata = _mapping(json.loads(metadata_payload.decode("utf-8", errors="strict")), location="metadata")
    _string(metadata.get("dataset_name"), location="metadata dataset_name")
    _aware_iso_timestamp(metadata.get("generated_at"), location="metadata generated_at")
    if _string(metadata.get("price_unit"), location="metadata price_unit") != "USDT":
        raise DataIntegrityError("metadata price_unit must be USDT")
    source = _mapping(metadata.get("source"), location="metadata source")
    if _string(source.get("name"), location="metadata source.name") != "public_market_data":
        raise DataIntegrityError("metadata source.name must be public_market_data")
    if _string(source.get("type"), location="metadata source.type") != "public_market_data":
        raise DataIntegrityError("metadata source.type must be public_market_data")
    if _string(metadata.get("time_basis"), location="metadata time_basis") != "UTC":
        raise DataIntegrityError("metadata time_basis must be UTC")
    if _string(metadata.get("interval"), location="metadata interval") != "1d":
        raise DataIntegrityError("metadata interval must be 1d")

    period = _mapping(metadata.get("period"), location="metadata period")
    start = _iso_date(period.get("start_date"), location="metadata period start_date")
    end = _iso_date(period.get("end_date"), location="metadata period end_date")
    columns = _list(metadata.get("columns"), location="metadata columns")
    names = []
    for index, item in enumerate(columns):
        column = _mapping(item, location=f"metadata columns[{index}]")
        names.append(_string(column.get("name"), location=f"metadata columns[{index}].name"))
        _string(column.get("description"), location=f"metadata columns[{index}].description")
    if names != list(OHLCV_COLUMNS):
        raise DataIntegrityError("metadata columns do not match the OHLCV schema")

    symbol_items = _list(metadata.get("symbols"), location="metadata symbols")
    if len(symbol_items) != len(COINS):
        raise DataIntegrityError("metadata symbols must contain exactly six entries")
    symbols: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(symbol_items):
        symbol = _mapping(item, location=f"metadata symbols[{index}]")
        asset = _string(symbol.get("asset"), location=f"metadata symbols[{index}].asset")
        if asset in symbols:
            raise DataIntegrityError(f"metadata contains duplicate symbol {asset}")
        symbols[asset] = symbol
    if set(symbols) != set(COINS):
        raise DataIntegrityError(f"metadata symbols must be exactly {', '.join(COINS)}")

    for coin in COINS:
        symbol = symbols[coin]
        prefix = f"metadata {coin}"
        if _string(symbol.get("pair"), location=f"{prefix} pair") != f"{coin}USDT":
            raise DataIntegrityError(f"{coin}: metadata pair mismatch")
        if _string(symbol.get("file"), location=f"{prefix} file") != f"data/{coin}_daily_ohlcv.csv":
            raise DataIntegrityError(f"{coin}: metadata file mismatch")
        if _integer(symbol.get("rows"), location=f"{prefix} rows") != expected_rows:
            raise DataIntegrityError(f"{coin}: metadata row count mismatch")
        symbol_start = _iso_date(symbol.get("start_date"), location=f"{prefix} start_date")
        symbol_end = _iso_date(symbol.get("end_date"), location=f"{prefix} end_date")
        if symbol_start != start or symbol_end != end:
            raise DataIntegrityError(f"{coin}: symbol dates do not match metadata period")
    return start, end, symbols


def _audit_csv(
    payload: bytes, *, relative_path: str, coin: str, expected_rows: int
) -> tuple[date, date]:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise DataIntegrityError(f"{relative_path}: CSV must be valid UTF-8") from exc
    with io.StringIO(text, newline="") as handle:
        reader = csv.reader(handle, strict=True)
        header = next(reader)
        if tuple(header) != OHLCV_COLUMNS:
            raise DataIntegrityError(f"{coin}: CSV columns do not match the OHLCV schema")
        rows = list(reader)
    if len(rows) != expected_rows:
        raise DataIntegrityError(f"{coin}: CSV rows={len(rows)}, expected {expected_rows}")

    dates: list[date] = []
    for line_number, fields in enumerate(rows, start=2):
        location = f"data/{relative_path}:{line_number}"
        if len(fields) != len(OHLCV_COLUMNS):
            raise DataIntegrityError(f"{location}: expected exactly six CSV fields")
        if any(not isinstance(value, str) or not value.strip() for value in fields):
            raise DataIntegrityError(f"{location}: CSV fields must be non-empty strings")
        day = _iso_date(fields[0], location=f"{location}: date")
        open_price, high, low, close, volume = (
            _decimal(value, field=field, location=location)
            for field, value in zip(OHLCV_COLUMNS[1:], fields[1:], strict=True)
        )
        if min(open_price, high, low, close) <= 0:
            raise DataIntegrityError(f"{location}: OHLC prices must be positive")
        if not (low <= open_price <= high and low <= close <= high):
            raise DataIntegrityError(f"{location}: OHLC invariant violated")
        if volume < 0:
            raise DataIntegrityError(f"{location}: volume must be non-negative")
        dates.append(day)

    if len(set(dates)) != expected_rows:
        raise DataIntegrityError(f"{coin}: dates are not unique")
    consecutive = [dates[0] + timedelta(days=offset) for offset in range(expected_rows)]
    if dates != consecutive:
        raise DataIntegrityError(f"{coin}: dates are not sorted and consecutive")
    return dates[0], dates[-1]


def audit_ohlcv_dataset(repo_root: str | Path, *, expected_rows: int = EXPECTED_OHLCV_ROWS) -> dict[str, Any]:
    """Verify immutable bytes first, then metadata and all six CSV contracts."""
    try:
        root = Path(repo_root).resolve()
        if isinstance(expected_rows, bool) or not isinstance(expected_rows, int) or expected_rows <= 0:
            raise DataIntegrityError("expected_rows must be a positive integer")
        verified_payloads = _verify_checksums(root)
        global_start, global_end, _ = _read_metadata(root, expected_rows=expected_rows)
        summary: dict[str, Any] = {}
        for relative_path, coin in EXPECTED_FILES.items():
            start, end = _audit_csv(
                verified_payloads[relative_path],
                relative_path=relative_path,
                coin=coin,
                expected_rows=expected_rows,
            )
            if start != global_start or end != global_end:
                raise DataIntegrityError(f"{coin}: CSV coverage does not match metadata period")
            summary[coin] = {
                "rows": expected_rows,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            }
        return {"status": "ok", "checksum_schema_version": CHECKSUM_SCHEMA_VERSION, "coins": summary}
    except DataIntegrityError:
        raise
    except (OSError, TypeError, ValueError, InvalidOperation, csv.Error, StopIteration) as exc:
        raise DataIntegrityError(f"OHLCV audit failed: {exc}") from exc
