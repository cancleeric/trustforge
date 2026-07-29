"""Versioned, point-in-time asset-intrinsic facts.

This module deliberately stores facts independently from the trust scorer.  A
profile never changes a report score; a later, separately reviewed consumer may
use only dimensions exposed by :meth:`AssetIntrinsicRepository.pit_view`.
"""

from __future__ import annotations

import json
import hashlib
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable

ASSET_INTRINSIC_SCHEMA_VERSION = "1.0.0"
MAX_RECORDS_FILE_BYTES = 1_048_576
MAX_EVIDENCE_FILE_BYTES = 65_536
MAX_RECORD_COUNT = 1_000
MAX_URL_COUNT = 16
MAX_URL_LENGTH = 2_048
MAX_TEXT_LENGTH = 4_096
MAX_PATH_LENGTH = 255
MAX_REVISION_LENGTH = 256
MAX_TIMESTAMP_LENGTH = 64
STALE_WINDOW_DAYS: int = 365


def asset_intrinsic_migration_contract() -> dict[str, Any]:
    return {
        "schema_version": ASSET_INTRINSIC_SCHEMA_VERSION,
        "supported_migrations": [],
        "description": (
            "Initial schema; five-dimension asset-intrinsic profiles with "
            "PIT-safe views."
        ),
        "breaking_changes": [],
    }


class IntrinsicDimensionName(StrEnum):
    ISSUANCE_PREDICTABILITY = "issuance_predictability"
    CONTROL_DISPERSION = "control_dispersion"
    SUPPLY_VERIFIABILITY = "supply_verifiability"
    GOVERNANCE_CAPTURE_RESISTANCE = "governance_capture_resistance"
    HOLDER_CONCENTRATION = "holder_concentration"


class IntrinsicFactStatus(StrEnum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    STALE = "stale"
    CONFLICTED = "conflicted"


INTRINSIC_DIMENSION_NAMES = tuple(item.value for item in IntrinsicDimensionName)
INTRINSIC_FACT_STATUSES = tuple(item.value for item in IntrinsicFactStatus)


@dataclass(frozen=True)
class IntrinsicProvenance:
    source_urls: tuple[str, ...]
    methodology: str
    content_hash: str
    coverage: str
    evidence_path: str
    source_revision: str
    evidence_kind: str
    source_coordinates: str

    def __post_init__(self) -> None:
        if not isinstance(self.source_urls, tuple) or any(
            not isinstance(url, str) or not url.startswith("https://") for url in self.source_urls
        ):
            raise ValueError("provenance.source_urls must contain HTTPS URLs")
        for name in (
            "methodology", "coverage", "evidence_path", "source_revision",
            "evidence_kind", "source_coordinates",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"provenance.{name} must be a non-empty string")
        if len(self.source_urls) > MAX_URL_COUNT:
            raise ValueError("provenance.source_urls exceeds maximum count")
        if any(len(url) > MAX_URL_LENGTH for url in self.source_urls):
            raise ValueError("provenance.source URL exceeds maximum length")
        if len(self.methodology) > MAX_TEXT_LENGTH or len(self.coverage) > MAX_TEXT_LENGTH:
            raise ValueError("provenance text exceeds maximum length")
        if len(self.evidence_path) > MAX_PATH_LENGTH:
            raise ValueError("provenance.evidence_path exceeds maximum length")
        if len(self.source_revision) > MAX_REVISION_LENGTH:
            raise ValueError("provenance.source_revision exceeds maximum length")
        if len(self.source_coordinates) > MAX_TEXT_LENGTH:
            raise ValueError("provenance.source_coordinates exceeds maximum length")
        if self.evidence_kind not in {"upstream_excerpt", "decision_record"}:
            raise ValueError("provenance.evidence_kind is invalid")
        if (
            not isinstance(self.content_hash, str)
            or len(self.content_hash) != 64
            or any(char not in "0123456789abcdef" for char in self.content_hash)
        ):
            raise ValueError("provenance.content_hash must be a lowercase SHA-256 hex digest")
        evidence_path = Path(self.evidence_path)
        if (
            evidence_path.is_absolute()
            or ".." in evidence_path.parts
            or evidence_path.parts[:2] != ("data", "asset_intrinsic_evidence")
            or len(evidence_path.parts) != 3
            or evidence_path.suffix != ".txt"
        ):
            raise ValueError(
                "provenance.evidence_path must be a safe path under "
                "data/asset_intrinsic_evidence"
            )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["source_urls"] = list(self.source_urls)
        return payload


@dataclass(frozen=True)
class IntrinsicDimension:
    name: IntrinsicDimensionName
    status: IntrinsicFactStatus
    value: float | None
    as_of: datetime
    valid_from: datetime
    valid_until: datetime | None
    fetched_at: datetime
    provenance: IntrinsicProvenance

    def __post_init__(self) -> None:
        for name in ("as_of", "valid_from", "fetched_at"):
            _ensure_aware(getattr(self, name), f"dimension.{name}")
        if self.valid_until is not None:
            _ensure_aware(self.valid_until, "dimension.valid_until")
            if self.valid_until <= self.valid_from:
                raise ValueError("dimension.valid_until must be after valid_from")
        if self.fetched_at < self.as_of:
            raise ValueError("dimension.fetched_at cannot precede as_of")
        if self.status is IntrinsicFactStatus.KNOWN:
            if type(self.value) not in {int, float}:
                raise ValueError("known dimension.value must be numeric")
            numeric = float(self.value)
            if not math.isfinite(numeric) or not 0.0 <= numeric <= 1.0:
                raise ValueError("known dimension.value must be finite and within [0, 1]")
            if not self.provenance.source_urls:
                raise ValueError("known dimension requires at least one source URL")
            if self.provenance.evidence_kind != "upstream_excerpt":
                raise ValueError("known dimension requires upstream_excerpt evidence")
        elif self.value is not None:
            raise ValueError("non-known dimension.value must be null")

    def eligible_at(self, as_of: datetime) -> bool:
        _ensure_aware(as_of, "as_of")
        return (
            self.status is IntrinsicFactStatus.KNOWN
            and self.valid_from <= as_of
            and self.fetched_at <= as_of
            and self.as_of <= as_of
            and (self.valid_until is None or as_of < self.valid_until)
        )

    def visible_unknown_at(self, as_of: datetime) -> bool:
        _ensure_aware(as_of, "as_of")
        return (
            self.status is IntrinsicFactStatus.UNKNOWN
            and self.valid_from <= as_of
            and self.fetched_at <= as_of
            and self.as_of <= as_of
            and (self.valid_until is None or as_of < self.valid_until)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name.value,
            "status": self.status.value,
            "value": self.value,
            "as_of": _iso(self.as_of),
            "valid_from": _iso(self.valid_from),
            "valid_until": _iso(self.valid_until) if self.valid_until else None,
            "fetched_at": _iso(self.fetched_at),
            "provenance": self.provenance.to_dict(),
        }


@dataclass(frozen=True)
class AssetIntrinsicProfile:
    asset_id: str
    dimensions: tuple[IntrinsicDimension, ...]
    schema_version: str = ASSET_INTRINSIC_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ASSET_INTRINSIC_SCHEMA_VERSION:
            raise ValueError(f"unsupported AssetIntrinsicProfile schema_version: {self.schema_version}")
        if not isinstance(self.asset_id, str) or not self.asset_id.strip():
            raise ValueError("AssetIntrinsicProfile.asset_id must be a non-empty string")
        if len(self.asset_id) > MAX_REVISION_LENGTH:
            raise ValueError("AssetIntrinsicProfile.asset_id exceeds maximum length")
        if not isinstance(self.dimensions, tuple):
            raise ValueError("AssetIntrinsicProfile.dimensions must be a tuple")
        names = tuple(dimension.name for dimension in self.dimensions)
        expected = tuple(IntrinsicDimensionName)
        if len(names) != len(set(names)) or set(names) != set(expected):
            raise ValueError("AssetIntrinsicProfile must contain each intrinsic dimension exactly once")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "asset_id": self.asset_id,
            "dimensions": [dimension.to_dict() for dimension in self.dimensions],
        }


@dataclass(frozen=True)
class AssetIntrinsicRecord:
    profile: AssetIntrinsicProfile
    valid_from: datetime
    fetched_at: datetime

    def __post_init__(self) -> None:
        _ensure_aware(self.valid_from, "record.valid_from")
        _ensure_aware(self.fetched_at, "record.fetched_at")


@dataclass(frozen=True)
class AssetIntrinsicView:
    """PIT-safe view. Stale, conflicted and future facts are omitted."""

    asset_id: str
    as_of: datetime
    dimensions: tuple[IntrinsicDimension, ...]

    def __post_init__(self) -> None:
        _ensure_aware(self.as_of, "view.as_of")
        if any(
            dimension.status not in {IntrinsicFactStatus.KNOWN, IntrinsicFactStatus.UNKNOWN}
            for dimension in self.dimensions
        ):
            raise ValueError("PIT view may contain only known or unknown dimensions")

    @property
    def eligible_dimensions(self) -> tuple[IntrinsicDimension, ...]:
        return tuple(dimension for dimension in self.dimensions if dimension.eligible_at(self.as_of))


class AssetIntrinsicRepository:
    def __init__(self, records: Iterable[AssetIntrinsicRecord] = ()) -> None:
        materialized = tuple(records)
        identities = tuple(
            (record.profile.asset_id, record.valid_from, record.fetched_at)
            for record in materialized
        )
        if len(identities) != len(set(identities)):
            raise ValueError(
                "ambiguous duplicate asset intrinsic record identity "
                "(asset_id, valid_from, fetched_at)"
            )
        self._records = tuple(
            sorted(
                materialized,
                key=lambda record: (
                    record.profile.asset_id,
                    record.valid_from,
                    record.fetched_at,
                ),
            )
        )

    def lookup(self, asset_id: str, as_of: datetime) -> AssetIntrinsicRecord | None:
        _ensure_aware(as_of, "as_of")
        candidates = (
            record
            for record in self._records
            if record.profile.asset_id == asset_id
            and record.valid_from <= as_of
            and record.fetched_at <= as_of
        )
        return max(
            candidates,
            key=lambda record: (record.valid_from, record.fetched_at),
            default=None,
        )

    def pit_view(self, asset_id: str, as_of: datetime) -> AssetIntrinsicView | None:
        record = self.lookup(asset_id, as_of)
        if record is None:
            return None
        dimensions = tuple(
            dimension
            for dimension in record.profile.dimensions
            if dimension.eligible_at(as_of) or dimension.visible_unknown_at(as_of)
        )
        return AssetIntrinsicView(asset_id=asset_id, as_of=as_of, dimensions=dimensions)


def parse_asset_intrinsic_profile(payload: dict[str, Any]) -> AssetIntrinsicProfile:
    _require_exact_keys(payload, {"schema_version", "asset_id", "dimensions"}, "profile")
    dimensions = payload["dimensions"]
    if not isinstance(dimensions, list):
        raise ValueError("profile.dimensions must be an array")
    return AssetIntrinsicProfile(
        schema_version=_required_string(payload["schema_version"], "profile.schema_version"),
        asset_id=_required_string(payload["asset_id"], "profile.asset_id"),
        dimensions=tuple(_parse_dimension(item) for item in dimensions),
    )


def parse_asset_intrinsic_record(payload: dict[str, Any]) -> AssetIntrinsicRecord:
    _require_exact_keys(payload, {"profile", "valid_from", "fetched_at"}, "record")
    if not isinstance(payload["profile"], dict):
        raise ValueError("record.profile must be an object")
    return AssetIntrinsicRecord(
        profile=parse_asset_intrinsic_profile(payload["profile"]),
        valid_from=_parse_timestamp(payload["valid_from"], "record.valid_from"),
        fetched_at=_parse_timestamp(payload["fetched_at"], "record.fetched_at"),
    )


def load_asset_intrinsic_records(
    path: Path, *, evidence_root: Path | None = None
) -> tuple[AssetIntrinsicRecord, ...]:
    raw = json.loads(
        _read_bounded_bytes(path, MAX_RECORDS_FILE_BYTES, "records file").decode("utf-8")
    )
    if not isinstance(raw, list):
        raise ValueError("asset intrinsic records must be an array")
    if len(raw) > MAX_RECORD_COUNT:
        raise ValueError("asset intrinsic record count exceeds maximum")
    if any(not isinstance(item, dict) for item in raw):
        raise ValueError("asset intrinsic record must be an object")
    records = tuple(parse_asset_intrinsic_record(item) for item in raw)
    root = evidence_root if evidence_root is not None else path.resolve().parent.parent
    verify_asset_intrinsic_evidence(records, root)
    from trustforge.asset_intrinsic_shadow import (
        validate_intrinsic_forbidden_inferences,
    )
    for record in records:
        violations = validate_intrinsic_forbidden_inferences(record.profile)
        if violations:
            raise ValueError(
                f"forbidden inference in asset {record.profile.asset_id}: "
                + "; ".join(violations)
            )
    return records


def verify_asset_intrinsic_evidence(
    records: Iterable[AssetIntrinsicRecord], root: Path
) -> None:
    """Verify every provenance hash against exact checked-in evidence bytes."""
    resolved_root = root.resolve()
    for record in records:
        for dimension in record.profile.dimensions:
            provenance = dimension.provenance
            evidence_file = (resolved_root / provenance.evidence_path).resolve()
            try:
                evidence_file.relative_to(resolved_root)
            except ValueError as exc:
                raise ValueError("evidence path escapes repository root") from exc
            if not evidence_file.is_file():
                raise ValueError(f"evidence file does not exist: {provenance.evidence_path}")
            exact_bytes = _read_bounded_bytes(
                evidence_file, MAX_EVIDENCE_FILE_BYTES, "evidence file"
            )
            actual = hashlib.sha256(exact_bytes).hexdigest()
            if actual != provenance.content_hash:
                raise ValueError(
                    f"evidence fingerprint mismatch for {provenance.evidence_path}: "
                    f"expected {provenance.content_hash}, got {actual}"
                )


def _parse_dimension(payload: Any) -> IntrinsicDimension:
    if not isinstance(payload, dict):
        raise ValueError("dimension must be an object")
    _require_exact_keys(
        payload,
        {
            "name", "status", "value", "as_of", "valid_from", "valid_until",
            "fetched_at", "provenance",
        },
        "dimension",
    )
    provenance = payload["provenance"]
    if not isinstance(provenance, dict):
        raise ValueError("dimension.provenance must be an object")
    _require_exact_keys(
        provenance,
        {
            "source_urls", "methodology", "content_hash", "coverage",
            "evidence_path", "source_revision", "evidence_kind",
            "source_coordinates",
        },
        "provenance",
    )
    source_urls = provenance["source_urls"]
    if not isinstance(source_urls, list) or any(not isinstance(item, str) for item in source_urls):
        raise ValueError("provenance.source_urls must be an array of strings")
    valid_until = payload["valid_until"]
    if valid_until is not None and not isinstance(valid_until, str):
        raise ValueError("dimension.valid_until must be an ISO timestamp or null")
    try:
        name = IntrinsicDimensionName(payload["name"])
        status = IntrinsicFactStatus(payload["status"])
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid intrinsic dimension name or status") from exc
    return IntrinsicDimension(
        name=name,
        status=status,
        value=payload["value"],
        as_of=_parse_timestamp(payload["as_of"], "dimension.as_of"),
        valid_from=_parse_timestamp(payload["valid_from"], "dimension.valid_from"),
        valid_until=(
            _parse_timestamp(valid_until, "dimension.valid_until") if valid_until is not None else None
        ),
        fetched_at=_parse_timestamp(payload["fetched_at"], "dimension.fetched_at"),
        provenance=IntrinsicProvenance(
            source_urls=tuple(source_urls),
            methodology=_required_string(provenance["methodology"], "provenance.methodology"),
            content_hash=_required_string(provenance["content_hash"], "provenance.content_hash"),
            coverage=_required_string(provenance["coverage"], "provenance.coverage"),
            evidence_path=_required_string(
                provenance["evidence_path"], "provenance.evidence_path"
            ),
            source_revision=_required_string(
                provenance["source_revision"], "provenance.source_revision"
            ),
            evidence_kind=_required_string(
                provenance["evidence_kind"], "provenance.evidence_kind"
            ),
            source_coordinates=_required_string(
                provenance["source_coordinates"], "provenance.source_coordinates"
            ),
        ),
    )


def _require_exact_keys(payload: dict[str, Any], expected: set[str], label: str) -> None:
    missing = sorted(expected - set(payload))
    extra = sorted(set(payload) - expected)
    if missing:
        raise ValueError(f"missing {label} fields: {', '.join(missing)}")
    if extra:
        raise ValueError(f"unexpected {label} fields: {', '.join(extra)}")


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _parse_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be an ISO timestamp")
    if len(value) > MAX_TIMESTAMP_LENGTH:
        raise ValueError(f"{label} exceeds maximum length")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO timestamp") from exc
    _ensure_aware(parsed, label)
    return parsed.astimezone(timezone.utc)


def _ensure_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware")


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_bounded_bytes(path: Path, maximum: int, label: str) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ValueError(f"{label} cannot be inspected: {path}") from exc
    if size > maximum:
        raise ValueError(f"{label} exceeds maximum size of {maximum} bytes")
    try:
        with path.open("rb") as handle:
            payload = handle.read(maximum + 1)
    except OSError as exc:
        raise ValueError(f"{label} cannot be read: {path}") from exc
    if len(payload) > maximum:
        raise ValueError(f"{label} exceeds maximum size of {maximum} bytes")
    return payload
