"""AssetContext repository with as-of validity lookups."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from trustforge.asset_context import AssetContext, asset_context_from_dict


@dataclass(frozen=True)
class AssetContextRecord:
    context: AssetContext
    valid_from: datetime
    fetched_at: datetime
    source: str

    def __post_init__(self) -> None:
        if self.valid_from.tzinfo is None or self.fetched_at.tzinfo is None:
            raise ValueError("AssetContextRecord timestamps must be timezone-aware")
        if not self.source.strip():
            raise ValueError("AssetContextRecord.source must be non-empty string")

    def to_dict(self) -> dict[str, Any]:
        return {
            "context": self.context.to_dict(),
            "valid_from": self.valid_from.isoformat(),
            "fetched_at": self.fetched_at.isoformat(),
            "source": self.source,
        }


class AssetContextRepository:
    def __init__(self, records: Iterable[AssetContextRecord] = ()) -> None:
        self._records = tuple(sorted(records, key=lambda record: record.valid_from))

    def lookup(self, asset_id: str, as_of: datetime) -> AssetContextRecord | None:
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        candidates = (
            record
            for record in self._records
            if record.context.asset_id == asset_id and record.valid_from <= as_of
        )
        return max(candidates, key=lambda record: record.valid_from, default=None)

    def by_symbol(self, symbol: str, as_of: datetime) -> AssetContextRecord | None:
        normalized = symbol.casefold()
        candidates = (
            record
            for record in self._records
            if record.context.symbol.casefold() == normalized and record.valid_from <= as_of
        )
        return max(candidates, key=lambda record: record.valid_from, default=None)


def parse_asset_context_record(payload: dict[str, Any]) -> AssetContextRecord:
    required = {"context", "valid_from", "fetched_at", "source"}
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"missing AssetContextRecord fields: {', '.join(missing)}")
    extra = sorted(set(payload) - required)
    if extra:
        raise ValueError(f"unexpected AssetContextRecord fields: {', '.join(extra)}")
    context_payload = payload["context"]
    if not isinstance(context_payload, dict):
        raise ValueError("AssetContextRecord.context must be object")
    source = payload["source"]
    if not isinstance(source, str) or not source.strip():
        raise ValueError("AssetContextRecord.source must be non-empty string")
    return AssetContextRecord(
        context=asset_context_from_dict(context_payload),
        valid_from=_parse_timestamp(payload["valid_from"], "valid_from"),
        fetched_at=_parse_timestamp(payload["fetched_at"], "fetched_at"),
        source=source,
    )


def load_asset_context_records(path: Path) -> tuple[AssetContextRecord, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Asset context fixture must be a list")
    return tuple(parse_asset_context_record(item) for item in raw)


def _parse_timestamp(raw: Any, field_name: str) -> datetime:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"AssetContextRecord.{field_name} must be ISO timestamp string")
    timestamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError(f"AssetContextRecord.{field_name} must be timezone-aware")
    return timestamp.astimezone(timezone.utc)
