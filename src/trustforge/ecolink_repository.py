"""EcoLink fixture repository and impact-path assembly.

Impact paths are only ever assembled from an ``UpgradeEvent`` plus a
``DependencyEdge`` that both carry an allowlisted official source URL
(enforced by the underlying dataclasses). Paths are never inferred from
correlation alone, and low-confidence edges are dropped rather than
surfaced as if they were settled fact -- callers should treat an empty
result as "insufficient_data", not "no impact".
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from trustforge.ecolink import (
    DependencyEdge,
    ImpactDirection,
    UpgradeEvent,
    dependency_edge_from_dict,
)
from trustforge.ecolink_connector import parse_upgrade_events_fixture

DEFAULT_MIN_CONFIDENCE = 0.4


@dataclass(frozen=True)
class ImpactPath:
    event_id: str
    path: tuple[str, ...]
    direction: ImpactDirection
    confidence: float
    official_source_url: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "path": list(self.path),
            "direction": self.direction.value,
            "confidence": self.confidence,
            "official_source_url": self.official_source_url,
        }


class EcoLinkRepository:
    def __init__(
        self,
        dependencies: Iterable[DependencyEdge] = (),
        upgrade_events: Iterable[UpgradeEvent] = (),
    ) -> None:
        self._dependencies = tuple(dependencies)
        self._upgrade_events = tuple(upgrade_events)

    def dependencies_for(self, asset_id: str) -> tuple[DependencyEdge, ...]:
        return tuple(
            edge
            for edge in self._dependencies
            if edge.source_asset_id == asset_id or edge.target_asset_id == asset_id
        )

    def upgrade_events_for(self, asset_id: str) -> tuple[UpgradeEvent, ...]:
        return tuple(event for event in self._upgrade_events if event.asset_id == asset_id)

    def impact_paths_for(
        self, asset_id: str, *, min_confidence: float = DEFAULT_MIN_CONFIDENCE
    ) -> tuple[ImpactPath, ...]:
        paths: list[ImpactPath] = []
        for event in self.upgrade_events_for(asset_id):
            for impacted_asset_id in event.impacted_asset_ids:
                if impacted_asset_id == asset_id:
                    continue
                edge = self._best_edge(asset_id, impacted_asset_id, min_confidence)
                if edge is None:
                    continue
                paths.append(
                    ImpactPath(
                        event_id=event.event_id,
                        path=(asset_id, impacted_asset_id),
                        direction=event.impact_direction,
                        confidence=edge.confidence,
                        official_source_url=event.official_source_url,
                    )
                )
        return tuple(paths)

    def _best_edge(
        self, asset_id: str, impacted_asset_id: str, min_confidence: float
    ) -> DependencyEdge | None:
        candidates = [
            edge
            for edge in self._dependencies
            if {edge.source_asset_id, edge.target_asset_id} == {asset_id, impacted_asset_id}
            and edge.confidence >= min_confidence
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda edge: edge.confidence)


def parse_dependency_edges_fixture(payload: list[dict[str, Any]]) -> tuple[DependencyEdge, ...]:
    return tuple(dependency_edge_from_dict(item) for item in payload)


def load_dependency_edges_fixture(path: Path) -> tuple[DependencyEdge, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Dependency edges fixture must be a list")
    return parse_dependency_edges_fixture(raw)


def load_upgrade_events_fixture(path: Path, *, fetched_at: datetime) -> tuple[UpgradeEvent, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Upgrade events fixture must be a list")
    return parse_upgrade_events_fixture(raw, fetched_at=fetched_at)


def load_ecolink_fixtures(
    edges_path: Path, events_path: Path, *, fetched_at: datetime
) -> EcoLinkRepository:
    edges = load_dependency_edges_fixture(edges_path)
    events = load_upgrade_events_fixture(events_path, fetched_at=fetched_at)
    return EcoLinkRepository(dependencies=edges, upgrade_events=events)
