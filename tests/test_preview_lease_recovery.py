from __future__ import annotations

from dataclasses import asdict, replace
import uuid
import threading
import time
from email.utils import formatdate
from concurrent.futures import ThreadPoolExecutor

import boto3
from moto import mock_aws
import pytest

from trustforge.preview_admission_compiler import (
    AdmissionHandle,
    RETENTION_SECONDS,
    build_counter_specs,
)
from trustforge.preview_admission_executor import (
    AdmissionAmbiguity,
    PreviewAdmissionExecutor,
    _confirmed_ambiguity_resolution,
)
from trustforge.preview_lease_recovery import (
    CONTROL_PK,
    CONTROL_SK,
    MAX_PAGES,
    PAGE_LIMIT,
    PreviewAmbiguityRecovery,
    PreviewLeaseRecovery,
    RecoveryOutcome,
    _ddb_map,
)
from trustforge.preview_durable_admission_gate import (
    DurableAdmissionGate,
    GateState,
    _control_item,
)
from trustforge.preview_terminal_reconcile import (
    PreviewTerminalReconciler,
    TerminalDisposition,
    TerminalExecutionResult,
    TerminalIntent,
    TerminalOutcome,
)
from trustforge.preview_trusted_clock import PreviewTrustedClock, TrustedUtcInterval
from trustforge.quota_key_lifecycle import (
    DurableQuotaKeyLifecycleAuthority,
    LIFECYCLE_CONTROL_KEY,
    QuotaKeyLifecycle,
    QuotaKeyMaterialProvider,
)
from tests.test_preview_terminal_reconcile import (
    _admit,
    _create,
    _native,
    _number,
    _request,
)


def _handle() -> AdmissionHandle:
    return AdmissionHandle(
        "12345678-1234-4234-9234-123456789abc",
        "b" * 64,
        "c" * 64,
        None,
        28_333_333,
        "20231114",
        100,
        200,
        1_700_000_000,
        1_700_000_001,
        1_700_000_016,
        28_333_333,
        "a" * 64,
        None,
        1,
        1,
        1,
        1,
        1,
        None,
    )


def _reservation(handle: AdmissionHandle) -> dict[str, object]:
    item = asdict(handle)
    item.update(
        {
            "pk": f"PAP#1#RESERVATION#{handle.expiry_shard:010d}",
            "sk": f"ID#{handle.reservation_id}",
            "kind": "preview_reservation",
            "status": "reserved",
            "version": 0,
            "ttl": handle.created_upper + RETENTION_SECONDS,
        }
    )
    item = {key: value for key, value in item.items() if value is not None}
    return _ddb_map(item)


def _watermark(shard: int, version: int, last_sk: str | None = None):
    raw = {
        "pk": CONTROL_PK,
        "sk": CONTROL_SK,
        "kind": "preview_recovery_watermark",
        "schema_version": 1,
        "version": version,
        "shard": shard,
    }
    if last_sk is not None:
        raw["last_sk"] = last_sk
    return _ddb_map(raw)


class Client:
    def __init__(self, shard: int):
        self.item = _watermark(shard, 0)
        self.queries = []
        self.puts = []
        self.reservation = None
        self.admission_control = _control_item(GateState.OPEN, 0, 0, None)
        self.lifecycle_item = None

    def describe_table(self, **kwargs):
        del kwargs
        return {
            "ResponseMetadata": {
                "HTTPHeaders": {"date": formatdate(time.time(), usegmt=True)}
            }
        }

    def get_item(self, **kwargs):
        if kwargs["Key"] == LIFECYCLE_CONTROL_KEY:
            return {} if self.lifecycle_item is None else {"Item": self.lifecycle_item}
        if kwargs["Key"]["pk"]["S"] == CONTROL_PK:
            return {"Item": self.item}
        if kwargs["Key"]["pk"]["S"] == "PAP#1#CONTROL":
            return {"Item": self.admission_control}
        return {} if self.reservation is None else {"Item": self.reservation}

    def query(self, **kwargs):
        self.queries.append(kwargs)
        return {"Items": []}

    def put_item(self, **kwargs):
        if kwargs.get("Item", {}).get("sk") == LIFECYCLE_CONTROL_KEY["sk"]:
            self.lifecycle_item = kwargs["Item"]
            return {}
        self.puts.append(kwargs)
        self.item = kwargs["Item"]
        return {
            "ResponseMetadata": {"HTTPStatusCode": 200, "RequestId": "ok"}
        }

    def transact_get_items(self, **kwargs):
        return {
            "Responses": [
                {"Item": self.item},
                {"Item": self.admission_control},
            ]
        }

    def transact_write_items(self, **kwargs):
        actions = kwargs["TransactItems"]
        if len(actions) == 2 and "ConditionCheck" in actions[1]:
            self.item = actions[0]["Put"]["Item"]
            self.puts.append(actions[0]["Put"])
            return {
                "ResponseMetadata": {
                    "HTTPStatusCode": 200,
                    "RequestId": "checkpoint",
                }
            }
        raise AssertionError("unexpected D1 write")


def _durable_lifecycle(client):
    clock = PreviewTrustedClock(dynamodb_client=client, table_name="preview-store")
    now = clock.refresh()
    second = int(now.earliest)
    provider = QuotaKeyMaterialProvider()
    authority = DurableQuotaKeyLifecycleAuthority(
        clock,
        dynamodb_client=client,
        table_name="preview-store",
        key_material_provider=provider,
    )
    authority.install(
        QuotaKeyLifecycle(
            1,
            TrustedUtcInterval(second - 10, second - 9),
            provider.verify(
                version=1,
                key_id="quota-1",
                key_bytes=bytes(range(32)),
                activated=second - 5,
                source_revision="ssm-v1",
                authenticated_revision=True,
                csprng_provenance=True,
            ),
        )
    )
    return authority


def _bind(authority, request):
    snapshot = authority.snapshot()
    return authority.bind_admission(
        request, authority.derive(snapshot, b"lease-recovery-test")
    )


def _terminal(client: Client) -> PreviewTerminalReconciler:
    return PreviewTerminalReconciler(client, "preview-store")


def _exact_terminal(client):
    terminal = PreviewTerminalReconciler(client, "preview-store")
    terminal.intents = []

    def reconcile(intent):
        terminal.intents.append(intent)
        return TerminalExecutionResult(TerminalOutcome.RECONCILED)

    terminal.reconcile = reconcile
    return terminal


def test_empty_expired_shards_are_bounded_and_checkpointed():
    shard = _handle().expiry_shard - 10
    client = Client(shard)
    recovery = PreviewLeaseRecovery(client, "preview-store", _terminal(client))

    result = recovery.run(TrustedUtcInterval((shard + 20) * 60, (shard + 20) * 60))

    assert result.outcome is RecoveryOutcome.PROGRESSED
    assert result.pages == MAX_PAGES
    assert result.candidates == 0
    assert len(client.queries) == len(client.puts) == MAX_PAGES
    assert all(call["ConsistentRead"] is True for call in client.queries)
    assert all(call["Limit"] == PAGE_LIMIT for call in client.queries)
    assert all("FilterExpression" not in call for call in client.queries)


def test_equal_or_unexpired_shard_does_zero_query_io():
    shard = _handle().expiry_shard
    client = Client(shard)
    recovery = PreviewLeaseRecovery(client, "preview-store", _terminal(client))

    result = recovery.run(TrustedUtcInterval(shard * 60 + 59, shard * 60 + 60))

    assert result.outcome is RecoveryOutcome.IDLE
    assert client.queries == []
    assert client.puts == []


def test_missing_or_malformed_watermark_fails_closed():
    client = Client(_handle().expiry_shard)
    client.item = {}

    result = PreviewLeaseRecovery(
        client, "preview-store", _terminal(client)
    ).run(TrustedUtcInterval(2_000_000_000, 2_000_000_001))

    assert result.outcome is RecoveryOutcome.UNAVAILABLE
    assert client.queries == []


def test_ttl_danger_never_skips_an_expired_shard():
    shard = _handle().expiry_shard
    client = Client(shard)
    recovery = PreviewLeaseRecovery(client, "preview-store", _terminal(client))
    after_retention = shard * 60 + RETENTION_SECONDS

    result = recovery.run(
        TrustedUtcInterval(after_retention, after_retention + 1)
    )

    assert result.outcome is RecoveryOutcome.UNAVAILABLE
    assert client.queries == []
    assert client.puts == []


def test_malformed_last_evaluated_key_stops_without_checkpoint():
    shard = _handle().expiry_shard
    client = Client(shard)
    client.query = lambda **kwargs: {
        "Items": [],
        "LastEvaluatedKey": {"pk": {"S": "wrong"}},
    }

    result = PreviewLeaseRecovery(
        client, "preview-store", _terminal(client)
    ).run(TrustedUtcInterval(shard * 60 + 100, shard * 60 + 101))

    assert result.outcome is RecoveryOutcome.UNAVAILABLE
    assert client.puts == []


def test_exact_terminal_row_is_checkpointed_without_second_d1():
    handle = _handle()
    terminal_item = {
        key: value for key, value in asdict(handle).items() if value is not None
    }
    terminal_item.update(
        {
            "pk": f"PAP#1#RESERVATION#{handle.expiry_shard:010d}",
            "sk": f"ID#{handle.reservation_id}",
            "kind": "preview_reservation",
            "status": "terminal",
            "version": 1,
            "ttl": handle.created_upper + RETENTION_SECONDS,
            "terminal_disposition": "known_success",
            "actual_tokens": 50,
            "actual_micro_usd": 100,
        }
    )
    client = Client(handle.expiry_shard)
    returned = False

    def query(**kwargs):
        nonlocal returned
        if returned:
            return {"Items": []}
        returned = True
        return {"Items": [_ddb_map(terminal_item)]}

    client.query = query
    terminal = _exact_terminal(client)
    result = PreviewLeaseRecovery(client, "preview-store", terminal).run(
        TrustedUtcInterval(
            handle.expiry_shard * 60 + 60, handle.expiry_shard * 60 + 61
        )
    )

    assert result.outcome is RecoveryOutcome.PROGRESSED
    assert result.candidates == 1
    assert terminal.intents == []


def test_conflicting_terminal_row_blocks_watermark():
    handle = _handle()
    raw = {
        key: value for key, value in asdict(handle).items() if value is not None
    }
    raw.update(
        {
            "pk": f"PAP#1#RESERVATION#{handle.expiry_shard:010d}",
            "sk": f"ID#{handle.reservation_id}",
            "kind": "preview_reservation",
            "status": "terminal",
            "version": 1,
            "ttl": handle.created_upper + RETENTION_SECONDS,
            "terminal_disposition": "known_success",
            # missing canonical actuals
        }
    )
    client = Client(handle.expiry_shard)
    client.query = lambda **kwargs: {"Items": [_ddb_map(raw)]}

    result = PreviewLeaseRecovery(
        client, "preview-store", _terminal(client)
    ).run(
        TrustedUtcInterval(
            handle.expiry_shard * 60 + 60, handle.expiry_shard * 60 + 61
        )
    )

    assert result.outcome is RecoveryOutcome.UNAVAILABLE
    assert client.puts == []


def test_watermark_response_loss_requires_exact_strong_read_proof():
    shard = _handle().expiry_shard
    client = Client(shard)

    def lost(**kwargs):
        client.item = kwargs["Item"]
        raise TimeoutError("ambiguous")

    client.put_item = lost
    result = PreviewLeaseRecovery(
        client, "preview-store", _terminal(client)
    ).run(TrustedUtcInterval(shard * 60 + 100, shard * 60 + 101))

    assert result.outcome is RecoveryOutcome.PROGRESSED


def test_constructor_rejects_fake_or_subclass_terminal_and_bad_client():
    handle = _handle()
    client = Client(handle.expiry_shard)

    class SubTerminal(PreviewTerminalReconciler):
        pass

    for terminal in (object(), SubTerminal(client, "preview-store")):
        try:
            PreviewAmbiguityRecovery(
                client,
                "preview-store",
                terminal,
                TrustedUtcInterval(handle.lease_until + 1, handle.lease_until + 2),
            )
        except ValueError:
            pass
        else:
            raise AssertionError("non-authoritative terminal accepted")

    try:
        PreviewAmbiguityRecovery(
            object(),
            "preview-store",
            PreviewTerminalReconciler(client, "preview-store"),
            TrustedUtcInterval(handle.lease_until + 1, handle.lease_until + 2),
        )
    except ValueError:
        pass
    else:
        raise AssertionError("invalid client accepted")


@mock_aws
def test_moto_real_query_paginates_and_resumes_101_terminal_rows():
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
    base = _handle()
    client.put_item(
        TableName="preview-store",
        Item=_watermark(base.expiry_shard, 0),
    )
    for index in range(1, 102):
        handle = replace(base, reservation_id=str(uuid.UUID(int=index, version=4)))
        raw = {
            key: value for key, value in asdict(handle).items() if value is not None
        }
        raw.update(
            {
                "pk": f"PAP#1#RESERVATION#{handle.expiry_shard:010d}",
                "sk": f"ID#{handle.reservation_id}",
                "kind": "preview_reservation",
                "status": "terminal",
                "version": 1,
                "ttl": handle.created_upper + RETENTION_SECONDS,
                "terminal_disposition": "uncertain",
            }
        )
        client.put_item(TableName="preview-store", Item=_ddb_map(raw))

    terminal = PreviewTerminalReconciler(client, "preview-store")
    interval = TrustedUtcInterval(
        base.expiry_shard * 60 + 60, base.expiry_shard * 60 + 61
    )
    result = PreviewLeaseRecovery(
        client, "preview-store", terminal
    ).run(interval)

    assert result.outcome is RecoveryOutcome.PROGRESSED
    assert result.pages == 2
    assert result.candidates == 101
    control = client.get_item(
        TableName="preview-store",
        Key=_ddb_map({"pk": CONTROL_PK, "sk": CONTROL_SK}),
        ConsistentRead=True,
    )["Item"]
    assert control["shard"]["N"] == str(base.expiry_shard + 1)
    assert "last_sk" not in control


def _seed_watermark(client, shard):
    client.put_item(
        TableName="preview-store",
        Item=_watermark(shard, 0),
    )


@mock_aws
def test_real_reserved_reaper_d1_uncertain_releases_concurrency_once():
    client = boto3.client("dynamodb", region_name="us-east-1")
    _create(client)
    request = _request(previous="d" * 64)
    handle = _admit(client, request)
    client.put_item(
        TableName="preview-store",
        Item=_control_item(GateState.OPEN, 0, 0, None),
    )
    _seed_watermark(client, handle.expiry_shard)

    interval = TrustedUtcInterval(
        handle.expiry_shard * 60 + 60, handle.expiry_shard * 60 + 61
    )
    result = PreviewLeaseRecovery(
        client,
        "preview-store",
        PreviewTerminalReconciler(client, "preview-store"),
    ).run(interval)

    assert result.outcome is RecoveryOutcome.PROGRESSED
    assert result.candidates == 1
    reservation = _native(
        client,
        {
            "pk": f"PAP#1#RESERVATION#{handle.expiry_shard:010d}",
            "sk": f"ID#{handle.reservation_id}",
        },
    )
    assert reservation["terminal_disposition"] == {"S": "uncertain"}
    assert reservation["status"] == {"S": "terminal"}
    values = {
        spec.kind: _number(_native(client, spec.key), "value")
        for spec in build_counter_specs(request)
    }
    assert values["preview_identity_minute"] == 1
    assert values["preview_identity_day"] == 1
    assert values["preview_identity_concurrency"] == 0
    assert values["preview_global_concurrency"] == 0
    assert values["preview_global_token_minute"] == handle.reserved_tokens
    assert values["preview_global_usd_minute"] == handle.reserved_micro_usd


@mock_aws
def test_late_known_wins_or_uncertain_wins_without_fact_rewrite():
    client = boto3.client("dynamodb", region_name="us-east-1")
    _create(client)
    request = _request()
    handle = _admit(client, request)
    client.put_item(
        TableName="preview-store",
        Item=_control_item(GateState.OPEN, 0, 0, None),
    )
    interval = TrustedUtcInterval(
        handle.expiry_shard * 60 + 60, handle.expiry_shard * 60 + 61
    )
    known = TerminalIntent(
        handle,
        interval,
        TerminalDisposition.KNOWN_SUCCESS,
        actual_tokens=30,
        actual_micro_usd=40,
    )
    reconciler = PreviewTerminalReconciler(client, "preview-store")
    assert reconciler.reconcile(known).outcome is TerminalOutcome.RECONCILED
    _seed_watermark(client, handle.expiry_shard)
    assert (
        PreviewLeaseRecovery(client, "preview-store", reconciler).run(interval).outcome
        is RecoveryOutcome.PROGRESSED
    )
    assert reconciler.reconcile(
        TerminalIntent(handle, interval, TerminalDisposition.UNCERTAIN)
    ).outcome is TerminalOutcome.UNAVAILABLE
    reservation = _native(
        client,
        {
            "pk": f"PAP#1#RESERVATION#{handle.expiry_shard:010d}",
            "sk": f"ID#{handle.reservation_id}",
        },
    )
    assert reservation["terminal_disposition"] == {"S": "known_success"}
    assert reservation["actual_tokens"] == {"N": "30"}

    second = _admit(client, _request())
    uncertain = TerminalIntent(
        second, interval, TerminalDisposition.UNCERTAIN
    )
    assert reconciler.reconcile(uncertain).outcome is TerminalOutcome.RECONCILED
    late_known = TerminalIntent(
        second,
        interval,
        TerminalDisposition.KNOWN_SUCCESS,
        actual_tokens=10,
        actual_micro_usd=20,
    )
    assert reconciler.reconcile(late_known).outcome is TerminalOutcome.UNAVAILABLE
    second_row = _native(
        client,
        {
            "pk": f"PAP#1#RESERVATION#{second.expiry_shard:010d}",
            "sk": f"ID#{second.reservation_id}",
        },
    )
    assert second_row["terminal_disposition"] == {"S": "uncertain"}
    assert "actual_tokens" not in second_row


@mock_aws
def test_d1_commit_response_loss_is_proved_then_checkpointed():
    raw = boto3.client("dynamodb", region_name="us-east-1")
    _create(raw)
    request = _request()
    handle = _admit(raw, request)
    raw.put_item(
        TableName="preview-store",
        Item=_control_item(GateState.OPEN, 0, 0, None),
    )
    _seed_watermark(raw, handle.expiry_shard)

    class CommitThenRaise:
        def __init__(self):
            self.raised = False

        def __getattr__(self, name):
            return getattr(raw, name)

        def transact_write_items(self, **kwargs):
            response = raw.transact_write_items(**kwargs)
            if not self.raised:
                self.raised = True
                raise TimeoutError("lost")
            return response

    client = CommitThenRaise()
    interval = TrustedUtcInterval(
        handle.expiry_shard * 60 + 60, handle.expiry_shard * 60 + 61
    )
    result = PreviewLeaseRecovery(
        client,
        "preview-store",
        PreviewTerminalReconciler(client, "preview-store"),
    ).run(interval)

    assert result.outcome is RecoveryOutcome.PROGRESSED
    assert client.raised is True
    control = raw.get_item(
        TableName="preview-store",
        Key=_ddb_map({"pk": CONTROL_PK, "sk": CONTROL_SK}),
        ConsistentRead=True,
    )["Item"]
    assert control["shard"]["N"] == str(handle.expiry_shard + 1)


@mock_aws
def test_two_real_reapers_converge_without_double_release_or_skip():
    raw = boto3.client("dynamodb", region_name="us-east-1")
    _create(raw)
    request = _request()
    handle = _admit(raw, request)
    raw.put_item(
        TableName="preview-store",
        Item=_control_item(GateState.OPEN, 0, 0, None),
    )
    _seed_watermark(raw, handle.expiry_shard)

    class Shared:
        def __init__(self):
            self.lock = threading.RLock()
            self.query_barrier = threading.Barrier(2, timeout=5)

        def query(self, **kwargs):
            with self.lock:
                response = raw.query(**kwargs)
            self.query_barrier.wait()
            return response

        def __getattr__(self, name):
            method = getattr(raw, name)

            def call(**kwargs):
                with self.lock:
                    return method(**kwargs)

            return call

    client = Shared()
    interval = TrustedUtcInterval(
        handle.expiry_shard * 60 + 60, handle.expiry_shard * 60 + 61
    )

    def run(_):
        terminal = PreviewTerminalReconciler(client, "preview-store")
        return PreviewLeaseRecovery(
            client, "preview-store", terminal
        ).run(interval)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))

    # The CAS loser may also report progressed only after its transactional
    # proof observes the exact same following watermark and unchanged OPEN.
    outcomes = {result.outcome for result in results}
    assert RecoveryOutcome.PROGRESSED in outcomes
    assert outcomes <= {
        RecoveryOutcome.PROGRESSED,
        RecoveryOutcome.UNAVAILABLE,
    }
    token = next(
        spec
        for spec in build_counter_specs(request)
        if spec.kind == "preview_global_token_minute"
    )
    concurrency = next(
        spec
        for spec in build_counter_specs(request)
        if spec.kind == "preview_global_concurrency"
    )
    assert _number(_native(raw, token.key), "value") == handle.reserved_tokens
    assert _number(_native(raw, concurrency.key), "value") == 0
    control = raw.get_item(
        TableName="preview-store",
        Key=_ddb_map({"pk": CONTROL_PK, "sk": CONTROL_SK}),
        ConsistentRead=True,
    )["Item"]
    assert control["shard"]["N"] == str(handle.expiry_shard + 1)
    resumed = PreviewLeaseRecovery(
        client,
        "preview-store",
        PreviewTerminalReconciler(client, "preview-store"),
    ).run(interval)
    assert resumed.outcome is RecoveryOutcome.IDLE


def test_midpage_deadline_checkpoints_exact_item_and_next_run_resumes():
    base = _handle()
    handles = [
        replace(base, reservation_id=str(uuid.UUID(int=index, version=4)))
        for index in range(1, 4)
    ]

    def terminal_item(handle):
        raw = {
            key: value for key, value in asdict(handle).items() if value is not None
        }
        raw.update(
            {
                "pk": f"PAP#1#RESERVATION#{handle.expiry_shard:010d}",
                "sk": f"ID#{handle.reservation_id}",
                "kind": "preview_reservation",
                "status": "terminal",
                "version": 1,
                "ttl": handle.created_upper + RETENTION_SECONDS,
                "terminal_disposition": "uncertain",
            }
        )
        return _ddb_map(raw)

    client = Client(base.expiry_shard)
    items = [terminal_item(handle) for handle in handles]

    def query(**kwargs):
        start = kwargs.get("ExclusiveStartKey", {}).get("sk", {}).get("S")
        remaining = [
            item for item in items if start is None or item["sk"]["S"] > start
        ]
        return {
            "Items": remaining,
            "LastEvaluatedKey": {
                "pk": remaining[-1]["pk"],
                "sk": remaining[-1]["sk"],
            },
        }

    client.query = query
    ticks = iter([0, 0, 0, 0, 2])
    interval = TrustedUtcInterval(
        base.expiry_shard * 60 + 60, base.expiry_shard * 60 + 61
    )
    first = PreviewLeaseRecovery(
        client,
        "preview-store",
        _terminal(client),
        monotonic_clock=lambda: next(ticks),
        deadline_seconds=1,
    ).run(interval)
    assert first.outcome is RecoveryOutcome.PROGRESSED
    assert first.candidates == 2
    assert client.item["last_sk"]["S"] == f"ID#{handles[1].reservation_id}"

    seen_start = []

    def final_query(**kwargs):
        seen_start.append(kwargs["ExclusiveStartKey"]["sk"]["S"])
        return {"Items": [items[-1]]}

    client.query = final_query
    second = PreviewLeaseRecovery(
        client, "preview-store", _terminal(client)
    ).run(interval)
    assert second.outcome is RecoveryOutcome.PROGRESSED
    assert second.candidates == 1
    assert seen_start == [f"ID#{handles[1].reservation_id}"]


@pytest.mark.parametrize("restart", [False, True])
@mock_aws
def test_real_executor_latch_present_expired_d1_unlocks_sealed_gate(restart):
    raw = boto3.client("dynamodb", region_name="us-east-1")
    _create(raw)

    class AdmissionCommitThenRaise:
        def __init__(self):
            self.write_calls = 0
            self.raised = False
            self.calls = {
                "transact_get_items": 0,
                "transact_write_items": 0,
                "get_item": 0,
                "query": 0,
                "put_item": 0,
            }
            self.block_terminal_read = False
            self.terminal_read_entered = threading.Event()
            self.release_terminal_read = threading.Event()

        def __getattr__(self, name):
            return getattr(raw, name)

        def transact_write_items(self, **kwargs):
            self.calls["transact_write_items"] += 1
            self.write_calls += 1
            response = raw.transact_write_items(**kwargs)
            if self.write_calls in (1, 2):
                self.raised = True
                raise TimeoutError("admission response lost")
            return response

        def transact_get_items(self, **kwargs):
            self.calls["transact_get_items"] += 1
            first = kwargs["TransactItems"][0]["Get"]["Key"]["pk"]["S"]
            if self.block_terminal_read and "#RESERVATION#" in first:
                self.terminal_read_entered.set()
                self.release_terminal_read.wait(timeout=5)
            return raw.transact_get_items(**kwargs)

        def get_item(self, **kwargs):
            self.calls["get_item"] += 1
            return raw.get_item(**kwargs)

        def query(self, **kwargs):
            self.calls["query"] += 1
            return raw.query(**kwargs)

        def put_item(self, **kwargs):
            self.calls["put_item"] += 1
            return raw.put_item(**kwargs)

    client = AdmissionCommitThenRaise()
    raw.put_item(
        TableName="preview-store",
        Item=_control_item(GateState.OPEN, 0, 0, None),
    )
    clock = PreviewTrustedClock(
        dynamodb_client=client, table_name="preview-store"
    )
    gate = DurableAdmissionGate(
        client, "preview-store", trusted_clock=clock
    )
    authority = _durable_lifecycle(client)
    executor = PreviewAdmissionExecutor(
        client, "preview-store", durable_gate=gate, lifecycle_authority=authority
    )
    first_request = _request()
    first = executor.execute(_bind(authority, first_request))
    assert first.outcome.value == "unavailable"
    assert executor.latched_closed is True
    assert client.write_calls == 1
    assert executor._ambiguity.interval == TrustedUtcInterval(
        executor._ambiguity.handle.created_lower,
        executor._ambiguity.handle.created_upper,
    )
    if restart:
        # A replacement process has no process-local ambiguity object; durable
        # QUARANTINED is the only authority.
        replacement_clock = PreviewTrustedClock(
            dynamodb_client=client, table_name="preview-store"
        )
        gate = DurableAdmissionGate(
            client, "preview-store", trusted_clock=replacement_clock
        )
        executor = PreviewAdmissionExecutor(
            client,
            "preview-store",
            durable_gate=gate,
            lifecycle_authority=authority,
        )
        assert executor._ambiguity is None

    # While closed, execute performs zero DynamoDB I/O.
    blocked = _bind(authority, _request())
    calls_before = dict(client.calls)
    assert executor.execute(blocked).outcome.value == "unavailable"
    assert client.calls == calls_before

    terminal = PreviewTerminalReconciler(client, "preview-store")
    resolver = PreviewAmbiguityRecovery(
        client, "preview-store", terminal, gate
    )
    client.block_terminal_read = True
    results = {}
    resolve_thread = threading.Thread(
        target=lambda: results.setdefault(
            "resolved",
            (
                executor.recover_pending(resolver)
                if restart
                else executor.resolve_ambiguity(resolver)
            ),
        )
    )
    resolve_thread.start()
    assert client.terminal_read_entered.wait(timeout=5)
    following_bound = _bind(authority, _request())
    execute_thread = threading.Thread(
        target=lambda: results.setdefault(
            "following", executor.execute(following_bound)
        )
    )
    execute_thread.start()
    assert execute_thread.is_alive()
    assert client.write_calls == 1
    client.release_terminal_read.set()
    resolve_thread.join(timeout=5)
    execute_thread.join(timeout=5)
    assert not resolve_thread.is_alive() and not execute_thread.is_alive()
    assert results["resolved"] is True
    assert executor.latched_closed is False

    following = results["following"]
    assert following.outcome.value == "admitted"
    assert client.write_calls == 4  # admission, D1+OPEN, next admission+finalize


def test_public_resolver_boolean_cannot_forge_unlatch():
    handle = _handle()
    client = Client(handle.expiry_shard)
    clock = PreviewTrustedClock(
        dynamodb_client=client, table_name="preview-store"
    )
    gate = DurableAdmissionGate(
        client, "preview-store", trusted_clock=clock
    )
    authority = _durable_lifecycle(client)
    executor = PreviewAdmissionExecutor(
        client, "preview-store", durable_gate=gate, lifecycle_authority=authority
    )
    ambiguity = AdmissionAmbiguity(
        handle,
        "d" * 64,
        TrustedUtcInterval(handle.created_lower, handle.created_upper),
    )
    with executor._write_gate:
        executor._ambiguity = ambiguity
        executor._latched_closed = True

    class Forged:
        def resolve(self, value):
            assert value is ambiguity
            return True

    assert executor.resolve_ambiguity(Forged()) is False
    assert executor.latched_closed is True


def test_resolution_is_bound_to_exact_ambiguity_and_fingerprint():
    handle = _handle()
    first = AdmissionAmbiguity(
        handle,
        "d" * 64,
        TrustedUtcInterval(handle.created_lower, handle.created_upper),
    )
    wrong_identity = AdmissionAmbiguity(
        handle,
        "d" * 64,
        TrustedUtcInterval(handle.created_lower, handle.created_upper),
    )
    wrong_fingerprint = AdmissionAmbiguity(
        handle,
        "e" * 64,
        TrustedUtcInterval(handle.created_lower, handle.created_upper),
    )
    resolution = _confirmed_ambiguity_resolution(first)

    assert resolution._proves(first) is True
    assert resolution._proves(wrong_identity) is False
    assert resolution._proves(wrong_fingerprint) is False


def test_source_contract_has_no_scan_provider_or_retry():
    import inspect
    import trustforge.preview_lease_recovery as module

    source = inspect.getsource(module).lower()
    assert ".scan(" not in source
    assert "bedrock" not in source
    assert "hermes" not in source
    assert "sleep(" not in source
    assert "total_max_attempts\": 1" in source
