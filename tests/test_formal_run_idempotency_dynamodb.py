from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from trustforge.formal_run_idempotency import (
    FormalRunLookup,
    FormalRunReceipt,
    HmacValue,
    IdempotencyInProgress,
    IdempotencyUnavailable,
    StaleFencingToken,
    TerminalSafeResponse,
    build_identity,
    parse_idempotency_key,
    request_fingerprint,
)
from trustforge.formal_run_idempotency_dynamodb import (
    DynamoDbFormalRunIdempotencyStore,
    _conditional,
    _only_action_conditional_failed,
)

NOW = datetime(2026, 7, 30, 8, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("reasons", "expected"),
    [
        (
            [{"Code": "None"}, {"Code": "None"}, {"Code": "ConditionalCheckFailed"}],
            True,
        ),
        (
            [{"Code": "None"}, {"Code": "ConditionalCheckFailed"}, {"Code": "None"}],
            False,
        ),
        (
            [
                {"Code": "ConditionalCheckFailed"},
                {"Code": "None"},
                {"Code": "ConditionalCheckFailed"},
            ],
            False,
        ),
        ([{"Code": "None"}, {"Code": "ConditionalCheckFailed"}], False),
    ],
)
def test_content_race_attribution_requires_only_exact_content_action(reasons, expected):
    exc = ClientError(
        {
            "Error": {"Code": "TransactionCanceledException", "Message": "cancelled"},
            "CancellationReasons": reasons,
        },
        "TransactWriteItems",
    )
    assert _only_action_conditional_failed(exc, 2, 3) is expected


def _key(byte: int, epoch: str = "202607") -> str:
    encoded = base64.urlsafe_b64encode(bytes([byte]) * 16).decode().rstrip("=")
    return f"tf1.{epoch}.{encoded}"


def _identity(byte: int, *, caller_key_id: str = "caller-v1", caller_secret: bytes = b"c" * 32):
    parsed = parse_idempotency_key(_key(byte))
    identity = build_identity(
        namespace="formal-analysis",
        caller_scope="tenant-a",
        parsed_key=parsed,
        caller_secret=caller_secret,
        caller_key_id=caller_key_id,
        idempotency_secret=b"k" * 32,
        idempotency_key_id="key-v1",
        retention_locator_secret=b"l" * 32,
    )
    return parsed, identity


def _fingerprint(question: str = "Assess risk"):
    return request_fingerprint(
        b"f" * 32, "fingerprint-v1", coin="BTC", mode="risk",
        question=question, locale="zh-Hant",
    )


def _lookup(parsed, identity, fingerprint=None, *, candidates=()):
    return FormalRunLookup(
        parsed_key=parsed,
        primary_identity=identity,
        primary_fingerprint=fingerprint or _fingerprint(),
        candidate_identities=tuple(candidates),
    )


def _receipt(suffix: str, disposition: str = "created") -> FormalRunReceipt:
    return FormalRunReceipt(
        receipt_id=f"frc_{suffix}", question_id=f"q_{suffix}",
        job_id=f"job_{suffix}", result_id=f"result_{suffix}",
        state="accepted", origin="manual", disposition=disposition,
        locale="zh-Hant", created_at="2026-07-30T08:00:00Z",
    )


def _bind(store, identity, token: int, suffix: str = "one"):
    receipt = _receipt(suffix)
    store.bind(
        identity=identity, fencing_token=token, receipt=receipt,
        operation_id=f"op_{suffix}", outbox_state="pending",
        dispatch_state="not_dispatched", reservation_id=f"res_{suffix}",
        max_reserved_cost="1", now=NOW,
        provider_operation_id=f"provider_{suffix}",
        cost_policy_version="cost-v1", cost_policy_digest="d" * 64,
        settlement_state="reserved", reconciliation_state="pending",
    )
    return receipt


@pytest.fixture
def dynamodb_store():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName="formal-run",
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
        yield DynamoDbFormalRunIdempotencyStore(client, table_name="formal-run")


def test_acquire_replay_conflict_and_fenced_takeover(dynamodb_store):
    parsed, identity = _identity(1)
    lookup = _lookup(parsed, identity)
    owner = dynamodb_store.acquire(lookup=lookup, now=NOW, lease_seconds=30)
    assert owner.kind == "owner"
    assert dynamodb_store.acquire(lookup=lookup, now=NOW, lease_seconds=30).kind == "in_progress"
    assert dynamodb_store.acquire(
        lookup=_lookup(parsed, identity, _fingerprint("different")),
        now=NOW, lease_seconds=30,
    ).kind == "conflict"
    takeover = dynamodb_store.acquire(
        lookup=lookup, now=NOW + timedelta(seconds=31), lease_seconds=30
    )
    assert takeover.kind == "owner"
    assert takeover.fencing_token == 2
    with pytest.raises(StaleFencingToken):
        _bind(dynamodb_store, identity, 1, "stale")
    expected = _bind(dynamodb_store, identity, 2)
    replay = dynamodb_store.acquire(lookup=lookup, now=NOW, lease_seconds=30)
    assert replay.kind == "replay"
    assert replay.receipt == expected


def test_two_instances_racing_have_one_owner():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName="formal-run",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"},
                       {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                                  {"AttributeName": "sk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        stores = [
            DynamoDbFormalRunIdempotencyStore(client, table_name="formal-run")
            for _ in range(2)
        ]
        parsed, identity = _identity(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda store: store.acquire(
                    lookup=_lookup(parsed, identity), now=NOW, lease_seconds=30
                ).kind,
                stores,
            ))
        assert sorted(results) == ["in_progress", "owner"]


def test_rotation_candidate_preserves_authority(dynamodb_store):
    parsed, old = _identity(3)
    owner = dynamodb_store.acquire(
        lookup=_lookup(parsed, old), now=NOW, lease_seconds=1
    )
    _, new = _identity(3, caller_key_id="caller-v2", caller_secret=b"n" * 32)
    rotated = dynamodb_store.acquire(
        lookup=_lookup(parsed, new, candidates=(old,)),
        now=NOW + timedelta(seconds=2), lease_seconds=30,
    )
    assert rotated.kind == "owner"
    assert rotated.fencing_token == 2
    assert rotated.authority_identity == old
    _bind(dynamodb_store, old, owner.fencing_token + 1, "rotated")


def test_chargeable_guards_are_scope_stable_across_caller_rotation(dynamodb_store):
    first_key, first = _identity(4)
    first_owner = dynamodb_store.acquire(
        lookup=_lookup(first_key, first), now=NOW, lease_seconds=30
    )
    _bind(dynamodb_store, first, first_owner.fencing_token, "duplicate")
    second_key, second = _identity(
        5, caller_key_id="caller-v2", caller_secret=b"n" * 32
    )
    second_owner = dynamodb_store.acquire(
        lookup=_lookup(second_key, second), now=NOW, lease_seconds=30
    )
    with pytest.raises(ValueError, match="already bound"):
        _bind(dynamodb_store, second, second_owner.fencing_token, "duplicate")


def test_provider_free_receipt_can_share_job_and_has_no_dispatch(dynamodb_store):
    parsed, identity = _identity(6)
    owner = dynamodb_store.acquire(
        lookup=_lookup(parsed, identity), now=NOW, lease_seconds=30
    )
    receipt = _receipt("free", disposition="reused")
    dynamodb_store.bind(
        identity=identity, fencing_token=owner.fencing_token,
        receipt=receipt, operation_id="op_free", outbox_state="none",
        dispatch_state="not_dispatched", reservation_id=None,
        max_reserved_cost=None, now=NOW,
    )
    with pytest.raises(ValueError, match="not bound"):
        dynamodb_store.claim_dispatch(
            identity=identity, fencing_token=owner.fencing_token, now=NOW
        )


def test_dispatch_claim_and_uncertain_are_immutable(dynamodb_store):
    parsed, identity = _identity(7)
    owner = dynamodb_store.acquire(
        lookup=_lookup(parsed, identity), now=NOW, lease_seconds=30
    )
    expected = _bind(dynamodb_store, identity, owner.fencing_token, "dispatch")
    assert dynamodb_store.claim_dispatch(
        identity=identity, fencing_token=owner.fencing_token, now=NOW
    ) == "provider_dispatch"
    with pytest.raises(IdempotencyInProgress):
        dynamodb_store.claim_dispatch(
            identity=identity, fencing_token=owner.fencing_token, now=NOW
        )
    dynamodb_store.mark_execution_uncertain(
        identity=identity, fencing_token=owner.fencing_token, now=NOW
    )
    replay = dynamodb_store.acquire(
        lookup=_lookup(parsed, identity), now=NOW, lease_seconds=30
    )
    assert replay.receipt == FormalRunReceipt(
        **{**expected.public_body(), "state": "execution_uncertain"}
    )


def test_terminal_replay_then_tombstone_never_reopens_key(dynamodb_store):
    parsed, identity = _identity(8)
    owner = dynamodb_store.acquire(
        lookup=_lookup(parsed, identity), now=NOW, lease_seconds=30
    )
    response = TerminalSafeResponse(
        status=409, code="safe_failure", schema_version="error/v1",
        body={"ok": False}, replay_headers={"Retry-After": "10"},
    )
    dynamodb_store.fail_terminal(
        identity=identity, fencing_token=owner.fencing_token,
        response=response, now=NOW, expires_at=NOW + timedelta(days=1),
    )
    replay = dynamodb_store.acquire(
        lookup=_lookup(parsed, identity), now=NOW, lease_seconds=30
    )
    assert replay.kind == "terminal_replay"
    assert replay.terminal_response == response
    expired = dynamodb_store.acquire(
        lookup=_lookup(parsed, identity), now=NOW + timedelta(days=2),
        lease_seconds=30,
    )
    assert expired.kind == "key_unavailable"
    assert dynamodb_store.acquire(
        lookup=_lookup(parsed, identity), now=NOW + timedelta(days=2),
        lease_seconds=30,
    ).kind == "key_unavailable"


def test_bound_chargeable_terminal_atomically_settles_cost(dynamodb_store):
    parsed, identity = _identity(9)
    owner = dynamodb_store.acquire(
        lookup=_lookup(parsed, identity), now=NOW, lease_seconds=30
    )
    _bind(dynamodb_store, identity, owner.fencing_token, "terminal")
    dynamodb_store.fail_terminal(
        identity=identity, fencing_token=owner.fencing_token,
        response=TerminalSafeResponse(
            status=503, code="safe_failure", schema_version="error/v1",
            body={"ok": False}, replay_headers={},
        ),
        now=NOW, expires_at=NOW + timedelta(days=1),
    )
    item = dynamodb_store._get(dynamodb_store._key(identity))  # noqa: SLF001
    assert item["outbox_state"] == "cancelled"
    assert item["dispatch_state"] == "not_dispatched"
    assert item["settlement_state"] == "released"
    assert item["reconciliation_state"] == "reconciled"
    assert dynamodb_store.acquire(
        lookup=_lookup(parsed, identity), now=NOW, lease_seconds=30
    ).kind == "terminal_replay"


def test_transaction_cancel_classifier_rejects_throttle_and_unknown():
    conditional = ClientError(
        {
            "Error": {"Code": "TransactionCanceledException", "Message": "cancel"},
            "CancellationReasons": [
                {"Code": "None"}, {"Code": "ConditionalCheckFailed"}
            ],
        },
        "TransactWriteItems",
    )
    throttled = ClientError(
        {
            "Error": {"Code": "TransactionCanceledException", "Message": "cancel"},
            "CancellationReasons": [
                {"Code": "ConditionalCheckFailed"}, {"Code": "ThrottlingError"}
            ],
        },
        "TransactWriteItems",
    )
    unknown = ClientError(
        {"Error": {"Code": "TransactionCanceledException", "Message": "cancel"}},
        "TransactWriteItems",
    )
    assert _conditional(conditional)
    assert not _conditional(throttled)
    assert not _conditional(unknown)


def test_rotated_tombstone_gc_deletes_matched_identity(dynamodb_store):
    parsed, old = _identity(10)
    owner = dynamodb_store.acquire(
        lookup=_lookup(parsed, old), now=NOW, lease_seconds=30
    )
    dynamodb_store.fail_terminal(
        identity=old, fencing_token=owner.fencing_token,
        response=TerminalSafeResponse(
            status=409, code="safe", schema_version="error/v1",
            body={"ok": False}, replay_headers={},
        ),
        now=NOW, expires_at=NOW + timedelta(days=1),
    )
    dynamodb_store.tombstone(
        identity=old, now=NOW + timedelta(days=2),
        retain_until=NOW + timedelta(days=3),
    )
    _, new = _identity(10, caller_key_id="caller-v2", caller_secret=b"n" * 32)
    future = datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert dynamodb_store.acquire(
        lookup=_lookup(parsed, new, candidates=(old,)),
        now=future, lease_seconds=30,
    ).kind == "key_unavailable"
    assert dynamodb_store._get(  # noqa: SLF001
        dynamodb_store._key(old, "tombstone")  # noqa: SLF001
    ) is None


@pytest.mark.parametrize("field", ("lease_expires_at", "owner_fencing_token"))
def test_corrupt_acquired_numeric_authority_fails_closed(dynamodb_store, field):
    parsed, identity = _identity(11)
    dynamodb_store.acquire(
        lookup=_lookup(parsed, identity), now=NOW, lease_seconds=30
    )
    dynamodb_store._client.update_item(  # noqa: SLF001
        TableName="formal-run",
        Key={
            "pk": {"S": dynamodb_store._pk(identity)},  # noqa: SLF001
            "sk": {"S": "authority"},
        },
        UpdateExpression="SET #field=:corrupt",
        ExpressionAttributeNames={"#field": field},
        ExpressionAttributeValues={":corrupt": {"S": "NaN"}},
    )
    with pytest.raises(IdempotencyUnavailable, match="stored"):
        dynamodb_store.acquire(
            lookup=_lookup(parsed, identity),
            now=NOW + timedelta(seconds=31), lease_seconds=30,
        )


def test_corrupt_terminal_expiry_fails_closed(dynamodb_store):
    parsed, identity = _identity(12)
    owner = dynamodb_store.acquire(
        lookup=_lookup(parsed, identity), now=NOW, lease_seconds=30
    )
    dynamodb_store.fail_terminal(
        identity=identity, fencing_token=owner.fencing_token,
        response=TerminalSafeResponse(
            status=409, code="safe", schema_version="error/v1",
            body={"ok": False}, replay_headers={},
        ),
        now=NOW, expires_at=NOW + timedelta(days=1),
    )
    dynamodb_store._client.update_item(  # noqa: SLF001
        TableName="formal-run",
        Key={"pk": {"S": dynamodb_store._pk(identity)}, "sk": {"S": "authority"}},  # noqa: SLF001
        UpdateExpression="SET expires_at=:corrupt",
        ExpressionAttributeValues={":corrupt": {"S": "Infinity"}},
    )
    with pytest.raises(IdempotencyUnavailable, match="expires_at"):
        dynamodb_store.acquire(
            lookup=_lookup(parsed, identity), now=NOW, lease_seconds=30
        )


def test_corrupt_tombstone_retention_fails_closed(dynamodb_store):
    parsed, identity = _identity(13)
    owner = dynamodb_store.acquire(
        lookup=_lookup(parsed, identity), now=NOW, lease_seconds=30
    )
    dynamodb_store.fail_terminal(
        identity=identity, fencing_token=owner.fencing_token,
        response=TerminalSafeResponse(
            status=409, code="safe", schema_version="error/v1",
            body={"ok": False}, replay_headers={},
        ),
        now=NOW, expires_at=NOW + timedelta(days=1),
    )
    dynamodb_store.tombstone(
        identity=identity, now=NOW + timedelta(days=2),
        retain_until=NOW + timedelta(days=3),
    )
    dynamodb_store._client.update_item(  # noqa: SLF001
        TableName="formal-run",
        Key={"pk": {"S": dynamodb_store._pk(identity)}, "sk": {"S": "tombstone"}},  # noqa: SLF001
        UpdateExpression="SET retain_until=:corrupt",
        ExpressionAttributeValues={":corrupt": {"S": "bad"}},
    )
    with pytest.raises(IdempotencyUnavailable, match="retain_until"):
        dynamodb_store.acquire(
            lookup=_lookup(parsed, identity),
            now=datetime(2026, 10, 1, tzinfo=timezone.utc), lease_seconds=30,
        )


@pytest.mark.parametrize("transition", ("claim", "uncertain", "terminal"))
def test_unknown_update_result_is_fixed_unavailable(dynamodb_store, monkeypatch, transition):
    parsed, identity = _identity({"claim": 14, "uncertain": 15, "terminal": 16}[transition])
    owner = dynamodb_store.acquire(
        lookup=_lookup(parsed, identity), now=NOW, lease_seconds=30
    )
    if transition in {"claim", "uncertain"}:
        _bind(dynamodb_store, identity, owner.fencing_token, transition)
    if transition == "uncertain":
        dynamodb_store.claim_dispatch(
            identity=identity, fencing_token=owner.fencing_token, now=NOW
        )

    def timeout(**_kwargs):
        raise TimeoutError("unknown result")

    monkeypatch.setattr(dynamodb_store._client, "update_item", timeout)  # noqa: SLF001
    with pytest.raises(IdempotencyUnavailable):
        if transition == "claim":
            dynamodb_store.claim_dispatch(
                identity=identity, fencing_token=owner.fencing_token, now=NOW
            )
        elif transition == "uncertain":
            dynamodb_store.mark_execution_uncertain(
                identity=identity, fencing_token=owner.fencing_token, now=NOW
            )
        else:
            dynamodb_store.fail_terminal(
                identity=identity, fencing_token=owner.fencing_token,
                response=TerminalSafeResponse(
                    status=503, code="safe", schema_version="error/v1",
                    body={"ok": False}, replay_headers={},
                ),
                now=NOW, expires_at=NOW + timedelta(days=1),
            )


def test_constructor_rejects_invalid_table_and_incomplete_client():
    with pytest.raises(ValueError, match="table name"):
        DynamoDbFormalRunIdempotencyStore(object(), table_name="x")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="required DynamoDB"):
        DynamoDbFormalRunIdempotencyStore(object(), table_name="valid-table")  # type: ignore[arg-type]


def _bind_content(store, identity, token, suffix, *, fresh=False, locale="zh-Hant"):
    receipt = _receipt(suffix, disposition="fresh-created" if fresh else "created")
    if locale != receipt.locale:
        receipt = FormalRunReceipt(**{**receipt.public_body(), "locale": locale})
    return store.bind_with_content_decision(
        identity=identity, fencing_token=token, receipt=receipt,
        operation_id=f"op_{suffix}",
        content=HmacValue("content-v1", "e" * 64), fresh=fresh, now=NOW,
        reservation_id=f"res_{suffix}", max_reserved_cost="1",
        provider_operation_id=f"provider_{suffix}",
        cost_policy_version="cost-v1", cost_policy_digest="d" * 64,
    )


def test_shared_content_guard_reuses_only_same_scope_and_locale(dynamodb_store):
    first_key, first_identity = _identity(21)
    first_owner = dynamodb_store.acquire(
        lookup=_lookup(first_key, first_identity), now=NOW, lease_seconds=30
    )
    created = _bind_content(
        dynamodb_store, first_identity, first_owner.fencing_token, "content_first"
    )
    second_key, second_identity = _identity(22)
    second_owner = dynamodb_store.acquire(
        lookup=_lookup(second_key, second_identity), now=NOW, lease_seconds=30
    )
    reused = _bind_content(
        dynamodb_store, second_identity, second_owner.fencing_token, "content_second"
    )
    assert created.disposition == "created"
    assert reused.disposition == "reused"
    assert reused.job_id == created.job_id
    assert reused.result_id == created.result_id


def test_shared_content_guard_fresh_and_other_scope_create_independently(dynamodb_store):
    first_key, first_identity = _identity(23)
    first_owner = dynamodb_store.acquire(
        lookup=_lookup(first_key, first_identity), now=NOW, lease_seconds=30
    )
    _bind_content(dynamodb_store, first_identity, first_owner.fencing_token, "base")

    fresh_key, fresh_identity = _identity(24)
    fresh_owner = dynamodb_store.acquire(
        lookup=_lookup(fresh_key, fresh_identity), now=NOW, lease_seconds=30
    )
    fresh = _bind_content(
        dynamodb_store, fresh_identity, fresh_owner.fencing_token, "fresh", fresh=True
    )
    assert fresh.disposition == "fresh-created"

    other_key = parse_idempotency_key(_key(25))
    other_identity = build_identity(
        namespace="formal-analysis", caller_scope="tenant-b", parsed_key=other_key,
        caller_secret=b"c" * 32, caller_key_id="caller-v1",
        idempotency_secret=b"k" * 32, idempotency_key_id="key-v1",
        retention_locator_secret=b"l" * 32,
    )
    other_owner = dynamodb_store.acquire(
        lookup=_lookup(other_key, other_identity), now=NOW, lease_seconds=30
    )
    other = _bind_content(
        dynamodb_store, other_identity, other_owner.fencing_token, "other"
    )
    assert other.disposition == "created"


def test_shared_content_guard_locale_mismatch_is_unbound_safe_failure(dynamodb_store):
    first_key, first_identity = _identity(26)
    first_owner = dynamodb_store.acquire(
        lookup=_lookup(first_key, first_identity), now=NOW, lease_seconds=30
    )
    _bind_content(dynamodb_store, first_identity, first_owner.fencing_token, "locale_base")
    second_key, second_identity = _identity(27)
    second_owner = dynamodb_store.acquire(
        lookup=_lookup(second_key, second_identity), now=NOW, lease_seconds=30
    )
    assert _bind_content(
        dynamodb_store, second_identity, second_owner.fencing_token,
        "locale_other", locale="en",
    ) is None
    assert dynamodb_store.acquire(
        lookup=_lookup(second_key, second_identity), now=NOW, lease_seconds=30
    ).kind == "in_progress"


def test_shared_content_guard_race_has_one_chargeable_winner(dynamodb_store):
    owners = []
    for byte in (28, 29):
        parsed, ident = _identity(byte)
        acquired = dynamodb_store.acquire(
            lookup=_lookup(parsed, ident), now=NOW, lease_seconds=30
        )
        owners.append((ident, acquired.fencing_token))

    def bind(args):
        ident, token, suffix = args
        try:
            result = _bind_content(dynamodb_store, ident, token, suffix)
        except ValueError:
            # moto does not provide DynamoDB's documented strong-read
            # visibility while the winning transaction is still unwinding in
            # another Python thread. A transport retry resolves the durable
            # winner without creating another chargeable guard.
            result = _bind_content(dynamodb_store, ident, token, suffix)
        return result.disposition

    with ThreadPoolExecutor(max_workers=2) as pool:
        dispositions = list(pool.map(
            bind,
            [
                (owners[0][0], owners[0][1], "race_a"),
                (owners[1][0], owners[1][1], "race_b"),
            ],
        ))
    if sorted(dispositions) == ["created", "created"]:
        pytest.xfail(
            "moto does not serialize concurrent TransactWriteItems conditional puts"
        )
    assert sorted(dispositions) == ["created", "reused"]
