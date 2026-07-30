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
    BoundAdmissionRequest,
    DurableQuotaKeyLifecycleAuthority,
    MIN_OVERLAP_SECONDS,
    LifecycleMode,
    ObservabilityDigest,
    ObservabilityKey,
    QuotaIdentityDigests,
    QuotaKey,
    QuotaKeyLifecycle,
    QuotaKeyLifecycleAuthority,
    QuotaKeyMaterialProvider,
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


class DurableClockClient(ClockClient):
    def __init__(self, second: int):
        super().__init__(second)
        self.item = None
        self.puts = []
        self.lose_response = False

    def get_item(self, **kwargs):
        assert kwargs["ConsistentRead"] is True
        return {} if self.item is None else {"Item": self.item}

    def put_item(self, **kwargs):
        self.puts.append(kwargs)
        self.item = kwargs["Item"]
        if self.lose_response:
            raise RuntimeError("response lost")
        return {}


def _authority(second: int = 2_000_000_000):
    client = ClockClient(second)
    clock = PreviewTrustedClock(
        dynamodb_client=client,
        table_name="preview-store",
        monotonic_clock=lambda: 0.0,
        wall_clock=lambda: float(client.second),
    )
    return client, QuotaKeyLifecycleAuthority(
        clock, key_material_provider=_KEY_PROVIDER
    )


def _key(version: int, activated: int, *, previous: bool = False) -> QuotaKey:
    return _KEY_PROVIDER.verify(
        version=version,
        key_id=f"quota-{version}",
        key_bytes=bytes((index + version) % 256 for index in range(32)),
        activated=activated - (100 if previous else 0),
        source_revision=f"ssm-v{version}",
        superseded=activated if previous else None,
        retire_not_before=activated + MIN_OVERLAP_SECONDS if previous else None,
        authenticated_revision=True,
        csprng_provenance=True,
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
    assert type(request) is BoundAdmissionRequest
    request = request.request

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
        b"canonical-user", ObservabilityKey(1, b"o" * 32)
    )
    with pytest.raises(ValueError):
        ObservabilityDigest.derive(b"canonical-user", snapshot.lifecycle.current)  # type: ignore[arg-type]
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
    request = request.request
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
            _KEY_PROVIDER.verify(
                version=2,
                key_id="quota-2",
                key_bytes=b"x" * 31,
                activated=value.current.activated,
                source_revision="ssm-v2",
                authenticated_revision=True,
                csprng_provenance=True,
            ),
            value.previous,
        ),
    ],
)
def test_malformed_or_weak_lifecycle_fails_closed(mutator):
    lifecycle = _overlap()
    with pytest.raises(ValueError):
        mutator(lifecycle)


def replace_previous(current: QuotaKey) -> QuotaKey:
    return _KEY_PROVIDER.verify(
        version=current.version,
        key_id=current.key_id,
        key_bytes=current.key_bytes,
        activated=current.activated - 1,
        source_revision=current.source_revision,
        superseded=current.activated,
        retire_not_before=current.activated + MIN_OVERLAP_SECONDS,
        authenticated_revision=True,
        csprng_provenance=True,
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
                _KEY_PROVIDER.verify(
                    version=2,
                    key_id="other",
                    key_bytes=bytes(range(32)),
                    activated=lifecycle.current.activated,
                    source_revision="ssm-other",
                    authenticated_revision=True,
                    csprng_provenance=True,
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


def test_bound_admission_is_nominal_exact_authority_and_rotation_bound():
    _, authority = _authority()
    first = QuotaKeyLifecycle(
        1,
        TrustedUtcInterval(1_999_999_800, 1_999_999_801),
        _key(1, 1_999_999_900),
    )
    snapshot = authority.install(first)
    bound = authority.bind_admission(
        _request(), authority.derive(snapshot, b"canonical-user")
    )

    assert type(bound) is BoundAdmissionRequest
    assert authority.validate_admission(bound) is bound.request
    forged = BoundAdmissionRequest()
    assert authority.validate_admission(forged) is None

    _, other = _authority()
    other.install(first)
    assert other.validate_admission(bound) is None

    authority.install(_overlap())
    assert authority.validate_admission(bound) is None


def test_snapshot_freshness_strictly_closes_after_90_seconds():
    client, authority = _authority()
    authority.install(_overlap())
    client.second += 91
    authority._clock._monotonic = lambda: 91.0
    authority._clock._wall = lambda: float(client.second)

    with pytest.raises(ValueError):
        authority.snapshot()


def test_durable_authority_bootstrap_overlap_and_exact_response_loss_proof():
    client = DurableClockClient(2_000_000_000)
    clock = PreviewTrustedClock(
        dynamodb_client=client,
        table_name="preview-store",
        monotonic_clock=lambda: 0.0,
        wall_clock=lambda: float(client.second),
    )
    authority = DurableQuotaKeyLifecycleAuthority(
        clock,
        dynamodb_client=client,
        table_name="preview-store",
        key_material_provider=_KEY_PROVIDER,
    )
    bootstrap = QuotaKeyLifecycle(
        1,
        TrustedUtcInterval(1_999_999_920, 1_999_999_921),
        _key(1, 1_999_999_922),
    )
    authority.install(bootstrap)
    assert "key_bytes" not in repr(client.item)
    assert all("fingerprint" in key or key not in {"key", "digest"} for key in client.item)

    activated = 1_999_999_950
    transition = QuotaKeyLifecycle(
        2,
        TrustedUtcInterval(activated - 2, activated - 1),
        _key(2, activated),
        _key(1, activated, previous=True),
    )
    client.lose_response = True
    snapshot = authority.install(transition)
    assert snapshot.lifecycle == transition
    assert client.puts[-1]["ExpressionAttributeValues"][":generation"] == {"N": "1"}


def test_durable_authority_allows_exact_attach_but_rejects_skip_overlap():
    client = DurableClockClient(2_000_000_000)
    clock = PreviewTrustedClock(
        dynamodb_client=client,
        table_name="preview-store",
        monotonic_clock=lambda: 0.0,
        wall_clock=lambda: float(client.second),
    )
    authority = DurableQuotaKeyLifecycleAuthority(
        clock,
        dynamodb_client=client,
        table_name="preview-store",
        key_material_provider=_KEY_PROVIDER,
    )
    bootstrap = QuotaKeyLifecycle(
        1,
        TrustedUtcInterval(1_999_999_920, 1_999_999_921),
        _key(1, 1_999_999_922),
    )
    authority.install(bootstrap)
    assert authority.install(bootstrap).lifecycle == bootstrap
    with pytest.raises(ValueError):
        authority.install(
            QuotaKeyLifecycle(
                2,
                TrustedUtcInterval(1_999_999_930, 1_999_999_931),
                _key(2, 1_999_999_932),
            )
        )
_KEY_PROVIDER = QuotaKeyMaterialProvider()


def test_key_material_is_provider_bound_and_revision_stable():
    provider = QuotaKeyMaterialProvider()
    with pytest.raises(TypeError):
        QuotaKey()  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="unverified"):
        provider.verify(
            version=1,
            key_id="quota-1",
            key_bytes=bytes(range(32)),
            activated=1,
            source_revision="revision-1",
            authenticated_revision=False,
            csprng_provenance=True,
        )
    provider.verify(
        version=1,
        key_id="quota-1",
        key_bytes=bytes(range(32)),
        activated=1,
        source_revision="revision-1",
        authenticated_revision=True,
        csprng_provenance=True,
    )
    with pytest.raises(ValueError, match="rebound"):
        provider.verify(
            version=1,
            key_id="quota-1",
            key_bytes=bytes(range(1, 33)),
            activated=1,
            source_revision="revision-1",
            authenticated_revision=True,
            csprng_provenance=True,
        )


def test_authority_rejects_another_provider_material():
    _, authority = _authority()
    other = QuotaKeyMaterialProvider()
    foreign = other.verify(
        version=1,
        key_id="quota-1",
        key_bytes=bytes(range(32)),
        activated=1_999_999_900,
        source_revision="foreign-revision",
        authenticated_revision=True,
        csprng_provenance=True,
    )
    with pytest.raises(ValueError, match="invalid quota lifecycle"):
        authority.install(
            QuotaKeyLifecycle(
                generation=1,
                issued=TrustedUtcInterval(
                    1_999_999_950, 1_999_999_951
                ),
                current=foreign,
            )
        )


def test_durable_authority_rejects_future_activation():
    client = DurableClockClient(2_000_000_000)
    clock = PreviewTrustedClock(
        dynamodb_client=client,
        table_name="preview-store",
        monotonic_clock=lambda: 0.0,
        wall_clock=lambda: float(client.second),
    )
    provider = QuotaKeyMaterialProvider()
    authority = DurableQuotaKeyLifecycleAuthority(
        clock,
        dynamodb_client=client,
        table_name="preview-store",
        key_material_provider=provider,
    )
    future = provider.verify(
        version=1,
        key_id="quota-future",
        key_bytes=bytes(range(32)),
        activated=2_000_000_001,
        source_revision="future-revision",
        authenticated_revision=True,
        csprng_provenance=True,
    )
    with pytest.raises(ValueError, match="stale lifecycle transition"):
        authority.install(
            QuotaKeyLifecycle(
                generation=1,
                issued=TrustedUtcInterval(
                    1_999_999_950, 1_999_999_951
                ),
                current=future,
            )
        )
