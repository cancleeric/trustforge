from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from trustforge.asset_context_repository import (
    AssetContextRepository,
    load_asset_context_records,
    parse_asset_context_record,
)


FIXTURE = Path(__file__).parent / "fixtures" / "asset_context_records.json"


def utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


def test_repository_loads_fixture_and_preserves_lineage() -> None:
    records = load_asset_context_records(FIXTURE)

    assert len(records) == 2
    assert records[0].context.asset_id == "asset:arb"
    assert records[0].source.startswith("fixture://asset-context/arb/")
    assert records[0].valid_from.tzinfo is timezone.utc
    assert records[0].fetched_at.tzinfo is timezone.utc


def test_repository_returns_latest_context_at_as_of_time() -> None:
    repository = AssetContextRepository(load_asset_context_records(FIXTURE))

    old_record = repository.lookup("asset:arb", utc(2025, 6, 1))
    new_record = repository.lookup("asset:arb", utc(2026, 6, 1))

    assert old_record is not None
    assert old_record.context.market_cap_tier.value == "mid"
    assert new_record is not None
    assert new_record.context.market_cap_tier.value == "large"
    assert repository.by_symbol("arb", utc(2026, 6, 1)) == new_record


def test_repository_does_not_return_context_before_it_was_fetched() -> None:
    repository = AssetContextRepository(load_asset_context_records(FIXTURE))

    record = repository.lookup("asset:arb", utc(2026, 1, 1))

    assert record is not None
    assert record.context.market_cap_tier.value == "mid"
    assert repository.by_symbol("ARB", utc(2026, 1, 1)) == record


def test_repository_fail_soft_for_unknown_or_not_yet_valid_asset() -> None:
    repository = AssetContextRepository(load_asset_context_records(FIXTURE))

    assert repository.lookup("asset:unknown", utc(2026, 6, 1)) is None
    assert repository.lookup("asset:arb", utc(2024, 12, 31)) is None


def test_repository_rejects_invalid_records_and_naive_as_of() -> None:
    with pytest.raises(ValueError, match="missing AssetContextRecord fields: source"):
        parse_asset_context_record({"context": {}, "valid_from": "2026-01-01T00:00:00Z", "fetched_at": "2026-01-01T00:00:00Z"})

    with pytest.raises(ValueError, match="must be timezone-aware"):
        parse_asset_context_record(
            {
                "context": load_asset_context_records(FIXTURE)[0].context.to_dict(),
                "valid_from": "2026-01-01T00:00:00",
                "fetched_at": "2026-01-01T00:00:00Z",
                "source": "fixture://asset-context/arb",
            }
        )

    repository = AssetContextRepository(load_asset_context_records(FIXTURE))
    with pytest.raises(ValueError, match="as_of must be timezone-aware"):
        repository.lookup("asset:arb", datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="as_of must be timezone-aware"):
        repository.by_symbol("ARB", datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="as_of must be timezone-aware"):
        repository.by_symbol("UNKNOWN", datetime(2026, 1, 1))
