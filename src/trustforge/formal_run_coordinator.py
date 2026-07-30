"""Provider-free HTTP coordination for formal manual analysis intents."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable, Protocol, Sequence

from .formal_run_idempotency import (
    FormalRunIdempotencyStore,
    FormalRunIdentity,
    FormalRunLookup,
    FormalRunReceipt,
    IdempotencyUnavailable,
    TerminalSafeResponse,
    build_identity,
    content_fingerprint,
    normalize_locale,
    parse_idempotency_key,
    request_fingerprint,
)


class FormalProjectionFlow(Protocol):
    def plan_formal_manual(
        self,
        coin: str,
        mode: str,
        question: str,
        *,
        locale: str,
        fresh: bool,
        operation_id: str,
        identity: FormalRunIdentity,
    ) -> dict[str, str | None]: ...

    def enqueue_formal_projection(
        self,
        coin: str,
        mode: str,
        question: str,
        *,
        locale: str,
        job_id: str,
        operation_id: str,
        identity: FormalRunIdentity,
        fencing_token: int,
        pending_authority: bool = False,
    ) -> tuple[str, str]: ...

    def cancel_staged_formal_projection(
        self, *, identity: FormalRunIdentity, operation_id: str
    ) -> None: ...


@dataclass(frozen=True)
class FormalRunSecrets:
    caller_secret: bytes
    caller_key_id: str
    idempotency_secret: bytes
    idempotency_key_id: str
    retention_locator_secret: bytes
    fingerprint_secret: bytes
    fingerprint_key_id: str
    content_secret: bytes
    content_key_id: str


@dataclass(frozen=True)
class FormalRunOutcome:
    status: int
    body: dict[str, object]
    replayed: bool = False
    replay_headers: dict[str, str] | None = None


@dataclass(frozen=True)
class FormalBudgetReservation:
    """Opaque reservation identity issued by the budget authority."""

    reservation_id: str
    max_reserved_cost: str

    def __post_init__(self) -> None:
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9._:-]{0,127}", self.reservation_id) is None:
            raise ValueError("invalid budget reservation id")
        try:
            cost = Decimal(self.max_reserved_cost)
        except InvalidOperation as exc:
            raise ValueError("invalid maximum reserved cost") from exc
        if not cost.is_finite() or cost <= 0:
            raise ValueError("invalid maximum reserved cost")


class FormalRunCoordinator:
    """Own the exact-once authority boundary; never invokes a provider."""

    def __init__(
        self,
        *,
        store: FormalRunIdempotencyStore,
        flow: FormalProjectionFlow,
        secrets: FormalRunSecrets,
        reserve_budget: Callable[[str], FormalBudgetReservation | None],
        release_budget: Callable[[FormalBudgetReservation], None],
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        namespace: str = "formal-analysis",
        lease_seconds: int = 30,
    ) -> None:
        self._store = store
        self._flow = flow
        self._secrets = secrets
        self._reserve = reserve_budget
        self._release = release_budget
        self._now = now
        self._namespace = namespace
        self._lease_seconds = lease_seconds

    def submit(
        self,
        *,
        idempotency_keys: str | Sequence[str],
        caller_scope: str,
        coin: str,
        mode: str,
        question: str,
        locale: str | None,
        fresh: bool,
        admit_owner: Callable[[], None] | None = None,
    ) -> FormalRunOutcome:
        if not isinstance(fresh, bool):
            raise ValueError("fresh must be boolean")
        from .analysis_flow import COIN_POOL, QUESTION_TYPES

        if not isinstance(coin, str) or coin.strip().upper() not in COIN_POOL:
            raise ValueError("unsupported coin")
        if not isinstance(mode, str) or mode.strip() not in QUESTION_TYPES:
            raise ValueError("unsupported mode")
        if (
            not isinstance(question, str)
            or not question.strip()
            or len(question.strip()) > 1000
        ):
            raise ValueError("question must contain 1..1000 characters")
        parsed = parse_idempotency_key(idempotency_keys)
        canonical_locale = normalize_locale(locale)
        identity = build_identity(
            namespace=self._namespace,
            caller_scope=caller_scope,
            parsed_key=parsed,
            caller_secret=self._secrets.caller_secret,
            caller_key_id=self._secrets.caller_key_id,
            idempotency_secret=self._secrets.idempotency_secret,
            idempotency_key_id=self._secrets.idempotency_key_id,
            retention_locator_secret=self._secrets.retention_locator_secret,
        )
        lookup = FormalRunLookup(
            parsed_key=parsed,
            primary_identity=identity,
            primary_fingerprint=request_fingerprint(
                self._secrets.fingerprint_secret,
                self._secrets.fingerprint_key_id,
                coin=coin,
                mode=mode,
                question=question,
                locale=canonical_locale,
                fresh=fresh,
            ),
        )
        now = self._now()
        acquired = self._store.acquire(
            lookup=lookup, now=now, lease_seconds=self._lease_seconds
        )
        def error_body(code: str) -> dict[str, object]:
            return {"ok": False, "data": None, "error": {"code": code}}

        if acquired.kind == "conflict":
            return FormalRunOutcome(
                409, error_body("idempotency_conflict")
            )
        if acquired.kind == "in_progress":
            return FormalRunOutcome(
                409,
                error_body("idempotency_request_in_progress"),
                replay_headers={"Retry-After": "2"},
            )
        if acquired.kind == "key_unavailable":
            return FormalRunOutcome(
                409, error_body("idempotency_key_unavailable")
            )
        if acquired.kind == "terminal_replay":
            terminal = acquired.terminal_response
            if terminal is None:
                raise IdempotencyUnavailable("terminal replay is incomplete")
            return FormalRunOutcome(
                terminal.status,
                dict(terminal.body),
                replayed=True,
                replay_headers=dict(terminal.replay_headers),
            )
        if acquired.kind == "replay":
            if acquired.receipt is None or acquired.authority_identity is None:
                raise IdempotencyUnavailable("receipt replay is incomplete")
            token = self._store.pending_projection_token(
                identity=acquired.authority_identity
            )
            if token is not None:
                self._enqueue(
                    acquired.receipt,
                    acquired.authority_identity,
                    token,
                    coin,
                    mode,
                    question,
                    canonical_locale,
                )
            return FormalRunOutcome(
                202, acquired.receipt.public_body(), replayed=True
            )
        if (
            acquired.kind != "owner"
            or acquired.fencing_token is None
            or acquired.authority_identity is None
        ):
            raise IdempotencyUnavailable("owner acquisition is incomplete")
        if admit_owner is not None:
            try:
                admit_owner()
            except Exception:
                response = TerminalSafeResponse(
                    status=429,
                    code="analysis_rate_limited",
                    schema_version="error/v1",
                    body=error_body("analysis_rate_limited"),
                    replay_headers={"Retry-After": "60"},
                )
                self._store.fail_terminal(
                    identity=acquired.authority_identity,
                    fencing_token=acquired.fencing_token,
                    response=response,
                    now=now,
                    expires_at=now + timedelta(hours=24),
                )
                return FormalRunOutcome(
                    response.status,
                    dict(response.body),
                    replay_headers=dict(response.replay_headers),
                )

        authority = acquired.authority_identity
        operation_id = "op_" + hashlib.sha256(
            (
                f"{authority.namespace}\0{authority.scope_locator}\0"
                f"{authority.key_hmac.key_id}\0{authority.key_hmac.digest}"
            ).encode()
        ).hexdigest()[:32]
        planned = self._flow.plan_formal_manual(
            coin,
            mode,
            question,
            locale=canonical_locale,
            fresh=fresh,
            operation_id=operation_id,
            identity=authority,
        )
        ids = (planned.get("question_id"), planned.get("job_id"), planned.get("result_id"))
        if any(not isinstance(value, str) for value in ids):
            raise IdempotencyUnavailable("formal projection plan is incomplete")
        question_id, job_id, result_id = ids
        receipt = FormalRunReceipt(
            receipt_id="frc_" + hashlib.sha256(operation_id.encode()).hexdigest()[:32],
            question_id=question_id,
            job_id=job_id,
            result_id=result_id,
            state="accepted",
            origin="manual",
            disposition="fresh-created" if fresh else "created",
            locale=canonical_locale,
            created_at=now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        )
        # Persist the local reservation intent before crossing the budget
        # authority boundary.  The reservation id is deterministic from this
        # operation, so a timeout-after-commit can always be strong-read and
        # reconciled after restart.
        self._enqueue(
            receipt,
            authority,
            acquired.fencing_token,
            coin,
            mode,
            question,
            canonical_locale,
            pending_authority=True,
        )
        reserved = self._reserve(operation_id)
        if reserved is None:
            response = TerminalSafeResponse(
                status=429,
                code="analysis_budget_unavailable",
                schema_version="error/v1",
                body=error_body("analysis_budget_unavailable"),
                replay_headers={"Retry-After": "60"},
            )
            self._store.fail_terminal(
                identity=authority,
                fencing_token=acquired.fencing_token,
                response=response,
                now=now,
                expires_at=now + timedelta(hours=24),
            )
            self._flow.cancel_staged_formal_projection(
                identity=authority, operation_id=operation_id
            )
            return FormalRunOutcome(
                response.status,
                dict(response.body),
                replay_headers=dict(response.replay_headers),
            )
        try:
            decided = self._store.bind_with_content_decision(
                identity=authority,
                fencing_token=acquired.fencing_token,
                receipt=receipt,
                operation_id=operation_id,
                content=content_fingerprint(
                    self._secrets.content_secret,
                    self._secrets.content_key_id,
                    coin=coin,
                    mode=mode,
                    question=question,
                ),
                fresh=fresh,
                now=now,
                reservation_id=reserved.reservation_id,
                max_reserved_cost=reserved.max_reserved_cost,
                provider_operation_id="provider_" + operation_id[3:],
                cost_policy_version="cost-v1",
                cost_policy_digest=hashlib.sha256(b"cost-v1").hexdigest(),
            )
        except Exception:
            # The bind transaction may have committed before a timeout.  The
            # reservation therefore remains held for reconciliation; releasing
            # it here would permit budget overspend under unknown outcome.
            raise
        if decided is None:
            self._release(reserved)
            self._flow.cancel_staged_formal_projection(
                identity=authority, operation_id=operation_id
            )
            terminal = TerminalSafeResponse(
                status=409,
                code="relocalization_unavailable",
                schema_version="error/v1",
                body=error_body("relocalization_unavailable"),
                replay_headers={},
            )
            self._store.fail_terminal(
                identity=authority,
                fencing_token=acquired.fencing_token,
                response=terminal,
                now=now,
                expires_at=now + timedelta(hours=24),
            )
            return FormalRunOutcome(409, dict(terminal.body))
        if decided.disposition == "reused":
            self._release(reserved)
            self._flow.cancel_staged_formal_projection(
                identity=authority, operation_id=operation_id
            )
        else:
            self._enqueue(
                decided,
                authority,
                acquired.fencing_token,
                coin,
                mode,
                question,
                canonical_locale,
                pending_authority=False,
            )
        return FormalRunOutcome(202, decided.public_body())

    def _enqueue(
        self,
        receipt: FormalRunReceipt,
        identity: FormalRunIdentity,
        fencing_token: int,
        coin: str,
        mode: str,
        question: str,
        locale: str,
        pending_authority: bool = False,
    ) -> None:
        operation_id = "op_" + hashlib.sha256(
            (
                f"{identity.namespace}\0{identity.scope_locator}\0"
                f"{identity.key_hmac.key_id}\0{identity.key_hmac.digest}"
            ).encode()
        ).hexdigest()[:32]
        self._flow.enqueue_formal_projection(
            coin,
            mode,
            question,
            locale=locale,
            job_id=receipt.job_id,
            operation_id=operation_id,
            identity=identity,
            fencing_token=fencing_token,
            pending_authority=pending_authority,
        )
