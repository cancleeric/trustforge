from __future__ import annotations

from copy import deepcopy

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
    _control_item,
    admission_plan_fingerprint,
    append_quarantine_action,
    pre_provider_abort_intent,
)
from trustforge.preview_admission_executor import AdmissionOutcome, PreviewAdmissionExecutor
from trustforge.preview_terminal_reconcile import TerminalDisposition
from trustforge.preview_trusted_clock import TrustedBuckets, TrustedUtcInterval


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

    def get_item(self, **kwargs: object) -> object:
        self.get_calls.append(kwargs)
        return {"Item": deepcopy(self.item)} if self.item is not None else {}

    def put_item(self, **kwargs: object) -> object:
        self.put_calls.append(kwargs)
        response = self.put_response
        if isinstance(response, Exception):
            raise response
        if isinstance(response, dict) and response.get("ResponseMetadata", {}).get(
            "HTTPStatusCode"
        ) == 200:
            self.item = deepcopy(kwargs["Item"])
        return response


class IntegratedClient(FakeGateClient):
    def __init__(self, item: object, request: AdmissionCompileRequest):
        super().__init__(item)
        self.request = request
        self.read_calls: list[dict[str, object]] = []
        self.write_calls: list[dict[str, object]] = []

    def transact_get_items(self, **kwargs: object) -> object:
        self.read_calls.append(kwargs)
        return {
            "Responses": [
                {} for _ in range(len(build_counter_specs(self.request)) + 1)
            ]
        }

    def transact_write_items(self, **kwargs: object) -> object:
        self.write_calls.append(kwargs)
        actions = kwargs["TransactItems"]
        self.item = deepcopy(actions[-1]["Put"]["Item"])
        return _response()


def _open(generation: int = 0, version: int = 0) -> dict[str, object]:
    return _control_item(GateState.OPEN, generation, version, None)


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
    gate = DurableAdmissionGate(client, "preview-store")

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
    gate = DurableAdmissionGate(client, "preview-store")
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
    gate = DurableAdmissionGate(client, "preview-store")
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
    gate = DurableAdmissionGate(client, "preview-store")
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
    gate = DurableAdmissionGate(client, "preview-store")
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
    present = gate.prove_present(binding, plan.handle)
    assert present.disposition is ProofDisposition.PRESENT
    assert binding.reservation_id not in repr(present)
    intent = pre_provider_abort_intent(
        present,
        TrustedUtcInterval(
            plan.handle.created_upper + 1, plan.handle.created_upper + 1.1
        ),
    )
    assert intent.disposition is TerminalDisposition.PRE_PROVIDER_ABORT


def test_restart_retains_nominal_pending_binding_but_never_opens():
    client = FakeGateClient(_open())
    original = DurableAdmissionGate(client, "preview-store")
    plan = _plan()
    binding = original.begin(
        plan,
        dispatch_lower=plan.handle.created_lower,
        dispatch_upper=plan.handle.created_upper,
    )
    assert binding is not None
    client.item = _control_item(GateState.QUARANTINED, 1, 2, binding)

    replacement = DurableAdmissionGate(client, "preview-store")

    assert replacement.ready is False
    assert replacement.pending_binding == binding
    assert binding.reservation_id not in repr(replacement.pending_binding)


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
    gate = DurableAdmissionGate(client, "preview-store")
    executor = PreviewAdmissionExecutor(
        client, "preview-store", durable_gate=gate
    )

    result = executor.execute(request)

    assert result.outcome is AdmissionOutcome.ADMITTED
    assert gate.ready is True
    assert client.item["state"] == {"S": "open"}
    assert len(client.read_calls) == len(client.write_calls) == 1
    # startup, exact QUARANTINED proof; no speculative/retry reads
    assert len(client.get_calls) == 2
    # standalone DISPATCHING CAS and final OPEN CAS
    assert len(client.put_calls) == 2
    assert client.write_calls[0]["TransactItems"][-1]["Put"]["Item"]["state"] == {
        "S": "quarantined"
    }


def test_closed_startup_executor_performs_zero_admission_io():
    request = _request()
    client = IntegratedClient(None, request)
    gate = DurableAdmissionGate(client, "preview-store")

    result = PreviewAdmissionExecutor(
        client, "preview-store", durable_gate=gate
    ).execute(request)

    assert result.outcome is AdmissionOutcome.UNAVAILABLE
    assert client.read_calls == []
    assert client.write_calls == []
    assert client.put_calls == []


def test_executor_rejects_missing_mismatched_or_subclass_gate():
    request = _request()
    first = IntegratedClient(_open(), request)
    second = IntegratedClient(_open(), request)
    gate = DurableAdmissionGate(first, "preview-store")

    with pytest.raises(ValueError):
        PreviewAdmissionExecutor(first, "preview-store", durable_gate=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        PreviewAdmissionExecutor(second, "preview-store", durable_gate=gate)
    with pytest.raises(ValueError):
        PreviewAdmissionExecutor(first, "other-store", durable_gate=gate)

    class DerivedGate(DurableAdmissionGate):
        pass

    derived = DerivedGate(first, "preview-store")
    with pytest.raises(ValueError):
        PreviewAdmissionExecutor(first, "preview-store", durable_gate=derived)


def test_finalize_response_loss_accepts_only_exact_strong_open_proof():
    client = FakeGateClient(_open())
    gate = DurableAdmissionGate(client, "preview-store")
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
    gate = DurableAdmissionGate(client, "preview-store")
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
    replacement = DurableAdmissionGate(client, "preview-store")
    assert replacement.ready is False
    assert replacement.pending_binding == binding
