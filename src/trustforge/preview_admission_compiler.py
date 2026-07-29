"""Pure, fail-closed compiler for paid-preview admission transactions.

This module deliberately has no DynamoDB client.  It validates a consistent
pre-read and emits an immutable, low-level ``TransactWriteItems`` request for
the executor owned by #983.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
import json
import math
from types import MappingProxyType
from typing import Mapping

from trustforge.preview_admission_store import (
    CircuitAdmissionAction,
    CircuitDisposition,
    CircuitSnapshot,
    CircuitState,
    KEY_VERSION,
    MAX_DDB_INTEGER,
    MAX_EPOCH_MINUTE,
    MAX_EPOCH_SECOND,
    PreviewStoreUnavailable,
    SCHEMA_VERSION,
    build_admission_action,
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
    ttl_from_interval,
    validate_interval,
)
from trustforge.preview_trusted_clock import TrustedBuckets, TrustedUtcInterval


MAX_RESERVED_TOKENS = 2_560
MAX_MINUTE_TOKENS = 8_000
MAX_DAY_TOKENS = 51_200
MAX_MINUTE_MICRO_USD = 50_000
MAX_DAY_MICRO_USD = 500_000
IDENTITY_MINUTE_CAP = 3
IDENTITY_DAY_CAP = 20
IDENTITY_CONCURRENCY_CAP = 1
GLOBAL_CONCURRENCY_CAP = 4
RESERVATION_LEASE_SECONDS = 15
RETENTION_SECONDS = 7 * 86_400
MAX_TRANSACTION_BYTES = 256 * 1024
_PLAN_FACTORY_TOKEN = object()


class AdmissionDeniedReason(StrEnum):
    IDENTITY_MINUTE = "identity_minute"
    IDENTITY_DAY = "identity_day"
    IDENTITY_CONCURRENCY = "identity_concurrency"
    GLOBAL_CONCURRENCY = "global_concurrency"
    GLOBAL_TOKEN_MINUTE = "global_token_minute"
    GLOBAL_TOKEN_DAY = "global_token_day"
    GLOBAL_USD_MINUTE = "global_usd_minute"
    GLOBAL_USD_DAY = "global_usd_day"
    CIRCUIT_OPEN = "circuit_open"


class AdmissionCompileDenied(RuntimeError):
    def __init__(self, reason: AdmissionDeniedReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


@dataclass(frozen=True, slots=True)
class CounterSpec:
    key: Mapping[str, str]
    kind: str
    increment: int
    cap: int
    ttl: int
    denied_reason: AdmissionDeniedReason
    fixed_ttl: bool

    def __post_init__(self) -> None:
        copied = _strict_key(self.key)
        if (
            type(self.kind) is not str
            or not self.kind.startswith("preview_")
            or type(self.increment) is not int
            or type(self.cap) is not int
            or self.cap < 1
            or not 1 <= self.increment <= self.cap
            or type(self.ttl) is not int
            or not 1 <= self.ttl <= MAX_EPOCH_SECOND
            or type(self.denied_reason) is not AdmissionDeniedReason
            or type(self.fixed_ttl) is not bool
        ):
            raise ValueError("invalid counter specification")
        object.__setattr__(self, "key", MappingProxyType(copied))


@dataclass(frozen=True, slots=True)
class CounterSnapshot:
    spec: CounterSpec
    version: int | None
    value: int
    ttl: int

    def __post_init__(self) -> None:
        if (
            type(self.spec) is not CounterSpec
            or type(self.value) is not int
            or not 0 <= self.value <= self.spec.cap
            or type(self.ttl) is not int
            or not 1 <= self.ttl <= MAX_EPOCH_SECOND
        ):
            raise ValueError("invalid counter snapshot")
        if self.version is None:
            if self.value != 0 or self.ttl != self.spec.ttl:
                raise ValueError("invalid absent counter snapshot")
        elif (
            type(self.version) is not int
            or not 0 <= self.version <= MAX_DDB_INTEGER
            or (self.spec.fixed_ttl and self.ttl != self.spec.ttl)
            or (not self.spec.fixed_ttl and self.ttl > self.spec.ttl)
        ):
            raise ValueError("invalid present counter snapshot")

    @property
    def absent(self) -> bool:
        return self.version is None


@dataclass(frozen=True, slots=True)
class AdmissionHandle:
    reservation_id: str
    owner_digest: str
    identity_digest: str
    previous_identity_digest: str | None
    epoch_minute: int
    utc_day: str
    reserved_tokens: int
    reserved_micro_usd: int
    created_lower: int
    created_upper: int
    lease_until: int
    expiry_shard: int
    policy_digest: str
    circuit_half_open_owner: str | None
    policy_version: int
    key_version: int
    schema_version: int
    lifecycle_generation: int
    current_quota_key_version: int
    previous_quota_key_version: int | None

    def __post_init__(self) -> None:
        if (
            not _uuid4(self.reservation_id)
            or not _digest(self.owner_digest)
            or not _digest(self.identity_digest)
            or (
                self.previous_identity_digest is not None
                and (
                    not _digest(self.previous_identity_digest)
                    or self.previous_identity_digest == self.identity_digest
                )
            )
            or type(self.epoch_minute) is not int
            or not 0 <= self.epoch_minute <= MAX_EPOCH_MINUTE
            or type(self.utc_day) is not str
            or type(self.reserved_tokens) is not int
            or not 1 <= self.reserved_tokens <= MAX_RESERVED_TOKENS
            or type(self.reserved_micro_usd) is not int
            or not 1 <= self.reserved_micro_usd <= MAX_MINUTE_MICRO_USD
            or type(self.created_lower) is not int
            or type(self.created_upper) is not int
            or not 0 <= self.created_lower <= self.created_upper <= MAX_EPOCH_SECOND
            or self.created_lower // 60 != self.epoch_minute
            or self.created_upper > (self.epoch_minute + 1) * 60
            or _utc_day(self.created_lower) != self.utc_day
            or (
                self.created_upper < (self.epoch_minute + 1) * 60
                and _utc_day(self.created_upper) != self.utc_day
            )
            or self.created_upper + RETENTION_SECONDS > MAX_EPOCH_SECOND
            or type(self.lease_until) is not int
            or self.lease_until != self.created_upper + RESERVATION_LEASE_SECONDS
            or self.lease_until > MAX_EPOCH_SECOND
            or type(self.expiry_shard) is not int
            or self.expiry_shard != self.lease_until // 60
            or not _digest(self.policy_digest)
            or (
                self.circuit_half_open_owner is not None
                and (
                    not _digest(self.circuit_half_open_owner)
                    or self.circuit_half_open_owner != self.owner_digest
                )
            )
            or type(self.policy_version) is not int
            or self.policy_version != 1
            or type(self.key_version) is not int
            or self.key_version != 1
            or type(self.schema_version) is not int
            or self.schema_version != 1
            or type(self.lifecycle_generation) is not int
            or self.lifecycle_generation < 1
            or type(self.current_quota_key_version) is not int
            or self.current_quota_key_version < 1
            or (self.previous_identity_digest is None)
            != (self.previous_quota_key_version is None)
            or (
                self.previous_quota_key_version is not None
                and (
                    type(self.previous_quota_key_version) is not int
                    or self.previous_quota_key_version + 1
                    != self.current_quota_key_version
                )
            )
        ):
            raise ValueError("invalid admission handle")
        try:
            datetime.strptime(self.utc_day, "%Y%m%d")
        except ValueError:
            raise ValueError("invalid admission handle") from None


@dataclass(frozen=True, slots=True)
class AdmissionCompileRequest:
    interval: TrustedUtcInterval
    buckets: TrustedBuckets
    policy_digest: str
    owner_digest: str
    identity_digest: str
    previous_identity_digest: str | None
    reservation_id: str
    reserved_tokens: int
    reserved_micro_usd: int
    lifecycle_generation: int
    current_quota_key_version: int
    previous_quota_key_version: int | None
    policy_version: int = 1
    key_version: int = KEY_VERSION
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        try:
            interval = validate_interval(self.interval)
        except (TypeError, ValueError, PreviewStoreUnavailable):
            raise ValueError("invalid admission compile request") from None
        if (
            type(self.buckets) is not TrustedBuckets
            or type(self.buckets.epoch_minute) is not int
            or type(self.buckets.utc_day) is not str
            or self.buckets.epoch_minute != math.floor(interval.earliest / 60)
            or self.buckets.epoch_minute != math.floor(interval.latest / 60)
            or self.buckets.utc_day != _utc_day(interval.earliest)
            or self.buckets.utc_day != _utc_day(interval.latest)
            or not _digest(self.policy_digest)
            or not _digest(self.owner_digest)
            or not _digest(self.identity_digest)
            or (
                self.previous_identity_digest is not None
                and (
                    not _digest(self.previous_identity_digest)
                    or self.previous_identity_digest == self.identity_digest
                )
            )
            or not _uuid4(self.reservation_id)
            or type(self.reserved_tokens) is not int
            or not 1 <= self.reserved_tokens <= MAX_RESERVED_TOKENS
            or type(self.reserved_micro_usd) is not int
            or not 1 <= self.reserved_micro_usd <= min(
                MAX_MINUTE_MICRO_USD, MAX_DAY_MICRO_USD
            )
            or type(self.lifecycle_generation) is not int
            or self.lifecycle_generation < 1
            or type(self.current_quota_key_version) is not int
            or self.current_quota_key_version < 1
            or (self.previous_identity_digest is None)
            != (self.previous_quota_key_version is None)
            or (
                self.previous_quota_key_version is not None
                and (
                    type(self.previous_quota_key_version) is not int
                    or self.previous_quota_key_version + 1
                    != self.current_quota_key_version
                )
            )
            or type(self.policy_version) is not int
            or self.policy_version != 1
            or type(self.key_version) is not int
            or self.key_version != KEY_VERSION
            or type(self.schema_version) is not int
            or self.schema_version != SCHEMA_VERSION
        ):
            raise ValueError("invalid admission compile request")


@dataclass(frozen=True, slots=True)
class AdmissionSnapshots:
    circuit: CircuitSnapshot
    counters: tuple[CounterSnapshot, ...]

    def __post_init__(self) -> None:
        if (
            type(self.circuit) is not CircuitSnapshot
            or type(self.counters) is not tuple
            or self.circuit.pk != "PAP#1#CIRCUIT"
            or not self.circuit.sk.startswith("POLICY#")
            or len(self.circuit.sk) != len("POLICY#") + 64
            or not _digest(self.circuit.sk.removeprefix("POLICY#"))
            or not all(type(counter) is CounterSnapshot for counter in self.counters)
        ):
            raise ValueError("invalid admission snapshots")


@dataclass(frozen=True, slots=True, init=False)
class CompiledAdmissionPlan:
    read_request: Mapping[str, object]
    write_request: Mapping[str, object]
    handle: AdmissionHandle
    action_count: int
    estimated_bytes: int

    @classmethod
    def _create(
        cls,
        factory_token: object,
        request: AdmissionCompileRequest,
        snapshots: AdmissionSnapshots,
        table_name: str,
        read_request: Mapping[str, object],
        write_request: Mapping[str, object],
        handle: AdmissionHandle,
        action_count: int,
        estimated_bytes: int,
    ) -> "CompiledAdmissionPlan":
        if (
            factory_token is not _PLAN_FACTORY_TOKEN
            or
            type(handle) is not AdmissionHandle
            or action_count not in (10, 13)
            or not 0 < estimated_bytes < MAX_TRANSACTION_BYTES
        ):
            raise ValueError("invalid compiled admission plan")
        try:
            handle.__post_init__()
            (
                expected_read,
                expected_write,
                expected_handle,
                expected_count,
                expected_size,
            ) = _compile_components(request, table_name, snapshots)
            if (
                read_request != expected_read
                or write_request != expected_write
                or handle != expected_handle
                or action_count != expected_count
                or estimated_bytes != expected_size
            ):
                raise ValueError
            actions = write_request["TransactItems"]
            if type(actions) is not list:
                raise ValueError
            # Keep explicit reservation parity at the final trust boundary.
            expected_reservation = _serialize_action(
                _reservation_put(handle, _table(table_name))
            )
            if actions[-1] != expected_reservation:
                raise ValueError
        except (KeyError, TypeError, IndexError, ValueError):
            raise ValueError("invalid compiled admission plan") from None
        instance = object.__new__(cls)
        object.__setattr__(instance, "read_request", _freeze(read_request))
        object.__setattr__(instance, "write_request", _freeze(write_request))
        object.__setattr__(instance, "handle", handle)
        object.__setattr__(instance, "action_count", action_count)
        object.__setattr__(instance, "estimated_bytes", estimated_bytes)
        return instance

    def transact_get_items_request(self) -> dict[str, object]:
        """Return a defensive, SDK-ready copy without performing any I/O."""

        return _thaw(self.read_request)  # type: ignore[return-value]

    def transact_write_items_request(self) -> dict[str, object]:
        """Return a defensive, SDK-ready copy without performing any I/O."""

        return _thaw(self.write_request)  # type: ignore[return-value]


def build_counter_specs(request: AdmissionCompileRequest) -> tuple[CounterSpec, ...]:
    """Return deterministic counters with bucket-canonical cleanup TTLs."""

    request = _request(request)
    requested_ttl = ttl_from_interval(request.interval, RETENTION_SECONDS)
    minute_ttl = request.buckets.epoch_minute * 60 + RETENTION_SECONDS
    day_start = int(
        datetime.strptime(request.buckets.utc_day, "%Y%m%d")
        .replace(tzinfo=UTC)
        .timestamp()
    )
    day_ttl = day_start + RETENTION_SECONDS
    specs: list[CounterSpec] = []
    for digest in (request.identity_digest, request.previous_identity_digest):
        if digest is None:
            continue
        specs.extend(
            (
                CounterSpec(identity_minute_key(1, digest, request.buckets.epoch_minute),
                            "preview_identity_minute", 1, IDENTITY_MINUTE_CAP,
                            minute_ttl, AdmissionDeniedReason.IDENTITY_MINUTE, True),
                CounterSpec(identity_day_key(1, digest, request.buckets.utc_day),
                            "preview_identity_day", 1, IDENTITY_DAY_CAP,
                            day_ttl, AdmissionDeniedReason.IDENTITY_DAY, True),
                CounterSpec(identity_concurrency_key(1, digest),
                            "preview_identity_concurrency", 1, IDENTITY_CONCURRENCY_CAP,
                            requested_ttl, AdmissionDeniedReason.IDENTITY_CONCURRENCY, False),
            )
        )
    specs.extend(
        (
            CounterSpec(global_concurrency_key(1), "preview_global_concurrency", 1,
                        GLOBAL_CONCURRENCY_CAP, requested_ttl,
                        AdmissionDeniedReason.GLOBAL_CONCURRENCY, False),
            CounterSpec(global_token_minute_key(1, request.buckets.epoch_minute),
                        "preview_global_token_minute", request.reserved_tokens,
                        MAX_MINUTE_TOKENS, minute_ttl,
                        AdmissionDeniedReason.GLOBAL_TOKEN_MINUTE, True),
            CounterSpec(global_token_day_key(1, request.buckets.utc_day),
                        "preview_global_token_day", request.reserved_tokens,
                        MAX_DAY_TOKENS, day_ttl,
                        AdmissionDeniedReason.GLOBAL_TOKEN_DAY, True),
            CounterSpec(global_usd_minute_key(1, request.buckets.epoch_minute),
                        "preview_global_usd_minute", request.reserved_micro_usd,
                        MAX_MINUTE_MICRO_USD, minute_ttl,
                        AdmissionDeniedReason.GLOBAL_USD_MINUTE, True),
            CounterSpec(global_usd_day_key(1, request.buckets.utc_day),
                        "preview_global_usd_day", request.reserved_micro_usd,
                        MAX_DAY_MICRO_USD, day_ttl,
                        AdmissionDeniedReason.GLOBAL_USD_DAY, True),
        )
    )
    keys = {(spec.key["pk"], spec.key["sk"]) for spec in specs}
    if len(keys) != len(specs):
        raise ValueError("counter key collision")
    return tuple(specs)


def build_transact_get_request(
    request: AdmissionCompileRequest, table_name: str
) -> dict[str, object]:
    specs = build_counter_specs(request)
    table = _table(table_name)
    return {
        "TransactItems": [
            {
                "Get": {
                    "TableName": table,
                    "Key": _ddb_map(circuit_key(request.key_version, request.policy_digest)),
                }
            },
            *[
                {"Get": {"TableName": table, "Key": _ddb_map(spec.key)}}
                for spec in specs
            ],
        ]
    }


def decode_counter_item(item: object, spec: CounterSpec) -> CounterSnapshot:
    if item is None:
        return CounterSnapshot(spec, None, 0, spec.ttl)
    if type(item) is not dict or set(item) != {
        "pk", "sk", "kind", "schema_version", "version", "value", "ttl"
    }:
        raise ValueError("malformed counter")
    if (
        item["pk"] != spec.key["pk"]
        or item["sk"] != spec.key["sk"]
        or item["kind"] != spec.kind
        or _integer(item["schema_version"], 1, 1) != SCHEMA_VERSION
        or (
            spec.fixed_ttl
            and _integer(item["ttl"], 1, MAX_EPOCH_SECOND) != spec.ttl
        )
    ):
        raise ValueError("malformed counter")
    version = _integer(item["version"], 0, MAX_DDB_INTEGER)
    value = _integer(item["value"], 0, spec.cap)
    ttl = _integer(item["ttl"], 1, MAX_EPOCH_SECOND)
    if not spec.fixed_ttl and ttl > spec.ttl:
        raise ValueError("malformed counter")
    return CounterSnapshot(spec, version, value, ttl)


def decode_transact_get_responses(
    request: AdmissionCompileRequest, responses: object
) -> AdmissionSnapshots:
    """Decode the exact low-level DynamoDB TransactGet ``Responses`` grammar."""

    specs = build_counter_specs(request)
    if type(responses) is not list or len(responses) != len(specs) + 1:
        raise ValueError("counter response mismatch")
    circuit_response = responses[0]
    expected_key = circuit_key(request.key_version, request.policy_digest)
    if type(circuit_response) is not dict:
        raise ValueError("malformed circuit response")
    if circuit_response == {}:
        circuit = CircuitSnapshot.absent(expected_key)
    elif (
        set(circuit_response) == {"Item"}
        and type(circuit_response["Item"]) is dict
    ):
        circuit = decode_circuit_item(
            _decode_ddb_map(circuit_response["Item"]), expected_key
        )
    else:
        raise ValueError("malformed circuit response")
    decoded: list[CounterSnapshot] = []
    for response, spec in zip(responses[1:], specs, strict=True):
        if type(response) is not dict:
            raise ValueError("malformed counter response")
        if response == {}:
            decoded.append(CounterSnapshot(spec, None, 0, spec.ttl))
            continue
        if set(response) != {"Item"} or type(response["Item"]) is not dict:
            raise ValueError("malformed counter response")
        decoded.append(decode_counter_item(_decode_ddb_map(response["Item"]), spec))
    return AdmissionSnapshots(circuit, tuple(decoded))


def _compile_components(
    request: AdmissionCompileRequest,
    table_name: str,
    snapshots: AdmissionSnapshots,
) -> tuple[dict[str, object], dict[str, object], AdmissionHandle, int, int]:
    request = _request(request)
    specs = build_counter_specs(request)
    if type(snapshots) is not AdmissionSnapshots:
        raise ValueError("counter snapshot mismatch")
    counter_snapshots = snapshots.counters
    if (
        type(counter_snapshots) not in (tuple, list)
        or len(counter_snapshots) != len(specs)
    ):
        raise ValueError("counter snapshot mismatch")
    validated_counters: tuple[CounterSnapshot, ...] = tuple(counter_snapshots)
    for snapshot, spec in zip(validated_counters, specs, strict=True):
        if (
            type(snapshot) is not CounterSnapshot
            or snapshot.spec != spec
            or type(snapshot.value) is not int
            or not 0 <= snapshot.value <= spec.cap
            or type(snapshot.ttl) is not int
            or not 1 <= snapshot.ttl <= MAX_EPOCH_SECOND
            or (spec.fixed_ttl and snapshot.ttl != spec.ttl)
            or (not spec.fixed_ttl and snapshot.ttl > spec.ttl)
            or (
                snapshot.version is None
                and (snapshot.value != 0 or snapshot.ttl != spec.ttl)
            )
            or (
                snapshot.version is not None
                and (
                    type(snapshot.version) is not int
                    or not 0 <= snapshot.version <= MAX_DDB_INTEGER
                )
            )
        ):
            raise ValueError("counter snapshot mismatch")
    for snapshot in validated_counters:
        if snapshot.value + snapshot.spec.increment > snapshot.spec.cap:
            raise AdmissionCompileDenied(snapshot.spec.denied_reason)
        if snapshot.version == MAX_DDB_INTEGER:
            raise ValueError("counter version exhausted")
    expected_circuit_key = circuit_key(request.key_version, request.policy_digest)
    if snapshots.circuit.key != expected_circuit_key:
        raise ValueError("circuit snapshot mismatch")
    circuit = build_admission_action(
        snapshots.circuit, request.owner_digest, request.interval
    )
    if circuit.disposition is CircuitDisposition.DENY:
        raise AdmissionCompileDenied(AdmissionDeniedReason.CIRCUIT_OPEN)

    table = _table(table_name)
    native_actions = [_counter_put(snapshot, table) for snapshot in validated_counters]
    native_actions.append(circuit.transaction_item(table))
    handle = _build_handle(request, circuit)
    native_actions.append(_reservation_put(handle, table))
    keys = [_action_key(action) for action in native_actions]
    if len(set(keys)) != len(keys) or len(native_actions) not in (10, 13) or len(native_actions) > 100:
        raise ValueError("invalid admission action set")
    low_level = {"TransactItems": [_serialize_action(action) for action in native_actions]}
    estimated = len(json.dumps(low_level, sort_keys=True, separators=(",", ":")).encode())
    if estimated >= MAX_TRANSACTION_BYTES:
        raise ValueError("admission transaction too large")
    return (
        build_transact_get_request(request, table),
        low_level,
        handle,
        len(native_actions),
        estimated,
    )


def compile_admission(
    request: AdmissionCompileRequest,
    table_name: str,
    snapshots: AdmissionSnapshots,
) -> CompiledAdmissionPlan:
    components = _compile_components(request, table_name, snapshots)
    return CompiledAdmissionPlan._create(
        _PLAN_FACTORY_TOKEN, request, snapshots, table_name, *components
    )


def _counter_put(snapshot: CounterSnapshot, table: str) -> dict[str, object]:
    spec = snapshot.spec
    following = snapshot.value + spec.increment
    following_ttl = spec.ttl if spec.fixed_ttl else max(snapshot.ttl, spec.ttl)
    version = 0 if snapshot.version is None else snapshot.version + 1
    item = {
        **dict(spec.key), "kind": spec.kind, "schema_version": SCHEMA_VERSION,
        "version": version, "value": following, "ttl": following_ttl,
    }
    if snapshot.absent:
        condition = "attribute_not_exists(#pk) AND attribute_not_exists(#sk)"
        names = {"#pk": "pk", "#sk": "sk"}
        values: dict[str, object] = {}
    else:
        names = {
            "#pk": "pk", "#sk": "sk", "#kind": "kind",
            "#schema": "schema_version", "#version": "version",
            "#value": "value", "#ttl": "ttl",
        }
        condition = (
            "#pk=:pk AND #sk=:sk AND #kind=:kind AND #schema=:schema "
            "AND #version=:version AND #value=:value AND #ttl=:ttl"
        )
        values = {
            ":pk": spec.key["pk"], ":sk": spec.key["sk"], ":kind": spec.kind,
            ":schema": SCHEMA_VERSION, ":version": snapshot.version,
            ":value": snapshot.value, ":ttl": snapshot.ttl,
        }
    body: dict[str, object] = {
        "TableName": table, "Item": item, "ConditionExpression": condition,
        "ExpressionAttributeNames": names,
    }
    if values:
        body["ExpressionAttributeValues"] = values
    return {"Put": body}


def _build_handle(
    request: AdmissionCompileRequest, circuit: CircuitAdmissionAction
) -> AdmissionHandle:
    created_lower = math.floor(request.interval.earliest)
    created_upper = math.ceil(request.interval.latest)
    lease_until = created_upper + RESERVATION_LEASE_SECONDS
    if lease_until > MAX_EPOCH_SECOND:
        raise ValueError("reservation lease out of range")
    half_open_owner = None
    if (
        circuit.next_snapshot is not None
        and circuit.next_snapshot.state is CircuitState.HALF_OPEN
    ):
        half_open_owner = circuit.next_snapshot.owner
    return AdmissionHandle(
        request.reservation_id, request.owner_digest, request.identity_digest,
        request.previous_identity_digest, request.buckets.epoch_minute,
        request.buckets.utc_day, request.reserved_tokens,
        request.reserved_micro_usd, created_lower, created_upper, lease_until,
        lease_until // 60, request.policy_digest, half_open_owner, request.policy_version,
        request.key_version, request.schema_version,
        request.lifecycle_generation, request.current_quota_key_version,
        request.previous_quota_key_version,
    )


def _reservation_put(handle: AdmissionHandle, table: str) -> dict[str, object]:
    key = reservation_key(handle.key_version, handle.expiry_shard, handle.reservation_id)
    item = {
        **key, "kind": "preview_reservation", "status": "reserved",
        "version": 0, "ttl": handle.created_upper + RETENTION_SECONDS,
        "reservation_id": handle.reservation_id,
        "owner_digest": handle.owner_digest,
        "identity_digest": handle.identity_digest,
        "epoch_minute": handle.epoch_minute, "utc_day": handle.utc_day,
        "reserved_tokens": handle.reserved_tokens,
        "reserved_micro_usd": handle.reserved_micro_usd,
        "created_lower": handle.created_lower, "created_upper": handle.created_upper,
        "lease_until": handle.lease_until, "expiry_shard": handle.expiry_shard,
        "policy_digest": handle.policy_digest,
        "policy_version": handle.policy_version, "key_version": handle.key_version,
        "schema_version": handle.schema_version,
        "lifecycle_generation": handle.lifecycle_generation,
        "current_quota_key_version": handle.current_quota_key_version,
    }
    if handle.previous_identity_digest is not None:
        item["previous_identity_digest"] = handle.previous_identity_digest
        item["previous_quota_key_version"] = handle.previous_quota_key_version
    if handle.circuit_half_open_owner is not None:
        item["circuit_half_open_owner"] = handle.circuit_half_open_owner
    return {"Put": {
        "TableName": table, "Item": item,
        "ConditionExpression": "attribute_not_exists(#pk) AND attribute_not_exists(#sk)",
        "ExpressionAttributeNames": {"#pk": "pk", "#sk": "sk"},
    }}


def _serialize_action(action: Mapping[str, object]) -> dict[str, object]:
    kind, raw = next(iter(action.items()))
    body = dict(raw)  # type: ignore[arg-type]
    body["Item"] = _ddb_map(body["Item"])  # type: ignore[arg-type]
    if "ExpressionAttributeValues" in body:
        body["ExpressionAttributeValues"] = _ddb_map(
            body["ExpressionAttributeValues"]  # type: ignore[arg-type]
        )
    return {kind: body}


def _ddb_map(value: Mapping[str, object]) -> dict[str, object]:
    return {key: _ddb_value(item) for key, item in value.items()}


def _ddb_value(value: object) -> dict[str, object]:
    if type(value) is str:
        return {"S": value}
    if type(value) is int:
        return {"N": str(value)}
    if type(value) in (tuple, list):
        return {"L": [_ddb_value(item) for item in value]}
    raise ValueError("unsupported DynamoDB value")


def _decode_ddb_map(value: Mapping[str, object]) -> dict[str, object]:
    if type(value) is not dict or not all(type(key) is str for key in value):
        raise ValueError("malformed AttributeValue map")
    return {key: _decode_ddb_value(item) for key, item in value.items()}


def _decode_ddb_value(value: object) -> object:
    if type(value) is not dict or len(value) != 1:
        raise ValueError("malformed AttributeValue")
    if set(value) == {"S"} and type(value["S"]) is str:
        return value["S"]
    if set(value) == {"N"} and type(value["N"]) is str:
        raw = value["N"]
        if (
            not raw
            or raw.startswith("+")
            or not raw.isascii()
            or not raw.isdecimal()
            or str(int(raw)) != raw
        ):
            raise ValueError("malformed AttributeValue")
        return int(raw)
    if set(value) == {"L"} and type(value["L"]) is list:
        return [_decode_ddb_value(item) for item in value["L"]]
    raise ValueError("malformed AttributeValue")


def _action_key(action: Mapping[str, object]) -> tuple[str, str]:
    body = action["Put"]  # type: ignore[index]
    item = body["Item"]  # type: ignore[index]
    return item["pk"], item["sk"]  # type: ignore[index]


def _strict_key(value: Mapping[str, str]) -> dict[str, str]:
    if type(value) is not dict or set(value) != {"pk", "sk"}:
        raise ValueError("invalid key")
    if not all(type(item) is str and item for item in value.values()):
        raise ValueError("invalid key")
    return dict(value)


def _request(value: AdmissionCompileRequest) -> AdmissionCompileRequest:
    if type(value) is not AdmissionCompileRequest:
        raise ValueError("invalid admission compile request")
    return value


def _integer(value: object, minimum: int, maximum: int = 9_007_199_254_740_991) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("invalid integer")
    return value


def _digest(value: object) -> bool:
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _uuid4(value: object) -> bool:
    import uuid
    if type(value) is not str:
        return False
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return False
    return parsed.version == 4 and str(parsed) == value


def _utc_day(epoch: float) -> str:
    from datetime import UTC, datetime
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y%m%d")


def _table(value: object) -> str:
    import re
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_.-]{3,255}", value):
        raise ValueError("invalid table name")
    return value


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
