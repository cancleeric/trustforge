from __future__ import annotations

import inspect
import copy
import json
import uuid
from dataclasses import replace

import pytest

import trustforge.preview_admission_compiler as compiler
from trustforge.preview_admission_compiler import (
    AdmissionHandle,
    AdmissionSnapshots,
    AdmissionCompileDenied,
    AdmissionCompileRequest,
    build_counter_specs,
    compile_admission,
    decode_counter_item,
    decode_transact_get_responses,
)
from trustforge.preview_admission_store import CircuitSnapshot, CircuitState, circuit_key
from trustforge.preview_trusted_clock import TrustedBuckets, TrustedUtcInterval


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
OWNER = "c" * 64
POLICY = "d" * 64
RESERVATION = str(uuid.UUID("12345678-1234-4234-9234-123456789abc"))


def request(
    *,
    previous: str | None = None,
    tokens: int = 512,
    micros: int = 1000,
    interval: TrustedUtcInterval = TrustedUtcInterval(60, 61),
    reservation_id: str = RESERVATION,
):
    return AdmissionCompileRequest(
        interval=interval,
        buckets=TrustedBuckets(1, "19700101"),
        policy_digest=POLICY,
        owner_digest=OWNER,
        identity_digest=DIGEST_A,
        previous_identity_digest=previous,
        reservation_id=reservation_id,
        reserved_tokens=tokens,
        reserved_micro_usd=micros,
        lifecycle_generation=2 if previous else 1,
        current_quota_key_version=2 if previous else 1,
        previous_quota_key_version=1 if previous else None,
    )


def canonical(spec, *, value=0, version=0):
    return {
        **dict(spec.key),
        "kind": spec.kind,
        "schema_version": 1,
        "version": version,
        "value": value,
        "ttl": spec.ttl,
    }


def absent_snapshots(req):
    return decode_transact_get_responses(
        req, [{} for _ in range(len(build_counter_specs(req)) + 1)]
    )


def ddb_item(item):
    return {
        key: ({"S": value} if type(value) is str else {"N": str(value)})
        for key, value in item.items()
    }


@pytest.mark.parametrize(
    "change",
    [
        {"owner_digest": "A" * 64},
        {"identity_digest": "a" * 63},
        {"previous_identity_digest": DIGEST_A},
        {"reservation_id": "12345678-1234-1234-9234-123456789abc"},
        {"reserved_tokens": 0},
        {"reserved_tokens": True},
        {"reserved_tokens": 2561},
        {"reserved_micro_usd": 0},
        {"reserved_micro_usd": True},
        {"reserved_micro_usd": 50_001},
        {"key_version": 2},
        {"key_version": True},
        {"schema_version": 2},
        {"schema_version": True},
        {"policy_version": True},
        {"policy_version": 0},
        {"policy_version": 2},
        {"policy_version": 9_007_199_254_740_991},
        {"buckets": TrustedBuckets(True, "19700101")},
        {"buckets": TrustedBuckets(1, 19700101)},
        {"buckets": TrustedBuckets(2, "19700101")},
        {"buckets": TrustedBuckets(1, "19700102")},
    ],
)
def test_request_is_strict_and_matches_trusted_buckets(change):
    fields = {
        "interval": TrustedUtcInterval(60, 61),
        "buckets": TrustedBuckets(1, "19700101"),
        "policy_digest": POLICY,
        "owner_digest": OWNER,
        "identity_digest": DIGEST_A,
        "previous_identity_digest": None,
        "reservation_id": RESERVATION,
            "reserved_tokens": 512,
            "reserved_micro_usd": 1000,
            "lifecycle_generation": 1,
            "current_quota_key_version": 1,
            "previous_quota_key_version": None,
    }
    fields.update(change)
    with pytest.raises(ValueError):
        AdmissionCompileRequest(**fields)


@pytest.mark.parametrize("previous,count", [(None, 8), (DIGEST_B, 11)])
def test_exact_unique_counter_read_set(previous, count):
    specs = build_counter_specs(request(previous=previous))
    assert len(specs) == count
    assert len({(x.key["pk"], x.key["sk"]) for x in specs}) == count
    read = compiler.build_transact_get_request(request(previous=previous), "preview-table")
    assert len(read["TransactItems"]) == count + 1
    first = read["TransactItems"][0]["Get"]["Key"]
    assert first["pk"] == {"S": "PAP#1#CIRCUIT"}
    assert first["sk"] == {"S": f"POLICY#{POLICY}"}


def test_counter_decoder_accepts_only_absent_or_complete_canonical_item():
    spec = build_counter_specs(request())[0]
    absent = decode_counter_item(None, spec)
    assert absent.absent and absent.value == 0
    present = decode_counter_item(canonical(spec, value=2, version=7), spec)
    assert present.version == 7 and present.value == 2
    for mutation in (
        {"raw_question": "secret"},
        {"kind": "preview_global_concurrency"},
        {"schema_version": 2},
        {"ttl": spec.ttl + 1},
        {"value": True},
        {"value": spec.cap + 1},
    ):
        item = canonical(spec)
        item.update(mutation)
        with pytest.raises(ValueError):
            decode_counter_item(item, spec)


@pytest.mark.parametrize("previous,actions", [(None, 10), (DIGEST_B, 13)])
def test_compiles_exact_low_level_action_counts(previous, actions):
    req = request(previous=previous)
    plan = compile_admission(req, "preview-table", absent_snapshots(req))
    assert plan.action_count == actions
    assert len(plan.write_request["TransactItems"]) == actions
    assert plan.estimated_bytes < 256 * 1024
    puts = plan.write_request["TransactItems"]
    keys = {
        (action["Put"]["Item"]["pk"]["S"], action["Put"]["Item"]["sk"]["S"])
        for action in puts
    }
    assert len(keys) == actions
    rendered = repr(plan.write_request)
    for forbidden in ("raw_question", "client_request", "prompt", "model", "raw_ip"):
        assert forbidden not in rendered


def test_present_counter_uses_full_snapshot_predicate_and_canonical_replacement():
    req = request()
    specs = build_counter_specs(req)
    items = [canonical(spec, value=0, version=4) for spec in specs]
    snapshots = tuple(decode_counter_item(item, spec) for item, spec in zip(items, specs))
    decoded = absent_snapshots(req)
    plan = compile_admission(
        req, "preview-table", AdmissionSnapshots(decoded.circuit, snapshots)
    )
    first = plan.write_request["TransactItems"][0]["Put"]
    assert "#kind=:kind" in first["ConditionExpression"]
    assert first["Item"]["version"] == {"N": "5"}
    assert set(first["Item"]) == {
        "pk", "sk", "kind", "schema_version", "version", "value", "ttl"
    }


def test_every_counter_cap_denies_before_a_plan_exists():
    req = request()
    specs = build_counter_specs(req)
    for index, spec in enumerate(specs):
        items = [None] * len(specs)
        items[index] = canonical(spec, value=spec.cap)
        snapshots = tuple(
            decode_counter_item(item, expected)
            for item, expected in zip(items, specs)
        )
        with pytest.raises(AdmissionCompileDenied) as caught:
            decoded = absent_snapshots(req)
            compile_admission(
                req, "preview-table", AdmissionSnapshots(decoded.circuit, snapshots)
            )
        assert caught.value.reason is spec.denied_reason


def test_mismatch_and_unknown_snapshot_fail_closed():
    req = request()
    specs = build_counter_specs(req)
    with pytest.raises(ValueError):
        compile_admission(req, "preview-table", [])
    item = canonical(specs[0])
    item["unknown"] = 1
    with pytest.raises(ValueError):
        decode_transact_get_responses(
            req, [{}, {"Item": ddb_item(item)}, *([{}] * (len(specs) - 1))]
        )


def test_reservation_has_fifteen_second_lease_and_seven_day_ttl():
    req = request()
    plan = compile_admission(req, "preview-table", absent_snapshots(req))
    item = plan.write_request["TransactItems"][-1]["Put"]["Item"]
    assert item["lease_until"] == {"N": "76"}
    assert item["ttl"] == {"N": str(61 + 7 * 86_400)}
    assert item["reserved_tokens"] == {"N": "512"}
    assert item["reserved_micro_usd"] == {"N": "1000"}
    assert item["expiry_shard"] == {"N": "1"}
    assert "previous_identity_digest" not in item
    assert "circuit_half_open_owner" not in item


def test_compiled_plan_is_deeply_immutable_and_compiler_has_no_write_client():
    req = request()
    snapshots = absent_snapshots(req)
    plan = compile_admission(req, "preview-table", snapshots)
    with pytest.raises(TypeError):
        plan.write_request["TransactItems"][0]["Put"]["Item"]["value"] = {"N": "9"}
    with pytest.raises(TypeError):
        compiler.CompiledAdmissionPlan({}, {}, plan.handle, 10, 1)
    with pytest.raises(ValueError):
        compiler.CompiledAdmissionPlan._create(
            object(), req, snapshots, "preview-table", {}, {}, plan.handle, 10, 1
        )
    source = inspect.getsource(compiler)
    assert "transact_write_items(" not in source
    assert "DynamoDbClient" not in source


def test_moto_accepts_low_level_transaction_syntax_without_executing_it():
    boto3 = pytest.importorskip("boto3")
    pytest.importorskip("moto")
    req = request()
    plan = compile_admission(req, "preview-table", absent_snapshots(req))
    # Botocore parameter validation proves client-ready AttributeValue syntax;
    # the compiler test intentionally never sends the write.
    client = boto3.client(
        "dynamodb",
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    operation = client.meta.service_model.operation_model("TransactWriteItems")
    from botocore.validate import ParamValidator
    report = ParamValidator().validate(
        plan.transact_write_items_request(), operation.input_shape
    )
    assert not report.has_errors(), report.generate_report()


def test_moto_executes_compiled_syntax_but_production_compiler_never_writes():
    boto3 = pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")
    from botocore.exceptions import ClientError

    with moto.mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName="preview-table",
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
        req = request()
        plan = compile_admission(req, "preview-table", absent_snapshots(req))
        client.transact_write_items(**plan.transact_write_items_request())
        with pytest.raises(ClientError):
            client.transact_write_items(**plan.transact_write_items_request())


def test_handle_and_reservation_item_have_exact_safe_parity():
    req = request(previous=DIGEST_B)
    plan = compile_admission(req, "preview-table", absent_snapshots(req))
    handle = plan.handle
    assert type(handle) is AdmissionHandle
    assert handle == AdmissionHandle(
        RESERVATION, OWNER, DIGEST_A, DIGEST_B, 1, "19700101", 512, 1000,
        60, 61, 76, 1, POLICY, None, 1, 1, 1, 2, 2, 1,
    )
    item = plan.write_request["TransactItems"][-1]["Put"]["Item"]
    expected = {
        "pk", "sk", "kind", "status", "version", "ttl", "reservation_id",
        "owner_digest", "identity_digest", "previous_identity_digest",
        "epoch_minute", "utc_day", "reserved_tokens", "reserved_micro_usd",
        "created_lower", "created_upper", "lease_until", "expiry_shard",
        "policy_digest", "policy_version", "key_version", "schema_version",
        "lifecycle_generation", "current_quota_key_version",
        "previous_quota_key_version",
    }
    assert set(item) == expected
    for field in expected - {"pk", "sk", "kind", "status", "version", "ttl"}:
        raw = getattr(handle, field)
        assert item[field] == ({"S": raw} if type(raw) is str else {"N": str(raw)})


def test_expired_open_circuit_records_only_canonical_half_open_owner():
    key = circuit_key(1, POLICY)
    circuit = CircuitSnapshot(
        key["pk"], key["sk"], CircuitState.OPEN, 4, (1, 2, 3, 4, 5),
        open_until=50,
    )
    base = request()
    req = AdmissionCompileRequest(
        base.interval, base.buckets, POLICY, OWNER, DIGEST_A, None,
        RESERVATION, 512, 1000, 1, 1, None,
    )
    decoded = absent_snapshots(req)
    plan = compile_admission(
        req, "preview-table", AdmissionSnapshots(circuit, decoded.counters)
    )
    assert plan.handle.circuit_half_open_owner == OWNER
    item = plan.write_request["TransactItems"][-1]["Put"]["Item"]
    assert item["circuit_half_open_owner"] == {"S": OWNER}


@pytest.mark.parametrize(
    "response",
    [
        None,
        {"Item": None},
        {"Other": {}},
        {"Item": {"value": {"N": "+1"}}},
        {"Item": {"value": {"N": "01"}}},
        {"Item": {"value": {"N": "1.0"}}},
        {"Item": {"value": {"BOOL": True}}},
        {"Item": {"value": {"S": "1", "N": "1"}}},
    ],
)
def test_transact_get_decoder_rejects_malformed_response_grammar(response):
    req = request()
    responses = [{} for _ in range(len(build_counter_specs(req)) + 1)]
    responses[1] = response
    with pytest.raises(ValueError):
        decode_transact_get_responses(req, responses)


def test_transact_get_decoder_preserves_exact_request_order():
    req = request()
    specs = build_counter_specs(req)
    responses = [
        {"Item": ddb_item(canonical(spec, value=0, version=index))}
        for index, spec in enumerate(specs)
    ]
    snapshots = decode_transact_get_responses(req, [{}, *responses])
    assert [snapshot.spec for snapshot in snapshots.counters] == list(specs)
    assert [snapshot.version for snapshot in snapshots.counters] == list(range(len(specs)))


@pytest.mark.parametrize("bad", [True, -1, 9_007_199_254_740_992])
def test_snapshot_version_bool_and_range_attacks_fail_closed(bad):
    req = request()
    specs = build_counter_specs(req)
    item = canonical(specs[0])
    item["version"] = bad
    with pytest.raises(ValueError):
        decode_counter_item(item, specs[0])


def test_max_counter_version_cannot_wrap_during_compile():
    req = request()
    specs = build_counter_specs(req)
    decoded = absent_snapshots(req)
    snapshots = list(decoded.counters)
    snapshots[0] = decode_counter_item(
        canonical(specs[0], version=9_007_199_254_740_991), specs[0]
    )
    with pytest.raises(ValueError, match="version exhausted"):
        compile_admission(
            req, "preview-table", AdmissionSnapshots(decoded.circuit, tuple(snapshots))
        )


def test_same_bucket_retry_uses_fixed_ttl_and_extends_only_concurrency_ttl():
    first = request()
    old_specs = build_counter_specs(first)
    second = request(
        interval=TrustedUtcInterval(62, 63),
        reservation_id="87654321-4321-4321-8321-cba987654321",
    )
    new_specs = build_counter_specs(second)
    assert [x.key for x in old_specs] == [x.key for x in new_specs]
    for old, new in zip(old_specs, new_specs):
        if old.fixed_ttl:
            assert old.ttl == new.ttl
        else:
            assert new.ttl == old.ttl + 2
    responses = [{}]
    for spec in old_specs:
        value = 0 if "concurrency" in spec.kind else 1
        responses.append({"Item": ddb_item(canonical(spec, value=value, version=0))})
    decoded = decode_transact_get_responses(second, responses)
    plan = compile_admission(second, "preview-table", decoded)
    counter_puts = plan.write_request["TransactItems"][: len(new_specs)]
    for action, old, new in zip(counter_puts, old_specs, new_specs):
        written_ttl = int(action["Put"]["Item"]["ttl"]["N"])
        assert written_ttl == (new.ttl if old.fixed_ttl else max(old.ttl, new.ttl))
    assert counter_puts[0]["Put"]["Item"]["value"] == {"N": "2"}


def test_circuit_snapshot_policy_digest_mismatch_fails_closed():
    req = request()
    wrong = {
        "pk": "PAP#1#CIRCUIT",
        "sk": f"POLICY#{'e' * 64}",
        "kind": "preview_circuit",
        "schema_version": 1,
        "state": "closed",
        "version": 0,
        "failures": [],
    }
    response = {"Item": {
        **ddb_item({key: value for key, value in wrong.items() if key != "failures"}),
        "failures": {"L": []},
    }}
    with pytest.raises(ValueError):
        decode_transact_get_responses(
            req, [response, *([{}] * len(build_counter_specs(req)))]
        )
    decoded = absent_snapshots(req)
    wrong_snapshot = CircuitSnapshot.absent(circuit_key(1, "e" * 64))
    with pytest.raises(ValueError, match="circuit snapshot mismatch"):
        compile_admission(
            req, "preview-table",
            AdmissionSnapshots(wrong_snapshot, decoded.counters),
        )


def test_moto_actual_transact_get_response_round_trips_circuit_first():
    boto3 = pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")
    with moto.mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName="preview-table",
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
        req = request()
        plan = compile_admission(req, "preview-table", absent_snapshots(req))
        client.transact_write_items(**plan.transact_write_items_request())
        response = client.transact_get_items(**plan.transact_get_items_request())
        decoded = decode_transact_get_responses(req, response["Responses"])
        assert decoded.circuit.state is CircuitState.CLOSED
        assert len(decoded.counters) == 8


@pytest.mark.parametrize(
    "version,value,ttl_mode",
    [
        (None, 1, "exact"),
        (None, 0, "plus_one"),
        (True, 0, "exact"),
        (0, True, "exact"),
        (0, 0, "bool"),
        (0, 0, "zero"),
        (0, 0, "too_large"),
        (0, 0, "plus_one"),
    ],
)
def test_direct_counter_snapshot_attacks_are_rejected(version, value, ttl_mode):
    spec = build_counter_specs(request())[0]  # fixed minute TTL
    ttl = {
        "exact": spec.ttl,
        "plus_one": spec.ttl + 1,
        "bool": True,
        "zero": 0,
        "too_large": compiler.MAX_EPOCH_SECOND + 1,
    }[ttl_mode]
    with pytest.raises(ValueError):
        compiler.CounterSnapshot(spec, version, value, ttl)


def test_rolling_concurrency_snapshot_mirrors_decoder_ttl_rule():
    spec = next(
        item for item in build_counter_specs(request())
        if item.kind == "preview_identity_concurrency"
    )
    # An older bounded TTL is valid and will be monotonically extended by compile.
    snapshot = compiler.CounterSnapshot(spec, 0, 0, spec.ttl - 10)
    assert snapshot.ttl == spec.ttl - 10
    equal = compiler.CounterSnapshot(spec, 0, 0, spec.ttl)
    assert equal.ttl == spec.ttl
    for invalid in (True, 0, spec.ttl + 1, compiler.MAX_EPOCH_SECOND):
        with pytest.raises(ValueError):
            compiler.CounterSnapshot(spec, 0, 0, invalid)
    for future_ttl in (spec.ttl + 1, compiler.MAX_EPOCH_SECOND):
        item = canonical(spec, value=0, version=0)
        item["ttl"] = future_ttl
        with pytest.raises(ValueError, match="malformed counter"):
            decode_counter_item(item, spec)


def test_compile_rejects_reordered_and_wrong_spec_snapshots():
    req = request()
    decoded = absent_snapshots(req)
    reordered = list(decoded.counters)
    reordered[0], reordered[1] = reordered[1], reordered[0]
    with pytest.raises(ValueError, match="counter snapshot mismatch"):
        compile_admission(
            req, "preview-table",
            AdmissionSnapshots(decoded.circuit, tuple(reordered)),
        )
    wrong_req = request(previous=DIGEST_B)
    wrong_counter = absent_snapshots(wrong_req).counters[3]
    forged = (wrong_counter, *decoded.counters[1:])
    with pytest.raises(ValueError, match="counter snapshot mismatch"):
        compile_admission(
            req, "preview-table",
            AdmissionSnapshots(decoded.circuit, forged),
        )


def test_admission_snapshots_rejects_malformed_circuit_key_grammar():
    decoded = absent_snapshots(request())
    malformed = CircuitSnapshot.absent({
        "pk": "PAP#1#CIRCUITX",
        "sk": f"POLICY#{POLICY}",
    })
    with pytest.raises(ValueError, match="invalid admission snapshots"):
        AdmissionSnapshots(malformed, decoded.counters)


@pytest.mark.parametrize(
    "field,value",
    [
        ("reservation_id", "12345678-1234-1234-9234-123456789abc"),
        ("owner_digest", "A" * 64),
        ("identity_digest", "a" * 63),
        ("previous_identity_digest", DIGEST_A),
        ("epoch_minute", True),
        ("epoch_minute", -1),
        ("utc_day", "19700230"),
        ("reserved_tokens", True),
        ("reserved_tokens", 2561),
        ("reserved_micro_usd", True),
        ("reserved_micro_usd", 50_001),
        ("created_lower", True),
        ("created_lower", 59),
        ("created_upper", 121),
        ("lease_until", 75),
        ("expiry_shard", 2),
        ("policy_digest", "d" * 63),
        ("circuit_half_open_owner", DIGEST_B),
        ("policy_version", True),
        ("policy_version", 2),
        ("key_version", True),
        ("schema_version", 2),
    ],
)
def test_admission_handle_direct_attack_matrix(field, value):
    handle = compile_admission(
        request(), "preview-table", absent_snapshots(request())
    ).handle
    with pytest.raises(ValueError, match="invalid admission handle"):
        replace(handle, **{field: value})


def test_plan_factory_rejects_reservation_or_handle_parity_tampering():
    req = request()
    snapshots = absent_snapshots(req)
    plan = compile_admission(req, "preview-table", snapshots)
    read = plan.transact_get_items_request()
    write = plan.transact_write_items_request()
    write["TransactItems"][-1]["Put"]["Item"]["reserved_tokens"] = {"N": "513"}
    with pytest.raises(ValueError, match="invalid compiled admission plan"):
        compiler.CompiledAdmissionPlan._create(
            compiler._PLAN_FACTORY_TOKEN, req, snapshots, "preview-table",
            read, write, plan.handle,
            plan.action_count, plan.estimated_bytes,
        )
    other_handle = replace(
        plan.handle,
        reservation_id="87654321-4321-4321-8321-cba987654321",
    )
    with pytest.raises(ValueError, match="invalid compiled admission plan"):
        compiler.CompiledAdmissionPlan._create(
            compiler._PLAN_FACTORY_TOKEN,
            req, snapshots, "preview-table",
            plan.transact_get_items_request(),
            plan.transact_write_items_request(),
            other_handle,
            plan.action_count,
            plan.estimated_bytes,
        )


def test_plan_factory_rederives_and_rejects_every_action_tamper():
    req = request()
    specs = build_counter_specs(req)
    responses = [{}, *[
        {"Item": ddb_item(canonical(spec, value=0, version=0))}
        for spec in specs
    ]]
    snapshots = decode_transact_get_responses(req, responses)
    plan = compile_admission(req, "preview-table", snapshots)
    canonical_read = plan.transact_get_items_request()
    canonical_write = plan.transact_write_items_request()

    variants: list[tuple[dict, dict]] = []
    read_extra = copy.deepcopy(canonical_read)
    read_extra["raw_question"] = "secret"
    variants.append((read_extra, copy.deepcopy(canonical_write)))

    extra_item = copy.deepcopy(canonical_write)
    extra_item["TransactItems"][0]["Put"]["Item"]["raw_question"] = {"S": "secret"}
    variants.append((copy.deepcopy(canonical_read), extra_item))

    missing_item = copy.deepcopy(canonical_write)
    del missing_item["TransactItems"][0]["Put"]["Item"]["kind"]
    variants.append((copy.deepcopy(canonical_read), missing_item))

    predicate = copy.deepcopy(canonical_write)
    predicate["TransactItems"][0]["Put"]["ConditionExpression"] = "attribute_exists(pk)"
    variants.append((copy.deepcopy(canonical_read), predicate))

    predicate_value = copy.deepcopy(canonical_write)
    predicate_value["TransactItems"][0]["Put"]["ExpressionAttributeValues"][":value"] = {
        "N": "9"
    }
    variants.append((copy.deepcopy(canonical_read), predicate_value))

    circuit = copy.deepcopy(canonical_write)
    circuit["TransactItems"][len(specs)]["Put"]["Item"]["state"] = {"S": "open"}
    variants.append((copy.deepcopy(canonical_read), circuit))

    duplicate = copy.deepcopy(canonical_write)
    duplicate["TransactItems"][1] = copy.deepcopy(duplicate["TransactItems"][0])
    variants.append((copy.deepcopy(canonical_read), duplicate))

    reordered = copy.deepcopy(canonical_write)
    reordered["TransactItems"][0], reordered["TransactItems"][1] = (
        reordered["TransactItems"][1],
        reordered["TransactItems"][0],
    )
    variants.append((copy.deepcopy(canonical_read), reordered))

    for read, write in variants:
        forged_count = len(write["TransactItems"])
        forged_size = len(
            json.dumps(write, sort_keys=True, separators=(",", ":")).encode()
        )
        with pytest.raises(ValueError, match="invalid compiled admission plan"):
            compiler.CompiledAdmissionPlan._create(
                compiler._PLAN_FACTORY_TOKEN,
                req, snapshots, "preview-table", read, write, plan.handle,
                forged_count, forged_size,
            )
