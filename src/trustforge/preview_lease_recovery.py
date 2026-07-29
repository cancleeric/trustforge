"""Bounded, fail-closed recovery for paid-preview reservation leases."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
import re
import time
from typing import Callable, Protocol

from trustforge.preview_admission_compiler import (
    AdmissionHandle,
    RESERVATION_LEASE_SECONDS,
    RETENTION_SECONDS,
)
from trustforge.preview_admission_executor import (
    AdmissionAmbiguity,
    AdmissionAmbiguityResolution,
    _confirmed_ambiguity_resolution,
)
from trustforge.preview_admission_store import MAX_EPOCH_MINUTE, SCHEMA_VERSION
from trustforge.preview_durable_admission_gate import (
    CONTROL_KEY as ADMISSION_CONTROL_KEY,
    DurableAdmissionGate,
    GateState,
    ProofDisposition,
    _decode_control,
)
from trustforge.preview_terminal_reconcile import (
    PreviewTerminalReconciler,
    TerminalDisposition,
    TerminalIntent,
    TerminalOutcome,
    build_terminal_read_request,
    compile_terminal,
    decode_terminal_responses,
)
from trustforge.preview_trusted_clock import TrustedUtcInterval


MAX_PAGES = 4
PAGE_LIMIT = 100
MAX_CANDIDATES = MAX_PAGES * PAGE_LIMIT
CONTROL_PK = "PAP#1#RECOVERY"
CONTROL_SK = "LEASE#WATERMARK"
_TABLE_RE = re.compile(r"^[A-Za-z0-9_.-]{3,255}$")


class RecoveryOutcome(StrEnum):
    PROGRESSED = "progressed"
    IDLE = "idle"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    outcome: RecoveryOutcome
    pages: int
    candidates: int

    def __post_init__(self) -> None:
        if (
            type(self.outcome) is not RecoveryOutcome
            or type(self.pages) is not int
            or type(self.candidates) is not int
            or not 0 <= self.pages <= MAX_PAGES
            or not 0 <= self.candidates <= MAX_CANDIDATES
        ):
            raise ValueError("invalid recovery result")


class RecoveryClient(Protocol):
    def get_item(self, **kwargs: object) -> object: ...
    def query(self, **kwargs: object) -> object: ...
    def put_item(self, **kwargs: object) -> object: ...
    def transact_get_items(self, **kwargs: object) -> object: ...
    def transact_write_items(self, **kwargs: object) -> object: ...


@dataclass(frozen=True, slots=True)
class _Watermark:
    shard: int
    last_sk: str | None
    version: int


@dataclass(frozen=True, slots=True)
class _Reservation:
    handle: AdmissionHandle
    disposition: TerminalDisposition | None
    actual_tokens: int | None = None
    actual_micro_usd: int | None = None


class PreviewLeaseRecovery:
    """Recover at most four strongly-consistent Query pages per invocation."""

    def __init__(
        self,
        client: RecoveryClient,
        table_name: str,
        terminal: PreviewTerminalReconciler,
        *,
        monotonic_clock: Callable[[], float] = time.monotonic,
        deadline_seconds: float = 20.0,
    ) -> None:
        if (
            not _TABLE_RE.fullmatch(table_name)
            or not all(
                callable(getattr(client, name, None))
                for name in ("get_item", "query", "put_item")
            )
            or type(terminal) is not PreviewTerminalReconciler
            or terminal._client is not client
            or terminal._table_name != table_name
            or type(deadline_seconds) not in (int, float)
            or not math.isfinite(deadline_seconds)
            or not 0 < deadline_seconds <= 30
        ):
            raise ValueError("invalid recovery configuration")
        self._client = client
        self._table = table_name
        self._terminal = terminal
        self._monotonic = monotonic_clock
        self._deadline_seconds = float(deadline_seconds)

    @classmethod
    def from_boto3(
        cls, table_name: str, *, region_name: str | None = None
    ) -> "PreviewLeaseRecovery":
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "dynamodb",
            region_name=region_name,
            config=Config(retries={"total_max_attempts": 1, "mode": "standard"}),
        )
        return cls(client, table_name, PreviewTerminalReconciler(client, table_name))

    def run(self, interval: TrustedUtcInterval) -> RecoveryResult:
        if (
            type(interval) is not TrustedUtcInterval
            or not math.isfinite(interval.earliest)
            or not math.isfinite(interval.latest)
            or interval.earliest > interval.latest
        ):
            return RecoveryResult(RecoveryOutcome.UNAVAILABLE, 0, 0)
        try:
            started = self._now()
            watermark = self._read_watermark()
            pages = candidates = 0
            progressed = False
            while pages < MAX_PAGES and candidates < MAX_CANDIDATES:
                if self._now() - started >= self._deadline_seconds:
                    break
                # TTL is cleanup-only.  Once the trusted upper bound reaches
                # the earliest possible TTL in a shard, absence can no longer
                # distinguish "never existed" from cleanup, so never advance.
                earliest_ttl = (
                    watermark.shard * 60
                    - RESERVATION_LEASE_SECONDS
                    + RETENTION_SECONDS
                )
                if interval.latest >= earliest_ttl:
                    return RecoveryResult(
                        RecoveryOutcome.UNAVAILABLE, pages, candidates
                    )
                # No item in this minute shard is provably expired until the
                # trusted lower bound is beyond the shard's final second.
                if interval.earliest <= watermark.shard * 60 + 59:
                    break
                response = self._client.query(**self._query(watermark))
                pages += 1
                items, last_key = _query_page(response, watermark.shard)
                stopped_midpage = False
                for raw in items:
                    if (
                        candidates >= MAX_CANDIDATES
                        or self._now() - started >= self._deadline_seconds
                    ):
                        stopped_midpage = True
                        break
                    reservation = _decode_reservation(raw)
                    handle = reservation.handle
                    candidates += 1
                    if interval.earliest <= handle.lease_until:
                        return RecoveryResult(
                            RecoveryOutcome.UNAVAILABLE, pages, candidates
                        )
                    if reservation.disposition is None:
                        if not self._fence_allows_uncertain():
                            return RecoveryResult(
                                RecoveryOutcome.UNAVAILABLE, pages, candidates
                            )
                        result = self._terminal.reconcile(
                            TerminalIntent(
                                handle, interval, TerminalDisposition.UNCERTAIN
                            )
                        )
                        if result.outcome is not TerminalOutcome.RECONCILED:
                            return RecoveryResult(
                                RecoveryOutcome.UNAVAILABLE, pages, candidates
                            )
                    watermark = self._checkpoint(
                        watermark, handle.reservation_id, same_shard=True
                    )
                    progressed = True
                if stopped_midpage:
                    break
                if last_key is not None:
                    expected_sk = _cursor(last_key, watermark.shard)
                    if not items or watermark.last_sk != expected_sk:
                        return RecoveryResult(
                            RecoveryOutcome.UNAVAILABLE, pages, candidates
                        )
                    continue
                watermark = self._checkpoint(watermark, None, same_shard=False)
                progressed = True
            return RecoveryResult(
                RecoveryOutcome.PROGRESSED if progressed else RecoveryOutcome.IDLE,
                pages,
                candidates,
            )
        except Exception:  # noqa: BLE001 - every malformed/backend path stays closed
            return RecoveryResult(RecoveryOutcome.UNAVAILABLE, 0, 0)

    def _fence_allows_uncertain(self) -> bool:
        return self._read_open_control() is not None

    def _read_open_control(self) -> object:
        response = self._client.get_item(
            TableName=self._table,
            Key=ADMISSION_CONTROL_KEY,
            ConsistentRead=True,
        )
        if type(response) is not dict or set(response) < {"Item"}:
            return None
        control = _decode_control(response["Item"])
        return (
            control
            if control is not None and control.state is GateState.OPEN
            else None
        )

    def _read_watermark(self) -> _Watermark:
        response = self._client.get_item(
            TableName=self._table,
            Key=_ddb_map({"pk": CONTROL_PK, "sk": CONTROL_SK}),
            ConsistentRead=True,
        )
        if type(response) is not dict or set(response) < {"Item"}:
            raise ValueError("missing recovery watermark")
        return _decode_watermark(_decode_map(response["Item"]))

    def _query(self, watermark: _Watermark) -> dict[str, object]:
        pk = f"PAP#1#RESERVATION#{watermark.shard:010d}"
        request: dict[str, object] = {
            "TableName": self._table,
            "KeyConditionExpression": "#pk=:pk",
            "ExpressionAttributeNames": {"#pk": "pk"},
            "ExpressionAttributeValues": {":pk": {"S": pk}},
            "ConsistentRead": True,
            "Limit": PAGE_LIMIT,
        }
        if watermark.last_sk is not None:
            request["ExclusiveStartKey"] = _ddb_map(
                {"pk": pk, "sk": watermark.last_sk}
            )
        return request

    def _checkpoint(
        self, previous: _Watermark, reservation_id: str | None, *, same_shard: bool
    ) -> _Watermark:
        following = _Watermark(
            previous.shard if same_shard else previous.shard + 1,
            f"ID#{reservation_id}" if same_shard else None,
            previous.version + 1,
        )
        if following.shard > MAX_EPOCH_MINUTE:
            raise ValueError("watermark exhausted")
        item = _watermark_item(following)
        control = self._read_open_control()
        if control is None:
            raise ValueError("admission fence is not open")
        try:
            checkpoint = {
                "TableName": self._table,
                "Item": _ddb_map(item),
                "ConditionExpression": (
                    "#kind=:kind AND #schema=:schema AND #version=:version "
                    "AND #shard=:shard"
                    + (
                        " AND #last=:last"
                        if previous.last_sk is not None
                        else " AND attribute_not_exists(#last)"
                    )
                ),
                "ExpressionAttributeNames": {
                    "#kind": "kind",
                    "#schema": "schema_version",
                    "#version": "version",
                    "#shard": "shard",
                    "#last": "last_sk",
                },
                "ExpressionAttributeValues": _ddb_map(
                    {
                        ":kind": "preview_recovery_watermark",
                        ":schema": SCHEMA_VERSION,
                        ":version": previous.version,
                        ":shard": previous.shard,
                        **(
                            {":last": previous.last_sk}
                            if previous.last_sk is not None
                            else {}
                        ),
                    }
                ),
            }
            response = self._client.transact_write_items(
                TransactItems=[
                    {"Put": checkpoint},
                    {"ConditionCheck": _open_condition(self._table, control)},
                ]
            )
            if not _confirmed(response):
                raise ValueError("ambiguous watermark")
            return following
        except Exception:
            # Response loss is safe only when a strong read proves this exact CAS.
            return (
                following
                if self._prove_checkpoint_and_open(following, control)
                else (_raise())
            )

    def _prove_checkpoint_and_open(
        self, expected: _Watermark, control: object
    ) -> bool:
        try:
            response = self._client.transact_get_items(
                TransactItems=[
                    {
                        "Get": {
                            "TableName": self._table,
                            "Key": _ddb_map({"pk": CONTROL_PK, "sk": CONTROL_SK}),
                        }
                    },
                    {
                        "Get": {
                            "TableName": self._table,
                            "Key": ADMISSION_CONTROL_KEY,
                        }
                    },
                ]
            )
            responses = response["Responses"]
            if type(responses) is not list or len(responses) != 2:
                return False
            watermark = _decode_watermark(
                _decode_map(responses[0]["Item"])
            )
            actual_control = _decode_control(responses[1]["Item"])
            return watermark == expected and actual_control == control
        except Exception:
            return False

    def _now(self) -> float:
        value = self._monotonic()
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("invalid monotonic clock")
        return float(value)


class PreviewAmbiguityRecovery:
    """Resolve PRESENT only; ABSENT has no authoritative completion proof."""

    def __init__(
        self,
        client: RecoveryClient,
        table_name: str,
        terminal: PreviewTerminalReconciler,
        durable_gate: DurableAdmissionGate,
    ) -> None:
        if not _TABLE_RE.fullmatch(table_name):
            raise ValueError("invalid table")
        if (
            not all(
                callable(getattr(client, name, None))
                for name in ("get_item", "query", "put_item")
            )
            or type(terminal) is not PreviewTerminalReconciler
            or terminal._client is not client
            or terminal._table_name != table_name
            or type(durable_gate) is not DurableAdmissionGate
            or durable_gate._client is not client
            or durable_gate._table != table_name
        ):
            raise ValueError("invalid ambiguity recovery configuration")
        self._client = client
        self._table = table_name
        self._terminal = terminal
        self._gate = durable_gate

    def resolve(
        self, ambiguity: AdmissionAmbiguity
    ) -> AdmissionAmbiguityResolution | None:
        return (
            _confirmed_ambiguity_resolution(ambiguity)
            if self._recover(ambiguity)
            else None
        )

    def resolve_pending(self) -> bool:
        """Recover a restart-discovered durable quarantine."""

        return self._recover(None)

    def _recover(self, expected: AdmissionAmbiguity | None) -> bool:
        try:
            binding = self._gate.pending_binding
            if binding is None:
                return False
            proof = self._gate.prove_pending_present()
            if (
                proof.disposition is not ProofDisposition.PRESENT
                or (
                    expected is not None
                    and (
                        proof.handle != expected.handle
                        or expected.write_fingerprint
                        != binding.plan_fingerprint
                        or expected.interval.earliest
                        != binding.dispatch_lower
                        or expected.interval.latest
                        != binding.dispatch_upper
                    )
                )
            ):
                return False
            authority = self._gate.pre_provider_abort_authority(proof)
            request = build_terminal_read_request(
                authority.intent, self._table
            )
            response = self._client.transact_get_items(**request)
            snapshot = decode_terminal_responses(
                authority.intent, response["Responses"]
            )
            plan = compile_terminal(
                authority.intent, self._table, snapshot
            )
            return self._gate.execute_recovery(authority, plan)
        except Exception:  # noqa: BLE001 - malformed/backend/ambiguity remains closed
            return False


def _decode_reservation(item: dict[str, object]) -> _Reservation:
    handle = AdmissionHandle(
        *[
            item.get(name)
            for name in (
                "reservation_id", "owner_digest", "identity_digest",
                "previous_identity_digest", "epoch_minute", "utc_day",
                "reserved_tokens", "reserved_micro_usd", "created_lower",
                "created_upper", "lease_until", "expiry_shard", "policy_digest",
                "circuit_half_open_owner", "policy_version", "key_version",
                "schema_version",
            )
        ]
    )
    expected_pk = f"PAP#1#RESERVATION#{handle.expiry_shard:010d}"
    if item.get("pk") != expected_pk or item.get("sk") != f"ID#{handle.reservation_id}":
        raise ValueError("reservation key mismatch")
    if item.get("ttl") != handle.created_upper + RETENTION_SECONDS:
        raise ValueError("reservation ttl mismatch")
    base = {
        "pk": expected_pk,
        "sk": f"ID#{handle.reservation_id}",
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
    if item == {**base, "status": "reserved", "version": 0}:
        return _Reservation(handle, None)
    try:
        disposition = TerminalDisposition(item["terminal_disposition"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("malformed terminal reservation") from None
    terminal = {
        **base,
        "status": "terminal",
        "version": 1,
        "terminal_disposition": disposition.value,
    }
    actual_tokens = actual_micro_usd = None
    if disposition in {
        TerminalDisposition.KNOWN_SUCCESS,
        TerminalDisposition.KNOWN_FAILURE,
    }:
        actual_tokens = item.get("actual_tokens")
        actual_micro_usd = item.get("actual_micro_usd")
        if (
            type(actual_tokens) is not int
            or type(actual_micro_usd) is not int
            or not 0 <= actual_tokens <= handle.reserved_tokens
            or not 0 <= actual_micro_usd <= handle.reserved_micro_usd
        ):
            raise ValueError("malformed terminal actuals")
        terminal["actual_tokens"] = actual_tokens
        terminal["actual_micro_usd"] = actual_micro_usd
    if item != terminal:
        raise ValueError("conflicting terminal reservation")
    return _Reservation(handle, disposition, actual_tokens, actual_micro_usd)


def _query_page(response: object, shard: int) -> tuple[list[dict[str, object]], object]:
    if type(response) is not dict or type(response.get("Items")) is not list:
        raise ValueError("malformed query")
    items = [_decode_map(item) for item in response["Items"]]
    lek = response.get("LastEvaluatedKey")
    if lek is not None and (type(lek) is not dict or not lek):
        raise ValueError("malformed cursor")
    if len(items) > PAGE_LIMIT:
        raise ValueError("query bound exceeded")
    return items, lek


def _cursor(value: object, shard: int) -> str:
    decoded = _decode_map(value)
    if set(decoded) != {"pk", "sk"}:
        raise ValueError("malformed cursor")
    if decoded["pk"] != f"PAP#1#RESERVATION#{shard:010d}":
        raise ValueError("cursor shard mismatch")
    sk = decoded["sk"]
    if type(sk) is not str or not sk.startswith("ID#"):
        raise ValueError("malformed cursor")
    return sk


def _watermark_item(value: _Watermark) -> dict[str, object]:
    return {
        "pk": CONTROL_PK,
        "sk": CONTROL_SK,
        "kind": "preview_recovery_watermark",
        "schema_version": SCHEMA_VERSION,
        "version": value.version,
        "shard": value.shard,
        **({"last_sk": value.last_sk} if value.last_sk is not None else {}),
    }


def _open_condition(table: str, control: object) -> dict[str, object]:
    if (
        getattr(control, "state", None) is not GateState.OPEN
        or type(getattr(control, "generation", None)) is not int
        or type(getattr(control, "version", None)) is not int
    ):
        raise ValueError("invalid open control")
    return {
        "TableName": table,
        "Key": ADMISSION_CONTROL_KEY,
        "ConditionExpression": (
            "#kind=:kind AND #schema=:schema AND #state=:open "
            "AND #generation=:generation AND #version=:version"
        ),
        "ExpressionAttributeNames": {
            "#kind": "kind",
            "#schema": "schema_version",
            "#state": "state",
            "#generation": "generation",
            "#version": "version",
        },
        "ExpressionAttributeValues": _ddb_map(
            {
                ":kind": "preview_admission_quarantine",
                ":schema": 1,
                ":open": "open",
                ":generation": control.generation,
                ":version": control.version,
            }
        ),
    }


def _decode_watermark(item: dict[str, object]) -> _Watermark:
    expected = {"pk", "sk", "kind", "schema_version", "version", "shard"}
    if "last_sk" in item:
        expected.add("last_sk")
    if (
        set(item) != expected
        or item.get("pk") != CONTROL_PK
        or item.get("sk") != CONTROL_SK
        or item.get("kind") != "preview_recovery_watermark"
        or item.get("schema_version") != SCHEMA_VERSION
        or type(item.get("version")) is not int
        or item["version"] < 0
        or type(item.get("shard")) is not int
        or not 0 <= item["shard"] <= MAX_EPOCH_MINUTE
        or (
            "last_sk" in item
            and (
                type(item["last_sk"]) is not str
                or not item["last_sk"].startswith("ID#")
            )
        )
    ):
        raise ValueError("malformed watermark")
    return _Watermark(item["shard"], item.get("last_sk"), item["version"])


def _ddb_map(value: dict[str, object]) -> dict[str, object]:
    return {key: _ddb_value(item) for key, item in value.items()}


def _ddb_value(value: object) -> dict[str, str]:
    if type(value) is str:
        return {"S": value}
    if type(value) is int:
        return {"N": str(value)}
    raise ValueError("unsupported value")


def _decode_map(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise ValueError("malformed map")
    return {key: _decode_value(item) for key, item in value.items()}


def _decode_value(value: object) -> object:
    if type(value) is not dict or len(value) != 1:
        raise ValueError("malformed value")
    if set(value) == {"S"} and type(value["S"]) is str:
        return value["S"]
    if set(value) == {"N"} and type(value["N"]) is str:
        raw = value["N"]
        if not raw.isascii() or not raw.isdecimal() or str(int(raw)) != raw:
            raise ValueError("malformed number")
        return int(raw)
    raise ValueError("malformed value")


def _confirmed(response: object) -> bool:
    return (
        type(response) is dict
        and type(response.get("ResponseMetadata")) is dict
        and response["ResponseMetadata"].get("HTTPStatusCode") == 200
        and type(response["ResponseMetadata"].get("RequestId")) is str
        and bool(response["ResponseMetadata"]["RequestId"])
    )


def _raise() -> _Watermark:
    raise ValueError("watermark CAS unresolved")
