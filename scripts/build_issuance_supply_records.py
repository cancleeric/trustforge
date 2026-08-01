#!/usr/bin/env python3
"""Protocol-agnostic offline packager for AssetIntrinsicProfile from PEP directories.

Reads a Protocol Evidence Pack (PEP), verifies SHA-256 hashes and file sizes,
and emits a deterministic, sorted JSON AssetIntrinsicProfile to stdout.
No network I/O is performed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from trustforge.asset_intrinsic import (
    MAX_EVIDENCE_FILE_BYTES,
    MAX_PATH_LENGTH,
    MAX_REVISION_LENGTH,
    MAX_TEXT_LENGTH,
    MAX_TIMESTAMP_LENGTH,
    MAX_URL_COUNT,
    MAX_URL_LENGTH,
)

PEP_SCHEMA_VERSION = "1.0.0"
MAX_MANIFEST_BYTES = 65_536


class ProtocolFamily(StrEnum):
    POW_SOURCE_CODE = "pow_source_code"
    POS_CONSENSUS_SPEC = "pos_consensus_spec"
    EVM_BYTECODE = "evm_bytecode"
    FORMAL_POLICY_DOC = "formal_policy_doc"


class PepValidationError(ValueError):
    """PEP content failed validation."""


def _read_bounded_bytes(path: Path, maximum: int, label: str) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise PepValidationError(f"{label} cannot be inspected: {path}") from exc
    if size > maximum:
        raise PepValidationError(f"{label} exceeds maximum size of {maximum} bytes")
    try:
        with path.open("rb") as handle:
            payload = handle.read(maximum + 1)
    except OSError as exc:
        raise PepValidationError(f"{label} cannot be read: {path}") from exc
    if len(payload) > maximum:
        raise PepValidationError(f"{label} exceeds maximum size of {maximum} bytes")
    return payload


def _validate_https_url_list(urls: list[str]) -> None:
    if not isinstance(urls, list):
        raise PepValidationError("source_urls must be a list")
    if len(urls) > MAX_URL_COUNT:
        raise PepValidationError("source_urls exceeds maximum count")
    for url in urls:
        if not isinstance(url, str) or not url.startswith("https://"):
            raise PepValidationError("source_urls must contain HTTPS URLs")
        if len(url) > MAX_URL_LENGTH:
            raise PepValidationError("source URL exceeds maximum length")


def _validate_sha256_hex(value: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(
        char not in "0123456789abcdef" for char in value
    ):
        raise PepValidationError("content_hash must be a lowercase SHA-256 hex digest")


def _load_manifest(manifest_path: Path) -> dict[str, Any]:
    raw = _read_bounded_bytes(manifest_path, MAX_MANIFEST_BYTES, "manifest")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise PepValidationError("manifest is not valid UTF-8 JSON") from exc
    if not isinstance(data, dict):
        raise PepValidationError("manifest must be a JSON object")
    return data


def _validate_manifest(manifest: dict[str, Any]) -> None:
    required = [
        "manifest_version", "asset_id", "protocol_family", "source_revision",
        "source_urls", "source_coordinates", "evidence_files", "methodology",
        "coverage", "valid_from", "valid_until", "dimensions",
    ]
    missing = sorted(set(required) - set(manifest))
    extra = sorted(set(manifest) - set(required))
    if missing:
        raise PepValidationError(f"missing manifest fields: {', '.join(missing)}")
    if extra:
        raise PepValidationError(f"unexpected manifest fields: {', '.join(extra)}")

    if manifest["manifest_version"] != PEP_SCHEMA_VERSION:
        raise PepValidationError(
            f"unsupported manifest_version: {manifest['manifest_version']}"
        )

    try:
        ProtocolFamily(manifest["protocol_family"])
    except ValueError as exc:
        raise PepValidationError(f"unknown protocol_family: {manifest['protocol_family']}") from exc

    asset_id = manifest["asset_id"]
    if not isinstance(asset_id, str) or not asset_id.strip():
        raise PepValidationError("asset_id must be a non-empty string")
    if len(asset_id) > MAX_REVISION_LENGTH:
        raise PepValidationError("asset_id exceeds maximum length")

    source_revision = manifest["source_revision"]
    if not isinstance(source_revision, str) or not source_revision.strip():
        raise PepValidationError("source_revision must be a non-empty string")
    if len(source_revision) > MAX_REVISION_LENGTH:
        raise PepValidationError("source_revision exceeds maximum length")

    _validate_https_url_list(manifest["source_urls"])

    source_coordinates = manifest["source_coordinates"]
    if not isinstance(source_coordinates, str) or not source_coordinates.strip():
        raise PepValidationError("source_coordinates must be a non-empty string")
    if len(source_coordinates) > MAX_TEXT_LENGTH:
        raise PepValidationError("source_coordinates exceeds maximum length")

    evidence_files = manifest["evidence_files"]
    if not isinstance(evidence_files, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in evidence_files.items()
    ):
        raise PepValidationError("evidence_files must be a dict of filename->sha256")

    methodology = manifest["methodology"]
    if not isinstance(methodology, str) or not methodology.strip():
        raise PepValidationError("methodology must be a non-empty string")
    if len(methodology) > MAX_TEXT_LENGTH:
        raise PepValidationError("methodology exceeds maximum length")

    coverage = manifest["coverage"]
    if not isinstance(coverage, str) or not coverage.strip():
        raise PepValidationError("coverage must be a non-empty string")
    if len(coverage) > MAX_TEXT_LENGTH:
        raise PepValidationError("coverage exceeds maximum length")

    for field in ("valid_from", "valid_until"):
        value = manifest[field]
        if value is None:
            continue
        if not isinstance(value, str):
            raise PepValidationError(f"{field} must be an ISO timestamp or null")
        if len(value) > MAX_TIMESTAMP_LENGTH:
            raise PepValidationError(f"{field} exceeds maximum length")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise PepValidationError(f"{field} must be timezone-aware")
        except ValueError as exc:
            raise PepValidationError(f"{field} must be an ISO timestamp") from exc

    dimensions = manifest["dimensions"]
    if not isinstance(dimensions, dict):
        raise PepValidationError("dimensions must be a dict")
    for dim_name, dim_spec in dimensions.items():
        if not isinstance(dim_spec, dict):
            raise PepValidationError(f"dimension {dim_name} must be a dict")
        for key in ("status", "value", "evidence_file"):
            if key not in dim_spec:
                raise PepValidationError(f"dimension {dim_name} missing {key}")
        if dim_spec["status"] == "known":
            if not isinstance(dim_spec["value"], (int, float)):
                raise PepValidationError(f"dimension {dim_name} value must be numeric when known")
            if not 0.0 <= dim_spec["value"] <= 1.0:
                raise PepValidationError(f"dimension {dim_name} value must be in [0,1]")


def _verify_evidence_files(
    manifest: dict[str, Any], pep_dir: Path, evidence_root: Path
) -> dict[str, Path]:
    evidence_files = manifest["evidence_files"]
    resolved: dict[str, Path] = {}
    for filename, expected_hash in evidence_files.items():
        _validate_sha256_hex(expected_hash)
        # Search PEP evidence dir first, then flat evidence dir
        pep_evidence_path = pep_dir / "evidence" / filename
        flat_evidence_path = evidence_root / "data" / "asset_intrinsic_evidence" / filename
        
        if pep_evidence_path.is_file():
            evidence_path = pep_evidence_path
        elif flat_evidence_path.is_file():
            evidence_path = flat_evidence_path
        else:
            raise PepValidationError(f"evidence file not found: {filename}")

        exact_bytes = _read_bounded_bytes(evidence_path, MAX_EVIDENCE_FILE_BYTES, "evidence file")
        actual = hashlib.sha256(exact_bytes).hexdigest()
        if actual != expected_hash:
            raise PepValidationError(
                f"evidence fingerprint mismatch for {filename}: expected {expected_hash}, got {actual}"
            )
        resolved[filename] = evidence_path
    return resolved


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _build_profile(
    manifest: dict[str, Any], evidence_resolved: dict[str, Path], evidence_root: Path
) -> dict[str, Any]:
    asset_id = manifest["asset_id"]
    source_revision = manifest["source_revision"]
    source_urls = tuple(manifest["source_urls"])
    methodology = manifest["methodology"]
    coverage = manifest["coverage"]
    source_coordinates = manifest["source_coordinates"]
    
    valid_from_str = manifest["valid_from"]
    valid_from = datetime.fromisoformat(valid_from_str.replace("Z", "+00:00"))
    valid_until_str = manifest["valid_until"]
    valid_until = (datetime.fromisoformat(valid_until_str.replace("Z", "+00:00"))
                   if valid_until_str is not None else None)

    all_dimension_names = [
        "issuance_predictability",
        "control_dispersion",
        "supply_verifiability",
        "governance_capture_resistance",
        "holder_concentration",
    ]

    dimensions: list[dict[str, Any]] = []
    covered = manifest["dimensions"]

    for dim_name in all_dimension_names:
        if dim_name in covered:
            spec = covered[dim_name]
            evidence_filename = spec["evidence_file"]
            evidence_path = evidence_resolved[evidence_filename]
            exact_bytes = _read_bounded_bytes(evidence_path, MAX_EVIDENCE_FILE_BYTES, "evidence file")
            content_hash = hashlib.sha256(exact_bytes).hexdigest()

            dimensions.append({
                "name": dim_name,
                "status": "known",
                "value": spec["value"],
                "as_of": _iso(valid_from),
                "valid_from": _iso(valid_from),
                "valid_until": _iso(valid_until),
                "fetched_at": _iso(valid_from),
                "provenance": {
                    "source_urls": list(source_urls),
                    "methodology": methodology,
                    "content_hash": content_hash,
                    "coverage": coverage,
                    "evidence_path": f"data/asset_intrinsic_evidence/{evidence_filename}",
                    "source_revision": source_revision,
                    "evidence_kind": "upstream_excerpt",
                    "source_coordinates": source_coordinates,
                },
            })
        else:
            # Unknown dimensions get a generic decision_record
            coverage_boundary_path = evidence_root / "data" / "asset_intrinsic_evidence" / "pep-coverage-boundary.txt"
            if not coverage_boundary_path.is_file():
                raise PepValidationError("pep-coverage-boundary.txt is missing from evidence directory")
            boundary_bytes = _read_bounded_bytes(
                coverage_boundary_path, MAX_EVIDENCE_FILE_BYTES, "coverage boundary"
            )
            boundary_hash = hashlib.sha256(boundary_bytes).hexdigest()
            dimensions.append({
                "name": dim_name,
                "status": "unknown",
                "value": None,
                "as_of": _iso(valid_from),
                "valid_from": _iso(valid_from),
                "valid_until": _iso(valid_until),
                "fetched_at": _iso(valid_from),
                "provenance": {
                    "source_urls": [],
                    "methodology": f"This dimension is not covered by the {manifest['protocol_family']} protocol evidence pack.",
                    "content_hash": boundary_hash,
                    "coverage": "not covered by current PEP",
                    "evidence_path": "data/asset_intrinsic_evidence/pep-coverage-boundary.txt",
                    "source_revision": f"pep-{manifest['protocol_family']}",
                    "evidence_kind": "decision_record",
                    "source_coordinates": "pep coverage boundary",
                },
            })

    return {
        "schema_version": "1.0.0",
        "asset_id": asset_id,
        "dimensions": dimensions,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pep_dir", type=Path, help="PEP directory (e.g. data/asset_intrinsic_evidence/pep/asset_eth)")
    parser.add_argument("--dry-run", action="store_true", help="Validate without emitting JSON")
    parser.add_argument(
        "--evidence-root",
        type=Path,
        default=None,
        help="Repository root for evidence resolution (default: pep_dir parent)",
    )
    args = parser.parse_args(argv)

    if args.evidence_root is not None:
        evidence_root = args.evidence_root.resolve()
    else:
        evidence_root = args.pep_dir.resolve().parent.parent.parent.parent

    manifest_path = args.pep_dir / "manifest.json"
    if not manifest_path.is_file():
        print(f"manifest.json not found in {args.pep_dir}", file=sys.stderr)
        return 2

    try:
        manifest = _load_manifest(manifest_path)
        _validate_manifest(manifest)
        evidence_resolved = _verify_evidence_files(manifest, args.pep_dir, evidence_root)
        if args.dry_run:
            return 0
        profile = _build_profile(manifest, evidence_resolved, evidence_root)
    except PepValidationError as exc:
        if "fingerprint mismatch" in str(exc):
            print(f"fingerprint mismatch", file=sys.stderr)
        else:
            print(f"PEP validation failed: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(profile, ensure_ascii=False, indent=2, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
