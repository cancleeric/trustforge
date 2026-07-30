from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
import threading

import boto3
from moto import mock_aws
import pytest

from trustforge.preview_admission_compiler import (
    AdmissionCompileRequest,
    build_counter_specs,
    compile_admission,
    decode_transact_get_responses,
)
from trustforge.preview_durable_admission_gate import (
    CONTROL_KEY,
    DurableAdmissionGate,
    GateState,
    ProofDisposition,
    QuarantineProof,
    RecoveryAuthority,
    _control_item,
    _reserved_item,
    admission_plan_fingerprint,
    append_quarantine_action,
)
from trustforge.preview_admission_executor import AdmissionOutcome, PreviewAdmissionExecutor
from trustforge.preview_terminal_reconcile import (
    CompiledTerminalPlan,
    TerminalDisposition,
    TerminalIntent,
    build_terminal_read_request,
    compile_terminal,
    decode_terminal_responses,
)
from trustforge.preview_trusted_clock import (
    PreviewTrustedClock,
    TrustedBuckets,
    TrustedUtcInterval,
)
from trustforge.quota_key_lifecycle import (
    DurableQuotaKeyLifecycleAuthority,
    LIFECYCLE_CONTROL_KEY,
    QuotaKeyLifecycle,
)
from tests.test_quota_key_lifecycle import _provider


def _request() -> AdmissionCompileRequest:
    return AdmissionCompileRequest(
        interval=TrustedUtcInterval(1_700_000_000.1, 1_700_000_000.2),
        buckets=TrustedBuckets(28_333_333, "20231114"),
        policy_digest="a" * 64,
        owner_digest="b" * 64,
        identity_digest="c" * 64,
        previous_identity_digest=None,
        reservation_id="12345678-1234-4234-9234-123456789abc",
        reserved_tokens=100,
        reserved_micro_usd=200,
        lifecycle_generation=1,
        current_quota_key_version=1,
        previous_quota_key_version=None,
    )


def _plan():
    request = _request()
    snapshots = decode_transact_get_responses(
        request, [{} for _ in range(len(build_counter_specs(request)) + 1)]
    )
    return compile_admission(request, "preview-store", snapshots)


def _response() -> dict[str, object]:
    return {"ResponseMetadata": {"HTTPStatusCode": 200, "RequestId": "ok"}}


class FakeGateClient:
    def __init__(self, item: object):
        self.item = deepcopy(item)
        self.get_calls: list[dict[str, object]] = []
        self.put_calls: list[dict[str, object]] = []
        self.put_response: object = _response()
        self.reservation_item: object = None
        self.transact_get_calls: list[dict[str, object]] = []
        self.transact_write_calls: list[dict[str, object]] = []
        self.trusted_second = 1_700_000_100
        self.lifecycle_item = None

    def describe_table(self, **kwargs: object) -> object:
        del kwargs
        date = datetime.fromtimestamp(self.trusted_second, UTC).strftime(
            "%a, %d %b %Y %H:%M:%S GMT"
        )
        return {"ResponseMetadata": {"HTTPHeaders": {"date": date}}}

    def get_item(self, **kwargs: object) -> object:
        if kwargs["Key"] == LIFECYCLE_CONTROL_KEY:
            return (
                {"Item": deepcopy(self.lifecycle_item)}
                if self.lifecycle_item is not None
                else {}
            )
        self.get_calls.append(kwargs)
        return {"Item": deepcopy(self.item)} if self.item is not None else {}

    def put_item(self, **kwargs: object) -> object:
        if kwargs.get("Item", {}).get("sk") == LIFECYCLE_CONTROL_KEY["sk"]:
            self.lifecycle_item = deepcopy(kwargs["Item"])
            return _response()
        self.put_calls.append(kwargs)
        response = self.put_response
        if isinstance(response, Exception):
            raise response
        if isinstance(response, dict) and response.get("ResponseMetadata", {}).get(
            "HTTPStatusCode"
        ) == 200:
            self.item = deepcopy(kwargs["Item"])
        return response

    def transact_get_items(self, **kwargs: object) -> object:
        self.transact_get_calls.append(kwargs)
        return {
            "Responses": [
                {"Item": deepcopy(self.item)} if self.item is not None else {},
                (
                    {"Item": deepcopy(self.reservation_item)}
                    if self.reservation_item is not None
                    else {}
                ),
            ]
        }

    def transact_write_items(self, **kwargs: object) -> object:
        self.transact_write_calls.append(kwargs)
        self.item = deepcopy(kwargs["TransactItems"][-1]["Put"]["Item"])
        return _response()


class IntegratedClient(FakeGateClient):
    def __init__(self, item: object, request: AdmissionCompileRequest):
        super().__init__(item)
        self.request = request
        self.read_calls: list[dict[str, object]] = []
        self.write_calls: list[dict[str, object]] = []

    def transact_get_items(self, **kwargs: object) -> object:
        self.read_calls.append(kwargs)
        if len(kwargs["TransactItems"]) == 2:
            return super().transact_get_items(**kwargs)
        return {
            "Responses": [
                {} for _ in range(len(build_counter_specs(self.request)) + 1)
            ]
        }

    def transact_write_items(self, **kwargs: object) -> object:
        self.write_calls.append(kwargs)
        actions = kwargs["TransactItems"]
        if len(actions) == 2:
            self.item = deepcopy(actions[-1]["Put"]["Item"])
        else:
            self.item = deepcopy(actions[-2]["Put"]["Item"])
            self.reservation_item = deepcopy(actions[-3]["Put"]["Item"])
        return _response()


def _authority(client) -> DurableQuotaKeyLifecycleAuthority:
    second = client.trusted_second
    provider = _provider({1: bytes(range(32))})
    authority = DurableQuotaKeyLifecycleAuthority(
        PreviewTrustedClock(
            dynamodb_client=client,
            table_name="preview-store",
            monotonic_clock=lambda: 0.0,
            wall_clock=lambda: float(second),
        ),
        dynamodb_client=client,
        table_name="preview-store",
        key_material_provider=provider,
    )
    authority.install(
        QuotaKeyLifecycle(
            1,
            TrustedUtcInterval(second - 50, second - 49),
            provider.bind_lifecycle(
                provider.load(
                parameter_name="/trustforge/quota",
                expected_version=1,
                key_id="quota-1",
                ),
                activated=second - 40,
            ),
        )
    )
    return authority


def _bound(authority, request):
    snapshot = authority.snapshot()
    return authority.bind_admission(
        request, authority.derive(snapshot, b"durable-gate-test")
    )


def _open(generation: int = 0, version: int = 0) -> dict[str, object]:
    return _control_item(GateState.OPEN, generation, version, None)


def _clock(client, second: float) -> PreviewTrustedClock:
    client.trusted_second = int(second)
    clock = PreviewTrustedClock(
        dynamodb_client=client,
        table_name="preview-store",
        monotonic_clock=lambda: 0.0,
        wall_clock=lambda: float(second),
    )
    clock.refresh()
    return clock


def _gate(client, table_name: str = "preview-store") -> DurableAdmissionGate:
    if hasattr(client, "trusted_second"):
        clock = _clock(client, float(client.trusted_second))
    else:
        clock = PreviewTrustedClock(
            dynamodb_client=client, table_name=table_name
        )
        clock.refresh()
    return DurableAdmissionGate(
        client, table_name, trusted_clock=clock
    )


@pytest.mark.parametrize(
    "item",
    [
        None,
        {},
        {"pk": CONTROL_KEY["pk"]},
        {**_open(), "unknown": {"S": "x"}},
        {**_open(), "state": {"S": "dispatching"}},
    ],
)
def test_startup_requires_strict_open_authority(item):
    client = FakeGateClient(item)
    gate = _gate(client)

    assert gate.ready is False
    assert client.get_calls == [
        {
            "TableName": "preview-store",
            "Key": CONTROL_KEY,
            "ConsistentRead": True,
        }
    ]
    assert client.put_calls == []


def test_begin_is_standalone_cas_and_transaction_atomically_quarantines():
    client = FakeGateClient(_open())
    gate = _gate(client)
    plan = _plan()

    binding = gate.begin(
        plan,
        dispatch_lower=plan.handle.created_lower,
        dispatch_upper=plan.handle.created_upper,
    )

    assert binding is not None
    assert gate.ready is False
    assert len(client.put_calls) == 1
    assert client.put_calls[0]["Item"]["state"] == {"S": "dispatching"}
    request = append_quarantine_action(plan, gate, binding)
    assert len(request["TransactItems"]) == plan.action_count + 1
    fence = request["TransactItems"][-1]["Put"]
    assert fence["Item"]["state"] == {"S": "quarantined"}
    assert fence["Item"]["plan_fingerprint"] == {
        "S": admission_plan_fingerprint(plan)
    }
    assert request["ClientRequestToken"] == plan.handle.reservation_id


def test_confirmed_rejection_reopens_exact_dispatch():
    client = FakeGateClient(_open())
    gate = _gate(client)
    plan = _plan()
    binding = gate.begin(
        plan,
        dispatch_lower=plan.handle.created_lower,
        dispatch_upper=plan.handle.created_upper,
    )

    assert binding is not None
    assert gate.confirm_rejected(binding) is True
    assert gate.ready is True
    assert client.item["state"] == {"S": "open"}
    assert set(client.item) == {
        "pk",
        "sk",
        "kind",
        "schema_version",
        "state",
        "generation",
        "version",
    }


def test_ambiguous_prewrite_stays_closed_and_does_not_repeat_io():
    client = FakeGateClient(_open())
    gate = _gate(client)
    client.put_response = TimeoutError("sensitive")
    plan = _plan()

    assert (
        gate.begin(
            plan,
            dispatch_lower=plan.handle.created_lower,
            dispatch_upper=plan.handle.created_upper,
        )
        is None
    )
    assert gate.ready is False
    assert (
        gate.begin(
            plan,
            dispatch_lower=plan.handle.created_lower,
            dispatch_upper=plan.handle.created_upper,
        )
        is None
    )
    assert len(client.put_calls) == 1


def test_present_requires_exact_strong_quarantine_and_absent_never_opens():
    client = FakeGateClient(_open())
    gate = _gate(client)
    plan = _plan()
    binding = gate.begin(
        plan,
        dispatch_lower=plan.handle.created_lower,
        dispatch_upper=plan.handle.created_upper,
    )
    assert binding is not None

    client.item = None
    absent = gate.prove_present(binding, plan.handle)
    assert absent.disposition is ProofDisposition.UNRESOLVED
    assert gate.ready is False

    client.item = _control_item(GateState.QUARANTINED, 1, 2, binding)
    client.reservation_item = _reserved_item(plan.handle)
    present = gate.prove_present(binding, plan.handle)
    assert present.disposition is ProofDisposition.PRESENT
    assert binding.reservation_id not in repr(present)
    authority = gate.pre_provider_abort_authority(present)
    assert authority.intent.disposition is TerminalDisposition.PRE_PROVIDER_ABORT


def test_restart_retains_nominal_pending_binding_but_never_opens():
    client = FakeGateClient(_open())
    original = _gate(client)
    plan = _plan()
    binding = original.begin(
        plan,
        dispatch_lower=plan.handle.created_lower,
        dispatch_upper=plan.handle.created_upper,
    )
    assert binding is not None
    client.item = _control_item(GateState.QUARANTINED, 1, 2, binding)

    replacement = _gate(client)

    assert replacement.ready is False
    assert replacement.pending_binding == binding
    assert binding.reservation_id not in repr(replacement.pending_binding)


@pytest.mark.parametrize(
    ("field_name", "wrong"),
    [
        ("owner_digest", {"S": "d" * 64}),
        ("identity_digest", {"S": "e" * 64}),
        ("policy_digest", {"S": "f" * 64}),
        ("reserved_tokens", {"N": "1"}),
        ("reserved_micro_usd", {"N": "1"}),
        ("epoch_minute", {"N": "28333332"}),
        ("utc_day", {"S": "20231113"}),
        ("key_version", {"N": "2"}),
        ("schema_version", {"N": "2"}),
        ("circuit_half_open_owner", {"S": "d" * 64}),
    ],
)
def test_pending_same_uuid_hostile_handle_fingerprint_is_unresolved(
    field_name, wrong
):
    client = FakeGateClient(_open())
    gate = _gate(client)
    plan = _plan()
    binding = gate.begin(
        plan,
        dispatch_lower=plan.handle.created_lower,
        dispatch_upper=plan.handle.created_upper,
    )
    assert binding is not None
    client.item = _control_item(GateState.QUARANTINED, 1, 2, binding)
    reservation = _reserved_item(plan.handle)
    reservation[field_name] = wrong
    client.reservation_item = reservation
    replacement = _gate(client)
    writes_before = len(client.transact_write_calls)

    proof = replacement.prove_pending_present()

    assert proof.disposition is ProofDisposition.UNRESOLVED
    assert replacement.ready is False
    assert len(client.transact_write_calls) == writes_before


def test_fingerprint_is_stable_domain_separated_sha256_without_secret():
    plan = _plan()

    first = admission_plan_fingerprint(plan)
    second = admission_plan_fingerprint(plan)

    assert first == second
    assert len(first) == 64
    assert plan.handle.reservation_id not in repr(first)


def test_executor_uses_shared_durable_gate_and_opens_before_admitted():
    request = _request()
    client = IntegratedClient(_open(), request)
    gate = _gate(client)
    authority = _authority(client)
    executor = PreviewAdmissionExecutor(
        client, "preview-store", durable_gate=gate, lifecycle_authority=authority
    )

    result = executor.execute(_bound(authority, request))

    assert result.outcome is AdmissionOutcome.ADMITTED
    assert gate.ready is True
    assert client.item["state"] == {"S": "open"}
    assert len(client.read_calls) == 2
    assert len(client.write_calls) == 2
    # Only startup is a GetItem; exact proof is one transactional snapshot.
    assert len(client.get_calls) == 1
    # Only DISPATCHING is standalone; final OPEN is transaction-bound.
    assert len(client.put_calls) == 1
    assert client.write_calls[0]["TransactItems"][-2]["Put"]["Item"]["state"] == {
        "S": "quarantined"
    }


def test_closed_startup_executor_performs_zero_admission_io():
    request = _request()
    client = IntegratedClient(None, request)
    gate = _gate(client)
    authority = _authority(client)

    result = PreviewAdmissionExecutor(
        client, "preview-store", durable_gate=gate, lifecycle_authority=authority
    ).execute(_bound(authority, request))

    assert result.outcome is AdmissionOutcome.UNAVAILABLE
    assert client.read_calls == []
    assert client.write_calls == []
    assert client.put_calls == []


@pytest.mark.parametrize("commit", [True, False])
def test_admission_finalize_response_loss_requires_exact_open_commit(commit):
    request = _request()

    class LostFinalizeClient(IntegratedClient):
        def transact_write_items(self, **kwargs: object) -> object:
            actions = kwargs["TransactItems"]
            if len(actions) == 2:
                self.write_calls.append(kwargs)
                if commit:
                    self.item = deepcopy(actions[-1]["Put"]["Item"])
                raise TimeoutError("lost finalize response")
            return super().transact_write_items(**kwargs)

    client = LostFinalizeClient(_open(), request)
    gate = _gate(client)
    authority = _authority(client)

    result = PreviewAdmissionExecutor(
        client, "preview-store", durable_gate=gate, lifecycle_authority=authority
    ).execute(_bound(authority, request))

    assert result.outcome is (
        AdmissionOutcome.ADMITTED if commit else AdmissionOutcome.UNAVAILABLE
    )
    assert gate.ready is commit


def test_executor_rejects_missing_mismatched_or_subclass_gate():
    request = _request()
    first = IntegratedClient(_open(), request)
    second = IntegratedClient(_open(), request)
    gate = _gate(first)
    authority = _authority(first)

    with pytest.raises(ValueError):
        PreviewAdmissionExecutor(first, "preview-store", durable_gate=None, lifecycle_authority=authority)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        PreviewAdmissionExecutor(second, "preview-store", durable_gate=gate, lifecycle_authority=authority)
    with pytest.raises(ValueError):
        PreviewAdmissionExecutor(first, "other-store", durable_gate=gate, lifecycle_authority=authority)

    class DerivedGate(DurableAdmissionGate):
        pass

    derived = DerivedGate(
        first, "preview-store", trusted_clock=gate._trusted_clock
    )
    with pytest.raises(ValueError):
        PreviewAdmissionExecutor(first, "preview-store", durable_gate=derived, lifecycle_authority=authority)


def test_finalize_response_loss_accepts_only_exact_strong_open_proof():
    client = FakeGateClient(_open())
    gate = _gate(client)
    plan = _plan()
    binding = gate.begin(
        plan,
        dispatch_lower=plan.handle.created_lower,
        dispatch_upper=plan.handle.created_upper,
    )
    assert binding is not None

    original_put = client.put_item

    def commit_then_timeout(**kwargs: object) -> object:
        original_put(**kwargs)
        raise TimeoutError("lost")

    client.put_item = commit_then_timeout  # type: ignore[method-assign]

    assert gate.confirm_rejected(binding) is True
    assert gate.ready is True


def test_finalize_response_loss_without_commit_remains_closed():
    client = FakeGateClient(_open())
    gate = _gate(client)
    plan = _plan()
    binding = gate.begin(
        plan,
        dispatch_lower=plan.handle.created_lower,
        dispatch_upper=plan.handle.created_upper,
    )
    assert binding is not None
    client.put_response = TimeoutError("lost")

    assert gate.confirm_rejected(binding) is False
    assert gate.ready is False
    replacement = _gate(client)
    assert replacement.ready is False
    assert replacement.pending_binding == binding


@pytest.mark.parametrize(
    ("field_name", "wrong"),
    [
        ("owner_digest", "d" * 64),
        ("identity_digest", "e" * 64),
        ("reserved_tokens", 1),
        ("reserved_micro_usd", 1),
        ("policy_digest", "f" * 64),
        ("lease_until", 1_700_000_099),
    ],
)
def test_same_uuid_forged_handle_cannot_mint_present(field_name, wrong):
    client = FakeGateClient(_open())
    gate = _gate(client)
    plan = _plan()
    binding = gate.begin(
        plan,
        dispatch_lower=plan.handle.created_lower,
        dispatch_upper=plan.handle.created_upper,
    )
    assert binding is not None
    client.item = _control_item(GateState.QUARANTINED, 1, 2, binding)
    client.reservation_item = _reserved_item(plan.handle)
    try:
        forged = replace(plan.handle, **{field_name: wrong})
    except ValueError:
        return

    proof = gate.prove_present(binding, forged)

    assert proof.disposition is ProofDisposition.UNRESOLVED
    with pytest.raises(ValueError):
        gate.pre_provider_abort_authority(proof)


def test_caller_cannot_construct_or_cross_gate_reuse_present_proof():
    with pytest.raises(TypeError):
        QuarantineProof(  # type: ignore[call-arg]
            ProofDisposition.PRESENT, _plan().handle, None
        )
    plan = _plan()
    client = FakeGateClient(_open())
    first = _gate(client)
    binding = first.begin(
        plan,
        dispatch_lower=plan.handle.created_lower,
        dispatch_upper=plan.handle.created_upper,
    )
    assert binding is not None
    client.item = _control_item(GateState.QUARANTINED, 1, 2, binding)
    client.reservation_item = _reserved_item(plan.handle)
    proof = first.prove_present(binding, plan.handle)
    second = _gate(client)

    with pytest.raises(ValueError):
        second.pre_provider_abort_authority(proof)


def test_recovery_authority_rejects_forged_or_mismatched_clock():
    plan = _plan()
    client = FakeGateClient(_open())
    gate = _gate(client)
    binding = gate.begin(
        plan,
        dispatch_lower=plan.handle.created_lower,
        dispatch_upper=plan.handle.created_upper,
    )
    assert binding is not None
    client.item = _control_item(GateState.QUARANTINED, 1, 2, binding)
    client.reservation_item = _reserved_item(plan.handle)
    proof = gate.prove_present(binding, plan.handle)

    with pytest.raises(TypeError):
        RecoveryAuthority(proof, None, None, None)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        gate.pre_provider_abort_authority(  # type: ignore[arg-type]
            proof,
            TrustedUtcInterval(
                plan.handle.lease_until + 1, plan.handle.lease_until + 2
            ),
        )
    other = FakeGateClient(_open())
    with pytest.raises(TypeError):
        gate.pre_provider_abort_authority(
            proof, _clock(other, plan.handle.lease_until + 1)
        )
    wrong_table = PreviewTrustedClock(
        dynamodb_client=client,
        table_name="other-store",
        monotonic_clock=lambda: 0.0,
        wall_clock=lambda: float(plan.handle.lease_until + 1),
    )
    with pytest.raises(TypeError):
        gate.pre_provider_abort_authority(proof, wrong_table)


def test_gate_constructor_binds_exact_matching_trusted_clock():
    client = FakeGateClient(_open())
    clock = _clock(client, client.trusted_second)
    gate = DurableAdmissionGate(
        client, "preview-store", trusted_clock=clock
    )

    assert gate._trusted_clock is clock
    with pytest.raises(TypeError):
        DurableAdmissionGate(client, "preview-store")  # type: ignore[call-arg]
    other = FakeGateClient(_open())
    with pytest.raises(ValueError):
        DurableAdmissionGate(
            client,
            "preview-store",
            trusted_clock=_clock(other, other.trusted_second),
        )
    wrong_table = PreviewTrustedClock(
        dynamodb_client=client,
        table_name="other-store",
    )
    with pytest.raises(ValueError):
        DurableAdmissionGate(
            client, "preview-store", trusted_clock=wrong_table
        )


def test_invalid_proof_inputs_fail_closed_without_raising():
    plan = _plan()
    client = IntegratedClient(_open(), _request())
    gate = _gate(client)

    assert gate.prove_present(None, plan.handle).disposition is ProofDisposition.UNRESOLVED
    assert gate.prove_present(object(), object()).disposition is ProofDisposition.UNRESOLVED


@pytest.mark.parametrize("reservation", [None, {}, {"status": {"S": "terminal"}}])
def test_control_only_or_malformed_reservation_is_unresolved(reservation):
    client = FakeGateClient(_open())
    gate = _gate(client)
    plan = _plan()
    binding = gate.begin(
        plan,
        dispatch_lower=plan.handle.created_lower,
        dispatch_upper=plan.handle.created_upper,
    )
    assert binding is not None
    client.item = _control_item(GateState.QUARANTINED, 1, 2, binding)
    client.reservation_item = reservation

    assert (
        gate.prove_present(binding, plan.handle).disposition
        is ProofDisposition.UNRESOLVED
    )
    assert gate.ready is False


@pytest.mark.parametrize("mode", ["backend", "malformed", "partial"])
def test_transactional_proof_failure_never_mints_present(mode):
    client = FakeGateClient(_open())
    gate = _gate(client)
    plan = _plan()
    binding = gate.begin(
        plan,
        dispatch_lower=plan.handle.created_lower,
        dispatch_upper=plan.handle.created_upper,
    )
    assert binding is not None
    client.item = _control_item(GateState.QUARANTINED, 1, 2, binding)
    client.reservation_item = _reserved_item(plan.handle)

    def hostile(**kwargs: object) -> object:
        client.transact_get_calls.append(kwargs)
        if mode == "backend":
            raise TimeoutError("raw backend secret")
        if mode == "partial":
            return {"Responses": [{"Item": client.item}]}
        return {"Responses": "not-a-list"}

    client.transact_get_items = hostile  # type: ignore[method-assign]

    proof = gate.prove_present(binding, plan.handle)

    assert proof.disposition is ProofDisposition.UNRESOLVED
    assert "secret" not in repr(proof)
    assert gate.ready is False


@pytest.mark.parametrize("earliest_delta", [0.0, -0.1])
def test_pre_provider_abort_requires_trusted_lease_expiry(earliest_delta):
    client = FakeGateClient(_open())
    plan = _plan()
    client.trusted_second = int(plan.handle.lease_until + earliest_delta)
    gate = _gate(client)
    binding = gate.begin(
        plan,
        dispatch_lower=plan.handle.created_lower,
        dispatch_upper=plan.handle.created_upper,
    )
    assert binding is not None
    client.item = _control_item(GateState.QUARANTINED, 1, 2, binding)
    client.reservation_item = _reserved_item(plan.handle)
    proof = gate.prove_present(binding, plan.handle)

    with pytest.raises(ValueError):
        gate.pre_provider_abort_authority(proof)


@mock_aws
def test_execute_finalize_vs_recovery_terminal_barrier_has_one_winner():
    raw = boto3.client("dynamodb", region_name="us-east-1")
    raw.create_table(
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
    plan = _plan()
    raw.put_item(TableName="preview-store", Item=_open())
    seed_gate = _gate(raw)
    binding = seed_gate.begin(
        plan,
        dispatch_lower=plan.handle.created_lower,
        dispatch_upper=plan.handle.created_upper,
    )
    assert binding is not None
    raw.transact_write_items(**append_quarantine_action(plan, seed_gate, binding))
    reserved = _reserved_item(plan.handle)

    barrier = threading.Barrier(2, timeout=3)

    class BarrierClient:
        def get_item(self, **kwargs):
            return raw.get_item(**kwargs)

        def put_item(self, **kwargs):
            return raw.put_item(**kwargs)

        def transact_get_items(self, **kwargs):
            return raw.transact_get_items(**kwargs)

        def transact_write_items(self, **kwargs):
            barrier.wait()
            return raw.transact_write_items(**kwargs)

        def describe_table(self, **kwargs):
            return raw.describe_table(**kwargs)

    client = BarrierClient()
    gate = _gate(client)
    proof = gate.prove_present(binding, plan.handle)
    authority = gate.pre_provider_abort_authority(proof)
    intent = authority.intent
    terminal_read = raw.transact_get_items(
        **build_terminal_read_request(intent, "preview-store")
    )
    terminal_plan = compile_terminal(
        intent,
        "preview-store",
        decode_terminal_responses(intent, terminal_read["Responses"]),
    )
    recovery = gate.append_recovery_open_action(authority, terminal_plan)
    outcomes: list[str] = []

    def finalize() -> None:
        outcomes.append(
            "admitted" if gate.confirm_admitted(binding, plan.handle) else "closed"
        )

    def recover() -> None:
        try:
            client.transact_write_items(**recovery)
            outcomes.append("recovered")
        except Exception:
            outcomes.append("lost")

    threads = [threading.Thread(target=finalize), threading.Thread(target=recover)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert ("admitted" in outcomes) != ("recovered" in outcomes)
    reservation = raw.get_item(
        TableName="preview-store",
        Key={"pk": reserved["pk"], "sk": reserved["sk"]},
        ConsistentRead=True,
    )["Item"]
    assert (reservation["status"]["S"] == "reserved") == (
        "admitted" in outcomes
    )


@mock_aws
def test_confirmed_stale_open_cas_refreshes_for_later_generation():
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
    winner = _gate(client)
    stale = _gate(client)
    plan = _plan()
    first = winner.begin(
        plan,
        dispatch_lower=plan.handle.created_lower,
        dispatch_upper=plan.handle.created_upper,
    )
    assert first is not None
    client.transact_write_items(**append_quarantine_action(plan, winner, first))
    assert winner.confirm_admitted(first, plan.handle) is True

    assert (
        stale.begin(
            plan,
            dispatch_lower=plan.handle.created_lower,
            dispatch_upper=plan.handle.created_upper,
        )
        is None
    )
    assert stale.ready is True
    following = stale.begin(
        plan,
        dispatch_lower=plan.handle.created_lower,
        dispatch_upper=plan.handle.created_upper,
    )
    assert following is not None
    assert following.generation == 2


@mock_aws
def test_recovery_accepts_only_sealed_canonical_matching_terminal_plan():
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
    client.put_item(TableName="preview-store", Item=_open())
    gate = _gate(client)
    admission = _plan()
    binding = gate.begin(
        admission,
        dispatch_lower=admission.handle.created_lower,
        dispatch_upper=admission.handle.created_upper,
    )
    assert binding is not None
    client.transact_write_items(
        **append_quarantine_action(admission, gate, binding)
    )
    proof = gate.prove_present(binding, admission.handle)
    authority = gate.pre_provider_abort_authority(proof)
    intent = authority.intent
    interval = intent.interval
    read = client.transact_get_items(
        **build_terminal_read_request(intent, "preview-store")
    )
    snapshot = decode_terminal_responses(intent, read["Responses"])
    sealed = compile_terminal(intent, "preview-store", snapshot)
    other_gate = _gate(client)
    with pytest.raises(ValueError):
        other_gate.append_recovery_open_action(authority, sealed)
    composed = gate.append_recovery_open_action(authority, sealed)
    assert composed["ClientRequestToken"] != sealed.client_request_token()
    assert (
        gate.append_recovery_open_action(authority, sealed)["ClientRequestToken"]
        == composed["ClientRequestToken"]
    )
    assert all(
        next(iter(action.values()))["TableName"] == "preview-store"
        for action in sealed.transact_write_items_request()["TransactItems"]
    )

    with pytest.raises(ValueError):
        gate.append_recovery_open_action(authority, {})  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        CompiledTerminalPlan({}, False, intent)  # type: ignore[call-arg]

    mutated = sealed.transact_write_items_request()
    mutated["TransactItems"][0]["Put"]["ConditionExpression"] = "forged"
    mutated["TransactItems"].append({"Delete": {}})
    appended = gate.append_recovery_open_action(authority, sealed)
    assert appended["TransactItems"][0]["Put"]["ConditionExpression"] != "forged"
    assert all("Delete" not in action for action in appended["TransactItems"])

    wrong_disposition = TerminalIntent(
        admission.handle,
        interval,
        TerminalDisposition.KNOWN_FAILURE,
        actual_tokens=0,
        actual_micro_usd=0,
    )
    wrong_read = client.transact_get_items(
        **build_terminal_read_request(wrong_disposition, "preview-store")
    )
    wrong_plan = compile_terminal(
        wrong_disposition,
        "preview-store",
        decode_terminal_responses(wrong_disposition, wrong_read["Responses"]),
    )
    with pytest.raises(ValueError):
        gate.append_recovery_open_action(authority, wrong_plan)

    wrong_table = compile_terminal(intent, "other-store", snapshot)
    assert all(
        next(iter(action.values()))["TableName"] == "other-store"
        for action in wrong_table.transact_write_items_request()["TransactItems"]
    )
    with pytest.raises(ValueError):
        gate.append_recovery_open_action(authority, wrong_table)

    forged_handle = replace(admission.handle, reserved_tokens=1)
    forged_intent = TerminalIntent(
        forged_handle, interval, TerminalDisposition.PRE_PROVIDER_ABORT
    )
    forged_read = client.transact_get_items(
        **build_terminal_read_request(forged_intent, "preview-store")
    )
    with pytest.raises(ValueError):
        decode_terminal_responses(forged_intent, forged_read["Responses"])

    terminal_request = sealed.transact_write_items_request()
    client.transact_write_items(**terminal_request)
    replay_read = client.transact_get_items(
        **build_terminal_read_request(intent, "preview-store")
    )
    replay = compile_terminal(
        intent,
        "preview-store",
        decode_terminal_responses(intent, replay_read["Responses"]),
    )
    assert replay.replay is True
    with pytest.raises(ValueError):
        gate.append_recovery_open_action(authority, replay)


@pytest.mark.parametrize("earliest_delta", [-0.1, 0.0])
@mock_aws
def test_direct_external_unexpired_terminal_plan_rejected_at_composition(
    earliest_delta,
):
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
    client.put_item(TableName="preview-store", Item=_open())
    gate = _gate(client)
    admission = _plan()
    binding = gate.begin(
        admission,
        dispatch_lower=admission.handle.created_lower,
        dispatch_upper=admission.handle.created_upper,
    )
    assert binding is not None
    client.transact_write_items(
        **append_quarantine_action(admission, gate, binding)
    )
    proof = gate.prove_present(binding, admission.handle)
    authority = gate.pre_provider_abort_authority(proof)
    interval = TrustedUtcInterval(
        admission.handle.lease_until + earliest_delta,
        admission.handle.lease_until + 0.1,
    )
    direct = TerminalIntent(
        admission.handle, interval, TerminalDisposition.PRE_PROVIDER_ABORT
    )
    read = client.transact_get_items(
        **build_terminal_read_request(direct, "preview-store")
    )
    plan = compile_terminal(
        direct,
        "preview-store",
        decode_terminal_responses(direct, read["Responses"]),
    )

    with pytest.raises(ValueError):
        gate.append_recovery_open_action(authority, plan)
