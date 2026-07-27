"""Versioned, point-in-time asset-intrinsic facts.

This module deliberately stores facts independently from the trust scorer.  A
profile never changes a report score; a later, separately reviewed consumer may
use only dimensions exposed by :meth:`AssetIntrinsicRepository.pit_view`.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable

ASSET_INTRINSIC_SCHEMA_VERSION = "1.0.0"


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

    def __post_init__(self) -> None:
        if not isinstance(self.source_urls, tuple) or any(
            not isinstance(url, str) or not url.startswith("https://") for url in self.source_urls
        ):
            raise ValueError("provenance.source_urls must contain HTTPS URLs")
        for name in ("methodology", "coverage"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"provenance.{name} must be a non-empty string")
        if (
            not isinstance(self.content_hash, str)
            or len(self.content_hash) != 64
            or any(char not in "0123456789abcdef" for char in self.content_hash)
        ):
            raise ValueError("provenance.content_hash must be a lowercase SHA-256 hex digest")

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


def load_asset_intrinsic_records(path: Path) -> tuple[AssetIntrinsicRecord, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("asset intrinsic records must be an array")
    if any(not isinstance(item, dict) for item in raw):
        raise ValueError("asset intrinsic record must be an object")
    return tuple(parse_asset_intrinsic_record(item) for item in raw)


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
        provenance, {"source_urls", "methodology", "content_hash", "coverage"}, "provenance"
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
