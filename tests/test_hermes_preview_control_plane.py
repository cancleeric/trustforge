from __future__ import annotations

from threading import Event, Thread
from types import SimpleNamespace
import uuid

import pytest

import trustforge.hermes_preview_control_plane as control
from trustforge.hermes_preview_control_plane import (
    HermesPreviewControlPlane,
    BoundedPlannerExecutionAuthority,
    canonical_planner_envelope,
    canonical_preview_policy_digest,
    PreviewControlStatus,
    PreviewObservation,
    PreviewPolicy,
    PreviewRequest,
    PreviewTopology,
    PlannerPortFailure,
    PlannerExecutionSaturated,
    PlannerExecution,
    PlannerExecutionState,
    PreviewObservationOutcome,
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
        self.result = result or PlannerResult({"strategy": "internal"}, 100, 100)
        self.calls = []

    def plan(self, payload, **kwargs):
        self.calls.append((payload, kwargs))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def _policy(**changes):
    values = {
        "source_policy_version": "analysis-plan-source-classes-v1",
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
    probe = SimpleNamespace(
        **{
            **values,
            "policy_version": values.get("policy_version", 1),
            "identity_requests_minute": values.get("identity_requests_minute", 3),
            "identity_requests_day": values.get("identity_requests_day", 20),
            "identity_concurrency": values.get("identity_concurrency", 1),
            "global_concurrency": values.get("global_concurrency", 4),
            "minute_tokens": values.get("minute_tokens", 8_000),
            "day_tokens": values.get("day_tokens", 51_200),
            "minute_micro_usd": values.get("minute_micro_usd", 50_000),
            "day_micro_usd": values.get("day_micro_usd", 500_000),
        }
    )
    values["policy_digest"] = canonical_preview_policy_digest(probe)
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
        def __init__(self):
            self.identities = []

        def snapshot(self):
            return snapshot

        def derive(self, selected, identity):
            assert selected is snapshot
            assert identity.startswith(b"pap1-client-ip:")
            self.identities.append(identity)
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


class ImmediateExecution:
    capacity = 4

    def __init__(self, failure=None):
        self.failure = failure
        self.calls = []

    def invoke(self, operation, *, timeout_seconds):
        self.calls.append((operation, timeout_seconds))
        if self.failure is not None:
            raise self.failure
        try:
            return PlannerExecution(
                PlannerExecutionState.COMPLETED,
                value=operation(),
                termination_proven=True,
            )
        except Exception as exc:
            return PlannerExecution(
                PlannerExecutionState.COMPLETED,
                failure=exc,
                termination_proven=True,
            )


def _plane(
    outcome=AdmissionOutcome.DENIED,
    tokenizer=None,
    planner=None,
    monotonic=None,
    planner_execution=None,
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
        planner_execution=planner_execution or ImmediateExecution(),
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


def test_canonical_provider_envelope_is_the_exact_reserved_and_dispatched_input():
    tokenizer = Tokenizer(100)
    planner = Planner()
    plane, _, _ = _plane(
        AdmissionOutcome.ADMITTED,
        tokenizer=tokenizer,
        planner=planner,
    )
    request = _request("任意問題")
    expected = canonical_planner_envelope(
        request.canonical_payload(),
        model_id="approved-model-v1",
    )
    plane.execute(
        request,
        peer_ip="127.0.0.1",
        canonical_client_ip="203.0.113.2",
    )
    assert tokenizer.payloads == [expected]
    assert planner.calls[0][0] == expected
    decoded = control.json.loads(expected)
    assert decoded["system"] == control.PLANNER_SYSTEM_PROMPT
    assert decoded["max_output_tokens"] == 512
    assert decoded["input"]["question"] == "任意問題"


def test_question_is_trimmed_and_asset_hints_use_exact_uppercase_contract():
    request = PreviewRequest("  任意問題  ", "zh-TW", ("BTC:USD",))
    assert request.question == "任意問題"
    assert '"question":"任意問題"'.encode() in request.canonical_payload()
    for invalid in ("btc", "BTC/USD", "-BTC"):
        with pytest.raises(ValueError):
            PreviewRequest("ok", "en", (invalid,))


def test_policy_is_fixed_and_silent_cap_relaxation_is_rejected():
    with pytest.raises(ValueError, match="invalid preview policy"):
        _policy(global_concurrency=5)
    with pytest.raises(ValueError, match="invalid preview policy"):
        _policy(input_micro_usd_per_million_tokens=25_000_000)
    for field in (
        "model_price_policy_version",
        "tokenizer_package",
        "tokenizer_version",
        "allowed_model_id",
        "planner_identity",
    ):
        with pytest.raises(ValueError, match="invalid preview policy"):
            _policy(**{field: "ascii\u0000escape"})
        with pytest.raises(ValueError, match="invalid preview policy"):
            _policy(**{field: "非ASCII"})


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
    mapped_policy = TrustedProxyPolicy(("::ffff:127.0.0.1",))
    assert mapped_policy.canonical_identity(
        peer_ip="127.0.0.1", canonical_client_ip="::ffff:192.0.2.1"
    ) == mapped_policy.canonical_identity(
        peer_ip="::ffff:127.0.0.1", canonical_client_ip="192.0.2.1"
    )
    with pytest.raises(ValueError, match="invalid trusted proxy policy"):
        TrustedProxyPolicy(("127.0.0.1", "::ffff:127.0.0.1"))


def test_mapped_and_raw_client_ips_bind_the_same_quota_identity():
    plane, runtime, _ = _plane()
    plane.admit(
        _request(), peer_ip="127.0.0.1", canonical_client_ip="192.0.2.1"
    )
    plane.admit(
        _request(),
        peer_ip="::ffff:127.0.0.1",
        canonical_client_ip="::ffff:192.0.2.1",
    )
    assert runtime.executor._lifecycle_authority.identities == [
        b"pap1-client-ip:192.0.2.1",
        b"pap1-client-ip:192.0.2.1",
    ]


def test_admission_uses_worst_case_reservation_without_remote_tokenization():
    tokenizer = Tokenizer(100)
    plane, runtime, observer = _plane(AdmissionOutcome.DENIED, tokenizer)
    result = plane.admit(
        _request(), peer_ip="127.0.0.1", canonical_client_ip="203.0.113.2"
    )
    assert result.status is PreviewControlStatus.DENIED
    bound = runtime.executor.requests[0]
    assert bound.request.reserved_tokens == 2_560
    assert bound.request.reserved_micro_usd == 10_000
    assert tokenizer.payloads == []
    assert observer.events == [
        PreviewObservation.mint(PreviewObservationOutcome.DENIED, None, False)
    ]


def test_rate_budget_or_circuit_denial_calls_neither_tokenizer_nor_inference():
    tokenizer = Tokenizer(100)
    planner = Planner()
    plane, _, _ = _plane(
        AdmissionOutcome.DENIED, tokenizer=tokenizer, planner=planner
    )
    result = plane.execute(
        _request(), peer_ip="127.0.0.1", canonical_client_ip="203.0.113.2"
    )
    assert result.status is PreviewControlStatus.DENIED
    assert tokenizer.payloads == []
    assert planner.calls == []


def test_client_request_id_never_becomes_durable_reservation_identity():
    sentinel = str(uuid.uuid4())
    plane, runtime, _ = _plane()
    plane.admit(
        PreviewRequest("任意問題", "zh-TW", client_request_id=sentinel),
        peer_ip="127.0.0.1",
        canonical_client_ip="203.0.113.2",
    )
    bound = runtime.executor.requests[0]
    assert bound.request.reservation_id != sentinel
    assert sentinel not in repr(bound.request)


def test_tokenizer_overflow_is_admitted_but_never_dispatches_inference():
    tokenizer = Tokenizer(2049)
    planner = Planner()
    plane, runtime, _ = _plane(
        AdmissionOutcome.ADMITTED, tokenizer=tokenizer, planner=planner
    )
    result = plane.execute(
        _request(), peer_ip="127.0.0.1", canonical_client_ip="203.0.113.2"
    )
    assert result.status is PreviewControlStatus.UNAVAILABLE
    assert len(runtime.executor.requests) == 1
    assert len(tokenizer.payloads) == 1
    assert planner.calls == []


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
        planner_execution=ImmediateExecution(),
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
    assert captured[0]["actual_tokens"] == 200
    assert captured[0]["actual_micro_usd"] == 782


def test_completed_result_observed_after_provider_deadline_returns_timeout_504_reason(
    monkeypatch,
):
    plane, _, observer = _plane(
        AdmissionOutcome.ADMITTED,
        monotonic=Monotonic([0, 0, 5.001]),
    )
    captured = []
    monkeypatch.setattr(
        control, "TerminalIntent", lambda **kwargs: captured.append(kwargs)
    )
    result = plane.execute(
        _request(), peer_ip="127.0.0.1", canonical_client_ip="203.0.113.2"
    )
    assert result.status is PreviewControlStatus.UNAVAILABLE
    assert result.reason == "planner_timeout"
    assert captured[0]["disposition"] is control.TerminalDisposition.UNCERTAIN
    assert (
        observer.events[-1].terminal_class
        is PreviewTerminalClass.PROVIDER_TIMEOUT
    )


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


def test_known_failure_usage_is_locally_priced(monkeypatch):
    planner = Planner(
        PlannerPortFailure.provider(
            PreviewTerminalClass.KNOWN_PROVIDER_FAILURE,
            input_tokens=100,
            output_tokens=10,
        )
    )
    plane, _, _ = _plane(AdmissionOutcome.ADMITTED, planner=planner)
    captured = []
    monkeypatch.setattr(
        control, "TerminalIntent", lambda **kwargs: captured.append(kwargs)
    )
    plane.execute(
        _request(), peer_ip="127.0.0.1", canonical_client_ip="203.0.113.2"
    )
    assert captured[0]["disposition"] is control.TerminalDisposition.KNOWN_FAILURE
    assert captured[0]["actual_tokens"] == 110
    assert captured[0]["actual_micro_usd"] == 430


def test_observer_failure_never_changes_durable_result():
    plane, runtime, observer = _plane()
    observer.record = lambda observation: (_ for _ in ()).throw(
        RuntimeError("observer unavailable")
    )
    result = plane.admit(
        _request(), peer_ip="127.0.0.1", canonical_client_ip="203.0.113.2"
    )
    assert result.status is PreviewControlStatus.DENIED
    assert len(runtime.executor.requests) == 1


def test_observer_cannot_capture_question_payload_or_provider_exception():
    observer = ZeroSensitiveObserver(clock=Monotonic())
    with pytest.raises(ValueError, match="invalid preview observation"):
        observer.record({"question": "secret"})
    observer.record(
        PreviewObservation.mint(PreviewObservationOutcome.DENIED, None, False)
    )
    rendered = repr(observer.events)
    assert "question" not in rendered
    assert "provider exception" not in rendered


@pytest.mark.parametrize("count", [1, 100, 2048])
def test_reservation_price_is_worst_case_before_exact_remote_count(count):
    plane, runtime, _ = _plane(tokenizer=Tokenizer(count))
    plane.admit(
        _request(), peer_ip="127.0.0.1", canonical_client_ip="203.0.113.2"
    )
    assert runtime.executor.requests[0].request.reserved_micro_usd == 10_000


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
            planner_execution=ImmediateExecution(),
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


@pytest.mark.parametrize(
    "origin",
    [
        "http://127.0.0.1:4174",
        "http://127.0.0.1:4175",
        "http://localhost:4175",
    ],
)
def test_explicit_development_topology_accepts_only_direct_loopback(origin):
    topology = PreviewTopology(
        "127.0.0.1",
        origin,
        TrustedProxyPolicy(("127.0.0.1",)),
        False,
        development_loopback=True,
    )
    identity = topology.canonical_identity(
        peer_ip="127.0.0.1",
        canonical_client_ip="203.0.113.99",
    )
    assert identity == b"pap1-client-ip:127.0.0.1"


@pytest.mark.parametrize(
    "bind,origin,peer",
    [
        ("0.0.0.0", "http://127.0.0.1:4175", "127.0.0.1"),
        ("127.0.0.1", "http://127.0.0.1.evil.test:4175", "127.0.0.1"),
        ("127.0.0.1", "http://127.0.0.1:4176", "127.0.0.1"),
        ("127.0.0.1", "https://127.0.0.1:4175", "127.0.0.1"),
        ("127.0.0.1", "http://127.0.0.1:4175", "203.0.113.9"),
    ],
)
def test_development_topology_rejects_lookalike_nonloopback_and_wrong_port(
    bind, origin, peer
):
    if peer != "127.0.0.1":
        topology = PreviewTopology(
            bind,
            origin,
            TrustedProxyPolicy(("127.0.0.1",)),
            False,
            development_loopback=True,
        )
        with pytest.raises(ValueError, match="unsafe ingress identity"):
            topology.canonical_identity(
                peer_ip=peer, canonical_client_ip="127.0.0.1"
            )
        return
    with pytest.raises(ValueError, match="invalid preview topology"):
        PreviewTopology(
            bind,
            origin,
            TrustedProxyPolicy(("127.0.0.1",)),
            False,
            development_loopback=True,
        )


def test_observer_is_capacity_and_retention_bounded():
    clock = Monotonic([0, 1, 2, 10])
    observer = ZeroSensitiveObserver(clock=clock, retention_seconds=5, capacity=2)
    observation = PreviewObservation.mint(
        PreviewObservationOutcome.DENIED, None, False
    )
    observer.record(observation)
    observer.record(observation)
    observer.record(observation)
    assert observer.events == []


def test_observer_record_and_snapshot_are_thread_safe():
    observer = ZeroSensitiveObserver(
        clock=Monotonic(), retention_seconds=5, capacity=4096
    )
    observation = PreviewObservation.mint(
        PreviewObservationOutcome.DENIED, None, False
    )
    workers = [
        Thread(target=lambda: [observer.record(observation) for _ in range(100)])
        for _ in range(8)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert len(observer.events) == 800


def test_bounded_execution_holds_slot_until_timed_out_worker_terminates():
    authority = BoundedPlannerExecutionAuthority(1)
    started = Event()
    release = Event()
    finished = Event()

    def operation():
        started.set()
        release.wait()
        finished.set()
        return "late"

    result = authority.invoke(operation, timeout_seconds=0.001)
    assert started.is_set()
    assert result.state is PlannerExecutionState.TIMED_OUT
    assert result.termination_proven is False
    with pytest.raises(PlannerExecutionSaturated):
        authority.invoke(lambda: "unsafe overlap", timeout_seconds=1)
    release.set()
    assert finished.wait(1)
    assert authority.invoke(
        lambda: "next", timeout_seconds=1
    ) == PlannerExecution(
        PlannerExecutionState.COMPLETED,
        value="next",
        termination_proven=True,
    )


def test_bounded_execution_timeout_done_success_race_returns_success():
    class RacedFuture:
        def __init__(self):
            self.result_calls = 0

        def add_done_callback(self, callback):
            callback(self)

        def result(self, timeout=None):
            self.result_calls += 1
            if self.result_calls == 1:
                raise control.FutureTimeoutError
            return "completed-at-deadline"

        def done(self):
            return True

        def exception(self):
            return None

    raced = RacedFuture()

    class Executor:
        def submit(self, operation):
            del operation
            return raced

    authority = BoundedPlannerExecutionAuthority(1)
    authority._executor = Executor()
    result = authority.invoke(lambda: "unused", timeout_seconds=1)
    assert result == PlannerExecution(
        PlannerExecutionState.COMPLETED,
        value="completed-at-deadline",
        termination_proven=True,
    )


def test_bounded_execution_running_before_guard_can_timeout_without_dispatch():
    class RunningBeforeGuardFuture:
        def add_done_callback(self, callback):
            self.callback = callback

        def result(self, timeout=None):
            del timeout
            raise control.FutureTimeoutError

        def done(self):
            return False

        def cancel(self):
            return False

    future = RunningBeforeGuardFuture()

    class Executor:
        def submit(self, operation):
            self.guarded_operation = operation
            return future

    executor = Executor()
    authority = BoundedPlannerExecutionAuthority(1)
    authority._executor = executor
    result = authority.invoke(lambda: "not-entered", timeout_seconds=1)
    assert result == PlannerExecution(
        PlannerExecutionState.TIMED_OUT,
        termination_proven=False,
        provider_dispatched=False,
    )
    assert callable(executor.guarded_operation)


def test_provider_timeout_exception_from_completed_worker_is_not_runner_timeout():
    authority = BoundedPlannerExecutionAuthority(1)

    def operation():
        raise TimeoutError("provider classified this separately")

    result = authority.invoke(operation, timeout_seconds=1)
    assert result.state is PlannerExecutionState.COMPLETED
    assert type(result.failure) is TimeoutError


def test_bounded_executor_enforces_four_concurrent_and_context_lifecycle():
    release = Event()
    four_started = Event()
    lock = control.RLock()
    active = 0
    maximum = 0
    outcomes = []

    def operation():
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            if active == 4:
                four_started.set()
        release.wait()
        with lock:
            active -= 1
        return "done"

    def invoke(authority):
        try:
            outcomes.append(authority.invoke(operation, timeout_seconds=1))
        except PlannerExecutionSaturated:
            outcomes.append("saturated")

    with BoundedPlannerExecutionAuthority(4) as authority:
        running = [Thread(target=invoke, args=(authority,)) for _ in range(4)]
        for worker in running:
            worker.start()
        assert four_started.wait(1)
        saturated = [Thread(target=invoke, args=(authority,)) for _ in range(4)]
        for worker in saturated:
            worker.start()
        for worker in saturated:
            worker.join()
        release.set()
        for worker in running:
            worker.join()
    assert maximum == 4
    assert sum(result == "saturated" for result in outcomes) == 4
    assert sum(type(result) is PlannerExecution for result in outcomes) == 4
    with pytest.raises(RuntimeError):
        authority.invoke(lambda: "after-close", timeout_seconds=1)


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


@pytest.mark.parametrize("termination_proven", [False, True])
def test_pre_inference_timeout_rolls_back_reserve_and_releases_lease(
    monkeypatch, termination_proven
):
    class TimeoutExecution:
        capacity = 4

        def invoke(self, operation, *, timeout_seconds):
            return PlannerExecution(
                PlannerExecutionState.TIMED_OUT,
                termination_proven=termination_proven,
            )

    plane, runtime, observer = _plane(
        AdmissionOutcome.ADMITTED, planner_execution=TimeoutExecution()
    )
    captured = []
    monkeypatch.setattr(
        control, "TerminalIntent", lambda **kwargs: captured.append(kwargs)
    )
    result = plane.execute(
        _request(), peer_ip="127.0.0.1", canonical_client_ip="203.0.113.2"
    )
    assert result.status is PreviewControlStatus.UNAVAILABLE
    assert result.value is None
    assert len(runtime.terminal.intents) == 1
    assert len(captured) == 1
    assert observer.events[-1].terminal_class is PreviewTerminalClass.CLIENT_ABORT


def test_proven_timeout_reconcile_failure_is_terminal_unavailable():
    class TimeoutExecution:
        capacity = 4

        def invoke(self, operation, *, timeout_seconds):
            return PlannerExecution(
                PlannerExecutionState.TIMED_OUT,
                termination_proven=True,
            )

    plane, runtime, _ = _plane(
        AdmissionOutcome.ADMITTED, planner_execution=TimeoutExecution()
    )
    runtime.terminal.reconcile = lambda intent: (_ for _ in ()).throw(
        RuntimeError("durable terminal unavailable")
    )
    result = plane.execute(
        _request(), peer_ip="127.0.0.1", canonical_client_ip="203.0.113.2"
    )
    assert result.reason == "terminal_unavailable"


def test_proven_pre_dispatch_cancellation_is_not_a_circuit_failure(monkeypatch):
    class CancelledExecution:
        capacity = 4

        def invoke(self, operation, *, timeout_seconds):
            return PlannerExecution(
                PlannerExecutionState.TIMED_OUT,
                termination_proven=True,
                provider_dispatched=False,
            )

    plane, _, observer = _plane(
        AdmissionOutcome.ADMITTED, planner_execution=CancelledExecution()
    )
    captured = []
    monkeypatch.setattr(
        control, "TerminalIntent", lambda **kwargs: captured.append(kwargs)
    )
    result = plane.execute(
        _request(), peer_ip="127.0.0.1", canonical_client_ip="203.0.113.2"
    )
    assert result.reason == "planner_unavailable"
    assert captured[0]["disposition"] is control.TerminalDisposition.PRE_PROVIDER_ABORT
    assert captured[0]["circuit_failure"] is False
    assert observer.events[-1].circuit_failure is False


def test_saturation_is_pre_provider_abort_without_circuit(monkeypatch):
    execution = ImmediateExecution(PlannerExecutionSaturated("full"))
    tokenizer = Tokenizer()
    planner = Planner()
    plane, _, observer = _plane(
        AdmissionOutcome.ADMITTED,
        tokenizer=tokenizer,
        planner=planner,
        planner_execution=execution,
    )
    captured = []
    monkeypatch.setattr(
        control, "TerminalIntent", lambda **kwargs: captured.append(kwargs)
    )
    result = plane.execute(
        _request(), peer_ip="127.0.0.1", canonical_client_ip="203.0.113.2"
    )
    assert result.reason == "planner_saturated"
    assert captured[0]["disposition"] is control.TerminalDisposition.PRE_PROVIDER_ABORT
    assert captured[0]["circuit_failure"] is False
    assert observer.events[-1].circuit_failure is False
    assert tokenizer.payloads == []
    assert planner.calls == []


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


def test_pre_dispatch_deadline_reconcile_failure_is_terminal_unavailable():
    plane, runtime, _ = _plane(
        AdmissionOutcome.ADMITTED,
        monotonic=Monotonic([0, 6]),
    )
    runtime.terminal.reconcile = lambda intent: (_ for _ in ()).throw(
        RuntimeError("terminal unavailable")
    )
    result = plane.execute(
        _request(), peer_ip="127.0.0.1", canonical_client_ip="203.0.113.2"
    )
    assert result.reason == "terminal_unavailable"


def test_base_exception_from_planner_reconciles_then_propagates(monkeypatch):
    class FatalPlannerExit(BaseException):
        pass

    class BaseExceptionExecution:
        capacity = 4

        def invoke(self, operation, *, timeout_seconds):
            del timeout_seconds
            return operation()

    planner = Planner(FatalPlannerExit("fatal"))
    plane, _, _ = _plane(
        AdmissionOutcome.ADMITTED,
        planner=planner,
        planner_execution=BaseExceptionExecution(),
    )
    captured = []
    monkeypatch.setattr(
        control, "TerminalIntent", lambda **kwargs: captured.append(kwargs)
    )
    with pytest.raises(FatalPlannerExit):
        plane.execute(
            _request(), peer_ip="127.0.0.1", canonical_client_ip="203.0.113.2"
        )
    assert len(captured) == 1
    assert captured[0]["disposition"] is control.TerminalDisposition.UNCERTAIN
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
