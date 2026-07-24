from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from trustforge.ecolink_repository import (
    EcoLinkRepository,
    load_dependency_edges_fixture,
    load_ecolink_fixtures,
    load_upgrade_events_fixture,
    parse_dependency_edges_fixture,
)

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
EDGES_PATH = DATA_DIR / "ecolink_dependency_edges.json"
EVENTS_PATH = DATA_DIR / "ecolink_upgrade_events.json"


def utc(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def repository() -> EcoLinkRepository:
    return load_ecolink_fixtures(EDGES_PATH, EVENTS_PATH, fetched_at=utc(2026, 1, 1))


def test_fixtures_load_and_validate_against_contract(repository: EcoLinkRepository) -> None:
    assert len(repository.dependencies_for("asset:arb")) == 2
    assert len(repository.dependencies_for("asset:op")) == 1
    assert len(repository.upgrade_events_for("asset:arb")) == 2
    assert len(repository.upgrade_events_for("asset:op")) == 1


def test_dependencies_for_matches_source_or_target(repository: EcoLinkRepository) -> None:
    edges = repository.dependencies_for("asset:eth")
    assert len(edges) == 3
    assert all(edge.target_asset_id == "asset:eth" for edge in edges)


def test_impact_paths_found_for_high_confidence_edge(repository: EcoLinkRepository) -> None:
    paths = repository.impact_paths_for("asset:arb")
    stylus_paths = [path for path in paths if path.event_id == "upgrade:arb:stylus"]
    assert len(stylus_paths) == 1
    path = stylus_paths[0]
    assert path.path == ("asset:arb", "asset:eth")
    assert path.confidence == 0.85
    assert path.official_source_url == "https://arbitrum.foundation/upgrade/stylus"


def test_impact_paths_excludes_no_known_dependency(repository: EcoLinkRepository) -> None:
    paths = repository.impact_paths_for("asset:arb")
    assert all(path.event_id != "upgrade:arb:no-known-path" for path in paths)


def test_impact_paths_low_confidence_edge_is_insufficient_data(repository: EcoLinkRepository) -> None:
    paths = repository.impact_paths_for("asset:op")
    assert paths == ()


def test_impact_paths_empty_for_unknown_asset(repository: EcoLinkRepository) -> None:
    assert repository.impact_paths_for("asset:unknown") == ()


def test_load_dependency_edges_fixture_rejects_non_allowlisted_host(tmp_path: Path) -> None:
    bad_path = tmp_path / "bad_edges.json"
    bad_path.write_text(
        """
        [{
          "schema_version": "1.0.0",
          "source_asset_id": "asset:arb",
          "target_asset_id": "asset:eth",
          "kind": "settlement",
          "valid_from": "2026-01-01T00:00:00+00:00",
          "valid_until": null,
          "confidence": 0.9,
          "official_source_url": "https://evil.example/dependency",
          "observed_at": "2026-01-01T00:00:00+00:00"
        }]
        """,
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="not.*allowlist"):
        load_dependency_edges_fixture(bad_path)


def test_parse_dependency_edges_fixture_matches_loader() -> None:
    import json

    raw = json.loads(EDGES_PATH.read_text(encoding="utf-8"))
    edges = parse_dependency_edges_fixture(raw)
    assert len(edges) == 3


def test_load_upgrade_events_fixture_deduplicates_and_parses(repository: EcoLinkRepository) -> None:
    events = load_upgrade_events_fixture(EVENTS_PATH, fetched_at=utc(2026, 1, 1))
    assert len(events) == 3
    assert {event.event_id for event in events} == {
        "upgrade:arb:stylus",
        "upgrade:op:bedrock",
        "upgrade:arb:no-known-path",
    }
