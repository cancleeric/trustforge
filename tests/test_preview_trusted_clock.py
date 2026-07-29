from __future__ import annotations

from datetime import UTC, datetime
import threading
from typing import Any

import pytest

from trustforge.preview_trusted_clock import (
    PreviewTrustedClock,
    TrustedClockFailure,
    TrustedClockUnavailable,
)


class Clocks:
    def __init__(self, mono: float, wall: float) -> None:
        self.mono = mono
        self.wall = wall


class Client:
    def __init__(
        self, clocks: Clocks, date: str | None, *, rtt: float = 0.2
    ) -> None:
        self.clocks = clocks
        self.date = date
        self.rtt = rtt
        self.error: Exception | None = None

    def describe_table(self, *, TableName: str) -> dict[str, object]:
        assert TableName == "preview"
        if self.error:
            raise self.error
        self.clocks.mono += self.rtt
        headers = {} if self.date is None else {"date": self.date}
        return {"ResponseMetadata": {"HTTPHeaders": headers}}


def http_date(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")


def make_clock(
    epoch: float, *, wall_offset: float = 0, rtt: float = 0.2
) -> tuple[PreviewTrustedClock, Clocks, Client]:
    clocks = Clocks(100.0, epoch + rtt + wall_offset)
    client = Client(clocks, http_date(epoch), rtt=rtt)
    clock = PreviewTrustedClock(
        dynamodb_client=client,
        table_name="preview",
        monotonic_clock=lambda: clocks.mono,
        wall_clock=lambda: clocks.wall,
    )
    return clock, clocks, client


def reason(exc: pytest.ExceptionInfo[TrustedClockUnavailable]) -> TrustedClockFailure:
    return exc.value.reason


def test_date_rounding_and_rtt_form_conservative_interval() -> None:
    clock, _, _ = make_clock(1_800_000_010, rtt=0.75)
    assert clock.refresh().earliest == 1_800_000_010
    assert clock.trusted_interval().latest == 1_800_000_011.75


def test_only_wholly_unique_minute_and_day_return_buckets() -> None:
    epoch = 1_800_000_010
    clock, _, _ = make_clock(epoch, rtt=0)
    clock.refresh()
    buckets = clock.buckets()
    assert buckets.epoch_minute == epoch // 60
    assert buckets.utc_day == datetime.fromtimestamp(epoch, UTC).strftime("%Y%m%d")


def test_prior_server_second_with_receive_after_minute_boundary_fails_closed() -> None:
    boundary = datetime(2030, 1, 2, 3, 4, tzinfo=UTC).timestamp()
    clock, _, _ = make_clock(boundary - 1, rtt=1.2)
    clock.refresh()
    with pytest.raises(TrustedClockUnavailable) as exc:
        clock.buckets()
    assert reason(exc) is TrustedClockFailure.AMBIGUOUS_BUCKET


def test_server_date_wholly_after_minute_boundary_uses_new_bucket() -> None:
    boundary = datetime(2030, 1, 2, 3, 4, tzinfo=UTC).timestamp()
    clock, _, _ = make_clock(boundary, rtt=0.2)
    clock.refresh()
    assert clock.buckets().epoch_minute == boundary // 60


def test_prior_server_second_with_receive_after_day_boundary_fails_closed() -> None:
    midnight = datetime(2030, 1, 2, tzinfo=UTC).timestamp()
    clock, _, _ = make_clock(midnight - 1, rtt=1.2)
    clock.refresh()
    with pytest.raises(TrustedClockUnavailable) as exc:
        clock.buckets()
    assert reason(exc) is TrustedClockFailure.AMBIGUOUS_BUCKET


def test_server_date_wholly_after_day_boundary_uses_new_day() -> None:
    midnight = datetime(2030, 1, 2, tzinfo=UTC).timestamp()
    clock, _, _ = make_clock(midnight, rtt=0.2)
    clock.refresh()
    assert clock.buckets().utc_day == "20300102"


@pytest.mark.parametrize("wall_offset", [-3.0, 3.0])
def test_opposite_wall_skew_fails_closed(wall_offset: float) -> None:
    clock, _, _ = make_clock(1_800_000_010, wall_offset=wall_offset)
    with pytest.raises(TrustedClockUnavailable) as exc:
        clock.refresh()
    assert reason(exc) is TrustedClockFailure.WALL_CLOCK_ANOMALY


def test_refresh_due_at_30_seconds_and_stale_after_90() -> None:
    clock, clocks, _ = make_clock(1_800_000_010)
    clock.refresh()
    clocks.mono += 30
    clocks.wall += 30
    assert clock.needs_refresh()
    with pytest.raises(TrustedClockUnavailable) as exc:
        clock.trusted_interval()
    assert reason(exc) is TrustedClockFailure.REFRESH_OVERDUE
    clocks.mono += 61
    clocks.wall += 61
    with pytest.raises(TrustedClockUnavailable) as stale:
        clock.trusted_interval()
    assert reason(stale) is TrustedClockFailure.STALE


@pytest.mark.parametrize("wall_change", [-1.0, 5.0])
def test_wall_step_or_backward_fails_closed(wall_change: float) -> None:
    clock, clocks, _ = make_clock(1_800_000_010)
    clock.refresh()
    clocks.mono += 1
    clocks.wall += wall_change
    with pytest.raises(TrustedClockUnavailable) as exc:
        clock.trusted_interval()
    assert reason(exc) is TrustedClockFailure.WALL_CLOCK_ANOMALY


def test_monotonic_reset_fails_closed() -> None:
    clock, clocks, _ = make_clock(1_800_000_010)
    clock.refresh()
    clocks.mono = 0
    with pytest.raises(TrustedClockUnavailable) as exc:
        clock.trusted_interval()
    assert reason(exc) is TrustedClockFailure.MONOTONIC_ANOMALY


def test_restart_without_sample_fails_closed() -> None:
    clock, _, _ = make_clock(1_800_000_010)
    with pytest.raises(TrustedClockUnavailable) as exc:
        clock.buckets()
    assert reason(exc) is TrustedClockFailure.NO_SAMPLE


@pytest.mark.parametrize("date", [None, "", "not an HTTP date"])
def test_missing_or_malformed_date_fails_closed(date: str | None) -> None:
    clock, _, client = make_clock(1_800_000_010)
    client.date = date
    with pytest.raises(TrustedClockUnavailable) as exc:
        clock.refresh()
    expected = (
        TrustedClockFailure.MISSING_DATE
        if date is None or date == ""
        else TrustedClockFailure.MALFORMED_DATE
    )
    assert reason(exc) is expected


def test_aws_error_fails_closed_without_provider_detail() -> None:
    clock, _, client = make_clock(1_800_000_010)
    client.error = RuntimeError("secret provider detail")
    with pytest.raises(TrustedClockUnavailable) as exc:
        clock.refresh()
    assert reason(exc) is TrustedClockFailure.AWS_ERROR
    assert "secret provider detail" not in str(exc.value)
    assert exc.value.__cause__ is None
    with pytest.raises(TrustedClockUnavailable) as no_sample:
        clock.buckets()
    assert reason(no_sample) is TrustedClockFailure.NO_SAMPLE


def test_failed_refresh_invalidates_previous_sample() -> None:
    clock, _, client = make_clock(1_800_000_010)
    clock.refresh()
    client.error = RuntimeError("backend unavailable")
    with pytest.raises(TrustedClockUnavailable):
        clock.refresh()
    with pytest.raises(TrustedClockUnavailable) as exc:
        clock.buckets()
    assert reason(exc) is TrustedClockFailure.NO_SAMPLE


def test_non_imf_fixdate_is_rejected() -> None:
    clock, _, client = make_clock(1_800_000_010)
    client.date = "Sunday, 06-Nov-94 08:49:37 GMT"
    with pytest.raises(TrustedClockUnavailable) as exc:
        clock.refresh()
    assert reason(exc) is TrustedClockFailure.MALFORMED_DATE


def test_timestamp_beyond_datetime_range_fails_closed() -> None:
    last_second = datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC).timestamp()
    clock, _, _ = make_clock(last_second, rtt=1.2)
    clock.refresh()
    with pytest.raises(TrustedClockUnavailable) as exc:
        clock.buckets()
    assert reason(exc) is TrustedClockFailure.TIME_RANGE_ERROR


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf")])
def test_non_finite_monotonic_clock_fails_closed(bad_value: float) -> None:
    clock, clocks, _ = make_clock(1_800_000_010)
    clocks.mono = bad_value
    with pytest.raises(TrustedClockUnavailable) as exc:
        clock.refresh()
    assert reason(exc) is TrustedClockFailure.MONOTONIC_ANOMALY


def test_non_finite_wall_clock_fails_closed() -> None:
    clock, clocks, _ = make_clock(1_800_000_010)
    clocks.wall = float("inf")
    with pytest.raises(TrustedClockUnavailable) as exc:
        clock.refresh()
    assert reason(exc) is TrustedClockFailure.WALL_CLOCK_ANOMALY


def test_clock_callable_exception_is_typed_and_suppressed() -> None:
    clocks = Clocks(100, 1_800_000_010)
    client = Client(clocks, http_date(1_800_000_010))

    def broken_clock() -> float:
        raise RuntimeError("clock implementation detail")

    clock = PreviewTrustedClock(
        dynamodb_client=client,
        table_name="preview",
        monotonic_clock=broken_clock,
        wall_clock=lambda: clocks.wall,
    )
    with pytest.raises(TrustedClockUnavailable) as exc:
        clock.refresh()
    assert reason(exc) is TrustedClockFailure.MONOTONIC_ANOMALY
    assert exc.value.__cause__ is None


def test_non_finite_configuration_is_rejected() -> None:
    clocks = Clocks(100, 1_800_000_010)
    client = Client(clocks, http_date(1_800_000_010))
    with pytest.raises(ValueError):
        PreviewTrustedClock(
            dynamodb_client=client,
            table_name="preview",
            monotonic_clock=lambda: clocks.mono,
            wall_clock=lambda: clocks.wall,
            refresh_after_seconds=float("nan"),
        )


def test_bool_configuration_is_rejected() -> None:
    clocks = Clocks(100, 1_800_000_010)
    client = Client(clocks, http_date(1_800_000_010))
    with pytest.raises(ValueError):
        PreviewTrustedClock(
            dynamodb_client=client,
            table_name="preview",
            monotonic_clock=lambda: clocks.mono,
            wall_clock=lambda: clocks.wall,
            refresh_after_seconds=True,
        )


@pytest.mark.parametrize("table_name", [None, False, 123, "", "   ", " preview", "preview "])
def test_table_name_must_be_canonical_non_empty_string(table_name: object) -> None:
    clocks = Clocks(100, 1_800_000_010)
    client = Client(clocks, http_date(1_800_000_010))
    with pytest.raises(ValueError):
        PreviewTrustedClock(
            dynamodb_client=client,
            table_name=table_name,  # type: ignore[arg-type]
            monotonic_clock=lambda: clocks.mono,
            wall_clock=lambda: clocks.wall,
        )


def test_malformed_aws_response_shape_fails_closed() -> None:
    clock, _, client = make_clock(1_800_000_010)
    client.describe_table = lambda **_: None  # type: ignore[method-assign]
    with pytest.raises(TrustedClockUnavailable) as exc:
        clock.refresh()
    assert reason(exc) is TrustedClockFailure.MISSING_DATE


class HostileDict(dict[str, Any]):
    def get(self, *_: object, **__: object) -> Any:
        raise RuntimeError("hostile get detail")

    def items(self) -> Any:
        raise RuntimeError("hostile items detail")


@pytest.mark.parametrize(
    "response",
    [
        HostileDict(),
        {"ResponseMetadata": HostileDict()},
        {"ResponseMetadata": {"HTTPHeaders": HostileDict()}},
    ],
)
def test_hostile_mapping_layers_fail_closed_without_invocation(
    response: dict[str, Any],
) -> None:
    clock, _, client = make_clock(1_800_000_010)
    client.describe_table = lambda **_: response  # type: ignore[method-assign]
    with pytest.raises(TrustedClockUnavailable) as exc:
        clock.refresh()
    assert reason(exc) is TrustedClockFailure.MISSING_DATE
    assert exc.value.__cause__ is None


@pytest.mark.parametrize(("mono", "wall"), [(True, 1_800_000_010), (100, False)])
def test_bool_clock_values_fail_closed(mono: object, wall: object) -> None:
    clocks = Clocks(mono, wall)  # type: ignore[arg-type]
    client = Client(clocks, http_date(1_800_000_010), rtt=0)
    clock = PreviewTrustedClock(
        dynamodb_client=client,
        table_name="preview",
        monotonic_clock=lambda: clocks.mono,
        wall_clock=lambda: clocks.wall,
    )
    with pytest.raises(TrustedClockUnavailable) as exc:
        clock.refresh()
    assert reason(exc) in {
        TrustedClockFailure.MONOTONIC_ANOMALY,
        TrustedClockFailure.WALL_CLOCK_ANOMALY,
    }


def test_minimum_http_date_never_raises_raw_range_exception() -> None:
    clocks = Clocks(100, datetime(1, 1, 1, tzinfo=UTC).timestamp())
    client = Client(clocks, "Mon, 01 Jan 0001 00:00:00 GMT", rtt=0)
    clock = PreviewTrustedClock(
        dynamodb_client=client,
        table_name="preview",
        monotonic_clock=lambda: clocks.mono,
        wall_clock=lambda: clocks.wall,
    )
    try:
        clock.refresh()
        clock.buckets()
    except TrustedClockUnavailable:
        pass


def test_opposite_accepted_wall_skews_produce_identical_buckets() -> None:
    epoch = datetime(2030, 1, 2, 3, 4, 10, tzinfo=UTC).timestamp()
    first, _, _ = make_clock(epoch, wall_offset=1.7)
    second, _, _ = make_clock(epoch, wall_offset=-0.9)
    first.refresh()
    second.refresh()
    assert first.buckets() == second.buckets()


@pytest.mark.parametrize("wall_offset", [-0.9, 0.7])
def test_boundary_ambiguity_is_independent_of_accepted_wall_skew(
    wall_offset: float,
) -> None:
    boundary = datetime(2030, 1, 2, 3, 4, tzinfo=UTC).timestamp()
    clock, _, _ = make_clock(boundary - 1, wall_offset=wall_offset, rtt=1.2)
    clock.refresh()
    with pytest.raises(TrustedClockUnavailable) as exc:
        clock.buckets()
    assert reason(exc) is TrustedClockFailure.AMBIGUOUS_BUCKET


def test_wide_rtt_uncertainty_fails_wall_bound() -> None:
    clock, _, _ = make_clock(1_800_000_010, rtt=3)
    with pytest.raises(TrustedClockUnavailable) as exc:
        clock.refresh()
    assert reason(exc) is TrustedClockFailure.WALL_CLOCK_ANOMALY


@pytest.mark.parametrize("wall_offset", [-0.99, 1.79])
def test_wall_near_each_accepted_endpoint_extreme(wall_offset: float) -> None:
    clock, _, _ = make_clock(1_800_000_010, wall_offset=wall_offset, rtt=0.2)
    clock.refresh()
    clock.buckets()


class PausingRefreshClient(Client):
    def __init__(self, clocks: Clocks, date: str) -> None:
        super().__init__(clocks, date, rtt=0)
        self.pause = False
        self.fail = False
        self.entered = threading.Event()
        self.release = threading.Event()

    def describe_table(self, *, TableName: str) -> dict[str, object]:
        if self.pause:
            self.entered.set()
            assert self.release.wait(timeout=2)
        if self.fail:
            raise RuntimeError("refresh failure detail")
        return super().describe_table(TableName=TableName)


def test_failed_refresh_blocks_reader_then_reader_sees_no_sample() -> None:
    epoch = 1_800_000_010
    clocks = Clocks(100, epoch)
    client = PausingRefreshClient(clocks, http_date(epoch))
    clock = PreviewTrustedClock(
        dynamodb_client=client,
        table_name="preview",
        monotonic_clock=lambda: clocks.mono,
        wall_clock=lambda: clocks.wall,
    )
    clock.refresh()
    client.pause = True
    client.fail = True
    refresh_result: list[TrustedClockFailure] = []
    reader_result: list[object] = []
    reader_started = threading.Event()
    reader_done = threading.Event()

    def refresh_worker() -> None:
        try:
            clock.refresh()
        except TrustedClockUnavailable as exc:
            refresh_result.append(exc.reason)

    def reader_worker() -> None:
        reader_started.set()
        try:
            reader_result.append(clock.buckets())
        except TrustedClockUnavailable as exc:
            reader_result.append(exc.reason)
        finally:
            reader_done.set()

    refresh_thread = threading.Thread(target=refresh_worker)
    refresh_thread.start()
    assert client.entered.wait(timeout=2)
    reader_thread = threading.Thread(target=reader_worker)
    reader_thread.start()
    assert reader_started.wait(timeout=2)
    assert not reader_done.wait(timeout=0.02)
    client.release.set()
    refresh_thread.join(timeout=2)
    reader_thread.join(timeout=2)
    assert not refresh_thread.is_alive()
    assert not reader_thread.is_alive()
    assert refresh_result == [TrustedClockFailure.AWS_ERROR]
    assert reader_result == [TrustedClockFailure.NO_SAMPLE]


def test_successful_refresh_blocks_reader_until_new_sample_is_installed() -> None:
    epoch = 1_800_000_010
    clocks = Clocks(100, epoch)
    client = PausingRefreshClient(clocks, http_date(epoch))
    clock = PreviewTrustedClock(
        dynamodb_client=client,
        table_name="preview",
        monotonic_clock=lambda: clocks.mono,
        wall_clock=lambda: clocks.wall,
    )
    clock.refresh()
    client.pause = True
    reader_result: list[object] = []
    reader_started = threading.Event()
    reader_done = threading.Event()

    def reader_worker() -> None:
        reader_started.set()
        reader_result.append(clock.buckets())
        reader_done.set()

    refresh_thread = threading.Thread(target=clock.refresh)
    refresh_thread.start()
    assert client.entered.wait(timeout=2)
    reader_thread = threading.Thread(target=reader_worker)
    reader_thread.start()
    assert reader_started.wait(timeout=2)
    assert not reader_done.wait(timeout=0.02)
    client.release.set()
    refresh_thread.join(timeout=2)
    reader_thread.join(timeout=2)
    assert not refresh_thread.is_alive()
    assert not reader_thread.is_alive()
    assert len(reader_result) == 1
    assert reader_result[0] == clock.buckets()
