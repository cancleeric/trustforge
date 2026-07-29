"""Canonical shadow-store projection for intrinsic promotion evidence."""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from trustforge.agent.shadow_contracts import (
    CONTRACT_VERSION,
    ShadowPolicy,
    ShadowReleaseIdentity,
    canonical_json,
    to_dict,
)
from trustforge.agent.shadow_evidence_store import (
    APPLICATION_ID,
    SCHEMA_VERSION as STORE_SCHEMA_VERSION,
    ShadowEvidenceStore,
    ShadowEvidenceStoreError,
)
from trustforge.asset_intrinsic_shadow import (
    normalized_source_family,
    validate_intrinsic_shadow_observation,
)
from trustforge.asset_intrinsic import (
    INTRINSIC_DIMENSION_NAMES,
    INTRINSIC_FACT_STATUSES,
)

DATASET_SCHEMA_VERSION = "trustforge.intrinsic-promotion-dataset/v1"
_DATASET_DOMAIN = b"trustforge.intrinsic-promotion-dataset.v1\x00"
class IntrinsicPromotionDatasetError(RuntimeError):
    """Persisted observations cannot form trustworthy promotion evidence."""


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise IntrinsicPromotionDatasetError(f"{label} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IntrinsicPromotionDatasetError(f"{label} is malformed") from exc
    if parsed.tzinfo is None:
        raise IntrinsicPromotionDatasetError(f"{label} lacks timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _families(payload: Mapping[str, Any]) -> tuple[str, ...]:
    dimensions = payload.get("dimensions")
    if not isinstance(dimensions, list) or not dimensions:
        raise IntrinsicPromotionDatasetError(
            "intrinsic observation dimensions are malformed"
        )
    families: set[str] = set()
    names: set[str] = set()
    for dimension in dimensions:
        if not isinstance(dimension, Mapping):
            raise IntrinsicPromotionDatasetError(
                "intrinsic observation dimension is malformed"
            )
        name, status = dimension.get("name"), dimension.get("status")
        if not isinstance(name, str) or not name or name in names:
            raise IntrinsicPromotionDatasetError(
                "intrinsic observation dimension identity is malformed"
            )
        names.add(name)
        if status == "conflicted":
            raise IntrinsicPromotionDatasetError(
                "conflicted intrinsic observation fails promotion dataset closed"
            )
        if status not in INTRINSIC_FACT_STATUSES:
            raise IntrinsicPromotionDatasetError(
                "intrinsic observation dimension status is malformed"
            )
        provenance = dimension.get("provenance")
        if provenance is None:
            continue
        if not isinstance(provenance, Mapping):
            raise IntrinsicPromotionDatasetError(
                "intrinsic observation provenance is malformed"
            )
        urls = provenance.get("source_urls", [])
        if not isinstance(urls, list):
            raise IntrinsicPromotionDatasetError(
                "intrinsic observation source URLs are malformed"
            )
        try:
            normalized = tuple(normalized_source_family(url) for url in urls)
        except ValueError as exc:
            raise IntrinsicPromotionDatasetError(
                "intrinsic observation source family is invalid"
            ) from exc
        if status == "known":
            families.update(normalized)
    if names != set(INTRINSIC_DIMENSION_NAMES):
        raise IntrinsicPromotionDatasetError(
            "intrinsic observation dimension set is malformed"
        )
    return tuple(sorted(families))


def _validate_intrinsic(
    payload: Any,
    *,
    observed_at: datetime,
    pit_time: datetime,
    cutoff: datetime,
) -> tuple[dict[str, Any], tuple[str, ...], datetime]:
    if not isinstance(payload, Mapping):
        raise IntrinsicPromotionDatasetError(
            "persisted observation lacks intrinsic shadow evidence"
        )
    value = dict(payload)
    try:
        validate_intrinsic_shadow_observation(value)
    except (TypeError, ValueError) as exc:
        raise IntrinsicPromotionDatasetError(
            "intrinsic shadow observation reconstruction failed"
        ) from exc
    as_of = _timestamp(value.get("as_of"), "intrinsic as_of")
    if observed_at > cutoff or pit_time > cutoff or as_of > cutoff:
        raise IntrinsicPromotionDatasetError(
            "future intrinsic observation fails promotion dataset closed"
        )
    if as_of > pit_time or pit_time > observed_at:
        raise IntrinsicPromotionDatasetError(
            "intrinsic observation event-time ordering is invalid"
        )
    families = _families(value)
    return value, families, as_of


def build_promotion_evidence_dataset(
    store: ShadowEvidenceStore,
    identity: ShadowReleaseIdentity,
    policy: ShadowPolicy,
    *,
    pit_cutoff: str,
    stale_after_days: int,
    limit: int = 10_000,
) -> dict[str, Any]:
    """Build a deterministic dataset from persisted canonical observations."""
    if isinstance(stale_after_days, bool) or not isinstance(stale_after_days, int):
        raise IntrinsicPromotionDatasetError("stale_after_days must be an integer")
    if stale_after_days <= 0:
        raise IntrinsicPromotionDatasetError("stale_after_days must be positive")
    cutoff = _timestamp(pit_cutoff, "PIT cutoff")
    stale_before = cutoff - timedelta(days=stale_after_days)
    try:
        persisted = store.read_canonical_observations(
            identity, policy, pit_cutoff=_iso(cutoff), limit=limit
        )
    except ShadowEvidenceStoreError as exc:
        raise IntrinsicPromotionDatasetError(
            "canonical shadow observation store failed closed"
        ) from exc

    deduplicated: dict[str, dict[str, Any]] = {}
    excluded_after_cutoff = 0
    for item in persisted:
        observation = item.observation
        recorded_at = _timestamp(item.recorded_at, "recorded_at")
        completion_recorded_at = _timestamp(
            item.completion_recorded_at, "completion_recorded_at"
        )
        observed_at = _timestamp(observation.observed_at, "observed_at")
        if not math.isfinite(observation.canonical_input.pit_epoch):
            raise IntrinsicPromotionDatasetError("observation PIT epoch is nonfinite")
        pit_time = datetime.fromtimestamp(
            observation.canonical_input.pit_epoch, tz=timezone.utc
        )
        if observed_at > cutoff or pit_time > cutoff:
            raise IntrinsicPromotionDatasetError(
                "future observation fails promotion dataset closed"
            )
        if recorded_at > cutoff or completion_recorded_at > cutoff:
            excluded_after_cutoff += 1
            continue
        if observed_at < stale_before:
            raise IntrinsicPromotionDatasetError(
                "stale observation fails promotion dataset closed"
            )
        intrinsic, families, intrinsic_as_of = _validate_intrinsic(
            observation.intrinsic_shadow,
            observed_at=observed_at,
            pit_time=pit_time,
            cutoff=cutoff,
        )
        if intrinsic_as_of < stale_before:
            raise IntrinsicPromotionDatasetError(
                "stale intrinsic evidence fails promotion dataset closed"
            )
        if intrinsic["asset_id"] != observation.canonical_input.coin:
            raise IntrinsicPromotionDatasetError(
                "intrinsic asset identity conflicts with canonical input"
            )
        projected = {
            "event_id": item.event_id,
            "input_digest": observation.input_digest,
            "observed_at": _iso(observed_at),
            "recorded_at": _iso(recorded_at),
            "asset_id": intrinsic["asset_id"],
            "source_families": list(families),
            "intrinsic_shadow": intrinsic,
        }
        key = observation.input_digest
        prior = deduplicated.get(key)
        if prior is not None:
            comparable = {
                name: value
                for name, value in projected.items()
                if name not in {"event_id", "recorded_at"}
            }
            prior_comparable = {
                name: value
                for name, value in prior.items()
                if name not in {"event_id", "recorded_at"}
            }
            if comparable != prior_comparable:
                raise IntrinsicPromotionDatasetError(
                    "conflicting retry observations fail promotion dataset closed"
                )
            if (projected["recorded_at"], projected["event_id"]) < (
                prior["recorded_at"],
                prior["event_id"],
            ):
                deduplicated[key] = projected
            continue
        deduplicated[key] = projected

    observations = sorted(
        deduplicated.values(),
        key=lambda value: (
            value["observed_at"],
            value["asset_id"],
            value["input_digest"],
            value["event_id"],
        ),
    )
    if not observations:
        raise IntrinsicPromotionDatasetError(
            "promotion dataset has no PIT-visible observations"
        )
    coverage_by_day: dict[str, dict[str, set[str] | int]] = {}
    for observation in observations:
        day = observation["observed_at"][:10]
        bucket = coverage_by_day.setdefault(
            day, {"observation_count": 0, "assets": set(), "source_families": set()}
        )
        bucket["observation_count"] = int(bucket["observation_count"]) + 1
        assert isinstance(bucket["assets"], set)
        assert isinstance(bucket["source_families"], set)
        bucket["assets"].add(observation["asset_id"])
        bucket["source_families"].update(observation["source_families"])
    day_rows = [
        {
            "day": day,
            "observation_count": bucket["observation_count"],
            "assets": sorted(bucket["assets"]),
            "source_families": sorted(bucket["source_families"]),
        }
        for day, bucket in sorted(coverage_by_day.items())
    ]
    assets = sorted({item["asset_id"] for item in observations})
    families = sorted(
        {family for item in observations for family in item["source_families"]}
    )
    body = {
        "schema_version": DATASET_SCHEMA_VERSION,
        "pit_cutoff": _iso(cutoff),
        "stale_after_days": stale_after_days,
        "provenance": {
            "store_schema_version": STORE_SCHEMA_VERSION,
            "store_application_id": APPLICATION_ID,
            "shadow_contract_version": CONTRACT_VERSION,
            "release_identity": to_dict(identity),
            "policy_digest": identity.policy_digest,
            "ordered_observation_event_ids": [
                item["event_id"] for item in observations
            ],
            "excluded_recorded_after_cutoff": excluded_after_cutoff,
        },
        "counts": {
            "observations": len(observations),
            "assets": len(assets),
            "days": len(day_rows),
            "source_families": len(families),
        },
        "assets": assets,
        "source_families": families,
        "coverage_by_day": day_rows,
        "observations": observations,
    }
    return {
        **body,
        "dataset_digest": "sha256:"
        + hashlib.sha256(_DATASET_DOMAIN + canonical_json(body)).hexdigest(),
    }


def promotion_observations(dataset: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Project a verified dataset into the existing pure promotion gate shape."""
    value = dict(dataset)
    digest = value.pop("dataset_digest", None)
    expected = "sha256:" + hashlib.sha256(
        _DATASET_DOMAIN + canonical_json(value)
    ).hexdigest()
    if (
        dataset.get("schema_version") != DATASET_SCHEMA_VERSION
        or digest != expected
        or not isinstance(dataset.get("observations"), list)
    ):
        raise IntrinsicPromotionDatasetError("promotion dataset digest is invalid")
    result: list[dict[str, Any]] = []
    for row in dataset["observations"]:
        if not isinstance(row, Mapping) or not isinstance(
            row.get("intrinsic_shadow"), Mapping
        ):
            raise IntrinsicPromotionDatasetError("promotion dataset row is malformed")
        projected = dict(row["intrinsic_shadow"])
        projected["observed_at"] = row.get("observed_at")
        result.append(projected)
    return result
