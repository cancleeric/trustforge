from __future__ import annotations

from datetime import UTC, datetime

import pytest

from trustforge.preview_admission_compiler import (
    build_counter_specs,
    compile_admission,
    decode_transact_get_responses,
)
from trustforge.preview_trusted_clock import PreviewTrustedClock, TrustedUtcInterval
from trustforge.quota_key_lifecycle import (
    MIN_OVERLAP_SECONDS,
    LifecycleMode,
    ObservabilityDigest,
    QuotaIdentityDigests,
    QuotaKey,
    QuotaKeyLifecycle,
    QuotaKeyLifecycleAuthority,
    QuotaLifecycleSnapshot,
)
from tests.test_preview_admission_compiler import request as _request


class ClockClient:
    def __init__(self, second: int):
        self.second = second

    def describe_table(self, **kwargs):
        del kwargs
        date = datetime.fromtimestamp(self.second, UTC).strftime(
            "%a, %d %b %Y %H:%M:%S GMT"
        )
        return {"ResponseMetadata": {"HTTPHeaders": {"date": date}}}


def _authority(second: int = 2_000_000_000):
    client = ClockClient(second)
    clock = PreviewTrustedClock(
        dynamodb_client=client,
        table_name="preview-store",
        monotonic_clock=lambda: 0.0,
        wall_clock=lambda: float(client.second),
    )
    return client, QuotaKeyLifecycleAuthority(clock)


def _key(version: int, activated: int, *, previous: bool = False) -> QuotaKey:
    return QuotaKey(
        version,
        f"quota-{version}",
        bytes([version]) * 32,
        activated - (100 if previous else 0),
        activated if previous else None,
        activated + MIN_OVERLAP_SECONDS if previous else None,
    )


def _overlap() -> QuotaKeyLifecycle:
    activated = 1_999_999_900
    return QuotaKeyLifecycle(
        generation=2,
        issued=TrustedUtcInterval(activated - 2, activated - 1),
        current=_key(2, activated),
        previous=_key(1, activated, previous=True),
    )


def test_overlap_derives_nominal_purpose_separated_digests_and_13_actions():
    _, authority = _authority()
    snapshot = authority.install(_overlap())
    digests = authority.derive(snapshot, b"canonical-user")
    request = authority.bind_admission(_request(), digests)

    assert snapshot.lifecycle.mode is LifecycleMode.OVERLAP
    assert digests.current != digests.previous
    assert len(build_counter_specs(request)) == 11
    responses = [{} for _ in range(12)]
    plan = compile_admission(
        request,
        "preview-store",
        decode_transact_get_responses(request, responses),
    )
    assert plan.action_count == 13
    observability = ObservabilityDigest.derive(
        b"canonical-user", snapshot.lifecycle.current
    )
    assert observability.value != digests.current
    rendered = f"{snapshot!r} {digests!r} {observability!r}"
    assert "canonical-user" not in rendered
    assert bytes([2]).hex() not in rendered
    assert digests.current not in rendered


def test_single_uses_one_identity_layout_and_global_once():
    _, authority = _authority()
    lifecycle = QuotaKeyLifecycle(
        1,
        TrustedUtcInterval(1_999_999_800, 1_999_999_801),
        _key(1, 1_999_999_900),
    )
    digests = authority.derive(
        authority.install(lifecycle), b"canonical-user"
    )
    request = authority.bind_admission(_request(), digests)
    specs = build_counter_specs(request)

    assert digests.previous is None
    assert len(specs) == 8
    assert sum(spec.kind == "preview_global_concurrency" for spec in specs) == 1


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: QuotaKeyLifecycle(
            value.generation,
            value.issued,
            value.current,
            replace_previous(value.current),
        ),
        lambda value: QuotaKeyLifecycle(
            value.generation,
            value.issued,
            QuotaKey(2, "quota-2", b"x" * 31, value.current.activated),
            value.previous,
        ),
    ],
)
def test_malformed_or_weak_lifecycle_fails_closed(mutator):
    lifecycle = _overlap()
    with pytest.raises(ValueError):
        mutator(lifecycle)


def replace_previous(current: QuotaKey) -> QuotaKey:
    return QuotaKey(
        current.version,
        current.key_id,
        current.key_bytes,
        current.activated - 1,
        current.activated,
        current.activated + MIN_OVERLAP_SECONDS,
    )


def test_generation_rollback_and_same_generation_conflict_rejected():
    _, authority = _authority()
    lifecycle = _overlap()
    authority.install(lifecycle)

    with pytest.raises(ValueError):
        authority.install(
            QuotaKeyLifecycle(
                1, lifecycle.issued, lifecycle.current, lifecycle.previous
            )
        )
    with pytest.raises(ValueError):
        authority.install(
            QuotaKeyLifecycle(
                2,
                lifecycle.issued,
                QuotaKey(
                    2,
                    "other",
                    b"z" * 32,
                    lifecycle.current.activated,
                ),
                lifecycle.previous,
            )
        )


def test_snapshot_and_digest_capabilities_cannot_be_forged_or_cross_authority():
    _, first = _authority()
    _, second = _authority()
    snapshot = first.install(_overlap())
    digests = first.derive(snapshot, b"canonical-user")

    with pytest.raises(TypeError):
        QuotaLifecycleSnapshot(_overlap(), snapshot.observed, object())  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        QuotaIdentityDigests("a" * 64, None, 1, 1, None, snapshot)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        second.derive(snapshot, b"canonical-user")
    with pytest.raises(ValueError):
        second.bind_admission(_request(), digests)


def test_snapshot_freshness_strictly_closes_after_90_seconds():
    client, authority = _authority()
    authority.install(_overlap())
    client.second += 91
    authority._clock._monotonic = lambda: 91.0
    authority._clock._wall = lambda: float(client.second)

    with pytest.raises(ValueError):
        authority.snapshot()

