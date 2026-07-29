"""Typed, symbol-blind control/governance plane PIT replay.

This module produces only the existing ``control_dispersion`` and
``governance_capture_resistance`` facts.  It neither scores assets nor changes
the signed promotion gate.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Iterable
from urllib.parse import urlsplit

from trustforge.asset_intrinsic import IntrinsicDimensionName

DEFAULT_FRESHNESS = timedelta(days=30)
CONFLICT_SPREAD = 0.20
_TOKEN = re.compile(r"[a-z0-9][a-z0-9._:-]{0,255}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


class ControlPlane(StrEnum):
    VALIDATOR = "validator"
    MINER_POOL = "miner_pool"
    NODE_CLIENT = "node_client"
    GOVERNANCE = "governance"


class ConsensusKind(StrEnum):
    PROOF_OF_WORK = "proof_of_work"
    PROOF_OF_STAKE = "proof_of_stake"
    HYBRID = "hybrid"


class PlaneStatus(StrEnum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    CONFLICT = "conflict"


class EvidenceKind(StrEnum):
    ENTITY_MEASUREMENT = "entity_measurement"
    CLIENT_TELEMETRY = "client_telemetry"
    GOVERNANCE_RECORD = "governance_record"


_PLANE_EVIDENCE = {
    ControlPlane.VALIDATOR: EvidenceKind.ENTITY_MEASUREMENT,
    ControlPlane.MINER_POOL: EvidenceKind.ENTITY_MEASUREMENT,
    ControlPlane.NODE_CLIENT: EvidenceKind.CLIENT_TELEMETRY,
    ControlPlane.GOVERNANCE: EvidenceKind.GOVERNANCE_RECORD,
}


def _aware(value: datetime, name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _token(value: str, name: str) -> str:
    if not isinstance(value, str) or _TOKEN.fullmatch(value) is None:
        raise ValueError(f"{name} is invalid")
    return value


def _source_url(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("source_url is invalid")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.fragment
    ):
        raise ValueError("source_url must be a canonical HTTPS URL")
    host = parsed.hostname.rstrip(".").lower()
    if not host or ":" in host or re.fullmatch(r"[a-z0-9.-]+", host) is None:
        raise ValueError("source_url hostname is invalid")
    return value


@dataclass(frozen=True)
class PlaneObservation:
    observation_id: str
    plane: ControlPlane
    source_id: str
    source_family: str
    source_url: str
    control_entity_id: str
    revision: int
    evidence_kind: EvidenceKind
    evidence_digest: str
    value: float
    observed_at: datetime
    fetched_at: datetime
    valid_until: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.plane, ControlPlane):
            raise ValueError("plane must be a ControlPlane")
        if not isinstance(self.evidence_kind, EvidenceKind):
            raise ValueError("evidence_kind must be an EvidenceKind")
        for name in (
            "observation_id",
            "source_id",
            "source_family",
            "control_entity_id",
        ):
            _token(getattr(self, name), name)
        _source_url(self.source_url)
        if (
            not isinstance(self.revision, int)
            or isinstance(self.revision, bool)
            or self.revision < 1
        ):
            raise ValueError("revision must be a positive integer")
        if (
            not isinstance(self.evidence_digest, str)
            or _DIGEST.fullmatch(self.evidence_digest) is None
        ):
            raise ValueError("evidence_digest is invalid")
        if self.evidence_kind is not _PLANE_EVIDENCE[self.plane]:
            raise ValueError("evidence kind cannot prove this control plane")
        if type(self.value) not in {int, float}:
            raise ValueError("observation value must be numeric")
        numeric = float(self.value)
        if not math.isfinite(numeric) or not 0.0 <= numeric <= 1.0:
            raise ValueError("observation value must be finite within [0, 1]")
        observed = _aware(self.observed_at, "observed_at")
        fetched = _aware(self.fetched_at, "fetched_at")
        if fetched < observed:
            raise ValueError("fetched_at cannot precede observed_at")
        if self.valid_until is not None:
            valid_until = _aware(self.valid_until, "valid_until")
            if valid_until <= observed:
                raise ValueError("valid_until must follow observed_at")


@dataclass(frozen=True)
class SourceWithdrawal:
    withdrawal_id: str
    observation_id: str
    source_id: str
    effective_at: datetime
    fetched_at: datetime
    reason_code: str

    def __post_init__(self) -> None:
        for name in (
            "withdrawal_id",
            "observation_id",
            "source_id",
            "reason_code",
        ):
            _token(getattr(self, name), name)
        effective = _aware(self.effective_at, "effective_at")
        fetched = _aware(self.fetched_at, "fetched_at")
        if fetched < effective:
            raise ValueError("withdrawal fetched_at cannot precede effective_at")


@dataclass(frozen=True)
class PlaneResult:
    plane: ControlPlane
    status: PlaneStatus
    value: float | None
    reason_code: str
    contribution_ids: tuple[str, ...]
    source_families: tuple[str, ...]
    control_entities: tuple[str, ...]


@dataclass(frozen=True)
class DimensionResult:
    name: IntrinsicDimensionName
    status: PlaneStatus
    value: float | None
    reason_code: str
    planes: tuple[ControlPlane, ...]
    source_families: tuple[str, ...]


@dataclass(frozen=True)
class ControlGovernanceReplay:
    pit_cutoff: datetime
    planes: tuple[PlaneResult, ...]
    dimensions: tuple[DimensionResult, ...]

    def plane(self, name: ControlPlane) -> PlaneResult:
        return next(result for result in self.planes if result.plane is name)

    def dimension(self, name: IntrinsicDimensionName) -> DimensionResult:
        return next(result for result in self.dimensions if result.name is name)


class ControlPlaneRepository:
    def __init__(
        self,
        observations: Iterable[PlaneObservation] = (),
        withdrawals: Iterable[SourceWithdrawal] = (),
    ) -> None:
        self._observations = tuple(
            sorted(observations, key=lambda item: item.observation_id)
        )
        self._withdrawals = tuple(
            sorted(withdrawals, key=lambda item: item.withdrawal_id)
        )
        observation_ids = [item.observation_id for item in self._observations]
        withdrawal_ids = [item.withdrawal_id for item in self._withdrawals]
        if len(observation_ids) != len(set(observation_ids)):
            raise ValueError("duplicate observation_id")
        if len(withdrawal_ids) != len(set(withdrawal_ids)):
            raise ValueError("duplicate withdrawal_id")
        revisions = [
            (item.plane, item.source_id, item.revision) for item in self._observations
        ]
        if len(revisions) != len(set(revisions)):
            raise ValueError("duplicate plane/source revision")
        observations_by_id = {item.observation_id: item for item in self._observations}
        for withdrawal in self._withdrawals:
            target = observations_by_id.get(withdrawal.observation_id)
            if target is None or target.source_id != withdrawal.source_id:
                raise ValueError("withdrawal does not bind its source observation")

    def replay(
        self,
        *,
        pit_cutoff: datetime,
        consensus: ConsensusKind,
        freshness: timedelta = DEFAULT_FRESHNESS,
    ) -> ControlGovernanceReplay:
        cutoff = _aware(pit_cutoff, "pit_cutoff")
        if (
            not isinstance(freshness, timedelta)
            or freshness <= timedelta(0)
            or freshness > timedelta(days=365)
        ):
            raise ValueError("freshness window is invalid")
        if not isinstance(consensus, ConsensusKind):
            raise ValueError("consensus must be a ConsensusKind")
        visible_withdrawals = {
            item.observation_id
            for item in self._withdrawals
            if _aware(item.effective_at, "effective_at") <= cutoff
            and _aware(item.fetched_at, "fetched_at") <= cutoff
        }
        pit_visible = tuple(
            item
            for item in self._observations
            if _aware(item.observed_at, "observed_at") <= cutoff
            and _aware(item.fetched_at, "fetched_at") <= cutoff
        )
        latest_by_source: dict[tuple[ControlPlane, str], PlaneObservation] = {}
        for item in pit_visible:
            key = (item.plane, item.source_id)
            prior = latest_by_source.get(key)
            if prior is None or item.revision > prior.revision:
                latest_by_source[key] = item
        latest = tuple(
            sorted(
                latest_by_source.values(),
                key=lambda item: (item.plane.value, item.source_id),
            )
        )
        withdrawn_planes = {
            item.plane for item in latest if item.observation_id in visible_withdrawals
        }
        latest_planes = {item.plane for item in latest}
        plane_results = tuple(
            self._plane_result(
                plane,
                latest,
                visible_withdrawals=visible_withdrawals,
                withdrawn_planes=withdrawn_planes,
                latest_planes=latest_planes,
                cutoff=cutoff,
                freshness=freshness,
            )
            for plane in ControlPlane
        )
        by_plane = {item.plane: item for item in plane_results}
        required_control = {
            ConsensusKind.PROOF_OF_WORK: (
                ControlPlane.MINER_POOL,
                ControlPlane.NODE_CLIENT,
            ),
            ConsensusKind.PROOF_OF_STAKE: (
                ControlPlane.VALIDATOR,
                ControlPlane.NODE_CLIENT,
            ),
            ConsensusKind.HYBRID: (
                ControlPlane.VALIDATOR,
                ControlPlane.MINER_POOL,
                ControlPlane.NODE_CLIENT,
            ),
        }[consensus]
        control = self._dimension_result(
            IntrinsicDimensionName.CONTROL_DISPERSION,
            tuple(by_plane[name] for name in required_control),
        )
        governance = self._dimension_result(
            IntrinsicDimensionName.GOVERNANCE_CAPTURE_RESISTANCE,
            (by_plane[ControlPlane.GOVERNANCE],),
        )
        return ControlGovernanceReplay(
            pit_cutoff=cutoff,
            planes=plane_results,
            dimensions=(control, governance),
        )

    @staticmethod
    def _plane_result(
        plane: ControlPlane,
        latest: tuple[PlaneObservation, ...],
        *,
        visible_withdrawals: set[str],
        withdrawn_planes: set[ControlPlane],
        latest_planes: set[ControlPlane],
        cutoff: datetime,
        freshness: timedelta,
    ) -> PlaneResult:
        all_plane = tuple(item for item in latest if item.plane is plane)
        fresh = tuple(
            item
            for item in all_plane
            if item.observation_id not in visible_withdrawals
            and (item.valid_until is None or cutoff < item.valid_until)
            and cutoff - item.observed_at <= freshness
        )
        if not fresh:
            withdrawn = plane in withdrawn_planes
            had_plane = plane in latest_planes
            reason = (
                "source_withdrawn"
                if withdrawn
                else ("stale" if had_plane else "missing")
            )
            return PlaneResult(plane, PlaneStatus.UNKNOWN, None, reason, (), (), ())
        by_family: dict[str, list[PlaneObservation]] = {}
        for item in fresh:
            by_family.setdefault(item.source_family, []).append(item)
        family_values: dict[str, float] = {}
        contribution_ids: list[str] = []
        entities = tuple(sorted({item.control_entity_id for item in fresh}))
        for family, items in sorted(by_family.items()):
            unique_values = tuple(sorted({float(item.value) for item in items}))
            family_entities = {item.control_entity_id for item in items}
            contribution_ids.append(f"family:{family}")
            if len(family_entities) != 1:
                return PlaneResult(
                    plane,
                    PlaneStatus.CONFLICT,
                    None,
                    "conflicting_family_attribution",
                    tuple(contribution_ids),
                    tuple(sorted(by_family)),
                    entities,
                )
            if len(unique_values) != 1:
                return PlaneResult(
                    plane,
                    PlaneStatus.CONFLICT,
                    None,
                    "conflicting_family_aliases",
                    tuple(contribution_ids),
                    tuple(sorted(by_family)),
                    entities,
                )
            family_values[family] = unique_values[0]
        values = tuple(family_values[family] for family in sorted(family_values))
        families = tuple(sorted(family_values))
        if max(values) - min(values) > CONFLICT_SPREAD:
            return PlaneResult(
                plane,
                PlaneStatus.CONFLICT,
                None,
                "conflicting_observations",
                tuple(contribution_ids),
                families,
                entities,
            )
        return PlaneResult(
            plane,
            PlaneStatus.KNOWN,
            round(math.fsum(values) / len(values), 8),
            "known",
            tuple(contribution_ids),
            families,
            entities,
        )

    @staticmethod
    def _dimension_result(
        name: IntrinsicDimensionName, planes: tuple[PlaneResult, ...]
    ) -> DimensionResult:
        families = tuple(
            sorted({family for plane in planes for family in plane.source_families})
        )
        if any(plane.status is PlaneStatus.CONFLICT for plane in planes):
            return DimensionResult(
                name,
                PlaneStatus.CONFLICT,
                None,
                "plane_conflict",
                tuple(plane.plane for plane in planes),
                families,
            )
        if any(plane.status is not PlaneStatus.KNOWN for plane in planes):
            return DimensionResult(
                name,
                PlaneStatus.UNKNOWN,
                None,
                "plane_missing_withdrawn_or_stale",
                tuple(plane.plane for plane in planes),
                families,
            )
        if len(families) < 2:
            return DimensionResult(
                name,
                PlaneStatus.UNKNOWN,
                None,
                "insufficient_independent_source_families",
                tuple(plane.plane for plane in planes),
                families,
            )
        values = tuple(plane.value for plane in planes)
        assert all(value is not None for value in values)
        return DimensionResult(
            name,
            PlaneStatus.KNOWN,
            round(
                math.fsum(value for value in values if value is not None) / len(values),
                8,
            ),
            "known",
            tuple(plane.plane for plane in planes),
            families,
        )
