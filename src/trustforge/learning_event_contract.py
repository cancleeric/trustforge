"""Canonical, non-DB contract for three-track learning events."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Literal, Mapping
from urllib.parse import quote

SCHEMA_VERSION = "learning-event.v1"
LearningEventKind = Literal[
    "evidentiary",
    "historical_non_evidentiary",
    "delayed_outcome",
    "human_gold_label",
    "candidate_diagnostic",
]
_KINDS = {
    "evidentiary",
    "historical_non_evidentiary",
    "delayed_outcome",
    "human_gold_label",
    "candidate_diagnostic",
}
_ENVELOPE_FIELDS = {
    "schema_version",
    "kind",
    "tenant_id",
    "entity_id",
    "revision",
    "identity",
    "event_time",
    "available_time",
    "as_of_time",
    "provenance",
    "payload",
}
_PROVENANCE_FIELDS = {
    "source",
    "collector",
    "observed_at",
    "tenant_id",
    "source_record",
    "version",
    "checksum",
}


class LearningEventError(ValueError):
    pass


@dataclass(frozen=True)
class LearningEvent:
    schema_version: str
    kind: LearningEventKind
    tenant_id: str
    entity_id: str
    revision: int
    identity: str
    event_time: str
    available_time: str
    as_of_time: str
    provenance: Mapping[str, Any]
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        canonical_provenance = dict(self.provenance) if isinstance(self.provenance, Mapping) else self.provenance
        if isinstance(canonical_provenance, dict) and "observed_at" in canonical_provenance:
            canonical_provenance["observed_at"] = _canonical_timestamp(
                canonical_provenance["observed_at"],
                "provenance.observed_at",
            )
        object.__setattr__(self, "event_time", _canonical_timestamp(self.event_time, "event_time"))
        object.__setattr__(self, "available_time", _canonical_timestamp(self.available_time, "available_time"))
        object.__setattr__(self, "as_of_time", _canonical_timestamp(self.as_of_time, "as_of_time"))
        object.__setattr__(self, "provenance", _deep_freeze(canonical_provenance, "provenance"))
        object.__setattr__(self, "payload", _deep_freeze(self.payload, "payload"))
        _validate_event(self)


def canonical_identity(*, tenant_id: str, kind: LearningEventKind, entity_id: str, revision: int) -> str:
    """Return an unambiguous identity from all canonical identity dimensions."""

    if not _nonempty(tenant_id):
        raise LearningEventError("tenant_id is required")
    if kind not in _KINDS:
        raise LearningEventError("unknown learning event kind")
    if not _nonempty(entity_id):
        raise LearningEventError("entity_id is required")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise LearningEventError("revision must be a positive integer")
    return f"le1/{quote(tenant_id, safe='')}/{kind}/{quote(entity_id, safe='')}/v{revision}"


def canonical_integrity_checksum(source_record: Any) -> str:
    """Return an integrity-only checksum of canonical source-record bytes.

    This proves only deterministic byte self-consistency.  It does not prove
    authenticity, authorization, tenant ownership, evidentiary classification,
    or that the source record is truthful.  Callers must establish those
    properties through separate trusted controls.
    """

    canonical = _canonical_json_bytes(source_record, "provenance.source_record")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def make_learning_event(
    *,
    kind: LearningEventKind,
    tenant_id: str,
    entity_id: str,
    revision: int,
    event_time: str,
    available_time: str,
    as_of_time: str,
    provenance: Mapping[str, Any],
    payload: Mapping[str, Any],
    identity: str | None = None,
) -> LearningEvent:
    expected_identity = canonical_identity(
        tenant_id=tenant_id,
        kind=kind,
        entity_id=entity_id,
        revision=revision,
    )
    if identity is not None and identity != expected_identity:
        raise LearningEventError("identity does not match canonical identity fields")
    event = LearningEvent(
        schema_version=SCHEMA_VERSION,
        kind=kind,
        tenant_id=tenant_id,
        entity_id=entity_id,
        revision=revision,
        identity=expected_identity,
        event_time=event_time,
        available_time=available_time,
        as_of_time=as_of_time,
        provenance=provenance,
        payload=payload,
    )
    return event


def serialize_learning_event(event: LearningEvent) -> str:
    _validate_event(event)
    return _canonical_json_bytes(_to_dict(event), "learning event").decode("utf-8")


def deserialize_learning_event(raw: str | bytes) -> LearningEvent:
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise LearningEventError("learning event JSON is invalid") from None
    if not isinstance(value, dict):
        raise LearningEventError("learning event must be an object")
    unknown = set(value) - _ENVELOPE_FIELDS
    missing = _ENVELOPE_FIELDS - set(value)
    if unknown:
        raise LearningEventError(f"unknown learning event fields: {', '.join(sorted(unknown))}")
    if missing:
        raise LearningEventError(f"missing learning event fields: {', '.join(sorted(missing))}")
    if value["schema_version"] != SCHEMA_VERSION:
        raise LearningEventError("unknown learning event schema version")
    for field in ("event_time", "available_time", "as_of_time"):
        if value[field] != _canonical_timestamp(value[field], field):
            raise LearningEventError(f"{field} must use canonical UTC form")
    if isinstance(value["provenance"], dict) and "observed_at" in value["provenance"]:
        observed_at = value["provenance"]["observed_at"]
        if observed_at != _canonical_timestamp(observed_at, "provenance.observed_at"):
            raise LearningEventError("provenance.observed_at must use canonical UTC form")
    event = make_learning_event(
        kind=value["kind"],
        tenant_id=value["tenant_id"],
        entity_id=value["entity_id"],
        revision=value["revision"],
        identity=value["identity"],
        event_time=value["event_time"],
        available_time=value["available_time"],
        as_of_time=value["as_of_time"],
        provenance=value["provenance"],
        payload=value["payload"],
    )
    return event


def assert_append_only(original: LearningEvent, candidate: LearningEvent) -> None:
    """Reject in-place rewrites of an existing canonical identity."""

    _validate_event(original)
    _validate_event(candidate)
    if original.identity == candidate.identity and serialize_learning_event(original) != serialize_learning_event(candidate):
        raise LearningEventError("learning event is immutable; create a new revision")


def _to_dict(event: LearningEvent) -> dict[str, Any]:
    return {
        "schema_version": event.schema_version,
        "kind": event.kind,
        "tenant_id": event.tenant_id,
        "entity_id": event.entity_id,
        "revision": event.revision,
        "identity": event.identity,
        "event_time": event.event_time,
        "available_time": event.available_time,
        "as_of_time": event.as_of_time,
        "provenance": _deep_thaw(event.provenance),
        "payload": _deep_thaw(event.payload),
    }


def _validate_event(event: LearningEvent) -> None:
    if event.schema_version != SCHEMA_VERSION:
        raise LearningEventError("unknown learning event schema version")
    expected = canonical_identity(
        tenant_id=event.tenant_id,
        kind=event.kind,
        entity_id=event.entity_id,
        revision=event.revision,
    )
    if event.identity != expected:
        raise LearningEventError("identity does not match canonical identity fields")
    event_ts = _parse_timestamp(event.event_time, "event_time")
    available_ts = _parse_timestamp(event.available_time, "available_time")
    as_of_ts = _parse_timestamp(event.as_of_time, "as_of_time")
    if available_ts < event_ts:
        raise LearningEventError("available_time cannot precede event_time")
    if available_ts > as_of_ts:
        raise LearningEventError("available_time cannot follow as_of_time")
    if as_of_ts < event_ts:
        raise LearningEventError("as_of_time cannot precede event_time")
    _validate_provenance(event.provenance, event.tenant_id, as_of_ts)
    if not isinstance(event.payload, Mapping):
        raise LearningEventError("learning event payload must be an object")
    _validate_json_value(event.payload, "payload")
    _validate_kind_payload(event.kind, event.payload)


def _parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise LearningEventError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise LearningEventError(f"{field} must be ISO-8601") from None
    if parsed.tzinfo is None:
        raise LearningEventError(f"{field} must include timezone")
    return parsed.astimezone(timezone.utc)


def _canonical_timestamp(value: Any, field: str) -> str:
    parsed = _parse_timestamp(value, field)
    return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validate_provenance(value: Any, tenant_id: str, as_of_ts: datetime) -> None:
    if not isinstance(value, Mapping):
        raise LearningEventError("provenance is required")
    unknown = set(value) - _PROVENANCE_FIELDS
    missing = _PROVENANCE_FIELDS - set(value)
    if unknown:
        raise LearningEventError(f"unknown provenance fields: {', '.join(sorted(unknown))}")
    if missing:
        raise LearningEventError(f"missing provenance fields: {', '.join(sorted(missing))}")
    for key in ("source", "collector", "tenant_id", "version", "checksum"):
        if not _nonempty(value.get(key)):
            raise LearningEventError(f"provenance.{key} is required")
    if value["tenant_id"] != tenant_id:
        raise LearningEventError("provenance.tenant_id must match event tenant_id")
    observed = _parse_timestamp(value["observed_at"], "provenance.observed_at")
    if value["observed_at"] != _canonical_timestamp(value["observed_at"], "provenance.observed_at"):
        raise LearningEventError("provenance.observed_at must use canonical UTC form")
    if observed > as_of_ts:
        raise LearningEventError("provenance.observed_at cannot follow as_of_time")
    expected = canonical_integrity_checksum(value["source_record"])
    if value["checksum"] != expected:
        raise LearningEventError("provenance.checksum does not match canonical source_record bytes")


def _validate_kind_payload(kind: str, payload: Mapping[str, Any]) -> None:
    discriminator_fields = {
        "evidentiary": {"evidence_id"},
        "historical_non_evidentiary": {"historical_answer_id"},
        "delayed_outcome": {"outcome_id"},
        "human_gold_label": {"label_id"},
        "candidate_diagnostic": {"diagnostic_id"},
    }
    if kind == "evidentiary":
        _require(payload, "evidence_id", "claim", "source_url")
    elif kind == "historical_non_evidentiary":
        _require(payload, "historical_answer_id", "question")
    elif kind == "delayed_outcome":
        _require(payload, "outcome_id", "analysis_id", "horizon", "status")
        if payload["horizon"] not in {"T+1", "T+7", "T+14"}:
            raise LearningEventError("unsupported outcome horizon")
    elif kind == "human_gold_label":
        _require(payload, "label_id", "analysis_id", "reviewer", "label")
    elif kind == "candidate_diagnostic":
        _require(payload, "diagnostic_id", "analysis_id", "reason")
        _forbid(payload, "approval_action", "activation")
    forbidden = set().union(*(fields for name, fields in discriminator_fields.items() if name != kind))
    _forbid(payload, *sorted(forbidden))


def _require(payload: Mapping[str, Any], *keys: str) -> None:
    for key in keys:
        if not _nonempty(payload.get(key)):
            raise LearningEventError(f"{key} is required for learning event payload")


def _forbid(payload: Mapping[str, Any], *keys: str) -> None:
    for key in keys:
        if key in payload:
            raise LearningEventError(f"{key} is not allowed for this learning event kind")


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"invalid JSON constant: {value}")


def _validate_json_value(value: Any, field: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise LearningEventError(f"{field} contains a non-finite number")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise LearningEventError(f"{field} object keys must be strings")
            _validate_json_value(item, f"{field}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{field}[{index}]")
        return
    raise LearningEventError(f"{field} contains a non-JSON value")


def _canonical_json_bytes(value: Any, field: str) -> bytes:
    _validate_json_value(value, field)
    try:
        return json.dumps(
            _deep_thaw(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise LearningEventError(f"{field} is not canonical JSON") from None


def _deep_freeze(value: Any, field: str) -> Any:
    _validate_json_value(value, field)
    if isinstance(value, Mapping):
        return MappingProxyType({key: _deep_freeze(item, f"{field}.{key}") for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item, f"{field}[]") for item in value)
    return value


def _deep_thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _deep_thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_deep_thaw(item) for item in value]
    return value
