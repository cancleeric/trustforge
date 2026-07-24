from __future__ import annotations

import csv
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from trustforge import data_integrity
from trustforge.data_integrity import COINS, DataIntegrityError, audit_ohlcv_dataset


def _write_manifest(root: Path) -> None:
    files = {}
    for coin in COINS:
        relative_path = f"data/{coin}_daily_ohlcv.csv"
        payload = (root / "data" / relative_path).read_bytes()
        files[relative_path] = hashlib.sha256(payload).hexdigest()
    manifest = {"schema_version": "1.0.0", "algorithm": "sha256", "files": files}
    (root / "data" / "ohlcv_checksums.json").write_text(json.dumps(manifest), encoding="utf-8")


def _write_ohlcv_fixture(root: Path, *, bad_high: bool = False) -> None:
    data_dir = root / "data" / "data"
    data_dir.mkdir(parents=True)
    start = date(2026, 1, 1)
    symbols = []
    for coin in COINS:
        file_name = f"{coin}_daily_ohlcv.csv"
        with (data_dir / file_name).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(("date", "open", "high", "low", "close", "volume"))
            for offset in range(3):
                high = "9" if bad_high and coin == "BTC" and offset == 1 else "12"
                writer.writerow(((start + timedelta(days=offset)).isoformat(), "10", high, "8", "11", "1"))
        symbols.append(
            {
                "asset": coin,
                "pair": f"{coin}USDT",
                "file": f"data/{file_name}",
                "rows": 3,
                "start_date": "2026-01-01",
                "end_date": "2026-01-03",
            }
        )
    metadata = {
        "dataset_name": "Fixture OHLCV",
        "generated_at": "2026-01-04T00:00:00Z",
        "price_unit": "USDT",
        "source": {"name": "public_market_data", "type": "public_market_data"},
        "time_basis": "UTC",
        "interval": "1d",
        "period": {"start_date": "2026-01-01", "end_date": "2026-01-03"},
        "symbols": symbols,
        "columns": [
            {"name": field, "description": f"Fixture {field}"}
            for field in ("date", "open", "high", "low", "close", "volume")
        ],
    }
    (root / "data" / "dataset_metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    _write_manifest(root)


def _rewrite_btc_row(root: Path, replacement: str) -> None:
    path = root / "data" / "data" / "BTC_daily_ohlcv.csv"
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[1] = replacement
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _write_manifest(root)


def test_ohlcv_audit_checks_all_five_coins_and_metadata(tmp_path: Path) -> None:
    _write_ohlcv_fixture(tmp_path)
    result = audit_ohlcv_dataset(tmp_path, expected_rows=3)
    assert result["status"] == "ok"
    assert result["checksum_schema_version"] == "1.0.0"
    assert set(result["coins"]) == set(COINS)


def test_ohlcv_audit_rejects_tampered_bytes_before_parsing(tmp_path: Path) -> None:
    _write_ohlcv_fixture(tmp_path)
    path = tmp_path / "data" / "data" / "BTC_daily_ohlcv.csv"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(DataIntegrityError, match="checksum mismatch"):
        audit_ohlcv_dataset(tmp_path, expected_rows=3)


def test_ohlcv_parser_uses_the_same_bytes_snapshot_as_checksum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_ohlcv_fixture(tmp_path)
    original_read_metadata = data_integrity._read_metadata

    def mutate_after_digest(root: Path, *, expected_rows: int):
        result = original_read_metadata(root, expected_rows=expected_rows)
        (root / "data" / "data" / "BTC_daily_ohlcv.csv").write_text("mutated after digest", encoding="utf-8")
        return result

    monkeypatch.setattr(data_integrity, "_read_metadata", mutate_after_digest)
    result = audit_ohlcv_dataset(tmp_path, expected_rows=3)
    assert result["coins"]["BTC"]["rows"] == 3


def test_ohlcv_audit_rejects_non_utf8_snapshot(tmp_path: Path) -> None:
    _write_ohlcv_fixture(tmp_path)
    path = tmp_path / "data" / "data" / "BTC_daily_ohlcv.csv"
    path.write_bytes(path.read_bytes() + b"\xff")
    _write_manifest(tmp_path)
    with pytest.raises(DataIntegrityError, match="valid UTF-8"):
        audit_ohlcv_dataset(tmp_path, expected_rows=3)


def _protected_path(root: Path, kind: str) -> Path:
    if kind == "manifest":
        return root / "data" / "ohlcv_checksums.json"
    if kind == "metadata":
        return root / "data" / "dataset_metadata.json"
    return root / "data" / "data" / "BTC_daily_ohlcv.csv"


@pytest.mark.parametrize("kind", ["manifest", "metadata", "csv"])
def test_ohlcv_audit_rejects_symlinked_inputs(tmp_path: Path, kind: str) -> None:
    _write_ohlcv_fixture(tmp_path)
    path = _protected_path(tmp_path, kind)
    target = path.with_name(f"{path.name}.real")
    path.rename(target)
    path.symlink_to(target.name)
    with pytest.raises(DataIntegrityError):
        audit_ohlcv_dataset(tmp_path, expected_rows=3)


@pytest.mark.parametrize(
    ("kind", "maximum_bytes"),
    [
        ("manifest", data_integrity.MANIFEST_MAX_BYTES),
        ("metadata", data_integrity.METADATA_MAX_BYTES),
        ("csv", data_integrity.CSV_MAX_BYTES),
    ],
)
def test_ohlcv_audit_rejects_oversized_inputs(
    tmp_path: Path, kind: str, maximum_bytes: int
) -> None:
    _write_ohlcv_fixture(tmp_path)
    path = _protected_path(tmp_path, kind)
    path.write_bytes(b"x" * (maximum_bytes + 1))
    if kind == "csv":
        _write_manifest(tmp_path)
    with pytest.raises(DataIntegrityError, match="size limit"):
        audit_ohlcv_dataset(tmp_path, expected_rows=3)


@pytest.mark.parametrize(
    "replacement",
    [
        "2026-01-01,10,12,8,11,1,unexpected",
        "2026-01-01,10,12,8,11",
    ],
)
def test_ohlcv_audit_rejects_extra_and_short_rows(tmp_path: Path, replacement: str) -> None:
    _write_ohlcv_fixture(tmp_path)
    _rewrite_btc_row(tmp_path, replacement)
    with pytest.raises(DataIntegrityError, match="exactly six CSV fields"):
        audit_ohlcv_dataset(tmp_path, expected_rows=3)


def test_ohlcv_audit_rejects_bad_metadata_type(tmp_path: Path) -> None:
    _write_ohlcv_fixture(tmp_path)
    path = tmp_path / "data" / "dataset_metadata.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata["symbols"][0]["rows"] = "3"
    path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(DataIntegrityError, match="rows must be an integer"):
        audit_ohlcv_dataset(tmp_path, expected_rows=3)


def test_ohlcv_audit_rejects_bad_metadata_date_format(tmp_path: Path) -> None:
    _write_ohlcv_fixture(tmp_path)
    path = tmp_path / "data" / "dataset_metadata.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata["period"]["start_date"] = "2026-1-01"
    path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(DataIntegrityError, match="YYYY-MM-DD"):
        audit_ohlcv_dataset(tmp_path, expected_rows=3)


@pytest.mark.parametrize(
    ("path_parts", "bad_value", "message"),
    [
        (("price_unit",), "USD", "price_unit must be USDT"),
        (("generated_at",), "2026-01-04T00:00:00", "timezone-aware ISO8601"),
        (("source", "name"), "fixture", "source.name must be public_market_data"),
        (("source", "type"), "fixture", "source.type must be public_market_data"),
    ],
)
def test_ohlcv_audit_locks_official_metadata_values(
    tmp_path: Path, path_parts: tuple[str, ...], bad_value: str, message: str
) -> None:
    _write_ohlcv_fixture(tmp_path)
    path = tmp_path / "data" / "dataset_metadata.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    target = metadata
    for part in path_parts[:-1]:
        target = target[part]
    target[path_parts[-1]] = bad_value
    path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(DataIntegrityError, match=message):
        audit_ohlcv_dataset(tmp_path, expected_rows=3)


@pytest.mark.parametrize("bad_date", ["2026-1-01", "2026-01-01T00:00:00Z", "2026-02-30"])
def test_ohlcv_audit_rejects_bad_date_format_or_calendar_date(tmp_path: Path, bad_date: str) -> None:
    _write_ohlcv_fixture(tmp_path)
    _rewrite_btc_row(tmp_path, f"{bad_date},10,12,8,11,1")
    with pytest.raises(DataIntegrityError, match="date"):
        audit_ohlcv_dataset(tmp_path, expected_rows=3)


def test_ohlcv_audit_rejects_price_invariant_violation(tmp_path: Path) -> None:
    _write_ohlcv_fixture(tmp_path, bad_high=True)
    with pytest.raises(DataIntegrityError, match="OHLC invariant violated"):
        audit_ohlcv_dataset(tmp_path, expected_rows=3)


def test_repository_ohlcv_dataset_has_verified_1826_day_coverage() -> None:
    root = Path(__file__).resolve().parents[1]
    result = audit_ohlcv_dataset(root)
    expected = {"rows": 1826, "start_date": "2021-06-01", "end_date": "2026-05-31"}
    assert all(item == expected for item in result["coins"].values())
