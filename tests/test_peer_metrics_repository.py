from __future__ import annotations

from pathlib import Path

import pytest

from trustforge.peer_metrics import snapshots_comparable
from trustforge.peer_metrics_repository import (
    PeerMetricsRecord,
    PeerMetricsRepository,
    load_peer_metrics_fixture,
    parse_peer_metrics_record,
)

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "data" / "peer_metrics_snapshots.json"


@pytest.fixture(scope="module")
def records() -> tuple[PeerMetricsRecord, ...]:
    return load_peer_metrics_fixture(FIXTURE_PATH)


@pytest.fixture(scope="module")
def repository(records: tuple[PeerMetricsRecord, ...]) -> PeerMetricsRepository:
    return PeerMetricsRepository(records)


def test_fixture_loads_and_validates_against_contract(records: tuple[PeerMetricsRecord, ...]) -> None:
    assert len(records) == 6
    assert all(record.illustrative for record in records)
    assert all(
        value.source.startswith("fixture://")
        for record in records
        for value in (record.snapshot.observed_tps, record.snapshot.tvl, record.snapshot.gas_fee)
    )


def test_by_asset_id_hit(repository: PeerMetricsRepository) -> None:
    snapshot = repository.by_asset_id("asset:arb")
    assert snapshot is not None
    assert snapshot.asset_id == "asset:arb"


def test_by_asset_id_miss(repository: PeerMetricsRepository) -> None:
    assert repository.by_asset_id("asset:unknown") is None


def test_peer_group_returns_other_members(repository: PeerMetricsRepository) -> None:
    peers = repository.peer_group("asset:arb")
    assert set(peers) == {"asset:op", "asset:matic"}
    assert "asset:arb" not in peers


def test_peer_group_l1_members(repository: PeerMetricsRepository) -> None:
    peers = repository.peer_group("asset:eth")
    assert set(peers) == {"asset:sol", "asset:bnb"}


def test_peer_group_unknown_asset_returns_empty(repository: PeerMetricsRepository) -> None:
    assert repository.peer_group("asset:unknown") == ()


def test_matic_missing_tps_is_not_comparable(repository: PeerMetricsRepository) -> None:
    arb = repository.by_asset_id("asset:arb")
    matic = repository.by_asset_id("asset:matic")
    assert arb is not None and matic is not None
    comparable, reason = snapshots_comparable(arb, matic)
    assert comparable is False
    assert reason == "observed_tps missing"


def test_arb_and_op_are_comparable(repository: PeerMetricsRepository) -> None:
    arb = repository.by_asset_id("asset:arb")
    op = repository.by_asset_id("asset:op")
    assert arb is not None and op is not None
    comparable, reason = snapshots_comparable(arb, op)
    assert comparable is True
    assert reason is None


def test_record_rejects_non_illustrative_fixture() -> None:
    payload = {
        "illustrative": False,
        "peer_group": ["asset:arb"],
        "snapshot": {
            "asset_id": "asset:arb",
            "observed_tps": {"value": 1.0, "unit": "count/s", "method": "observed", "source": "fixture://x"},
            "tvl": {"value": 1.0, "unit": "usd", "method": "observed", "source": "fixture://x"},
            "gas_fee": {"value": 1.0, "unit": "usd/transfer", "method": "observed", "source": "fixture://x"},
            "activity_breakdown": {
                "swap": {"value": 1.0, "unit": "count/s", "method": "observed", "source": "fixture://x"}
            },
            "window_start": "2026-01-01T00:00:00Z",
            "window_end": "2026-01-08T00:00:00Z",
            "observed_at": "2026-01-08T00:00:00Z",
        },
    }
    with pytest.raises(ValueError, match="illustrative"):
        parse_peer_metrics_record(payload)


def test_record_rejects_peer_group_without_self() -> None:
    payload = {
        "illustrative": True,
        "peer_group": ["asset:op", "asset:matic"],
        "snapshot": {
            "asset_id": "asset:arb",
            "observed_tps": {"value": 1.0, "unit": "count/s", "method": "observed", "source": "fixture://x"},
            "tvl": {"value": 1.0, "unit": "usd", "method": "observed", "source": "fixture://x"},
            "gas_fee": {"value": 1.0, "unit": "usd/transfer", "method": "observed", "source": "fixture://x"},
            "activity_breakdown": {
                "swap": {"value": 1.0, "unit": "count/s", "method": "observed", "source": "fixture://x"}
            },
            "window_start": "2026-01-01T00:00:00Z",
            "window_end": "2026-01-08T00:00:00Z",
            "observed_at": "2026-01-08T00:00:00Z",
        },
    }
    with pytest.raises(ValueError, match="peer_group must include"):
        parse_peer_metrics_record(payload)
