from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import threading
import uuid

import pytest

import trustforge.preview_admission_store as admission
from trustforge.preview_admission_store import (
    ActionKind, CircuitDisposition, CircuitSnapshot, CircuitState,
    KEY_VERSION, MAX_EPOCH_MINUTE, PreviewCircuitStore, PreviewStoreFailure,
    PreviewStoreUnavailable, ProviderFailure, TABLE_KEY_SCHEMA,
    build_admission_action, circuit_key, decode_circuit_item, decode_integral,
    global_concurrency_key, global_token_day_key, global_token_minute_key,
    global_usd_day_key, global_usd_minute_key, identity_concurrency_key,
    identity_day_key, identity_minute_key, reservation_key, ttl_from_interval,
    usd_to_micros_ceiling,
)
from trustforge.preview_trusted_clock import TrustedUtcInterval

DIGEST = "a" * 64
OWNER_A = "1" * 64
OWNER_B = "2" * 64


def at(a: float, b: float | None = None) -> TrustedUtcInterval:
    return TrustedUtcInterval(a, a if b is None else b)


class MemoryTable:
    def __init__(self) -> None:
        self.item = None
        self.lock = threading.Lock()
        self.update_calls = 0
        self.transact_calls = 0

    def get_item(self, **kwargs):
        with self.lock:
            return {} if self.item is None else {"Item": dict(self.item)}

    def put_item(self, **kwargs):
        with self.lock:
            self.update_calls += 1
            vals = kwargs.get("ExpressionAttributeValues", {})
            expected = vals.get(":previous")
            actual = None if self.item is None else self.item["version"]
            initial = kwargs["ConditionExpression"].startswith(
                "attribute_not_exists(#pk)"
            )
            if (initial and self.item is not None) or (not initial and actual != expected):
                from botocore.exceptions import ClientError
                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
            if not initial and (
                self.item.get("kind") != vals[":pkind"]
                or self.item.get("schema_version") != vals[":pschema"]
                or self.item.get("state") != vals[":pstate"]
                or tuple(self.item.get("failures", ())) != vals[":pfailures"]
            ):
                from botocore.exceptions import ClientError
                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
            self.item = dict(kwargs["Item"])
            self.item["failures"] = list(self.item["failures"])
        return {}

    def transact_write_items(self, **kwargs):
        self.transact_calls += 1
        raise AssertionError("must not transact")


class AlwaysContended(MemoryTable):
    def put_item(self, **kwargs):
        self.update_calls += 1
        from botocore.exceptions import ClientError
        raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")


class SecretGetFailure(MemoryTable):
    def get_item(self, **kwargs):
        raise RuntimeError("https://secret.internal/get?token=raw")


class SecretUpdateFailure(MemoryTable):
    def put_item(self, **kwargs):
        raise RuntimeError("https://secret.internal/update?token=raw")


class MalformedResponse(MemoryTable):
    def get_item(self, **kwargs):
        return ["not", "a", "dict"]


class NoneItemResponse(MemoryTable):
    def get_item(self, **kwargs):
        return {"Item": None}


def store(table=None):
    return PreviewCircuitStore(table=table or MemoryTable(), key_version=1, policy_digest=DIGEST)


def test_table_schema_is_dedicated_and_immutable_tuple():
    assert TABLE_KEY_SCHEMA == (("pk", "HASH", "S"), ("sk", "RANGE", "S"))


def test_all_key_namespaces_are_versioned_and_canonical():
    assert identity_minute_key(1, DIGEST, 3) == {"pk": f"PAP#1#IDENTITY#{DIGEST}", "sk": "MINUTE#0000000003"}
    assert identity_day_key(1, DIGEST, "20260729")["sk"] == "DAY#20260729"
    assert identity_concurrency_key(1, DIGEST)["sk"] == "CONCURRENCY"
    assert global_concurrency_key(1) == {"pk": "PAP#1#GLOBAL", "sk": "CONCURRENCY"}
    assert global_token_minute_key(1, 3)["sk"] == "TOKEN#MINUTE#0000000003"
    assert global_token_day_key(1, "20260729")["sk"] == "TOKEN#DAY#20260729"
    assert global_usd_minute_key(1, 3)["sk"] == "USD#MINUTE#0000000003"
    assert global_usd_day_key(1, "20260729")["sk"] == "USD#DAY#20260729"
    assert circuit_key(1, DIGEST)["sk"] == f"POLICY#{DIGEST}"


@pytest.mark.parametrize("version", [0, 2, True, Decimal(1), "1"])
def test_key_version_is_exact_integer_one(version):
    with pytest.raises(ValueError):
        circuit_key(version, DIGEST)


@pytest.mark.parametrize("digest", ["", "a"*63, "a"*65, "A"*64, "../"+"a"*61, "raw@example.com"])
def test_digest_rejects_injection_and_raw_identity(digest):
    with pytest.raises(ValueError):
        identity_concurrency_key(1, digest)


@pytest.mark.parametrize("day", ["20260229", "20261301", "00000000", "2026-07-29", "２０２６０７２９"])
def test_day_is_real_ascii_calendar_day(day):
    with pytest.raises(ValueError):
        identity_day_key(1, DIGEST, day)


@pytest.mark.parametrize("minute", [-1, MAX_EPOCH_MINUTE + 1, True, 1.0, Decimal("1.1")])
def test_epoch_minute_is_strict_and_bounded(minute):
    with pytest.raises(ValueError):
        identity_minute_key(1, DIGEST, minute)


def test_reservation_uses_expiry_shard_and_canonical_uuid4():
    rid = str(uuid.uuid4())
    key = reservation_key(1, 42, rid)
    assert key == {"pk": "PAP#1#RESERVATION#0000000042", "sk": f"ID#{rid}"}


@pytest.mark.parametrize(
    "rid",
    [
        "",
        "d73375ac-8b4a-11f1-a346-5ea42a7a4f76",
        "C35634DF-9B7B-4BBF-970F-EFE529C59012",
        "not-a-uuid",
    ],
)
def test_reservation_rejects_noncanonical_uuid4(rid):
    with pytest.raises(ValueError):
        reservation_key(1, 1, rid)


@pytest.mark.parametrize("bad", [True, 1.0, Decimal("1.1"), Decimal("NaN"), Decimal("Infinity"), "1"])
def test_integral_codec_rejects_unsafe_values(bad):
    with pytest.raises(ValueError):
        decode_integral(bad)


def test_micro_usd_accepts_decimal_only_and_ceil_rounds():
    assert usd_to_micros_ceiling(Decimal("0.0000001")) == 1
    assert usd_to_micros_ceiling(Decimal("1.0000001")) == 1_000_001
    for bad in ("1", 1, 1.0, True, Decimal("-1"), Decimal("NaN")):
        with pytest.raises(ValueError):
            usd_to_micros_ceiling(bad)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [Decimal("1e999999999"), Decimal("-1e999999999")])
def test_micro_usd_rejects_huge_exponents_before_conversion(value):
    with pytest.raises(ValueError):
        usd_to_micros_ceiling(value)


def test_ttl_requires_finite_ordered_trusted_interval_and_max_seven_days():
    assert ttl_from_interval(at(100.1, 101.2), 60) == 162
    for interval in (at(float("nan")), at(2, 1), at(-1)):
        with pytest.raises(ValueError):
            ttl_from_interval(interval, 60)
    with pytest.raises(ValueError):
        ttl_from_interval(at(1), 7 * 86400 + 1)


def valid_item(state="closed"):
    item = {
        **circuit_key(1, DIGEST), "kind": "preview_circuit",
        "schema_version": Decimal(1), "state": state, "version": Decimal(0),
        "failures": [Decimal(1), Decimal(1)],
    }
    if state == "open":
        item["failures"] = [Decimal(1)] * 5
        item["open_until"] = Decimal(100)
    if state == "half_open":
        item["failures"] = [Decimal(1)] * 5
        item.update(owner=OWNER_A, lease_until=Decimal(100))
    return item


@pytest.mark.parametrize(
    "mutation",
    [
        lambda x: x.update(secret="x"), lambda x: x.pop("version"),
        lambda x: x.update(pk="wrong"), lambda x: x.update(schema_version=True),
        lambda x: x.update(version=Decimal("1.1")), lambda x: x.update(failures=[Decimal(2), Decimal(1)]),
        lambda x: x.update(failures=[Decimal(1)]*6), lambda x: x.update(failures=[True]),
        lambda x: x.update(state="bogus"), lambda x: x.update(open_until=1),
    ],
)
def test_strict_circuit_item_rejects_each_malformed_field(mutation):
    item = valid_item()
    mutation(item)
    with pytest.raises(ValueError):
        decode_circuit_item(item, circuit_key(1, DIGEST))


def test_state_specific_required_and_forbidden_fields():
    for state, remove in (("open", "open_until"), ("half_open", "owner"), ("half_open", "lease_until")):
        item = valid_item(state)
        item.pop(remove)
        with pytest.raises(ValueError):
            decode_circuit_item(item, circuit_key(1, DIGEST))
    item = valid_item("open")
    item["owner"] = OWNER_A
    with pytest.raises(ValueError):
        decode_circuit_item(item, circuit_key(1, DIGEST))


@pytest.mark.parametrize(
    ("state", "count"),
    [("closed", 5), ("open", 4), ("half_open", 4), ("open", 0), ("half_open", 0)],
)
def test_state_failure_count_invariants_are_strict(state, count):
    item = valid_item(state)
    item["failures"] = [Decimal(1)] * count
    with pytest.raises(ValueError):
        decode_circuit_item(item, circuit_key(1, DIGEST))


def test_absent_snapshot_has_one_explicit_grammar_and_canonical_put():
    snap = CircuitSnapshot.absent(circuit_key(1, DIGEST))
    action = build_admission_action(snap, OWNER_A, at(1))
    assert snap.state is None and snap.version is None and snap.failures == ()
    assert action.kind is ActionKind.PUT and action.disposition is CircuitDisposition.ALLOW
    assert "attribute_not_exists" in action.condition_expression
    assert action.item["state"] == "closed" and action.item["version"] == 0


def test_closed_canonical_put_permit_does_not_claim_half_open_owner():
    table = MemoryTable()
    permit = store(table).acquire(interval=at(1), owner=OWNER_A)
    assert permit.allowed
    assert permit.snapshot.state is CircuitState.CLOSED
    assert permit.half_open_owner is None


def test_closed_admission_and_cas_use_identical_exact_previous_predicate():
    previous = decode_circuit_item(valid_item("closed"), circuit_key(1, DIGEST))
    admission_action = build_admission_action(previous, OWNER_A, at(1))
    next_snapshot = CircuitSnapshot(
        previous.pk, previous.sk, CircuitState.CLOSED, 1, (1,)
    )
    cas_action = admission._cas_action(previous, next_snapshot)
    assert admission_action.condition_expression == cas_action.condition_expression
    assert dict(admission_action.expression_attribute_names) == {
        key: value
        for key, value in cas_action.expression_attribute_names.items()
        if key.startswith("#p") or key in {"#pk", "#sk"}
    }
    assert dict(admission_action.expression_attribute_values) == {
        key: value
        for key, value in cas_action.expression_attribute_values.items()
        if key.startswith(":p")
    }


def test_snapshot_and_action_are_deeply_immutable_defensive_values():
    original = circuit_key(1, DIGEST)
    snap = CircuitSnapshot.absent(original)
    original["pk"] = "mutated"
    assert snap.pk == "PAP#1#CIRCUIT"
    returned = snap.key
    returned["pk"] = "mutated-again"
    assert snap.pk == "PAP#1#CIRCUIT"
    action = build_admission_action(snap, OWNER_A, at(1))
    with pytest.raises(TypeError):
        action.key["pk"] = "x"  # type: ignore[index]
    with pytest.raises(TypeError):
        action.expression_attribute_names["#x"] = "x"  # type: ignore[index]


def test_public_action_builder_rejects_noncanonical_snapshot_grammar():
    bad = CircuitSnapshot(
        "PAP#1#CIRCUIT", f"POLICY#{DIGEST}",
        state=None, version=0, failures=(1,)
    )
    with pytest.raises(ValueError):
        build_admission_action(bad, OWNER_A, at(1))
    injected = CircuitSnapshot.absent({"pk": "PAP#1#CIRCUIT", "sk": "POLICY#../raw"})
    with pytest.raises(ValueError):
        build_admission_action(injected, OWNER_A, at(1))


def test_direct_allow_action_construction_cannot_weaken_canonical_authority():
    base = build_admission_action(
        CircuitSnapshot.absent(circuit_key(1, DIGEST)), OWNER_A, at(1)
    )
    assert base.previous_snapshot is not None and base.next_snapshot is not None

    def construct(**overrides):
        fields = {
            "disposition": CircuitDisposition.ALLOW,
            "kind": ActionKind.PUT,
            "key": dict(base.key),
            "condition_expression": base.condition_expression,
            "expression_attribute_names": dict(base.expression_attribute_names),
            "expression_attribute_values": dict(base.expression_attribute_values),
            "previous_snapshot": base.previous_snapshot,
            "next_snapshot": base.next_snapshot,
            "item": dict(base.item),
        }
        fields.update(overrides)
        return admission.CircuitAdmissionAction(**fields)

    missing_item = dict(base.item)
    missing_item.pop("kind")
    extra_item = {**dict(base.item), "raw_question": "secret"}
    wrong_next = CircuitSnapshot(
        base.next_snapshot.pk, base.next_snapshot.sk, CircuitState.CLOSED, 2, ()
    )
    wrong_previous = CircuitSnapshot.absent(circuit_key(1, "b" * 64))
    attacks = (
        {"condition_expression": ""},
        {"condition_expression": "attribute_exists(pk)"},
        {"key": circuit_key(1, "b" * 64)},
        {"item": missing_item},
        {"item": extra_item},
        {"previous_snapshot": wrong_previous},
        {"next_snapshot": wrong_next},
        {"expression_attribute_names": {}},
        {"expression_attribute_values": {":raw": "secret"}},
    )
    for attack in attacks:
        with pytest.raises(ValueError):
            construct(**attack)


def test_direct_deny_action_requires_empty_nonserializable_canonical_payload():
    key = circuit_key(1, DIGEST)
    canonical = admission.CircuitAdmissionAction(
        CircuitDisposition.DENY, ActionKind.CONDITION_CHECK, key, "", {}, {}
    )
    with pytest.raises(ValueError):
        canonical.transaction_item("preview")
    with pytest.raises(ValueError):
        admission.CircuitAdmissionAction(
            CircuitDisposition.DENY, ActionKind.CONDITION_CHECK, key,
            "attribute_exists(pk)", {}, {},
        )


def test_fifth_failure_opens_and_open_failure_cannot_close_or_mutate():
    table = MemoryTable()
    s = store(table)
    for second in range(5):
        s.record_failure(interval=at(second), failure=ProviderFailure.TIMEOUT)
    before = dict(table.item)
    with pytest.raises(PreviewStoreUnavailable) as caught:
        s.record_failure(interval=at(6), failure=ProviderFailure.TIMEOUT)
    assert caught.value.reason is PreviewStoreFailure.CIRCUIT_OPEN
    assert table.item == before and table.item["open_until"] == 124


def test_open_denies_until_conservative_earliest_reaches_boundary():
    table = MemoryTable()
    table.item = valid_item("open")
    s = store(table)
    assert not s.acquire(interval=at(99, 101), owner=OWNER_A).allowed
    assert s.acquire(interval=at(100, 101), owner=OWNER_A).allowed


def test_timestamp_additions_reject_year_9999_overflow_before_dynamodb():
    table = MemoryTable()
    table.item = valid_item("open")
    table.item["open_until"] = Decimal(253_402_300_799)
    with pytest.raises(ValueError):
        store(table).acquire(
            interval=at(253_402_300_799), owner=OWNER_A
        )
    with pytest.raises(ValueError):
        ttl_from_interval(at(253_402_300_799), 1)


def test_same_owner_after_expiry_reacquires_with_new_version_and_lease():
    table = MemoryTable()
    table.item = valid_item("half_open")
    s = store(table)
    permit = s.acquire(interval=at(100, 101), owner=OWNER_A)
    assert permit.allowed and table.item["version"] == 1 and table.item["lease_until"] == 116


def test_expired_owner_cannot_report_success_or_failure():
    table = MemoryTable()
    table.item = valid_item("half_open")
    s = store(table)
    for method in (
        lambda: s.record_success(interval=at(99, 100), owner=OWNER_A),
        lambda: s.record_failure(interval=at(99, 100), failure=ProviderFailure.TIMEOUT, owner=OWNER_A),
    ):
        with pytest.raises(PreviewStoreUnavailable) as caught:
            method()
        assert caught.value.reason is PreviewStoreFailure.LEASE_INVALID


def test_half_open_matching_unexpired_owner_success_closes_failure_reopens():
    for success in (True, False):
        table = MemoryTable()
        table.item = valid_item("half_open")
        s = store(table)
        if success:
            s.record_success(interval=at(90, 99), owner=OWNER_A)
            assert table.item["state"] == "closed"
        else:
            s.record_failure(interval=at(90, 99), failure=ProviderFailure.PROVIDER_5XX, owner=OWNER_A)
            assert table.item["state"] == "open"


def test_two_threads_compete_and_only_one_half_open_owner_wins():
    table = MemoryTable()
    table.item = valid_item("open")
    s1, s2 = store(table), store(table)
    gate = threading.Barrier(2)
    def run(s, owner):
        gate.wait()
        return s.acquire(interval=at(100), owner=owner)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda args: run(*args), ((s1, OWNER_A), (s2, OWNER_B))))
    assert sum(result.allowed for result in results) == 1
    assert table.item["owner"] in (OWNER_A, OWNER_B)


def test_three_cas_attempts_then_typed_contention():
    table = AlwaysContended()
    with pytest.raises(PreviewStoreUnavailable) as caught:
        store(table).record_failure(interval=at(1), failure=ProviderFailure.TIMEOUT)
    assert caught.value.reason is PreviewStoreFailure.CONTENTION
    assert table.update_calls == 3


def test_max_version_fails_closed_before_write():
    table = MemoryTable()
    table.item = valid_item("closed")
    table.item["version"] = Decimal(9_007_199_254_740_991)
    with pytest.raises(PreviewStoreUnavailable) as caught:
        store(table).record_failure(interval=at(2), failure=ProviderFailure.TIMEOUT)
    assert caught.value.reason is PreviewStoreFailure.VERSION_EXHAUSTED
    assert table.update_calls == 0


def test_failure_time_regression_fails_closed_without_write():
    table = MemoryTable()
    table.item = valid_item("closed")
    table.item["failures"] = [Decimal(100)]
    with pytest.raises(PreviewStoreUnavailable) as caught:
        store(table).record_failure(interval=at(99), failure=ProviderFailure.TIMEOUT)
    assert caught.value.reason is PreviewStoreFailure.TIME_REGRESSION
    assert table.update_calls == 0


@pytest.mark.parametrize(
    ("table", "reason"),
    [
        (SecretGetFailure(), PreviewStoreFailure.BACKEND_UNAVAILABLE),
        (MalformedResponse(), PreviewStoreFailure.MALFORMED_ITEM),
        (NoneItemResponse(), PreviewStoreFailure.MALFORMED_ITEM),
    ],
)
def test_get_failure_and_malformed_response_are_typed_without_secret(table, reason):
    with pytest.raises(PreviewStoreUnavailable) as caught:
        store(table).snapshot()
    assert caught.value.reason is reason
    assert "secret" not in str(caught.value)


def test_update_failure_is_typed_without_backend_secret():
    table = SecretUpdateFailure()
    with pytest.raises(PreviewStoreUnavailable) as caught:
        store(table).record_failure(interval=at(1), failure=ProviderFailure.TIMEOUT)
    assert caught.value.reason is PreviewStoreFailure.BACKEND_UNAVAILABLE
    assert "secret" not in str(caught.value)


def test_canonical_put_removes_sensitive_attr_added_after_snapshot():
    table = MemoryTable()
    table.item = valid_item("closed")
    s = store(table)
    snapshot = s.snapshot()
    action = build_admission_action(snapshot, OWNER_A, at(2))
    table.item["raw_question"] = "sensitive"
    assert s._execute(action)
    assert "raw_question" not in table.item

def test_provider_enum_has_exact_operational_failures():
    assert {x.value for x in ProviderFailure} == {
        "transport_connect", "provider_5xx", "throttle_unavailable", "timeout"
    }
    with pytest.raises(ValueError):
        store().record_failure(interval=at(1), failure="schema")  # type: ignore[arg-type]


@pytest.mark.parametrize("present", [False, True])
def test_closed_or_absent_failure_rejects_owner_argument_misuse(present):
    table = MemoryTable()
    if present:
        table.item = valid_item("closed")
    with pytest.raises(ValueError):
        store(table).record_failure(
            interval=at(1), failure=ProviderFailure.TIMEOUT, owner=OWNER_A
        )


@pytest.mark.parametrize(
    "name", ["ab", "a b", " table", "table/", "", "x" * 256, 123, str.__new__(type("S", (str,), {}), "valid")]
)
def test_transaction_table_name_uses_exact_dynamodb_grammar(name):
    action = build_admission_action(CircuitSnapshot.absent(circuit_key(1, DIGEST)), OWNER_A, at(1))
    with pytest.raises(ValueError):
        action.transaction_item(name)  # type: ignore[arg-type]


def test_pure_action_serializes_for_transaction_but_store_never_calls_it():
    table = MemoryTable()
    table.item = valid_item("open")
    s = store(table)
    permit = s.acquire(interval=at(100), owner=OWNER_A)
    assert permit.allowed and table.transact_calls == 0
    snap = decode_circuit_item(table.item, circuit_key(1, DIGEST))
    action = build_admission_action(snap, OWNER_B, at(101))
    assert action.kind is ActionKind.CONDITION_CHECK
    assert action.disposition is CircuitDisposition.DENY
    with pytest.raises(ValueError):
        action.transaction_item("preview-table")


def test_moto_executes_actual_cas_expressions_and_pk_sk_schema():
    boto3 = pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")
    with moto.mock_aws():
        table = boto3.resource("dynamodb", region_name="us-east-1").create_table(
            TableName="preview",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        s = store(table)
        s.record_failure(interval=at(1), failure=ProviderFailure.TIMEOUT)
        item = table.get_item(Key=circuit_key(1, DIGEST), ConsistentRead=True)["Item"]
        assert item["version"] == Decimal(0) and item["failures"] == [Decimal(1)]


def test_moto_full_open_probe_close_and_probe_reopen_transitions():
    boto3 = pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")
    with moto.mock_aws():
        table = boto3.resource("dynamodb", region_name="us-east-1").create_table(
            TableName="preview",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        s = store(table)
        for second in range(5):
            s.record_failure(interval=at(second), failure=ProviderFailure.TIMEOUT)
        assert s.acquire(interval=at(124), owner=OWNER_A).allowed
        s.record_success(interval=at(125), owner=OWNER_A)
        assert s.snapshot().state is CircuitState.CLOSED
        for second in range(200, 205):
            s.record_failure(interval=at(second), failure=ProviderFailure.PROVIDER_5XX)
        assert s.acquire(interval=at(324), owner=OWNER_B).allowed
        s.record_failure(
            interval=at(325), failure=ProviderFailure.TIMEOUT, owner=OWNER_B
        )
        assert s.snapshot().state is CircuitState.OPEN


def test_moto_same_version_malformed_substitution_loses_full_snapshot_cas():
    boto3 = pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")
    with moto.mock_aws():
        table = boto3.resource("dynamodb", region_name="us-east-1").create_table(
            TableName="preview",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        s = store(table)
        for second in range(5):
            s.record_failure(interval=at(second), failure=ProviderFailure.TIMEOUT)
        snapshot = s.snapshot()
        action = build_admission_action(snapshot, OWNER_A, at(124))
        replaced = table.get_item(Key=circuit_key(1, DIGEST))["Item"]
        replaced["failures"] = [Decimal(2)] * 5
        table.put_item(Item=replaced)
        assert not s._execute(action)


def test_moto_actual_transaction_closed_exact_predicate_rejects_substitutions():
    boto3 = pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")
    from boto3.dynamodb.types import TypeSerializer
    from botocore.exceptions import ClientError

    serializer = TypeSerializer()
    with moto.mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        table = resource.create_table(
            TableName="preview",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        low_level = boto3.client("dynamodb", region_name="us-east-1")
        canonical = valid_item("closed")
        table.put_item(Item=canonical)
        snapshot = decode_circuit_item(canonical, circuit_key(1, DIGEST))
        action = build_admission_action(snapshot, OWNER_A, at(2))

        def condition_check():
            body = action.transaction_item("preview")["Put"]
            body["Item"] = {key: serializer.serialize(value) for key, value in body["Item"].items()}
            if "ExpressionAttributeValues" in body:
                body["ExpressionAttributeValues"] = {
                    key: serializer.serialize(value)
                    for key, value in body["ExpressionAttributeValues"].items()
                }
            low_level.transact_write_items(TransactItems=[{"Put": body}])

        condition_check()
        substitutions = (
            {"kind": "other"},
            {"schema_version": Decimal(2)},
            {"failures": [Decimal(2)]},
            {"owner": OWNER_A},
        )
        for replacement in substitutions:
            table.put_item(Item={**canonical, **replacement})
            with pytest.raises(ClientError):
                condition_check()
        assert not hasattr(PreviewCircuitStore, "transact_write_items")


def test_moto_transaction_canonical_put_shapes_and_one_probe_winner():
    boto3 = pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")
    from boto3.dynamodb.types import TypeSerializer
    from botocore.exceptions import ClientError

    serializer = TypeSerializer()
    with moto.mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        table = resource.create_table(
            TableName="preview",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        client = boto3.client("dynamodb", region_name="us-east-1")

        def transact(action):
            body = action.transaction_item("preview")["Put"]
            body["Item"] = {
                key: serializer.serialize(value) for key, value in body["Item"].items()
            }
            if "ExpressionAttributeValues" in body:
                body["ExpressionAttributeValues"] = {
                    key: serializer.serialize(value)
                    for key, value in body["ExpressionAttributeValues"].items()
                }
            client.transact_write_items(TransactItems=[{"Put": body}])

        absent = CircuitSnapshot.absent(circuit_key(1, DIGEST))
        transact(build_admission_action(absent, OWNER_A, at(1)))
        closed = decode_circuit_item(
            table.get_item(Key=circuit_key(1, DIGEST))["Item"],
            circuit_key(1, DIGEST),
        )
        closed_action = build_admission_action(closed, OWNER_A, at(2))
        polluted = table.get_item(Key=circuit_key(1, DIGEST))["Item"]
        polluted["raw_question"] = "secret"
        table.put_item(Item=polluted)
        transact(closed_action)
        assert "raw_question" not in table.get_item(Key=circuit_key(1, DIGEST))["Item"]

        for state in ("open", "half_open"):
            source = valid_item(state)
            table.put_item(Item=source)
            snapshot = decode_circuit_item(source, circuit_key(1, DIGEST))
            first = build_admission_action(snapshot, OWNER_A, at(100))
            second = build_admission_action(snapshot, OWNER_B, at(100))
            transact(first)
            current = table.get_item(Key=circuit_key(1, DIGEST))["Item"]
            assert current["state"] == "half_open" and current["owner"] == OWNER_A
            with pytest.raises(ClientError):
                transact(second)
        assert not hasattr(PreviewCircuitStore, "transact_write_items")
