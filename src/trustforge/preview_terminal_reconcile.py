"""Atomic, fail-closed terminal reconciliation for paid-preview reservations.

The module contains no provider integration.  It strictly reads the durable
reservation and its counters, compiles one conditional DynamoDB transaction,
and executes that transaction once.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from datetime import UTC, datetime
from enum import StrEnum
import hashlib
import math
from types import MappingProxyType
from typing import Mapping, Protocol

from trustforge.preview_admission_compiler import (
    AdmissionHandle,
    IDENTITY_DAY_CAP,
    IDENTITY_MINUTE_CAP,
    MAX_DAY_MICRO_USD,
    MAX_DAY_TOKENS,
    MAX_MINUTE_MICRO_USD,
    MAX_MINUTE_TOKENS,
    RETENTION_SECONDS,
)
from trustforge.preview_admission_store import (
    CircuitSnapshot,
    CircuitState,
    MAX_DDB_INTEGER,
    MAX_EPOCH_SECOND,
    SCHEMA_VERSION,
    circuit_key,
    decode_circuit_item,
    global_concurrency_key,
    global_token_day_key,
    global_token_minute_key,
    global_usd_day_key,
    global_usd_minute_key,
    identity_concurrency_key,
    identity_day_key,
    identity_minute_key,
    reservation_key,
    validate_interval,
)
from trustforge.preview_trusted_clock import TrustedUtcInterval


class TerminalDisposition(StrEnum):
    PRE_PROVIDER_ABORT = "pre_provider_abort"
    KNOWN_SUCCESS = "known_success"
    KNOWN_FAILURE = "known_failure"
    UNCERTAIN = "uncertain"


class TerminalOutcome(StrEnum):
    RECONCILED = "reconciled"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class TerminalIntent:
    handle: AdmissionHandle = dataclass_field(repr=False)
    interval: TrustedUtcInterval
    disposition: TerminalDisposition
    actual_tokens: int | None = None
    actual_micro_usd: int | None = None

    def __post_init__(self) -> None:
        try:
            self.handle.__post_init__()
            validate_interval(self.interval)
        except (TypeError, ValueError):
            raise ValueError("invalid terminal intent") from None
        known = self.disposition in {
            TerminalDisposition.KNOWN_SUCCESS,
            TerminalDisposition.KNOWN_FAILURE,
        }
        if (
            type(self.handle) is not AdmissionHandle
            or type(self.disposition) is not TerminalDisposition
            # The whole terminal clock interval must be provably after the
            # admission interval's conservative upper whole-second bound.
            or self.interval.earliest < self.handle.created_upper
            or known
            != (
                type(self.actual_tokens) is int
                and type(self.actual_micro_usd) is int
            )
            or (
                known
                and (
                    not 0 <= self.actual_tokens <= self.handle.reserved_tokens  # type: ignore[operator]
                    or not 0
                    <= self.actual_micro_usd
                    <= self.handle.reserved_micro_usd  # type: ignore[operator]
                )
            )
            or (
                not known
                and (
                    self.actual_tokens is not None
                    or self.actual_micro_usd is not None
                )
            )
        ):
            raise ValueError("invalid terminal intent")


@dataclass(frozen=True, slots=True)
class TerminalExecutionResult:
    outcome: TerminalOutcome
    replay: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.outcome) is not TerminalOutcome
            or type(self.replay) is not bool
            or (self.outcome is TerminalOutcome.UNAVAILABLE and self.replay)
        ):
            raise ValueError("invalid terminal result")


@dataclass(frozen=True, slots=True)
class _Counter:
    key: Mapping[str, str]
    kind: str
    cap: int
    decrement: int
    version: int
    value: int
    ttl: int
    expected_ttl: int
    rolling_ttl: bool
    terminal_replay: bool

    def __post_init__(self) -> None:
        copied = dict(self.key)
        if (
            set(copied) != {"pk", "sk"}
            or not all(type(value) is str and value for value in copied.values())
            or type(self.kind) is not str
            or not self.kind.startswith("preview_")
            or type(self.cap) is not int
            or type(self.decrement) is not int
            or type(self.version) is not int
            or type(self.value) is not int
            or type(self.ttl) is not int
            or type(self.expected_ttl) is not int
            or type(self.rolling_ttl) is not bool
            or type(self.terminal_replay) is not bool
            or not 0 <= self.decrement <= self.value <= self.cap
            or not 0 <= self.version < MAX_DDB_INTEGER
            or not 1 <= self.ttl <= MAX_EPOCH_SECOND
            or not 1 <= self.expected_ttl <= MAX_EPOCH_SECOND
            or (
                self.ttl != self.expected_ttl
                and not (
                    self.terminal_replay
                    and self.rolling_ttl
                    and self.ttl > self.expected_ttl
                )
            )
        ):
            raise ValueError("malformed terminal counter")
        object.__setattr__(self, "key", MappingProxyType(copied))


@dataclass(frozen=True, slots=True)
class _Snapshot:
    terminal_replay: bool
    reservation: Mapping[str, object]
    counters: tuple[_Counter, ...]
    circuit: CircuitSnapshot


@dataclass(frozen=True, slots=True)
class CompiledTerminalPlan:
    write_request: Mapping[str, object]
    replay: bool

    def transact_write_items_request(self) -> dict[str, object]:
        return _thaw(self.write_request)  # type: ignore[return-value]


class DynamoTerminalClient(Protocol):
    def transact_get_items(self, **kwargs: object) -> object: ...
    def transact_write_items(self, **kwargs: object) -> object: ...


_UNAVAILABLE = TerminalExecutionResult(TerminalOutcome.UNAVAILABLE)


class PreviewTerminalReconciler:
    """Perform one read and at most one atomic terminal write."""

    def __init__(self, client: DynamoTerminalClient, table_name: str) -> None:
        if not callable(getattr(client, "transact_get_items", None)) or not callable(
            getattr(client, "transact_write_items", None)
        ):
            raise ValueError("invalid terminal client")
        _table(table_name)
        self._client = client
        self._table_name = table_name

    @classmethod
    def from_boto3(
        cls, table_name: str, *, region_name: str | None = None
    ) -> "PreviewTerminalReconciler":
        """Create a low-level client with SDK retries disabled."""

        import boto3
        from botocore.config import Config

        client = boto3.client(
            "dynamodb",
            region_name=region_name,
            config=Config(retries={"total_max_attempts": 1, "mode": "standard"}),
        )
        return cls(client, table_name)

    def reconcile(self, intent: TerminalIntent) -> TerminalExecutionResult:
        try:
            request = build_terminal_read_request(intent, self._table_name)
            response = self._client.transact_get_items(**request)
            if type(response) is not dict or "Responses" not in response:
                return _UNAVAILABLE
            snapshot = decode_terminal_responses(intent, response["Responses"])
            plan = compile_terminal(intent, self._table_name, snapshot)
            if plan.replay:
                return TerminalExecutionResult(TerminalOutcome.RECONCILED, replay=True)
            write = plan.transact_write_items_request()
            # A distinct deterministic token prevents admission/reconcile token reuse.
            write["ClientRequestToken"] = _terminal_client_token(
                intent.handle.reservation_id
            )
            try:
                written = self._client.transact_write_items(**write)
            except Exception:  # noqa: BLE001 - prove exact commit before success
                return (
                    TerminalExecutionResult(TerminalOutcome.RECONCILED, replay=True)
                    if self._prove_replay(intent)
                    else _UNAVAILABLE
                )
            if not _confirmed_success(written):
                return (
                    TerminalExecutionResult(TerminalOutcome.RECONCILED, replay=True)
                    if self._prove_replay(intent)
                    else _UNAVAILABLE
                )
            return TerminalExecutionResult(TerminalOutcome.RECONCILED)
        except Exception:  # noqa: BLE001 - every malformed/race/backend path is closed
            return (
                TerminalExecutionResult(TerminalOutcome.RECONCILED, replay=True)
                if self._prove_replay(intent)
                else _UNAVAILABLE
            )

    def _prove_replay(self, intent: TerminalIntent) -> bool:
        """Strong transaction read may prove an ambiguous write committed exactly."""

        try:
            response = self._client.transact_get_items(
                **build_terminal_read_request(intent, self._table_name)
            )
            return (
                type(response) is dict
                and "Responses" in response
                and decode_terminal_responses(
                    intent, response["Responses"]
                ).terminal_replay
            )
        except Exception:  # noqa: BLE001 - absence of proof stays unavailable
            return False


def build_terminal_read_request(
    intent: TerminalIntent, table_name: str
) -> dict[str, object]:
    intent.__post_init__()
    table = _table(table_name)
    keys = [
        reservation_key(
            intent.handle.key_version,
            intent.handle.expiry_shard,
            intent.handle.reservation_id,
        ),
        *[entry[0] for entry in _counter_layout(intent)],
        circuit_key(intent.handle.key_version, intent.handle.policy_digest),
    ]
    if len({(key["pk"], key["sk"]) for key in keys}) != len(keys):
        raise ValueError("terminal key collision")
    return {
        "TransactItems": [
            {
                "Get": {
                    "TableName": table,
                    "Key": _ddb_map(key),
                }
            }
            for key in keys
        ]
    }


def decode_terminal_responses(
    intent: TerminalIntent, responses: object
) -> _Snapshot:
    intent.__post_init__()
    layout = _counter_layout(intent)
    if type(responses) is not list or len(responses) != len(layout) + 2:
        raise ValueError("terminal response mismatch")
    reservation_response = responses[0]
    if (
        type(reservation_response) is not dict
        or set(reservation_response) != {"Item"}
        or type(reservation_response["Item"]) is not dict
    ):
        raise ValueError("missing terminal reservation")
    reservation = _decode_ddb_map(reservation_response["Item"])
    replay = _decode_reservation(intent, reservation)
    counters: list[_Counter] = []
    for response, (key, kind, cap, decrement, expected_ttl, rolling_ttl) in zip(
        responses[1:-1], layout, strict=True
    ):
        if (
            type(response) is not dict
            or set(response) != {"Item"}
            or type(response["Item"]) is not dict
        ):
            raise ValueError("missing terminal counter")
        item = _decode_ddb_map(response["Item"])
        expected = {"pk", "sk", "kind", "schema_version", "version", "value", "ttl"}
        if (
            set(item) != expected
            or item["pk"] != key["pk"]
            or item["sk"] != key["sk"]
            or item["kind"] != kind
            or item["schema_version"] != SCHEMA_VERSION
        ):
            raise ValueError("malformed terminal counter")
        counters.append(
            _Counter(
                key,
                kind,
                cap,
                0 if replay else decrement,
                _integer(item["version"], 0, MAX_DDB_INTEGER - 1),
                _integer(item["value"], 0, cap),
                _integer(item["ttl"], 1, MAX_EPOCH_SECOND),
                expected_ttl,
                rolling_ttl,
                replay,
            )
        )
    circuit_response = responses[-1]
    if (
        type(circuit_response) is not dict
        or set(circuit_response) != {"Item"}
        or type(circuit_response["Item"]) is not dict
    ):
        raise ValueError("missing terminal circuit")
    expected_circuit = circuit_key(
        intent.handle.key_version, intent.handle.policy_digest
    )
    circuit = decode_circuit_item(
        _decode_ddb_map(circuit_response["Item"]), expected_circuit
    )
    return _Snapshot(replay, MappingProxyType(dict(reservation)), tuple(counters), circuit)


def compile_terminal(
    intent: TerminalIntent, table_name: str, snapshot: _Snapshot
) -> CompiledTerminalPlan:
    intent.__post_init__()
    table = _table(table_name)
    if type(snapshot) is not _Snapshot:
        raise ValueError("invalid terminal snapshot")
    if snapshot.terminal_replay:
        return CompiledTerminalPlan(MappingProxyType({}), True)
    actions: list[dict[str, object]] = []
    terminal_item = _terminal_item(intent, snapshot.reservation)
    actions.append(
        _put_with_exact_previous(
            table, terminal_item, snapshot.reservation, "reservation"
        )
    )
    for counter in snapshot.counters:
        if counter.decrement:
            item = {
                **dict(counter.key),
                "kind": counter.kind,
                "schema_version": SCHEMA_VERSION,
                "version": counter.version + 1,
                "value": counter.value - counter.decrement,
                "ttl": counter.ttl,
            }
            previous = {
                **dict(counter.key),
                "kind": counter.kind,
                "schema_version": SCHEMA_VERSION,
                "version": counter.version,
                "value": counter.value,
                "ttl": counter.ttl,
            }
            actions.append(_put_with_exact_previous(table, item, previous, "counter"))
    next_circuit = _next_circuit(intent, snapshot.circuit)
    if next_circuit is not None:
        actions.append(
            _put_with_exact_previous(
                table,
                _circuit_item(next_circuit),
                _circuit_item(snapshot.circuit),
                "circuit",
            )
        )
    if not 2 <= len(actions) <= 100:
        raise ValueError("invalid terminal action set")
    keys = [
        _action_key(action)
        for action in actions
    ]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate terminal action")
    return CompiledTerminalPlan(
        _freeze({"TransactItems": [_serialize_action(action) for action in actions]}),
        False,
    )


def _counter_layout(
    intent: TerminalIntent,
) -> tuple[tuple[dict[str, str], str, int, int, int, bool], ...]:
    handle = intent.handle
    abort = intent.disposition is TerminalDisposition.PRE_PROVIDER_ABORT
    known = intent.disposition in {
        TerminalDisposition.KNOWN_SUCCESS,
        TerminalDisposition.KNOWN_FAILURE,
    }
    token_refund = (
        handle.reserved_tokens
        if abort
        else handle.reserved_tokens - intent.actual_tokens  # type: ignore[operator]
        if known
        else 0
    )
    usd_refund = (
        handle.reserved_micro_usd
        if abort
        else handle.reserved_micro_usd - intent.actual_micro_usd  # type: ignore[operator]
        if known
        else 0
    )
    minute_ttl = handle.epoch_minute * 60 + RETENTION_SECONDS
    day_ttl = int(
        datetime.strptime(handle.utc_day, "%Y%m%d")
        .replace(tzinfo=UTC)
        .timestamp()
    ) + RETENTION_SECONDS
    concurrency_ttl = handle.created_upper + RETENTION_SECONDS
    rows: list[tuple[dict[str, str], str, int, int, int, bool]] = []
    for digest in (handle.identity_digest, handle.previous_identity_digest):
        if digest is None:
            continue
        rows.extend(
            (
                (
                    identity_minute_key(handle.key_version, digest, handle.epoch_minute),
                    "preview_identity_minute",
                    IDENTITY_MINUTE_CAP,
                    1 if abort else 0,
                    minute_ttl,
                    False,
                ),
                (
                    identity_day_key(handle.key_version, digest, handle.utc_day),
                    "preview_identity_day",
                    IDENTITY_DAY_CAP,
                    1 if abort else 0,
                    day_ttl,
                    False,
                ),
                (
                    identity_concurrency_key(handle.key_version, digest),
                    "preview_identity_concurrency",
                    1,
                    1,
                    concurrency_ttl,
                    True,
                ),
            )
        )
    rows.extend(
        (
            (
                global_concurrency_key(handle.key_version),
                "preview_global_concurrency",
                4,
                1,
                concurrency_ttl,
                True,
            ),
            (
                global_token_minute_key(handle.key_version, handle.epoch_minute),
                "preview_global_token_minute",
                MAX_MINUTE_TOKENS,
                token_refund,
                minute_ttl,
                False,
            ),
            (
                global_token_day_key(handle.key_version, handle.utc_day),
                "preview_global_token_day",
                MAX_DAY_TOKENS,
                token_refund,
                day_ttl,
                False,
            ),
            (
                global_usd_minute_key(handle.key_version, handle.epoch_minute),
                "preview_global_usd_minute",
                MAX_MINUTE_MICRO_USD,
                usd_refund,
                minute_ttl,
                False,
            ),
            (
                global_usd_day_key(handle.key_version, handle.utc_day),
                "preview_global_usd_day",
                MAX_DAY_MICRO_USD,
                usd_refund,
                day_ttl,
                False,
            ),
        )
    )
    return tuple(rows)


def _decode_reservation(intent: TerminalIntent, item: Mapping[str, object]) -> bool:
    handle = intent.handle
    base = {
        **reservation_key(handle.key_version, handle.expiry_shard, handle.reservation_id),
        "kind": "preview_reservation",
        "reservation_id": handle.reservation_id,
        "owner_digest": handle.owner_digest,
        "identity_digest": handle.identity_digest,
        "epoch_minute": handle.epoch_minute,
        "utc_day": handle.utc_day,
        "reserved_tokens": handle.reserved_tokens,
        "reserved_micro_usd": handle.reserved_micro_usd,
        "created_lower": handle.created_lower,
        "created_upper": handle.created_upper,
        "lease_until": handle.lease_until,
        "expiry_shard": handle.expiry_shard,
        "policy_digest": handle.policy_digest,
        "policy_version": handle.policy_version,
        "key_version": handle.key_version,
        "schema_version": handle.schema_version,
        "ttl": handle.created_upper + RETENTION_SECONDS,
    }
    if handle.previous_identity_digest is not None:
        base["previous_identity_digest"] = handle.previous_identity_digest
    if handle.circuit_half_open_owner is not None:
        base["circuit_half_open_owner"] = handle.circuit_half_open_owner
    reserved = {**base, "status": "reserved", "version": 0}
    if dict(item) == reserved:
        return False
    expected = _terminal_item(intent, reserved)
    if dict(item) == expected:
        return True
    raise ValueError("conflicting terminal reservation")


def _terminal_item(
    intent: TerminalIntent, reservation: Mapping[str, object]
) -> dict[str, object]:
    item = dict(reservation)
    item["status"] = "terminal"
    item["version"] = 1
    item["terminal_disposition"] = intent.disposition.value
    if intent.actual_tokens is not None:
        item["actual_tokens"] = intent.actual_tokens
        item["actual_micro_usd"] = intent.actual_micro_usd
    return item


def _next_circuit(
    intent: TerminalIntent, current: CircuitSnapshot
) -> CircuitSnapshot | None:
    handle = intent.handle
    if current.version is None or current.state is None:
        raise ValueError("absent terminal circuit")
    if current.version >= MAX_DDB_INTEGER:
        raise ValueError("circuit version exhausted")
    half_open = handle.circuit_half_open_owner is not None
    if half_open != (current.state is CircuitState.HALF_OPEN):
        raise ValueError("circuit reservation mismatch")
    if half_open and (
        current.owner != handle.owner_digest
        or handle.circuit_half_open_owner != handle.owner_digest
    ):
        raise ValueError("wrong circuit owner")
    if intent.disposition is TerminalDisposition.KNOWN_SUCCESS:
        if not half_open:
            return None
        return CircuitSnapshot(
            current.pk, current.sk, CircuitState.CLOSED, current.version + 1, ()
        )
    if intent.disposition is TerminalDisposition.PRE_PROVIDER_ABORT and not half_open:
        return None
    # Confirmed provider failure and uncertain/aborted probes use the same safe
    # failure transition. Uncertain usage never receives a refund.
    timestamp = math.ceil(validate_interval(intent.interval).latest)
    if current.failures and timestamp < current.failures[-1]:
        raise ValueError("terminal circuit time regression")
    if current.state is CircuitState.HALF_OPEN:
        return CircuitSnapshot(
            current.pk,
            current.sk,
            CircuitState.OPEN,
            current.version + 1,
            current.failures,
            open_until=min(timestamp + 120, MAX_EPOCH_SECOND),
        )
    if current.state is not CircuitState.CLOSED:
        raise ValueError("invalid terminal circuit state")
    cutoff = math.floor(intent.interval.earliest) - 60
    failures = tuple(value for value in current.failures if value >= cutoff)
    failures = (*failures, timestamp)[-5:]
    opened = len(failures) >= 5
    return CircuitSnapshot(
        current.pk,
        current.sk,
        CircuitState.OPEN if opened else CircuitState.CLOSED,
        current.version + 1,
        failures,
        open_until=min(timestamp + 120, MAX_EPOCH_SECOND) if opened else None,
    )


def _circuit_item(snapshot: CircuitSnapshot) -> dict[str, object]:
    if snapshot.state is None or snapshot.version is None:
        raise ValueError("absent circuit")
    item: dict[str, object] = {
        "pk": snapshot.pk,
        "sk": snapshot.sk,
        "kind": "preview_circuit",
        "schema_version": SCHEMA_VERSION,
        "state": snapshot.state.value,
        "version": snapshot.version,
        "failures": list(snapshot.failures),
    }
    if snapshot.state is CircuitState.OPEN:
        item["open_until"] = snapshot.open_until
    elif snapshot.state is CircuitState.HALF_OPEN:
        item["owner"] = snapshot.owner
        item["lease_until"] = snapshot.lease_until
    return item


def _put_with_exact_previous(
    table: str,
    item: Mapping[str, object],
    previous: Mapping[str, object],
    prefix: str,
) -> dict[str, object]:
    names = {f"#{prefix}{index}": key for index, key in enumerate(previous)}
    values = {f":{prefix}{index}": value for index, value in enumerate(previous.values())}
    condition = " AND ".join(
        f"{name}={value}"
        for name, value in zip(names, values, strict=True)
    )
    return {
        "Put": {
            "TableName": table,
            "Item": dict(item),
            "ConditionExpression": condition,
            "ExpressionAttributeNames": names,
            "ExpressionAttributeValues": values,
        }
    }


def _serialize_action(action: Mapping[str, object]) -> dict[str, object]:
    raw = dict(action["Put"])  # type: ignore[arg-type]
    raw["Item"] = _ddb_map(raw["Item"])  # type: ignore[arg-type]
    raw["ExpressionAttributeValues"] = _ddb_map(
        raw["ExpressionAttributeValues"]  # type: ignore[arg-type]
    )
    return {"Put": raw}


def _action_key(action: Mapping[str, object]) -> tuple[str, str]:
    item = action["Put"]["Item"]  # type: ignore[index]
    return item["pk"], item["sk"]  # type: ignore[index]


def _table(value: object) -> str:
    import re

    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_.-]{3,255}", value):
        raise ValueError("invalid table name")
    return value


def _integer(value: object, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("invalid terminal integer")
    return value


def _ddb_map(value: Mapping[str, object]) -> dict[str, object]:
    return {key: _ddb_value(item) for key, item in value.items()}


def _ddb_value(value: object) -> dict[str, object]:
    if type(value) is str:
        return {"S": value}
    if type(value) is int:
        return {"N": str(value)}
    if type(value) in (list, tuple):
        return {"L": [_ddb_value(item) for item in value]}
    raise ValueError("unsupported terminal value")


def _decode_ddb_map(value: Mapping[str, object]) -> dict[str, object]:
    if type(value) is not dict or not all(type(key) is str for key in value):
        raise ValueError("malformed terminal map")
    return {key: _decode_ddb_value(item) for key, item in value.items()}


def _decode_ddb_value(value: object) -> object:
    if type(value) is not dict or len(value) != 1:
        raise ValueError("malformed terminal value")
    if set(value) == {"S"} and type(value["S"]) is str:
        return value["S"]
    if set(value) == {"N"} and type(value["N"]) is str:
        raw = value["N"]
        if not raw or not raw.isascii() or not raw.isdecimal() or str(int(raw)) != raw:
            raise ValueError("malformed terminal value")
        return int(raw)
    if set(value) == {"L"} and type(value["L"]) is list:
        return [_decode_ddb_value(item) for item in value["L"]]
    raise ValueError("malformed terminal value")


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


def _terminal_client_token(reservation_id: str) -> str:
    """Return a 36-byte token derived from the complete canonical UUID."""

    # AdmissionHandle is the canonical UUID validation authority.  This helper
    # is deliberately private and receives only a handle-validated value.
    material = b"preview-terminal-v1\0" + reservation_id.encode("ascii")
    return hashlib.sha256(material).hexdigest()[:36]


def _freeze(value: object) -> object:
    if type(value) is dict:
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_thaw(item) for item in value]
    return value
