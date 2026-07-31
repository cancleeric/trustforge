"""Idempotent tokenized budget reservations for formal runs."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import BotoCoreError, ClientError

from .formal_run_coordinator import FormalBudgetReservation
from .formal_run_idempotency import IdempotencyUnavailable

_SERIALIZER = TypeSerializer()
_DESERIALIZER = TypeDeserializer()


def _marshal(value: dict[str, object]) -> dict[str, dict[str, object]]:
    return {key: _SERIALIZER.serialize(item) for key, item in value.items()}


def _unmarshal(value: dict[str, object]) -> dict[str, object]:
    return {key: _DESERIALIZER.deserialize(item) for key, item in value.items()}


class SqliteFormalBudgetAuthority:
    """Single-host test/development token authority."""

    def __init__(self, path: str | Path, *, environment: str) -> None:
        if environment not in {"test", "development"}:
            raise IdempotencyUnavailable("SQLite budget authority is forbidden")
        self._path = str(path)
        with sqlite3.connect(self._path) as db:
            db.executescript(
                """CREATE TABLE IF NOT EXISTS formal_budget_day (
                     day TEXT PRIMARY KEY, reserved_total TEXT NOT NULL,
                     spent_total TEXT NOT NULL DEFAULT '0'
                   );
                   CREATE TABLE IF NOT EXISTS formal_budget_reservation (
                     reservation_id TEXT PRIMARY KEY, day TEXT NOT NULL,
                     amount TEXT NOT NULL, state TEXT NOT NULL,
                     actual_cost TEXT
                   );"""
            )
            columns = {
                row[1]
                for row in db.execute(
                    "PRAGMA table_info(formal_budget_reservation)"
                ).fetchall()
            }
            if "actual_cost" not in columns:
                db.execute(
                    "ALTER TABLE formal_budget_reservation ADD COLUMN actual_cost TEXT"
                )

    def reserve(
        self, *, reservation_id: str, spent: Decimal, cost: Decimal, cap: Decimal, now: datetime
    ) -> FormalBudgetReservation | None:
        if now.tzinfo is None or any(not value.is_finite() for value in (spent, cost, cap)):
            raise IdempotencyUnavailable("trusted budget inputs unavailable")
        if cost <= 0 or cap <= 0:
            return None
        candidate = FormalBudgetReservation(reservation_id, str(cost))
        day = now.astimezone(timezone.utc).date().isoformat()
        with sqlite3.connect(self._path, isolation_level=None, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                """SELECT amount,state FROM formal_budget_reservation
                   WHERE reservation_id=?""",
                (reservation_id,),
            ).fetchone()
            if existing is not None:
                db.commit()
                return candidate if existing == (str(cost), "reserved") else None
            row = db.execute(
                "SELECT reserved_total,spent_total FROM formal_budget_day WHERE day=?", (day,)
            ).fetchone()
            reserved = Decimal(row[0]) if row else Decimal("0")
            settled = Decimal(row[1]) if row else Decimal("0")
            if spent + settled + reserved + cost > cap:
                db.commit()
                return None
            db.execute(
                """INSERT INTO formal_budget_day(day,reserved_total,spent_total)
                   VALUES(?,?,?)
                   ON CONFLICT(day) DO UPDATE SET
                     reserved_total=excluded.reserved_total,
                     spent_total=excluded.spent_total""",
                (day, str(reserved + cost), str(settled)),
            )
            db.execute(
                """INSERT INTO formal_budget_reservation
                   (reservation_id,day,amount,state) VALUES(?,?,?,'reserved')""",
                (reservation_id, day, str(cost)),
            )
            db.commit()
        return candidate

    def release(self, reservation: FormalBudgetReservation) -> bool:
        with sqlite3.connect(self._path, isolation_level=None, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT day,amount,state FROM formal_budget_reservation
                   WHERE reservation_id=?""",
                (reservation.reservation_id,),
            ).fetchone()
            if row is None or row[2] in {"released", "settled"}:
                db.commit()
                return False
            if (
                row[2] not in {"reserved", "reserved_uncertain"}
                or row[1] != reservation.max_reserved_cost
            ):
                raise IdempotencyUnavailable("budget reservation amount mismatch")
            total_row = db.execute(
                "SELECT reserved_total FROM formal_budget_day WHERE day=?", (row[0],)
            ).fetchone()
            if total_row is None or Decimal(total_row[0]) < Decimal(row[1]):
                raise IdempotencyUnavailable("budget counter is inconsistent")
            token_cas = db.execute(
                """UPDATE formal_budget_reservation SET state='released'
                   WHERE reservation_id=? AND state IN ('reserved','reserved_uncertain')""",
                (reservation.reservation_id,),
            )
            if token_cas.rowcount != 1:
                db.rollback()
                return False
            db.execute(
                "UPDATE formal_budget_day SET reserved_total=? WHERE day=?",
                (str(Decimal(total_row[0]) - Decimal(row[1])), row[0]),
            )
            db.commit()
            return True

    def lookup(self, reservation_id: str) -> FormalBudgetReservation | None:
        with sqlite3.connect(self._path) as db:
            row = db.execute(
                """SELECT amount,state FROM formal_budget_reservation
                   WHERE reservation_id=?""",
                (reservation_id,),
            ).fetchone()
        if row is None or row[1] not in {"reserved", "reserved_uncertain"}:
            return None
        return FormalBudgetReservation(reservation_id, row[0])

    def mark_uncertain(self, reservation: FormalBudgetReservation) -> bool:
        """Durably retain a token whose provider usage outcome is unknown."""
        with sqlite3.connect(self._path, isolation_level=None, timeout=10) as db:
            cursor = db.execute(
                """UPDATE formal_budget_reservation SET state='reserved_uncertain'
                   WHERE reservation_id=? AND amount=? AND state='reserved'""",
                (reservation.reservation_id, reservation.max_reserved_cost),
            )
            return cursor.rowcount == 1

    def settle(self, reservation: FormalBudgetReservation, actual_cost: Decimal) -> bool:
        reserved_maximum = Decimal(reservation.max_reserved_cost)
        if (
            not actual_cost.is_finite()
            or actual_cost < 0
            or actual_cost > reserved_maximum
        ):
            raise IdempotencyUnavailable("actual cost is invalid")
        with sqlite3.connect(self._path, isolation_level=None, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT day,amount,state,actual_cost FROM formal_budget_reservation
                   WHERE reservation_id=?""",
                (reservation.reservation_id,),
            ).fetchone()
            if row is None or row[2] == "released":
                db.commit()
                return False
            if row[2] == "settled":
                db.commit()
                return row[3] == str(actual_cost)
            if (
                row[2] not in {"reserved", "reserved_uncertain"}
                or row[1] != reservation.max_reserved_cost
            ):
                raise IdempotencyUnavailable("budget reservation is inconsistent")
            totals = db.execute(
                """SELECT reserved_total,spent_total FROM formal_budget_day
                   WHERE day=?""",
                (row[0],),
            ).fetchone()
            amount = Decimal(row[1])
            if totals is None or Decimal(totals[0]) < amount:
                raise IdempotencyUnavailable("budget counter is inconsistent")
            token_cas = db.execute(
                """UPDATE formal_budget_reservation
                   SET state='settled',actual_cost=?
                   WHERE reservation_id=? AND state IN ('reserved','reserved_uncertain')""",
                (str(actual_cost), reservation.reservation_id),
            )
            if token_cas.rowcount != 1:
                db.rollback()
                return False
            db.execute(
                """UPDATE formal_budget_day SET reserved_total=?,spent_total=?
                   WHERE day=?""",
                (
                    str(Decimal(totals[0]) - amount),
                    str(Decimal(totals[1]) + actual_cost),
                    row[0],
                ),
            )
            db.commit()
            return True


class DynamoDbFormalBudgetAuthority:
    """Shared token authority using the formal-run DynamoDB table."""

    def __init__(self, client, *, table_name: str) -> None:
        self._client = client
        self._table = table_name

    @staticmethod
    def _day(now: datetime) -> str:
        if now.tzinfo is None:
            raise IdempotencyUnavailable("trusted budget clock unavailable")
        return now.astimezone(timezone.utc).date().isoformat()

    def reserve(
        self, *, reservation_id: str, spent: Decimal, cost: Decimal, cap: Decimal, now: datetime
    ) -> FormalBudgetReservation | None:
        if any(not value.is_finite() for value in (spent, cost, cap)) or cost <= 0:
            raise IdempotencyUnavailable("trusted budget inputs unavailable")
        candidate = FormalBudgetReservation(reservation_id, str(cost))
        day = self._day(now)
        counter_key = {"pk": f"formal_budget#{day}", "sk": "counter"}
        token_key = {"pk": f"formal_budget_reservation#{reservation_id}", "sk": "token"}
        try:
            existing = self._client.get_item(
                TableName=self._table, Key=_marshal(token_key), ConsistentRead=True
            ).get("Item")
            if isinstance(existing, dict):
                item = _unmarshal(existing)
                if item.get("state") == "reserved" and item.get("amount") == cost:
                    return candidate
                return None
            for _attempt in range(3):
                counter_raw = self._client.get_item(
                    TableName=self._table,
                    Key=_marshal(counter_key),
                    ConsistentRead=True,
                ).get("Item")
                counter = _unmarshal(counter_raw) if isinstance(counter_raw, dict) else {}
                reserved = counter.get("reserved_total", Decimal("0"))
                settled = counter.get("spent_total", Decimal("0"))
                if not isinstance(reserved, Decimal) or not isinstance(settled, Decimal):
                    raise IdempotencyUnavailable("budget counter is invalid")
                baseline = spent + settled
                if baseline + reserved + cost > cap:
                    return None
                if counter:
                    condition = "reserved_total=:old_reserved AND spent_total=:old_spent"
                    values = {
                        ":new_reserved": reserved + cost,
                        ":new_spent": settled,
                        ":old_reserved": reserved,
                        ":old_spent": settled,
                    }
                else:
                    condition = "attribute_not_exists(pk)"
                    values = {
                        ":new_reserved": reserved + cost,
                        ":new_spent": settled,
                    }
                try:
                    self._client.transact_write_items(
                        TransactItems=[
                    {
                        "Update": {
                            "TableName": self._table,
                            "Key": _marshal(counter_key),
                            "UpdateExpression": (
                                "SET reserved_total=:new_reserved, spent_total=:new_spent"
                            ),
                            "ConditionExpression": condition,
                            "ExpressionAttributeValues": _marshal(values),
                        }
                    },
                    {
                        "Put": {
                            "TableName": self._table,
                            "Item": _marshal(
                                {
                                    **token_key,
                                    "entity_type": "formal_budget_reservation",
                                    "reservation_id": reservation_id,
                                    "day": day,
                                    "amount": cost,
                                    "state": "reserved",
                                }
                            ),
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    },
                        ]
                    )
                    break
                except ClientError as exc:
                    if exc.response.get("Error", {}).get("Code") != "TransactionCanceledException":
                        raise
                    reasons = exc.response.get("CancellationReasons", [])
                    # Counter CAS races are retried; token conflicts are
                    # resolved by the strong read at the top of reserve().
                    if (
                        reasons
                        and isinstance(reasons[0], dict)
                        and reasons[0].get("Code") == "ConditionalCheckFailed"
                    ):
                        continue
                    raise
            else:
                raise IdempotencyUnavailable("budget reservation contention exceeded")
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "TransactionCanceledException":
                reasons = exc.response.get("CancellationReasons", [])
                if any(
                    isinstance(reason, dict)
                    and reason.get("Code") == "ConditionalCheckFailed"
                    for reason in reasons
                ):
                    return None
            raise IdempotencyUnavailable("budget reservation unavailable") from exc
        except (BotoCoreError, TimeoutError) as exc:
            # Unknown transaction outcome must fail closed.  A token may exist;
            # a later reconciliation scan can recover it by its durable ID.
            raise IdempotencyUnavailable("budget reservation outcome is unknown") from exc
        return candidate

    def release(self, reservation: FormalBudgetReservation) -> bool:
        token_key = {"pk": f"formal_budget_reservation#{reservation.reservation_id}", "sk": "token"}
        try:
            response = self._client.get_item(
                TableName=self._table, Key=_marshal(token_key), ConsistentRead=True
            )
            raw = response.get("Item")
            if not isinstance(raw, dict):
                return False
            item = _unmarshal(raw)
            if item.get("state") == "released":
                return False
            amount = item.get("amount")
            day = item.get("day")
            if amount != Decimal(reservation.max_reserved_cost) or not isinstance(day, str):
                raise IdempotencyUnavailable("budget reservation is inconsistent")
            self._client.transact_write_items(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": self._table,
                            "Key": _marshal({"pk": f"formal_budget#{day}", "sk": "counter"}),
                            "UpdateExpression": "ADD reserved_total :negative",
                            "ConditionExpression": "reserved_total>=:amount",
                            "ExpressionAttributeValues": _marshal(
                                {":negative": -amount, ":amount": amount}
                            ),
                        }
                    },
                    {
                        "Update": {
                            "TableName": self._table,
                            "Key": _marshal(token_key),
                            "UpdateExpression": "SET #state=:released",
                            "ConditionExpression": (
                                "#state IN (:reserved,:uncertain) AND amount=:amount"
                            ),
                            "ExpressionAttributeNames": {"#state": "state"},
                            "ExpressionAttributeValues": _marshal(
                                {
                                    ":released": "released",
                                    ":reserved": "reserved",
                                    ":uncertain": "reserved_uncertain",
                                    ":amount": amount,
                                }
                            ),
                        }
                    },
                ]
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "TransactionCanceledException":
                current = self._client.get_item(
                    TableName=self._table, Key=_marshal(token_key), ConsistentRead=True
                ).get("Item")
                if isinstance(current, dict) and _unmarshal(current).get("state") == "released":
                    return False
            raise IdempotencyUnavailable("budget release unavailable") from exc
        except (BotoCoreError, TimeoutError) as exc:
            raise IdempotencyUnavailable("budget release outcome is unknown") from exc
        return True

    def mark_uncertain(self, reservation: FormalBudgetReservation) -> bool:
        """Persist an operator-visible retained-token disposition."""
        token_key = {
            "pk": f"formal_budget_reservation#{reservation.reservation_id}",
            "sk": "token",
        }
        try:
            self._client.update_item(
                TableName=self._table,
                Key=_marshal(token_key),
                UpdateExpression="SET #state=:uncertain",
                ConditionExpression="#state=:reserved AND amount=:amount",
                ExpressionAttributeNames={"#state": "state"},
                ExpressionAttributeValues=_marshal(
                    {
                        ":uncertain": "reserved_uncertain",
                        ":reserved": "reserved",
                        ":amount": Decimal(reservation.max_reserved_cost),
                    }
                ),
            )
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise IdempotencyUnavailable(
                "budget uncertainty disposition unavailable"
            ) from exc

    def lookup(self, reservation_id: str) -> FormalBudgetReservation | None:
        token_key = {
            "pk": f"formal_budget_reservation#{reservation_id}",
            "sk": "token",
        }
        try:
            raw = self._client.get_item(
                TableName=self._table,
                Key=_marshal(token_key),
                ConsistentRead=True,
            ).get("Item")
        except (ClientError, BotoCoreError, TimeoutError) as exc:
            raise IdempotencyUnavailable("budget token lookup unavailable") from exc
        if not isinstance(raw, dict):
            return None
        item = _unmarshal(raw)
        amount = item.get("amount")
        if (
            item.get("state") not in {"reserved", "reserved_uncertain"}
            or not isinstance(amount, Decimal)
        ):
            return None
        return FormalBudgetReservation(reservation_id, str(amount))

    def settle(self, reservation: FormalBudgetReservation, actual_cost: Decimal) -> bool:
        reserved_maximum = Decimal(reservation.max_reserved_cost)
        if (
            not actual_cost.is_finite()
            or actual_cost < 0
            or actual_cost > reserved_maximum
        ):
            raise IdempotencyUnavailable("actual cost is invalid")
        token_key = {"pk": f"formal_budget_reservation#{reservation.reservation_id}", "sk": "token"}
        try:
            response = self._client.get_item(
                TableName=self._table, Key=_marshal(token_key), ConsistentRead=True
            )
            raw = response.get("Item")
            if not isinstance(raw, dict):
                raise IdempotencyUnavailable("budget reservation is missing")
            item = _unmarshal(raw)
            if item.get("state") == "settled":
                return item.get("actual_cost") == actual_cost
            amount, day = item.get("amount"), item.get("day")
            expected = Decimal(reservation.max_reserved_cost)
            if amount != expected or not isinstance(day, str):
                raise IdempotencyUnavailable("budget reservation is inconsistent")
            self._client.transact_write_items(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": self._table,
                            "Key": _marshal({"pk": f"formal_budget#{day}", "sk": "counter"}),
                            "UpdateExpression": (
                                "ADD reserved_total :negative, spent_total :actual"
                            ),
                            "ConditionExpression": "reserved_total>=:amount",
                            "ExpressionAttributeValues": _marshal(
                                {
                                    ":negative": -expected,
                                    ":actual": actual_cost,
                                    ":amount": expected,
                                }
                            ),
                        }
                    },
                    {
                        "Update": {
                            "TableName": self._table,
                            "Key": _marshal(token_key),
                            "UpdateExpression": "SET #state=:settled, actual_cost=:actual",
                            "ConditionExpression": (
                                "#state IN (:reserved,:uncertain) AND amount=:amount"
                            ),
                            "ExpressionAttributeNames": {"#state": "state"},
                            "ExpressionAttributeValues": _marshal(
                                {
                                    ":settled": "settled",
                                    ":reserved": "reserved",
                                    ":uncertain": "reserved_uncertain",
                                    ":actual": actual_cost,
                                    ":amount": expected,
                                }
                            ),
                        }
                    },
                ]
            )
            return True
        except ClientError as exc:
            raise IdempotencyUnavailable("budget settlement unavailable") from exc
        except (BotoCoreError, TimeoutError) as exc:
            raise IdempotencyUnavailable("budget settlement outcome is unknown") from exc
