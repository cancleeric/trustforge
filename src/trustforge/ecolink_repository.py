"""EcoLink fixture repository and impact-path assembly.

Impact paths are only ever assembled from an ``UpgradeEvent`` plus a
``DependencyEdge`` that both carry an allowlisted official source URL
(enforced both by the underlying dataclasses and, explicitly and
redundantly, by ``_ensure_allowlisted_host`` in this module). Paths are
never inferred from correlation alone, and low-confidence edges are
dropped rather than surfaced as if they were settled fact -- callers
should treat an empty result as "insufficient_data", not "no impact".

IMPORTANT (honesty guard): an ``ImpactPath`` is a *correlation* between an
official upgrade event and a dependency edge -- it is NOT a proven causal
claim. Nothing in this module (or its confidence score) establishes that
the upgrade event actually caused an effect on the target asset. Any UI or
API text derived from ``ImpactPath`` MUST use hedged language such as
"可能相關" / "may be related" and MUST NOT use causal language such as
"導致" / "因此" / "caused" / "therefore".

All fixture records loaded through this module must be tagged
``illustrative: true`` at the top level (validated strictly -- only the
Python boolean ``True`` is accepted, not truthy strings). This flag is
verified at load time and then stripped before the remaining payload is
handed to the underlying contract parsers, since ``DependencyEdge``'s and
``UpgradeEvent``'s dict parsers reject unknown keys.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from trustforge.ecolink import (
    OFFICIAL_ECOLINK_HOSTS,
    DependencyEdge,
    ImpactDirection,
    UpgradeEvent,
    dependency_edge_from_dict,
)
from trustforge.ecolink_connector import parse_upgrade_events_fixture

DEFAULT_MIN_CONFIDENCE = 0.4

# Re-exported for callers/tests; sourced from the same allowlist the
# underlying contract dataclasses already enforce (trustforge.ecolink).
ECOLINK_OFFICIAL_HOSTS = OFFICIAL_ECOLINK_HOSTS


@dataclass(frozen=True)
class ImpactPath:
    """A *correlation* between an official upgrade event and a dependency
    edge -- NOT a proven causal claim. ``direction``/``confidence`` describe
    the strength of the observed relationship only; consumers must render
    this as "可能相關" (may be related), never as "導致"/"因此" (caused/therefore).
    """

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
        *,
        illustrative: bool = True,
    ) -> None:
        self._dependencies = tuple(dependencies)
        self._upgrade_events = tuple(upgrade_events)
        # Every `DependencyEdge`/`UpgradeEvent` accepted by the loaders in
        # this module must have carried `illustrative: true` at parse time
        # (see `_require_illustrative_and_strip`); the flag itself is
        # stripped before the contract dataclasses parse the remaining
        # payload (they reject unknown keys), so we re-attach it here at the
        # repository level rather than lose it, letting API responses
        # disclose that the data is illustrative/fixture-sourced.
        self.illustrative = illustrative

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
    edges: list[DependencyEdge] = []
    for item in payload:
        contract_payload = _require_illustrative_and_strip(item, "DependencyEdge")
        _ensure_allowlisted_host(
            contract_payload.get("official_source_url"), "DependencyEdge.official_source_url"
        )
        edges.append(dependency_edge_from_dict(contract_payload))
    return tuple(edges)


def load_dependency_edges_fixture(path: Path) -> tuple[DependencyEdge, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Dependency edges fixture must be a list")
    return parse_dependency_edges_fixture(raw)


def load_upgrade_events_fixture(path: Path, *, fetched_at: datetime) -> tuple[UpgradeEvent, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Upgrade events fixture must be a list")
    contract_payloads = [_require_illustrative_and_strip(item, "UpgradeEvent") for item in raw]
    return parse_upgrade_events_fixture(contract_payloads, fetched_at=fetched_at)


def _require_illustrative_and_strip(payload: dict[str, Any], contract_name: str) -> dict[str, Any]:
    if "illustrative" not in payload:
        raise ValueError(f"missing {contract_name}.illustrative field")
    value = payload["illustrative"]
    if value is not True:
        raise ValueError(
            f"{contract_name}.illustrative must be the boolean true, got {value!r}"
        )
    return {key: val for key, val in payload.items() if key != "illustrative"}


def _ensure_allowlisted_host(url: object, field_name: str) -> None:
    if not isinstance(url, str) or not url.strip():
        raise ValueError(f"{field_name} must be non-empty string")
    host = urlparse(url).hostname
    if host not in ECOLINK_OFFICIAL_HOSTS:
        raise ValueError(f"{field_name} host is not allowlisted official source: {host}")


def load_ecolink_fixtures(
    edges_path: Path, events_path: Path, *, fetched_at: datetime
) -> EcoLinkRepository:
    edges = load_dependency_edges_fixture(edges_path)
    events = load_upgrade_events_fixture(events_path, fetched_at=fetched_at)
    return EcoLinkRepository(dependencies=edges, upgrade_events=events)
