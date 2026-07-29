"""Single-attempt, fail-closed executor for paid-preview admission."""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from enum import StrEnum
import math
import re
import threading
from typing import Protocol

from trustforge.preview_admission_compiler import (
    AdmissionCompileDenied,
    AdmissionCompileRequest,
    AdmissionDeniedReason,
    AdmissionHandle,
    build_transact_get_request,
    compile_admission,
    decode_transact_get_responses,
)
from trustforge.preview_durable_admission_gate import (
    DispatchBinding,
    DurableAdmissionGate,
    append_quarantine_action,
)
from trustforge.preview_trusted_clock import PreviewTrustedClock, TrustedUtcInterval


class DynamoAdmissionClient(Protocol):
    def transact_get_items(self, **kwargs: object) -> object: ...

    def transact_write_items(self, **kwargs: object) -> object: ...


@dataclass(frozen=True, slots=True)
class AdmissionAmbiguity:
    """Immutable, non-rendering material required to prove an ambiguous write."""

    handle: AdmissionHandle = dataclass_field(repr=False)
    write_fingerprint: str = dataclass_field(repr=False)
    interval: TrustedUtcInterval = dataclass_field(repr=False)

    def __post_init__(self) -> None:
        try:
            self.handle.__post_init__()
        except (TypeError, ValueError):
            raise ValueError("invalid admission ambiguity") from None
        if (
            type(self.handle) is not AdmissionHandle
            or type(self.write_fingerprint) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", self.write_fingerprint)
            or type(self.interval) is not TrustedUtcInterval
            or not math.isfinite(self.interval.earliest)
            or not math.isfinite(self.interval.latest)
            or self.interval.earliest > self.interval.latest
            or self.interval.earliest != self.handle.created_lower
            or self.interval.latest != self.handle.created_upper
        ):
            raise ValueError("invalid admission ambiguity")


_RESOLUTION_TOKEN = object()


class AdmissionAmbiguityResolution:
    """Composition-integrity result created by the concrete recovery service."""

    __slots__ = ("_ambiguity", "_write_fingerprint")

    def __init__(self, ambiguity: AdmissionAmbiguity, token: object) -> None:
        if token is not _RESOLUTION_TOKEN or type(ambiguity) is not AdmissionAmbiguity:
            raise ValueError("invalid ambiguity resolution")
        self._ambiguity = ambiguity
        self._write_fingerprint = ambiguity.write_fingerprint

    def _proves(self, ambiguity: AdmissionAmbiguity) -> bool:
        return (
            self._ambiguity is ambiguity
            and self._write_fingerprint == ambiguity.write_fingerprint
        )


def _confirmed_ambiguity_resolution(
    ambiguity: AdmissionAmbiguity,
) -> AdmissionAmbiguityResolution:
    return AdmissionAmbiguityResolution(ambiguity, _RESOLUTION_TOKEN)


class AdmissionAmbiguityResolver(Protocol):
    def resolve(
        self, ambiguity: AdmissionAmbiguity
    ) -> AdmissionAmbiguityResolution | None: ...


class AdmissionOutcome(StrEnum):
    ADMITTED = "admitted"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class AdmissionExecutionResult:
    outcome: AdmissionOutcome
    handle: AdmissionHandle | None = dataclass_field(default=None, repr=False)
    denied_reason: AdmissionDeniedReason | None = None

    def __post_init__(self) -> None:
        valid = (
            (
                self.outcome is AdmissionOutcome.ADMITTED
                and type(self.handle) is AdmissionHandle
                and self.denied_reason is None
            )
            or (
                self.outcome is AdmissionOutcome.DENIED
                and self.handle is None
                and type(self.denied_reason) is AdmissionDeniedReason
            )
            or (
                self.outcome is AdmissionOutcome.UNAVAILABLE
                and self.handle is None
                and self.denied_reason is None
            )
        )
        if not valid:
            raise ValueError("invalid admission execution result")


_UNAVAILABLE = AdmissionExecutionResult(AdmissionOutcome.UNAVAILABLE)

# These service responses establish that the requested transaction was rejected.
# Everything else after dispatch has an unknown commit disposition and poisons the
# executor instance until lifecycle recovery exists (#974).
_CONFIRMED_WRITE_REJECTION_CODES = frozenset(
    {
        "ConditionalCheckFailedException",
        "IdempotentParameterMismatchException",
        "ProvisionedThroughputExceededException",
        "RequestLimitExceeded",
        "ResourceNotFoundException",
        "ThrottlingException",
        "TransactionCanceledException",
        "TransactionConflictException",
        "ValidationException",
    }
)


class PreviewAdmissionExecutor:
    """Execute canonical admission transactions with no retry or fallback."""

    def __init__(
        self,
        client: DynamoAdmissionClient,
        table_name: str,
        *,
        durable_gate: DurableAdmissionGate,
    ) -> None:
        if not callable(getattr(client, "transact_get_items", None)) or not callable(
            getattr(client, "transact_write_items", None)
        ):
            raise ValueError("invalid admission client")
        # The compiler validates the table grammar before any I/O.
        build_transact_get_request  # keep validation authority in the compiler
        self._client = client
        self._table_name = table_name
        self._write_gate = threading.Lock()
        self._latched_closed = False
        if (
            type(durable_gate) is not DurableAdmissionGate
            or durable_gate._client is not client
            or durable_gate._table != table_name
        ):
            raise ValueError("invalid durable admission gate")
        self._durable_gate = durable_gate
        self._ambiguity: AdmissionAmbiguity | None = None

    @property
    def latched_closed(self) -> bool:
        with self._write_gate:
            return self._latched_closed or not self._durable_gate.ready

    def resolve_ambiguity(self, resolver: AdmissionAmbiguityResolver) -> bool:
        """Resolve under the same gate as writes; false/exception remains closed."""

        with self._write_gate:
            # Runtime exact-type sealing prevents a caller-supplied duck type
            # from asserting success.  Importing here avoids a module cycle.
            from trustforge.preview_lease_recovery import PreviewAmbiguityRecovery

            if type(resolver) is not PreviewAmbiguityRecovery:
                return False
            ambiguity = self._ambiguity
            if not self._latched_closed or ambiguity is None:
                return False
            try:
                resolution = resolver.resolve(ambiguity)
            except Exception:  # noqa: BLE001 - proof failures are fail closed
                return False
            if (
                type(resolution) is not AdmissionAmbiguityResolution
                or not resolution._proves(ambiguity)
                or not self._durable_gate.ready
            ):
                return False
            self._ambiguity = None
            self._latched_closed = False
            return True

    def recover_pending(self, resolver: AdmissionAmbiguityResolver) -> bool:
        """Recover a restart-visible durable quarantine under the write lock."""

        with self._write_gate:
            from trustforge.preview_lease_recovery import PreviewAmbiguityRecovery

            if (
                type(resolver) is not PreviewAmbiguityRecovery
                or self._durable_gate.ready
            ):
                return False
            try:
                recovered = resolver.resolve_pending()
            except Exception:  # noqa: BLE001 - recovery is fail closed
                return False
            if not recovered or not self._durable_gate.ready:
                return False
            self._ambiguity = None
            self._latched_closed = False
            return True

    @classmethod
    def from_boto3(cls, table_name: str, *, region_name: str | None = None) -> "PreviewAdmissionExecutor":
        """Create a low-level client whose SDK performs exactly one attempt."""

        import boto3
        from botocore.config import Config

        client = boto3.client(
            "dynamodb",
            region_name=region_name,
            config=Config(retries={"total_max_attempts": 1, "mode": "standard"}),
        )
        return cls(
            client,
            table_name,
            durable_gate=DurableAdmissionGate(
                client,
                table_name,
                trusted_clock=PreviewTrustedClock(
                    dynamodb_client=client,
                    table_name=table_name,
                ),
            ),
        )

    def execute(self, request: AdmissionCompileRequest) -> AdmissionExecutionResult:
        # A latched instance must perform zero further DynamoDB I/O. A concurrent
        # execution may finish its read, but the second check under the write gate
        # prevents its write from crossing an ambiguous predecessor.
        with self._write_gate:
            if self._latched_closed or not self._durable_gate.ready:
                return _UNAVAILABLE

        try:
            read_request = build_transact_get_request(request, self._table_name)
            read_response = self._client.transact_get_items(**read_request)
            if type(read_response) is not dict or "Responses" not in read_response:
                return _UNAVAILABLE
            snapshots = decode_transact_get_responses(
                request, read_response["Responses"]
            )
            plan = compile_admission(request, self._table_name, snapshots)
        except AdmissionCompileDenied as exc:
            return AdmissionExecutionResult(
                AdmissionOutcome.DENIED, denied_reason=exc.reason
            )
        except Exception:  # noqa: BLE001 - all read/compiler failures fail closed
            return _UNAVAILABLE

        binding: DispatchBinding | None = None
        write_request = plan.transact_write_items_request()
        if (
            plan.handle.reservation_id != request.reservation_id
            or "ClientRequestToken" in write_request
        ):
            return _UNAVAILABLE
        write_request["ClientRequestToken"] = request.reservation_id

        with self._write_gate:
            if self._latched_closed or not self._durable_gate.ready:
                return _UNAVAILABLE
            binding = self._durable_gate.begin(
                plan,
                dispatch_lower=plan.handle.created_lower,
                dispatch_upper=plan.handle.created_upper,
            )
            if binding is None:
                self._latched_closed = True
                return _UNAVAILABLE
            try:
                write_request = append_quarantine_action(
                    plan, self._durable_gate, binding
                )
            except ValueError:
                self._latched_closed = True
                return _UNAVAILABLE
            try:
                response = self._client.transact_write_items(**write_request)
            except Exception as exc:  # noqa: BLE001 - disposition is classified below
                if _confirmed_write_rejection(exc):
                    if not self._durable_gate.confirm_rejected(binding):
                        self._latched_closed = True
                    return _UNAVAILABLE
                self._latch(plan.handle, binding)
                self._durable_gate.close()
                return _UNAVAILABLE
            if not _confirmed_success(response):
                self._latch(plan.handle, binding)
                self._durable_gate.close()
                return _UNAVAILABLE
            if (
                binding is None
                or not self._durable_gate.confirm_admitted(binding, plan.handle)
            ):
                self._latch(plan.handle, binding)
                return _UNAVAILABLE
            return AdmissionExecutionResult(
                AdmissionOutcome.ADMITTED, handle=plan.handle
            )

    def _latch(
        self,
        handle: AdmissionHandle,
        binding: DispatchBinding,
    ) -> None:
        # Called only while _write_gate is held.
        self._ambiguity = AdmissionAmbiguity(
            handle=handle,
            write_fingerprint=binding.plan_fingerprint,
            interval=TrustedUtcInterval(
                handle.created_lower, handle.created_upper
            ),
        )
        self._latched_closed = True


def _confirmed_write_rejection(exc: Exception) -> bool:
    from botocore.exceptions import ClientError

    if (
        not isinstance(exc, ClientError)
        or exc.operation_name != "TransactWriteItems"
    ):
        return False
    response = exc.response
    if type(response) is not dict:
        return False
    error = response.get("Error")
    metadata = response.get("ResponseMetadata")
    if (
        type(error) is not dict
        or set(error) != {"Code", "Message"}
        or type(error.get("Code")) is not str
        or error["Code"] not in _CONFIRMED_WRITE_REJECTION_CODES
        or type(error.get("Message")) is not str
        or not error["Message"]
        or type(metadata) is not dict
    ):
        return False
    status = metadata.get("HTTPStatusCode")
    request_id = metadata.get("RequestId")
    return (
        type(status) is int
        and 400 <= status <= 499
        and type(request_id) is str
        and bool(request_id)
    )


def _confirmed_success(response: object) -> bool:
    if type(response) is not dict:
        return False
    metadata = response.get("ResponseMetadata")
    return (
        type(metadata) is dict
        and metadata.get("HTTPStatusCode") == 200
        and type(metadata.get("RequestId")) is str
        and bool(metadata["RequestId"])
    )
