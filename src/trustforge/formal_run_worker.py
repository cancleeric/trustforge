"""Crash-conservative worker for pending formal analysis projections."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Protocol

from .formal_run_idempotency import (
    FormalRunIdempotencyStore,
    FormalRunIdentity,
    HmacValue,
)
from .formal_run_coordinator import FormalBudgetReservation


class FormalProjectionQueue(Protocol):
    def claim_formal_projection(self, *, lease_seconds: int = 30) -> dict[str, object] | None: ...

    def set_formal_projection_state(
        self,
        *,
        namespace: str,
        scope_locator: str,
        operation_id: str,
        expected: str,
        state: str,
        claim_token: str,
        alert_reason: str | None = None,
    ) -> None: ...

    def reconcile_staged_formal_projections(
        self, store: FormalRunIdempotencyStore, budget=None
    ) -> int: ...


class FormalRunWorker:
    """Claims shared authority before invoking any unknown-result operation."""

    def __init__(
        self,
        *,
        store: FormalRunIdempotencyStore,
        queue: FormalProjectionQueue,
        execute: Callable[[dict[str, object], str], Decimal | None],
        reconcile: Callable[[str], tuple[str, Decimal | None]],
        settle_budget: Callable[[FormalBudgetReservation, Decimal], bool],
        budget_authority=None,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._store = store
        self._queue = queue
        self._execute = execute
        self._reconcile = reconcile
        self._settle = settle_budget
        self._budget = budget_authority
        self._now = now

    @staticmethod
    def _identity(row: dict[str, object]) -> FormalRunIdentity:
        return FormalRunIdentity(
            namespace=str(row["namespace"]),
            scope_locator=str(row["scope_locator"]),
            caller_scope_hmac=HmacValue(
                str(row["caller_key_id"]), str(row["caller_scope_hmac"])
            ),
            key_hmac=HmacValue(str(row["key_key_id"]), str(row["key_hmac"])),
        )

    def run_once(self) -> str:
        row = self._queue.claim_formal_projection(lease_seconds=30)
        if row is None:
            return "idle"
        identity = self._identity(row)
        token = int(row["fencing_token"])
        transition = {
            "namespace": identity.namespace,
            "scope_locator": identity.scope_locator,
            "operation_id": str(row["operation_id"]),
            "expected": str(row.get("state", "claiming")),
            "claim_token": str(row["claim_token"]),
        }
        # A claiming row can be retried only while shared authority still says
        # not_dispatched.  If that predicate is gone, a previous process may
        # have crossed the provider boundary: never blind-resend.
        resolution = self._store.dispatch_resolution(
            identity=identity, fencing_token=token
        )
        if resolution == "completed":
            self._queue.set_formal_projection_state(
                **transition, state="completed"
            )
            return "completed"
        if resolution == "uncertain" or resolution == "none":
            self._queue.set_formal_projection_state(
                **transition,
                state="execution_uncertain",
                alert_reason=f"authority_{resolution}",
            )
            return "execution_uncertain"
        if resolution == "claimed":
            provider = self._store.provider_operation(
                identity=identity, fencing_token=token
            )
            status, actual = (
                self._reconcile(provider) if provider is not None else ("unknown", None)
            )
            if status == "completed" and actual is not None:
                try:
                    self._settle_reservation(identity, token, actual)
                    self._store.complete_dispatch(
                        identity=identity, fencing_token=token, now=self._now()
                    )
                    self._queue.set_formal_projection_state(
                        **transition, state="completed"
                    )
                    return "completed"
                except Exception:
                    pass
            elif status == "pending":
                return "collecting"
            self._store.mark_execution_uncertain(
                identity=identity, fencing_token=token, now=self._now()
            )
            self._queue.set_formal_projection_state(
                **transition,
                state="execution_uncertain",
                alert_reason=f"provider_reconciliation_{status}",
            )
            return "execution_uncertain"
        if resolution != "pending":
            raise RuntimeError("unknown formal dispatch resolution")
        provider_operation_id = self._store.claim_dispatch(
            identity=identity, fencing_token=token, now=self._now()
        )
        self._queue.set_formal_projection_state(**transition, state="collecting")
        try:
            details = self._store.reservation_details(
                identity=identity, fencing_token=token
            )
            if details is None:
                raise RuntimeError("formal budget reservation is missing")
            execution_row = {
                **row,
                "reservation_id": details[0],
                "max_reserved_cost": details[1],
            }
            actual_cost = self._execute(execution_row, provider_operation_id)
            if actual_cost is None:
                return "collecting"
            self._settle_reservation(identity, token, actual_cost)
        except Exception:
            self._store.mark_execution_uncertain(
                identity=identity, fencing_token=token, now=self._now()
            )
            self._queue.set_formal_projection_state(
                **{**transition, "expected": "collecting"},
                state="execution_uncertain",
                alert_reason="provider_execution_outcome_unknown",
            )
            return "execution_uncertain"
        self._store.complete_dispatch(
            identity=identity, fencing_token=token, now=self._now()
        )
        self._queue.set_formal_projection_state(
            **{**transition, "expected": "collecting"}, state="completed"
        )
        return "completed"

    def reconcile_staged(self) -> int:
        return self._queue.reconcile_staged_formal_projections(
            self._store, self._budget
        )

    def _settle_reservation(
        self, identity: FormalRunIdentity, token: int, actual_cost: Decimal
    ) -> None:
        details = self._store.reservation_details(
            identity=identity, fencing_token=token
        )
        if details is None:
            raise RuntimeError("formal budget reservation is missing")
        if not self._settle(FormalBudgetReservation(*details), actual_cost):
            raise RuntimeError("formal budget settlement was not authoritative")
