"""Fixture-only delayed outcome observations.

This module implements the approved #501 outcome semantics without connecting a
production market-data provider, database, backfill job, or HTTP surface.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .learning_event_contract import (
    LearningEvent,
    LearningEventError,
    canonical_integrity_checksum,
    make_learning_event,
)

_HORIZONS = {"T+1": 1, "T+7": 7, "T+14": 14}
_VARIANTS = {"as_first_known", "latest_official"}
_DIRECTIONS = {"bullish": 1, "bearish": -1, "neutral": 0, "abstain": None}
_PERCENT_QUANTUM = Decimal("0.00000001")
_PRICE_QUANTUM = Decimal("0.00000001")
_LATE_CUTOFF = timedelta(hours=72)
_OUTCOME_CONTRACT = "delayed-outcome.v1"
_MAX_FIXTURE_RECORDS = 10_000
_MAX_FIXTURE_BYTES = 1024 * 1024
_MAX_REVISIONS_PER_LOGICAL_KEY = 100
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_FIXTURE_AUTHORITY = {
    ("fixture-provider", "fixture-dataset-v1", "split-v1"),
}
_OUTCOME_PAYLOAD_FIELDS = {
    "event_type", "classification", "eligible_as_evidence", "outcome_id",
    "analysis_id", "identity_inputs", "prediction_id",
    "source_event_identity", "horizon", "contract_version",
    "market_data_variant", "market_data_revision", "outcome_version",
    "maturity", "status", "reason_code", "start_session", "target_session",
    "matures_at", "labeled_at", "canonical_as_of",
    "supersedes_outcome_id", "lineage", "return_pct", "direction_sign",
    "directional_return_pct", "risk_abs_move_pct", "risk_downside_pct", "hit",
}


class OutcomeAppendPort(Protocol):
    def append(self, event: LearningEvent) -> str: ...


@dataclass(frozen=True)
class VenueSession:
    """Venue status: ``closed`` means venue closure, never instrument suspension."""

    label: str
    status: str
    scheduled_close_at: str | None

    def __post_init__(self) -> None:
        if type(self.label) is not str or not self.label or len(self.label.encode("utf-8")) > 32:
            raise LearningEventError("venue session label is invalid")
        if type(self.status) is not str or not self.status or len(self.status.encode("utf-8")) > 16:
            raise LearningEventError("venue session status is invalid")
        if self.scheduled_close_at is not None and (
            type(self.scheduled_close_at) is not str
            or not self.scheduled_close_at
            or len(self.scheduled_close_at.encode("utf-8")) > 4096
        ):
            raise LearningEventError("venue session scheduled_close_at is invalid")


@dataclass(frozen=True)
class FixtureVenueCalendar:
    """A complete, versioned fixture calendar; unknown dates fail closed."""

    calendar_id: str
    timezone: str
    version_available_at: str
    continuous_24_7: bool
    sessions: tuple[VenueSession, ...]
    prediction_cutoff_minutes: int
    publication_lag_hours: int

    def __post_init__(self) -> None:
        if type(self.sessions) is not tuple or any(
            type(session) is not VenueSession for session in self.sessions
        ):
            raise LearningEventError("calendar fixture must contain exact VenueSession records")
        for field in ("calendar_id", "timezone", "version_available_at"):
            value = getattr(self, field)
            if type(value) is not str or not value or len(value.encode("utf-8")) > 4096:
                raise LearningEventError(f"calendar {field} is invalid")
        if type(self.continuous_24_7) is not bool:
            raise LearningEventError("calendar continuous_24_7 must be boolean")
        if not self.calendar_id or not self.timezone:
            raise LearningEventError("calendar identity and timezone are required")
        if not self.sessions:
            raise LearningEventError("calendar sessions are required")
        if len(self.sessions) > _MAX_FIXTURE_RECORDS:
            raise LearningEventError("calendar fixture exceeds session limit")
        _assert_streaming_calendar_limit(self)
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise LearningEventError("calendar timezone must be a valid IANA zone") from None
        _parse_datetime(self.version_available_at, "calendar version_available_at")
        expected_buffer = 5 if self.continuous_24_7 else 15
        expected_sla = 1 if self.continuous_24_7 else 4
        if self.prediction_cutoff_minutes != expected_buffer or self.publication_lag_hours != expected_sla:
            raise LearningEventError("calendar rule is not approved")
        labels: set[str] = set()
        previous_label: str | None = None
        previous_close: datetime | None = None
        for session in self.sessions:
            if session.label in labels or session.status not in {"open", "closed", "unknown"}:
                raise LearningEventError("calendar session is invalid")
            try:
                parsed_label = datetime.strptime(session.label, "%Y-%m-%d")
            except ValueError:
                raise LearningEventError("calendar session label must be ISO date") from None
            if parsed_label.strftime("%Y-%m-%d") != session.label:
                raise LearningEventError("calendar session label must be ISO date")
            if previous_label is not None and session.label <= previous_label:
                raise LearningEventError("calendar session labels are not ordered")
            previous_label = session.label
            labels.add(session.label)
            if session.status == "open":
                if session.scheduled_close_at is None:
                    raise LearningEventError("open session close is required")
                close = _parse_datetime(session.scheduled_close_at, "session scheduled_close_at")
                if previous_close is not None and close <= previous_close:
                    raise LearningEventError("calendar sessions are not ordered")
                previous_close = close
            elif session.scheduled_close_at is not None:
                raise LearningEventError("closed or unknown session must not have a close")
        if self.continuous_24_7:
            if self.timezone != "UTC":
                raise LearningEventError("24/7 calendar timezone must be UTC")
            for index, session in enumerate(self.sessions):
                if session.status != "open":
                    raise LearningEventError("24/7 calendar sessions must all be open")
                label = datetime.strptime(session.label, "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                )
                expected_close = label + timedelta(days=1)
                actual_close = _parse_datetime(
                    session.scheduled_close_at or "",
                    "24/7 session scheduled_close_at",
                )
                if actual_close != expected_close:
                    raise LearningEventError(
                        "24/7 session close must be next UTC midnight"
                    )
                if index and label != datetime.strptime(
                    self.sessions[index - 1].label,
                    "%Y-%m-%d",
                ).replace(tzinfo=timezone.utc) + timedelta(days=1):
                    raise LearningEventError("24/7 session labels must be daily")

    def resolve(
        self,
        prediction_available: datetime,
        horizon_sessions: int,
    ) -> tuple[VenueSession, VenueSession] | None:
        """Resolve only the path needed for start and target."""

        buffer = timedelta(minutes=self.prediction_cutoff_minutes)
        start_index: int | None = None
        for index, session in enumerate(self.sessions):
            if session.status == "unknown":
                raise LearningEventError("CALENDAR_GAP")
            if session.status == "closed":
                continue
            close = _parse_datetime(session.scheduled_close_at or "", "session close")
            if prediction_available <= close - buffer:
                start_index = index
                break
        if start_index is None:
            return None
        remaining = horizon_sessions
        for session in self.sessions[start_index + 1 :]:
            if session.status == "unknown":
                raise LearningEventError("CALENDAR_GAP")
            if session.status == "closed":
                continue
            remaining -= 1
            if remaining == 0:
                return self.sessions[start_index], session
        return None


@dataclass(frozen=True)
class FixturePrice:
    session_label: str
    adjusted_close: str
    event_at: str
    available_at: str
    provider: str
    dataset_version: str
    methodology_version: str
    content_hash: str

    def __post_init__(self) -> None:
        for field in (
            "session_label",
            "adjusted_close",
            "event_at",
            "available_at",
            "provider",
            "dataset_version",
            "methodology_version",
            "content_hash",
        ):
            value = getattr(self, field)
            if type(value) is not str or not value or len(value.encode("utf-8")) > 4096:
                raise LearningEventError(f"fixture price {field} is invalid")


@dataclass(frozen=True)
class FixtureMarketData:
    """Explicit fixture data.  The name prevents accidental production use."""

    prices: tuple[FixturePrice, ...]

    def __post_init__(self) -> None:
        if type(self.prices) is not tuple or any(
            type(item) is not FixturePrice for item in self.prices
        ):
            raise LearningEventError("market-data fixture must contain exact FixturePrice records")
        if len(self.prices) > _MAX_FIXTURE_RECORDS:
            raise LearningEventError("market-data fixture exceeds price limit")
        _assert_streaming_price_limit(self.prices)

    def price_for(
        self,
        label: str,
        as_of: datetime,
        *,
        variant: str,
    ) -> FixturePrice | None:
        matches = [
            item
            for item in self.prices
            if item.session_label == label
            and _parse_datetime(item.available_at, "price available_at") <= as_of
        ]
        if not matches:
            return None
        ordered = sorted(
            matches,
            key=lambda item: (
                _parse_datetime(item.available_at, "price available_at"),
                json.dumps(
                    _price_manifest(item),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ),
            ),
        )
        return ordered[0] if variant == "as_first_known" else ordered[-1]


def canonical_market_data_revision(
    *,
    calendar: FixtureVenueCalendar,
    variant: str,
    fixture: FixtureMarketData,
    start: FixturePrice | None,
    target: FixturePrice | None,
    visible_at: str,
) -> str:
    """Hash only the exact selected records visible at the event cutoff."""

    cutoff = _parse_datetime(visible_at, "market revision visible_at")
    for selected in (start, target):
        if selected is not None and (
            selected not in fixture.prices
            or _parse_datetime(selected.available_at, "selected price available_at")
            > cutoff
        ):
            raise LearningEventError(
                "selected market data must belong to the visible fixture snapshot"
            )
    manifest = {
        "calendar": _calendar_manifest(calendar),
        "variant": variant,
        "selected": {
            "start": _price_manifest(start),
            "target": _price_manifest(target),
        },
    }
    encoded = json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    if len(encoded) > _MAX_FIXTURE_BYTES:
        raise LearningEventError("fixture manifest exceeds aggregate byte limit")
    return canonical_integrity_checksum(manifest)


def _build_delayed_outcome_observation(
    analysis_event: LearningEvent,
    *,
    trusted_tenant_id: str,
    trusted_as_of_time: str,
    trusted_labeled_at: str,
    calendar: FixtureVenueCalendar,
    market_data: FixtureMarketData,
    horizon: str,
    market_data_variant: str,
    market_data_revision: str,
    trusted_outcome_version: int,
    trusted_supersedes: LearningEvent | None = None,
) -> LearningEvent:
    """Build one immutable delayed outcome revision from trusted fixture inputs."""

    _validate_analysis(analysis_event, trusted_tenant_id)
    if horizon not in _HORIZONS:
        raise LearningEventError("unsupported outcome horizon")
    if market_data_variant not in _VARIANTS:
        raise LearningEventError("unsupported market data variant")
    if not isinstance(market_data_revision, str) or not _SHA256.fullmatch(
        market_data_revision
    ):
        raise LearningEventError("market_data_revision must be content-addressed")
    if (
        isinstance(trusted_outcome_version, bool)
        or not isinstance(trusted_outcome_version, int)
        or trusted_outcome_version < 1
    ):
        raise LearningEventError("trusted_outcome_version must be positive")

    as_of = _parse_datetime(trusted_as_of_time, "trusted_as_of_time")
    labeled_at = _parse_datetime(trusted_labeled_at, "trusted_labeled_at")
    if labeled_at > as_of:
        raise LearningEventError("labeled_at cannot be after as_of")
    if _parse_datetime(calendar.version_available_at, "calendar version_available_at") > as_of:
        raise LearningEventError("calendar version is not available at as_of")
    prediction_event = _parse_datetime(analysis_event.event_time, "prediction_event_at")
    prediction_available = _parse_datetime(analysis_event.available_time, "prediction_available_at")
    if as_of < prediction_available:
        raise LearningEventError("as_of cannot precede prediction availability")
    if labeled_at < prediction_available:
        raise LearningEventError("labeled_at cannot precede prediction availability")
    if _parse_datetime(calendar.version_available_at, "calendar version_available_at") > labeled_at:
        raise LearningEventError("calendar version is not available at labeled_at")
    if prediction_event > prediction_available:
        _assert_market_data_revision(
            market_data_revision, calendar, market_data, market_data_variant, None, None,
            trusted_labeled_at,
        )
        return _state_event(
            analysis_event, trusted_tenant_id, horizon, market_data_variant,
            market_data_revision, trusted_outcome_version, trusted_as_of_time,
            trusted_labeled_at, calendar, "unavailable", "INVALID_PREDICTION_TIMELINE",
            None, None, trusted_supersedes,
        )

    try:
        resolved = calendar.resolve(prediction_available, _HORIZONS[horizon])
    except LearningEventError as exc:
        if str(exc) != "CALENDAR_GAP":
            raise
        _assert_market_data_revision(
            market_data_revision, calendar, market_data, market_data_variant,
            None, None, trusted_labeled_at,
        )
        return _state_event(
            analysis_event, trusted_tenant_id, horizon, market_data_variant,
            market_data_revision, trusted_outcome_version, trusted_as_of_time,
            trusted_labeled_at, calendar, "unavailable", "CALENDAR_GAP",
            None, None, trusted_supersedes,
        )
    if resolved is None:
        _assert_market_data_revision(
            market_data_revision, calendar, market_data, market_data_variant, None, None,
            trusted_labeled_at,
        )
        return _state_event(
            analysis_event, trusted_tenant_id, horizon, market_data_variant,
            market_data_revision, trusted_outcome_version, trusted_as_of_time,
            trusted_labeled_at, calendar, "pending", "NOT_MATURE", None, None,
            trusted_supersedes,
        )
    start_session, target_session = resolved
    target_close_at = _parse_datetime(target_session.scheduled_close_at or "", "target close")
    matures_at = target_close_at + timedelta(hours=calendar.publication_lag_hours)
    late_cutoff = matures_at + _LATE_CUTOFF
    start_price = market_data.price_for(
        start_session.label, labeled_at, variant=market_data_variant
    )
    target_price = market_data.price_for(
        target_session.label, labeled_at, variant=market_data_variant
    )
    if start_price is not None:
        _validate_selected_price(start_price, start_session, "start")
    if target_price is not None:
        _validate_selected_price(target_price, target_session, "target")
    if start_price is not None and target_price is not None:
        _validate_price_lineage(start_price, target_price)
    _assert_market_data_revision(
        market_data_revision,
        calendar,
        market_data,
        market_data_variant,
        start_price,
        target_price,
        trusted_labeled_at,
    )

    if labeled_at < matures_at:
        maturity, reason = "pending", "WAITING_OFFICIAL_CLOSE"
    elif start_price is None or target_price is None:
        missing = "START_CLOSE_MISSING" if start_price is None else "TARGET_CLOSE_MISSING"
        if labeled_at <= late_cutoff:
            maturity, reason = "pending", "WAITING_LATE_DATA_CUTOFF"
        else:
            maturity, reason = "unavailable", missing
    else:
        maturity, reason = "labeled", None
        source_available = max(
            _parse_datetime(start_price.available_at, "start price available_at"),
            _parse_datetime(target_price.available_at, "target price available_at"),
        )
        if labeled_at < max(matures_at, source_available):
            raise LearningEventError(
                "labeled_at cannot precede maturity or selected source availability"
            )
        arrived_after_cutoff = any(
            _parse_datetime(price.available_at, "price available_at") > late_cutoff
            for price in (start_price, target_price)
            if price is not None
        )
        if arrived_after_cutoff and (
            trusted_outcome_version == 1 or trusted_supersedes is None
        ):
            raise LearningEventError(
                "late-after-cutoff data requires immutable successor revision"
            )
    return _state_event(
        analysis_event, trusted_tenant_id, horizon, market_data_variant,
        market_data_revision, trusted_outcome_version, trusted_as_of_time,
        trusted_labeled_at, calendar, maturity, reason, start_session,
        target_session, trusted_supersedes,
        start_price=start_price, target_price=target_price,
        matures_at=matures_at,
    )


class FixtureOutcomeLedger:
    """Bounded in-process fixture allocator; not durable or production-safe."""

    def __init__(
        self,
        *,
        append: OutcomeAppendPort,
        maximum_revisions: int = _MAX_REVISIONS_PER_LOGICAL_KEY,
    ) -> None:
        if (
            type(maximum_revisions) is not int
            or maximum_revisions < 1
            or maximum_revisions > _MAX_REVISIONS_PER_LOGICAL_KEY
        ):
            raise ValueError("maximum_revisions is invalid")
        self._append = append
        self._maximum_revisions = maximum_revisions
        self._lock = threading.RLock()
        self._current: dict[tuple[str, str, str, str], LearningEvent] = {}
        self._retries: dict[str, LearningEvent] = {}

    def observe(
        self,
        analysis_event: LearningEvent,
        *,
        trusted_tenant_id: str,
        trusted_as_of_time: str,
        trusted_labeled_at: str,
        calendar: FixtureVenueCalendar,
        market_data: FixtureMarketData,
        horizon: str,
        market_data_variant: str,
        dry_run: bool = False,
    ) -> LearningEvent:
        if type(dry_run) is not bool:
            raise LearningEventError("dry_run must be boolean")
        prediction_id = str(analysis_event.payload.get("analysis_id", ""))
        logical_key = (
            trusted_tenant_id,
            prediction_id,
            horizon,
            market_data_variant,
        )
        with self._lock:
            previous = self._current.get(logical_key)
            market_revision = _market_revision_for_request(
                analysis_event,
                trusted_labeled_at,
                calendar,
                market_data,
                horizon,
                market_data_variant,
            )
            fingerprint = canonical_integrity_checksum(
                {
                    "logical_key": logical_key,
                    "analysis_identity": analysis_event.identity,
                    "trusted_as_of_time": trusted_as_of_time,
                    "trusted_labeled_at": trusted_labeled_at,
                    "calendar": _calendar_manifest(calendar),
                    "market_data_revision": market_revision,
                }
            )
            retry = self._retries.get(fingerprint)
            if retry is not None:
                return retry
            version = 1 if previous is None else previous.revision + 1
            if version > self._maximum_revisions:
                raise LearningEventError("fixture outcome revision budget exceeded")
            event = _build_delayed_outcome_observation(
                analysis_event,
                trusted_tenant_id=trusted_tenant_id,
                trusted_as_of_time=trusted_as_of_time,
                trusted_labeled_at=trusted_labeled_at,
                calendar=calendar,
                market_data=market_data,
                horizon=horizon,
                market_data_variant=market_data_variant,
                market_data_revision=market_revision,
                trusted_outcome_version=version,
                trusted_supersedes=previous,
            )
            validate_canonical_delayed_outcome(event, predecessor=previous)
            if dry_run:
                return event
            append_status = self._append.append(event)
            if append_status not in {"created", "idempotent"}:
                raise LearningEventError(
                    "fixture outcome append did not confirm durable creation"
                )
            self._current[logical_key] = event
            self._retries[fingerprint] = event
            return event


def _market_revision_for_request(
    analysis_event: LearningEvent,
    labeled_at: str,
    calendar: FixtureVenueCalendar,
    market_data: FixtureMarketData,
    horizon: str,
    variant: str,
) -> str:
    prediction_available = _parse_datetime(
        analysis_event.available_time,
        "prediction_available_at",
    )
    try:
        resolved = calendar.resolve(prediction_available, _HORIZONS.get(horizon, 0))
    except LearningEventError as exc:
        if str(exc) != "CALENDAR_GAP":
            raise
        resolved = None
    start = target = None
    if resolved is not None:
        cutoff = _parse_datetime(labeled_at, "trusted_labeled_at")
        start = market_data.price_for(resolved[0].label, cutoff, variant=variant)
        target = market_data.price_for(resolved[1].label, cutoff, variant=variant)
    return canonical_market_data_revision(
        calendar=calendar,
        variant=variant,
        fixture=market_data,
        start=start,
        target=target,
        visible_at=labeled_at,
    )


def validate_canonical_delayed_outcome(
    event: LearningEvent,
    *,
    predecessor: LearningEvent | None = None,
) -> None:
    """Fail closed unless an event is the exact canonical delayed-outcome schema."""

    payload = event.payload
    provenance = event.provenance
    source_record = provenance.get("source_record")
    source_record_fields = {
        "fixture_only",
        "analysis_identity",
        "calendar_id",
        "calendar_version_available_at",
        "market_data_revision",
        "identity_inputs",
        "payload_checksum",
    }
    if (
        event.kind != "delayed_outcome"
        or set(payload) != _OUTCOME_PAYLOAD_FIELDS
        or payload.get("event_type") != _OUTCOME_CONTRACT
        or payload.get("classification") != "non_evidentiary_outcome"
        or payload.get("eligible_as_evidence") is not False
        or provenance.get("source") != "fixture-delayed-outcome-labeler"
        or provenance.get("collector") != "trustforge"
        or provenance.get("version") != _OUTCOME_CONTRACT
        or provenance.get("tenant_id") != event.tenant_id
        or provenance.get("observed_at") != event.available_time
        or payload.get("labeled_at") != event.available_time
        or payload.get("canonical_as_of") != event.as_of_time
        or payload.get("status") != payload.get("maturity")
        or not isinstance(payload.get("source_event_identity"), str)
        or not hasattr(source_record, "items")
        or set(source_record) != source_record_fields
        or source_record.get("fixture_only") is not True
        or source_record.get("analysis_identity") != payload.get("source_event_identity")
        or source_record.get("payload_checksum")
        != canonical_integrity_checksum(payload)
        or provenance.get("checksum")
        != canonical_integrity_checksum(source_record)
    ):
        raise LearningEventError("delayed outcome emission contract is invalid")
    inputs = payload.get("identity_inputs")
    if not hasattr(inputs, "items") or set(inputs) != {
        "tenant_id", "prediction_id", "horizon", "contract_version",
        "market_data_variant", "market_data_revision", "outcome_version",
    }:
        raise LearningEventError("delayed outcome identity inputs are invalid")
    reconstructed = "sha256:" + hashlib.sha256(
        json.dumps(
            dict(inputs),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    if (
        reconstructed != payload.get("outcome_id")
        or event.entity_id != reconstructed
        or event.revision != inputs["outcome_version"]
        or inputs["tenant_id"] != event.tenant_id
        or source_record.get("identity_inputs") != inputs
        or payload.get("analysis_id") != payload.get("prediction_id")
        or inputs["prediction_id"] != payload.get("prediction_id")
        or inputs["horizon"] != payload.get("horizon")
        or inputs["contract_version"] != payload.get("contract_version")
        or inputs["contract_version"] != _OUTCOME_CONTRACT
        or inputs["market_data_variant"] != payload.get("market_data_variant")
        or inputs["market_data_revision"] != payload.get("market_data_revision")
        or inputs["outcome_version"] != payload.get("outcome_version")
        or source_record.get("market_data_revision")
        != payload.get("market_data_revision")
    ):
        raise LearningEventError("delayed outcome canonical identity is invalid")
    maturity = payload.get("maturity")
    metric_fields = (
        "return_pct", "direction_sign", "directional_return_pct",
        "risk_abs_move_pct", "risk_downside_pct", "hit",
    )
    if maturity == "labeled":
        if payload.get("lineage") is None:
            raise LearningEventError("labeled delayed outcome metrics are invalid")
        realized = _canonical_metric_decimal(payload.get("return_pct"), "return_pct")
        absolute = _canonical_metric_decimal(
            payload.get("risk_abs_move_pct"),
            "risk_abs_move_pct",
        )
        downside = _canonical_metric_decimal(
            payload.get("risk_downside_pct"),
            "risk_downside_pct",
        )
        if realized < Decimal("-100") or absolute != abs(realized) or downside != min(
            realized,
            Decimal(0),
        ):
            raise LearningEventError("labeled delayed outcome metrics are inconsistent")
        sign = payload.get("direction_sign")
        if sign is not None and (type(sign) is not int or sign not in {-1, 0, 1}):
            raise LearningEventError("delayed outcome direction_sign is invalid")
        if sign in {0, None} and (
            payload.get("directional_return_pct") is not None
            or payload.get("hit") is not None
            or payload.get("reason_code") != "PREDICTION_NOT_DIRECTIONAL"
        ):
            raise LearningEventError("non-directional delayed outcome is invalid")
        if sign in {-1, 1}:
            directional = _canonical_metric_decimal(
                payload.get("directional_return_pct"),
                "directional_return_pct",
            )
            expected_directional = (realized * sign).quantize(_PERCENT_QUANTUM)
            if (
                directional != expected_directional
                or payload.get("hit") is not (directional > 0)
                or payload.get("reason_code") is not None
            ):
                raise LearningEventError(
                    "directional delayed outcome metrics are inconsistent"
                )
    elif maturity in {"pending", "unavailable"}:
        if any(payload.get(field) is not None for field in metric_fields):
            raise LearningEventError("unlabeled delayed outcome metrics must be null")
        if payload.get("lineage") is not None or not isinstance(
            payload.get("reason_code"), str
        ):
            raise LearningEventError("unlabeled delayed outcome state is invalid")
    else:
        raise LearningEventError("delayed outcome maturity is invalid")
    supersedes_id = payload.get("supersedes_outcome_id")
    if supersedes_id is None:
        if event.revision != 1 or predecessor is not None:
            raise LearningEventError("initial delayed outcome revision is invalid")
    else:
        if predecessor is None or predecessor.payload.get("outcome_id") != supersedes_id:
            raise LearningEventError("delayed outcome predecessor is required")
        _validate_supersession(
            predecessor,
            event.tenant_id,
            str(payload["prediction_id"]),
            str(payload["horizon"]),
            str(payload["market_data_variant"]),
            event.revision,
            str(payload["source_event_identity"]),
            event.available_time,
            event.as_of_time,
        )


def _state_event(
    analysis: LearningEvent,
    tenant_id: str,
    horizon: str,
    variant: str,
    revision_hash: str,
    outcome_version: int,
    as_of_time: str,
    labeled_at: str,
    calendar: FixtureVenueCalendar,
    maturity: str,
    reason: str | None,
    start_session: VenueSession | None,
    target_session: VenueSession | None,
    supersedes: LearningEvent | None,
    *,
    start_price: FixturePrice | None = None,
    target_price: FixturePrice | None = None,
    matures_at: datetime | None = None,
) -> LearningEvent:
    labeled_at = _timestamp(_parse_datetime(labeled_at, "labeled_at"))
    as_of_time = _timestamp(_parse_datetime(as_of_time, "canonical_as_of"))
    prediction_id = str(analysis.payload["analysis_id"])
    identity_inputs = {
        "tenant_id": tenant_id,
        "prediction_id": prediction_id,
        "horizon": horizon,
        "contract_version": _OUTCOME_CONTRACT,
        "market_data_variant": variant,
        "market_data_revision": revision_hash,
        "outcome_version": outcome_version,
    }
    outcome_id = "sha256:" + hashlib.sha256(
        json.dumps(identity_inputs, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    supersedes_id = _validate_supersession(
        supersedes, tenant_id, prediction_id, horizon, variant, outcome_version,
        analysis.identity, labeled_at, as_of_time,
    )
    metrics: dict[str, Any] = {
        "return_pct": None,
        "direction_sign": None,
        "directional_return_pct": None,
        "risk_abs_move_pct": None,
        "risk_downside_pct": None,
        "hit": None,
    }
    lineage: dict[str, Any] | None = None
    if maturity == "labeled":
        if start_price is None or target_price is None:
            raise LearningEventError("labeled outcome requires both prices")
        start = _price_decimal(start_price.adjusted_close)
        target = _price_decimal(target_price.adjusted_close)
        if start == 0:
            maturity, reason = "unavailable", "ZERO_START_CLOSE"
        else:
            direction = str(analysis.payload["decision"]["direction"])
            if direction not in _DIRECTIONS:
                raise LearningEventError("prediction direction is unsupported")
            with localcontext() as context:
                context.prec = 34
                context.rounding = ROUND_HALF_EVEN
                raw_return = (target / start - Decimal(1)) * Decimal(100)
                sign = _DIRECTIONS[direction]
                directional = raw_return * sign if sign in {-1, 1} else None
                if directional is None:
                    reason = "PREDICTION_NOT_DIRECTIONAL"
                metrics = {
                    "return_pct": _persist_decimal(raw_return),
                    "direction_sign": sign,
                    "directional_return_pct": _persist_decimal(directional) if directional is not None else None,
                    "risk_abs_move_pct": _persist_decimal(abs(raw_return)),
                    "risk_downside_pct": _persist_decimal(min(raw_return, Decimal(0))),
                    "hit": directional > 0 if directional is not None else None,
                }
            lineage = {
                "adjustment_basis": "split_adjusted_price_return",
                "cash_dividend_included": False,
                "start": _price_lineage(start_price),
                "target": _price_lineage(target_price),
            }
    payload = {
        "event_type": "delayed-outcome.v1",
        "classification": "non_evidentiary_outcome",
        "eligible_as_evidence": False,
        "outcome_id": outcome_id,
        "analysis_id": prediction_id,
        "identity_inputs": identity_inputs,
        "prediction_id": prediction_id,
        "source_event_identity": analysis.identity,
        "horizon": horizon,
        "contract_version": _OUTCOME_CONTRACT,
        "market_data_variant": variant,
        "market_data_revision": revision_hash,
        "outcome_version": outcome_version,
        "maturity": maturity,
        "status": maturity,
        "reason_code": reason,
        "start_session": start_session.label if start_session else None,
        "target_session": target_session.label if target_session else None,
        "matures_at": _timestamp(matures_at) if matures_at else None,
        "labeled_at": labeled_at,
        "canonical_as_of": as_of_time,
        "supersedes_outcome_id": supersedes_id,
        "lineage": lineage,
        **metrics,
    }
    source_record = {
        "fixture_only": True,
        "analysis_identity": analysis.identity,
        "calendar_id": calendar.calendar_id,
        "calendar_version_available_at": calendar.version_available_at,
        "market_data_revision": revision_hash,
        "identity_inputs": identity_inputs,
        "payload_checksum": canonical_integrity_checksum(payload),
    }
    return make_learning_event(
        kind="delayed_outcome",
        tenant_id=tenant_id,
        entity_id=outcome_id,
        revision=outcome_version,
        event_time=analysis.event_time,
        available_time=labeled_at,
        as_of_time=as_of_time,
        provenance={
            "source": "fixture-delayed-outcome-labeler",
            "collector": "trustforge",
            "observed_at": labeled_at,
            "tenant_id": tenant_id,
            "source_record": source_record,
            "version": _OUTCOME_CONTRACT,
            "checksum": canonical_integrity_checksum(source_record),
        },
        payload=payload,
    )


def _validate_analysis(event: LearningEvent, trusted_tenant_id: str) -> None:
    if not trusted_tenant_id or event.tenant_id != trusted_tenant_id:
        raise LearningEventError("trusted tenant does not match analysis")
    if event.kind != "historical_non_evidentiary" or event.payload.get("event_type") != "analysis-quality.v1":
        raise LearningEventError("delayed outcome source must be analysis-quality.v1")


def _validate_supersession(
    previous: LearningEvent | None,
    tenant_id: str,
    prediction_id: str,
    horizon: str,
    variant: str,
    outcome_version: int,
    analysis_identity: str,
    successor_labeled_at: str,
    successor_as_of: str,
) -> str | None:
    if previous is None:
        if outcome_version != 1:
            raise LearningEventError("outcome revision requires predecessor")
        return None
    payload = previous.payload
    identity_inputs = payload.get("identity_inputs")
    if not isinstance(identity_inputs, dict) and not hasattr(identity_inputs, "items"):
        raise LearningEventError("predecessor identity inputs are invalid")
    identity_inputs = dict(identity_inputs)
    expected_keys = {
        "tenant_id", "prediction_id", "horizon", "contract_version",
        "market_data_variant", "market_data_revision", "outcome_version",
    }
    if set(identity_inputs) != expected_keys:
        raise LearningEventError("predecessor identity inputs are invalid")
    reconstructed = "sha256:" + hashlib.sha256(
        json.dumps(
            identity_inputs,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    if (
        previous.kind != "delayed_outcome"
        or previous.tenant_id != tenant_id
        or identity_inputs.get("tenant_id") != previous.tenant_id
        or payload.get("prediction_id") != prediction_id
        or payload.get("source_event_identity") != analysis_identity
        or identity_inputs.get("prediction_id") != prediction_id
        or payload.get("horizon") != horizon
        or identity_inputs.get("horizon") != horizon
        or payload.get("market_data_variant") != variant
        or identity_inputs.get("market_data_variant") != variant
        or identity_inputs.get("contract_version") != _OUTCOME_CONTRACT
        or payload.get("contract_version") != identity_inputs.get("contract_version")
        or payload.get("market_data_revision")
        != identity_inputs.get("market_data_revision")
        or payload.get("outcome_version") != outcome_version - 1
        or identity_inputs.get("outcome_version") != outcome_version - 1
        or previous.revision != outcome_version - 1
        or payload.get("outcome_id") != reconstructed
        or previous.entity_id != reconstructed
        or payload.get("supersedes_outcome_id") == reconstructed
        or _parse_datetime(previous.available_time, "predecessor available_time")
        > _parse_datetime(successor_labeled_at, "successor labeled_at")
        or _parse_datetime(previous.as_of_time, "predecessor as_of_time")
        > _parse_datetime(successor_as_of, "successor as_of")
    ):
        raise LearningEventError("supersession must reference same-tenant logical predecessor")
    if reconstructed == canonical_integrity_checksum(
        {
            "tenant_id": tenant_id,
            "prediction_id": prediction_id,
            "horizon": horizon,
            "contract_version": _OUTCOME_CONTRACT,
            "market_data_variant": variant,
            "market_data_revision": identity_inputs["market_data_revision"],
            "outcome_version": outcome_version,
        }
    ):
        raise LearningEventError("supersession cannot self-reference")
    return str(payload["outcome_id"])


def _assert_market_data_revision(
    supplied: str,
    calendar: FixtureVenueCalendar,
    fixture: FixtureMarketData,
    variant: str,
    start: FixturePrice | None,
    target: FixturePrice | None,
    visible_at: str,
) -> None:
    expected = canonical_market_data_revision(
        calendar=calendar,
        variant=variant,
        fixture=fixture,
        start=start,
        target=target,
        visible_at=visible_at,
    )
    if supplied != expected:
        raise LearningEventError(
            "market_data_revision does not match selected fixture manifest"
        )


def _validate_price_lineage(start: FixturePrice, target: FixturePrice) -> None:
    if (
        start.provider != target.provider
        or start.dataset_version != target.dataset_version
        or start.methodology_version != target.methodology_version
    ):
        raise LearningEventError("adjustment lineage must use one provider and methodology")


def _validate_selected_price(
    item: FixturePrice,
    session: VenueSession,
    endpoint: str,
) -> None:
    if not all(
        (
            item.provider,
            item.dataset_version,
            item.methodology_version,
            item.content_hash,
        )
    ):
        raise LearningEventError("PRICE_LINEAGE_MISSING")
    if not _SHA256.fullmatch(item.content_hash):
        raise LearningEventError("PRICE_LINEAGE_MISSING")
    if (
        item.provider,
        item.dataset_version,
        item.methodology_version,
    ) not in _FIXTURE_AUTHORITY:
        raise LearningEventError("fixture market-data authority is not allowlisted")
    if item.content_hash != canonical_fixture_price_content_hash(item):
        raise LearningEventError("fixture price content hash does not match record")
    event_at = _parse_datetime(item.event_at, f"{endpoint} price event_at")
    available_at = _parse_datetime(
        item.available_at,
        f"{endpoint} price available_at",
    )
    if event_at > available_at:
        raise LearningEventError("price timeline is invalid")
    if (
        item.session_label != session.label
        or event_at
        != _parse_datetime(
            session.scheduled_close_at or "",
            f"{endpoint} session close",
        )
    ):
        raise LearningEventError("price lineage does not match calendar sessions")
    _price_decimal(item.adjusted_close)


def canonical_fixture_price_content_hash(item: FixturePrice) -> str:
    record = {
        "session_label": item.session_label,
        "adjusted_close": item.adjusted_close,
        "event_at": item.event_at,
        "available_at": item.available_at,
        "provider": item.provider,
        "dataset_version": item.dataset_version,
        "methodology_version": item.methodology_version,
    }
    return canonical_integrity_checksum(record)


def _price_lineage(item: FixturePrice) -> dict[str, str]:
    return {
        "session_label": item.session_label,
        "provider": item.provider,
        "dataset_version": item.dataset_version,
        "methodology_version": item.methodology_version,
        "event_at": item.event_at,
        "available_at": item.available_at,
        "content_hash": item.content_hash,
    }


def _price_manifest(item: FixturePrice | None) -> dict[str, str] | None:
    if item is None:
        return None
    return {
        **_price_lineage(item),
        "adjusted_close": item.adjusted_close,
    }


def _calendar_manifest(calendar: FixtureVenueCalendar) -> dict[str, Any]:
    return {
        "calendar_id": calendar.calendar_id,
        "timezone": calendar.timezone,
        "version_available_at": calendar.version_available_at,
        "continuous_24_7": calendar.continuous_24_7,
        "prediction_cutoff_minutes": calendar.prediction_cutoff_minutes,
        "publication_lag_hours": calendar.publication_lag_hours,
        "sessions": [
            {
                "label": session.label,
                "status": session.status,
                "scheduled_close_at": session.scheduled_close_at,
            }
            for session in calendar.sessions
        ],
    }


def _assert_streaming_calendar_limit(calendar: FixtureVenueCalendar) -> None:
    encoder = json.JSONEncoder(
        ensure_ascii=False,
        sort_keys=False,
        separators=(",", ":"),
    )
    total = 0

    def consume(chunk: str) -> None:
        nonlocal total
        total += len(chunk.encode("utf-8"))
        if total > _MAX_FIXTURE_BYTES:
            raise LearningEventError(
                "calendar fixture exceeds aggregate byte limit"
            )

    fields = (
        ("calendar_id", calendar.calendar_id),
        ("continuous_24_7", calendar.continuous_24_7),
        ("prediction_cutoff_minutes", calendar.prediction_cutoff_minutes),
        ("publication_lag_hours", calendar.publication_lag_hours),
    )
    consume("{")
    for index, (key, value) in enumerate(fields):
        consume(("" if index == 0 else ",") + json.dumps(key) + ":")
        for chunk in encoder.iterencode(value):
            consume(chunk)
    consume(',"sessions":[')
    for index, session in enumerate(calendar.sessions):
        consume("" if index == 0 else ",")
        record = {
            "label": session.label,
            "scheduled_close_at": session.scheduled_close_at,
            "status": session.status,
        }
        for chunk in encoder.iterencode(record):
            consume(chunk)
    consume('],"timezone":')
    for chunk in encoder.iterencode(calendar.timezone):
        consume(chunk)
    consume(',"version_available_at":')
    for chunk in encoder.iterencode(calendar.version_available_at):
        consume(chunk)
    consume("}")


def _assert_streaming_price_limit(prices: tuple[FixturePrice, ...]) -> None:
    encoder = json.JSONEncoder(
        ensure_ascii=False,
        sort_keys=False,
        separators=(",", ":"),
    )
    total = 2
    for item in prices:
        if total > 2:
            total += 1
        for chunk in encoder.iterencode(_price_manifest(item)):
            total += len(chunk.encode("utf-8"))
            if total > _MAX_FIXTURE_BYTES:
                raise LearningEventError(
                    "market-data fixture exceeds aggregate byte limit"
                )


def _price_decimal(value: str) -> Decimal:
    if not isinstance(value, str):
        raise LearningEventError("price must be a decimal string")
    try:
        decimal = Decimal(value)
    except InvalidOperation:
        raise LearningEventError("price is invalid") from None
    if not decimal.is_finite() or decimal <= 0:
        raise LearningEventError("price must be finite and positive")
    significant = len(decimal.as_tuple().digits)
    fractional = max(0, -decimal.as_tuple().exponent)
    if significant > 18 or fractional > 8 or decimal != decimal.quantize(_PRICE_QUANTUM):
        raise LearningEventError("price exceeds numeric contract")
    return decimal


def _canonical_metric_decimal(value: Any, field: str) -> Decimal:
    if not isinstance(value, str) or not re.fullmatch(r"-?\d+\.\d{8}", value):
        raise LearningEventError(f"{field} must be a canonical Decimal8 string")
    try:
        decimal = Decimal(value)
    except InvalidOperation:
        raise LearningEventError(f"{field} is invalid") from None
    if not decimal.is_finite():
        raise LearningEventError(f"{field} is invalid")
    return decimal


def _persist_decimal(value: Decimal) -> str:
    return format(value.quantize(_PERCENT_QUANTUM, rounding=ROUND_HALF_EVEN), "f")


def _parse_datetime(value: str, field: str) -> datetime:
    if not isinstance(value, str):
        raise LearningEventError(f"{field} must be ISO-8601")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise LearningEventError(f"{field} must be ISO-8601") from None
    if parsed.tzinfo is None:
        raise LearningEventError(f"{field} must include timezone")
    return parsed.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
