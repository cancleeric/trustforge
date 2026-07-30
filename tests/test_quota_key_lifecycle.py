from __future__ import annotations

from datetime import UTC, datetime
import base64
from types import SimpleNamespace

import pytest
import boto3
from botocore.config import Config
from botocore.stub import Stubber

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
    AwsSsmQuotaKeyMaterialProvider,
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


class FakeSsmClient:
    def __init__(self, values: dict[int, bytes] | None = None):
        self.meta = SimpleNamespace(
            config=SimpleNamespace(retries={"total_max_attempts": 1})
        )
        self.values = values or {
            version: bytes(
                (index + version) % 256 for index in range(32)
            )
            for version in range(1, 5)
        }
        self.calls = []
        self.mutate: str | None = None

    def get_parameter(self, **kwargs):
        self.calls.append(kwargs)
        requested = kwargs["Name"]
        version = int(requested.rsplit(":", 1)[1])
        parameter = {
            "Name": requested.rsplit(":", 1)[0],
            "ARN": (
                "arn:aws:ssm:us-east-1:123:parameter"
                f"{requested.rsplit(':', 1)[0]}"
            ),
            "Type": "SecureString",
            "Version": version,
            "Value": base64.b64encode(self.values[version]).decode(),
            "Selector": f":{version}",
            "LastModifiedDate": datetime(
                2026, 7, version, tzinfo=UTC
            ),
            "DataType": "text",
        }
        if self.mutate is not None:
            parameter[self.mutate] = "wrong"
        return {
            "ResponseMetadata": {
                "HTTPStatusCode": 200,
                "RequestId": "request-1",
            },
            "Parameter": parameter,
        }


def _provider(
    values: dict[int, bytes] | None = None,
) -> AwsSsmQuotaKeyMaterialProvider:
    return AwsSsmQuotaKeyMaterialProvider(FakeSsmClient(values))


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
    loaded = _KEY_PROVIDER.load(
        parameter_name="/trustforge/quota",
        expected_version=version,
        key_id=f"quota-{version}",
    )
    return _KEY_PROVIDER.bind_lifecycle(
        loaded,
        activated=activated - (100 if previous else 0),
        superseded=activated if previous else None,
        retire_not_before=activated + MIN_OVERLAP_SECONDS if previous else None,
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
            _provider({2: b"x" * 31}).load(
                parameter_name="/trustforge/quota",
                expected_version=2,
                key_id="quota-2",
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
    return _KEY_PROVIDER.bind_lifecycle(
        current,
        activated=current.activated - 1,
        superseded=current.activated,
        retire_not_before=current.activated + MIN_OVERLAP_SECONDS,
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
                _KEY_PROVIDER.bind_lifecycle(
                    _KEY_PROVIDER.load(
                    parameter_name="/trustforge/quota",
                    expected_version=2,
                    key_id="other",
                    ),
                    activated=lifecycle.current.activated,
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
_KEY_PROVIDER = _provider()


def test_key_material_is_provider_bound_and_revision_stable():
    provider = _provider()
    with pytest.raises(TypeError):
        QuotaKey()  # type: ignore[call-arg]
    first = provider.load(
        parameter_name="/trustforge/quota",
        expected_version=1,
        key_id="quota-1",
    )
    assert first.source_revision.startswith("aws-ssm:arn:")
    assert "Value" not in repr(provider)
    assert first.key_bytes not in repr(first).encode()


@pytest.mark.parametrize("field", ["Name", "ARN", "Type", "Version", "Value"])
def test_ssm_loader_rejects_malformed_identity_and_secret(field):
    client = FakeSsmClient({1: bytes(range(32))})
    client.mutate = field
    provider = AwsSsmQuotaKeyMaterialProvider(client)
    with pytest.raises(ValueError, match="SSM quota key load failed"):
        provider.load(
            parameter_name="/trustforge/quota",
            expected_version=1,
            key_id="quota-1",
        )
    assert client.calls == [
        {
            "Name": "/trustforge/quota:1",
            "WithDecryption": True,
        }
    ]


def test_ssm_loader_restart_exact_attach_uses_same_aws_revision():
    ssm = FakeSsmClient({1: bytes(range(32))})
    first = AwsSsmQuotaKeyMaterialProvider(ssm)
    second = AwsSsmQuotaKeyMaterialProvider(ssm)
    first_key = first.load(
        parameter_name="/trustforge/quota",
        expected_version=1,
        key_id="quota-1",
    )
    second_key = second.load(
        parameter_name="/trustforge/quota",
        expected_version=1,
        key_id="quota-1",
    )
    assert first_key.source_revision == second_key.source_revision
    assert first_key.key_bytes == second_key.key_bytes
    dynamodb = DurableClockClient(2_000_000_000)
    issued = TrustedUtcInterval(1_999_999_950, 1_999_999_951)
    for provider, loaded in ((first, first_key), (second, second_key)):
        clock = PreviewTrustedClock(
            dynamodb_client=dynamodb,
            table_name="preview-store",
            monotonic_clock=lambda: 0.0,
            wall_clock=lambda: float(dynamodb.second),
        )
        authority = DurableQuotaKeyLifecycleAuthority(
            clock,
            dynamodb_client=dynamodb,
            table_name="preview-store",
            key_material_provider=provider,
        )
        snapshot = authority.install(
            QuotaKeyLifecycle(
                generation=1,
                issued=issued,
                current=provider.bind_lifecycle(
                    loaded, activated=1_999_999_960
                ),
            )
        )
        assert snapshot.lifecycle.current.source_revision == (
            first_key.source_revision
        )


def test_ssm_loader_requires_retry_bounded_low_level_client():
    client = FakeSsmClient()
    client.meta.config.retries["total_max_attempts"] = 2
    with pytest.raises(ValueError, match="retry-bounded"):
        AwsSsmQuotaKeyMaterialProvider(client)


def test_attach_existing_is_read_only_and_rejects_durable_mismatch():
    client = DurableClockClient(2_000_000_000)
    provider = _provider()
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
        key_material_provider=provider,
    )
    loaded = provider.load(
        parameter_name="/trustforge/quota",
        expected_version=1,
        key_id="quota-1",
    )
    lifecycle = QuotaKeyLifecycle(
        generation=1,
        issued=TrustedUtcInterval(1_999_999_950, 1_999_999_951),
        current=provider.bind_lifecycle(loaded, activated=1_999_999_960),
    )
    authority.install(lifecycle)
    client.puts.clear()
    authority.attach_existing(lifecycle)
    assert client.puts == []
    client.item = None
    with pytest.raises(ValueError, match="durable lifecycle mismatch"):
        authority.attach_existing(lifecycle)
    assert client.puts == []


def test_ssm_loader_accepts_botocore_get_parameter_shape():
    client = boto3.client(
        "ssm",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
        config=Config(retries={"total_max_attempts": 1}),
    )
    modified = datetime(2026, 7, 30, 1, 2, 3, tzinfo=UTC)
    with Stubber(client) as stubber:
        stubber.add_response(
            "get_parameter",
            {
                "Parameter": {
                    "Name": "/trustforge/quota",
                    "Type": "SecureString",
                    "Value": base64.b64encode(bytes(range(32))).decode(),
                    "Version": 1,
                    "Selector": ":1",
                    "SourceResult": "{}",
                    "LastModifiedDate": modified,
                    "ARN": (
                        "arn:aws:ssm:us-east-1:123456789012:"
                        "parameter/trustforge/quota"
                    ),
                    "DataType": "text",
                },
                "ResponseMetadata": {
                    "HTTPStatusCode": 200,
                    "RequestId": "shape-accurate",
                },
            },
            {
                "Name": "/trustforge/quota:1",
                "WithDecryption": True,
            },
        )
        loaded = AwsSsmQuotaKeyMaterialProvider(client).load(
            parameter_name="trustforge/quota",
            expected_version=1,
            key_id="quota-1",
        )
    assert loaded.source_revision.endswith(
        ":1:2026-07-30T01:02:03.000000+00:00"
    )


def test_authority_rejects_another_provider_material():
    _, authority = _authority()
    other = _provider()
    foreign = other.bind_lifecycle(
        other.load(
        parameter_name="/trustforge/quota",
        expected_version=1,
        key_id="quota-1",
        ),
        activated=1_999_999_900,
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
    provider = _provider()
    authority = DurableQuotaKeyLifecycleAuthority(
        clock,
        dynamodb_client=client,
        table_name="preview-store",
        key_material_provider=provider,
    )
    future = provider.bind_lifecycle(
        provider.load(
        parameter_name="/trustforge/quota",
        expected_version=1,
        key_id="quota-future",
        ),
        activated=2_000_000_001,
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
