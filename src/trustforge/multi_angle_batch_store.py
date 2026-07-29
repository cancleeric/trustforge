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
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
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

    def create_batch(self, request: AtomicBatchRequest) -> AtomicBatchResult:
        request.validate()
        replay = self._read_replay(request)
        if replay is not None:
            return replay

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
                    "UpdateExpression": "SET #state=:claimed, owner_token=:owner",
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


class SQLiteAtomicMultiAngleBatchStore:
    """Local parity adapter with explicit authoritative budget bootstrap."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        with self._connect() as conn:
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
                  PRIMARY KEY(batch_id,mode)
                );
                CREATE TABLE IF NOT EXISTS atomic_jobs (
                  job_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, mode TEXT NOT NULL,
                  state TEXT NOT NULL, owner_token TEXT
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

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path, timeout=10, isolation_level=None)

    def bootstrap_budget(
        self, *, day: str, remaining_usd: Decimal, config_version: str
    ) -> None:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError("invalid day")
        if remaining_usd < 0 or not _ID_RE.fullmatch(config_version):
            raise ValueError("invalid budget configuration")
        with self._connect() as conn:
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
        with self._connect() as conn:
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
                    "INSERT INTO atomic_jobs VALUES(?,?,?,?,?)",
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
                """UPDATE atomic_allocations SET state='claimed',owner_token=?
                   WHERE batch_id=? AND mode=?""",
                (owner_token, batch_id, mode),
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
