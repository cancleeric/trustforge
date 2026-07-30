from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import threading
import uuid

import boto3
from botocore.exceptions import ClientError
from moto import mock_aws
import pytest

from trustforge.preview_admission_compiler import (
    AdmissionCompileRequest,
    build_counter_specs,
    compile_admission,
    decode_transact_get_responses,
)
from trustforge.preview_admission_store import reservation_key
from trustforge.preview_admission_store import circuit_key
from trustforge.preview_terminal_reconcile import (
    PreviewTerminalReconciler,
    TerminalDisposition,
    TerminalIntent,
    TerminalOutcome,
    _terminal_client_token,
    build_terminal_read_request,
    decode_terminal_responses,
)
from trustforge.preview_trusted_clock import TrustedBuckets, TrustedUtcInterval


POLICY = "a" * 64
OWNER = "b" * 64
IDENTITY = "c" * 64
PREVIOUS = "d" * 64


def _request(*, previous: str | None = None) -> AdmissionCompileRequest:
    return AdmissionCompileRequest(
        interval=TrustedUtcInterval(1_700_000_000.1, 1_700_000_000.2),
        buckets=TrustedBuckets(28_333_333, "20231114"),
        policy_digest=POLICY,
        owner_digest=OWNER,
        identity_digest=IDENTITY,
        previous_identity_digest=previous,
        reservation_id=str(uuid.uuid4()),
        reserved_tokens=100,
        reserved_micro_usd=200,
        lifecycle_generation=2 if previous else 1,
        current_quota_key_version=2 if previous else 1,
        previous_quota_key_version=1 if previous else None,
    )


def _create(client) -> None:
    client.create_table(
        TableName="preview-store",
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def _admit(client, request: AdmissionCompileRequest):
    read = {
        "TransactItems": [
            {
                "Get": {
                    "TableName": "preview-store",
                    "Key": {
                        name: {"S": value}
                        for name, value in circuit_key(
                            1, request.policy_digest
                        ).items()
                    },
                }
            },
            *[
                {
                    "Get": {
                        "TableName": "preview-store",
                        "Key": {
                            name: {"S": value}
                            for name, value in spec.key.items()
                        },
                    }
                }
                for spec in build_counter_specs(request)
            ],
        ]
    }
    snapshots = decode_transact_get_responses(
        request, client.transact_get_items(**read)["Responses"]
    )
    plan = compile_admission(request, "preview-store", snapshots)
    client.transact_write_items(**plan.transact_write_items_request())
    return plan.handle


def _seed_open_circuit(client, *, open_until=1_699_999_999):
    key = circuit_key(1, POLICY)
    client.put_item(
        TableName="preview-store",
        Item={
            "pk": {"S": key["pk"]},
            "sk": {"S": key["sk"]},
            "kind": {"S": "preview_circuit"},
            "schema_version": {"N": "1"},
            "state": {"S": "open"},
            "version": {"N": "7"},
            "failures": {
                "L": [{"N": str(value)} for value in range(1_699_999_995, 1_700_000_000)]
            },
            "open_until": {"N": str(open_until)},
        },
    )


def _native(client, key):
    return client.get_item(
        TableName="preview-store",
        Key={name: {"S": value} for name, value in key.items()},
        ConsistentRead=True,
    )["Item"]


def _number(item, field):
    return int(item[field]["N"])


class _CountingClient:
    def __init__(self, client):
        self.client = client
        self.reads = 0
        self.writes = 0

    def transact_get_items(self, **kwargs):
        self.reads += 1
        return self.client.transact_get_items(**kwargs)

    def transact_write_items(self, **kwargs):
        self.writes += 1
        return self.client.transact_write_items(**kwargs)


@pytest.mark.parametrize("half_open", [False, True])
@pytest.mark.parametrize("disposition", list(TerminalDisposition))
def test_terminal_interval_must_be_provably_after_admission(
    half_open, disposition
):
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        _create(client)
        if half_open:
            _seed_open_circuit(client)
        handle = _admit(client, _request())
        known = disposition in {
            TerminalDisposition.KNOWN_SUCCESS,
            TerminalDisposition.KNOWN_FAILURE,
        }

        with pytest.raises(ValueError, match="invalid terminal intent"):
            TerminalIntent(
                handle,
                TrustedUtcInterval(
                    handle.created_upper - 0.1, handle.created_upper
                ),
                disposition,
                actual_tokens=0 if known else None,
                actual_micro_usd=0 if known else None,
            )


@pytest.mark.parametrize("previous", [None, PREVIOUS])
def test_pre_provider_abort_refunds_requests_usage_and_all_concurrency(previous):
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        _create(client)
        request = _request(previous=previous)
        handle = _admit(client, request)
        intent = TerminalIntent(
            handle,
            TrustedUtcInterval(1_700_000_001, 1_700_000_002),
            TerminalDisposition.PRE_PROVIDER_ABORT,
        )

        result = PreviewTerminalReconciler(client, "preview-store").reconcile(intent)

        assert result.outcome is TerminalOutcome.RECONCILED
        assert not result.replay
        for spec in build_counter_specs(request):
            item = _native(client, spec.key)
            assert _number(item, "value") == 0
        reservation = _native(
            client,
            reservation_key(1, handle.expiry_shard, handle.reservation_id),
        )
        assert reservation["status"] == {"S": "terminal"}
        assert reservation["terminal_disposition"] == {"S": "pre_provider_abort"}


@pytest.mark.parametrize("previous", [None, PREVIOUS])
def test_known_success_keeps_requests_settles_down_and_releases_concurrency(
    previous,
):
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        _create(client)
        request = _request(previous=previous)
        handle = _admit(client, request)
        intent = TerminalIntent(
            handle,
            TrustedUtcInterval(1_700_000_001, 1_700_000_002),
            TerminalDisposition.KNOWN_SUCCESS,
            actual_tokens=30,
            actual_micro_usd=50,
        )

        result = PreviewTerminalReconciler(client, "preview-store").reconcile(intent)

        assert result.outcome is TerminalOutcome.RECONCILED
        values = {
            spec.kind: _number(_native(client, spec.key), "value")
            for spec in build_counter_specs(request)
        }
        assert values["preview_identity_minute"] == 1
        assert values["preview_identity_day"] == 1
        assert values["preview_identity_concurrency"] == 0
        assert values["preview_global_concurrency"] == 0
        assert values["preview_global_token_minute"] == 30
        assert values["preview_global_token_day"] == 30
        assert values["preview_global_usd_minute"] == 50
        assert values["preview_global_usd_day"] == 50
        for spec in build_counter_specs(request):
            if spec.kind.startswith("preview_identity_"):
                expected = 0 if spec.kind.endswith("concurrency") else 1
                assert _number(_native(client, spec.key), "value") == expected


@pytest.mark.parametrize("previous", [None, PREVIOUS])
def test_uncertain_keeps_all_usage_and_requests_but_releases_concurrency(previous):
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        _create(client)
        request = _request(previous=previous)
        handle = _admit(client, request)
        intent = TerminalIntent(
            handle,
            TrustedUtcInterval(1_700_000_001, 1_700_000_002),
            TerminalDisposition.UNCERTAIN,
        )

        assert (
            PreviewTerminalReconciler(client, "preview-store")
            .reconcile(intent)
            .outcome
            is TerminalOutcome.RECONCILED
        )
        values = {
            spec.kind: _number(_native(client, spec.key), "value")
            for spec in build_counter_specs(request)
        }
        assert values["preview_identity_minute"] == 1
        assert values["preview_identity_day"] == 1
        assert values["preview_identity_concurrency"] == 0
        assert values["preview_global_concurrency"] == 0
        assert values["preview_global_token_minute"] == 100
        assert values["preview_global_usd_minute"] == 200
        for spec in build_counter_specs(request):
            if spec.kind.startswith("preview_identity_"):
                expected = 0 if spec.kind.endswith("concurrency") else 1
                assert _number(_native(client, spec.key), "value") == expected


@pytest.mark.parametrize(
    "disposition,expected_state",
    [
        (TerminalDisposition.KNOWN_SUCCESS, "closed"),
        (TerminalDisposition.KNOWN_FAILURE, "open"),
        (TerminalDisposition.UNCERTAIN, "open"),
        (TerminalDisposition.PRE_PROVIDER_ABORT, "open"),
    ],
)
def test_half_open_terminal_dispositions_update_circuit_atomically(
    disposition, expected_state
):
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        _create(client)
        _seed_open_circuit(client)
        request = _request(previous=PREVIOUS)
        handle = _admit(client, request)
        assert handle.circuit_half_open_owner == OWNER
        known = disposition in {
            TerminalDisposition.KNOWN_SUCCESS,
            TerminalDisposition.KNOWN_FAILURE,
        }
        intent = TerminalIntent(
            handle,
            TrustedUtcInterval(1_700_000_001, 1_700_000_002),
            disposition,
            actual_tokens=10 if known else None,
            actual_micro_usd=20 if known else None,
        )

        result = PreviewTerminalReconciler(client, "preview-store").reconcile(intent)

        assert result.outcome is TerminalOutcome.RECONCILED
        circuit = _native(client, circuit_key(1, POLICY))
        assert circuit["state"] == {"S": expected_state}
        assert circuit["version"] == {"N": "9"}
        for spec in build_counter_specs(request):
            if spec.kind == "preview_identity_concurrency":
                assert _number(_native(client, spec.key), "value") == 0
        if expected_state == "closed":
            assert circuit["failures"] == {"L": []}
            assert "owner" not in circuit and "lease_until" not in circuit
        else:
            assert circuit["open_until"] == {"N": "1700000122"}


@pytest.mark.parametrize(
    "disposition",
    [TerminalDisposition.KNOWN_FAILURE, TerminalDisposition.UNCERTAIN],
)
def test_closed_failure_and_uncertain_append_safe_circuit_failure(disposition):
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        _create(client)
        request = _request()
        handle = _admit(client, request)
        known = disposition is TerminalDisposition.KNOWN_FAILURE
        intent = TerminalIntent(
            handle,
            TrustedUtcInterval(1_700_000_001, 1_700_000_002),
            disposition,
            actual_tokens=100 if known else None,
            actual_micro_usd=200 if known else None,
        )

        result = PreviewTerminalReconciler(client, "preview-store").reconcile(intent)

        assert result.outcome is TerminalOutcome.RECONCILED
        circuit = _native(client, circuit_key(1, POLICY))
        assert circuit["state"] == {"S": "closed"}
        assert circuit["failures"] == {"L": [{"N": "1700000002"}]}
        assert circuit["version"] == {"N": "1"}


@pytest.mark.parametrize("half_open", [False, True])
def test_non_provider_failure_never_opens_circuit_and_releases_once(half_open):
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        _create(client)
        if half_open:
            _seed_open_circuit(client)
        request = _request(previous=PREVIOUS) if half_open else _request()
        handle = _admit(client, request)
        intent = TerminalIntent(
            handle,
            TrustedUtcInterval(1_700_000_001, 1_700_000_002),
            TerminalDisposition.UNCERTAIN,
            circuit_failure=False,
        )

        result = PreviewTerminalReconciler(
            client, "preview-store"
        ).reconcile(intent)

        assert result.outcome is TerminalOutcome.RECONCILED
        circuit = _native(client, circuit_key(1, POLICY))
        assert circuit["state"] == {"S": "closed"}
        assert circuit["failures"] == {"L": []}
        for spec in build_counter_specs(request):
            if spec.kind == "preview_identity_concurrency":
                assert _number(_native(client, spec.key), "value") == 0


def test_non_provider_circuit_classification_is_replay_bound():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        _create(client)
        handle = _admit(client, _request())
        reconciler = PreviewTerminalReconciler(client, "preview-store")
        intent = TerminalIntent(
            handle,
            TrustedUtcInterval(1_700_000_001, 1_700_000_002),
            TerminalDisposition.UNCERTAIN,
            circuit_failure=False,
        )
        assert reconciler.reconcile(intent).outcome is TerminalOutcome.RECONCILED
        replay = reconciler.reconcile(intent)
        assert replay.outcome is TerminalOutcome.RECONCILED
        assert replay.replay is True
        conflicting = replace(intent, circuit_failure=True)
        assert (
            reconciler.reconcile(conflicting).outcome
            is TerminalOutcome.UNAVAILABLE
        )


def test_exact_replay_is_successful_noop_and_conflicting_replay_is_closed():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        _create(client)
        handle = _admit(client, _request())
        reconciler = PreviewTerminalReconciler(client, "preview-store")
        intent = TerminalIntent(
            handle,
            TrustedUtcInterval(1_700_000_001, 1_700_000_002),
            TerminalDisposition.KNOWN_FAILURE,
            actual_tokens=75,
            actual_micro_usd=125,
        )
        assert reconciler.reconcile(intent).outcome is TerminalOutcome.RECONCILED

        replay = reconciler.reconcile(intent)
        conflict = reconciler.reconcile(
            replace(intent, actual_tokens=74)
        )

        assert replay == type(replay)(TerminalOutcome.RECONCILED, replay=True)
        assert conflict.outcome is TerminalOutcome.UNAVAILABLE


def test_ambiguous_commit_is_success_only_after_exact_strong_read_proof():
    class CommitThenRaise:
        def __init__(self, client):
            self.client = client
            self.reads = 0
            self.writes = 0
            self.token = None

        def transact_get_items(self, **kwargs):
            self.reads += 1
            return self.client.transact_get_items(**kwargs)

        def transact_write_items(self, **kwargs):
            self.writes += 1
            self.token = kwargs["ClientRequestToken"]
            self.client.transact_write_items(**kwargs)
            raise TimeoutError("secret backend request id")

    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        _create(client)
        handle = _admit(client, _request())
        wrapped = CommitThenRaise(client)
        intent = TerminalIntent(
            handle,
            TrustedUtcInterval(1_700_000_001, 1_700_000_002),
            TerminalDisposition.UNCERTAIN,
        )

        result = PreviewTerminalReconciler(
            wrapped, "preview-store"
        ).reconcile(intent)

        assert result == type(result)(TerminalOutcome.RECONCILED, replay=True)
        assert wrapped.reads == 2
        assert wrapped.writes == 1
        assert len(wrapped.token) == 36
        assert wrapped.token != handle.reservation_id
        assert "secret" not in repr(result)


def test_rejected_write_with_reserved_proof_stays_unavailable_and_bounded():
    class Reject:
        def __init__(self, client):
            self.client = client
            self.reads = 0
            self.writes = 0

        def transact_get_items(self, **kwargs):
            self.reads += 1
            return self.client.transact_get_items(**kwargs)

        def transact_write_items(self, **kwargs):
            self.writes += 1
            raise ClientError(
                {
                    "Error": {
                        "Code": "TransactionCanceledException",
                        "Message": "secret backend detail",
                    },
                    "ResponseMetadata": {
                        "HTTPStatusCode": 400,
                        "RequestId": "secret-id",
                    },
                },
                "TransactWriteItems",
            )

    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        _create(client)
        handle = _admit(client, _request())
        wrapped = Reject(client)
        intent = TerminalIntent(
            handle,
            TrustedUtcInterval(1_700_000_001, 1_700_000_002),
            TerminalDisposition.PRE_PROVIDER_ABORT,
        )

        result = PreviewTerminalReconciler(
            wrapped, "preview-store"
        ).reconcile(intent)

        assert result.outcome is TerminalOutcome.UNAVAILABLE
        assert wrapped.reads == 2
        assert wrapped.writes == 1
        reservation = _native(
            client,
            reservation_key(1, handle.expiry_shard, handle.reservation_id),
        )
        assert reservation["status"] == {"S": "reserved"}
        assert "secret" not in repr(result)


def test_from_boto3_disables_sdk_retries():
    with mock_aws():
        reconciler = PreviewTerminalReconciler.from_boto3(
            "preview-store", region_name="us-east-1"
        )
        assert reconciler._client.meta.config.retries["total_max_attempts"] == 1
        assert reconciler._client.meta.config.retries["mode"] == "standard"


def test_terminal_client_token_uses_complete_uuid_and_fixed_domain():
    first = "12345678-1234-4234-9234-123456789abc"
    only_first_character_differs = "22345678-1234-4234-9234-123456789abc"

    token = _terminal_client_token(first)

    assert token == _terminal_client_token(first)
    assert token != first
    assert token != _terminal_client_token(only_first_character_differs)
    assert len(token) == 36
    assert token.isascii()
    assert set(token) <= set("0123456789abcdef")


def test_wrong_owner_underflow_malformed_and_backend_fail_closed():
    class BackendFailure:
        def transact_get_items(self, **kwargs):
            raise RuntimeError("secret request id")

        def transact_write_items(self, **kwargs):
            raise AssertionError("must not write")

    unavailable = PreviewTerminalReconciler(BackendFailure(), "preview-store")
    base = _request()
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        _create(client)
        handle = _admit(client, base)
        intent = TerminalIntent(
            handle,
            TrustedUtcInterval(1_700_000_001, 1_700_000_002),
            TerminalDisposition.PRE_PROVIDER_ABORT,
        )
        wrong = replace(handle, owner_digest="e" * 64)
        assert (
            PreviewTerminalReconciler(client, "preview-store")
            .reconcile(replace(intent, handle=wrong))
            .outcome
            is TerminalOutcome.UNAVAILABLE
        )
        spec = build_counter_specs(base)[0]
        item = _native(client, spec.key)
        item["value"] = {"N": "0"}
        client.put_item(TableName="preview-store", Item=item)
        assert (
            PreviewTerminalReconciler(client, "preview-store")
            .reconcile(intent)
            .outcome
            is TerminalOutcome.UNAVAILABLE
        )
        assert unavailable.reconcile(intent).outcome is TerminalOutcome.UNAVAILABLE
        assert "secret" not in repr(unavailable.reconcile(intent))


def test_strict_decoder_rejects_missing_reordered_and_extra_sensitive_field():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        _create(client)
        handle = _admit(client, _request())
        intent = TerminalIntent(
            handle,
            TrustedUtcInterval(1_700_000_001, 1_700_000_002),
            TerminalDisposition.UNCERTAIN,
        )
        read = build_terminal_read_request(intent, "preview-store")
        responses = client.transact_get_items(**read)["Responses"]
        with pytest.raises(ValueError):
            decode_terminal_responses(intent, responses[:-1])
        reordered = list(responses)
        reordered[1], reordered[2] = reordered[2], reordered[1]
        with pytest.raises(ValueError):
            decode_terminal_responses(intent, reordered)
        responses[0]["Item"]["raw_identity"] = {"S": "secret"}
        with pytest.raises(ValueError):
            decode_terminal_responses(intent, responses)


def test_reservation_ttl_must_match_handle_canonical_retention():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        _create(client)
        handle = _admit(client, _request())
        key = reservation_key(1, handle.expiry_shard, handle.reservation_id)
        item = _native(client, key)
        item["ttl"] = {"N": str(handle.created_upper + 7 * 86_400 + 1)}
        client.put_item(TableName="preview-store", Item=item)
        counted = _CountingClient(client)
        intent = TerminalIntent(
            handle,
            TrustedUtcInterval(handle.created_upper, handle.created_upper + 1),
            TerminalDisposition.UNCERTAIN,
        )

        result = PreviewTerminalReconciler(
            counted, "preview-store"
        ).reconcile(intent)

        assert result.outcome is TerminalOutcome.UNAVAILABLE
        assert counted.writes == 0


@pytest.mark.parametrize("disposition", list(TerminalDisposition))
@pytest.mark.parametrize(
    "kind,ttl_value",
    [
        ("preview_identity_minute", 1),
        ("preview_global_token_day", 253_402_300_799),
        ("preview_global_usd_minute", 1_700_000_060 + 7 * 86_400),
        ("preview_identity_concurrency", 1_700_604_800),
        ("preview_global_concurrency", 1_700_604_800),
    ],
)
def test_reserved_counter_ttl_tamper_is_unavailable_without_write(
    disposition, kind, ttl_value
):
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        _create(client)
        request = _request()
        handle = _admit(client, request)
        target = next(
            spec for spec in build_counter_specs(request) if spec.kind == kind
        )
        item = _native(client, target.key)
        item["ttl"] = {"N": str(ttl_value)}
        client.put_item(TableName="preview-store", Item=item)
        known = disposition in {
            TerminalDisposition.KNOWN_SUCCESS,
            TerminalDisposition.KNOWN_FAILURE,
        }
        intent = TerminalIntent(
            handle,
            TrustedUtcInterval(handle.created_upper, handle.created_upper + 1),
            disposition,
            actual_tokens=handle.reserved_tokens if known else None,
            actual_micro_usd=handle.reserved_micro_usd if known else None,
        )
        counted = _CountingClient(client)

        result = PreviewTerminalReconciler(
            counted, "preview-store"
        ).reconcile(intent)

        assert result.outcome is TerminalOutcome.UNAVAILABLE
        assert counted.writes == 0


@pytest.mark.parametrize(
    "disposition",
    [TerminalDisposition.KNOWN_FAILURE, TerminalDisposition.UNCERTAIN],
)
def test_circuit_time_regression_is_unavailable_without_write(disposition):
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        _create(client)
        handle = _admit(client, _request())
        key = circuit_key(1, POLICY)
        item = _native(client, key)
        item["version"] = {"N": "1"}
        item["failures"] = {"L": [{"N": "1700000010"}]}
        client.put_item(TableName="preview-store", Item=item)
        known = disposition is TerminalDisposition.KNOWN_FAILURE
        intent = TerminalIntent(
            handle,
            TrustedUtcInterval(handle.created_upper, handle.created_upper + 1),
            disposition,
            actual_tokens=0 if known else None,
            actual_micro_usd=0 if known else None,
        )
        counted = _CountingClient(client)

        result = PreviewTerminalReconciler(
            counted, "preview-store"
        ).reconcile(intent)

        assert result.outcome is TerminalOutcome.UNAVAILABLE
        assert counted.writes == 0


def test_exact_replay_accepts_legitimate_later_rolling_counter_ttl():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        _create(client)
        request = _request()
        handle = _admit(client, request)
        intent = TerminalIntent(
            handle,
            TrustedUtcInterval(handle.created_upper, handle.created_upper + 1),
            TerminalDisposition.KNOWN_SUCCESS,
            actual_tokens=20,
            actual_micro_usd=40,
        )
        reconciler = PreviewTerminalReconciler(client, "preview-store")
        assert reconciler.reconcile(intent).outcome is TerminalOutcome.RECONCILED
        for spec in build_counter_specs(request):
            if spec.kind.endswith("concurrency"):
                item = _native(client, spec.key)
                item["version"] = {"N": str(_number(item, "version") + 1)}
                item["value"] = {"N": "1"}
                item["ttl"] = {"N": str(_number(item, "ttl") + 5)}
                client.put_item(TableName="preview-store", Item=item)

        replay = reconciler.reconcile(intent)

        assert replay == type(replay)(TerminalOutcome.RECONCILED, replay=True)


def test_later_identity_extends_shared_concurrency_ttl_and_both_release_once():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        _create(client)
        request_a = _request()
        handle_a = _admit(client, request_a)
        request_b = replace(
            request_a,
            interval=TrustedUtcInterval(1_700_000_002.1, 1_700_000_002.2),
            identity_digest="e" * 64,
            reservation_id=str(uuid.uuid4()),
        )
        handle_b = _admit(client, request_b)
        global_spec_a = next(
            spec
            for spec in build_counter_specs(request_a)
            if spec.kind == "preview_global_concurrency"
        )
        extended = _native(client, global_spec_a.key)
        assert _number(extended, "value") == 2
        assert _number(extended, "ttl") == handle_b.created_upper + 7 * 86_400
        assert _number(extended, "ttl") > handle_a.created_upper + 7 * 86_400

        intent_a = TerminalIntent(
            handle_a,
            TrustedUtcInterval(1_700_000_004, 1_700_000_005),
            TerminalDisposition.KNOWN_SUCCESS,
            actual_tokens=handle_a.reserved_tokens,
            actual_micro_usd=handle_a.reserved_micro_usd,
        )
        intent_b = TerminalIntent(
            handle_b,
            TrustedUtcInterval(1_700_000_004, 1_700_000_005),
            TerminalDisposition.KNOWN_SUCCESS,
            actual_tokens=handle_b.reserved_tokens,
            actual_micro_usd=handle_b.reserved_micro_usd,
        )
        reconciler = PreviewTerminalReconciler(client, "preview-store")

        result_a = reconciler.reconcile(intent_a)
        after_a = _native(client, global_spec_a.key)
        result_b = reconciler.reconcile(intent_b)
        after_b = _native(client, global_spec_a.key)
        replay_a = reconciler.reconcile(intent_a)

        assert result_a.outcome is result_b.outcome is TerminalOutcome.RECONCILED
        assert _number(after_a, "value") == 1
        assert _number(after_a, "ttl") == _number(extended, "ttl")
        assert _number(after_b, "value") == 0
        assert _number(after_b, "ttl") == _number(extended, "ttl")
        assert replay_a == type(replay_a)(
            TerminalOutcome.RECONCILED, replay=True
        )
        assert _number(_native(client, global_spec_a.key), "value") == 0


def test_concurrent_exact_reconcile_converges_without_double_release():
    class SameSnapshotClient:
        """Make moto thread-safe while forcing both initial reads before writes."""

        def __init__(self, client):
            self.client = client
            self.lock = threading.Lock()
            self.initial_reads = 0
            self.read_barrier = threading.Barrier(2)
            self.write_calls = 0

        def transact_get_items(self, **kwargs):
            with self.lock:
                response = self.client.transact_get_items(**kwargs)
                self.initial_reads += 1
                initial = self.initial_reads <= 2
            if initial:
                self.read_barrier.wait(timeout=5)
            return response

        def transact_write_items(self, **kwargs):
            with self.lock:
                self.write_calls += 1
                return self.client.transact_write_items(**kwargs)

    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        _create(client)
        request = _request()
        handle = _admit(client, request)
        intent = TerminalIntent(
            handle,
            TrustedUtcInterval(1_700_000_001, 1_700_000_002),
            TerminalDisposition.KNOWN_SUCCESS,
            actual_tokens=20,
            actual_micro_usd=40,
        )
        shared = SameSnapshotClient(client)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda _: PreviewTerminalReconciler(
                        shared, "preview-store"
                    ).reconcile(intent),
                    range(2),
                )
            )

        assert all(
            result.outcome is TerminalOutcome.RECONCILED for result in results
        ), results
        assert shared.write_calls == 2
        assert sum(result.replay for result in results) == 1
        token = next(
            spec
            for spec in build_counter_specs(request)
            if spec.kind == "preview_global_token_minute"
        )
        assert _number(_native(client, token.key), "value") == 20


@pytest.mark.parametrize(
    "disposition,tokens,micros",
    [
        (TerminalDisposition.KNOWN_SUCCESS, None, None),
        (TerminalDisposition.UNCERTAIN, 0, 0),
        (TerminalDisposition.KNOWN_FAILURE, 101, 0),
        (TerminalDisposition.KNOWN_FAILURE, 0, 201),
    ],
)
def test_terminal_intent_rejects_malformed_usage(disposition, tokens, micros):
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        _create(client)
        handle = _admit(client, _request())
        with pytest.raises(ValueError):
            TerminalIntent(
                handle,
                TrustedUtcInterval(1_700_000_001, 1_700_000_002),
                disposition,
                tokens,
                micros,
            )
