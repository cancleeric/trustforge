"""Fail-closed trusted UTC buckets for the paid planning preview.

The application wall clock is used only as an anomaly detector.  Quota bucket
selection is derived from an authenticated AWS response and elapsed monotonic
time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
import math
import threading
import time
from typing import Callable, Protocol


class TrustedClockFailure(StrEnum):
    NO_SAMPLE = "no_sample"
    AWS_ERROR = "aws_error"
    MISSING_DATE = "missing_date"
    MALFORMED_DATE = "malformed_date"
    MONOTONIC_ANOMALY = "monotonic_anomaly"
    WALL_CLOCK_ANOMALY = "wall_clock_anomaly"
    TIME_RANGE_ERROR = "time_range_error"
    REFRESH_OVERDUE = "refresh_overdue"
    STALE = "stale"
    AMBIGUOUS_BUCKET = "ambiguous_bucket"


class TrustedClockUnavailable(RuntimeError):
    """Typed fail-closed result.  Its message contains no provider details."""

    def __init__(self, reason: TrustedClockFailure) -> None:
        self.reason = reason
        super().__init__(reason.value)


@dataclass(frozen=True, slots=True)
class TrustedUtcInterval:
    """Closed conservative interval of possible UTC epoch seconds."""

    earliest: float
    latest: float


@dataclass(frozen=True, slots=True)
class TrustedBuckets:
    epoch_minute: int
    utc_day: str


class DynamoDbClient(Protocol):
    def describe_table(self, *, TableName: str) -> dict[str, object]: ...


@dataclass(frozen=True, slots=True)
class _Sample:
    interval_at_receive: TrustedUtcInterval
    monotonic_receive: float
    wall_receive: float


class PreviewTrustedClock:
    HTTP_DATE_PRECISION_SECONDS = 1.0

    def __init__(
        self,
        *,
        dynamodb_client: DynamoDbClient,
        table_name: str,
        monotonic_clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        refresh_after_seconds: float = 30.0,
        stale_after_seconds: float = 90.0,
        max_clock_anomaly_seconds: float = 2.0,
    ) -> None:
        numeric_config = (
            refresh_after_seconds,
            stale_after_seconds,
            max_clock_anomaly_seconds,
        )
        if (
            type(table_name) is not str
            or not table_name
            or table_name != table_name.strip()
            or not all(
                type(value) in (int, float) and math.isfinite(value)
                for value in numeric_config
            )
            or refresh_after_seconds <= 0
            or refresh_after_seconds > 30
            or stale_after_seconds <= refresh_after_seconds
            or stale_after_seconds > 90
            or max_clock_anomaly_seconds < 0
            or max_clock_anomaly_seconds > 2
        ):
            raise ValueError("invalid trusted clock configuration")
        self._client = dynamodb_client
        self._table_name = table_name
        self._monotonic = monotonic_clock
        self._wall = wall_clock
        self._refresh_after = refresh_after_seconds
        self._stale_after = stale_after_seconds
        self._max_anomaly = max_clock_anomaly_seconds
        self._sample: _Sample | None = None
        self._lock = threading.RLock()

    def refresh(self) -> TrustedUtcInterval:
        """Replace the sample using an authenticated DynamoDB response."""

        with self._lock:
            return self._refresh_locked()

    def _refresh_locked(self) -> TrustedUtcInterval:
        previous = self._sample
        # A failed refresh must not leave an older sample available to a caller
        # that catches the error.
        self._sample = None
        sent = self._read_monotonic()
        try:
            response = self._client.describe_table(TableName=self._table_name)
        except Exception:
            raise TrustedClockUnavailable(TrustedClockFailure.AWS_ERROR) from None
        received = self._read_monotonic()
        wall_received = self._read_wall()
        if received < sent:
            raise TrustedClockUnavailable(TrustedClockFailure.MONOTONIC_ANOMALY)

        date_value = self._response_date(response)
        server_second = self._parse_http_date(date_value)
        round_trip = received - sent
        if not math.isfinite(round_trip):
            raise TrustedClockUnavailable(TrustedClockFailure.MONOTONIC_ANOMALY)
        interval = TrustedUtcInterval(
            earliest=server_second,
            latest=server_second + self.HTTP_DATE_PRECISION_SECONDS + round_trip,
        )

        if previous is not None:
            monotonic_elapsed = received - previous.monotonic_receive
            if monotonic_elapsed < 0:
                raise TrustedClockUnavailable(
                    TrustedClockFailure.MONOTONIC_ANOMALY
                )
            wall_elapsed = wall_received - previous.wall_receive
            if abs(wall_elapsed - monotonic_elapsed) > self._max_anomaly:
                raise TrustedClockUnavailable(TrustedClockFailure.WALL_CLOCK_ANOMALY)

        if not self._wall_within_interval_bound(wall_received, interval):
            raise TrustedClockUnavailable(TrustedClockFailure.WALL_CLOCK_ANOMALY)

        self._sample = _Sample(interval, received, wall_received)
        return interval

    def needs_refresh(self) -> bool:
        with self._lock:
            sample = self._sample
            if sample is None:
                return True
            elapsed = self._elapsed(sample)
            return elapsed > self._refresh_after or math.isclose(
                elapsed, self._refresh_after, abs_tol=1e-9
            )

    def trusted_interval(self) -> TrustedUtcInterval:
        with self._lock:
            return self._trusted_interval_locked()

    def _trusted_interval_locked(self) -> TrustedUtcInterval:
        sample = self._sample
        if sample is None:
            raise TrustedClockUnavailable(TrustedClockFailure.NO_SAMPLE)
        elapsed = self._elapsed(sample)
        if elapsed > self._stale_after:
            raise TrustedClockUnavailable(TrustedClockFailure.STALE)
        if elapsed > self._refresh_after or math.isclose(
            elapsed, self._refresh_after, abs_tol=1e-9
        ):
            raise TrustedClockUnavailable(TrustedClockFailure.REFRESH_OVERDUE)

        wall_now = self._read_wall()
        expected_wall = sample.wall_receive + elapsed
        wall_elapsed = wall_now - sample.wall_receive
        interval = TrustedUtcInterval(
            earliest=sample.interval_at_receive.earliest + elapsed,
            latest=sample.interval_at_receive.latest + elapsed,
        )
        if (
            not math.isfinite(wall_now)
            or wall_elapsed < 0
            or abs(wall_now - expected_wall) > self._max_anomaly
            or not self._wall_within_interval_bound(wall_now, interval)
        ):
            raise TrustedClockUnavailable(TrustedClockFailure.WALL_CLOCK_ANOMALY)
        return interval

    def buckets(self) -> TrustedBuckets:
        with self._lock:
            interval = self._trusted_interval_locked()
            try:
                earliest_minute = math.floor(interval.earliest / 60)
                latest_minute = math.floor(interval.latest / 60)
                earliest_day = datetime.fromtimestamp(interval.earliest, UTC).strftime(
                    "%Y%m%d"
                )
                latest_day = datetime.fromtimestamp(interval.latest, UTC).strftime(
                    "%Y%m%d"
                )
            except (OverflowError, OSError, ValueError):
                raise TrustedClockUnavailable(
                    TrustedClockFailure.TIME_RANGE_ERROR
                ) from None
            if earliest_minute != latest_minute or earliest_day != latest_day:
                raise TrustedClockUnavailable(TrustedClockFailure.AMBIGUOUS_BUCKET)
            return TrustedBuckets(epoch_minute=earliest_minute, utc_day=earliest_day)

    def _elapsed(self, sample: _Sample) -> float:
        now = self._read_monotonic()
        elapsed = now - sample.monotonic_receive
        if not math.isfinite(elapsed) or elapsed < 0:
            raise TrustedClockUnavailable(TrustedClockFailure.MONOTONIC_ANOMALY)
        return elapsed

    def _read_monotonic(self) -> float:
        try:
            value = self._monotonic()
        except Exception:
            raise TrustedClockUnavailable(
                TrustedClockFailure.MONOTONIC_ANOMALY
            ) from None
        if type(value) not in (int, float) or not math.isfinite(value):
            raise TrustedClockUnavailable(TrustedClockFailure.MONOTONIC_ANOMALY)
        return float(value)

    def _read_wall(self) -> float:
        try:
            value = self._wall()
        except Exception:
            raise TrustedClockUnavailable(
                TrustedClockFailure.WALL_CLOCK_ANOMALY
            ) from None
        if type(value) not in (int, float) or not math.isfinite(value):
            raise TrustedClockUnavailable(TrustedClockFailure.WALL_CLOCK_ANOMALY)
        return float(value)

    def _wall_within_interval_bound(
        self, wall: float, interval: TrustedUtcInterval
    ) -> bool:
        return max(
            abs(wall - interval.earliest),
            abs(wall - interval.latest),
        ) <= self._max_anomaly

    @staticmethod
    def _response_date(response: dict[str, object]) -> str:
        # botocore's documented response layers are concrete dicts.  Requiring
        # those prevents attacker-controlled Mapping methods from executing
        # during error handling.
        try:
            if type(response) is not dict:
                raise TypeError
            metadata = response.get("ResponseMetadata")
            if type(metadata) is not dict:
                raise TypeError
            headers = metadata.get("HTTPHeaders")
            if type(headers) is not dict:
                raise TypeError
            for key, value in headers.items():
                if type(key) is str and key.lower() == "date":
                    if type(value) is str and value:
                        return value
                    break
        except Exception:
            raise TrustedClockUnavailable(
                TrustedClockFailure.MISSING_DATE
            ) from None
        raise TrustedClockUnavailable(TrustedClockFailure.MISSING_DATE)

    @staticmethod
    def _parse_http_date(value: str) -> float:
        try:
            parsed = datetime.strptime(
                value, "%a, %d %b %Y %H:%M:%S GMT"
            ).replace(tzinfo=UTC)
            # ``strptime`` does not validate that the weekday matches the date.
            if parsed.strftime("%a, %d %b %Y %H:%M:%S GMT") != value:
                raise ValueError
            timestamp = parsed.timestamp()
            if not math.isfinite(timestamp):
                raise ValueError
            return timestamp
        except Exception:
            raise TrustedClockUnavailable(
                TrustedClockFailure.MALFORMED_DATE
            ) from None
