"""Production shared DynamoDB authority for formal-run idempotency."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Protocol

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import BotoCoreError, ClientError

from .formal_run_idempotency import (
    AcquireResult,
    FormalRunIdentity,
    FormalRunLookup,
    FormalRunReceipt,
    IdempotencyInProgress,
    IdempotencyUnavailable,
    StaleFencingToken,
    TerminalSafeResponse,
    accepted_acquisition_epochs,
)

_STRICT_ID = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_SERIALIZER = TypeSerializer()
_DESERIALIZER = TypeDeserializer()
_TABLE_NAME = re.compile(r"^[A-Za-z0-9_.-]{3,255}$")


class DynamoDbClient(Protocol):
    def get_item(self, **kwargs: Any) -> dict[str, Any]: ...
    def put_item(self, **kwargs: Any) -> dict[str, Any]: ...
    def update_item(self, **kwargs: Any) -> dict[str, Any]: ...
    def delete_item(self, **kwargs: Any) -> dict[str, Any]: ...
    def transact_write_items(self, **kwargs: Any) -> dict[str, Any]: ...


def _timestamp(value: datetime) -> Decimal:
    if value.tzinfo is None:
        raise IdempotencyUnavailable("trusted clock unavailable")
    return Decimal(str(value.astimezone(timezone.utc).timestamp()))


def _stored_decimal(value: object, field: str, *, integral: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, Decimal) or not value.is_finite():
        raise IdempotencyUnavailable(f"stored {field} is invalid")
    if integral and (value != value.to_integral_value() or value <= 0):
        raise IdempotencyUnavailable(f"stored {field} is invalid")
    return value


def _marshal(values: Mapping[str, object]) -> dict[str, dict[str, object]]:
    return {key: _SERIALIZER.serialize(value) for key, value in values.items()}


def _unmarshal(values: Mapping[str, object]) -> dict[str, object]:
    return {key: _DESERIALIZER.deserialize(value) for key, value in values.items()}


def _conditional(exc: ClientError) -> bool:
    code = exc.response.get("Error", {}).get("Code")
    if code == "ConditionalCheckFailedException":
        return True
    if code != "TransactionCanceledException":
        return False
    reasons = exc.response.get("CancellationReasons")
    if not isinstance(reasons, list) or not reasons:
        return False
    reason_codes = [
        reason.get("Code")
        for reason in reasons
        if isinstance(reason, Mapping)
    ]
    return (
        len(reason_codes) == len(reasons)
        and "ConditionalCheckFailed" in reason_codes
        and all(item in {None, "None", "ConditionalCheckFailed"} for item in reason_codes)
    )


class DynamoDbFormalRunIdempotencyStore:
    """Strongly consistent, conditionally fenced multi-instance authority.

    The table uses string partition/sort keys named ``pk`` and ``sk``.  It may
    be shared with other components because every item is namespaced.
    """

    def __init__(self, client: DynamoDbClient, *, table_name: str) -> None:
        if not isinstance(table_name, str) or _TABLE_NAME.fullmatch(table_name) is None:
            raise ValueError("invalid DynamoDB table name")
        required = (
            "get_item", "put_item", "update_item", "delete_item", "transact_write_items"
        )
        if any(not callable(getattr(client, method, None)) for method in required):
            raise TypeError("client does not implement required DynamoDB operations")
        self._client = client
        self._table = table_name

    @staticmethod
    def _pk(identity: FormalRunIdentity) -> str:
        material = "\x1f".join(
            (
                identity.namespace,
                identity.caller_scope_hmac.key_id,
                identity.caller_scope_hmac.digest,
                identity.key_hmac.key_id,
                identity.key_hmac.digest,
            )
        )
        return "formal-run#authority#" + hashlib.sha256(material.encode()).hexdigest()

    @classmethod
    def _key(cls, identity: FormalRunIdentity, kind: str = "authority") -> dict[str, object]:
        return {"pk": cls._pk(identity), "sk": kind}

    @staticmethod
    def _guard_key(identity: FormalRunIdentity, kind: str, value: str) -> dict[str, object]:
        material = "\x1f".join((identity.namespace, identity.scope_locator, kind, value))
        return {
            "pk": "formal-run#guard#" + hashlib.sha256(material.encode()).hexdigest(),
            "sk": kind,
        }

    def _get(self, key: Mapping[str, object]) -> dict[str, object] | None:
        try:
            response = self._client.get_item(
                TableName=self._table, Key=_marshal(key), ConsistentRead=True
            )
        except (BotoCoreError, ClientError, TimeoutError) as exc:
            raise IdempotencyUnavailable("DynamoDB idempotency authority unavailable") from exc
        item = response.get("Item")
        return _unmarshal(item) if item else None

    def _lookup(
        self, lookup: FormalRunLookup, *, kind: str = "authority"
    ) -> tuple[dict[str, object] | None, FormalRunIdentity | None]:
        matches: list[tuple[dict[str, object], FormalRunIdentity]] = []
        for identity in (lookup.primary_identity, *lookup.candidate_identities):
            item = self._get(self._key(identity, kind))
            if item is not None:
                matches.append((item, identity))
        if len(matches) > 1:
            raise IdempotencyUnavailable("ambiguous retained HMAC authority")
        return matches[0] if matches else (None, None)

    @staticmethod
    def _receipt(raw: object) -> FormalRunReceipt | None:
        try:
            return FormalRunReceipt(**json.loads(raw)) if isinstance(raw, str) else None
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise IdempotencyUnavailable("stored receipt is invalid") from exc

    @staticmethod
    def _terminal(item: Mapping[str, object]) -> TerminalSafeResponse:
        required = (
            "terminal_error_code", "terminal_http_status",
            "terminal_response_schema_version", "terminal_safe_response_body",
            "terminal_replay_headers", "terminal_response_digest", "terminal_at", "expires_at",
        )
        if any(field not in item for field in required):
            raise IdempotencyUnavailable("stored terminal response is incomplete")
        disposition = item.get("disposition")
        if disposition not in {None, "created", "fresh-created", "reused", "relocalized"}:
            raise IdempotencyUnavailable("stored terminal disposition is invalid")
        authority_fields = (
            "receipt_body", "receipt_id", "question_id", "job_id", "result_id",
            "operation_id", "outbox_state", "dispatch_state", "provider_operation_id",
            "cost_policy_version", "cost_policy_digest", "reservation_id",
            "max_reserved_cost", "settlement_state", "reconciliation_state",
        )
        if disposition is None and any(field in item for field in authority_fields):
            raise IdempotencyUnavailable("stored pre-bind terminal authority is invalid")
        if disposition in {"created", "fresh-created"} and (
            item.get("outbox_state") != "cancelled"
            or item.get("dispatch_state") != "not_dispatched"
            or "reservation_id" not in item
            or item.get("settlement_state") != "released"
            or item.get("reconciliation_state") != "reconciled"
        ):
            raise IdempotencyUnavailable("stored terminal cost settlement is invalid")
        if disposition in {"reused", "relocalized"} and (
            item.get("outbox_state") != "none"
            or item.get("dispatch_state") != "not_dispatched"
            or any(field in item for field in ("reservation_id", "settlement_state", "reconciliation_state"))
        ):
            raise IdempotencyUnavailable("stored provider-free terminal state is invalid")
        try:
            response = TerminalSafeResponse(
                status=int(item["terminal_http_status"]),
                code=str(item["terminal_error_code"]),
                schema_version=str(item["terminal_response_schema_version"]),
                body=json.loads(str(item["terminal_safe_response_body"])),
                replay_headers=json.loads(str(item["terminal_replay_headers"])),
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise IdempotencyUnavailable("stored terminal response is invalid") from exc
        if not hmac.compare_digest(response.digest(), str(item["terminal_response_digest"])):
            raise IdempotencyUnavailable("stored terminal response digest mismatch")
        return response

    def acquire(
        self, *, lookup: FormalRunLookup, now: datetime, lease_seconds: int
    ) -> AcquireResult:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now_ts = _timestamp(now)
        accepted_epochs = accepted_acquisition_epochs(now)
        item, matched = self._lookup(lookup)
        if item is None:
            tombstone, tombstone_identity = self._lookup(lookup, kind="tombstone")
            if tombstone is not None:
                assert tombstone_identity is not None
                if tombstone.get("key_epoch") != lookup.parsed_key.epoch:
                    raise IdempotencyUnavailable("stored tombstone epoch is invalid")
                retain = tombstone.get("retain_until")
                if (
                    retain is not None
                    and _stored_decimal(retain, "tombstone retain_until") <= now_ts
                    and lookup.parsed_key.epoch not in accepted_epochs
                ):
                    try:
                        self._client.delete_item(
                            TableName=self._table,
                            Key=_marshal(self._key(tombstone_identity, "tombstone")),
                            ConditionExpression="#epoch=:epoch",
                            ExpressionAttributeNames={"#epoch": "key_epoch"},
                            ExpressionAttributeValues=_marshal({":epoch": lookup.parsed_key.epoch}),
                        )
                    except ClientError as exc:
                        if not _conditional(exc):
                            raise IdempotencyUnavailable("DynamoDB tombstone GC unavailable") from exc
                    except (BotoCoreError, TimeoutError) as exc:
                        raise IdempotencyUnavailable(
                            "DynamoDB tombstone GC unavailable"
                        ) from exc
                return AcquireResult("key_unavailable")
            if lookup.parsed_key.epoch not in accepted_epochs:
                return AcquireResult("key_unavailable")
            identity = lookup.primary_identity
            new_item = {
                **self._key(identity),
                "entity_type": "formal_run_authority",
                "namespace": identity.namespace,
                "scope_locator": identity.scope_locator,
                "caller_key_id": identity.caller_scope_hmac.key_id,
                "caller_scope_hmac": identity.caller_scope_hmac.digest,
                "key_key_id": identity.key_hmac.key_id,
                "key_hmac": identity.key_hmac.digest,
                "key_epoch": lookup.parsed_key.epoch,
                "fingerprint_key_id": lookup.primary_fingerprint.key_id,
                "request_fingerprint_hmac": lookup.primary_fingerprint.digest,
                "fingerprint_version": "analysis-question/v1",
                "state": "acquired",
                "owner_fencing_token": 1,
                "lease_expires_at": now_ts + lease_seconds,
                "created_at": now_ts,
                "updated_at": now_ts,
            }
            try:
                self._client.put_item(
                    TableName=self._table,
                    Item=_marshal(new_item),
                    ConditionExpression="attribute_not_exists(#pk) AND attribute_not_exists(#sk)",
                    ExpressionAttributeNames={"#pk": "pk", "#sk": "sk"},
                )
                return AcquireResult("owner", fencing_token=1, authority_identity=identity)
            except ClientError as exc:
                if _conditional(exc):
                    winner, winner_identity = self._lookup(lookup)
                    if winner is None or winner_identity is None:
                        raise IdempotencyUnavailable(
                            "conditional acquisition winner was not durably readable"
                        ) from exc
                    item, matched = winner, winner_identity
                else:
                    raise IdempotencyUnavailable(
                        "DynamoDB idempotency acquisition unavailable"
                    ) from exc
            except (BotoCoreError, TimeoutError) as exc:
                raise IdempotencyUnavailable("DynamoDB idempotency acquisition unavailable") from exc

        assert matched is not None
        fingerprints = (lookup.primary_fingerprint, *lookup.candidate_fingerprints)
        if not any(
            item.get("fingerprint_key_id") == candidate.key_id
            and isinstance(item.get("request_fingerprint_hmac"), str)
            and hmac.compare_digest(str(item["request_fingerprint_hmac"]), candidate.digest)
            for candidate in fingerprints
        ):
            return AcquireResult("conflict")
        state = item.get("state")
        if state in {"bound", "execution_uncertain"}:
            receipt = self._receipt(item.get("receipt_body"))
            if receipt is None:
                raise IdempotencyUnavailable("replay row has no receipt")
            return AcquireResult("replay", receipt=receipt, authority_identity=matched)
        if state == "terminal_failed":
            expires = item.get("expires_at")
            if expires is not None and _stored_decimal(expires, "terminal expires_at") <= now_ts:
                tombstone = {
                    **self._key(matched, "tombstone"),
                    "entity_type": "formal_run_tombstone",
                    "key_epoch": item.get("key_epoch"),
                }
                try:
                    self._client.transact_write_items(TransactItems=[
                        {"Put": {
                            "TableName": self._table, "Item": _marshal(tombstone),
                            "ConditionExpression": "attribute_not_exists(#pk)",
                            "ExpressionAttributeNames": {"#pk": "pk"},
                        }},
                        {"Delete": {
                            "TableName": self._table, "Key": _marshal(self._key(matched)),
                            "ConditionExpression": "#state=:terminal AND #expires<=:now",
                            "ExpressionAttributeNames": {
                                "#state": "state", "#expires": "expires_at"
                            },
                            "ExpressionAttributeValues": _marshal({
                                ":terminal": "terminal_failed", ":now": now_ts
                            }),
                        }},
                    ])
                except ClientError as exc:
                    if not _conditional(exc):
                        raise IdempotencyUnavailable("DynamoDB tombstone transition unavailable") from exc
                except (BotoCoreError, TimeoutError) as exc:
                    raise IdempotencyUnavailable("DynamoDB tombstone transition unavailable") from exc
                return AcquireResult("key_unavailable")
            return AcquireResult(
                "terminal_replay", terminal_response=self._terminal(item),
                authority_identity=matched,
            )
        lease = item.get("lease_expires_at")
        if lease is not None and _stored_decimal(lease, "lease expiry") > now_ts:
            return AcquireResult("in_progress", authority_identity=matched)
        token = item.get("owner_fencing_token")
        if state != "acquired":
            raise IdempotencyUnavailable("unknown idempotency state")
        stored_token = _stored_decimal(token, "fencing token", integral=True)
        if stored_token < 1:
            raise IdempotencyUnavailable("stored fencing token is invalid")
        next_token = int(stored_token) + 1
        try:
            self._client.update_item(
                TableName=self._table,
                Key=_marshal(self._key(matched)),
                UpdateExpression="SET #token=:next, #lease=:lease, #updated=:now",
                ConditionExpression=(
                    "#state=:acquired AND #token=:old AND "
                    "(attribute_not_exists(#lease) OR #lease<=:now)"
                ),
                ExpressionAttributeNames={
                    "#state": "state", "#token": "owner_fencing_token",
                    "#lease": "lease_expires_at", "#updated": "updated_at",
                },
                ExpressionAttributeValues=_marshal({
                    ":acquired": "acquired", ":old": stored_token, ":next": next_token,
                    ":lease": now_ts + lease_seconds, ":now": now_ts,
                }),
            )
        except ClientError as exc:
            if _conditional(exc):
                return AcquireResult("in_progress", authority_identity=matched)
            raise IdempotencyUnavailable("DynamoDB fenced takeover unavailable") from exc
        except (BotoCoreError, TimeoutError) as exc:
            raise IdempotencyUnavailable("DynamoDB fenced takeover unavailable") from exc
        return AcquireResult("owner", fencing_token=next_token, authority_identity=matched)

    @staticmethod
    def _validate_bind(
        receipt: FormalRunReceipt, operation_id: str, outbox_state: str,
        dispatch_state: str, reservation_id: str | None, max_reserved_cost: str | None,
        provider_operation_id: str | None, cost_policy_version: str | None,
        cost_policy_digest: str | None, settlement_state: str | None,
        reconciliation_state: str | None,
    ) -> bool:
        chargeable = receipt.disposition in {"created", "fresh-created"}
        if _STRICT_ID.fullmatch(operation_id) is None or receipt.state != "accepted":
            raise ValueError("invalid operation or receipt")
        if chargeable:
            try:
                reserved = Decimal(max_reserved_cost or "")
            except InvalidOperation as exc:
                raise ValueError("maximum reserved cost must be a positive finite decimal") from exc
            if (
                reservation_id is None or not reserved.is_finite() or reserved <= 0
                or outbox_state != "pending" or dispatch_state != "not_dispatched"
                or provider_operation_id is None or cost_policy_version is None
                or cost_policy_digest is None or settlement_state != "reserved"
                or reconciliation_state != "pending"
                or _STRICT_ID.fullmatch(reservation_id) is None
                or _STRICT_ID.fullmatch(provider_operation_id) is None
                or _STRICT_ID.fullmatch(cost_policy_version) is None
                or _HEX_64.fullmatch(cost_policy_digest) is None
            ):
                raise ValueError("chargeable disposition requires a policy-bound reserved outbox")
        elif any(value is not None for value in (
            reservation_id, max_reserved_cost, provider_operation_id, cost_policy_version,
            cost_policy_digest, settlement_state, reconciliation_state,
        )) or outbox_state != "none" or dispatch_state != "not_dispatched":
            raise ValueError("provider-free disposition forbids reservation and dispatch")
        return chargeable

    def bind(
        self, *, identity: FormalRunIdentity, fencing_token: int,
        receipt: FormalRunReceipt, operation_id: str, outbox_state: str,
        dispatch_state: str, reservation_id: str | None, max_reserved_cost: str | None,
        now: datetime, provider_operation_id: str | None = None,
        cost_policy_version: str | None = None, cost_policy_digest: str | None = None,
        settlement_state: str | None = None, reconciliation_state: str | None = None,
    ) -> None:
        now_ts = _timestamp(now)
        chargeable = self._validate_bind(
            receipt, operation_id, outbox_state, dispatch_state, reservation_id,
            max_reserved_cost, provider_operation_id, cost_policy_version,
            cost_policy_digest, settlement_state, reconciliation_state,
        )
        values: dict[str, object] = {
            ":bound": "bound", ":receipt": json.dumps(
                receipt.public_body(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ),
            ":receipt_id": receipt.receipt_id, ":question_id": receipt.question_id,
            ":job_id": receipt.job_id, ":disposition": receipt.disposition,
            ":locale": receipt.locale, ":operation_id": operation_id,
            ":outbox": outbox_state, ":dispatch": dispatch_state, ":now": now_ts,
            ":token": fencing_token, ":acquired": "acquired",
        }
        sets = [
            "#state=:bound", "receipt_body=:receipt", "receipt_id=:receipt_id",
            "question_id=:question_id", "job_id=:job_id", "disposition=:disposition",
            "locale=:locale", "operation_id=:operation_id", "outbox_state=:outbox",
            "dispatch_state=:dispatch", "updated_at=:now",
        ]
        optional = {
            "result_id": receipt.result_id, "provider_operation_id": provider_operation_id,
            "cost_policy_version": cost_policy_version, "cost_policy_digest": cost_policy_digest,
            "reservation_id": reservation_id, "max_reserved_cost": max_reserved_cost,
            "settlement_state": settlement_state, "reconciliation_state": reconciliation_state,
        }
        for field, value in optional.items():
            if value is not None:
                placeholder = ":" + field
                sets.append(f"{field}={placeholder}")
                values[placeholder] = value
        update = {
            "TableName": self._table,
            "Key": _marshal(self._key(identity)),
            "UpdateExpression": "SET " + ", ".join(sets) + " REMOVE lease_expires_at",
            "ConditionExpression": "#state=:acquired AND owner_fencing_token=:token",
            "ExpressionAttributeNames": {"#state": "state"},
            "ExpressionAttributeValues": _marshal(values),
        }
        actions: list[dict[str, object]] = [{"Update": update}]
        if chargeable:
            assert reservation_id is not None and provider_operation_id is not None
            for kind, value in (
                ("operation", operation_id), ("job", receipt.job_id),
                ("reservation", reservation_id), ("provider_operation", provider_operation_id),
            ):
                guard = {
                    **self._guard_key(identity, kind, value),
                    "entity_type": "formal_run_unique_guard",
                    "namespace": identity.namespace, "scope_locator": identity.scope_locator,
                    "guard_kind": kind, "guard_value": value,
                    "authority_pk": self._pk(identity),
                }
                actions.append({"Put": {
                    "TableName": self._table, "Item": _marshal(guard),
                    "ConditionExpression": "attribute_not_exists(#pk)",
                    "ExpressionAttributeNames": {"#pk": "pk"},
                }})
        try:
            self._client.transact_write_items(TransactItems=actions)
        except ClientError as exc:
            if _conditional(exc):
                current = self._get(self._key(identity))
                if current is not None and (
                    current.get("state") != "acquired"
                    or _stored_decimal(
                        current.get("owner_fencing_token"), "fencing token", integral=True
                    ) != Decimal(fencing_token)
                ):
                    raise StaleFencingToken("stale or non-owning fencing token") from exc
                raise ValueError("formal-run identity is already bound in this caller scope") from exc
            raise IdempotencyUnavailable("DynamoDB bind transaction unavailable") from exc
        except (BotoCoreError, TimeoutError) as exc:
            raise IdempotencyUnavailable("DynamoDB bind transaction unavailable") from exc

    def claim_dispatch(
        self, *, identity: FormalRunIdentity, fencing_token: int, now: datetime
    ) -> str:
        item = self._get(self._key(identity))
        provider = item.get("provider_operation_id") if item else None
        if not isinstance(provider, str):
            raise ValueError("chargeable provider operation is not bound")
        try:
            self._client.update_item(
                TableName=self._table, Key=_marshal(self._key(identity)),
                UpdateExpression="SET outbox_state=:claimed, dispatch_state=:possible, updated_at=:now",
                ConditionExpression=(
                    "#state=:bound AND owner_fencing_token=:token AND "
                    "(disposition=:created OR disposition=:fresh) AND "
                    "attribute_exists(reservation_id) AND outbox_state=:pending "
                    "AND dispatch_state=:not_dispatched"
                ),
                ExpressionAttributeNames={"#state": "state"},
                ExpressionAttributeValues=_marshal({
                    ":claimed": "claimed", ":possible": "possibly_dispatched",
                    ":now": _timestamp(now), ":bound": "bound", ":token": fencing_token,
                    ":created": "created", ":fresh": "fresh-created", ":pending": "pending",
                    ":not_dispatched": "not_dispatched",
                }),
            )
        except ClientError as exc:
            if _conditional(exc):
                raise IdempotencyInProgress("dispatch is already claimed or fencing token is stale") from exc
            raise IdempotencyUnavailable("DynamoDB dispatch claim unavailable") from exc
        except (BotoCoreError, TimeoutError) as exc:
            raise IdempotencyUnavailable("DynamoDB dispatch claim unavailable") from exc
        return provider

    def mark_execution_uncertain(
        self, *, identity: FormalRunIdentity, fencing_token: int, now: datetime
    ) -> None:
        item = self._get(self._key(identity))
        receipt = self._receipt(item.get("receipt_body") if item else None)
        if receipt is None or receipt.state != "accepted":
            raise IdempotencyUnavailable("bound row has no accepted receipt")
        uncertain = replace(receipt, state="execution_uncertain")
        try:
            self._client.update_item(
                TableName=self._table, Key=_marshal(self._key(identity)),
                UpdateExpression=(
                    "SET #state=:uncertain, receipt_body=:receipt, "
                    "dispatch_state=:dispatch, updated_at=:now REMOVE lease_expires_at"
                ),
                ConditionExpression=(
                    "#state=:bound AND owner_fencing_token=:token AND "
                    "(disposition=:created OR disposition=:fresh) AND "
                    "attribute_exists(reservation_id) AND outbox_state=:claimed "
                    "AND dispatch_state=:possible"
                ),
                ExpressionAttributeNames={"#state": "state"},
                ExpressionAttributeValues=_marshal({
                    ":uncertain": "execution_uncertain", ":receipt": json.dumps(
                        uncertain.public_body(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ),
                    ":dispatch": "uncertain", ":now": _timestamp(now), ":bound": "bound",
                    ":token": fencing_token, ":created": "created", ":fresh": "fresh-created",
                    ":claimed": "claimed", ":possible": "possibly_dispatched",
                }),
            )
        except ClientError as exc:
            if _conditional(exc):
                raise IdempotencyInProgress("dispatch was not authoritatively claimed") from exc
            raise IdempotencyUnavailable("DynamoDB uncertain transition unavailable") from exc
        except (BotoCoreError, TimeoutError) as exc:
            raise IdempotencyUnavailable("DynamoDB uncertain transition unavailable") from exc

    def fail_terminal(
        self, *, identity: FormalRunIdentity, fencing_token: int,
        response: TerminalSafeResponse, now: datetime, expires_at: datetime,
    ) -> None:
        now_ts, expires_ts = _timestamp(now), _timestamp(expires_at)
        if expires_ts - now_ts < 86_400:
            raise ValueError("terminal replay SLA must be at least 24 hours")
        current = self._get(self._key(identity))
        if current is None or _stored_decimal(
            current.get("owner_fencing_token"), "fencing token", integral=True
        ) != Decimal(fencing_token):
            raise StaleFencingToken("stale owner or request may already have been dispatched")
        state, disposition = current.get("state"), current.get("disposition")
        condition = "#state=:acquired"
        settlement = ""
        if state == "bound" and disposition in {"created", "fresh-created"}:
            if (
                current.get("dispatch_state") != "not_dispatched"
                or current.get("outbox_state") != "pending"
                or "reservation_id" not in current
                or current.get("settlement_state") != "reserved"
                or current.get("reconciliation_state") != "pending"
            ):
                raise StaleFencingToken("stale owner or request may already have been dispatched")
            condition = (
                "#state=:bound AND dispatch_state=:not_dispatched AND "
                "(disposition=:created OR disposition=:fresh) AND outbox_state=:pending "
                "AND attribute_exists(reservation_id) AND settlement_state=:reserved "
                "AND reconciliation_state=:pending_reconciliation"
            )
            settlement = (
                ", outbox_state=:cancelled, settlement_state=:released, "
                "reconciliation_state=:reconciled"
            )
        elif state == "bound" and disposition in {"reused", "relocalized"}:
            if (
                current.get("dispatch_state") != "not_dispatched"
                or current.get("outbox_state") != "none"
                or any(field in current for field in (
                    "reservation_id", "settlement_state", "reconciliation_state"
                ))
            ):
                raise StaleFencingToken("stale owner or request may already have been dispatched")
            condition = (
                "#state=:bound AND dispatch_state=:not_dispatched AND "
                "(disposition=:reused OR disposition=:relocalized) AND outbox_state=:none "
                "AND attribute_not_exists(reservation_id) "
                "AND attribute_not_exists(settlement_state) "
                "AND attribute_not_exists(reconciliation_state)"
            )
        elif state != "acquired":
            raise StaleFencingToken("stale owner or request may already have been dispatched")
        update_expression = (
            "SET #state=:terminal, terminal_error_code=:code, "
            "terminal_http_status=:status, terminal_response_schema_version=:schema, "
            "terminal_safe_response_body=:body, terminal_replay_headers=:headers, "
            "terminal_response_digest=:digest, terminal_at=:now, expires_at=:expires, "
            f"updated_at=:now{settlement} REMOVE lease_expires_at"
        )
        condition_expression = f"owner_fencing_token=:token AND ({condition})"
        all_values: dict[str, object] = {
            ":terminal": "terminal_failed", ":code": response.code,
            ":status": response.status, ":schema": response.schema_version,
            ":body": response.canonical_body(),
            ":headers": json.dumps(
                dict(response.replay_headers), sort_keys=True, separators=(",", ":")
            ),
            ":digest": response.digest(), ":now": now_ts, ":expires": expires_ts,
            ":token": fencing_token, ":acquired": "acquired", ":bound": "bound",
            ":not_dispatched": "not_dispatched", ":created": "created",
            ":fresh": "fresh-created", ":pending": "pending", ":reserved": "reserved",
            ":pending_reconciliation": "pending", ":reused": "reused",
            ":relocalized": "relocalized", ":none": "none",
            ":cancelled": "cancelled", ":released": "released",
            ":reconciled": "reconciled",
        }
        used_placeholders = set(re.findall(r":[A-Za-z_]+", update_expression + condition_expression))
        try:
            self._client.update_item(
                TableName=self._table, Key=_marshal(self._key(identity)),
                UpdateExpression=update_expression,
                ConditionExpression=condition_expression,
                ExpressionAttributeNames={"#state": "state"},
                ExpressionAttributeValues=_marshal({
                    key: value for key, value in all_values.items()
                    if key in used_placeholders
                }),
            )
        except ClientError as exc:
            if _conditional(exc):
                raise StaleFencingToken("stale owner or request may already have been dispatched") from exc
            raise IdempotencyUnavailable("DynamoDB terminal transition unavailable") from exc
        except (BotoCoreError, TimeoutError) as exc:
            raise IdempotencyUnavailable("DynamoDB terminal transition unavailable") from exc

    def tombstone(
        self, *, identity: FormalRunIdentity, now: datetime,
        retain_until: datetime | None,
    ) -> None:
        item = self._get(self._key(identity))
        now_ts = _timestamp(now)
        if (
            item is None or item.get("state") != "terminal_failed"
            or "expires_at" not in item
            or _stored_decimal(item["expires_at"], "terminal expires_at") > now_ts
        ):
            raise ValueError("only an expired terminal record may be tombstoned")
        tombstone: dict[str, object] = {
            **self._key(identity, "tombstone"),
            "entity_type": "formal_run_tombstone", "key_epoch": item["key_epoch"],
        }
        if retain_until is not None:
            tombstone["retain_until"] = _timestamp(retain_until)
        try:
            self._client.transact_write_items(TransactItems=[
                {"Put": {
                    "TableName": self._table, "Item": _marshal(tombstone),
                    "ConditionExpression": "attribute_not_exists(#pk)",
                    "ExpressionAttributeNames": {"#pk": "pk"},
                }},
                {"Delete": {
                    "TableName": self._table, "Key": _marshal(self._key(identity)),
                    "ConditionExpression": "#state=:terminal AND expires_at<=:now",
                    "ExpressionAttributeNames": {"#state": "state"},
                    "ExpressionAttributeValues": _marshal({
                        ":terminal": "terminal_failed", ":now": now_ts
                    }),
                }},
            ])
        except ClientError as exc:
            if _conditional(exc):
                raise ValueError("only an expired terminal record may be tombstoned") from exc
            raise IdempotencyUnavailable("DynamoDB tombstone transition unavailable") from exc
        except (BotoCoreError, TimeoutError) as exc:
            raise IdempotencyUnavailable("DynamoDB tombstone transition unavailable") from exc
