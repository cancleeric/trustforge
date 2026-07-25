"""Deterministic, point-in-time confidence-calibration dataset manifests."""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from .delayed_outcome_labeler import (
    FixtureAuthorityRegistry,
    validate_canonical_delayed_outcome,
)
from .learning_event_contract import LearningEvent, LearningEventError

_MAX_INPUT_EVENTS = 100_000
_MAX_INPUT_BYTES = 16 * 1024 * 1024
_MAX_FIELD_BYTES = 64 * 1024
_MAX_INPUT_NODES = 1_000_000
_MAX_NESTING_DEPTH = 64
_SUPPORTED_SCHEMA = "learning-event.v1"
_SUPPORTED_DIRECTIONS = {"bullish", "bearish"}


class CalibrationDatasetError(ValueError):
    pass


@dataclass(frozen=True)
class CalibrationDatasetPolicy:
    """Exact immutable policy that makes a dataset build reproducible."""

    dataset_as_of: str
    train_end: str
    validation_end: str
    embargo_seconds: int
    eligibility_version: str
    split_version: str
    producer_version: str
    tenant_id: str
    market_data_variant: str

    def canonical(self) -> dict[str, Any]:
        values = asdict(self)
        parsed = {
            field: _parse(values[field])
            for field in ("dataset_as_of", "train_end", "validation_end")
        }
        if not parsed["train_end"] < parsed["validation_end"] <= parsed["dataset_as_of"]:
            raise CalibrationDatasetError(
                "split boundaries must satisfy train_end < validation_end <= dataset_as_of"
            )
        if type(self.embargo_seconds) is not int or self.embargo_seconds < 0:
            raise CalibrationDatasetError("embargo_seconds must be a non-negative integer")
        for field in (
            "eligibility_version",
            "split_version",
            "producer_version",
            "tenant_id",
        ):
            _bounded_text(values[field], field, required=True)
        if self.market_data_variant not in {"as_first_known", "latest_official"}:
            raise CalibrationDatasetError(
                "market_data_variant must be selected explicitly"
            )
        for field, value in parsed.items():
            values[field] = _canonical_utc(value)
        return values


def build_confidence_calibration_dataset(
    analysis_events: Iterable[LearningEvent],
    outcome_events: Iterable[LearningEvent],
    *,
    policy: CalibrationDatasetPolicy,
    trusted_authority_registry: FixtureAuthorityRegistry,
) -> dict[str, Any]:
    policy_dict = policy.canonical()
    dataset_as_of = _parse(policy.dataset_as_of)
    analyses_input = _bounded_visible_events(
        analysis_events,
        dataset_as_of=dataset_as_of,
        source="analysis",
        trusted_tenant_id=policy.tenant_id,
    )
    outcomes_input = _bounded_visible_events(
        outcome_events,
        dataset_as_of=dataset_as_of,
        source="outcome",
        trusted_tenant_id=policy.tenant_id,
    )

    excluded: Counter[str] = Counter(
        {
            "analysis_wrong_tenant": 0,
            "analysis_unsupported_direction": 0,
            "outcome_wrong_tenant": 0,
            "outcome_wrong_variant": 0,
            "outcome_not_labeled": 0,
            "outcome_non_directional": 0,
            "outcome_after_train_label_cutoff": 0,
            "outcome_after_validation_label_cutoff": 0,
            "outcome_after_test_label_cutoff": 0,
        }
    )
    analyses: dict[str, tuple[LearningEvent, dict[str, Any]]] = {}
    trusted_source_analyses: dict[str, LearningEvent] = {}
    group_splits: dict[str, str] = {}
    group_cutoffs: dict[str, datetime] = {}
    group_keys: set[tuple[str, str]] = set()
    for event in analyses_input:
        if event.tenant_id != policy.tenant_id:
            excluded["analysis_wrong_tenant"] += 1
            continue
        if event.identity in trusted_source_analyses:
            raise CalibrationDatasetError("duplicate source analysis identity")
        trusted_source_analyses[event.identity] = event
        if event.payload.get("decision", {}).get("direction") not in _SUPPORTED_DIRECTIONS:
            excluded["analysis_unsupported_direction"] += 1
            continue
        row = _analysis_row(event)
        group_key = (event.tenant_id, event.identity)
        if group_key in group_keys:
            raise CalibrationDatasetError("duplicate analysis group key")
        group_keys.add(group_key)
        analyses[event.identity] = (event, row)
        split = _split_for(_parse(event.available_time), policy)
        group_splits[event.identity] = split
        group_cutoffs[event.identity] = _label_cutoff(split, policy)

    outcomes = _latest_labeled_outcomes(
        outcomes_input,
        policy=policy,
        source_analyses=trusted_source_analyses,
        group_splits=group_splits,
        group_cutoffs=group_cutoffs,
        trusted_authority_registry=trusted_authority_registry,
        excluded=excluded,
    )

    rows: list[dict[str, Any]] = []
    for source_identity, (_analysis_event, analysis) in analyses.items():
        split = group_splits[source_identity]
        matched = outcomes.get(source_identity, {})
        for horizon, outcome in matched.items():
            if _parse(outcome["outcome_available_time"]) <= _parse(
                analysis["analysis_available_time"]
            ):
                raise CalibrationDatasetError(
                    "outcome cannot be available before analysis availability"
                )
            rows.append({**analysis, **outcome, "split": split})

    rows.sort(
        key=lambda row: (
            row["analysis_available_time"],
            row["tenant_id"],
            row["analysis_identity"],
            row["horizon"],
        )
    )
    unique_groups = {
        (row["tenant_id"], row["analysis_identity"], row["split"]) for row in rows
    }
    split_ranges = {
        "train": {"start": None, "end_exclusive": policy_dict["train_end"]},
        "validation": {
            "start": policy_dict["train_end"],
            "end_exclusive": policy_dict["validation_end"],
        },
        "test": {
            "start": policy_dict["validation_end"],
            "end_inclusive": policy_dict["dataset_as_of"],
        },
    }
    input_roots = {
        "analysis_sha256": _sha256(
            sorted(
                (_event_anchor(event) for event in trusted_source_analyses.values()),
                key=lambda anchor: (anchor["identity"], _sha256(anchor)),
            )
        ),
        "outcome_sha256": _sha256(
            sorted(
                (
                    _event_anchor(event)
                    for event in outcomes_input
                    if event.tenant_id == policy.tenant_id
                    and event.kind == "delayed_outcome"
                    and event.payload.get("market_data_variant")
                    == policy.market_data_variant
                ),
                key=lambda anchor: (anchor["identity"], _sha256(anchor)),
            )
        ),
    }
    manifest: dict[str, Any] = {
        "kind": "confidence-calibration-dataset.v2",
        "policy": policy_dict,
        "input_roots": input_roots,
        "versions": {
            "producer": policy.producer_version,
            "eligibility": policy.eligibility_version,
            "split": policy.split_version,
            "analysis_schema": "analysis-quality.v1",
            "outcome_schema": "delayed-outcome.v1",
            "kernel_schema": _SUPPORTED_SCHEMA,
        },
        "excluded_counts": dict(sorted(excluded.items())),
        "split_ranges": split_ranges,
        "row_counts": {
            split: sum(1 for row in rows if row["split"] == split)
            for split in ("train", "validation", "test")
        },
        "group_counts": {
            split: sum(1 for group in unique_groups if group[2] == split)
            for split in ("train", "validation", "test")
        },
        "row_count": len(rows),
        "group_count": len(unique_groups),
        "rows_sha256": _sha256(rows),
        "rows": rows,
    }
    manifest["manifest_sha256"] = _sha256(manifest)
    return manifest


def _bounded_visible_events(
    events: Iterable[LearningEvent],
    *,
    dataset_as_of: datetime,
    source: str,
    trusted_tenant_id: str,
) -> list[LearningEvent]:
    """Bound input before materialization/sort/hash; post-cutoff events are invisible."""

    visible: list[LearningEvent] = []
    total_bytes = 0
    total_nodes = 0
    scanned = 0
    for event in events:
        if not isinstance(event, LearningEvent):
            raise CalibrationDatasetError(f"{source} input must contain LearningEvent")
        if not isinstance(event.tenant_id, str):
            raise CalibrationDatasetError(f"{source} tenant metadata is invalid")
        if event.tenant_id != trusted_tenant_id:
            continue
        scanned += 1
        if scanned > _MAX_INPUT_EVENTS:
            raise CalibrationDatasetError(f"{source} input exceeds event count limit")
        event_bytes, event_nodes = _preflight_event(
            event,
            source=source,
            byte_budget=_MAX_INPUT_BYTES - total_bytes,
            node_budget=_MAX_INPUT_NODES - total_nodes,
        )
        total_bytes += event_bytes
        total_nodes += event_nodes
        if total_bytes > _MAX_INPUT_BYTES:
            raise CalibrationDatasetError(f"{source} input exceeds UTF-8 byte limit")
        if total_nodes > _MAX_INPUT_NODES:
            raise CalibrationDatasetError(f"{source} input exceeds node limit")
        if _parse(event.available_time) > dataset_as_of:
            continue
        visible.append(event)
    return visible


def _analysis_row(event: LearningEvent) -> dict[str, Any]:
    if event.schema_version != _SUPPORTED_SCHEMA:
        raise CalibrationDatasetError("unknown analysis schema version")
    if (
        event.kind != "historical_non_evidentiary"
        or event.payload.get("event_type") != "analysis-quality.v1"
    ):
        raise CalibrationDatasetError("dataset source must be analysis-quality event")
    if (
        "five_year_ohlcv_rows" in event.payload
        or event.payload.get("source_kind") == "five_year_ohlcv"
    ):
        raise CalibrationDatasetError(
            "five-year OHLCV cannot be expanded as analysis samples"
        )
    if event.payload.get("failure", {}).get("status") != "complete":
        raise CalibrationDatasetError("partial or failed analysis is ineligible")
    analysis_id = event.payload.get("analysis_id")
    _bounded_text(analysis_id, "analysis_id", required=True)
    confidence = event.payload.get("confidence")
    decision = event.payload.get("decision")
    if not isinstance(confidence, Mapping) or not isinstance(decision, Mapping):
        raise CalibrationDatasetError("analysis confidence and decision are required")
    raw = _unit_confidence(confidence.get("raw"), "confidence.raw")
    calibrated = _unit_confidence(
        confidence.get("calibrated"), "confidence.calibrated"
    )
    direction = decision.get("direction")
    if direction not in _SUPPORTED_DIRECTIONS:
        raise CalibrationDatasetError("analysis direction is unsupported")
    for field in ("coin", "mode", "question_type"):
        _bounded_text(event.payload.get(field), field, required=True)
    return {
        "analysis_id": analysis_id,
        "tenant_id": event.tenant_id,
        "analysis_identity": event.identity,
        "schema_version": event.schema_version,
        "analysis_event_time": event.event_time,
        "analysis_available_time": event.available_time,
        "coin": event.payload["coin"],
        "mode": event.payload["mode"],
        "question_type": event.payload["question_type"],
        "calibrated_confidence": calibrated,
        "raw_confidence": raw,
        "direction": direction,
    }


def _latest_labeled_outcomes(
    outcome_events: list[LearningEvent],
    *,
    policy: CalibrationDatasetPolicy,
    source_analyses: dict[str, LearningEvent],
    group_splits: dict[str, str],
    group_cutoffs: dict[str, datetime],
    trusted_authority_registry: FixtureAuthorityRegistry,
    excluded: Counter[str],
) -> dict[str, dict[str, dict[str, Any]]]:
    selected: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
    tenant_events = [
        event for event in outcome_events if event.tenant_id == policy.tenant_id
    ]
    wrong_tenant = len(outcome_events) - len(tenant_events)
    if wrong_tenant:
        excluded["outcome_wrong_tenant"] += wrong_tenant
    by_outcome_id: dict[str, LearningEvent] = {}
    for event in tenant_events:
        if event.schema_version != _SUPPORTED_SCHEMA:
            raise CalibrationDatasetError("unknown outcome schema version")
        if event.kind != "delayed_outcome":
            raise CalibrationDatasetError(
                "dataset outcome source must be delayed_outcome event"
            )
        outcome_id = event.payload.get("outcome_id")
        if isinstance(outcome_id, str):
            if outcome_id in by_outcome_id:
                raise CalibrationDatasetError("duplicate outcome identity")
            by_outcome_id[outcome_id] = event

    for event in sorted(tenant_events, key=lambda item: (item.identity, item.revision)):
        payload = event.payload
        if payload.get("market_data_variant") != policy.market_data_variant:
            excluded["outcome_wrong_variant"] += 1
            continue
        source_identity = payload.get("source_event_identity")
        source_analysis = source_analyses.get(source_identity)
        if source_analysis is None:
            raise CalibrationDatasetError(
                "outcome source_event_identity does not match analysis"
            )
        predecessor = by_outcome_id.get(payload.get("supersedes_outcome_id"))
        try:
            validate_canonical_delayed_outcome(
                event,
                source_analysis=source_analysis,
                trusted_authority_registry=trusted_authority_registry,
                predecessor=predecessor,
            )
        except LearningEventError as exc:
            raise CalibrationDatasetError(
                "dataset outcome failed canonical validation"
            ) from exc
        cutoff = group_cutoffs.get(source_identity)
        if cutoff is None:
            continue
        if _parse(event.available_time) > cutoff:
            excluded[
                f"outcome_after_{group_splits[source_identity]}_label_cutoff"
            ] += 1
            continue
        if payload.get("status") != "labeled":
            excluded["outcome_not_labeled"] += 1
            continue
        if payload.get("direction_sign") not in {-1, 1}:
            excluded["outcome_non_directional"] += 1
            continue
        horizon = payload.get("horizon")
        _bounded_text(horizon, "horizon", required=True)
        revision = payload.get("outcome_version")
        if type(revision) is not int or revision < 1:
            raise CalibrationDatasetError("canonical outcome_version is required")
        key = (source_identity, horizon)
        previous = selected.get(key)
        if previous is not None and revision == previous[0]:
            raise CalibrationDatasetError("duplicate outcome revision")
        if previous is not None and revision < previous[0]:
            continue
        selected[key] = (
            revision,
            {
                "outcome_identity": event.identity,
                "source_event_identity": source_identity,
                "tenant_id": event.tenant_id,
                "market_data_variant": policy.market_data_variant,
                "outcome_available_time": event.available_time,
                "outcome_source_version": payload.get("market_data_revision"),
                "horizon": horizon,
                "outcome_pct": payload.get("return_pct"),
                "ground_truth_direction": _ground_truth(payload),
            },
        )
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for (source_identity, horizon), (_, row) in selected.items():
        result.setdefault(source_identity, {})[horizon] = row
    return result


def _ground_truth(payload: Mapping[str, Any]) -> str:
    value = payload.get("return_pct")
    if not isinstance(value, str):
        raise CalibrationDatasetError("canonical return_pct is invalid")
    try:
        numeric = Decimal(value)
    except InvalidOperation:
        raise CalibrationDatasetError("canonical return_pct is invalid") from None
    if not numeric.is_finite():
        raise CalibrationDatasetError("canonical return_pct is invalid")
    return "bullish" if numeric > 0 else "bearish" if numeric < 0 else "neutral"


def _split_for(prediction_available_time: datetime, policy: CalibrationDatasetPolicy) -> str:
    if prediction_available_time > _parse(policy.dataset_as_of):
        raise CalibrationDatasetError(
            "analysis available_time exceeds dataset_as_of"
        )
    if prediction_available_time < _parse(policy.train_end):
        return "train"
    if prediction_available_time < _parse(policy.validation_end):
        return "validation"
    return "test"


def _label_cutoff(split: str, policy: CalibrationDatasetPolicy) -> datetime:
    boundary = {
        "train": _parse(policy.train_end),
        "validation": _parse(policy.validation_end),
        "test": _parse(policy.dataset_as_of),
    }[split]
    return boundary - timedelta(seconds=policy.embargo_seconds)


def _unit_confidence(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CalibrationDatasetError(f"{field} must be finite and within [0, 1]")
    numeric = float(value)
    if not math.isfinite(numeric) or not 0 <= numeric <= 1:
        raise CalibrationDatasetError(f"{field} must be finite and within [0, 1]")
    return numeric


def _bounded_text(value: Any, field: str, *, required: bool) -> None:
    if not isinstance(value, str) or (required and not value):
        raise CalibrationDatasetError(f"{field} is required")
    if len(value.encode("utf-8")) > _MAX_FIELD_BYTES:
        raise CalibrationDatasetError(f"{field} exceeds UTF-8 byte limit")


def _preflight_event(
    event: LearningEvent,
    *,
    source: str,
    byte_budget: int,
    node_budget: int,
) -> tuple[int, int]:
    state = {"nodes": 0, "node_budget": node_budget, "source": source}
    total_bytes = 0
    for token in _canonical_event_anchor_tokens(event, state=state):
        total_bytes += len(token.encode("utf-8"))
        if total_bytes > byte_budget:
            raise CalibrationDatasetError(
                f"{source} input exceeds UTF-8 byte limit"
            )
    return total_bytes, int(state["nodes"])


def _canonical_event_anchor_tokens(
    event: LearningEvent, *, state: dict[str, Any]
) -> Iterable[str]:
    yield from _canonical_mapping_tokens(
        _event_anchor_items(event), state=state, depth=1
    )


def _event_anchor_items(event: LearningEvent) -> tuple[tuple[str, Any], ...]:
    return (
        ("identity", event.identity),
        ("schema_version", event.schema_version),
        ("tenant_id", event.tenant_id),
        ("kind", event.kind),
        ("entity_id", event.entity_id),
        ("event_time", event.event_time),
        ("available_time", event.available_time),
        ("as_of_time", event.as_of_time),
        ("provenance", event.provenance),
        ("payload", event.payload),
    )


def _canonical_mapping_tokens(
    items: Iterable[tuple[str, Any]],
    *,
    state: dict[str, Any],
    depth: int,
    visited: bool = False,
) -> Iterable[str]:
    if not visited:
        _visit_node(state, depth)
    ordered = sorted(items, key=lambda item: item[0])
    yield "{"
    for index, (key, value) in enumerate(ordered):
        if index:
            yield ","
        yield from _canonical_scalar_tokens(key, state=state, depth=depth + 1)
        yield ":"
        yield from _canonical_value_tokens(value, state=state, depth=depth + 1)
    yield "}"


def _canonical_value_tokens(
    value: Any, *, state: dict[str, Any], depth: int
) -> Iterable[str]:
    if isinstance(value, Mapping):
        _visit_node(state, depth)
        if state["nodes"] + (2 * len(value)) > state["node_budget"]:
            raise CalibrationDatasetError(
                f"{state['source']} input exceeds node limit"
            )
        keys: list[str] = []
        for key in value:
            if not isinstance(key, str):
                raise CalibrationDatasetError(
                    f"{state['source']} input mapping key must be a string"
                )
            if _utf8_exceeds(key, _MAX_FIELD_BYTES):
                raise CalibrationDatasetError(
                    f"{state['source']} input field exceeds UTF-8 byte limit"
                )
            keys.append(key)
        keys.sort()
        yield from _canonical_mapping_tokens(
            ((key, value[key]) for key in keys),
            state=state,
            depth=depth,
            visited=True,
        )
        return
    if isinstance(value, (tuple, list)):
        _visit_node(state, depth)
        yield "["
        for index, item in enumerate(value):
            if index:
                yield ","
            yield from _canonical_value_tokens(item, state=state, depth=depth + 1)
        yield "]"
        return
    yield from _canonical_scalar_tokens(value, state=state, depth=depth)


def _canonical_scalar_tokens(
    value: Any, *, state: dict[str, Any], depth: int
) -> Iterable[str]:
    _visit_node(state, depth)
    if isinstance(value, str) and _utf8_exceeds(value, _MAX_FIELD_BYTES):
        raise CalibrationDatasetError(
            f"{state['source']} input field exceeds UTF-8 byte limit"
        )
    encoder = json.JSONEncoder(
        ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    yield from encoder.iterencode(value)


def _visit_node(state: dict[str, Any], depth: int) -> None:
    state["nodes"] += 1
    if state["nodes"] > state["node_budget"]:
        raise CalibrationDatasetError(f"{state['source']} input exceeds node limit")
    if depth > _MAX_NESTING_DEPTH:
        raise CalibrationDatasetError(
            f"{state['source']} input exceeds nesting depth limit"
        )


def _canonical_event_anchor_size(event: LearningEvent) -> int:
    state: dict[str, Any] = {
        "nodes": 0,
        "node_budget": _MAX_INPUT_NODES,
        "source": "analysis",
    }
    return sum(
        len(token.encode("utf-8"))
        for token in _canonical_event_anchor_tokens(event, state=state)
    )


def _utf8_exceeds(value: str, limit: int) -> bool:
    total = 0
    for character in value:
        total += len(character.encode("utf-8"))
        if total > limit:
            return True
    return False


def _event_anchor(event: LearningEvent) -> dict[str, Any]:
    return {
        key: _jsonable(value)
        for key, value in _event_anchor_items(event)
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _parse(value: str) -> datetime:
    if not isinstance(value, str):
        raise CalibrationDatasetError("dataset timestamps must be strings")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise CalibrationDatasetError("dataset timestamps are invalid") from None
    if parsed.tzinfo is None:
        raise CalibrationDatasetError("dataset timestamps must be timezone aware")
    return parsed.astimezone(timezone.utc)


def _canonical_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )
