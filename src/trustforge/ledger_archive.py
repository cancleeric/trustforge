"""Append-only cost-ledger export, integrity verification and local restore."""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .ledger import Ledger

ARCHIVE_SCHEMA_VERSION = 1


def _canonical_json(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _jsonl_payload(records: Iterable[dict[str, Any]]) -> bytes:
    return ("".join(f"{_canonical_json(record)}\n" for record in records)).encode("utf-8")


def _record_total(records: Iterable[dict[str, Any]]) -> float:
    return round(sum(float(record.get("total_cost_usd", 0.0) or 0.0) for record in records), 6)


def _manifest(records: list[dict[str, Any]], payload: bytes, *, format_name: str) -> dict[str, Any]:
    return {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "archive_kind": "trustforge_cost_ledger",
        "format": format_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "record_count": len(records),
        "total_cost_usd": _record_total(records),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "canonical_jsonl_sha256": hashlib.sha256(_jsonl_payload(records)).hexdigest(),
    }


def export_jsonl(ledger: Ledger, destination: str | Path) -> dict[str, Any]:
    """Write a canonical JSONL archive plus its sidecar manifest."""
    records = ledger.read_all()
    payload = _jsonl_payload(records)
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    manifest = _manifest(records, payload, format_name="jsonl")
    path.with_suffix(path.suffix + ".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def export_csv(ledger: Ledger, destination: str | Path) -> dict[str, Any]:
    """Write spreadsheet-friendly CSV while retaining structured calls in JSON."""
    records = ledger.read_all()
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["run_id", "ts", "coin", "question_type", "offline", "call_count", "total_cost_usd", "calls_json"]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for record in records:
            calls = record.get("calls") if isinstance(record.get("calls"), list) else []
            writer.writerow({
                "run_id": record.get("run_id", ""), "ts": record.get("ts", ""),
                "coin": record.get("coin", ""), "question_type": record.get("question_type", ""),
                "offline": bool(record.get("offline", False)), "call_count": len(calls),
                "total_cost_usd": record.get("total_cost_usd", 0.0),
                "calls_json": _canonical_json({"calls": calls}),
            })
    payload = path.read_bytes()
    manifest = _manifest(records, payload, format_name="csv")
    path.with_suffix(path.suffix + ".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def _load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"archive line {number} is not an object")
        records.append(value)
    return records


def verify_jsonl_archive(archive: str | Path, manifest_path: str | Path | None = None) -> dict[str, Any]:
    """Verify exact bytes and normalized ledger semantics from a JSONL manifest."""
    archive_path = Path(archive)
    manifest_file = Path(manifest_path) if manifest_path else archive_path.with_suffix(archive_path.suffix + ".manifest.json")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if manifest.get("archive_kind") != "trustforge_cost_ledger" or manifest.get("format") != "jsonl":
        raise ValueError("manifest is not a TrustForge JSONL ledger archive")
    payload = archive_path.read_bytes()
    records = _load_jsonl(archive_path)
    actual = {
        "record_count": len(records),
        "total_cost_usd": _record_total(records),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "canonical_jsonl_sha256": hashlib.sha256(_jsonl_payload(records)).hexdigest(),
    }
    expected = {key: manifest.get(key) for key in actual}
    if actual != expected:
        raise ValueError("ledger archive integrity mismatch")
    return {"verified": True, **actual}


def restore_jsonl_archive(archive: str | Path, destination: str | Path, *, manifest_path: str | Path | None = None) -> dict[str, Any]:
    """Restore only to a new local JSONL target for a safe, repeatable drill."""
    verified = verify_jsonl_archive(archive, manifest_path)
    destination_path = Path(destination)
    if destination_path.exists():
        raise FileExistsError("restore target already exists; refusing to overwrite a ledger")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    destination_path.write_bytes(Path(archive).read_bytes())
    return {"restored": True, "destination": str(destination_path), **verified}
