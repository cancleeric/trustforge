"""Atomic multi-angle admission storage contract (#896).

Production admission is one DynamoDB transaction. SQLite is local parity only;
it is never a production fallback. Worker consumption and retention are #884
and #885 respectively.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
import os
from botocore.exceptions import ClientError
from typing import Any, Protocol

ANGLE_MODES = ("risk", "sentiment", "fundamentals", "news", "catalyst")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_HASH_RE = re.compile(r"^[a-f0-9]{64}$")


class BatchStoreBackendError(RuntimeError):
    """Shared transaction authority unavailable; callers must fail closed."""


class BatchStoreIntegrityError(RuntimeError):
    """Durable replay manifest is incomplete or internally inconsistent."""


class BatchConflictError(RuntimeError):
    """Caller/idempotency key is already bound to a different request."""


@dataclass(frozen=True)
class AtomicBatchRequest:
    batch_id: str
    caller_hash: str
    idempotency_key_hash: str
    request_fingerprint: str
    coin: str
    snapshot_id: str
    day: str
    batch_cost_usd: Decimal
    config_version: str
    created_at: int

    def validate(self) -> None:
        for name, value in (
            ("batch_id", self.batch_id),
            ("coin", self.coin),
            ("snapshot_id", self.snapshot_id),
            ("config_version", self.config_version),
        ):
            if not isinstance(value, str) or not _ID_RE.fullmatch(value):
                raise ValueError(f"invalid {name}")
        for name, value in (
            ("caller_hash", self.caller_hash),
            ("idempotency_key_hash", self.idempotency_key_hash),
            ("request_fingerprint", self.request_fingerprint),
        ):
            if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
                raise ValueError(f"invalid {name}")
        try:
            parsed_day = date.fromisoformat(self.day)
        except (TypeError, ValueError) as exc:
            raise ValueError("day must be an ISO UTC date") from exc
        if parsed_day.isoformat() != self.day:
            raise ValueError("day must be canonical YYYY-MM-DD")
        if not isinstance(self.created_at, int) or self.created_at <= 0:
            raise ValueError("created_at must be a positive epoch integer")
        try:
            created_day = datetime.fromtimestamp(self.created_at, UTC).date()
        except (OSError, OverflowError, ValueError) as exc:
            raise ValueError("created_at is outside the supported UTC range") from exc
        if created_day != parsed_day:
            raise ValueError("day must match created_at UTC date")
        try:
            finite = math.isfinite(float(self.batch_cost_usd))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("batch_cost_usd must be a finite Decimal") from exc
        if (
            not isinstance(self.batch_cost_usd, Decimal)
            or not finite
            or self.batch_cost_usd <= 0
            or self.batch_cost_usd > Decimal(1000)
            or self.batch_cost_usd.as_tuple().exponent < -6
        ):
            raise ValueError("batch_cost_usd is outside accepted bounds")


@dataclass(frozen=True)
class AtomicBatchResult:
    admitted: bool
    replayed: bool
    batch_id: str
    job_ids: tuple[str, ...]
    snapshot_id: str | None = None


@dataclass(frozen=True)
class BatchSettlementResult:
    batch_id: str
    settled: bool
    replayed: bool
    actual_cost_usd: Decimal
    released_usd: Decimal
    synthesis_claimed: bool


class AtomicMultiAngleBatchStore(Protocol):
    def create_batch(self, request: AtomicBatchRequest) -> AtomicBatchResult: ...

    def claim_allocation(
        self, *, batch_id: str, mode: str, job_id: str, owner_token: str,
        config_version: str, expected_amount_usd: Decimal,
    ) -> bool: ...

    def find_replay(self, request: AtomicBatchRequest) -> AtomicBatchResult | None: ...

    def consume_call_slot(
        self, *, batch_id: str, mode: str, job_id: str, owner_token: str,
        config_version: str, expected_amount_usd: Decimal, slot: str,
    ) -> bool: ...

    def record_call_cost(
        self, *, batch_id: str, mode: str, job_id: str, owner_token: str,
        slot: str, accounting_token: str, ledger_receipt: str,
        actual_cost_usd: Decimal, tokens_in: int, tokens_out: int,
    ) -> bool: ...

    def record_job_terminal(
        self, *, batch_id: str, mode: str, job_id: str, owner_token: str,
        state: str,
    ) -> bool: ...

    def settle_batch(self, *, batch_id: str) -> BatchSettlementResult: ...

    def reconcile_stale_batches(
        self, *, stale_before: int, apply: bool = False,
    ) -> dict[str, Any]: ...

    def claim_synthesis(
        self, *, batch_id: str, owner_token: str, stale_before: int,
    ) -> bool: ...

    def complete_synthesis(self, *, batch_id: str, owner_token: str) -> bool: ...

    def call_accounting_state(
        self, *, batch_id: str, mode: str, job_id: str, owner_token: str,
    ) -> dict[str, str]: ...

    def cancel_call_slot(
        self, *, batch_id: str, mode: str, job_id: str, owner_token: str,
        slot: str,
    ) -> bool: ...


def _job_id(batch_id: str, mode: str) -> str:
    return hashlib.sha256(f"{batch_id}:{mode}".encode()).hexdigest()[:32]


def _job_ids(batch_id: str) -> tuple[str, ...]:
    return tuple(_job_id(batch_id, mode) for mode in ANGLE_MODES)


class DynamoDBAtomicMultiAngleBatchStore:
    """Injected low-level DynamoDB client/table production adapter."""

    def __init__(self, *, client: Any, table_name: str) -> None:
        if not table_name or len(table_name) > 255:
            raise ValueError("valid table_name is required")
        self._client = client
        self._table_name = table_name

    @staticmethod
    def _s(value: str) -> dict[str, str]:
        return {"S": value}

    @staticmethod
    def _n(value: Decimal | int) -> dict[str, str]:
        return {"N": str(value)}

    def _request_key(self, request: AtomicBatchRequest) -> dict[str, dict[str, str]]:
        return {
            "pk": self._s(f"REQUEST#{request.caller_hash}"),
            "sk": self._s(f"IDEMPOTENCY#{request.idempotency_key_hash}"),
        }

    def _consistent_get(self, key: dict[str, Any]) -> dict[str, Any] | None:
        try:
            return self._client.get_item(
                TableName=self._table_name, Key=key, ConsistentRead=True
            ).get("Item")
        except Exception as exc:
            raise BatchStoreBackendError("cannot read atomic batch state") from exc

    def _budget_denied_after_consistent_read(
        self, request: AtomicBatchRequest
    ) -> AtomicBatchResult:
        budget = self._consistent_get(
            {
                "pk": self._s(f"BUDGET#{request.day}"),
                "sk": self._s("COUNTER"),
            }
        )
        if not budget:
            raise BatchStoreBackendError(
                "authoritative budget counter is not bootstrapped"
            )
        version = budget.get("config_version", {}).get("S")
        if version != request.config_version:
            raise BatchStoreBackendError("authoritative budget config version mismatch")
        try:
            remaining = Decimal(budget["remaining_usd"]["N"])
        except (KeyError, TypeError, ArithmeticError) as exc:
            raise BatchStoreIntegrityError(
                "authoritative budget counter is malformed"
            ) from exc
        if remaining < request.batch_cost_usd:
            return AtomicBatchResult(False, False, request.batch_id, ())
        raise BatchStoreIntegrityError(
            "budget transaction was canceled although authoritative capacity exists"
        )

    def _read_replay(self, request: AtomicBatchRequest) -> AtomicBatchResult | None:
        item = self._consistent_get(self._request_key(request))
        if not item:
            return None
        fingerprint = item.get("request_fingerprint", {}).get("S")
        if fingerprint != request.request_fingerprint:
            raise BatchConflictError(
                "caller idempotency key is bound to a different request fingerprint"
            )
        batch_id = item.get("batch_id", {}).get("S")
        if not batch_id or not _ID_RE.fullmatch(batch_id):
            raise BatchStoreIntegrityError("request points to an invalid batch")
        expected = [
            {"pk": self._s(f"BATCH#{batch_id}"), "sk": self._s("META")},
            *[
                {"pk": self._s(f"BATCH#{batch_id}"), "sk": self._s(f"ALLOCATION#{mode}")}
                for mode in ANGLE_MODES
            ],
            *[
                {"pk": self._s(f"JOB#{job_id}"), "sk": self._s("META")}
                for job_id in _job_ids(batch_id)
            ],
        ]
        try:
            response = self._client.batch_get_item(
                RequestItems={
                    self._table_name: {"Keys": expected, "ConsistentRead": True}
                }
            )
        except Exception as exc:
            raise BatchStoreBackendError("cannot verify replay manifest") from exc
        if response.get("UnprocessedKeys"):
            raise BatchStoreBackendError("replay manifest read was not completed")
        items = response.get("Responses", {}).get(self._table_name, [])
        observed = {
            (entry.get("pk", {}).get("S"), entry.get("sk", {}).get("S")): entry
            for entry in items
        }
        wanted = {(key["pk"]["S"], key["sk"]["S"]) for key in expected}
        if set(observed) != wanted:
            raise BatchStoreIntegrityError("replay manifest is incomplete")
        batch = observed[(f"BATCH#{batch_id}", "META")]
        if (
            batch.get("request_fingerprint", {}).get("S") != request.request_fingerprint
            or batch.get("caller_hash", {}).get("S") != request.caller_hash
        ):
            raise BatchStoreIntegrityError("batch manifest identity is inconsistent")
        for mode, job_id in zip(ANGLE_MODES, _job_ids(batch_id), strict=True):
            allocation = observed[(f"BATCH#{batch_id}", f"ALLOCATION#{mode}")]
            job = observed[(f"JOB#{job_id}", "META")]
            if allocation.get("job_id", {}).get("S") != job_id:
                raise BatchStoreIntegrityError("allocation points to an unexpected job")
            if (
                job.get("batch_id", {}).get("S") != batch_id
                or job.get("mode", {}).get("S") != mode
            ):
                raise BatchStoreIntegrityError("job manifest identity is inconsistent")
        snapshot_id = batch.get("snapshot_id", {}).get("S")
        if not snapshot_id or not _ID_RE.fullmatch(snapshot_id):
            raise BatchStoreIntegrityError("batch snapshot identity is invalid")
        return AtomicBatchResult(
            True, True, batch_id, _job_ids(batch_id), snapshot_id
        )

    def find_replay(self, request: AtomicBatchRequest) -> AtomicBatchResult | None:
        request.validate()
        return self._read_replay(request)

    def ensure_budget_bootstrapped(self, *, day: str, config_version: str) -> None:
        """Idempotent 建立當天 BUDGET counter（補 #808/#809 daily bootstrap 缺失）。

        僅在明確設了 TRUSTFORGE_MULTI_ANGLE_DAILY_BUDGET_USD 時才 auto-bootstrap
        （明確授權當日預算）；否則保留既有 fail-closed 行為（不自動建立、讓上游
        決策）。create_batch 前以 attribute_not_exists 條件 idempotent 建立
        （已存在不覆蓋、不影響既有餘額）。
        """
        cap_env = os.getenv("TRUSTFORGE_MULTI_ANGLE_DAILY_BUDGET_USD", "").strip()
        if not cap_env:
            return  # 未明確授權 daily budget → 保留 fail-closed，不自動 bootstrap
        daily_cap = Decimal(cap_env)
        try:
            self._client.put_item(
                TableName=self._table_name,
                Item={
                    "pk": self._s(f"BUDGET#{day}"),
                    "sk": self._s("COUNTER"),
                    "remaining_usd": self._n(daily_cap),
                    "reserved_total": self._n(Decimal(0)),
                    "config_version": self._s(config_version),
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise

    def create_batch(self, request: AtomicBatchRequest) -> AtomicBatchResult:
        request.validate()
        replay = self._read_replay(request)
        if replay is not None:
            return replay
        self.ensure_budget_bootstrapped(
            day=request.day, config_version=request.config_version
        )

        tx: list[dict[str, Any]] = [
            {
                "Update": {
                    "TableName": self._table_name,
                    "Key": {
                        "pk": self._s(f"BUDGET#{request.day}"),
                        "sk": self._s("COUNTER"),
                    },
                    "UpdateExpression": (
                        "SET remaining_usd = remaining_usd - :cost, "
                        "reserved_total = if_not_exists(reserved_total, :zero) + :cost"
                    ),
                    "ConditionExpression": (
                        "attribute_exists(remaining_usd) AND config_version = :version "
                        "AND remaining_usd >= :cost"
                    ),
                    "ExpressionAttributeValues": {
                        ":zero": self._n(Decimal(0)),
                        ":cost": self._n(request.batch_cost_usd),
                        ":version": self._s(request.config_version),
                    },
                }
            },
            {
                "Put": {
                    "TableName": self._table_name,
                    "Item": {
                        **self._request_key(request),
                        "request_fingerprint": self._s(request.request_fingerprint),
                        "batch_id": self._s(request.batch_id),
                        "state": self._s("active"),
                        "created_at": self._n(request.created_at),
                    },
                    "ConditionExpression": "attribute_not_exists(pk)",
                }
            },
            {
                "Put": {
                    "TableName": self._table_name,
                    "Item": {
                        "pk": self._s(f"BATCH#{request.batch_id}"),
                        "sk": self._s("META"),
                        "request_fingerprint": self._s(request.request_fingerprint),
                        "caller_hash": self._s(request.caller_hash),
                        "coin": self._s(request.coin),
                        "snapshot_id": self._s(request.snapshot_id),
                        "state": self._s("reserved"),
                        "reserved_usd": self._n(request.batch_cost_usd),
                        "config_version": self._s(request.config_version),
                        "day": self._s(request.day),
                        "created_at": self._n(request.created_at),
                    },
                    "ConditionExpression": "attribute_not_exists(pk)",
                }
            },
        ]
        per_job = request.batch_cost_usd / Decimal(len(ANGLE_MODES))
        for mode, job_id in zip(ANGLE_MODES, _job_ids(request.batch_id), strict=True):
            tx.extend(
                (
                    {
                        "Put": {
                            "TableName": self._table_name,
                            "Item": {
                                "pk": self._s(f"BATCH#{request.batch_id}"),
                                "sk": self._s(f"ALLOCATION#{mode}"),
                                "job_id": self._s(job_id),
                                "amount_usd": self._n(per_job),
                                "state": self._s("reserved"),
                                "claim_extraction_slot": self._s("available"),
                                "evidence_narrative_slot": self._s("available"),
                            },
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    },
                    {
                        "Put": {
                            "TableName": self._table_name,
                            "Item": {
                                "pk": self._s(f"JOB#{job_id}"),
                                "sk": self._s("META"),
                                "batch_id": self._s(request.batch_id),
                                "mode": self._s(mode),
                                "coin": self._s(request.coin),
                                "snapshot_id": self._s(request.snapshot_id),
                                "state": self._s("pending"),
                                "created_at": self._n(request.created_at),
                            },
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    },
                )
            )
        try:
            self._client.transact_write_items(
                TransactItems=tx,
                ClientRequestToken=hashlib.sha256(
                    (
                        f"{request.caller_hash}:{request.idempotency_key_hash}:"
                        f"{request.request_fingerprint}"
                    ).encode()
                ).hexdigest()[:36],
            )
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if code != "TransactionCanceledException":
                raise BatchStoreBackendError("atomic multi-angle transaction failed") from exc
            reasons = getattr(exc, "response", {}).get("CancellationReasons")
            if not isinstance(reasons, list) or len(reasons) != len(tx):
                raise BatchStoreBackendError("transaction cancellation reason missing") from exc
            failed = [index for index, reason in enumerate(reasons) if reason.get("Code") != "None"]
            if 1 in failed and reasons[1].get("Code") == "ConditionalCheckFailed":
                replay = self._read_replay(request)
                if replay is None:
                    raise BatchStoreIntegrityError("request collision has no durable request") from exc
                return replay
            if failed == [0] and reasons[0].get("Code") == "ConditionalCheckFailed":
                return self._budget_denied_after_consistent_read(request)
            raise BatchStoreBackendError("atomic transaction integrity failure") from exc
        return AtomicBatchResult(
            True, False, request.batch_id, _job_ids(request.batch_id), request.snapshot_id
        )

    def claim_allocation(
        self, *, batch_id: str, mode: str, job_id: str, owner_token: str,
        config_version: str, expected_amount_usd: Decimal,
    ) -> bool:
        """Atomically bind one reserved allocation to its authority job.

        Re-entry after a daemon restart is idempotent only when both records
        already carry the expected claimed state and immutable identity.
        """
        if (
            not _ID_RE.fullmatch(batch_id)
            or mode not in ANGLE_MODES
            or job_id != _job_id(batch_id, mode)
            or not _ID_RE.fullmatch(owner_token)
            or not _ID_RE.fullmatch(config_version)
            or expected_amount_usd <= 0
        ):
            raise ValueError("invalid allocation identity")
        tx = [
            {
                "ConditionCheck": {
                    "TableName": self._table_name,
                    "Key": {
                        "pk": self._s(f"BATCH#{batch_id}"),
                        "sk": self._s("META"),
                    },
                    "ConditionExpression": "config_version=:version",
                    "ExpressionAttributeValues": {
                        ":version": self._s(config_version),
                    },
                }
            },
            {
                "Update": {
                    "TableName": self._table_name,
                    "Key": {
                        "pk": self._s(f"BATCH#{batch_id}"),
                        "sk": self._s(f"ALLOCATION#{mode}"),
                    },
                    "UpdateExpression": (
                        "SET #state=:claimed, owner_token=:owner, "
                        "claimed_at=if_not_exists(claimed_at,:claimed_at)"
                    ),
                    "ConditionExpression": (
                        "job_id=:job AND amount_usd=:amount AND "
                        "((#state=:reserved AND attribute_not_exists(owner_token)) "
                        "OR (#state=:claimed AND owner_token=:owner))"
                    ),
                    "ExpressionAttributeNames": {"#state": "state"},
                    "ExpressionAttributeValues": {
                        ":job": self._s(job_id),
                        ":reserved": self._s("reserved"),
                        ":claimed": self._s("claimed"),
                        ":owner": self._s(owner_token),
                        ":amount": self._n(expected_amount_usd),
                        ":claimed_at": self._n(int(datetime.now(UTC).timestamp())),
                    },
                }
            },
            {
                "Update": {
                    "TableName": self._table_name,
                    "Key": {"pk": self._s(f"JOB#{job_id}"), "sk": self._s("META")},
                    "UpdateExpression": "SET #state=:claimed, owner_token=:owner",
                    "ConditionExpression": (
                        "batch_id=:batch AND #mode=:mode "
                        "AND ((#state=:pending AND attribute_not_exists(owner_token)) "
                        "OR (#state=:claimed AND owner_token=:owner))"
                    ),
                    "ExpressionAttributeNames": {"#state": "state", "#mode": "mode"},
                    "ExpressionAttributeValues": {
                        ":batch": self._s(batch_id),
                        ":mode": self._s(mode),
                        ":pending": self._s("pending"),
                        ":claimed": self._s("claimed"),
                        ":owner": self._s(owner_token),
                    },
                }
            },
        ]
        try:
            self._client.transact_write_items(TransactItems=tx)
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if code == "TransactionCanceledException":
                raise BatchStoreIntegrityError(
                    "allocation claim identity/state condition failed"
                ) from exc
            raise BatchStoreBackendError("allocation authority unavailable") from exc
        return True

    def record_job_terminal(
        self, *, batch_id: str, mode: str, job_id: str, owner_token: str,
        state: str,
    ) -> bool:
        if state not in {"completed", "failed", "timeout"}:
            raise ValueError("invalid terminal state")
        key = {
            "pk": self._s(f"BATCH#{batch_id}"),
            "sk": self._s(f"ALLOCATION#{mode}"),
        }
        allocation = self._consistent_get(key)
        if not allocation:
            raise BatchStoreIntegrityError("terminal allocation is missing")
        if (
            allocation.get("job_id", {}).get("S") != job_id
            or allocation.get("owner_token", {}).get("S") != owner_token
        ):
            raise BatchStoreIntegrityError("terminal owner/identity mismatch")
        costs: list[Decimal] = []
        tokens_in = 0
        tokens_out = 0
        receipts: list[str] = []
        try:
            for slot in ("claim_extraction", "evidence_narrative"):
                if allocation[f"{slot}_slot"]["S"] != "consumed":
                    raise BatchStoreIntegrityError("terminal call slot was not consumed")
                costs.append(Decimal(allocation[f"{slot}_actual_cost_usd"]["N"]))
                tokens_in += int(allocation[f"{slot}_tokens_in"]["N"])
                tokens_out += int(allocation[f"{slot}_tokens_out"]["N"])
                receipts.append(allocation[f"{slot}_ledger_receipt"]["S"])
        except (KeyError, TypeError, ArithmeticError) as exc:
            raise BatchStoreIntegrityError(
                "terminal requires both durable call receipts"
            ) from exc
        actual = sum(costs, Decimal(0))
        receipt_set = json.dumps(receipts, separators=(",", ":"))
        tx = [
            {
                "Update": {
                    "TableName": self._table_name,
                    "Key": key,
                    "UpdateExpression": (
                        "SET #state=:terminal, actual_cost_usd=:cost, "
                        "tokens_in=:tin, tokens_out=:tout, ledger_receipts=:receipts"
                    ),
                    "ConditionExpression": (
                        "job_id=:job AND owner_token=:owner AND #state=:claimed"
                    ),
                    "ExpressionAttributeNames": {"#state": "state"},
                    "ExpressionAttributeValues": {
                        ":terminal": self._s(state), ":claimed": self._s("claimed"),
                        ":job": self._s(job_id), ":owner": self._s(owner_token),
                        ":cost": self._n(actual), ":tin": self._n(tokens_in),
                        ":tout": self._n(tokens_out), ":receipts": self._s(receipt_set),
                    },
                }
            },
            {
                "Update": {
                    "TableName": self._table_name,
                    "Key": {"pk": self._s(f"JOB#{job_id}"), "sk": self._s("META")},
                    "UpdateExpression": (
                        "SET #state=:terminal, actual_cost_usd=:cost, "
                        "tokens_in=:tin, tokens_out=:tout, ledger_receipts=:receipts"
                    ),
                    "ConditionExpression": (
                        "batch_id=:batch AND #mode=:mode AND owner_token=:owner "
                        "AND #state=:claimed"
                    ),
                    "ExpressionAttributeNames": {"#state": "state", "#mode": "mode"},
                    "ExpressionAttributeValues": {
                        ":terminal": self._s(state), ":claimed": self._s("claimed"),
                        ":batch": self._s(batch_id), ":mode": self._s(mode),
                        ":owner": self._s(owner_token), ":cost": self._n(actual),
                        ":tin": self._n(tokens_in), ":tout": self._n(tokens_out),
                        ":receipts": self._s(receipt_set),
                    },
                }
            },
        ]
        try:
            self._client.transact_write_items(TransactItems=tx)
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if code != "TransactionCanceledException":
                raise BatchStoreBackendError("terminal authority unavailable") from exc
            replay = self._consistent_get(key)
            expected = {
                "state": state, "owner_token": owner_token, "job_id": job_id,
                "actual_cost_usd": str(actual), "tokens_in": str(tokens_in),
                "tokens_out": str(tokens_out), "ledger_receipts": receipt_set,
            }
            for field, value in expected.items():
                kind = "N" if field in {"actual_cost_usd", "tokens_in", "tokens_out"} else "S"
                if replay is None or replay.get(field, {}).get(kind) != value:
                    raise BatchStoreIntegrityError("terminal replay conflict") from exc
        return True

    def settle_batch(self, *, batch_id: str) -> BatchSettlementResult:
        batch_key = {"pk": self._s(f"BATCH#{batch_id}"), "sk": self._s("META")}
        batch = self._consistent_get(batch_key)
        if not batch:
            raise BatchStoreIntegrityError("batch settlement manifest is missing")
        settlement_key = {
            "pk": self._s(f"BATCH#{batch_id}"), "sk": self._s("SETTLEMENT")
        }
        existing = self._consistent_get(settlement_key)
        if existing:
            try:
                return BatchSettlementResult(
                    batch_id, True, True,
                    Decimal(existing["actual_cost_usd"]["N"]),
                    Decimal(existing["released_usd"]["N"]),
                    existing["synthesis_claimed"]["BOOL"],
                )
            except (KeyError, TypeError, ArithmeticError) as exc:
                raise BatchStoreIntegrityError("settlement record is malformed") from exc
        allocations = [
            self._consistent_get({
                "pk": self._s(f"BATCH#{batch_id}"),
                "sk": self._s(f"ALLOCATION#{mode}"),
            })
            for mode in ANGLE_MODES
        ]
        if any(item is None for item in allocations):
            raise BatchStoreIntegrityError("settlement allocation manifest is incomplete")
        try:
            if any(
                item["state"]["S"] not in {"completed", "failed", "timeout"}
                for item in allocations
            ):
                return BatchSettlementResult(
                    batch_id, False, False, Decimal(0), Decimal(0), False
                )
            reserved = sum(
                (Decimal(item["amount_usd"]["N"]) for item in allocations), Decimal(0)
            )
            actual = sum(
                (Decimal(item["actual_cost_usd"]["N"]) for item in allocations), Decimal(0)
            )
            day = batch["day"]["S"]
            config_version = batch["config_version"]["S"]
            batch_reserved = Decimal(batch["reserved_usd"]["N"])
        except (KeyError, TypeError, ArithmeticError) as exc:
            raise BatchStoreIntegrityError("settlement authority data is malformed") from exc
        if reserved != batch_reserved or actual > reserved:
            raise BatchStoreIntegrityError("settlement costs exceed reserved authority")
        released = max(Decimal(0), reserved - actual)
        synthesize = all(
            item["state"]["S"] == "completed" for item in allocations
        )
        tx = [
            {"Update": {
                "TableName": self._table_name,
                "Key": {"pk": self._s(f"BUDGET#{day}"), "sk": self._s("COUNTER")},
                "UpdateExpression": (
                    "SET reserved_total=reserved_total-:reserved, "
                    "remaining_usd=remaining_usd+:released"
                ),
                "ConditionExpression": (
                    "reserved_total>=:reserved AND config_version=:version"
                ),
                "ExpressionAttributeValues": {
                    ":reserved": self._n(reserved), ":released": self._n(released),
                    ":version": self._s(config_version),
                },
            }},
            {"Update": {
                "TableName": self._table_name, "Key": batch_key,
                "UpdateExpression": "SET #state=:settled",
                "ConditionExpression": "#state=:reserved",
                "ExpressionAttributeNames": {"#state": "state"},
                "ExpressionAttributeValues": {
                    ":settled": self._s("settled"), ":reserved": self._s("reserved")
                },
            }},
            {"Put": {
                "TableName": self._table_name,
                "Item": {
                    **settlement_key, "actual_cost_usd": self._n(actual),
                    "released_usd": self._n(released),
                    "synthesis_claimed": {"BOOL": synthesize},
                },
                "ConditionExpression": "attribute_not_exists(pk)",
            }},
        ]
        if synthesize:
            tx.append({"Put": {
                "TableName": self._table_name,
                "Item": {
                    "pk": self._s(f"BATCH#{batch_id}"), "sk": self._s("SYNTHESIS"),
                    "state": self._s("available"),
                },
                "ConditionExpression": "attribute_not_exists(pk)",
            }})
        try:
            self._client.transact_write_items(TransactItems=tx)
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if code != "TransactionCanceledException":
                raise BatchStoreBackendError("batch settlement unavailable") from exc
            existing = self._consistent_get(settlement_key)
            if not existing:
                raise BatchStoreIntegrityError("batch settlement transaction conflicted") from exc
            if (
                existing.get("actual_cost_usd", {}).get("N") != str(actual)
                or existing.get("released_usd", {}).get("N") != str(released)
                or existing.get("synthesis_claimed", {}).get("BOOL") is not synthesize
            ):
                raise BatchStoreIntegrityError("batch settlement replay conflict") from exc
            return BatchSettlementResult(
                batch_id, True, True, actual, released, synthesize
            )
        return BatchSettlementResult(
            batch_id, True, False, actual, released, synthesize
        )

    def call_accounting_state(
        self, *, batch_id: str, mode: str, job_id: str, owner_token: str,
    ) -> dict[str, str]:
        item = self._consistent_get({
            "pk": self._s(f"BATCH#{batch_id}"),
            "sk": self._s(f"ALLOCATION#{mode}"),
        })
        if (
            not item or item.get("job_id", {}).get("S") != job_id
            or item.get("owner_token", {}).get("S") != owner_token
        ):
            raise BatchStoreIntegrityError("call accounting identity mismatch")
        result = {}
        for slot in ("claim_extraction", "evidence_narrative"):
            state = item.get(f"{slot}_slot", {}).get("S")
            if state == "available":
                result[slot] = "available"
            elif state == "consumed" and f"{slot}_ledger_receipt" in item:
                result[slot] = "receipted"
            elif state == "consumed":
                result[slot] = "uncertain"
            else:
                raise BatchStoreIntegrityError("call slot state is malformed")
        return result

    def claim_synthesis(
        self, *, batch_id: str, owner_token: str, stale_before: int,
    ) -> bool:
        if not _ID_RE.fullmatch(owner_token) or stale_before < 0:
            raise ValueError("invalid synthesis lease")
        key = {"pk": self._s(f"BATCH#{batch_id}"), "sk": self._s("SYNTHESIS")}
        now = int(datetime.now(UTC).timestamp())
        try:
            self._client.update_item(
                TableName=self._table_name, Key=key,
                UpdateExpression=(
                    "SET #state=:claimed, owner_token=:owner, claimed_at=:now, "
                    "lease_epoch=if_not_exists(lease_epoch,:zero)+:one"
                ),
                ConditionExpression=(
                    "#state=:available OR (#state=:claimed AND claimed_at<:stale)"
                ),
                ExpressionAttributeNames={"#state": "state"},
                ExpressionAttributeValues={
                    ":available": self._s("available"), ":claimed": self._s("claimed"),
                    ":owner": self._s(owner_token), ":now": self._n(now),
                    ":stale": self._n(stale_before), ":zero": self._n(0),
                    ":one": self._n(1),
                },
            )
            return True
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if code == "ConditionalCheckFailedException":
                return False
            raise BatchStoreBackendError("synthesis lease unavailable") from exc

    def complete_synthesis(self, *, batch_id: str, owner_token: str) -> bool:
        try:
            self._client.update_item(
                TableName=self._table_name,
                Key={"pk": self._s(f"BATCH#{batch_id}"), "sk": self._s("SYNTHESIS")},
                UpdateExpression="SET #state=:completed",
                ConditionExpression="#state=:claimed AND owner_token=:owner",
                ExpressionAttributeNames={"#state": "state"},
                ExpressionAttributeValues={
                    ":claimed": self._s("claimed"), ":completed": self._s("completed"),
                    ":owner": self._s(owner_token),
                },
            )
            return True
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if code == "ConditionalCheckFailedException":
                item = self._consistent_get({
                    "pk": self._s(f"BATCH#{batch_id}"), "sk": self._s("SYNTHESIS")
                })
                if item and item.get("state", {}).get("S") == "completed":
                    return True
                raise BatchStoreIntegrityError("synthesis completion conflict") from exc
            raise BatchStoreBackendError("synthesis completion unavailable") from exc

    def reconcile_stale_batches(
        self, *, stale_before: int, apply: bool = False,
    ) -> dict[str, Any]:
        """Inspect authority state and settle only fully receipted terminal batches.

        A stale claimed allocation with missing receipts is reported as
        ``uncertain`` and is never stolen, failed, or released automatically.
        """
        if stale_before < 0:
            raise ValueError("stale_before must be non-negative")
        items: list[dict[str, Any]] = []
        start_key: dict[str, Any] | None = None
        try:
            while True:
                kwargs: dict[str, Any] = {
                    "TableName": self._table_name,
                    "FilterExpression": "#sk=:meta AND #state=:reserved",
                    "ExpressionAttributeNames": {"#sk": "sk", "#state": "state"},
                    "ExpressionAttributeValues": {
                        ":meta": self._s("META"), ":reserved": self._s("reserved")
                    },
                    "ConsistentRead": True,
                }
                if start_key is not None:
                    kwargs["ExclusiveStartKey"] = start_key
                response = self._client.scan(**kwargs)
                items.extend(response.get("Items", []))
                start_key = response.get("LastEvaluatedKey")
                if not start_key:
                    break
        except Exception as exc:
            raise BatchStoreBackendError("cannot scan stale atomic batches") from exc
        summary: dict[str, Any] = {
            "dry_run": not apply, "ready": [], "settled": [],
            "uncertain": [], "pending": [],
        }
        for item in items:
            pk = item.get("pk", {}).get("S", "")
            created = int(item.get("created_at", {}).get("N", "0"))
            if not pk.startswith("BATCH#") or created > stale_before:
                continue
            batch_id = pk.removeprefix("BATCH#")
            allocations = [
                self._consistent_get({
                    "pk": self._s(pk),
                    "sk": self._s(f"ALLOCATION#{mode}"),
                })
                for mode in ANGLE_MODES
            ]
            if any(row is None for row in allocations):
                summary["uncertain"].append(batch_id)
                continue
            terminal = all(
                row.get("state", {}).get("S") in {"completed", "failed", "timeout"}
                for row in allocations
            )
            missing_receipt = any(
                row.get(f"{slot}_slot", {}).get("S") == "consumed"
                and f"{slot}_ledger_receipt" not in row
                for row in allocations
                for slot in ("claim_extraction", "evidence_narrative")
            )
            if terminal:
                summary["ready"].append(batch_id)
                if apply:
                    result = self.settle_batch(batch_id=batch_id)
                    if result.settled:
                        summary["settled"].append(batch_id)
            elif missing_receipt:
                summary["uncertain"].append(batch_id)
            else:
                summary["pending"].append(batch_id)
        return summary

    def consume_call_slot(
        self, *, batch_id: str, mode: str, job_id: str, owner_token: str,
        config_version: str, expected_amount_usd: Decimal, slot: str,
    ) -> bool:
        if slot not in {"claim_extraction", "evidence_narrative"}:
            raise ValueError("invalid call slot")
        if (
            not _ID_RE.fullmatch(batch_id)
            or mode not in ANGLE_MODES
            or job_id != _job_id(batch_id, mode)
            or not _ID_RE.fullmatch(owner_token)
            or not _ID_RE.fullmatch(config_version)
            or expected_amount_usd <= 0
        ):
            raise ValueError("invalid call slot identity")
        slot_name = f"{slot}_slot"
        tx = [
            {
                "ConditionCheck": {
                    "TableName": self._table_name,
                    "Key": {
                        "pk": self._s(f"BATCH#{batch_id}"),
                        "sk": self._s("META"),
                    },
                    "ConditionExpression": "config_version=:version",
                    "ExpressionAttributeValues": {
                        ":version": self._s(config_version),
                    },
                }
            },
            {
                "Update": {
                    "TableName": self._table_name,
                    "Key": {
                        "pk": self._s(f"BATCH#{batch_id}"),
                        "sk": self._s(f"ALLOCATION#{mode}"),
                    },
                    "UpdateExpression": "SET #slot=:consumed",
                    "ConditionExpression": (
                        "job_id=:job AND amount_usd=:amount "
                        "AND owner_token=:owner AND #state=:claimed "
                        "AND #slot=:available"
                    ),
                    "ExpressionAttributeNames": {
                        "#slot": slot_name,
                        "#state": "state",
                    },
                    "ExpressionAttributeValues": {
                        ":job": self._s(job_id),
                        ":amount": self._n(expected_amount_usd),
                        ":owner": self._s(owner_token),
                        ":claimed": self._s("claimed"),
                        ":available": self._s("available"),
                        ":consumed": self._s("consumed"),
                    },
                }
            },
        ]
        try:
            self._client.transact_write_items(TransactItems=tx)
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if code == "TransactionCanceledException":
                raise BatchStoreIntegrityError(
                    "call slot is consumed or identity/config does not match"
                ) from exc
            raise BatchStoreBackendError("call slot authority unavailable") from exc
        return True

    def record_call_cost(
        self, *, batch_id: str, mode: str, job_id: str, owner_token: str,
        slot: str, accounting_token: str, ledger_receipt: str,
        actual_cost_usd: Decimal, tokens_in: int, tokens_out: int,
    ) -> bool:
        if (
            slot not in {"claim_extraction", "evidence_narrative"}
            or mode not in ANGLE_MODES or job_id != _job_id(batch_id, mode)
            or not _ID_RE.fullmatch(owner_token)
            or not _HASH_RE.fullmatch(accounting_token) or not ledger_receipt
            or actual_cost_usd < 0 or tokens_in < 0 or tokens_out < 0
        ):
            raise ValueError("invalid call accounting")
        names = {"#slot": f"{slot}_slot"}
        values = {
            ":owner": self._s(owner_token), ":job": self._s(job_id),
            ":consumed": self._s("consumed"),
            ":token": self._s(accounting_token), ":receipt": self._s(ledger_receipt),
            ":cost": self._n(actual_cost_usd),
            ":tin": self._n(tokens_in), ":tout": self._n(tokens_out),
        }
        try:
            self._client.transact_write_items(TransactItems=[
                {"Update": {
                    "TableName": self._table_name,
                    "Key": {
                        "pk": self._s(f"BATCH#{batch_id}"),
                        "sk": self._s(f"ALLOCATION#{mode}"),
                    },
                    "UpdateExpression": (
                        f"SET {slot}_accounting_token=:token, "
                        f"{slot}_ledger_receipt=:receipt, "
                        f"{slot}_actual_cost_usd=:cost, {slot}_tokens_in=:tin, "
                        f"{slot}_tokens_out=:tout"
                    ),
                    "ConditionExpression": (
                        "#slot=:consumed AND owner_token=:owner AND job_id=:job "
                        f"AND attribute_not_exists({slot}_accounting_token)"
                    ),
                    "ExpressionAttributeNames": names,
                    "ExpressionAttributeValues": values,
                }},
                {"Put": {
                    "TableName": self._table_name,
                    "Item": {
                        "pk": self._s(f"ACCOUNTING#{accounting_token}"),
                        "sk": self._s("BINDING"), "batch_id": self._s(batch_id),
                        "mode": self._s(mode), "job_id": self._s(job_id),
                        "slot": self._s(slot), "owner_token": self._s(owner_token),
                        "ledger_receipt": self._s(ledger_receipt),
                        "actual_cost_usd": self._n(actual_cost_usd),
                        "tokens_in": self._n(tokens_in),
                        "tokens_out": self._n(tokens_out),
                    },
                    "ConditionExpression": "attribute_not_exists(pk)",
                }},
            ])
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if code != "TransactionCanceledException":
                raise BatchStoreBackendError(
                    "call accounting authority write failed"
                ) from exc
            binding = self._consistent_get({
                "pk": self._s(f"ACCOUNTING#{accounting_token}"),
                "sk": self._s("BINDING"),
            })
            allocation = self._consistent_get({
                "pk": self._s(f"BATCH#{batch_id}"),
                "sk": self._s(f"ALLOCATION#{mode}"),
            })
            expected = {
                "batch_id": ("S", batch_id), "mode": ("S", mode),
                "job_id": ("S", job_id), "slot": ("S", slot),
                "owner_token": ("S", owner_token),
                "ledger_receipt": ("S", ledger_receipt),
                "actual_cost_usd": ("N", str(actual_cost_usd)),
                "tokens_in": ("N", str(tokens_in)),
                "tokens_out": ("N", str(tokens_out)),
            }
            if binding is None or allocation is None or any(
                binding.get(field, {}).get(kind) != value
                for field, (kind, value) in expected.items()
            ) or any((
                allocation.get(f"{slot}_{field}", {}).get(kind) != value
                for field, (kind, value) in {
                    "accounting_token": ("S", accounting_token),
                    "ledger_receipt": ("S", ledger_receipt),
                    "actual_cost_usd": ("N", str(actual_cost_usd)),
                    "tokens_in": ("N", str(tokens_in)),
                    "tokens_out": ("N", str(tokens_out)),
                }.items()
            )):
                raise BatchStoreIntegrityError(
                    "call accounting replay conflict"
                ) from exc
        return True

    def cancel_call_slot(
        self, *, batch_id: str, mode: str, job_id: str, owner_token: str,
        slot: str,
    ) -> bool:
        if (
            slot not in {"claim_extraction", "evidence_narrative"}
            or mode not in ANGLE_MODES or job_id != _job_id(batch_id, mode)
            or not _ID_RE.fullmatch(batch_id)
            or not _ID_RE.fullmatch(owner_token)
        ):
            raise ValueError("invalid cancellation slot")
        token = hashlib.sha256(
            f"cancelled_before_call:{batch_id}:{mode}:{job_id}:{slot}".encode()
        ).hexdigest()
        receipt = f"cancelled-before-call:{token}"
        names = {"#state": "state", "#slot": f"{slot}_slot"}
        values = {
            ":claimed": self._s("claimed"), ":owner": self._s(owner_token),
            ":job": self._s(job_id), ":available": self._s("available"),
            ":consumed": self._s("consumed"), ":token": self._s(token),
            ":receipt": self._s(receipt), ":zero": self._n(0),
        }
        try:
            self._client.update_item(
                TableName=self._table_name,
                Key={
                    "pk": self._s(f"BATCH#{batch_id}"),
                    "sk": self._s(f"ALLOCATION#{mode}"),
                },
                UpdateExpression=(
                    f"SET #slot=:consumed, {slot}_accounting_token=:token, "
                    f"{slot}_ledger_receipt=:receipt, {slot}_actual_cost_usd=:zero, "
                    f"{slot}_tokens_in=:zero, {slot}_tokens_out=:zero"
                ),
                ConditionExpression=(
                    "#state=:claimed AND owner_token=:owner AND job_id=:job AND "
                    "((#slot=:available AND "
                    f"attribute_not_exists({slot}_accounting_token)) OR "
                    f"(#slot=:consumed AND {slot}_accounting_token=:token AND "
                    f"{slot}_ledger_receipt=:receipt AND "
                    f"{slot}_actual_cost_usd=:zero AND {slot}_tokens_in=:zero AND "
                    f"{slot}_tokens_out=:zero))"
                ),
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
            return True
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if code == "ConditionalCheckFailedException":
                raise BatchStoreIntegrityError("call cancellation conflict") from exc
            raise BatchStoreBackendError("call cancellation unavailable") from exc


class SQLiteAtomicMultiAngleBatchStore:
    """Local parity adapter with explicit authoritative budget bootstrap."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        with closing(self._connect()) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS atomic_budget (
                  day TEXT PRIMARY KEY, remaining_usd TEXT NOT NULL,
                  reserved_total TEXT NOT NULL, config_version TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS atomic_requests (
                  caller_hash TEXT NOT NULL, idempotency_key_hash TEXT NOT NULL,
                  fingerprint TEXT NOT NULL, batch_id TEXT NOT NULL,
                  state TEXT NOT NULL,
                  PRIMARY KEY(caller_hash,idempotency_key_hash)
                );
                CREATE TABLE IF NOT EXISTS atomic_batches (
                  batch_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                  payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS atomic_allocations (
                  batch_id TEXT NOT NULL, mode TEXT NOT NULL, job_id TEXT NOT NULL,
                  amount_usd TEXT NOT NULL, state TEXT NOT NULL, owner_token TEXT,
                  claim_extraction_slot TEXT NOT NULL DEFAULT 'available',
                  evidence_narrative_slot TEXT NOT NULL DEFAULT 'available',
                  claimed_at INTEGER,
                  PRIMARY KEY(batch_id,mode)
                );
                CREATE TABLE IF NOT EXISTS atomic_jobs (
                  job_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, mode TEXT NOT NULL,
                  state TEXT NOT NULL, owner_token TEXT
                );
                CREATE TABLE IF NOT EXISTS atomic_settlements (
                  batch_id TEXT PRIMARY KEY, actual_cost_usd TEXT NOT NULL,
                  released_usd TEXT NOT NULL, synthesis_claimed INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS atomic_synthesis_claims (
                  batch_id TEXT PRIMARY KEY, state TEXT NOT NULL,
                  owner_token TEXT, claimed_at INTEGER, lease_epoch INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS atomic_call_costs (
                  job_id TEXT NOT NULL, slot TEXT NOT NULL, batch_id TEXT NOT NULL,
                  mode TEXT NOT NULL, owner_token TEXT NOT NULL,
                  accounting_token TEXT NOT NULL UNIQUE,
                  ledger_receipt TEXT NOT NULL, actual_cost_usd TEXT NOT NULL,
                  tokens_in INTEGER NOT NULL, tokens_out INTEGER NOT NULL,
                  PRIMARY KEY(job_id,slot)
                );
                CREATE TABLE IF NOT EXISTS atomic_job_outcomes (
                  job_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, mode TEXT NOT NULL,
                  state TEXT NOT NULL, actual_cost_usd TEXT NOT NULL,
                  tokens_in INTEGER NOT NULL, tokens_out INTEGER NOT NULL,
                  ledger_receipt TEXT NOT NULL
                );
                """
            )
            for table_name in ("atomic_allocations", "atomic_jobs"):
                columns = {
                    row[1]
                    for row in conn.execute(
                        f"PRAGMA table_info({table_name})"
                    ).fetchall()
                }
                if "owner_token" not in columns:
                    conn.execute(
                        f"ALTER TABLE {table_name} ADD COLUMN owner_token TEXT"
                    )
            allocation_columns = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(atomic_allocations)"
                ).fetchall()
            }
            for slot in ("claim_extraction_slot", "evidence_narrative_slot"):
                if slot not in allocation_columns:
                    conn.execute(
                        f"""ALTER TABLE atomic_allocations ADD COLUMN {slot}
                            TEXT NOT NULL DEFAULT 'available'"""
                    )
            if "claimed_at" not in allocation_columns:
                conn.execute(
                    "ALTER TABLE atomic_allocations ADD COLUMN claimed_at INTEGER"
                )
            synthesis_columns = {
                row[1] for row in conn.execute(
                    "PRAGMA table_info(atomic_synthesis_claims)"
                ).fetchall()
            }
            for definition in (
                "owner_token TEXT", "claimed_at INTEGER",
                "lease_epoch INTEGER NOT NULL DEFAULT 0",
            ):
                name = definition.split()[0]
                if name not in synthesis_columns:
                    conn.execute(
                        f"ALTER TABLE atomic_synthesis_claims ADD COLUMN {definition}"
                    )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path, timeout=10, isolation_level=None)

    def bootstrap_budget(
        self, *, day: str, remaining_usd: Decimal, config_version: str
    ) -> None:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError("invalid day")
        if remaining_usd < 0 or not _ID_RE.fullmatch(config_version):
            raise ValueError("invalid budget configuration")
        with closing(self._connect()) as conn:
            conn.execute(
                """INSERT INTO atomic_budget VALUES(?,?,?,?)
                   ON CONFLICT(day) DO UPDATE SET remaining_usd=excluded.remaining_usd,
                     reserved_total=excluded.reserved_total,
                     config_version=excluded.config_version""",
                (day, str(remaining_usd), "0", config_version),
            )

    def ensure_budget(
        self, *, day: str, remaining_usd: Decimal, config_version: str
    ) -> None:
        """Local/test bootstrap that never resets an existing day's balance."""
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError("invalid day")
        if remaining_usd < 0 or not _ID_RE.fullmatch(config_version):
            raise ValueError("invalid budget configuration")
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO atomic_budget VALUES(?,?,?,?)",
                (day, str(remaining_usd), "0", config_version),
            )

    def _verify_replay(
        self, conn: sqlite3.Connection, request: AtomicBatchRequest, row: tuple[str, str]
    ) -> AtomicBatchResult:
        fingerprint, batch_id = row
        if fingerprint != request.request_fingerprint:
            raise BatchConflictError(
                "caller idempotency key is bound to a different request fingerprint"
            )
        batch_count = conn.execute(
            "SELECT count(*) FROM atomic_batches WHERE batch_id=?", (batch_id,)
        ).fetchone()[0]
        allocation_count = conn.execute(
            "SELECT count(*) FROM atomic_allocations WHERE batch_id=?", (batch_id,)
        ).fetchone()[0]
        job_count = conn.execute(
            "SELECT count(*) FROM atomic_jobs WHERE batch_id=?", (batch_id,)
        ).fetchone()[0]
        if (batch_count, allocation_count, job_count) != (1, 5, 5):
            raise BatchStoreIntegrityError("replay manifest is incomplete")
        batch = conn.execute(
            "SELECT fingerprint,payload_json FROM atomic_batches WHERE batch_id=?",
            (batch_id,),
        ).fetchone()
        allocations = conn.execute(
            """SELECT mode,job_id FROM atomic_allocations
               WHERE batch_id=? ORDER BY mode""",
            (batch_id,),
        ).fetchall()
        jobs = conn.execute(
            "SELECT job_id,mode FROM atomic_jobs WHERE batch_id=? ORDER BY mode",
            (batch_id,),
        ).fetchall()
        if (
            batch is None
            or batch[0] != request.request_fingerprint
            or {tuple(row) for row in allocations}
            != {(mode, _job_id(batch_id, mode)) for mode in ANGLE_MODES}
            or {tuple(row) for row in jobs}
            != {(_job_id(batch_id, mode), mode) for mode in ANGLE_MODES}
        ):
            raise BatchStoreIntegrityError("replay manifest identity is inconsistent")
        try:
            payload = json.loads(batch[1])
            snapshot_id = payload["snapshot_id"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise BatchStoreIntegrityError("batch snapshot identity is invalid") from exc
        if not isinstance(snapshot_id, str) or not _ID_RE.fullmatch(snapshot_id):
            raise BatchStoreIntegrityError("batch snapshot identity is invalid")
        return AtomicBatchResult(
            True, True, batch_id, _job_ids(batch_id), snapshot_id
        )

    def find_replay(self, request: AtomicBatchRequest) -> AtomicBatchResult | None:
        request.validate()
        conn = self._connect()
        try:
            row = conn.execute(
                """SELECT fingerprint,batch_id FROM atomic_requests
                   WHERE caller_hash=? AND idempotency_key_hash=?""",
                (request.caller_hash, request.idempotency_key_hash),
            ).fetchone()
            return self._verify_replay(conn, request, row) if row else None
        finally:
            conn.close()

    def cancel_call_slot(
        self, *, batch_id: str, mode: str, job_id: str, owner_token: str,
        slot: str,
    ) -> bool:
        if (
            slot not in {"claim_extraction", "evidence_narrative"}
            or mode not in ANGLE_MODES or job_id != _job_id(batch_id, mode)
            or not _ID_RE.fullmatch(batch_id)
            or not _ID_RE.fullmatch(owner_token)
        ):
            raise ValueError("invalid cancellation slot")
        token = hashlib.sha256(
            f"cancelled_before_call:{batch_id}:{mode}:{job_id}:{slot}".encode()
        ).hexdigest()
        receipt = f"cancelled-before-call:{token}"
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                f"""SELECT job_id,state,owner_token,{slot}_slot
                    FROM atomic_allocations WHERE batch_id=? AND mode=?""",
                (batch_id, mode),
            ).fetchone()
            if row is None or row[:3] != (job_id, "claimed", owner_token):
                raise BatchStoreIntegrityError("call cancellation identity mismatch")
            existing = conn.execute(
                """SELECT accounting_token,ledger_receipt,actual_cost_usd,
                          tokens_in,tokens_out FROM atomic_call_costs
                   WHERE job_id=? AND slot=?""",
                (job_id, slot),
            ).fetchone()
            expected = (token, receipt, "0", 0, 0)
            if row[3] == "consumed":
                if existing != expected:
                    raise BatchStoreIntegrityError("call cancellation conflict")
                conn.commit()
                return True
            if row[3] != "available" or existing is not None:
                raise BatchStoreIntegrityError("call cancellation conflict")
            conn.execute(
                f"""UPDATE atomic_allocations SET {slot}_slot='consumed'
                    WHERE batch_id=? AND mode=?""",
                (batch_id, mode),
            )
            conn.execute(
                """INSERT INTO atomic_call_costs(
                     job_id,slot,batch_id,mode,owner_token,accounting_token,
                     ledger_receipt,actual_cost_usd,tokens_in,tokens_out
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    job_id, slot, batch_id, mode, owner_token, token, receipt,
                    "0", 0, 0,
                ),
            )
            conn.commit()
            return True
        except (BatchStoreIntegrityError, ValueError):
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            raise BatchStoreBackendError("local call cancellation failed") from exc
        finally:
            conn.close()

    def create_batch(self, request: AtomicBatchRequest) -> AtomicBatchResult:
        request.validate()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """SELECT fingerprint,batch_id FROM atomic_requests
                   WHERE caller_hash=? AND idempotency_key_hash=?""",
                (request.caller_hash, request.idempotency_key_hash),
            ).fetchone()
            if existing:
                result = self._verify_replay(conn, request, existing)
                conn.commit()
                return result
            budget = conn.execute(
                """SELECT remaining_usd,reserved_total,config_version FROM atomic_budget
                   WHERE day=?""",
                (request.day,),
            ).fetchone()
            if not budget:
                raise BatchStoreBackendError("authoritative budget is not bootstrapped")
            remaining, reserved, version = (
                Decimal(budget[0]),
                Decimal(budget[1]),
                budget[2],
            )
            if version != request.config_version:
                raise BatchStoreBackendError("budget config version mismatch")
            if remaining < request.batch_cost_usd:
                conn.rollback()
                return AtomicBatchResult(False, False, request.batch_id, ())
            conn.execute(
                """UPDATE atomic_budget SET remaining_usd=?,
                   reserved_total=? WHERE day=?""",
                (
                    str(remaining - request.batch_cost_usd),
                    str(reserved + request.batch_cost_usd),
                    request.day,
                ),
            )
            conn.execute(
                "INSERT INTO atomic_requests VALUES(?,?,?,?,?)",
                (
                    request.caller_hash,
                    request.idempotency_key_hash,
                    request.request_fingerprint,
                    request.batch_id,
                    "active",
                ),
            )
            conn.execute(
                "INSERT INTO atomic_batches VALUES(?,?,?)",
                (
                    request.batch_id,
                    request.request_fingerprint,
                    json.dumps(
                        {
                            "coin": request.coin,
                            "snapshot_id": request.snapshot_id,
                            "day": request.day,
                            "config_version": request.config_version,
                            "batch_cost_usd": str(request.batch_cost_usd),
                            "created_at": request.created_at,
                        },
                        sort_keys=True,
                    ),
                ),
            )
            per_job = request.batch_cost_usd / Decimal(len(ANGLE_MODES))
            for mode, job_id in zip(ANGLE_MODES, _job_ids(request.batch_id), strict=True):
                conn.execute(
                    """INSERT INTO atomic_allocations
                       (batch_id,mode,job_id,amount_usd,state,owner_token)
                       VALUES(?,?,?,?,?,?)""",
                    (request.batch_id, mode, job_id, str(per_job), "reserved", None),
                )
                conn.execute(
                    """INSERT INTO atomic_jobs
                       (job_id,batch_id,mode,state,owner_token) VALUES(?,?,?,?,?)""",
                    (job_id, request.batch_id, mode, "pending", None),
                )
            conn.commit()
            return AtomicBatchResult(
                True, False, request.batch_id, _job_ids(request.batch_id),
                request.snapshot_id,
            )
        except (BatchConflictError, BatchStoreIntegrityError, BatchStoreBackendError):
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            raise BatchStoreBackendError("local atomic batch transaction failed") from exc
        finally:
            conn.close()

    def claim_allocation(
        self, *, batch_id: str, mode: str, job_id: str, owner_token: str,
        config_version: str, expected_amount_usd: Decimal,
    ) -> bool:
        if (
            not _ID_RE.fullmatch(batch_id)
            or mode not in ANGLE_MODES
            or job_id != _job_id(batch_id, mode)
            or not _ID_RE.fullmatch(owner_token)
            or not _ID_RE.fullmatch(config_version)
            or expected_amount_usd <= 0
        ):
            raise ValueError("invalid allocation identity")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            allocation = conn.execute(
                """SELECT job_id,amount_usd,state,owner_token FROM atomic_allocations
                   WHERE batch_id=? AND mode=?""",
                (batch_id, mode),
            ).fetchone()
            job = conn.execute(
                "SELECT batch_id,mode,state,owner_token FROM atomic_jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
            batch = conn.execute(
                "SELECT payload_json FROM atomic_batches WHERE batch_id=?", (batch_id,)
            ).fetchone()
            if (
                allocation is None
                or job is None
                or batch is None
                or allocation[0] != job_id
                or Decimal(allocation[1]) != expected_amount_usd
                or allocation[2] not in {"reserved", "claimed"}
                or tuple(job[:2]) != (batch_id, mode)
                or job[2] not in {"pending", "claimed"}
                or (
                    allocation[2] == "claimed"
                    and allocation[3] != owner_token
                )
                or (allocation[2] == "reserved" and allocation[3] is not None)
                or (job[2] == "claimed" and job[3] != owner_token)
                or (job[2] == "pending" and job[3] is not None)
                or json.loads(batch[0]).get("config_version") != config_version
            ):
                raise BatchStoreIntegrityError(
                    "allocation claim identity/state condition failed"
                )
            conn.execute(
                """UPDATE atomic_allocations SET state='claimed',owner_token=?,
                   claimed_at=COALESCE(claimed_at,?)
                   WHERE batch_id=? AND mode=?""",
                (owner_token, int(datetime.now(UTC).timestamp()), batch_id, mode),
            )
            conn.execute(
                """UPDATE atomic_jobs SET state='claimed',owner_token=?
                   WHERE job_id=?""",
                (owner_token, job_id),
            )
            conn.commit()
            return True
        except (BatchStoreIntegrityError, ValueError):
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            raise BatchStoreBackendError("local allocation claim failed") from exc
        finally:
            conn.close()

    def consume_call_slot(
        self, *, batch_id: str, mode: str, job_id: str, owner_token: str,
        config_version: str, expected_amount_usd: Decimal, slot: str,
    ) -> bool:
        if slot not in {"claim_extraction", "evidence_narrative"}:
            raise ValueError("invalid call slot")
        slot_name = f"{slot}_slot"
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            allocation = conn.execute(
                f"""SELECT job_id,amount_usd,state,owner_token,{slot_name}
                    FROM atomic_allocations WHERE batch_id=? AND mode=?""",
                (batch_id, mode),
            ).fetchone()
            batch = conn.execute(
                "SELECT payload_json FROM atomic_batches WHERE batch_id=?", (batch_id,)
            ).fetchone()
            if (
                allocation is None
                or batch is None
                or allocation[0] != job_id
                or Decimal(allocation[1]) != expected_amount_usd
                or allocation[2:] != ("claimed", owner_token, "available")
                or json.loads(batch[0]).get("config_version") != config_version
            ):
                raise BatchStoreIntegrityError(
                    "call slot is consumed or identity/config does not match"
                )
            conn.execute(
                f"""UPDATE atomic_allocations SET {slot_name}='consumed'
                    WHERE batch_id=? AND mode=?""",
                (batch_id, mode),
            )
            conn.commit()
            return True
        except (BatchStoreIntegrityError, ValueError):
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            raise BatchStoreBackendError("local call slot consumption failed") from exc
        finally:
            conn.close()

    def record_call_cost(
        self, *, batch_id: str, mode: str, job_id: str, owner_token: str,
        slot: str, accounting_token: str, ledger_receipt: str,
        actual_cost_usd: Decimal, tokens_in: int, tokens_out: int,
    ) -> bool:
        if (
            slot not in {"claim_extraction", "evidence_narrative"}
            or mode not in ANGLE_MODES or job_id != _job_id(batch_id, mode)
            or not _ID_RE.fullmatch(owner_token)
            or actual_cost_usd < 0 or tokens_in < 0 or tokens_out < 0
            or not _HASH_RE.fullmatch(accounting_token) or not ledger_receipt
        ):
            raise ValueError("invalid call accounting")
        conn = self._connect()
        payload = (
            job_id, slot, batch_id, mode, owner_token, accounting_token,
            ledger_receipt, str(actual_cost_usd), tokens_in, tokens_out,
        )
        try:
            conn.execute("BEGIN IMMEDIATE")
            allocation = conn.execute(
                f"""SELECT job_id,state,owner_token,{slot}_slot
                    FROM atomic_allocations WHERE batch_id=? AND mode=?""",
                (batch_id, mode),
            ).fetchone()
            if allocation != (job_id, "claimed", owner_token, "consumed"):
                raise BatchStoreIntegrityError("call accounting authority mismatch")
            existing = conn.execute(
                """SELECT job_id,slot,batch_id,mode,owner_token,accounting_token,
                          ledger_receipt,actual_cost_usd,tokens_in,tokens_out
                   FROM atomic_call_costs WHERE job_id=? AND slot=?""",
                (job_id, slot),
            ).fetchone()
            if existing is not None:
                if existing != payload:
                    raise BatchStoreIntegrityError("call accounting replay conflict")
                conn.commit()
                return True
            token_row = conn.execute(
                "SELECT job_id,slot FROM atomic_call_costs WHERE accounting_token=?",
                (accounting_token,),
            ).fetchone()
            if token_row is not None:
                raise BatchStoreIntegrityError("accounting token is already bound")
            conn.execute(
                """INSERT INTO atomic_call_costs
                   (job_id,slot,batch_id,mode,owner_token,accounting_token,
                    ledger_receipt,actual_cost_usd,tokens_in,tokens_out)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                payload,
            )
            conn.commit()
            return True
        except (BatchStoreIntegrityError, ValueError):
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            raise BatchStoreBackendError("local call accounting failed") from exc
        finally:
            conn.close()

    def record_job_terminal(
        self, *, batch_id: str, mode: str, job_id: str, owner_token: str,
        state: str,
    ) -> bool:
        if state not in {"completed", "failed", "timeout"}:
            raise ValueError("invalid terminal outcome")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            call_rows = conn.execute(
                """SELECT slot,actual_cost_usd,tokens_in,tokens_out,ledger_receipt
                   FROM atomic_call_costs WHERE job_id=? ORDER BY slot""",
                (job_id,),
            ).fetchall()
            if [row[0] for row in call_rows] != [
                "claim_extraction", "evidence_narrative"
            ]:
                raise BatchStoreIntegrityError(
                    "terminal requires both durable call receipts"
                )
            actual_cost_usd = sum(
                (Decimal(row[1]) for row in call_rows), Decimal(0)
            )
            tokens_in = sum(row[2] for row in call_rows)
            tokens_out = sum(row[3] for row in call_rows)
            ledger_receipt = json.dumps(
                [row[4] for row in call_rows], separators=(",", ":")
            )
            outcome = conn.execute(
                """SELECT batch_id,mode,state,actual_cost_usd,tokens_in,tokens_out,
                          ledger_receipt FROM atomic_job_outcomes WHERE job_id=?""",
                (job_id,),
            ).fetchone()
            expected_outcome = (
                batch_id, mode, state, str(actual_cost_usd),
                tokens_in, tokens_out, ledger_receipt,
            )
            if outcome is not None:
                if outcome != expected_outcome:
                    raise BatchStoreIntegrityError("terminal replay conflict")
                conn.commit()
                return True
            for table, where, args in (
                ("atomic_jobs", "job_id=?", (job_id,)),
                ("atomic_allocations", "batch_id=? AND mode=?", (batch_id, mode)),
            ):
                row = conn.execute(
                    f"SELECT state,owner_token FROM {table} WHERE {where}",
                    args,
                ).fetchone()
                if row is None or row[1] != owner_token:
                    raise BatchStoreIntegrityError("terminal owner mismatch")
                if row[0] != "claimed":
                    raise BatchStoreIntegrityError("job is not claimed")
                conn.execute(f"UPDATE {table} SET state=? WHERE {where}", (state, *args))
            conn.execute(
                """INSERT INTO atomic_job_outcomes VALUES(?,?,?,?,?,?,?,?)""",
                (
                    job_id, batch_id, mode, state, str(actual_cost_usd),
                    tokens_in, tokens_out, ledger_receipt,
                ),
            )
            conn.commit()
            return True
        except (BatchStoreIntegrityError, ValueError):
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            raise BatchStoreBackendError("terminal authority write failed") from exc
        finally:
            conn.close()

    def settle_batch(self, *, batch_id: str) -> BatchSettlementResult:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            replay = conn.execute(
                "SELECT actual_cost_usd,released_usd,synthesis_claimed FROM atomic_settlements WHERE batch_id=?",
                (batch_id,),
            ).fetchone()
            if replay:
                conn.commit()
                return BatchSettlementResult(
                    batch_id, True, True, Decimal(replay[0]), Decimal(replay[1]), bool(replay[2])
                )
            rows = conn.execute(
                """SELECT actual_cost_usd,state FROM atomic_job_outcomes
                   WHERE batch_id=?""",
                (batch_id,),
            ).fetchall()
            if len(rows) != len(ANGLE_MODES):
                conn.rollback()
                return BatchSettlementResult(batch_id, False, False, Decimal(0), Decimal(0), False)
            actual = sum((Decimal(row[0] or "0") for row in rows), Decimal(0))
            synthesize = all(row[1] == "completed" for row in rows)
            batch = conn.execute(
                "SELECT payload_json FROM atomic_batches WHERE batch_id=?", (batch_id,)
            ).fetchone()
            payload = json.loads(batch[0])
            budget = conn.execute(
                "SELECT remaining_usd,reserved_total FROM atomic_budget WHERE day=?",
                (payload["day"],),
            ).fetchone()
            reserved = sum(
                (Decimal(row[0]) for row in conn.execute(
                    "SELECT amount_usd FROM atomic_allocations WHERE batch_id=?", (batch_id,)
                )), Decimal(0)
            )
            if reserved != Decimal(payload["batch_cost_usd"]) or actual > reserved:
                raise BatchStoreIntegrityError(
                    "settlement costs exceed reserved authority"
                )
            released = max(Decimal(0), reserved - actual)
            if budget is None or Decimal(budget[1]) < reserved:
                raise BatchStoreIntegrityError("budget reserved_total underflow")
            remaining = Decimal(budget[0]) + released
            reserved_total = Decimal(budget[1]) - reserved
            conn.execute(
                """UPDATE atomic_budget SET reserved_total=?,remaining_usd=?
                   WHERE day=?""",
                (str(reserved_total), str(remaining), payload["day"]),
            )
            conn.execute(
                "INSERT INTO atomic_settlements VALUES(?,?,?,?)",
                (batch_id, str(actual), str(released), int(synthesize)),
            )
            if synthesize:
                conn.execute(
                    """INSERT INTO atomic_synthesis_claims
                       (batch_id,state,owner_token,claimed_at,lease_epoch)
                       VALUES(?,?,?,?,?)""",
                    (batch_id, "available", None, None, 0),
                )
            conn.commit()
            return BatchSettlementResult(
                batch_id, True, False, actual, released, synthesize
            )
        except (BatchStoreIntegrityError, ValueError):
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            raise BatchStoreBackendError("batch settlement failed") from exc
        finally:
            conn.close()

    def call_accounting_state(
        self, *, batch_id: str, mode: str, job_id: str, owner_token: str,
    ) -> dict[str, str]:
        conn = self._connect()
        try:
            allocation = conn.execute(
                """SELECT job_id,owner_token,claim_extraction_slot,
                          evidence_narrative_slot FROM atomic_allocations
                   WHERE batch_id=? AND mode=?""",
                (batch_id, mode),
            ).fetchone()
            if (
                allocation is None or allocation[0] != job_id
                or allocation[1] != owner_token
            ):
                raise BatchStoreIntegrityError("call accounting identity mismatch")
            receipts = {
                row[0] for row in conn.execute(
                    "SELECT slot FROM atomic_call_costs WHERE job_id=?", (job_id,)
                ).fetchall()
            }
            result = {}
            for slot, state in zip(
                ("claim_extraction", "evidence_narrative"),
                allocation[2:], strict=True,
            ):
                result[slot] = (
                    "available" if state == "available"
                    else "receipted" if slot in receipts
                    else "uncertain"
                )
            return result
        finally:
            conn.close()

    def claim_synthesis(
        self, *, batch_id: str, owner_token: str, stale_before: int,
    ) -> bool:
        if not _ID_RE.fullmatch(owner_token) or stale_before < 0:
            raise ValueError("invalid synthesis lease")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT state,claimed_at FROM atomic_synthesis_claims
                   WHERE batch_id=?""",
                (batch_id,),
            ).fetchone()
            if row is None:
                raise BatchStoreIntegrityError("synthesis authority is missing")
            if row[0] == "completed":
                conn.commit()
                return False
            if row[0] == "claimed" and (
                row[1] is None or int(row[1]) >= stale_before
            ):
                conn.commit()
                return False
            now = int(datetime.now(UTC).timestamp())
            conn.execute(
                """UPDATE atomic_synthesis_claims
                   SET state='claimed',owner_token=?,claimed_at=?,
                       lease_epoch=lease_epoch+1 WHERE batch_id=?""",
                (owner_token, now, batch_id),
            )
            conn.commit()
            return True
        except (BatchStoreIntegrityError, ValueError):
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            raise BatchStoreBackendError("local synthesis lease failed") from exc
        finally:
            conn.close()

    def complete_synthesis(self, *, batch_id: str, owner_token: str) -> bool:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT state,owner_token FROM atomic_synthesis_claims
                   WHERE batch_id=?""",
                (batch_id,),
            ).fetchone()
            if row and row[0] == "completed":
                conn.commit()
                return True
            if row != ("claimed", owner_token):
                raise BatchStoreIntegrityError("synthesis completion conflict")
            conn.execute(
                """UPDATE atomic_synthesis_claims SET state='completed'
                   WHERE batch_id=?""",
                (batch_id,),
            )
            conn.commit()
            return True
        except (BatchStoreIntegrityError, ValueError):
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            raise BatchStoreBackendError("local synthesis completion failed") from exc
        finally:
            conn.close()

    def reconcile_stale_batches(
        self, *, stale_before: int, apply: bool = False,
    ) -> dict[str, Any]:
        if stale_before < 0:
            raise ValueError("stale_before must be non-negative")
        summary: dict[str, Any] = {
            "dry_run": not apply, "ready": [], "settled": [],
            "uncertain": [], "pending": [],
        }
        conn = self._connect()
        try:
            batches = conn.execute(
                """SELECT b.batch_id,b.payload_json
                   FROM atomic_batches b
                   LEFT JOIN atomic_settlements s USING(batch_id)
                   WHERE s.batch_id IS NULL ORDER BY b.batch_id"""
            ).fetchall()
            for batch_id, payload_json in batches:
                try:
                    created_at = int(json.loads(payload_json)["created_at"])
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise BatchStoreIntegrityError(
                        "stale batch timestamp is malformed"
                    ) from exc
                if created_at > stale_before:
                    continue
                rows = conn.execute(
                    """SELECT state,claim_extraction_slot,evidence_narrative_slot
                       FROM atomic_allocations WHERE batch_id=?""",
                    (batch_id,),
                ).fetchall()
                if len(rows) != len(ANGLE_MODES):
                    summary["uncertain"].append(batch_id)
                    continue
                if all(
                    row[0] in {"completed", "failed", "timeout"} for row in rows
                ):
                    summary["ready"].append(batch_id)
                    if apply:
                        result = self.settle_batch(batch_id=batch_id)
                        if result.settled:
                            summary["settled"].append(batch_id)
                    continue
                consumed = sum(
                    state == "consumed" for row in rows for state in row[1:]
                )
                receipts = conn.execute(
                    "SELECT count(*) FROM atomic_call_costs WHERE batch_id=?",
                    (batch_id,),
                ).fetchone()[0]
                target = "uncertain" if receipts < consumed else "pending"
                summary[target].append(batch_id)
            return summary
        except (BatchStoreIntegrityError, ValueError):
            raise
        except Exception as exc:
            raise BatchStoreBackendError("local stale batch scan failed") from exc
        finally:
            conn.close()
