from __future__ import annotations

import threading
from typing import Callable

import boto3
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from moto import mock_aws
import pytest

from trustforge.preview_admission_compiler import (
    AdmissionCompileRequest,
    AdmissionDeniedReason,
    build_counter_specs,
)
from trustforge.preview_admission_executor import (
    AdmissionOutcome,
    PreviewAdmissionExecutor,
    _CONFIRMED_WRITE_REJECTION_CODES,
)
from trustforge.preview_durable_admission_gate import (
    DurableAdmissionGate,
    GateState,
    _control_item,
)
from trustforge.preview_trusted_clock import TrustedBuckets, TrustedUtcInterval


def _request(reservation_id: str = "12345678-1234-4234-9234-123456789abc"):
    interval = TrustedUtcInterval(1_700_000_000.1, 1_700_000_000.2)
    return AdmissionCompileRequest(
        interval=interval,
        buckets=TrustedBuckets(28_333_333, "20231114"),
        policy_digest="a" * 64,
        owner_digest="b" * 64,
        identity_digest="c" * 64,
        previous_identity_digest=None,
        reservation_id=reservation_id,
        reserved_tokens=100,
        reserved_micro_usd=200,
    )


def _responses(
    request: AdmissionCompileRequest,
    *,
    denied_reason: AdmissionDeniedReason | None = None,
):
    responses: list[dict[str, object]] = [{}]
    for spec in build_counter_specs(request):
        if spec.denied_reason is denied_reason:
            responses.append(
                {
                    "Item": {
                        "pk": {"S": spec.key["pk"]},
                        "sk": {"S": spec.key["sk"]},
                        "kind": {"S": spec.kind},
                        "schema_version": {"N": "1"},
                        "version": {"N": "0"},
                        "value": {"N": str(spec.cap)},
                        "ttl": {"N": str(spec.ttl)},
                    }
                }
            )
        else:
            responses.append({})
    return responses


_DEFAULT_WRITE = object()


class FakeClient:
    def __init__(
        self,
        request: AdmissionCompileRequest,
        write: object | Callable[[], object] = _DEFAULT_WRITE,
        *,
        denied_reason: AdmissionDeniedReason | None = None,
    ):
        self.request = request
        self.write = (
            {"ResponseMetadata": {"HTTPStatusCode": 200, "RequestId": "ok"}}
            if write is _DEFAULT_WRITE
            else write
        )
        self.denied_reason = denied_reason
        self.read_calls: list[dict[str, object]] = []
        self.write_calls: list[dict[str, object]] = []
        self.control_item = _control_item(GateState.OPEN, 0, 0, None)
        self.control_get_calls: list[dict[str, object]] = []
        self.control_put_calls: list[dict[str, object]] = []
        self.reservation_item = None
        self.finalize_calls: list[dict[str, object]] = []

    def get_item(self, **kwargs):
        self.control_get_calls.append(kwargs)
        return {"Item": self.control_item}

    def put_item(self, **kwargs):
        self.control_put_calls.append(kwargs)
        self.control_item = kwargs["Item"]
        return {"ResponseMetadata": {"HTTPStatusCode": 200, "RequestId": "gate"}}

    def transact_get_items(self, **kwargs):
        if len(kwargs["TransactItems"]) == 2:
            return {
                "Responses": [
                    {"Item": self.control_item},
                    (
                        {"Item": self.reservation_item}
                        if self.reservation_item is not None
                        else {}
                    ),
                ]
            }
        self.read_calls.append(kwargs)
        return {
            "Responses": _responses(
                self.request, denied_reason=self.denied_reason
            )
        }

    def transact_write_items(self, **kwargs):
        if len(kwargs["TransactItems"]) == 2:
            self.finalize_calls.append(kwargs)
            self.control_item = kwargs["TransactItems"][-1]["Put"]["Item"]
            return {
                "ResponseMetadata": {"HTTPStatusCode": 200, "RequestId": "final"}
            }
        self.write_calls.append(kwargs)
        if callable(self.write):
            return self.write()
        if isinstance(self.write, Exception):
            raise self.write
        if (
            isinstance(self.write, dict)
            and self.write.get("ResponseMetadata", {}).get("HTTPStatusCode") == 200
        ):
            self.control_item = kwargs["TransactItems"][-1]["Put"]["Item"]
            self.reservation_item = kwargs["TransactItems"][-2]["Put"]["Item"]
        return self.write


def _executor(client, table_name: str = "preview-store"):
    return PreviewAdmissionExecutor(
        client,
        table_name,
        durable_gate=DurableAdmissionGate(client, table_name),
    )


def _client_error(
    code: str,
    status: object = 400,
    *,
    request_id: object = "request-id",
    message: object = "secret backend detail",
    operation_name: str = "TransactWriteItems",
    error_extra: dict[str, object] | None = None,
    include_metadata: bool = True,
) -> ClientError:
    response: dict[str, object] = {
        "Error": {
            "Code": code,
            "Message": message,
            **(error_extra or {}),
        },
    }
    if include_metadata:
        response["ResponseMetadata"] = {
            "HTTPStatusCode": status,
            "RequestId": request_id,
        }
    return ClientError(
        response,
        operation_name,
    )


def test_confirmed_success_returns_handle_and_canonical_token():
    request = _request()
    client = FakeClient(request)

    result = _executor(client).execute(request)

    assert result.outcome is AdmissionOutcome.ADMITTED
    assert result.handle is not None
    assert result.denied_reason is None
    assert len(client.read_calls) == len(client.write_calls) == 1
    assert client.write_calls[0]["ClientRequestToken"] == request.reservation_id
    rendered = f"{result!r} {result!s}"
    for secret in (
        request.reservation_id,
        request.owner_digest,
        request.identity_digest,
    ):
        assert secret not in rendered


@pytest.mark.parametrize(
    "reason",
    [
        reason
        for reason in AdmissionDeniedReason
        if reason is not AdmissionDeniedReason.CIRCUIT_OPEN
    ],
)
def test_each_counter_pre_read_denial_returns_safe_reason(reason):
    request = _request()
    client = FakeClient(request, denied_reason=reason)

    result = _executor(client).execute(request)

    assert result.outcome is AdmissionOutcome.DENIED
    assert result.denied_reason is reason
    assert result.handle is None
    assert len(client.read_calls) == 1
    assert client.write_calls == []
    assert "secret" not in repr(result)


def test_open_circuit_pre_read_denial_returns_safe_reason():
    request = _request()
    client = FakeClient(request)
    circuit_key = {
        "pk": {"S": "PAP#1#CIRCUIT"},
        "sk": {"S": f"POLICY#{request.policy_digest}"},
        "kind": {"S": "preview_circuit"},
        "schema_version": {"N": "1"},
        "state": {"S": "open"},
        "version": {"N": "1"},
        "failures": {
            "L": [
                {"N": "1699999996"},
                {"N": "1699999997"},
                {"N": "1699999998"},
                {"N": "1699999999"},
                {"N": "1700000000"},
            ]
        },
        "open_until": {"N": "1700000100"},
    }
    client.transact_get_items = lambda **kwargs: {
        "Responses": [{"Item": circuit_key}, *_responses(request)[1:]]
    }

    result = _executor(client).execute(request)

    assert result.outcome is AdmissionOutcome.DENIED
    assert result.denied_reason is AdmissionDeniedReason.CIRCUIT_OPEN
    assert client.write_calls == []


@pytest.mark.parametrize("code", sorted(_CONFIRMED_WRITE_REJECTION_CODES))
def test_each_strict_confirmed_4xx_rejection_is_unavailable_without_latching(
    code,
):
    request = _request()
    client = FakeClient(request, _client_error(code))
    executor = _executor(client)

    first = executor.execute(request)
    second = executor.execute(request)

    assert first.outcome is second.outcome is AdmissionOutcome.UNAVAILABLE
    assert len(client.read_calls) == len(client.write_calls) == 2
    assert "secret" not in repr(first)


@pytest.mark.parametrize(
    "error",
    [
        _client_error("TransactionCanceledException", 500),
        _client_error(
            "TransactionCanceledException", include_metadata=False
        ),
        _client_error("TransactionCanceledException", request_id=""),
        _client_error("TransactionCanceledException", request_id=None),
        _client_error("TransactionCanceledException", status=True),
        _client_error("TransactionCanceledException", status="400"),
        _client_error("TransactionCanceledException", status=None),
        _client_error("TransactionCanceledException", message=""),
        _client_error("TransactionCanceledException", message=None),
        _client_error(
            "TransactionCanceledException",
            error_extra={"Type": "Sender"},
        ),
        _client_error(
            "TransactionCanceledException", operation_name="PutItem"
        ),
    ],
)
def test_allowlisted_code_with_contradictory_shape_latches(error):
    request = _request()
    client = FakeClient(request, error)
    executor = _executor(client)

    assert executor.execute(request).outcome is AdmissionOutcome.UNAVAILABLE
    assert executor.execute(request).outcome is AdmissionOutcome.UNAVAILABLE
    assert len(client.read_calls) == len(client.write_calls) == 1


def test_malformed_cancellation_reasons_never_becomes_denied():
    request = _request()
    error = ClientError(
        {
            "Error": {
                "Code": "TransactionCanceledException",
                "Message": "secret",
            },
            "CancellationReasons": "malformed",
            "ResponseMetadata": {"HTTPStatusCode": 400},
        },
        "TransactWriteItems",
    )
    client = FakeClient(request, error)

    result = _executor(client).execute(request)

    assert result.outcome is AdmissionOutcome.UNAVAILABLE
    assert result.denied_reason is None
    assert result.handle is None
    assert len(client.write_calls) == 1


@pytest.mark.parametrize(
    "transport_error",
    [
        ConnectTimeoutError(endpoint_url="https://example.invalid"),
        ReadTimeoutError(endpoint_url="https://example.invalid"),
        EndpointConnectionError(endpoint_url="https://example.invalid"),
        ConnectionClosedError(endpoint_url="https://example.invalid"),
    ],
)
def test_transport_failure_latches_instance_and_future_calls_do_zero_io(
    transport_error,
):
    request = _request()
    client = FakeClient(request, transport_error)
    executor = _executor(client)

    assert executor.execute(request).outcome is AdmissionOutcome.UNAVAILABLE
    assert executor.execute(request).outcome is AdmissionOutcome.UNAVAILABLE

    assert len(client.read_calls) == len(client.write_calls) == 1


def test_unknown_5xx_and_malformed_success_latch_closed():
    for response in (
        _client_error("UnknownClientFault", 400),
        _client_error("InternalServerError", 500),
        RuntimeError("unknown"),
        TimeoutError("unknown disposition"),
        {},
        None,
        {"ResponseMetadata": {"HTTPStatusCode": 200}},
        {"ResponseMetadata": {"HTTPStatusCode": 201, "RequestId": "x"}},
    ):
        request = _request()
        client = FakeClient(request, response)
        executor = _executor(client)

        assert executor.execute(request).outcome is AdmissionOutcome.UNAVAILABLE
        assert executor.execute(request).outcome is AdmissionOutcome.UNAVAILABLE
        assert len(client.read_calls) == len(client.write_calls) == 1


def test_malformed_read_is_unavailable_but_does_not_dispatch_or_latch():
    request = _request()
    client = FakeClient(request)
    client.transact_get_items = lambda **kwargs: {"Responses": []}
    executor = _executor(client)

    assert executor.execute(request).outcome is AdmissionOutcome.UNAVAILABLE
    assert executor.execute(request).outcome is AdmissionOutcome.UNAVAILABLE
    assert client.write_calls == []


@pytest.mark.parametrize(
    "read_result",
    [
        RuntimeError("read backend detail"),
        None,
        {},
        {"Responses": None},
        {"Responses": [{}]},
    ],
)
def test_read_exception_or_malformed_never_dispatches_write(read_result):
    request = _request()
    client = FakeClient(request)
    calls = 0

    def read(**kwargs):
        nonlocal calls
        calls += 1
        if isinstance(read_result, Exception):
            raise read_result
        return read_result

    client.transact_get_items = read
    executor = _executor(client)

    assert executor.execute(request).outcome is AdmissionOutcome.UNAVAILABLE
    assert executor.execute(request).outcome is AdmissionOutcome.UNAVAILABLE
    assert calls == 2
    assert client.write_calls == []


def test_write_gate_prevents_second_dispatch_crossing_ambiguity():
    request = _request()
    entered = threading.Event()
    release = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    def ambiguous():
        nonlocal calls
        with calls_lock:
            calls += 1
        entered.set()
        release.wait(timeout=2)
        raise TimeoutError("unknown disposition")

    client = FakeClient(request, ambiguous)
    executor = _executor(client)
    results = []
    first = threading.Thread(target=lambda: results.append(executor.execute(request)))
    second = threading.Thread(target=lambda: results.append(executor.execute(request)))
    first.start()
    assert entered.wait(timeout=2)
    second.start()
    release.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert len(results) == 2
    assert all(result.outcome is AdmissionOutcome.UNAVAILABLE for result in results)
    assert calls == 1
    assert len(client.write_calls) == 1


def test_factory_configures_exactly_one_sdk_attempt(monkeypatch):
    captured = {}
    fake = FakeClient(_request())

    def client(service_name, **kwargs):
        captured["service_name"] = service_name
        captured.update(kwargs)
        return fake

    monkeypatch.setattr(boto3, "client", client)

    PreviewAdmissionExecutor.from_boto3("preview-store", region_name="us-east-1")

    assert captured["service_name"] == "dynamodb"
    assert captured["region_name"] == "us-east-1"
    assert captured["config"].retries == {
        "total_max_attempts": 1,
        "mode": "standard",
    }


def test_source_has_no_provider_or_retry_boundary():
    import inspect
    import trustforge.preview_admission_executor as module

    source = inspect.getsource(module).lower()
    assert "bedrock" not in source
    assert "hermes" not in source
    assert "provider" not in source
    assert ".retry" not in source
    assert "sleep(" not in source


@mock_aws
def test_moto_canonical_syntax_and_atomic_rejection_smoke():
    request = _request()
    client = boto3.client("dynamodb", region_name="us-east-1")
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
    client.put_item(
        TableName="preview-store",
        Item=_control_item(GateState.OPEN, 0, 0, None),
    )

    class RaceClient:
        def __init__(self):
            self.before_rejected_transaction = None

        def transact_get_items(self, **kwargs):
            return client.transact_get_items(**kwargs)

        def get_item(self, **kwargs):
            return client.get_item(**kwargs)

        def put_item(self, **kwargs):
            return client.put_item(**kwargs)

        def transact_write_items(self, **kwargs):
            reservation = kwargs["TransactItems"][-2]["Put"]["Item"]
            client.put_item(
                TableName="preview-store",
                Item={
                    "pk": reservation["pk"],
                    "sk": reservation["sk"],
                    "kind": {"S": "race"},
                },
            )
            self.before_rejected_transaction = client.scan(
                TableName="preview-store"
            )["Items"]
            return client.transact_write_items(**kwargs)

    race_client = RaceClient()
    result = _executor(race_client).execute(request)
    after_rejected_transaction = client.scan(TableName="preview-store")["Items"]

    assert result.outcome is AdmissionOutcome.UNAVAILABLE
    assert race_client.before_rejected_transaction is not None
    def without_control(items):
        return [
            item
            for item in items
            if item.get("pk", {}).get("S") != "PAP#1#CONTROL"
        ]
    assert without_control(after_rejected_transaction) == without_control(
        race_client.before_rejected_transaction
    )


@mock_aws
def test_concurrent_same_absent_snapshot_has_one_atomic_winner():
    first_request = _request("12345678-1234-4234-9234-123456789abc")
    second_request = _request("22345678-1234-4234-9234-123456789abc")
    client = boto3.client("dynamodb", region_name="us-east-1")
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
    client.put_item(
        TableName="preview-store",
        Item=_control_item(GateState.OPEN, 0, 0, None),
    )
    reads_complete = threading.Barrier(2, timeout=3)

    class SameSnapshotClient:
        def __init__(self):
            self.write_attempts = 0
            self.lock = threading.Lock()

        def transact_get_items(self, **kwargs):
            response = client.transact_get_items(**kwargs)
            if len(kwargs["TransactItems"]) > 2:
                reads_complete.wait()
            return response

        def get_item(self, **kwargs):
            return client.get_item(**kwargs)

        def put_item(self, **kwargs):
            return client.put_item(**kwargs)

        def transact_write_items(self, **kwargs):
            if len(kwargs["TransactItems"]) > 2:
                with self.lock:
                    self.write_attempts += 1
            return client.transact_write_items(**kwargs)

    shared_client = SameSnapshotClient()
    executor = _executor(shared_client)
    results = []
    results_lock = threading.Lock()

    def execute(request):
        result = executor.execute(request)
        with results_lock:
            results.append(result)

    threads = [
        threading.Thread(target=execute, args=(first_request,)),
        threading.Thread(target=execute, args=(second_request,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == 2
    admitted = [
        result
        for result in results
        if result.outcome is AdmissionOutcome.ADMITTED
    ]
    unavailable = [
        result
        for result in results
        if result.outcome is AdmissionOutcome.UNAVAILABLE
    ]
    assert len(admitted) == len(unavailable) == 1
    assert admitted[0].handle is not None
    assert unavailable[0].handle is unavailable[0].denied_reason is None
    assert all(result.outcome is not AdmissionOutcome.DENIED for result in results)
    assert shared_client.write_attempts == 2

    items = client.scan(TableName="preview-store")["Items"]
    assert len(items) == 11
    reservation_items = [
        item for item in items if item["kind"]["S"] == "preview_reservation"
    ]
    assert len(reservation_items) == 1
    assert reservation_items[0]["reservation_id"]["S"] == (
        admitted[0].handle.reservation_id
    )
    assert reservation_items[0]["reservation_id"]["S"] in {
        first_request.reservation_id,
        second_request.reservation_id,
    }

    expected_counters = {
        (spec.key["pk"], spec.key["sk"]): spec.increment
        for spec in build_counter_specs(first_request)
    }
    counter_items = [
        item for item in items if item["kind"]["S"].startswith("preview_")
        and item["kind"]["S"]
        not in {
            "preview_reservation",
            "preview_circuit",
            "preview_admission_quarantine",
        }
    ]
    assert len(counter_items) == len(expected_counters) == 8
    assert {
        (item["pk"]["S"], item["sk"]["S"]): int(item["value"]["N"])
        for item in counter_items
    } == expected_counters
    circuit_items = [
        item for item in items if item["kind"]["S"] == "preview_circuit"
    ]
    assert len(circuit_items) == 1
    assert circuit_items[0]["state"]["S"] == "closed"
