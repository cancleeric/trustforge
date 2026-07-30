"""Single-host development/test adapter for formal-run idempotency.

This adapter is intentionally rejected in production.  It is not a shared,
multi-instance authority.
"""

from __future__ import annotations

import hmac
import json
import re
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .formal_run_idempotency import (
    AcquireResult,
    FormalRunIdentity,
    FormalRunLookup,
    FormalRunReceipt,
    HmacValue,
    IdempotencyUnavailable,
    IdempotencyInProgress,
    StaleFencingToken,
    TerminalSafeResponse,
    accepted_acquisition_epochs,
)


def _timestamp(value: datetime) -> float:
    if value.tzinfo is None:
        raise IdempotencyUnavailable("trusted clock unavailable")
    return value.astimezone(timezone.utc).timestamp()


_STRICT_ID = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


class SqliteFormalRunIdempotencyStore:
    """Development-only durable store with transaction-level fencing."""

    def __init__(self, path: str | Path, *, environment: str) -> None:
        if environment not in {"test", "development"}:
            raise IdempotencyUnavailable("SQLite idempotency authority is forbidden in production")
        requested_path = str(path)
        self._is_memory = requested_path == ":memory:"
        self._path = (
            f"file:formal-run-idempotency-{id(self)}?mode=memory&cache=shared"
            if self._is_memory
            else requested_path
        )
        self._keeper = (
            sqlite3.connect(self._path, uri=True, timeout=10, isolation_level=None)
            if self._is_memory
            else None
        )
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._path, uri=self._is_memory, timeout=10, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS formal_run_idempotency (
                    namespace TEXT NOT NULL,
                    scope_locator TEXT NOT NULL,
                    caller_key_id TEXT NOT NULL,
                    caller_scope_hmac TEXT NOT NULL,
                    key_key_id TEXT NOT NULL,
                    key_hmac TEXT NOT NULL,
                    key_epoch TEXT NOT NULL,
                    fingerprint_key_id TEXT NOT NULL,
                    request_fingerprint_hmac TEXT NOT NULL,
                    fingerprint_version TEXT NOT NULL,
                    state TEXT NOT NULL,
                    owner_fencing_token INTEGER NOT NULL,
                    lease_expires_at REAL,
                    receipt_body TEXT,
                    receipt_id TEXT,
                    question_id TEXT,
                    job_id TEXT,
                    result_id TEXT,
                    disposition TEXT,
                    locale TEXT,
                    operation_id TEXT,
                    outbox_state TEXT,
                    dispatch_state TEXT,
                    provider_operation_id TEXT,
                    cost_policy_version TEXT,
                    cost_policy_digest TEXT,
                    reservation_id TEXT,
                    max_reserved_cost TEXT,
                    settlement_state TEXT,
                    reconciliation_state TEXT,
                    terminal_error_code TEXT,
                    terminal_http_status INTEGER,
                    terminal_response_schema_version TEXT,
                    terminal_safe_response_body TEXT,
                    terminal_replay_headers TEXT,
                    terminal_response_digest TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    terminal_at REAL,
                    expires_at REAL,
                    PRIMARY KEY (namespace, caller_key_id, caller_scope_hmac, key_key_id, key_hmac)
                );
                CREATE TABLE IF NOT EXISTS formal_run_idempotency_tombstone (
                    namespace TEXT NOT NULL,
                    scope_locator TEXT NOT NULL,
                    caller_key_id TEXT NOT NULL,
                    caller_scope_hmac TEXT NOT NULL,
                    key_key_id TEXT NOT NULL,
                    key_hmac TEXT NOT NULL,
                    key_epoch TEXT NOT NULL,
                    retain_until REAL,
                    PRIMARY KEY (namespace, caller_key_id, caller_scope_hmac, key_key_id, key_hmac)
                );
                CREATE TABLE IF NOT EXISTS formal_run_content_guard (
                    namespace TEXT NOT NULL,
                    scope_locator TEXT NOT NULL,
                    content_key_id TEXT NOT NULL,
                    content_digest TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    result_id TEXT NOT NULL,
                    locale TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    PRIMARY KEY (
                        namespace, scope_locator, content_key_id, content_digest
                    )
                );
                CREATE UNIQUE INDEX IF NOT EXISTS uq_formal_operation_scope
                    ON formal_run_idempotency(namespace, scope_locator, operation_id)
                    WHERE operation_id IS NOT NULL AND disposition IN ('created','fresh-created');
                CREATE UNIQUE INDEX IF NOT EXISTS uq_formal_job_scope
                    ON formal_run_idempotency(namespace, scope_locator, job_id)
                    WHERE job_id IS NOT NULL AND disposition IN ('created','fresh-created');
                CREATE UNIQUE INDEX IF NOT EXISTS uq_formal_reservation_scope
                    ON formal_run_idempotency(namespace, scope_locator, reservation_id)
                    WHERE reservation_id IS NOT NULL AND disposition IN ('created','fresh-created');
                CREATE UNIQUE INDEX IF NOT EXISTS uq_formal_provider_operation_scope
                    ON formal_run_idempotency(namespace, scope_locator, provider_operation_id)
                    WHERE provider_operation_id IS NOT NULL AND disposition IN ('created','fresh-created');
                """
            )

    @staticmethod
    def _identity_args(identity: FormalRunIdentity) -> tuple[str, str, str, str, str, str]:
        return (
            identity.namespace,
            identity.scope_locator,
            identity.caller_scope_hmac.key_id,
            identity.caller_scope_hmac.digest,
            identity.key_hmac.key_id,
            identity.key_hmac.digest,
        )

    def _lookup_row(
        self, db: sqlite3.Connection, lookup: FormalRunLookup
    ) -> tuple[sqlite3.Row | None, FormalRunIdentity | None]:
        matches: list[tuple[sqlite3.Row, FormalRunIdentity]] = []
        for identity in (lookup.primary_identity, *lookup.candidate_identities):
            row = db.execute(
                """SELECT * FROM formal_run_idempotency
                   WHERE namespace=? AND scope_locator=? AND caller_key_id=? AND caller_scope_hmac=?
                     AND key_key_id=? AND key_hmac=?""",
                self._identity_args(identity),
            ).fetchone()
            if row is not None:
                matches.append((row, identity))
        if len(matches) > 1:
            raise IdempotencyUnavailable("ambiguous retained HMAC authority")
        return matches[0] if matches else (None, None)

    @staticmethod
    def _receipt(raw: str | None) -> FormalRunReceipt | None:
        try:
            return FormalRunReceipt(**json.loads(raw)) if raw else None
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise IdempotencyUnavailable("stored receipt is invalid") from exc

    @staticmethod
    def _terminal(row: sqlite3.Row) -> TerminalSafeResponse | None:
        required = (
            "terminal_error_code", "terminal_http_status",
            "terminal_response_schema_version", "terminal_safe_response_body",
            "terminal_replay_headers", "terminal_response_digest", "terminal_at", "expires_at",
        )
        if any(row[field] is None for field in required):
            raise IdempotencyUnavailable("stored terminal response is incomplete")
        if row["disposition"] not in {
            None, "created", "fresh-created", "reused", "relocalized"
        }:
            raise IdempotencyUnavailable("stored terminal disposition is invalid")
        if row["disposition"] is None and any(
            row[field] is not None
            for field in (
                "receipt_body", "receipt_id", "question_id", "job_id", "result_id",
                "operation_id", "outbox_state", "dispatch_state", "provider_operation_id",
                "cost_policy_version", "cost_policy_digest", "reservation_id",
                "max_reserved_cost", "settlement_state", "reconciliation_state",
            )
        ):
            raise IdempotencyUnavailable("stored pre-bind terminal authority is invalid")
        if row["disposition"] in {"created", "fresh-created"} and (
            row["outbox_state"] != "cancelled"
            or row["dispatch_state"] != "not_dispatched"
            or row["reservation_id"] is None
            or row["settlement_state"] != "released"
            or row["reconciliation_state"] != "reconciled"
        ):
            raise IdempotencyUnavailable("stored terminal cost settlement is invalid")
        if row["disposition"] in {"reused", "relocalized"} and (
            row["outbox_state"] != "none"
            or row["dispatch_state"] != "not_dispatched"
            or row["reservation_id"] is not None
            or row["settlement_state"] is not None
            or row["reconciliation_state"] is not None
        ):
            raise IdempotencyUnavailable("stored provider-free terminal state is invalid")
        try:
            response = TerminalSafeResponse(
                status=row["terminal_http_status"],
                code=row["terminal_error_code"],
                schema_version=row["terminal_response_schema_version"],
                body=json.loads(row["terminal_safe_response_body"]),
                replay_headers=json.loads(row["terminal_replay_headers"]),
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise IdempotencyUnavailable("stored terminal response is invalid") from exc
        if not hmac.compare_digest(response.digest(), row["terminal_response_digest"]):
            raise IdempotencyUnavailable("stored terminal response digest mismatch")
        return response

    def acquire(
        self,
        *,
        lookup: FormalRunLookup,
        now: datetime,
        lease_seconds: int,
    ) -> AcquireResult:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now_ts = _timestamp(now)
        args = self._identity_args(lookup.primary_identity)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            accepted_epochs = accepted_acquisition_epochs(now)
            row, matched_identity = self._lookup_row(db, lookup)
            if row is None:
                tombstones: list[tuple[FormalRunIdentity, sqlite3.Row]] = []
                for identity in (lookup.primary_identity, *lookup.candidate_identities):
                    tombstone_row = db.execute(
                        """SELECT key_epoch, retain_until FROM formal_run_idempotency_tombstone
                           WHERE namespace=? AND scope_locator=? AND caller_key_id=? AND caller_scope_hmac=?
                             AND key_key_id=? AND key_hmac=?""",
                        self._identity_args(identity),
                    ).fetchone()
                    if tombstone_row is not None:
                        tombstones.append((identity, tombstone_row))
                if len(tombstones) > 1:
                    raise IdempotencyUnavailable("ambiguous retained tombstone authority")
                if tombstones:
                    tombstone_identity, tombstone_row = tombstones[0]
                    if str(tombstone_row["key_epoch"]) != lookup.parsed_key.epoch:
                        raise IdempotencyUnavailable("stored tombstone epoch is invalid")
                    retain_until = tombstone_row["retain_until"]
                    if (
                        retain_until is not None
                        and retain_until <= now_ts
                        and lookup.parsed_key.epoch not in accepted_epochs
                    ):
                        db.execute(
                            """DELETE FROM formal_run_idempotency_tombstone
                               WHERE namespace=? AND scope_locator=? AND caller_key_id=?
                                 AND caller_scope_hmac=? AND key_key_id=? AND key_hmac=?""",
                            self._identity_args(tombstone_identity),
                        )
                    db.commit()
                    return AcquireResult("key_unavailable")
                if lookup.parsed_key.epoch not in accepted_epochs:
                    db.commit()
                    return AcquireResult("key_unavailable")
                db.execute(
                    """INSERT INTO formal_run_idempotency (
                        namespace, scope_locator, caller_key_id, caller_scope_hmac, key_key_id, key_hmac,
                        key_epoch, fingerprint_key_id, request_fingerprint_hmac,
                        fingerprint_version, state, owner_fencing_token, lease_expires_at,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'acquired', 1, ?, ?, ?)""",
                    (*args, lookup.parsed_key.epoch, lookup.primary_fingerprint.key_id,
                     lookup.primary_fingerprint.digest,
                     "analysis-question/v1", now_ts + lease_seconds, now_ts, now_ts),
                )
                db.commit()
                return AcquireResult(
                    "owner", fencing_token=1, authority_identity=lookup.primary_identity
                )
            assert matched_identity is not None
            fingerprints = (lookup.primary_fingerprint, *lookup.candidate_fingerprints)
            if (
                not any(
                    row["fingerprint_key_id"] == candidate.key_id
                    and hmac.compare_digest(row["request_fingerprint_hmac"], candidate.digest)
                    for candidate in fingerprints
                )
            ):
                db.commit()
                return AcquireResult("conflict")
            if row["state"] in {"bound", "execution_uncertain"}:
                receipt = self._receipt(row["receipt_body"])
                if receipt is None:
                    raise IdempotencyUnavailable("replay row has no receipt")
                db.commit()
                return AcquireResult(
                    "replay", receipt=receipt, authority_identity=matched_identity
                )
            if row["state"] == "terminal_failed":
                if row["expires_at"] is not None and row["expires_at"] <= now_ts:
                    db.execute(
                        """INSERT OR IGNORE INTO formal_run_idempotency_tombstone
                           (namespace, scope_locator, caller_key_id, caller_scope_hmac, key_key_id,
                            key_hmac, key_epoch, retain_until)
                           VALUES (?, ?, ?, ?, ?, ?, ?, NULL)""",
                        (*self._identity_args(matched_identity), row["key_epoch"]),
                    )
                    db.execute(
                        """DELETE FROM formal_run_idempotency
                           WHERE namespace=? AND scope_locator=? AND caller_key_id=? AND caller_scope_hmac=?
                             AND key_key_id=? AND key_hmac=?""",
                        self._identity_args(matched_identity),
                    )
                    db.commit()
                    return AcquireResult("key_unavailable")
                db.commit()
                return AcquireResult(
                    "terminal_replay", terminal_response=self._terminal(row),
                    authority_identity=matched_identity,
                )
            if row["lease_expires_at"] is not None and row["lease_expires_at"] > now_ts:
                db.commit()
                return AcquireResult("in_progress", authority_identity=matched_identity)
            if row["state"] != "acquired" or not isinstance(row["owner_fencing_token"], int):
                raise IdempotencyUnavailable("unknown idempotency state")
            token = int(row["owner_fencing_token"]) + 1
            cursor = db.execute(
                """UPDATE formal_run_idempotency
                   SET owner_fencing_token=?, lease_expires_at=?, updated_at=?
                   WHERE namespace=? AND scope_locator=? AND caller_key_id=? AND caller_scope_hmac=?
                     AND key_key_id=? AND key_hmac=? AND owner_fencing_token=?
                     AND state='acquired'""",
                (
                    token,
                    now_ts + lease_seconds,
                    now_ts,
                    *self._identity_args(matched_identity),
                    row["owner_fencing_token"],
                ),
            )
            if cursor.rowcount != 1:
                raise IdempotencyUnavailable("fenced takeover lost authority")
            db.commit()
            return AcquireResult(
                "owner", fencing_token=token, authority_identity=matched_identity
            )

    def _fenced_update(
        self,
        db: sqlite3.Connection,
        identity: FormalRunIdentity,
        fencing_token: int,
        sql: str,
        values: tuple[object, ...],
        allowed_state: str | tuple[str, ...] = "acquired",
    ) -> None:
        states = (allowed_state,) if isinstance(allowed_state, str) else allowed_state
        if not states:
            raise ValueError("at least one allowed state is required")
        state_placeholders = ",".join("?" for _ in states)
        cursor = db.execute(
            sql + """ WHERE namespace=? AND scope_locator=? AND caller_key_id=? AND caller_scope_hmac=?
                     AND key_key_id=? AND key_hmac=? AND owner_fencing_token=?
                     AND state IN (""" + state_placeholders + ")",
            (*values, *self._identity_args(identity), fencing_token, *states),
        )
        if cursor.rowcount != 1:
            raise StaleFencingToken("stale or non-owning fencing token")

    def bind(
        self,
        *,
        identity: FormalRunIdentity,
        fencing_token: int,
        receipt: FormalRunReceipt,
        operation_id: str,
        outbox_state: str,
        dispatch_state: str,
        reservation_id: str | None,
        max_reserved_cost: str | None,
        now: datetime,
        provider_operation_id: str | None = None,
        cost_policy_version: str | None = None,
        cost_policy_digest: str | None = None,
        settlement_state: str | None = None,
        reconciliation_state: str | None = None,
    ) -> None:
        now_ts = _timestamp(now)
        chargeable = receipt.disposition in {"created", "fresh-created"}
        if _STRICT_ID.fullmatch(operation_id) is None:
            raise ValueError("invalid operation id")
        if receipt.state != "accepted":
            raise ValueError("bind requires an accepted receipt")
        if chargeable:
            try:
                reserved_cost = Decimal(max_reserved_cost or "")
            except InvalidOperation as exc:
                raise ValueError("maximum reserved cost must be a positive finite decimal") from exc
            if (
                reservation_id is None
                or not reserved_cost.is_finite()
                or reserved_cost <= 0
                or outbox_state != "pending"
                or dispatch_state != "not_dispatched"
                or provider_operation_id is None
                or cost_policy_version is None
                or cost_policy_digest is None
                or settlement_state is None
                or reconciliation_state is None
                or _STRICT_ID.fullmatch(reservation_id) is None
                or _STRICT_ID.fullmatch(provider_operation_id) is None
                or _STRICT_ID.fullmatch(cost_policy_version) is None
                or _HEX_64.fullmatch(cost_policy_digest) is None
                or settlement_state != "reserved"
                or reconciliation_state != "pending"
            ):
                raise ValueError(
                    "chargeable disposition requires a policy-bound, pending undispatched reserved outbox"
                )
        elif (
            reservation_id is not None
            or max_reserved_cost is not None
            or outbox_state != "none"
            or dispatch_state != "not_dispatched"
            or provider_operation_id is not None
            or cost_policy_version is not None
            or cost_policy_digest is not None
            or settlement_state is not None
            or reconciliation_state is not None
        ):
            raise ValueError("provider-free disposition forbids reservation and dispatch")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                self._fenced_update(
                    db, identity, fencing_token,
                    """UPDATE formal_run_idempotency SET state='bound', receipt_body=?,
                       receipt_id=?, question_id=?, job_id=?, result_id=?, disposition=?, locale=?,
                       operation_id=?, outbox_state=?, dispatch_state=?, provider_operation_id=?,
                       cost_policy_version=?, cost_policy_digest=?, reservation_id=?,
                       max_reserved_cost=?, settlement_state=?, reconciliation_state=?,
                       lease_expires_at=NULL, updated_at=?""",
                    (json.dumps(receipt.public_body(), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                     receipt.receipt_id, receipt.question_id, receipt.job_id, receipt.result_id,
                     receipt.disposition, receipt.locale, operation_id, outbox_state, dispatch_state,
                     provider_operation_id, cost_policy_version, cost_policy_digest, reservation_id,
                     max_reserved_cost, settlement_state, reconciliation_state, now_ts),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("formal-run identity is already bound in this caller scope") from exc
            db.commit()

    def bind_with_content_decision(
        self,
        *,
        identity: FormalRunIdentity,
        fencing_token: int,
        receipt: FormalRunReceipt,
        operation_id: str,
        content: HmacValue,
        fresh: bool,
        now: datetime,
        reservation_id: str,
        max_reserved_cost: str,
        provider_operation_id: str,
        cost_policy_version: str,
        cost_policy_digest: str,
    ) -> FormalRunReceipt | None:
        """Atomically choose scoped content reuse or a new chargeable run."""
        if not isinstance(fresh, bool):
            raise ValueError("fresh must be boolean")
        if receipt.result_id is None:
            raise ValueError("content decision requires deterministic result id")
        if receipt.disposition != ("fresh-created" if fresh else "created"):
            raise ValueError("receipt disposition does not match fresh decision")
        now_ts = _timestamp(now)
        content_args = (
            identity.namespace,
            identity.scope_locator,
            content.key_id,
            content.digest,
        )
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not fresh:
                guard = db.execute(
                    """SELECT job_id, result_id, locale, expires_at
                       FROM formal_run_content_guard
                       WHERE namespace=? AND scope_locator=? AND content_key_id=?
                         AND content_digest=?""",
                    content_args,
                ).fetchone()
                if guard is not None and guard["expires_at"] > now_ts:
                    if guard["locale"] != receipt.locale:
                        db.commit()
                        return None
                    reused = replace(
                        receipt,
                        job_id=str(guard["job_id"]),
                        result_id=str(guard["result_id"]),
                        disposition="reused",
                    )
                    self._fenced_update(
                        db,
                        identity,
                        fencing_token,
                        """UPDATE formal_run_idempotency SET state='bound', receipt_body=?,
                           receipt_id=?, question_id=?, job_id=?, result_id=?,
                           disposition=?, locale=?, operation_id=?, outbox_state='none',
                           dispatch_state='not_dispatched', lease_expires_at=NULL,
                           updated_at=?""",
                        (
                            json.dumps(
                                reused.public_body(),
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                            reused.receipt_id,
                            reused.question_id,
                            reused.job_id,
                            reused.result_id,
                            reused.disposition,
                            reused.locale,
                            operation_id,
                            now_ts,
                        ),
                    )
                    db.commit()
                    return reused

            # Validate all chargeable invariants before mutating either table.
            try:
                reserved_cost = Decimal(max_reserved_cost)
            except InvalidOperation as exc:
                raise ValueError(
                    "maximum reserved cost must be a positive finite decimal"
                ) from exc
            if (
                _STRICT_ID.fullmatch(operation_id) is None
                or _STRICT_ID.fullmatch(reservation_id) is None
                or _STRICT_ID.fullmatch(provider_operation_id) is None
                or _STRICT_ID.fullmatch(cost_policy_version) is None
                or _HEX_64.fullmatch(cost_policy_digest) is None
                or not reserved_cost.is_finite()
                or reserved_cost <= 0
            ):
                raise ValueError("invalid chargeable content decision metadata")
            try:
                self._fenced_update(
                    db,
                    identity,
                    fencing_token,
                    """UPDATE formal_run_idempotency SET state='bound', receipt_body=?,
                       receipt_id=?, question_id=?, job_id=?, result_id=?, disposition=?,
                       locale=?, operation_id=?, outbox_state='pending',
                       dispatch_state='not_dispatched', provider_operation_id=?,
                       cost_policy_version=?, cost_policy_digest=?, reservation_id=?,
                       max_reserved_cost=?, settlement_state='reserved',
                       reconciliation_state='pending', lease_expires_at=NULL, updated_at=?""",
                    (
                        json.dumps(
                            receipt.public_body(),
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        receipt.receipt_id,
                        receipt.question_id,
                        receipt.job_id,
                        receipt.result_id,
                        receipt.disposition,
                        receipt.locale,
                        operation_id,
                        provider_operation_id,
                        cost_policy_version,
                        cost_policy_digest,
                        reservation_id,
                        max_reserved_cost,
                        now_ts,
                    ),
                )
                if not fresh:
                    db.execute(
                        """INSERT INTO formal_run_content_guard (
                               namespace, scope_locator, content_key_id, content_digest,
                               job_id, result_id, locale, expires_at
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(namespace, scope_locator, content_key_id, content_digest)
                           DO UPDATE SET job_id=excluded.job_id, result_id=excluded.result_id,
                               locale=excluded.locale, expires_at=excluded.expires_at
                           WHERE formal_run_content_guard.expires_at<=?""",
                        (
                            *content_args,
                            receipt.job_id,
                            receipt.result_id,
                            receipt.locale,
                            now_ts + 300,
                            now_ts,
                        ),
                    )
            except sqlite3.IntegrityError as exc:
                raise ValueError(
                    "formal-run identity is already bound in this caller scope"
                ) from exc
            db.commit()
            return receipt

    def claim_dispatch(
        self, *, identity: FormalRunIdentity, fencing_token: int, now: datetime
    ) -> str:
        now_ts = _timestamp(now)
        args = self._identity_args(identity)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT provider_operation_id FROM formal_run_idempotency
                   WHERE namespace=? AND scope_locator=? AND caller_key_id=? AND caller_scope_hmac=?
                     AND key_key_id=? AND key_hmac=?""",
                args,
            ).fetchone()
            if row is None or row["provider_operation_id"] is None:
                raise ValueError("chargeable provider operation is not bound")
            cursor = db.execute(
                """UPDATE formal_run_idempotency
                   SET outbox_state='claimed', dispatch_state='possibly_dispatched', updated_at=?
                   WHERE namespace=? AND scope_locator=? AND caller_key_id=? AND caller_scope_hmac=?
                     AND key_key_id=? AND key_hmac=? AND owner_fencing_token=?
                     AND state='bound' AND disposition IN ('created','fresh-created')
                     AND reservation_id IS NOT NULL AND outbox_state='pending'
                     AND dispatch_state='not_dispatched'""",
                (now_ts, *args, fencing_token),
            )
            if cursor.rowcount != 1:
                raise IdempotencyInProgress("dispatch is already claimed or fencing token is stale")
            db.commit()
            return str(row["provider_operation_id"])

    def pending_projection_token(self, *, identity: FormalRunIdentity) -> int | None:
        """Return recovery authority only before any dispatch claim."""
        with self._connect() as db:
            row = db.execute(
                """SELECT owner_fencing_token, state, disposition, outbox_state,
                          dispatch_state
                   FROM formal_run_idempotency
                   WHERE namespace=? AND scope_locator=? AND caller_key_id=?
                     AND caller_scope_hmac=? AND key_key_id=? AND key_hmac=?""",
                self._identity_args(identity),
            ).fetchone()
        if row is None:
            return None
        if (
            row["state"] == "bound"
            and row["disposition"] in {"created", "fresh-created"}
            and row["outbox_state"] == "pending"
            and row["dispatch_state"] == "not_dispatched"
        ):
            token = row["owner_fencing_token"]
            if isinstance(token, int) and token > 0:
                return token
            raise IdempotencyUnavailable("stored fencing token is invalid")
        return None

    def dispatch_resolution(
        self, *, identity: FormalRunIdentity, fencing_token: int
    ) -> str:
        with self._connect() as db:
            row = db.execute(
                """SELECT owner_fencing_token, state, outbox_state, dispatch_state
                   FROM formal_run_idempotency
                   WHERE namespace=? AND scope_locator=? AND caller_key_id=?
                     AND caller_scope_hmac=? AND key_key_id=? AND key_hmac=?""",
                self._identity_args(identity),
            ).fetchone()
        if row is None or row["owner_fencing_token"] != fencing_token:
            return "none"
        if row["state"] == "execution_uncertain" or row["dispatch_state"] == "uncertain":
            return "uncertain"
        if row["outbox_state"] == "pending" and row["dispatch_state"] == "not_dispatched":
            return "pending"
        if row["outbox_state"] == "claimed" and row["dispatch_state"] == "possibly_dispatched":
            return "claimed"
        if row["outbox_state"] == "completed" and row["dispatch_state"] == "dispatched":
            return "completed"
        return "none"

    def provider_operation(
        self, *, identity: FormalRunIdentity, fencing_token: int
    ) -> str | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT provider_operation_id FROM formal_run_idempotency
                   WHERE namespace=? AND scope_locator=? AND caller_key_id=?
                     AND caller_scope_hmac=? AND key_key_id=? AND key_hmac=?
                     AND owner_fencing_token=?""",
                (*self._identity_args(identity), fencing_token),
            ).fetchone()
        value = row["provider_operation_id"] if row else None
        return str(value) if value is not None else None

    def reservation_details(
        self, *, identity: FormalRunIdentity, fencing_token: int
    ) -> tuple[str, str] | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT reservation_id,max_reserved_cost FROM formal_run_idempotency
                   WHERE namespace=? AND scope_locator=? AND caller_key_id=?
                     AND caller_scope_hmac=? AND key_key_id=? AND key_hmac=?
                     AND owner_fencing_token=?""",
                (*self._identity_args(identity), fencing_token),
            ).fetchone()
        if row is None or row["reservation_id"] is None or row["max_reserved_cost"] is None:
            return None
        return str(row["reservation_id"]), str(row["max_reserved_cost"])

    def mark_execution_uncertain(
        self, *, identity: FormalRunIdentity, fencing_token: int, now: datetime
    ) -> None:
        now_ts = _timestamp(now)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT receipt_body FROM formal_run_idempotency
                   WHERE namespace=? AND scope_locator=? AND caller_key_id=? AND caller_scope_hmac=?
                     AND key_key_id=? AND key_hmac=? AND owner_fencing_token=?
                     AND state='bound' AND disposition IN ('created','fresh-created')
                     AND reservation_id IS NOT NULL AND outbox_state='claimed'
                     AND dispatch_state='possibly_dispatched'""",
                (*self._identity_args(identity), fencing_token),
            ).fetchone()
            if row is None:
                raise IdempotencyInProgress("dispatch was not authoritatively claimed")
            original = self._receipt(row["receipt_body"])
            if original is None or original.state != "accepted":
                raise IdempotencyUnavailable("bound row has no accepted receipt")
            receipt = replace(original, state="execution_uncertain")
            self._fenced_update(
                db, identity, fencing_token,
                """UPDATE formal_run_idempotency SET state='execution_uncertain',
                   receipt_body=?, dispatch_state='uncertain', lease_expires_at=NULL, updated_at=?""",
                (json.dumps(receipt.public_body(), ensure_ascii=False, sort_keys=True, separators=(",", ":")), now_ts),
                allowed_state="bound",
            )
            db.commit()

    def complete_dispatch(
        self, *, identity: FormalRunIdentity, fencing_token: int, now: datetime
    ) -> None:
        now_ts = _timestamp(now)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                """UPDATE formal_run_idempotency
                   SET outbox_state='completed', dispatch_state='dispatched', updated_at=?
                   WHERE namespace=? AND scope_locator=? AND caller_key_id=?
                     AND caller_scope_hmac=? AND key_key_id=? AND key_hmac=?
                     AND owner_fencing_token=? AND state='bound'
                     AND disposition IN ('created','fresh-created')
                     AND outbox_state='claimed'
                     AND dispatch_state='possibly_dispatched'""",
                (now_ts, *self._identity_args(identity), fencing_token),
            )
            if cursor.rowcount != 1:
                raise IdempotencyInProgress("dispatch is not authoritatively claimed")
            db.commit()

    def fail_terminal(
        self,
        *,
        identity: FormalRunIdentity,
        fencing_token: int,
        response: TerminalSafeResponse,
        now: datetime,
        expires_at: datetime,
    ) -> None:
        now_ts = _timestamp(now)
        expires_ts = _timestamp(expires_at)
        if expires_ts - now_ts < 86_400:
            raise ValueError("terminal replay SLA must be at least 24 hours")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                """UPDATE formal_run_idempotency SET state='terminal_failed',
                   terminal_error_code=?, terminal_http_status=?,
                   terminal_response_schema_version=?, terminal_safe_response_body=?,
                   terminal_replay_headers=?, terminal_response_digest=?,
                   terminal_at=?, expires_at=?, lease_expires_at=NULL, updated_at=?,
                   outbox_state=CASE WHEN outbox_state='pending' THEN 'cancelled' ELSE outbox_state END,
                   settlement_state=CASE WHEN settlement_state='reserved' THEN 'released'
                                         ELSE settlement_state END,
                   reconciliation_state=CASE WHEN reconciliation_state='pending' THEN 'reconciled'
                                             ELSE reconciliation_state END
                   WHERE namespace=? AND scope_locator=? AND caller_key_id=? AND caller_scope_hmac=?
                     AND key_key_id=? AND key_hmac=? AND owner_fencing_token=?
                     AND (
                       state='acquired'
                       OR (
                         state='bound' AND dispatch_state='not_dispatched'
                         AND (
                           (disposition IN ('created','fresh-created')
                            AND outbox_state='pending' AND reservation_id IS NOT NULL
                            AND settlement_state='reserved' AND reconciliation_state='pending')
                           OR
                           (disposition IN ('reused','relocalized')
                            AND outbox_state='none' AND reservation_id IS NULL
                            AND settlement_state IS NULL AND reconciliation_state IS NULL)
                         )
                       )
                     )""",
                (response.code, response.status, response.schema_version, response.canonical_body(),
                 json.dumps(dict(response.replay_headers), sort_keys=True, separators=(",", ":")),
                 response.digest(), now_ts, expires_ts, now_ts,
                 *self._identity_args(identity), fencing_token),
            )
            if cursor.rowcount != 1:
                raise StaleFencingToken(
                    "stale owner or request may already have been dispatched"
                )
            db.commit()

    def tombstone(
        self,
        *,
        identity: FormalRunIdentity,
        now: datetime,
        retain_until: datetime | None,
    ) -> None:
        now_ts = _timestamp(now)
        retain_ts = _timestamp(retain_until) if retain_until is not None else None
        args = self._identity_args(identity)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT state, expires_at, key_epoch FROM formal_run_idempotency
                   WHERE namespace=? AND scope_locator=? AND caller_key_id=? AND caller_scope_hmac=?
                     AND key_key_id=? AND key_hmac=?""",
                args,
            ).fetchone()
            if (
                row is None
                or row["state"] != "terminal_failed"
                or row["expires_at"] is None
                or row["expires_at"] > now_ts
            ):
                raise ValueError("only an expired terminal record may be tombstoned")
            db.execute(
                """INSERT OR REPLACE INTO formal_run_idempotency_tombstone
                   (namespace, scope_locator, caller_key_id, caller_scope_hmac, key_key_id,
                    key_hmac, key_epoch, retain_until)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (*args, row["key_epoch"], retain_ts),
            )
            db.execute(
                """DELETE FROM formal_run_idempotency
                   WHERE namespace=? AND scope_locator=? AND caller_key_id=? AND caller_scope_hmac=?
                     AND key_key_id=? AND key_hmac=?""",
                args,
            )
            db.commit()
