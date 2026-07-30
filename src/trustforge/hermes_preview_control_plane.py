"""Internal-only Hermes preview security and cost control plane.

This module has no HTTP, formal-job, tool, connector, or persistence authority.
It accepts arbitrary bounded intent text and composes only the paid-preview
admission and terminal authorities delivered by #975/#991.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import ipaddress
import json
import math
import re
from threading import BoundedSemaphore, RLock
import uuid
from typing import Callable, Protocol
from urllib.parse import urlsplit

from trustforge.preview_admission_compiler import AdmissionCompileRequest
from trustforge.preview_admission_deployment import (
    PreviewAdmissionProductionRuntime,
)
from trustforge.preview_admission_executor import (
    AdmissionOutcome,
)
from trustforge.preview_terminal_reconcile import (
    TerminalDisposition,
    TerminalIntent,
    TerminalOutcome,
)


MAX_BODY_BYTES = 16 * 1024
MAX_QUESTION_CODEPOINTS = 1000
MAX_ASSET_HINTS = 8
MAX_INPUT_TOKENS = 2048
MAX_OUTPUT_TOKENS = 512
PROVIDER_TIMEOUT_SECONDS = 5
TOTAL_DEADLINE_SECONDS = 6
ATTEMPTS = 1
POLICY_VERSION = 1
SOURCE_POLICY_VERSION = "analysis-plan-source-classes-v1"
PLANNER_ENVELOPE_VERSION = "hermes-preview-envelope-v1"
PLANNER_SYSTEM_PROMPT = (
    "Return one bounded internal analysis plan. Do not call tools, retrieve "
    "resources, or execute the plan."
)
_ASSET_RE = re.compile(r"^[A-Z0-9][A-Z0-9._:-]{0,15}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class PreviewControlStatus(StrEnum):
    ADMITTED = "admitted"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"


class PreviewTerminalClass(StrEnum):
    SUCCESS = "success"
    KNOWN_PROVIDER_FAILURE = "known_provider_failure"
    PROVIDER_TRANSPORT = "provider_transport"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_THROTTLE = "provider_throttle"
    PROVIDER_5XX = "provider_5xx"
    HOSTILE_OUTPUT = "hostile_output"
    SCHEMA_FAILURE = "schema_failure"
    CLIENT_ABORT = "client_abort"

    @property
    def counts_for_circuit(self) -> bool:
        return self in {
            self.PROVIDER_TRANSPORT,
            self.PROVIDER_TIMEOUT,
            self.PROVIDER_THROTTLE,
            self.PROVIDER_5XX,
        }


@dataclass(frozen=True, slots=True)
class PreviewPolicy:
    policy_digest: str
    source_policy_version: str
    model_price_policy_version: str
    tokenizer_package: str
    tokenizer_version: str
    tokenizer_vocab_hash: str
    allowed_model_id: str
    planner_identity: str
    input_micro_usd_per_million_tokens: int
    output_micro_usd_per_million_tokens: int
    valid_from_epoch: int
    valid_until_epoch: int
    policy_version: int = POLICY_VERSION
    identity_requests_minute: int = 3
    identity_requests_day: int = 20
    identity_concurrency: int = 1
    global_concurrency: int = 4
    minute_tokens: int = 8_000
    day_tokens: int = 51_200
    minute_micro_usd: int = 50_000
    day_micro_usd: int = 500_000

    def __post_init__(self) -> None:
        versions = (
            self.source_policy_version,
            self.model_price_policy_version,
            self.tokenizer_package,
            self.tokenizer_version,
            self.allowed_model_id,
            self.planner_identity,
        )
        if (
            self.source_policy_version != SOURCE_POLICY_VERSION
            or not _DIGEST_RE.fullmatch(self.policy_digest)
            or any(
                type(value) is not str
                or not value
                or len(value) > 64
                or value != value.strip()
                or not _strict_ascii_metadata(value)
                for value in versions
            )
            or not _DIGEST_RE.fullmatch(self.tokenizer_vocab_hash)
            or type(self.input_micro_usd_per_million_tokens) is not int
            or self.input_micro_usd_per_million_tokens < 1
            or type(self.output_micro_usd_per_million_tokens) is not int
            or self.output_micro_usd_per_million_tokens < 1
            or _ceil_micro_usd(
                MAX_INPUT_TOKENS,
                MAX_OUTPUT_TOKENS,
                self.input_micro_usd_per_million_tokens,
                self.output_micro_usd_per_million_tokens,
            )
            > 50_000
            or type(self.valid_from_epoch) is not int
            or type(self.valid_until_epoch) is not int
            or not 0 <= self.valid_from_epoch < self.valid_until_epoch
            or (
                self.policy_version,
                self.identity_requests_minute,
                self.identity_requests_day,
                self.identity_concurrency,
                self.global_concurrency,
                self.minute_tokens,
                self.day_tokens,
                self.minute_micro_usd,
                self.day_micro_usd,
            )
            != (1, 3, 20, 1, 4, 8_000, 51_200, 50_000, 500_000)
        ):
            raise ValueError("invalid preview policy")
        if self.policy_digest != canonical_preview_policy_digest(self):
            raise ValueError("invalid preview policy digest")


@dataclass(frozen=True, slots=True)
class PreviewRequest:
    question: str = field(repr=False)
    locale: str
    asset_hints: tuple[str, ...] = ()
    client_request_id: str | None = None

    def __post_init__(self) -> None:
        try:
            canonical_question = self.question.strip()
            encoded = canonical_question.encode("utf-8")
        except Exception:
            raise ValueError("invalid preview request") from None
        if (
            type(self.question) is not str
            or not 1 <= len(canonical_question) <= MAX_QUESTION_CODEPOINTS
            or len(encoded) > MAX_BODY_BYTES
            or self.locale not in {"zh-TW", "en"}
            or type(self.asset_hints) is not tuple
            or len(self.asset_hints) > MAX_ASSET_HINTS
            or any(
                type(value) is not str or not _ASSET_RE.fullmatch(value)
                for value in self.asset_hints
            )
            or len(set(self.asset_hints)) != len(self.asset_hints)
            or (
                self.client_request_id is not None
                and not _uuid4(self.client_request_id)
            )
        ):
            raise ValueError("invalid preview request")
        object.__setattr__(self, "question", canonical_question)

    def canonical_payload(self) -> bytes:
        return json.dumps(
            {
                "asset_hints": list(self.asset_hints),
                "locale": self.locale,
                "question": self.question,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()


@dataclass(frozen=True, slots=True)
class TrustedProxyPolicy:
    trusted_peer_ips: tuple[str, ...]
    _peers: tuple[object, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        try:
            peers = tuple(
                _canonical_ip(value) for value in self.trusted_peer_ips
            )
        except (TypeError, ValueError):
            raise ValueError("invalid trusted proxy policy") from None
        if not peers or len(set(peers)) != len(peers):
            raise ValueError("invalid trusted proxy policy")
        object.__setattr__(self, "_peers", peers)

    def canonical_identity(
        self,
        *,
        peer_ip: str,
        canonical_client_ip: str | None,
    ) -> bytes:
        try:
            peer = _canonical_ip(peer_ip)
        except ValueError:
            raise ValueError("unsafe ingress identity") from None
        if peer not in self._peers:
            raise ValueError("unsafe ingress identity")
        try:
            client = _canonical_ip(canonical_client_ip or "")
        except ValueError:
            raise ValueError("unsafe ingress identity") from None
        return f"pap1-client-ip:{client.compressed}".encode()


@dataclass(frozen=True, slots=True)
class PreviewTopology:
    app_bind_host: str
    allowed_origin: str
    proxy_policy: TrustedProxyPolicy
    ingress_overwrites_forwarded_headers: bool

    def __post_init__(self) -> None:
        try:
            bind = ipaddress.ip_address(self.app_bind_host)
            origin = urlsplit(self.allowed_origin)
        except ValueError:
            raise ValueError("invalid preview topology") from None
        if (
            not bind.is_loopback
            or origin.scheme != "https"
            or not origin.hostname
            or origin.username is not None
            or origin.password is not None
            or origin.path not in {"", "/"}
            or origin.query
            or origin.fragment
            or "*" in self.allowed_origin
            or type(self.proxy_policy) is not TrustedProxyPolicy
            or self.ingress_overwrites_forwarded_headers is not True
        ):
            raise ValueError("invalid preview topology")


class ExactTokenizer(Protocol):
    package: str
    version: str
    vocab_hash: str

    def count(self, payload: bytes) -> int: ...


@dataclass(frozen=True, slots=True)
class PlannerResult:
    value: object = field(repr=False)
    input_tokens: int
    output_tokens: int

    def __post_init__(self) -> None:
        if (
            type(self.input_tokens) is not int
            or not 0 <= self.input_tokens <= MAX_INPUT_TOKENS
            or type(self.output_tokens) is not int
            or not 0 <= self.output_tokens <= MAX_OUTPUT_TOKENS
        ):
            raise ValueError("invalid planner result")


class HermesPlannerPort(Protocol):
    identity: str
    model_id: str
    capabilities: tuple[str, ...]

    def plan(
        self,
        payload: bytes,
        *,
        max_output_tokens: int,
        provider_deadline: float,
        total_deadline: float,
        attempts: int,
    ) -> PlannerResult: ...


_PLANNER_FAILURE_TOKEN = object()


class PlannerPortFailure(RuntimeError):
    def __init__(
        self,
        terminal_class: PreviewTerminalClass,
        token: object,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        if (
            token is not _PLANNER_FAILURE_TOKEN
            or type(terminal_class) is not PreviewTerminalClass
            or terminal_class
            not in {
                PreviewTerminalClass.KNOWN_PROVIDER_FAILURE,
                PreviewTerminalClass.PROVIDER_TRANSPORT,
                PreviewTerminalClass.PROVIDER_TIMEOUT,
                PreviewTerminalClass.PROVIDER_THROTTLE,
                PreviewTerminalClass.PROVIDER_5XX,
                PreviewTerminalClass.CLIENT_ABORT,
            }
        ):
            raise ValueError("invalid planner failure")
        self.terminal_class = terminal_class
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        super().__init__(terminal_class.value)

    @classmethod
    def provider(
        cls,
        terminal_class: PreviewTerminalClass,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> "PlannerPortFailure":
        return cls(
            terminal_class,
            _PLANNER_FAILURE_TOKEN,
            input_tokens,
            output_tokens,
        )


@dataclass(frozen=True, slots=True)
class PreviewAdmission:
    status: PreviewControlStatus
    reason: str
    handle: object | None = field(default=None, repr=False)
    payload: bytes | None = field(default=None, repr=False)
    reserved_input_tokens: int | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class PreviewExecution:
    status: PreviewControlStatus
    reason: str
    value: object | None = field(default=None, repr=False)


class PreviewObservationOutcome(StrEnum):
    ADMITTED = "admitted"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"
    RECONCILED = "reconciled"


_OBSERVATION_TOKEN = object()


@dataclass(frozen=True, slots=True)
class PreviewObservation:
    outcome: PreviewObservationOutcome
    terminal_class: PreviewTerminalClass | None
    circuit_failure: bool
    policy_version: int
    _token: object = field(repr=False)

    def __post_init__(self) -> None:
        if (
            self._token is not _OBSERVATION_TOKEN
            or type(self.outcome) is not PreviewObservationOutcome
            or (
                self.terminal_class is not None
                and type(self.terminal_class) is not PreviewTerminalClass
            )
            or type(self.circuit_failure) is not bool
            or self.policy_version != POLICY_VERSION
        ):
            raise ValueError("invalid preview observation")

    @classmethod
    def mint(
        cls,
        outcome: PreviewObservationOutcome,
        terminal_class: PreviewTerminalClass | None,
        circuit_failure: bool,
    ) -> "PreviewObservation":
        return cls(
            outcome,
            terminal_class,
            circuit_failure,
            POLICY_VERSION,
            _OBSERVATION_TOKEN,
        )


class MonotonicAuthority(Protocol):
    def now(self) -> float: ...


class PlannerExecutionSaturated(RuntimeError):
    pass


class PlannerExecutionState(StrEnum):
    COMPLETED = "completed"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True, slots=True)
class PlannerExecution:
    state: PlannerExecutionState
    value: object | None = field(default=None, repr=False)
    failure: BaseException | None = field(default=None, repr=False)
    termination_proven: bool = False
    provider_dispatched: bool = True

    def __post_init__(self) -> None:
        if (
            type(self.state) is not PlannerExecutionState
            or type(self.termination_proven) is not bool
            or type(self.provider_dispatched) is not bool
            or (
                self.state is PlannerExecutionState.COMPLETED
                and (
                    self.termination_proven is not True
                    or self.provider_dispatched is not True
                    or (self.value is None) == (self.failure is None)
                )
            )
            or (
                self.state is PlannerExecutionState.TIMED_OUT
                and (
                    self.value is not None
                    or self.failure is not None
                )
            )
        ):
            raise ValueError("invalid planner execution result")


class PlannerExecutionAuthority(Protocol):
    capacity: int

    def invoke(
        self, operation: Callable[[], object], *, timeout_seconds: float
    ) -> PlannerExecution: ...


class BoundedPlannerExecutionAuthority:
    """Bounded production executor whose slots outlive caller-side timeouts."""

    def __init__(self, capacity: int) -> None:
        if type(capacity) is not int or not 1 <= capacity <= 64:
            raise ValueError("invalid planner execution capacity")
        self.capacity = capacity
        self._slots = BoundedSemaphore(capacity)
        self._executor = ThreadPoolExecutor(
            max_workers=capacity,
            thread_name_prefix="hermes-preview",
        )

    def invoke(
        self, operation: Callable[[], object], *, timeout_seconds: float
    ) -> PlannerExecution:
        if (
            not callable(operation)
            or type(timeout_seconds) not in (int, float)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("invalid planner execution")
        if not self._slots.acquire(blocking=False):
            raise PlannerExecutionSaturated("planner execution capacity exhausted")
        started = RLock()
        dispatch_state = {"started": False}

        def guarded_operation() -> object:
            with started:
                dispatch_state["started"] = True
            return operation()

        try:
            future = self._executor.submit(guarded_operation)
        except BaseException:
            self._slots.release()
            raise
        future.add_done_callback(lambda _: self._slots.release())
        try:
            value = future.result(timeout=float(timeout_seconds))
        except FutureTimeoutError:
            if future.done():
                try:
                    value = future.result()
                except BaseException as exc:
                    return PlannerExecution(
                        PlannerExecutionState.COMPLETED,
                        failure=exc,
                        termination_proven=True,
                    )
                return PlannerExecution(
                    PlannerExecutionState.COMPLETED,
                    value=value,
                    termination_proven=True,
                )
            # cancel() is authoritative only when the operation never started.
            terminated = future.cancel()
            with started:
                provider_dispatched = dispatch_state["started"]
            return PlannerExecution(
                PlannerExecutionState.TIMED_OUT,
                termination_proven=terminated,
                provider_dispatched=provider_dispatched,
            )
        except BaseException as exc:
            return PlannerExecution(
                PlannerExecutionState.COMPLETED,
                failure=exc,
                termination_proven=True,
            )
        return PlannerExecution(
            PlannerExecutionState.COMPLETED,
            value=value,
            termination_proven=True,
        )

    def close(self) -> None:
        """Stop accepting work and cancel operations that have not started."""
        self._executor.shutdown(wait=False, cancel_futures=True)

    def __enter__(self) -> "BoundedPlannerExecutionAuthority":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class ZeroSensitiveObserver:
    """Accept only fixed, low-cardinality fields; never arbitrary labels."""

    def __init__(
        self,
        *,
        clock: MonotonicAuthority,
        retention_seconds: int = 7 * 86_400,
        capacity: int = 1024,
    ) -> None:
        if (
            not callable(getattr(clock, "now", None))
            or type(retention_seconds) is not int
            or not 1 <= retention_seconds <= 7 * 86_400
            or type(capacity) is not int
            or not 1 <= capacity <= 4096
        ):
            raise ValueError("invalid preview observer")
        self._clock = clock
        self._retention = retention_seconds
        self._capacity = capacity
        self._events: list[tuple[float, PreviewObservation]] = []
        self._lock = RLock()

    def record(self, observation: PreviewObservation) -> None:
        if type(observation) is not PreviewObservation:
            raise ValueError("invalid preview observation")
        with self._lock:
            now = self._now()
            self._prune(now)
            self._events.append((now, observation))
            if len(self._events) > self._capacity:
                del self._events[: len(self._events) - self._capacity]

    @property
    def events(self) -> list[PreviewObservation]:
        with self._lock:
            self._prune(self._now())
            return [observation for _, observation in self._events]

    def _now(self) -> float:
        value = self._clock.now()
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("invalid observer clock")
        return float(value)

    def _prune(self, now: float) -> None:
        cutoff = now - self._retention
        self._events = [
            (timestamp, observation)
            for timestamp, observation in self._events
            if timestamp > cutoff
        ]


class HermesPreviewControlPlane:
    def __init__(
        self,
        *,
        runtime: PreviewAdmissionProductionRuntime,
        policy: PreviewPolicy,
        tokenizer: ExactTokenizer,
        planner: HermesPlannerPort,
        topology: PreviewTopology,
        monotonic: MonotonicAuthority,
        planner_execution: PlannerExecutionAuthority,
        observer: ZeroSensitiveObserver,
    ) -> None:
        if (
            type(runtime) is not PreviewAdmissionProductionRuntime
            or type(policy) is not PreviewPolicy
            or tokenizer.package != policy.tokenizer_package
            or tokenizer.version != policy.tokenizer_version
            or tokenizer.vocab_hash != policy.tokenizer_vocab_hash
            or not callable(getattr(tokenizer, "count", None))
            or not callable(getattr(planner, "plan", None))
            or planner.identity != policy.planner_identity
            or planner.model_id != policy.allowed_model_id
            or planner.capabilities != ("plan",)
            or type(topology) is not PreviewTopology
            or not callable(getattr(monotonic, "now", None))
            or planner_execution.capacity != policy.global_concurrency
            or not callable(getattr(planner_execution, "invoke", None))
            or type(observer) is not ZeroSensitiveObserver
        ):
            raise ValueError("invalid preview control plane")
        self._runtime = runtime
        self._policy = policy
        self._tokenizer = tokenizer
        self._planner = planner
        self._topology = topology
        self._proxy = topology.proxy_policy
        self._monotonic = monotonic
        self._planner_execution = planner_execution
        self._observer = observer

    def execute(
        self,
        request: PreviewRequest,
        *,
        peer_ip: str,
        canonical_client_ip: str | None,
    ) -> PreviewExecution:
        try:
            started = self._now()
        except ValueError:
            return PreviewExecution(
                PreviewControlStatus.UNAVAILABLE, "deadline_unavailable"
            )
        admission = self.admit(
            request,
            peer_ip=peer_ip,
            canonical_client_ip=canonical_client_ip,
        )
        if admission.status is not PreviewControlStatus.ADMITTED:
            return PreviewExecution(admission.status, admission.reason)
        total_deadline = started + TOTAL_DEADLINE_SECONDS
        try:
            before_dispatch = self._now()
        except ValueError:
            before_dispatch = total_deadline
        if before_dispatch >= total_deadline:
            if not self.reconcile(
                admission,
                terminal_class=PreviewTerminalClass.CLIENT_ABORT,
                provider_dispatched=False,
            ):
                return PreviewExecution(
                    PreviewControlStatus.UNAVAILABLE, "terminal_unavailable"
                )
            return PreviewExecution(
                PreviewControlStatus.UNAVAILABLE, "deadline_unavailable"
            )
        provider_deadline = min(
            before_dispatch + PROVIDER_TIMEOUT_SECONDS, total_deadline
        )
        try:
            envelope = canonical_planner_envelope(
                admission.payload,
                model_id=self._policy.allowed_model_id,
            )
            execution = self._planner_execution.invoke(
                lambda: self._planner.plan(
                    envelope,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                    provider_deadline=provider_deadline,
                    total_deadline=total_deadline,
                    attempts=ATTEMPTS,
                ),
                timeout_seconds=provider_deadline - before_dispatch,
            )
            if type(execution) is not PlannerExecution:
                raise ValueError("invalid planner execution authority")
            if execution.state is PlannerExecutionState.TIMED_OUT:
                if execution.termination_proven:
                    terminal_class = (
                        PreviewTerminalClass.PROVIDER_TIMEOUT
                        if execution.provider_dispatched
                        else PreviewTerminalClass.CLIENT_ABORT
                    )
                    if not self.reconcile(
                        admission,
                        terminal_class=terminal_class,
                        provider_dispatched=execution.provider_dispatched,
                    ):
                        return PreviewExecution(
                            PreviewControlStatus.UNAVAILABLE,
                            "terminal_unavailable",
                        )
                return PreviewExecution(
                    PreviewControlStatus.UNAVAILABLE,
                    "planner_timeout_pending"
                    if not execution.termination_proven
                    else "planner_unavailable",
                )
            if execution.failure is not None:
                raise execution.failure
            result = execution.value
            if type(result) is not PlannerResult:
                terminal_class = PreviewTerminalClass.SCHEMA_FAILURE
                result = None
            elif self._now() > provider_deadline:
                terminal_class = PreviewTerminalClass.PROVIDER_TIMEOUT
                result = None
            else:
                terminal_class = PreviewTerminalClass.SUCCESS
        except PlannerExecutionSaturated:
            if not self.reconcile(
                admission,
                terminal_class=PreviewTerminalClass.CLIENT_ABORT,
                provider_dispatched=False,
            ):
                return PreviewExecution(
                    PreviewControlStatus.UNAVAILABLE, "terminal_unavailable"
                )
            return PreviewExecution(
                PreviewControlStatus.UNAVAILABLE, "planner_saturated"
            )
        except PlannerPortFailure as exc:
            terminal_class = exc.terminal_class
            usage = self._usage(
                admission, exc.input_tokens, exc.output_tokens
            )
            if not self.reconcile(
                admission,
                terminal_class=terminal_class,
                actual_tokens=usage[0] if usage is not None else None,
                actual_micro_usd=usage[1] if usage is not None else None,
            ):
                return PreviewExecution(
                    PreviewControlStatus.UNAVAILABLE, "terminal_unavailable"
                )
            return PreviewExecution(
                PreviewControlStatus.UNAVAILABLE, "planner_unavailable"
            )
        except Exception:
            terminal_class = PreviewTerminalClass.SCHEMA_FAILURE
            if not self.reconcile(admission, terminal_class=terminal_class):
                return PreviewExecution(
                    PreviewControlStatus.UNAVAILABLE, "terminal_unavailable"
                )
            return PreviewExecution(
                PreviewControlStatus.UNAVAILABLE, "planner_unavailable"
            )
        except BaseException:
            self.reconcile(
                admission,
                terminal_class=PreviewTerminalClass.SCHEMA_FAILURE,
            )
            raise
        if result is None:
            if not self.reconcile(admission, terminal_class=terminal_class):
                return PreviewExecution(
                    PreviewControlStatus.UNAVAILABLE, "terminal_unavailable"
                )
            return PreviewExecution(
                PreviewControlStatus.UNAVAILABLE, "planner_unavailable"
            )
        usage = self._usage(
            admission, result.input_tokens, result.output_tokens
        )
        if not self.reconcile(
            admission,
            terminal_class=terminal_class,
            actual_tokens=usage[0] if usage is not None else None,
            actual_micro_usd=usage[1] if usage is not None else None,
        ):
            return PreviewExecution(
                PreviewControlStatus.UNAVAILABLE, "terminal_unavailable"
            )
        if usage is None:
            return PreviewExecution(
                PreviewControlStatus.UNAVAILABLE, "invalid_provider_usage"
            )
        return PreviewExecution(
            PreviewControlStatus.ADMITTED, "completed", result.value
        )

    def admit(
        self,
        request: PreviewRequest,
        *,
        peer_ip: str,
        canonical_client_ip: str | None,
    ) -> PreviewAdmission:
        try:
            if type(request) is not PreviewRequest:
                raise ValueError
            identity = self._proxy.canonical_identity(
                peer_ip=peer_ip,
                canonical_client_ip=canonical_client_ip,
            )
            payload = request.canonical_payload()
            envelope = canonical_planner_envelope(
                payload,
                model_id=self._policy.allowed_model_id,
            )
            input_tokens = self._tokenizer.count(envelope)
            if (
                type(input_tokens) is not int
                or not 1 <= input_tokens <= MAX_INPUT_TOKENS
            ):
                raise ValueError
            reserved_micro_usd = _ceil_micro_usd(
                input_tokens,
                MAX_OUTPUT_TOKENS,
                self._policy.input_micro_usd_per_million_tokens,
                self._policy.output_micro_usd_per_million_tokens,
            )
            if not 1 <= reserved_micro_usd <= self._policy.minute_micro_usd:
                raise ValueError
            clock = self._runtime.executor._durable_gate._trusted_clock
            interval = clock.trusted_interval()
            buckets = clock.buckets()
            if (
                interval.earliest < self._policy.valid_from_epoch
                or interval.latest >= self._policy.valid_until_epoch
            ):
                raise ValueError
            authority = self._runtime.executor._lifecycle_authority
            snapshot = authority.snapshot()
            digests = authority.derive(snapshot, identity)
            reservation_id = str(uuid.uuid4())
            owner_digest = hashlib.sha256(
                b"TrustForge/PAP1/owner/v1\x00"
                + reservation_id.encode()
            ).hexdigest()
            unbound = AdmissionCompileRequest(
                interval=interval,
                buckets=buckets,
                policy_digest=self._policy.policy_digest,
                owner_digest=owner_digest,
                identity_digest="0" * 64,
                previous_identity_digest=(
                    "1" * 64
                    if snapshot.lifecycle.previous is not None
                    else None
                ),
                reservation_id=reservation_id,
                reserved_tokens=input_tokens + MAX_OUTPUT_TOKENS,
                reserved_micro_usd=reserved_micro_usd,
                lifecycle_generation=snapshot.lifecycle.generation,
                current_quota_key_version=snapshot.lifecycle.current.version,
                previous_quota_key_version=(
                    snapshot.lifecycle.previous.version
                    if snapshot.lifecycle.previous is not None
                    else None
                ),
            )
            bound = authority.bind_admission(unbound, digests)
            result = self._runtime.executor.execute(bound)
        except Exception:
            return self._result(PreviewControlStatus.UNAVAILABLE, "authority_unavailable")
        if result.outcome is AdmissionOutcome.ADMITTED:
            return self._result(
                PreviewControlStatus.ADMITTED,
                "admitted",
                handle=result.handle,
                payload=payload,
                reserved_input_tokens=input_tokens,
            )
        if result.outcome is AdmissionOutcome.DENIED:
            return self._result(PreviewControlStatus.DENIED, "policy_denied")
        return self._result(PreviewControlStatus.UNAVAILABLE, "authority_unavailable")

    def reconcile(
        self,
        admission: PreviewAdmission,
        *,
        terminal_class: PreviewTerminalClass,
        actual_tokens: int | None = None,
        actual_micro_usd: int | None = None,
        provider_dispatched: bool = True,
    ) -> bool:
        if (
            type(admission) is not PreviewAdmission
            or admission.status is not PreviewControlStatus.ADMITTED
            or admission.handle is None
            or type(terminal_class) is not PreviewTerminalClass
            or type(provider_dispatched) is not bool
        ):
            return False
        handle = admission.handle
        usage_valid = (
            type(actual_tokens) is int
            and type(actual_micro_usd) is int
            and 0 <= actual_tokens <= handle.reserved_tokens
            and 0 <= actual_micro_usd <= handle.reserved_micro_usd
        )
        known = usage_valid and terminal_class in (
            PreviewTerminalClass.SUCCESS,
            PreviewTerminalClass.KNOWN_PROVIDER_FAILURE,
        )
        disposition = (
            TerminalDisposition.PRE_PROVIDER_ABORT
            if not provider_dispatched
            else
            TerminalDisposition.KNOWN_SUCCESS
            if terminal_class is PreviewTerminalClass.SUCCESS and known
            else TerminalDisposition.KNOWN_FAILURE
            if terminal_class is PreviewTerminalClass.KNOWN_PROVIDER_FAILURE and known
            else TerminalDisposition.UNCERTAIN
        )
        try:
            clock = self._runtime.executor._durable_gate._trusted_clock
            result = self._runtime.terminal.reconcile(
                TerminalIntent(
                    handle=admission.handle,
                    interval=clock.trusted_interval(),
                    disposition=disposition,
                    actual_tokens=actual_tokens if known else None,
                    actual_micro_usd=actual_micro_usd if known else None,
                    circuit_failure=terminal_class.counts_for_circuit,
                )
            )
            reconciled = result.outcome is TerminalOutcome.RECONCILED
        except Exception:
            reconciled = False
        self._observe(
            PreviewObservationOutcome.RECONCILED
            if reconciled
            else PreviewObservationOutcome.UNAVAILABLE,
            terminal_class,
            terminal_class.counts_for_circuit,
        )
        return reconciled

    def _usage(
        self,
        admission: PreviewAdmission,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> tuple[int, int] | None:
        if (
            type(input_tokens) is not int
            or type(output_tokens) is not int
            or type(admission.reserved_input_tokens) is not int
            or input_tokens != admission.reserved_input_tokens
            or not 0 <= output_tokens <= MAX_OUTPUT_TOKENS
        ):
            return None
        return (
            input_tokens + output_tokens,
            _ceil_micro_usd(
                input_tokens,
                output_tokens,
                self._policy.input_micro_usd_per_million_tokens,
                self._policy.output_micro_usd_per_million_tokens,
            ),
        )

    def _now(self) -> float:
        value = self._monotonic.now()
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("invalid deadline clock")
        return float(value)

    def _result(
        self,
        status: PreviewControlStatus,
        reason: str,
        *,
        handle: object | None = None,
        payload: bytes | None = None,
        reserved_input_tokens: int | None = None,
    ) -> PreviewAdmission:
        self._observe(PreviewObservationOutcome(status.value), None, False)
        return PreviewAdmission(
            status, reason, handle, payload, reserved_input_tokens
        )

    def _observe(
        self,
        outcome: PreviewObservationOutcome,
        terminal_class: PreviewTerminalClass | None,
        circuit_failure: bool,
    ) -> None:
        try:
            self._observer.record(
                PreviewObservation.mint(
                    outcome, terminal_class, circuit_failure
                )
            )
        except Exception:
            pass


def _uuid4(value: str) -> bool:
    try:
        parsed = uuid.UUID(value)
    except (TypeError, ValueError, AttributeError):
        return False
    return parsed.version == 4 and str(parsed) == value


def _strict_ascii_metadata(value: str) -> bool:
    return value.isascii() and all(0x21 <= ord(character) <= 0x7E for character in value)


def canonical_planner_envelope(payload: bytes, *, model_id: str) -> bytes:
    """The exact provider input reserved, dispatched, and usage-checked."""
    if (
        type(payload) is not bytes
        or not payload
        or len(payload) > MAX_BODY_BYTES
        or type(model_id) is not str
        or not _strict_ascii_metadata(model_id)
    ):
        raise ValueError("invalid planner envelope")
    try:
        request = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("invalid planner envelope") from None
    if (
        type(request) is not dict
        or json.dumps(
            request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        != payload
    ):
        raise ValueError("invalid planner envelope")
    envelope = json.dumps(
        {
            "input": request,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "model": model_id,
            "system": PLANNER_SYSTEM_PROMPT,
            "version": PLANNER_ENVELOPE_VERSION,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    if len(envelope) > MAX_BODY_BYTES:
        raise ValueError("invalid planner envelope")
    return envelope


def _canonical_ip(value: str) -> object:
    parsed = ipaddress.ip_address(value)
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        return parsed.ipv4_mapped
    return parsed


def _ceil_micro_usd(
    input_tokens: int,
    output_tokens: int,
    input_rate: int,
    output_rate: int,
) -> int:
    numerator = input_tokens * input_rate + output_tokens * output_rate
    return (numerator + 999_999) // 1_000_000


def canonical_preview_policy_digest(policy: PreviewPolicy) -> str:
    names = (
        "source_policy_version",
        "model_price_policy_version",
        "tokenizer_package",
        "tokenizer_version",
        "tokenizer_vocab_hash",
        "allowed_model_id",
        "planner_identity",
        "input_micro_usd_per_million_tokens",
        "output_micro_usd_per_million_tokens",
        "valid_from_epoch",
        "valid_until_epoch",
        "policy_version",
        "identity_requests_minute",
        "identity_requests_day",
        "identity_concurrency",
        "global_concurrency",
        "minute_tokens",
        "day_tokens",
        "minute_micro_usd",
        "day_micro_usd",
    )
    payload = json.dumps(
        {name: getattr(policy, name) for name in names},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(b"TrustForge/HermesPreviewPolicy/v1\x00" + payload).hexdigest()
