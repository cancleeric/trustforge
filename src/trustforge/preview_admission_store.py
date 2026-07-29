"""Strict, fail-closed DynamoDB primitives for paid-preview admission.

Only single-item circuit operations are executed here.  The public action
builder is consumed by #973 when it composes the complete admission
``TransactWriteItems`` request.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, DecimalException, ROUND_CEILING, localcontext
from enum import StrEnum
import math
import re
from types import MappingProxyType
import uuid
from typing import Mapping, Protocol

from trustforge.preview_trusted_clock import TrustedUtcInterval


TABLE_PARTITION_KEY = "pk"
TABLE_SORT_KEY = "sk"
TABLE_KEY_SCHEMA = (
    (TABLE_PARTITION_KEY, "HASH", "S"),
    (TABLE_SORT_KEY, "RANGE", "S"),
)
TABLE_ATTRIBUTE_DEFINITIONS = (
    (TABLE_PARTITION_KEY, "S"),
    (TABLE_SORT_KEY, "S"),
)
KEY_VERSION = 1
SCHEMA_VERSION = 1
MAX_CAS_ATTEMPTS = 3
MAX_TTL_SECONDS = 7 * 86_400
MAX_EPOCH_MINUTE = 4_223_371_679  # 9999-12-31T23:59 UTC
MAX_EPOCH_SECOND = 253_402_300_799  # 9999-12-31T23:59:59 UTC
MAX_DDB_INTEGER = 9_007_199_254_740_991
MAX_USD = Decimal("9007199254.740991")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_TABLE_RE = re.compile(r"^[A-Za-z0-9_.-]{3,255}$")


class PreviewStoreFailure(StrEnum):
    BACKEND_UNAVAILABLE = "backend_unavailable"
    MALFORMED_ITEM = "malformed_item"
    CONTENTION = "contention"
    CIRCUIT_OPEN = "circuit_open"
    LEASE_INVALID = "lease_invalid"
    TIME_REGRESSION = "time_regression"
    VERSION_EXHAUSTED = "version_exhausted"


class PreviewStoreUnavailable(RuntimeError):
    def __init__(self, reason: PreviewStoreFailure) -> None:
        self.reason = reason
        super().__init__(reason.value)


class ProviderFailure(StrEnum):
    TRANSPORT_CONNECT = "transport_connect"
    PROVIDER_5XX = "provider_5xx"
    THROTTLE_UNAVAILABLE = "throttle_unavailable"
    TIMEOUT = "timeout"


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitDisposition(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class ActionKind(StrEnum):
    CONDITION_CHECK = "ConditionCheck"
    PUT = "Put"


@dataclass(frozen=True, slots=True)
class CircuitSnapshot:
    """Validated circuit snapshot; ``state=None`` is the only absent grammar."""

    pk: str
    sk: str
    state: CircuitState | None
    version: int | None
    failures: tuple[int, ...] = ()
    open_until: int | None = None
    owner: str | None = None
    lease_until: int | None = None

    @classmethod
    def absent(cls, key: Mapping[str, str]) -> "CircuitSnapshot":
        copied = _copy_key(key)
        return cls(copied["pk"], copied["sk"], state=None, version=None)

    @property
    def key(self) -> dict[str, str]:
        return {"pk": self.pk, "sk": self.sk}


@dataclass(frozen=True, slots=True)
class CircuitPermit:
    disposition: CircuitDisposition
    snapshot: CircuitSnapshot
    half_open_owner: str | None = None

    @property
    def allowed(self) -> bool:
        return self.disposition is CircuitDisposition.ALLOW


@dataclass(frozen=True, slots=True)
class CircuitAdmissionAction:
    """Pure boto3 transaction action; this module never executes it."""

    disposition: CircuitDisposition
    kind: ActionKind
    key: Mapping[str, str]
    condition_expression: str
    expression_attribute_names: Mapping[str, str]
    expression_attribute_values: Mapping[str, object]
    previous_snapshot: CircuitSnapshot | None = None
    next_snapshot: CircuitSnapshot | None = None
    item: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if (
            type(self.disposition) is not CircuitDisposition
            or type(self.kind) is not ActionKind
            or type(self.condition_expression) is not str
            or (
                self.disposition is CircuitDisposition.ALLOW
                and (
                    self.kind is not ActionKind.PUT
                    or self.previous_snapshot is None
                    or self.next_snapshot is None
                    or self.item is None
                )
            )
            or (
                self.disposition is CircuitDisposition.DENY
                and (
                    self.kind is not ActionKind.CONDITION_CHECK
                    or self.previous_snapshot is not None
                    or self.next_snapshot is not None
                    or self.item is not None
                    or self.condition_expression
                    or self.expression_attribute_names
                    or self.expression_attribute_values
                )
            )
        ):
            raise ValueError("invalid circuit action")
        copied_key = _copy_key(self.key)
        copied_names = dict(self.expression_attribute_names)
        copied_values = dict(self.expression_attribute_values)
        if not all(type(key) is str and type(value) is str for key, value in copied_names.items()):
            raise ValueError("invalid circuit action")
        for key, value in copied_values.items():
            if type(key) is not str or not _immutable_expression_value(value):
                raise ValueError("invalid circuit action")
        if self.previous_snapshot is not None:
            _validate_snapshot(self.previous_snapshot)
        if self.next_snapshot is not None:
            _validate_snapshot(self.next_snapshot)
        if self.item is not None:
            copied_item = dict(self.item)
            if not all(
                type(key) is str and _immutable_expression_value(value)
                for key, value in copied_item.items()
            ):
                raise ValueError("invalid circuit action")
            object.__setattr__(self, "item", MappingProxyType(copied_item))
        if self.disposition is CircuitDisposition.ALLOW:
            assert self.previous_snapshot is not None
            assert self.next_snapshot is not None
            assert self.item is not None
            previous = _validate_snapshot(self.previous_snapshot)
            following = _validate_snapshot(self.next_snapshot)
            expected_condition, expected_names, expected_values = (
                _previous_snapshot_predicate(previous)
            )
            if (
                not self.condition_expression
                or copied_key != previous.key
                or following.key != previous.key
                or following.version != _next_version(previous.version)
                or copied_item != _canonical_item(following)
                or self.condition_expression != expected_condition
                or copied_names != expected_names
                or copied_values != expected_values
            ):
                raise ValueError("noncanonical circuit action")
        object.__setattr__(self, "key", MappingProxyType(copied_key))
        object.__setattr__(
            self, "expression_attribute_names",
            MappingProxyType(copied_names),
        )
        object.__setattr__(
            self, "expression_attribute_values",
            MappingProxyType(copied_values),
        )

    def transaction_item(self, table_name: str) -> dict[str, object]:
        if self.disposition is CircuitDisposition.DENY:
            raise ValueError("denied circuit action is not serializable")
        if type(table_name) is not str or not _TABLE_RE.fullmatch(table_name):
            raise ValueError("invalid table name")
        body: dict[str, object] = {
            "TableName": table_name,
            "Key": dict(self.key),
            "ConditionExpression": self.condition_expression,
        }
        if self.expression_attribute_names:
            body["ExpressionAttributeNames"] = dict(self.expression_attribute_names)
        if self.expression_attribute_values:
            body["ExpressionAttributeValues"] = dict(self.expression_attribute_values)
        assert self.kind is ActionKind.PUT and self.item is not None
        body.pop("Key")
        body["Item"] = dict(self.item)
        return {self.kind.value: body}


class DynamoTable(Protocol):
    def get_item(self, **kwargs: object) -> dict[str, object]: ...
    def put_item(self, **kwargs: object) -> dict[str, object]: ...


def _key_version(value: object) -> int:
    if type(value) is not int or value != KEY_VERSION:
        raise ValueError("unsupported key version")
    return value


def _digest(value: object) -> str:
    if type(value) is not str or not _DIGEST_RE.fullmatch(value):
        raise ValueError("invalid digest")
    return value


def _owner(value: object) -> str:
    return _digest(value)


def _uuid4(value: object) -> str:
    if type(value) is not str:
        raise ValueError("invalid reservation id")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        raise ValueError("invalid reservation id") from None
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("invalid reservation id")
    return value


def decode_integral(
    value: object, *, minimum: int = -9_007_199_254_740_991,
    maximum: int = 9_007_199_254_740_991,
) -> int:
    if type(value) is int:
        result = value
    elif type(value) is Decimal:
        if not value.is_finite() or value != value.to_integral_value():
            raise ValueError("invalid integral value")
        result = int(value)
    else:
        raise ValueError("invalid integral value")
    if result < minimum or result > maximum:
        raise ValueError("integral value out of range")
    return result


def usd_to_micros_ceiling(value: Decimal) -> int:
    """Convert a Decimal USD amount to microUSD; no implicit coercion."""

    if type(value) is not Decimal or not value.is_finite():
        raise ValueError("invalid USD value")
    try:
        # adjusted() is constant-time in the coefficient length and prevents a
        # hostile exponent from reaching multiplication or int conversion.
        if value.is_signed() or value.adjusted() > 9 or value > MAX_USD:
            raise ValueError("invalid USD value")
        with localcontext() as context:
            context.prec = 32
            context.clear_traps()
            micros = (value * Decimal(1_000_000)).to_integral_value(
                rounding=ROUND_CEILING
            )
        return decode_integral(micros, minimum=0, maximum=MAX_DDB_INTEGER)
    except (DecimalException, OverflowError, ValueError):
        raise ValueError("invalid USD value") from None


def validate_interval(interval: TrustedUtcInterval) -> TrustedUtcInterval:
    if (
        type(interval) is not TrustedUtcInterval
        or type(interval.earliest) not in (int, float)
        or type(interval.latest) not in (int, float)
        or not math.isfinite(interval.earliest)
        or not math.isfinite(interval.latest)
        or interval.earliest < 0
        or interval.latest < interval.earliest
        or interval.latest > MAX_EPOCH_SECOND
    ):
        raise ValueError("invalid trusted UTC interval")
    return interval


def ttl_from_interval(interval: TrustedUtcInterval, retention_seconds: int) -> int:
    trusted = validate_interval(interval)
    retention = decode_integral(retention_seconds, minimum=1, maximum=MAX_TTL_SECONDS)
    return _bounded_timestamp(math.ceil(trusted.latest) + retention)


def _bounded_timestamp(value: object) -> int:
    return decode_integral(value, minimum=0, maximum=MAX_EPOCH_SECOND)


def _copy_key(value: Mapping[str, str]) -> dict[str, str]:
    if type(value) is not dict or set(value) != {"pk", "sk"}:
        raise ValueError("invalid circuit key")
    if type(value["pk"]) is not str or type(value["sk"]) is not str:
        raise ValueError("invalid circuit key")
    return {"pk": value["pk"], "sk": value["sk"]}


def _immutable_expression_value(value: object) -> bool:
    if type(value) in (str, int, Decimal):
        return type(value) is not bool
    return type(value) is tuple and all(
        type(item) in (str, int, Decimal) and type(item) is not bool for item in value
    )


def _next_version(version: int | None) -> int:
    current = -1 if version is None else decode_integral(
        version, minimum=0, maximum=MAX_DDB_INTEGER
    )
    if current >= MAX_DDB_INTEGER:
        raise PreviewStoreUnavailable(PreviewStoreFailure.VERSION_EXHAUSTED)
    return current + 1


def _key(pk: str, sk: str) -> dict[str, str]:
    return {TABLE_PARTITION_KEY: pk, TABLE_SORT_KEY: sk}


def identity_minute_key(key_version: int, identity_digest: str, epoch_minute: int) -> dict[str, str]:
    version = _key_version(key_version)
    digest = _digest(identity_digest)
    minute = decode_integral(epoch_minute, minimum=0, maximum=MAX_EPOCH_MINUTE)
    return _key(f"PAP#{version}#IDENTITY#{digest}", f"MINUTE#{minute:010d}")


def identity_day_key(key_version: int, identity_digest: str, utc_day: str) -> dict[str, str]:
    version = _key_version(key_version)
    digest = _digest(identity_digest)
    if type(utc_day) is not str or not re.fullmatch(r"\d{8}", utc_day):
        raise ValueError("invalid UTC day")
    try:
        datetime.strptime(utc_day, "%Y%m%d").replace(tzinfo=UTC)
    except ValueError:
        raise ValueError("invalid UTC day") from None
    return _key(f"PAP#{version}#IDENTITY#{digest}", f"DAY#{utc_day}")


def identity_concurrency_key(key_version: int, identity_digest: str) -> dict[str, str]:
    version = _key_version(key_version)
    return _key(f"PAP#{version}#IDENTITY#{_digest(identity_digest)}", "CONCURRENCY")


def global_concurrency_key(key_version: int) -> dict[str, str]:
    return _key(f"PAP#{_key_version(key_version)}#GLOBAL", "CONCURRENCY")


def global_token_minute_key(key_version: int, epoch_minute: int) -> dict[str, str]:
    minute = decode_integral(epoch_minute, minimum=0, maximum=MAX_EPOCH_MINUTE)
    return _key(f"PAP#{_key_version(key_version)}#GLOBAL", f"TOKEN#MINUTE#{minute:010d}")


def global_token_day_key(key_version: int, utc_day: str) -> dict[str, str]:
    identity_day_key(key_version, "0" * 64, utc_day)
    return _key(f"PAP#{key_version}#GLOBAL", f"TOKEN#DAY#{utc_day}")


def global_usd_minute_key(key_version: int, epoch_minute: int) -> dict[str, str]:
    minute = decode_integral(epoch_minute, minimum=0, maximum=MAX_EPOCH_MINUTE)
    return _key(f"PAP#{_key_version(key_version)}#GLOBAL", f"USD#MINUTE#{minute:010d}")


def global_usd_day_key(key_version: int, utc_day: str) -> dict[str, str]:
    identity_day_key(key_version, "0" * 64, utc_day)
    return _key(f"PAP#{key_version}#GLOBAL", f"USD#DAY#{utc_day}")


def circuit_key(key_version: int, policy_digest: str) -> dict[str, str]:
    return _key(f"PAP#{_key_version(key_version)}#CIRCUIT", f"POLICY#{_digest(policy_digest)}")


def reservation_key(key_version: int, expiry_shard: int, reservation_id: str) -> dict[str, str]:
    shard = decode_integral(expiry_shard, minimum=0, maximum=MAX_EPOCH_MINUTE)
    return _key(
        f"PAP#{_key_version(key_version)}#RESERVATION#{shard:010d}",
        f"ID#{_uuid4(reservation_id)}",
    )


_ATTRS = {
    "pk", "sk", "kind", "schema_version", "state", "version", "failures",
    "open_until", "owner", "lease_until",
}
_BASE = {"pk", "sk", "kind", "schema_version", "state", "version", "failures"}


def decode_circuit_item(item: object, expected_key: Mapping[str, str]) -> CircuitSnapshot:
    if type(item) is not dict or set(item) - _ATTRS or not _BASE.issubset(item):
        raise ValueError("malformed circuit")
    if set(expected_key) != {"pk", "sk"} or item["pk"] != expected_key["pk"] or item["sk"] != expected_key["sk"]:
        raise ValueError("malformed circuit")
    if item["kind"] != "preview_circuit" or decode_integral(item["schema_version"], minimum=1, maximum=1) != SCHEMA_VERSION:
        raise ValueError("malformed circuit")
    try:
        state = CircuitState(item["state"]) if type(item["state"]) is str else None
    except ValueError:
        state = None
    if state is None:
        raise ValueError("malformed circuit")
    version = decode_integral(item["version"], minimum=0)
    raw = item["failures"]
    if type(raw) is not list or len(raw) > 5:
        raise ValueError("malformed circuit")
    failures = tuple(_bounded_timestamp(v) for v in raw)
    if tuple(sorted(failures)) != failures:
        raise ValueError("malformed circuit")
    optional = set(item) - _BASE
    if state is CircuitState.CLOSED and optional:
        raise ValueError("malformed circuit")
    if state is CircuitState.OPEN and optional != {"open_until"}:
        raise ValueError("malformed circuit")
    if state is CircuitState.HALF_OPEN and optional != {"owner", "lease_until"}:
        raise ValueError("malformed circuit")
    open_until = _bounded_timestamp(item["open_until"]) if state is CircuitState.OPEN else None
    owner = _owner(item["owner"]) if state is CircuitState.HALF_OPEN else None
    lease_until = _bounded_timestamp(item["lease_until"]) if state is CircuitState.HALF_OPEN else None
    snapshot = CircuitSnapshot(expected_key["pk"], expected_key["sk"], state, version, failures, open_until, owner, lease_until)
    return _validate_snapshot(snapshot)


def _validate_snapshot(snapshot: CircuitSnapshot) -> CircuitSnapshot:
    if type(snapshot) is not CircuitSnapshot:
        raise ValueError("invalid circuit snapshot")
    key = snapshot.key
    if (
        set(key) != {"pk", "sk"}
        or type(key["pk"]) is not str
        or type(key["sk"]) is not str
        or not re.fullmatch(r"PAP#1#CIRCUIT", key["pk"])
        or not re.fullmatch(r"POLICY#[0-9a-f]{64}", key["sk"])
    ):
        raise ValueError("invalid circuit snapshot")
    if snapshot.state is None:
        if (
            snapshot.version is not None
            or snapshot.failures
            or snapshot.open_until is not None
            or snapshot.owner is not None
            or snapshot.lease_until is not None
        ):
            raise ValueError("invalid absent circuit snapshot")
        return snapshot
    if type(snapshot.state) is not CircuitState:
        raise ValueError("invalid circuit snapshot")
    version = decode_integral(snapshot.version, minimum=0)
    if type(snapshot.failures) is not tuple or len(snapshot.failures) > 5:
        raise ValueError("invalid circuit snapshot")
    failures = tuple(_bounded_timestamp(value) for value in snapshot.failures)
    if tuple(sorted(failures)) != failures:
        raise ValueError("invalid circuit snapshot")
    if snapshot.state is CircuitState.CLOSED:
        valid = (
            len(failures) <= 4
            and snapshot.open_until is None
            and snapshot.owner is None
            and snapshot.lease_until is None
        )
    elif snapshot.state is CircuitState.OPEN:
        valid = (
            len(failures) == 5
            and
            snapshot.open_until is not None
            and snapshot.owner is None
            and snapshot.lease_until is None
        )
        if valid:
            _bounded_timestamp(snapshot.open_until)
    else:
        valid = (
            len(failures) == 5
            and
            snapshot.open_until is None
            and snapshot.owner is not None
            and snapshot.lease_until is not None
        )
        if valid:
            _owner(snapshot.owner)
            _bounded_timestamp(snapshot.lease_until)
    if not valid or version != snapshot.version:
        raise ValueError("invalid circuit snapshot")
    return snapshot


def _canonical_item(snapshot: CircuitSnapshot) -> dict[str, object]:
    snapshot = _validate_snapshot(snapshot)
    assert snapshot.state is not None and snapshot.version is not None
    item: dict[str, object] = {
        "pk": snapshot.pk, "sk": snapshot.sk, "kind": "preview_circuit",
        "schema_version": SCHEMA_VERSION, "state": snapshot.state.value,
        "version": snapshot.version, "failures": tuple(snapshot.failures),
    }
    if snapshot.state is CircuitState.OPEN:
        item["open_until"] = snapshot.open_until
    elif snapshot.state is CircuitState.HALF_OPEN:
        item["owner"] = snapshot.owner
        item["lease_until"] = snapshot.lease_until
    return item


def _previous_snapshot_predicate(
    snapshot: CircuitSnapshot,
) -> tuple[str, dict[str, str], dict[str, object]]:
    """Exact immutable-item predicate shared by CAS and admission checks."""

    snapshot = _validate_snapshot(snapshot)
    if snapshot.state is None:
        return (
            "attribute_not_exists(#pk) AND attribute_not_exists(#sk)",
            {"#pk": "pk", "#sk": "sk"},
            {},
        )
    names = {
        "#pk": "pk", "#sk": "sk", "#pkind": "kind",
        "#pschema": "schema_version", "#pstate": "state",
        "#pversion": "version", "#pfailures": "failures",
        "#popen": "open_until", "#powner": "owner", "#please": "lease_until",
    }
    values: dict[str, object] = {
        ":pkind": "preview_circuit", ":pschema": SCHEMA_VERSION,
        ":pstate": snapshot.state.value, ":previous": snapshot.version,
        ":pfailures": tuple(snapshot.failures),
    }
    condition = (
        "attribute_exists(#pk) AND attribute_exists(#sk)"
        " AND #pkind=:pkind AND #pschema=:pschema AND #pstate=:pstate"
        " AND #pversion=:previous AND #pfailures=:pfailures"
    )
    if snapshot.state is CircuitState.CLOSED:
        condition += (
            " AND attribute_not_exists(#popen)"
            " AND attribute_not_exists(#powner)"
            " AND attribute_not_exists(#please)"
        )
    elif snapshot.state is CircuitState.OPEN:
        values[":popen"] = snapshot.open_until
        condition += (
            " AND #popen=:popen AND attribute_not_exists(#powner)"
            " AND attribute_not_exists(#please)"
        )
    else:
        values[":powner"] = snapshot.owner
        values[":please"] = snapshot.lease_until
        condition += (
            " AND attribute_not_exists(#popen)"
            " AND #powner=:powner AND #please=:please"
        )
    return condition, names, values


def _cas_action(previous: CircuitSnapshot, next_snapshot: CircuitSnapshot) -> CircuitAdmissionAction:
    previous = _validate_snapshot(previous)
    next_snapshot = _validate_snapshot(next_snapshot)
    if previous.key != next_snapshot.key or next_snapshot.version != _next_version(previous.version):
        raise ValueError("invalid circuit transition")
    condition, predicate_names, predicate_values = _previous_snapshot_predicate(previous)
    return CircuitAdmissionAction(
        CircuitDisposition.ALLOW, ActionKind.PUT, dict(previous.key), condition,
        predicate_names, predicate_values, previous_snapshot=previous,
        next_snapshot=next_snapshot,
        item=_canonical_item(next_snapshot),
    )


def build_admission_action(
    snapshot: CircuitSnapshot, owner: str, interval: TrustedUtcInterval
) -> CircuitAdmissionAction:
    """Build the circuit component of #973's atomic admission transaction."""

    snapshot = _validate_snapshot(snapshot)
    trusted = validate_interval(interval)
    probe_owner = _owner(owner)
    if snapshot.state is None:
        created = CircuitSnapshot(
            snapshot.pk, snapshot.sk, CircuitState.CLOSED, 0, ()
        )
        return _cas_action(snapshot, created)
    if snapshot.state is CircuitState.CLOSED:
        refreshed = CircuitSnapshot(
            snapshot.pk, snapshot.sk, CircuitState.CLOSED,
            _next_version(snapshot.version), snapshot.failures,
        )
        return _cas_action(snapshot, refreshed)
    if snapshot.state is CircuitState.OPEN:
        assert snapshot.open_until is not None
        if trusted.earliest < snapshot.open_until:
            return CircuitAdmissionAction(
                CircuitDisposition.DENY, ActionKind.CONDITION_CHECK, dict(snapshot.key),
                "", {}, {},
            )
    else:
        assert snapshot.lease_until is not None
        if trusted.earliest < snapshot.lease_until:
            return CircuitAdmissionAction(
                CircuitDisposition.DENY, ActionKind.CONDITION_CHECK, dict(snapshot.key),
                "", {}, {},
            )
    next_snapshot = CircuitSnapshot(
        snapshot.pk, snapshot.sk, CircuitState.HALF_OPEN,
        _next_version(snapshot.version),
        snapshot.failures, owner=probe_owner,
        lease_until=_bounded_timestamp(
            math.ceil(trusted.latest) + PreviewCircuitStore.HALF_OPEN_LEASE_SECONDS
        ),
    )
    return _cas_action(snapshot, next_snapshot)


class PreviewCircuitStore:
    FAILURE_WINDOW_SECONDS = 60
    OPEN_SECONDS = 120
    HALF_OPEN_LEASE_SECONDS = 15
    FAILURE_THRESHOLD = 5

    def __init__(self, *, table: DynamoTable, key_version: int, policy_digest: str) -> None:
        self._table = table
        self._key = circuit_key(key_version, policy_digest)

    def snapshot(self) -> CircuitSnapshot:
        try:
            response = self._table.get_item(Key=self._key, ConsistentRead=True)
            if type(response) is not dict:
                raise ValueError
            if "Item" not in response:
                return CircuitSnapshot.absent(self._key)
            if type(response["Item"]) is not dict:
                raise ValueError
            return decode_circuit_item(response["Item"], self._key)
        except ValueError:
            raise PreviewStoreUnavailable(PreviewStoreFailure.MALFORMED_ITEM) from None
        except PreviewStoreUnavailable:
            raise
        except Exception:
            raise PreviewStoreUnavailable(PreviewStoreFailure.BACKEND_UNAVAILABLE) from None

    def acquire(self, *, interval: TrustedUtcInterval, owner: str) -> CircuitPermit:
        for _ in range(MAX_CAS_ATTEMPTS):
            current = self.snapshot()
            action = build_admission_action(current, owner, interval)
            if action.disposition is CircuitDisposition.DENY:
                return CircuitPermit(CircuitDisposition.DENY, current)
            if self._execute(action):
                assert action.next_snapshot is not None
                probe_owner = (
                    owner
                    if action.next_snapshot.state is CircuitState.HALF_OPEN
                    else None
                )
                return CircuitPermit(
                    CircuitDisposition.ALLOW, action.next_snapshot, probe_owner
                )
        raise PreviewStoreUnavailable(PreviewStoreFailure.CONTENTION)

    def record_failure(
        self, *, interval: TrustedUtcInterval, failure: ProviderFailure, owner: str | None = None
    ) -> None:
        if type(failure) is not ProviderFailure:
            raise ValueError("failure is not provider-operational")
        trusted = validate_interval(interval)
        probe_owner = _owner(owner) if owner is not None else None
        timestamp = math.ceil(trusted.latest)
        for _ in range(MAX_CAS_ATTEMPTS):
            current = self.snapshot()
            if current.failures and timestamp < current.failures[-1]:
                raise PreviewStoreUnavailable(PreviewStoreFailure.TIME_REGRESSION)
            if current.state is CircuitState.OPEN:
                raise PreviewStoreUnavailable(PreviewStoreFailure.CIRCUIT_OPEN)
            if current.state is CircuitState.HALF_OPEN:
                if (
                    probe_owner != current.owner
                    or current.lease_until is None
                    or trusted.latest >= current.lease_until
                ):
                    raise PreviewStoreUnavailable(PreviewStoreFailure.LEASE_INVALID)
                next_value = CircuitSnapshot(
                    self._key["pk"], self._key["sk"], CircuitState.OPEN,
                    _next_version(current.version), current.failures,
                    open_until=_bounded_timestamp(timestamp + self.OPEN_SECONDS),
                )
            else:
                cutoff = math.floor(trusted.earliest) - self.FAILURE_WINDOW_SECONDS
                failures = tuple(v for v in current.failures if v >= cutoff)
                failures = (*failures, timestamp)[-self.FAILURE_THRESHOLD :]
                if current.state in (None, CircuitState.CLOSED) and probe_owner is not None:
                    raise ValueError("closed failure must not have owner")
                next_value = CircuitSnapshot(
                    self._key["pk"], self._key["sk"],
                    CircuitState.OPEN if len(failures) >= self.FAILURE_THRESHOLD else CircuitState.CLOSED,
                    _next_version(current.version),
                    failures,
                    open_until=_bounded_timestamp(timestamp + self.OPEN_SECONDS)
                    if len(failures) >= self.FAILURE_THRESHOLD else None,
                )
            if self._execute(_cas_action(current, next_value)):
                return
        raise PreviewStoreUnavailable(PreviewStoreFailure.CONTENTION)

    def record_success(self, *, interval: TrustedUtcInterval, owner: str) -> None:
        trusted = validate_interval(interval)
        probe_owner = _owner(owner)
        for _ in range(MAX_CAS_ATTEMPTS):
            current = self.snapshot()
            if (
                current.state is not CircuitState.HALF_OPEN
                or current.owner != probe_owner
                or current.lease_until is None
                or trusted.latest >= current.lease_until
            ):
                raise PreviewStoreUnavailable(PreviewStoreFailure.LEASE_INVALID)
            closed = CircuitSnapshot(
                self._key["pk"], self._key["sk"], CircuitState.CLOSED,
                _next_version(current.version), ()
            )
            if self._execute(_cas_action(current, closed)):
                return
        raise PreviewStoreUnavailable(PreviewStoreFailure.CONTENTION)

    def _execute(self, action: CircuitAdmissionAction) -> bool:
        assert action.kind is ActionKind.PUT and action.item is not None
        try:
            kwargs: dict[str, object] = dict(
                Item=dict(action.item),
                ConditionExpression=action.condition_expression,
                ExpressionAttributeNames=dict(action.expression_attribute_names),
            )
            if action.expression_attribute_values:
                kwargs["ExpressionAttributeValues"] = dict(
                    action.expression_attribute_values
                )
            self._table.put_item(**kwargs)
            return True
        except Exception as exc:
            # Botocore's response is touched only after a concrete ClientError type
            # check; arbitrary hostile exceptions cannot run Mapping methods.
            from botocore.exceptions import ClientError
            if isinstance(exc, ClientError):
                response = exc.response
                if (
                    type(response) is dict
                    and type(response.get("Error")) is dict
                    and response["Error"].get("Code") == "ConditionalCheckFailedException"
                ):
                    return False
            raise PreviewStoreUnavailable(PreviewStoreFailure.BACKEND_UNAVAILABLE) from None
