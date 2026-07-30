from __future__ import annotations

from types import SimpleNamespace
import uuid

import pytest

import trustforge.hermes_preview_control_plane as control
from trustforge.hermes_preview_control_plane import (
    HermesPreviewControlPlane,
    PreviewControlStatus,
    PreviewObservation,
    PreviewPolicy,
    PreviewRequest,
    PreviewTopology,
    PlannerPortFailure,
    PlannerResult,
    PreviewTerminalClass,
    TrustedProxyPolicy,
    ZeroSensitiveObserver,
)
from trustforge.preview_admission_deployment import (
    PreviewAdmissionProductionRuntime,
)
from trustforge.preview_admission_executor import AdmissionOutcome
from trustforge.preview_terminal_reconcile import TerminalOutcome
from trustforge.preview_trusted_clock import (
    TrustedBuckets,
    TrustedUtcInterval,
)


POLICY_DIGEST = "a" * 64


class Tokenizer:
    package = "hermes-tokenizer"
    version = "hermes-tokenizer-v1"
    vocab_hash = "b" * 64

    def __init__(self, count=100):
        self.value = count
        self.payloads = []

    def count(self, payload):
        self.payloads.append(payload)
        return self.value


class Planner:
    identity = "hermes-planner-port-v1"
    model_id = "approved-model-v1"
    capabilities = ("plan",)

    def __init__(self, result=None):
        self.result = result or PlannerResult({"strategy": "internal"}, 50, 100)
        self.calls = []

    def plan(self, payload, **kwargs):
        self.calls.append((payload, kwargs))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _policy(**changes):
    values = {
        "policy_digest": POLICY_DIGEST,
        "source_policy_version": "sources-v1",
        "model_price_policy_version": "price-v1",
        "tokenizer_version": "hermes-tokenizer-v1",
        "tokenizer_package": "hermes-tokenizer",
        "tokenizer_vocab_hash": "b" * 64,
        "allowed_model_id": "approved-model-v1",
        "planner_identity": "hermes-planner-port-v1",
        "input_micro_usd_per_million_tokens": 3_906_250,
        "output_micro_usd_per_million_tokens": 3_906_250,
        "valid_from_epoch": 0,
        "valid_until_epoch": 10_000,
    }
    values.update(changes)
    return PreviewPolicy(**values)


def _request(question="分析任何我指定的投資意圖，不限制題型"):
    return PreviewRequest(
        question=question,
        locale="zh-TW",
        asset_hints=("BTC", "ETH"),
        client_request_id=str(uuid.uuid4()),
    )


def _runtime(outcome=AdmissionOutcome.DENIED):
    class Clock:
        def trusted_interval(self):
            return TrustedUtcInterval(120, 120)

        def buckets(self):
            return TrustedBuckets(2, "19700101")

    lifecycle = SimpleNamespace(
        generation=1,
        current=SimpleNamespace(version=1),
        previous=None,
    )
    snapshot = SimpleNamespace(lifecycle=lifecycle)

    class Authority:
        def snapshot(self):
            return snapshot

        def derive(self, selected, identity):
            assert selected is snapshot
            assert identity.startswith(b"pap1-client-ip:")
            return object()

        def bind_admission(self, request, digests):
            assert request.identity_digest == "0" * 64
            return SimpleNamespace(request=request, digests=digests)

    class Executor:
        def __init__(self):
            self._lifecycle_authority = Authority()
            self._durable_gate = SimpleNamespace(_trusted_clock=Clock())
            self.requests = []

        def execute(self, bound):
            self.requests.append(bound)
            return SimpleNamespace(
                outcome=outcome,
                handle=SimpleNamespace(
                    reserved_tokens=bound.request.reserved_tokens,
                    reserved_micro_usd=bound.request.reserved_micro_usd,
                )
                if outcome is AdmissionOutcome.ADMITTED
                else None,
            )

    class Terminal:
        def __init__(self):
            self.intents = []

        def reconcile(self, intent):
            self.intents.append(intent)
            return SimpleNamespace(outcome=TerminalOutcome.RECONCILED)

    runtime = object.__new__(PreviewAdmissionProductionRuntime)
    object.__setattr__(runtime, "executor", Executor())
    object.__setattr__(runtime, "terminal", Terminal())
    object.__setattr__(runtime, "lease_recovery", object())
    object.__setattr__(runtime, "ambiguity_recovery", object())
    return runtime


class Monotonic:
    def __init__(self, values=None):
        self.values = list(values or [0])
        self.last = self.values[-1]

    def now(self):
        if self.values:
            self.last = self.values.pop(0)
        return self.last


def _plane(
    outcome=AdmissionOutcome.DENIED,
    tokenizer=None,
    planner=None,
    monotonic=None,
):
    runtime = _runtime(outcome)
    deadline_clock = monotonic or Monotonic()
    observer = ZeroSensitiveObserver(clock=Monotonic())
    plane = HermesPreviewControlPlane(
        runtime=runtime,
        policy=_policy(),
        tokenizer=tokenizer or Tokenizer(),
        planner=planner or Planner(),
        topology=PreviewTopology(
            "127.0.0.1",
            "https://trustforge.example",
            TrustedProxyPolicy(("127.0.0.1", "10.0.0.1")),
            True,
        ),
        monotonic=deadline_clock,
        observer=observer,
    )
    return plane, runtime, observer


def test_arbitrary_intent_is_not_question_type_topic_or_coin_whitelisted():
    for question in (
        "BTC 適合長期配置嗎？",
        "比較三個未知資產並自行找出需要的分析角度",
        "What risks have I failed to ask about?",
        "混合語言 intent with no predefined question type",
    ):
        assert _request(question).question == question


@pytest.mark.parametrize(
    "factory",
    [
        lambda: PreviewRequest("", "zh-TW"),
        lambda: PreviewRequest("x" * 1001, "zh-TW"),
        lambda: PreviewRequest("ok", "fr"),
        lambda: PreviewRequest("ok", "en", tuple(str(i) for i in range(9))),
        lambda: PreviewRequest("ok", "en", ("bad symbol!",)),
        lambda: PreviewRequest("ok", "en", client_request_id=str(uuid.uuid1())),
    ],
)
def test_request_contract_is_strict(factory):
    with pytest.raises(ValueError):
        factory()


def test_canonical_payload_is_exact_and_does_not_add_routing_fields():
    payload = _request("任意問題").canonical_payload()
    assert payload == (
        '{"asset_hints":["BTC","ETH"],"locale":"zh-TW",'
        '"question":"任意問題"}'
    ).encode()
    assert b"question_type" not in payload
    assert b"topic" not in payload


def test_policy_is_fixed_and_silent_cap_relaxation_is_rejected():
    with pytest.raises(ValueError, match="invalid preview policy"):
        _policy(global_concurrency=5)
    with pytest.raises(ValueError, match="invalid preview policy"):
        _policy(input_micro_usd_per_million_tokens=25_000_000)


def test_proxy_identity_requires_trusted_peer_and_canonical_ip():
    policy = TrustedProxyPolicy(("127.0.0.1",))
    assert policy.canonical_identity(
        peer_ip="127.0.0.1", canonical_client_ip="2001:db8::1"
    ) == b"pap1-client-ip:2001:db8::1"
    with pytest.raises(ValueError, match="unsafe ingress"):
        policy.canonical_identity(
            peer_ip="203.0.113.8", canonical_client_ip="1.2.3.4"
        )
    with pytest.raises(ValueError, match="unsafe ingress"):
        policy.canonical_identity(
            peer_ip="127.0.0.1", canonical_client_ip="forged, 1.2.3.4"
        )
    with pytest.raises(ValueError, match="invalid trusted proxy policy"):
        TrustedProxyPolicy(("127.0.0.0/8",))


def test_admission_uses_exact_token_reservation_and_merged_executor():
    tokenizer = Tokenizer(100)
    plane, runtime, observer = _plane(AdmissionOutcome.DENIED, tokenizer)
    result = plane.admit(
        _request(), peer_ip="127.0.0.1", canonical_client_ip="203.0.113.2"
    )
    assert result.status is PreviewControlStatus.DENIED
    bound = runtime.executor.requests[0]
    assert bound.request.reserved_tokens == 612
    assert bound.request.reserved_micro_usd == 2_391
    assert tokenizer.payloads == [_request().canonical_payload()]
    assert observer.events == [
        PreviewObservation("denied", None, False, 1)
    ]


def test_tokenizer_overflow_and_unsafe_proxy_fail_before_executor():
    plane, runtime, _ = _plane(tokenizer=Tokenizer(2049))
    result = plane.admit(
        _request(), peer_ip="127.0.0.1", canonical_client_ip="203.0.113.2"
    )
    assert result.status is PreviewControlStatus.UNAVAILABLE
    assert runtime.executor.requests == []


def test_stale_price_policy_fails_before_executor():
    runtime = _runtime()
    observer = ZeroSensitiveObserver(clock=Monotonic())
    plane = HermesPreviewControlPlane(
        runtime=runtime,
        policy=_policy(valid_until_epoch=120),
        tokenizer=Tokenizer(),
        planner=Planner(),
        topology=PreviewTopology(
            "127.0.0.1",
            "https://trustforge.example",
            TrustedProxyPolicy(("127.0.0.1",)),
            True,
        ),
        monotonic=Monotonic(),
        observer=observer,
    )
    result = plane.admit(
        _request(), peer_ip="127.0.0.1", canonical_client_ip="203.0.113.2"
    )
    assert result.status is PreviewControlStatus.UNAVAILABLE
    assert runtime.executor.requests == []
    spoofed = plane.admit(
        _request(), peer_ip="203.0.113.2", canonical_client_ip="1.2.3.4"
    )
    assert spoofed.status is PreviewControlStatus.UNAVAILABLE
    assert runtime.executor.requests == []


def test_circuit_classification_excludes_hostile_and_schema_output():
    circuit = {
        value
        for value in PreviewTerminalClass
        if value.counts_for_circuit
    }
    assert circuit == {
        PreviewTerminalClass.PROVIDER_TRANSPORT,
        PreviewTerminalClass.PROVIDER_TIMEOUT,
        PreviewTerminalClass.PROVIDER_THROTTLE,
        PreviewTerminalClass.PROVIDER_5XX,
    }
    assert not PreviewTerminalClass.HOSTILE_OUTPUT.counts_for_circuit
    assert not PreviewTerminalClass.SCHEMA_FAILURE.counts_for_circuit


def test_terminal_unknown_usage_is_worst_case_and_releases_once(monkeypatch):
    plane, runtime, observer = _plane(AdmissionOutcome.ADMITTED)
    admission = plane.admit(
        _request(), peer_ip="127.0.0.1", canonical_client_ip="203.0.113.2"
    )
    captured = []

    class Intent:
        def __init__(self, **kwargs):
            captured.append(kwargs)

    monkeypatch.setattr(control, "TerminalIntent", Intent)
    assert plane.reconcile(
        admission, terminal_class=PreviewTerminalClass.PROVIDER_TIMEOUT
    )
    assert captured[0]["actual_tokens"] is None
    assert captured[0]["actual_micro_usd"] is None
    assert captured[0]["disposition"] is control.TerminalDisposition.UNCERTAIN
    assert len(runtime.terminal.intents) == 1
    assert observer.events[-1].circuit_failure is True


def test_execute_binds_attempt_timeout_deadline_and_known_usage(monkeypatch):
    planner = Planner()
    plane, runtime, _ = _plane(AdmissionOutcome.ADMITTED, planner=planner)
    captured = []

    class Intent:
        def __init__(self, **kwargs):
            captured.append(kwargs)

    monkeypatch.setattr(control, "TerminalIntent", Intent)
    result = plane.execute(
        _request(), peer_ip="127.0.0.1", canonical_client_ip="203.0.113.2"
    )
    assert result.status is PreviewControlStatus.ADMITTED
    assert planner.calls[0][1] == {
        "max_output_tokens": 512,
        "provider_deadline": 5.0,
        "total_deadline": 6.0,
        "attempts": 1,
    }
    assert captured[0]["actual_tokens"] == 50
    assert captured[0]["actual_micro_usd"] == 100
    assert len(runtime.terminal.intents) == 1


def test_schema_failure_is_uncertain_worst_case_and_not_circuit(monkeypatch):
    planner = Planner(RuntimeError("local adapter failure"))
    plane, _, observer = _plane(AdmissionOutcome.ADMITTED, planner=planner)
    captured = []

    class Intent:
        def __init__(self, **kwargs):
            captured.append(kwargs)

    monkeypatch.setattr(control, "TerminalIntent", Intent)
    result = plane.execute(
        _request(), peer_ip="127.0.0.1", canonical_client_ip="203.0.113.2"
    )
    assert result.status is PreviewControlStatus.UNAVAILABLE
    assert captured[0]["actual_tokens"] is None
    assert captured[0]["disposition"] is control.TerminalDisposition.UNCERTAIN
    assert captured[0]["circuit_failure"] is False
    assert observer.events[-1].circuit_failure is False


def test_observer_cannot_capture_question_payload_or_provider_exception():
    observer = ZeroSensitiveObserver(clock=Monotonic())
    with pytest.raises(ValueError, match="invalid preview observation"):
        observer.record({"question": "secret"})
    observer.record(PreviewObservation("denied", None, False, 1))
    rendered = repr(observer.events)
    assert "question" not in rendered
    assert "provider exception" not in rendered


@pytest.mark.parametrize("count,expected", [(1, 2_004), (100, 2_391), (2048, 10_000)])
def test_reservation_price_is_exact_ceil_for_actual_input(count, expected):
    plane, runtime, _ = _plane(tokenizer=Tokenizer(count))
    plane.admit(
        _request(), peer_ip="127.0.0.1", canonical_client_ip="203.0.113.2"
    )
    assert runtime.executor.requests[0].request.reserved_micro_usd == expected


@pytest.mark.parametrize(
    "component,attribute,value",
    [
        ("tokenizer", "package", "other-package"),
        ("tokenizer", "version", "other-version"),
        ("tokenizer", "vocab_hash", "c" * 64),
        ("planner", "identity", "other-planner"),
        ("planner", "model_id", "other-model"),
        ("planner", "capabilities", ("plan", "tools")),
    ],
)
def test_component_policy_mismatch_rejected_before_io(component, attribute, value):
    tokenizer, planner = Tokenizer(), Planner()
    setattr(tokenizer if component == "tokenizer" else planner, attribute, value)
    with pytest.raises(ValueError, match="invalid preview control plane"):
        HermesPreviewControlPlane(
            runtime=_runtime(),
            policy=_policy(),
            tokenizer=tokenizer,
            planner=planner,
            topology=PreviewTopology(
                "127.0.0.1",
                "https://trustforge.example",
                TrustedProxyPolicy(("127.0.0.1",)),
                True,
            ),
            monotonic=Monotonic(),
            observer=ZeroSensitiveObserver(clock=Monotonic()),
        )
    assert tokenizer.payloads == []
    assert planner.calls == []


@pytest.mark.parametrize(
    "args",
    [
        ("0.0.0.0", "https://trustforge.example", True),
        ("127.0.0.1", "http://trustforge.example", True),
        ("127.0.0.1", "https://*.example", True),
        ("127.0.0.1", "https://trustforge.example/path", True),
        ("127.0.0.1", "https://trustforge.example", False),
    ],
)
def test_topology_rejects_non_nominal_capability(args):
    with pytest.raises(ValueError, match="invalid preview topology"):
        PreviewTopology(
            args[0], args[1], TrustedProxyPolicy(("127.0.0.1",)), args[2]
        )


def test_observer_is_capacity_and_retention_bounded():
    clock = Monotonic([0, 1, 2, 10])
    observer = ZeroSensitiveObserver(clock=clock, retention_seconds=5, capacity=2)
    observation = PreviewObservation("denied", None, False, 1)
    observer.record(observation)
    observer.record(observation)
    observer.record(observation)
    assert observer.events == []


def test_provider_overrun_is_timeout_and_uncertain(monkeypatch):
    plane, _, _ = _plane(
        AdmissionOutcome.ADMITTED, monotonic=Monotonic([0, 0, 5.1])
    )
    captured = []
    monkeypatch.setattr(
        control, "TerminalIntent", lambda **kwargs: captured.append(kwargs)
    )
    result = plane.execute(
        _request(), peer_ip="127.0.0.1", canonical_client_ip="203.0.113.2"
    )
    assert result.status is PreviewControlStatus.UNAVAILABLE
    assert captured[0]["disposition"] is control.TerminalDisposition.UNCERTAIN
    assert captured[0]["circuit_failure"] is True


def test_total_deadline_before_dispatch_is_pre_provider_abort(monkeypatch):
    planner = Planner()
    plane, _, _ = _plane(
        AdmissionOutcome.ADMITTED,
        planner=planner,
        monotonic=Monotonic([0, 6]),
    )
    captured = []
    monkeypatch.setattr(
        control, "TerminalIntent", lambda **kwargs: captured.append(kwargs)
    )
    result = plane.execute(
        _request(), peer_ip="127.0.0.1", canonical_client_ip="203.0.113.2"
    )
    assert result.status is PreviewControlStatus.UNAVAILABLE
    assert planner.calls == []
    assert captured[0]["disposition"] is control.TerminalDisposition.PRE_PROVIDER_ABORT
    assert captured[0]["circuit_failure"] is False


@pytest.mark.parametrize("tokens,cost", [(None, None), (613, 1), (1, 2_392)])
def test_known_provider_failure_invalid_usage_becomes_uncertain(
    monkeypatch, tokens, cost
):
    plane, _, _ = _plane(AdmissionOutcome.ADMITTED)
    admission = plane.admit(
        _request(), peer_ip="127.0.0.1", canonical_client_ip="203.0.113.2"
    )
    captured = []
    monkeypatch.setattr(
        control, "TerminalIntent", lambda **kwargs: captured.append(kwargs)
    )
    assert plane.reconcile(
        admission,
        terminal_class=PreviewTerminalClass.KNOWN_PROVIDER_FAILURE,
        actual_tokens=tokens,
        actual_micro_usd=cost,
    )
    assert captured[0]["disposition"] is control.TerminalDisposition.UNCERTAIN
    assert captured[0]["actual_tokens"] is None
    assert captured[0]["actual_micro_usd"] is None


def test_sealed_client_cancel_after_dispatch_is_uncertain(monkeypatch):
    planner = Planner(
        PlannerPortFailure.provider(PreviewTerminalClass.CLIENT_ABORT)
    )
    plane, _, _ = _plane(AdmissionOutcome.ADMITTED, planner=planner)
    captured = []
    monkeypatch.setattr(
        control, "TerminalIntent", lambda **kwargs: captured.append(kwargs)
    )
    plane.execute(
        _request(), peer_ip="127.0.0.1", canonical_client_ip="203.0.113.2"
    )
    assert captured[0]["disposition"] is control.TerminalDisposition.UNCERTAIN
    assert captured[0]["circuit_failure"] is False


def test_module_has_no_formal_resource_or_provider_tool_authority():
    source = open(
        "src/trustforge/hermes_preview_control_plane.py", encoding="utf-8"
    ).read()
    for forbidden in (
        "AnalysisFlow",
        "analysis_job",
        "deployment_receipt",
        "dedup",
        "boto3",
        "QuestionType",
        "COIN_POOL",
    ):
        assert forbidden not in source
