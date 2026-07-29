"""Durable, fail-closed dispatch fence for paid-preview admission.

The control row is deliberately a single, fixed DynamoDB item.  An application
process may cache that it is closed, but only this row is admission authority.
There is no missing-row bootstrap path in application code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
import threading
from typing import Protocol

from trustforge.preview_admission_compiler import AdmissionHandle, CompiledAdmissionPlan


CONTROL_KEY = {
    "pk": {"S": "PAP#1#CONTROL"},
    "sk": {"S": "ADMISSION#QUARANTINE"},
}
CONTROL_KIND = "preview_admission_quarantine"
CONTROL_SCHEMA_VERSION = 1
FINGERPRINT_VERSION = 1
_DOMAIN = b"TrustForge/PAP1/admission-write-plan/v1\x00"
_MAX_GENERATION = 10**38 - 1


class GateState(StrEnum):
    OPEN = "open"
    DISPATCHING = "dispatching"
    QUARANTINED = "quarantined"


class ProofDisposition(StrEnum):
    PRESENT = "present"
    UNRESOLVED = "unresolved"


class GateClient(Protocol):
    def get_item(self, **kwargs: object) -> object: ...

    def put_item(self, **kwargs: object) -> object: ...


@dataclass(frozen=True, slots=True)
class DispatchBinding:
    """Opaque exact binding for one dispatch; safe repr omits all material."""

    generation: int = field(repr=False)
    reservation_id: str = field(repr=False)
    plan_fingerprint: str = field(repr=False)
    dispatch_lower: int = field(repr=False)
    dispatch_upper: int = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.generation) is not int
            or not 1 <= self.generation <= _MAX_GENERATION
            or not _uuid(self.reservation_id)
            or not _digest(self.plan_fingerprint)
            or type(self.dispatch_lower) is not int
            or type(self.dispatch_upper) is not int
            or not 0 <= self.dispatch_lower <= self.dispatch_upper
        ):
            raise ValueError("invalid dispatch binding")


@dataclass(frozen=True, slots=True)
class QuarantineProof:
    disposition: ProofDisposition
    handle: AdmissionHandle | None = field(default=None, repr=False)
    binding: DispatchBinding | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        present = self.disposition is ProofDisposition.PRESENT
        if (
            type(self.disposition) is not ProofDisposition
            or present != (type(self.handle) is AdmissionHandle)
            or present != (type(self.binding) is DispatchBinding)
        ):
            raise ValueError("invalid quarantine proof")


@dataclass(frozen=True, slots=True)
class _Control:
    state: GateState
    generation: int
    version: int
    binding: DispatchBinding | None


class DurableAdmissionGate:
    """Serializes dispatches across processes using exact DynamoDB CAS writes."""

    def __init__(self, client: GateClient, table_name: str) -> None:
        if (
            not callable(getattr(client, "get_item", None))
            or not callable(getattr(client, "put_item", None))
            or type(table_name) is not str
            or not table_name
        ):
            raise ValueError("invalid durable admission gate")
        self._client = client
        self._table = table_name
        self._lock = threading.Lock()
        self._control: _Control | None = None
        self._closed = True
        self._load_startup_authority()

    @classmethod
    def from_boto3(
        cls, table_name: str, *, region_name: str | None = None
    ) -> "DurableAdmissionGate":
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "dynamodb",
            region_name=region_name,
            config=Config(retries={"total_max_attempts": 1, "mode": "standard"}),
        )
        return cls(client, table_name)

    @property
    def ready(self) -> bool:
        with self._lock:
            return not self._closed and self._control is not None

    @property
    def pending_binding(self) -> DispatchBinding | None:
        """Return a nominal recovery capability for a strict non-OPEN row."""

        with self._lock:
            control = self._control
            if (
                control is None
                or control.state is GateState.OPEN
                or type(control.binding) is not DispatchBinding
            ):
                return None
            return control.binding

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def begin(
        self,
        plan: CompiledAdmissionPlan,
        *,
        dispatch_lower: int,
        dispatch_upper: int,
    ) -> DispatchBinding | None:
        """CAS OPEN to DISPATCHING once; any uncertainty permanently closes."""

        if type(plan) is not CompiledAdmissionPlan:
            return None
        if (
            dispatch_lower != plan.handle.created_lower
            or dispatch_upper != plan.handle.created_upper
        ):
            self.close()
            return None
        fingerprint = admission_plan_fingerprint(plan)
        with self._lock:
            current = self._control
            if self._closed or current is None or current.state is not GateState.OPEN:
                return None
            try:
                binding = DispatchBinding(
                    current.generation + 1,
                    plan.handle.reservation_id,
                    fingerprint,
                    dispatch_lower,
                    dispatch_upper,
                )
            except ValueError:
                self._closed = True
                return None
            request = _put_request(
                self._table,
                _control_item(GateState.DISPATCHING, binding.generation, current.version + 1, binding),
                current,
            )
            try:
                response = self._client.put_item(**request)
            except Exception:
                self._closed = True
                return None
            if not _confirmed_2xx(response):
                self._closed = True
                return None
            self._control = _Control(
                GateState.DISPATCHING, binding.generation, current.version + 1, binding
            )
            self._closed = True
            return binding

    def quarantine_action(self, binding: DispatchBinding) -> dict[str, object]:
        """Return the action appended to the canonical admission transaction."""

        current = self._control
        if (
            type(binding) is not DispatchBinding
            or current is None
            or current.state is not GateState.DISPATCHING
            or current.binding != binding
        ):
            raise ValueError("invalid dispatch binding")
        item = _control_item(
            GateState.QUARANTINED, binding.generation, current.version + 1, binding
        )
        return {
            "Put": {
                "TableName": self._table,
                "Item": item,
                "ConditionExpression": (
                    "#pk=:pk AND #sk=:sk AND #kind=:kind AND #schema=:schema "
                    "AND #state=:state AND #generation=:generation "
                    "AND #version=:version AND #reservation=:reservation "
                    "AND #fingerprint=:fingerprint AND #lower=:lower AND #upper=:upper"
                ),
                "ExpressionAttributeNames": _NAMES,
                "ExpressionAttributeValues": _expected_values(current),
            }
        }

    def confirm_rejected(self, binding: DispatchBinding) -> bool:
        """Reopen only after an authoritative 4xx transaction rejection."""

        return self._open_exact(binding, GateState.DISPATCHING)

    def confirm_admitted(self, binding: DispatchBinding, handle: AdmissionHandle) -> bool:
        """Prove QUARANTINED then reopen before callers may return ADMITTED."""

        proof = self.prove_present(binding, handle)
        return (
            proof.disposition is ProofDisposition.PRESENT
            and self._open_exact(binding, GateState.QUARANTINED)
        )

    def prove_present(
        self, binding: DispatchBinding, handle: AdmissionHandle
    ) -> QuarantineProof:
        """Strongly prove the exact quarantine row; absence is never proof."""

        if (
            type(binding) is not DispatchBinding
            or type(handle) is not AdmissionHandle
            or handle.reservation_id != binding.reservation_id
        ):
            return QuarantineProof(ProofDisposition.UNRESOLVED)
        try:
            response = self._client.get_item(
                TableName=self._table, Key=CONTROL_KEY, ConsistentRead=True
            )
            item = _response_item(response)
            control = _decode_control(item)
        except Exception:
            return QuarantineProof(ProofDisposition.UNRESOLVED)
        if (
            control is None
            or control.state is not GateState.QUARANTINED
            or control.binding != binding
        ):
            return QuarantineProof(ProofDisposition.UNRESOLVED)
        with self._lock:
            self._control = control
            self._closed = True
        return QuarantineProof(ProofDisposition.PRESENT, handle, binding)

    def _load_startup_authority(self) -> None:
        try:
            response = self._client.get_item(
                TableName=self._table, Key=CONTROL_KEY, ConsistentRead=True
            )
            control = _decode_control(_response_item(response))
        except Exception:
            return
        if control is not None:
            self._control = control
            self._closed = control.state is not GateState.OPEN

    def _open_exact(self, binding: DispatchBinding, state: GateState) -> bool:
        with self._lock:
            current = self._control
            if (
                type(binding) is not DispatchBinding
                or current is None
                or current.state is not state
                or current.binding != binding
            ):
                return False
            following = _Control(
                GateState.OPEN, current.generation, current.version + 1, None
            )
            try:
                response = self._client.put_item(
                    **_put_request(
                        self._table,
                        _control_item(
                            GateState.OPEN,
                            following.generation,
                            following.version,
                            None,
                        ),
                        current,
                    )
                )
            except Exception:
                self._closed = True
                return self._prove_exact_open(following)
            if not _confirmed_2xx(response):
                self._closed = True
                return self._prove_exact_open(following)
            self._control = following
            self._closed = False
            return True

    def _prove_exact_open(self, expected: _Control) -> bool:
        try:
            response = self._client.get_item(
                TableName=self._table, Key=CONTROL_KEY, ConsistentRead=True
            )
            actual = _decode_control(_response_item(response))
        except Exception:
            return False
        if actual != expected or actual.state is not GateState.OPEN:
            return False
        self._control = actual
        self._closed = False
        return True


def admission_plan_fingerprint(plan: CompiledAdmissionPlan) -> str:
    """Domain-separated SHA-256 over the versioned canonical write projection."""

    if type(plan) is not CompiledAdmissionPlan:
        raise ValueError("invalid admission plan")
    request = plan.transact_write_items_request()
    projection = {
        "fingerprint_version": FINGERPRINT_VERSION,
        "write": request,
    }
    encoded = json.dumps(
        projection, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return hashlib.sha256(_DOMAIN + encoded).hexdigest()


def append_quarantine_action(
    plan: CompiledAdmissionPlan, gate: DurableAdmissionGate, binding: DispatchBinding
) -> dict[str, object]:
    request = plan.transact_write_items_request()
    actions = request.get("TransactItems")
    if type(actions) is not list or len(actions) not in (10, 13):
        raise ValueError("invalid admission plan")
    request["TransactItems"] = [*actions, gate.quarantine_action(binding)]
    request["ClientRequestToken"] = plan.handle.reservation_id
    return request


def pre_provider_abort_intent(
    proof: QuarantineProof, interval: object
) -> object:
    """Compile the only terminal disposition authorized by QUARANTINED.

    The local import keeps the gate independent from the terminal executor while
    still making the proof protocol nominal and fail closed for #992.
    """

    if (
        type(proof) is not QuarantineProof
        or proof.disposition is not ProofDisposition.PRESENT
        or type(proof.handle) is not AdmissionHandle
    ):
        raise ValueError("exact quarantine proof required")
    from trustforge.preview_terminal_reconcile import (
        TerminalDisposition,
        TerminalIntent,
    )

    return TerminalIntent(
        handle=proof.handle,
        interval=interval,  # type: ignore[arg-type]
        disposition=TerminalDisposition.PRE_PROVIDER_ABORT,
    )


_NAMES = {
    "#pk": "pk",
    "#sk": "sk",
    "#kind": "kind",
    "#schema": "schema_version",
    "#state": "state",
    "#generation": "generation",
    "#version": "version",
    "#reservation": "reservation_id",
    "#fingerprint": "plan_fingerprint",
    "#lower": "dispatch_lower",
    "#upper": "dispatch_upper",
}


def _control_item(
    state: GateState,
    generation: int,
    version: int,
    binding: DispatchBinding | None,
) -> dict[str, object]:
    item: dict[str, object] = {
        **CONTROL_KEY,
        "kind": {"S": CONTROL_KIND},
        "schema_version": {"N": str(CONTROL_SCHEMA_VERSION)},
        "state": {"S": state.value},
        "generation": {"N": str(generation)},
        "version": {"N": str(version)},
    }
    if binding is not None:
        item.update(
            {
                "reservation_id": {"S": binding.reservation_id},
                "plan_fingerprint": {"S": binding.plan_fingerprint},
                "dispatch_lower": {"N": str(binding.dispatch_lower)},
                "dispatch_upper": {"N": str(binding.dispatch_upper)},
            }
        )
    return item


def _put_request(
    table: str, item: dict[str, object], expected: _Control
) -> dict[str, object]:
    return {
        "TableName": table,
        "Item": item,
        "ConditionExpression": (
            "#pk=:pk AND #sk=:sk AND #kind=:kind AND #schema=:schema "
            "AND #state=:state AND #generation=:generation AND #version=:version"
            + (
                ""
                if expected.binding is None
                else (
                    " AND #reservation=:reservation AND #fingerprint=:fingerprint "
                    "AND #lower=:lower AND #upper=:upper"
                )
            )
        ),
        "ExpressionAttributeNames": {
            key: value
            for key, value in _NAMES.items()
            if expected.binding is not None
            or key
            not in {"#reservation", "#fingerprint", "#lower", "#upper"}
        },
        "ExpressionAttributeValues": _expected_values(expected),
    }


def _expected_values(control: _Control) -> dict[str, object]:
    values: dict[str, object] = {
        ":pk": CONTROL_KEY["pk"],
        ":sk": CONTROL_KEY["sk"],
        ":kind": {"S": CONTROL_KIND},
        ":schema": {"N": str(CONTROL_SCHEMA_VERSION)},
        ":state": {"S": control.state.value},
        ":generation": {"N": str(control.generation)},
        ":version": {"N": str(control.version)},
    }
    if control.binding is not None:
        values.update(
            {
                ":reservation": {"S": control.binding.reservation_id},
                ":fingerprint": {"S": control.binding.plan_fingerprint},
                ":lower": {"N": str(control.binding.dispatch_lower)},
                ":upper": {"N": str(control.binding.dispatch_upper)},
            }
        )
    return values


def _decode_control(item: object) -> _Control | None:
    if type(item) is not dict:
        return None
    base = {
        "pk", "sk", "kind", "schema_version", "state", "generation", "version"
    }
    try:
        state = GateState(_s(item["state"]))
        expected = base if state is GateState.OPEN else base | {
            "reservation_id", "plan_fingerprint", "dispatch_lower", "dispatch_upper"
        }
        if (
            set(item) != expected
            or item["pk"] != CONTROL_KEY["pk"]
            or item["sk"] != CONTROL_KEY["sk"]
            or _s(item["kind"]) != CONTROL_KIND
            or _n(item["schema_version"]) != CONTROL_SCHEMA_VERSION
        ):
            return None
        generation = _n(item["generation"])
        version = _n(item["version"])
        if not 0 <= generation <= _MAX_GENERATION or not 0 <= version <= _MAX_GENERATION:
            return None
        binding = None
        if state is not GateState.OPEN:
            binding = DispatchBinding(
                generation,
                _s(item["reservation_id"]),
                _s(item["plan_fingerprint"]),
                _n(item["dispatch_lower"]),
                _n(item["dispatch_upper"]),
            )
        return _Control(state, generation, version, binding)
    except (KeyError, TypeError, ValueError):
        return None


def _response_item(response: object) -> object:
    if type(response) is not dict or set(response) - {"Item", "ResponseMetadata"}:
        raise ValueError("malformed control response")
    return response.get("Item")


def _confirmed_2xx(response: object) -> bool:
    if type(response) is not dict:
        return False
    metadata = response.get("ResponseMetadata")
    return (
        type(metadata) is dict
        and metadata.get("HTTPStatusCode") == 200
        and type(metadata.get("RequestId")) is str
        and bool(metadata["RequestId"])
    )


def _s(value: object) -> str:
    if type(value) is not dict or set(value) != {"S"} or type(value["S"]) is not str:
        raise ValueError
    return value["S"]


def _n(value: object) -> int:
    if type(value) is not dict or set(value) != {"N"} or type(value["N"]) is not str:
        raise ValueError
    text = value["N"]
    if not text or (text.startswith("0") and text != "0") or not text.isascii() or not text.isdigit():
        raise ValueError
    return int(text)


def _uuid(value: object) -> bool:
    import uuid

    try:
        return type(value) is str and str(uuid.UUID(value, version=4)) == value
    except (ValueError, AttributeError):
        return False


def _digest(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
