"""Internal-only Hermes preview security and cost control plane.

This module has no HTTP, formal-job, tool, connector, or persistence authority.
It accepts arbitrary bounded intent text and composes only the paid-preview
admission and terminal authorities delivered by #975/#991.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import ipaddress
import json
import math
import re
import uuid
from typing import Protocol

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
_ASSET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,15}$")
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
    tokenizer_version: str
    reserved_micro_usd: int
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
            self.tokenizer_version,
        )
        if (
            not _DIGEST_RE.fullmatch(self.policy_digest)
            or any(
                type(value) is not str
                or not value
                or len(value) > 64
                or value != value.strip()
                for value in versions
            )
            or type(self.reserved_micro_usd) is not int
            or not 1 <= self.reserved_micro_usd <= 50_000
            or type(self.input_micro_usd_per_million_tokens) is not int
            or self.input_micro_usd_per_million_tokens < 1
            or type(self.output_micro_usd_per_million_tokens) is not int
            or self.output_micro_usd_per_million_tokens < 1
            or self.reserved_micro_usd
            != math.ceil(
                (
                    MAX_INPUT_TOKENS
                    * self.input_micro_usd_per_million_tokens
                    + MAX_OUTPUT_TOKENS
                    * self.output_micro_usd_per_million_tokens
                )
                / 1_000_000
            )
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


@dataclass(frozen=True, slots=True)
class PreviewRequest:
    question: str = field(repr=False)
    locale: str
    asset_hints: tuple[str, ...] = ()
    client_request_id: str | None = None

    def __post_init__(self) -> None:
        try:
            encoded = self.question.encode("utf-8")
        except Exception:
            raise ValueError("invalid preview request") from None
        if (
            type(self.question) is not str
            or not self.question.strip()
            or not 1 <= len(self.question) <= MAX_QUESTION_CODEPOINTS
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
    trusted_peer_networks: tuple[str, ...]
    _networks: tuple[object, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        try:
            networks = tuple(
                ipaddress.ip_network(value, strict=True)
                for value in self.trusted_peer_networks
            )
        except (TypeError, ValueError):
            raise ValueError("invalid trusted proxy policy") from None
        if not networks:
            raise ValueError("invalid trusted proxy policy")
        object.__setattr__(self, "_networks", networks)

    def canonical_identity(
        self,
        *,
        peer_ip: str,
        canonical_client_ip: str | None,
    ) -> bytes:
        try:
            peer = ipaddress.ip_address(peer_ip)
        except ValueError:
            raise ValueError("unsafe ingress identity") from None
        if not any(peer in network for network in self._networks):
            raise ValueError("unsafe ingress identity")
        try:
            client = ipaddress.ip_address(canonical_client_ip or "")
        except ValueError:
            raise ValueError("unsafe ingress identity") from None
        return f"pap1-client-ip:{client.compressed}".encode()


class ExactTokenizer(Protocol):
    version: str

    def count(self, payload: bytes) -> int: ...


@dataclass(frozen=True, slots=True)
class PlannerResult:
    value: object = field(repr=False)
    actual_tokens: int
    actual_micro_usd: int

    def __post_init__(self) -> None:
        if (
            type(self.actual_tokens) is not int
            or not 0 <= self.actual_tokens <= MAX_INPUT_TOKENS + MAX_OUTPUT_TOKENS
            or type(self.actual_micro_usd) is not int
            or self.actual_micro_usd < 0
        ):
            raise ValueError("invalid planner result")


class HermesPlannerPort(Protocol):
    def plan(
        self,
        payload: bytes,
        *,
        max_output_tokens: int,
        timeout_seconds: int,
        total_deadline_seconds: int,
        attempts: int,
    ) -> PlannerResult: ...


class PlannerPortFailure(RuntimeError):
    def __init__(self, terminal_class: PreviewTerminalClass) -> None:
        if type(terminal_class) is not PreviewTerminalClass:
            raise ValueError("invalid planner failure")
        self.terminal_class = terminal_class
        super().__init__(terminal_class.value)


@dataclass(frozen=True, slots=True)
class PreviewAdmission:
    status: PreviewControlStatus
    reason: str
    handle: object | None = field(default=None, repr=False)
    payload: bytes | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class PreviewExecution:
    status: PreviewControlStatus
    reason: str
    value: object | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class PreviewObservation:
    outcome: str
    terminal_class: str | None
    circuit_failure: bool
    policy_version: int


class ZeroSensitiveObserver:
    """Accept only fixed, low-cardinality fields; never arbitrary labels."""

    def __init__(self) -> None:
        self.events: list[PreviewObservation] = []

    def record(self, observation: PreviewObservation) -> None:
        if type(observation) is not PreviewObservation:
            raise ValueError("invalid preview observation")
        self.events.append(observation)


class HermesPreviewControlPlane:
    def __init__(
        self,
        *,
        runtime: PreviewAdmissionProductionRuntime,
        policy: PreviewPolicy,
        tokenizer: ExactTokenizer,
        planner: HermesPlannerPort,
        proxy_policy: TrustedProxyPolicy,
        observer: ZeroSensitiveObserver,
    ) -> None:
        if (
            type(runtime) is not PreviewAdmissionProductionRuntime
            or type(policy) is not PreviewPolicy
            or tokenizer.version != policy.tokenizer_version
            or not callable(getattr(tokenizer, "count", None))
            or not callable(getattr(planner, "plan", None))
            or type(proxy_policy) is not TrustedProxyPolicy
            or type(observer) is not ZeroSensitiveObserver
        ):
            raise ValueError("invalid preview control plane")
        self._runtime = runtime
        self._policy = policy
        self._tokenizer = tokenizer
        self._planner = planner
        self._proxy = proxy_policy
        self._observer = observer

    def execute(
        self,
        request: PreviewRequest,
        *,
        peer_ip: str,
        canonical_client_ip: str | None,
    ) -> PreviewExecution:
        admission = self.admit(
            request,
            peer_ip=peer_ip,
            canonical_client_ip=canonical_client_ip,
        )
        if admission.status is not PreviewControlStatus.ADMITTED:
            return PreviewExecution(admission.status, admission.reason)
        try:
            result = self._planner.plan(
                admission.payload,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                timeout_seconds=PROVIDER_TIMEOUT_SECONDS,
                total_deadline_seconds=TOTAL_DEADLINE_SECONDS,
                attempts=ATTEMPTS,
            )
            if type(result) is not PlannerResult:
                raise PlannerPortFailure(PreviewTerminalClass.SCHEMA_FAILURE)
            terminal_class = PreviewTerminalClass.SUCCESS
        except PlannerPortFailure as exc:
            terminal_class = exc.terminal_class
            if not self.reconcile(admission, terminal_class=terminal_class):
                return PreviewExecution(
                    PreviewControlStatus.UNAVAILABLE, "terminal_unavailable"
                )
            return PreviewExecution(
                PreviewControlStatus.UNAVAILABLE, "planner_unavailable"
            )
        except Exception:
            terminal_class = PreviewTerminalClass.PROVIDER_TRANSPORT
            if not self.reconcile(admission, terminal_class=terminal_class):
                return PreviewExecution(
                    PreviewControlStatus.UNAVAILABLE, "terminal_unavailable"
                )
            return PreviewExecution(
                PreviewControlStatus.UNAVAILABLE, "planner_unavailable"
            )
        if not self.reconcile(
            admission,
            terminal_class=terminal_class,
            actual_tokens=result.actual_tokens,
            actual_micro_usd=result.actual_micro_usd,
        ):
            return PreviewExecution(
                PreviewControlStatus.UNAVAILABLE, "terminal_unavailable"
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
            input_tokens = self._tokenizer.count(payload)
            if (
                type(input_tokens) is not int
                or not 1 <= input_tokens <= MAX_INPUT_TOKENS
            ):
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
            reservation_id = request.client_request_id or str(uuid.uuid4())
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
                reserved_micro_usd=self._policy.reserved_micro_usd,
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
    ) -> bool:
        if (
            type(admission) is not PreviewAdmission
            or admission.status is not PreviewControlStatus.ADMITTED
            or admission.handle is None
            or type(terminal_class) is not PreviewTerminalClass
        ):
            return False
        known = terminal_class is PreviewTerminalClass.SUCCESS or (
            terminal_class is PreviewTerminalClass.KNOWN_PROVIDER_FAILURE
            and type(actual_tokens) is int
            and type(actual_micro_usd) is int
        )
        disposition = (
            TerminalDisposition.KNOWN_SUCCESS
            if terminal_class is PreviewTerminalClass.SUCCESS
            else TerminalDisposition.KNOWN_FAILURE
            if terminal_class is PreviewTerminalClass.KNOWN_PROVIDER_FAILURE
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
        self._observer.record(
            PreviewObservation(
                "reconciled" if reconciled else "unavailable",
                terminal_class.value,
                terminal_class.counts_for_circuit,
                self._policy.policy_version,
            )
        )
        return reconciled

    def _result(
        self,
        status: PreviewControlStatus,
        reason: str,
        *,
        handle: object | None = None,
        payload: bytes | None = None,
    ) -> PreviewAdmission:
        self._observer.record(
            PreviewObservation(status.value, None, False, self._policy.policy_version)
        )
        return PreviewAdmission(status, reason, handle, payload)


def _uuid4(value: str) -> bool:
    try:
        parsed = uuid.UUID(value)
    except (TypeError, ValueError, AttributeError):
        return False
    return parsed.version == 4 and str(parsed) == value
