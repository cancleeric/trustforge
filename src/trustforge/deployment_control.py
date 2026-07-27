"""Authenticated release activation control and routing-ledger projection."""
from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Mapping

from trustforge.activation_lock import (
    acquire_activation_lock,
    get_activation_lock,
    release_activation_lock,
)
from trustforge.agent.shadow_contracts import canonical_json
from trustforge.authenticated_ledger import (
    AuthenticatedLedger,
    LedgerError,
)
from trustforge.release_router import (
    ReleaseEndpoint,
    ReleaseRoutingLedger,
    RoutingPolicy,
    RoutingSnapshot,
)

Action = Literal["start", "stop", "promote", "rollback-a"]


class DeploymentControlError(RuntimeError):
    """A deployment transition or receipt is invalid."""


def _utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise DeploymentControlError("invalid timestamp") from exc
    if parsed.tzinfo is None:
        raise DeploymentControlError("timestamp must be timezone aware")
    return parsed.astimezone(timezone.utc)


def _sign(secret: bytes, domain: bytes, payload: Mapping[str, Any]) -> str:
    if len(secret) < 32:
        raise DeploymentControlError("signing key must be at least 32 bytes")
    return hmac.new(secret, domain + canonical_json(payload), hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class DeploymentAuthorization:
    action: Action
    target: str
    target_confirmation: str
    ledger_id: str
    active_artifact_digest: str
    candidate_artifact_digest: str
    evidence_bundle_digest: str
    routing_policy_digest: str
    routing_key_id: str
    actor: str
    issued_at: str
    expires_at: str
    nonce: str
    key_id: str
    signature: str
    receipt_version: str = "trustforge.deployment-authorization/v2"

    def unsigned(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("signature")
        return value


@dataclass(frozen=True, slots=True)
class ActivationCompletionReceipt:
    transaction_id: str
    action: Action
    target: str
    prepared_event_hash: str
    active_artifact_digest: str
    candidate_artifact_digest: str
    pointer_active_digest: str
    observed_manifest_digest: str
    status: str
    verified_at: str
    actor: str
    nonce: str
    key_id: str
    signature: str
    receipt_version: str = "trustforge.activation-completion/v1"

    def unsigned(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("signature")
        return value


class DeploymentControlLedger(ReleaseRoutingLedger):
    """Project authenticated control state from one append-only SSOT ledger."""

    def __init__(
        self,
        ledger: AuthenticatedLedger,
        *,
        authorization_keys: Mapping[str, bytes],
        completion_keys: Mapping[str, bytes],
        target: str,
        target_confirmation: str,
        active: ReleaseEndpoint,
        candidate: ReleaseEndpoint,
        policy: RoutingPolicy,
        evidence_bundle_digest: str,
        stop_after_errors: int = 3,
        require_distributed_lock: bool = True,
    ):
        expected_confirmation = (
            f"PRODUCTION:{target}:{active.release_digest}:{candidate.release_digest}"
        )
        if not target or target_confirmation != expected_confirmation:
            raise DeploymentControlError("explicit production target confirmation is required")
        if not 1 <= stop_after_errors <= 100:
            raise DeploymentControlError("automatic stop threshold is invalid")
        self.ledger = ledger
        self.authorization_keys = dict(authorization_keys)
        self.completion_keys = dict(completion_keys)
        self.target = target
        self.target_confirmation = target_confirmation
        self.active = active
        self.candidate = candidate
        self.policy = policy
        self.evidence_bundle_digest = evidence_bundle_digest
        self.stop_after_errors = stop_after_errors
        self.require_distributed_lock = require_distributed_lock

    def initialize(self) -> RoutingSnapshot:
        records = self.ledger.read()
        if records:
            return self.routing_snapshot()
        self.ledger.append(
            {
                "kind": "deployment_initialized",
                "target": self.target,
                "target_confirmation": self.target_confirmation,
                "active": asdict(self.active),
                "candidate": asdict(self.candidate),
                "policy": asdict(self.policy),
                "evidence_bundle_digest": self.evidence_bundle_digest,
                "stop_after_errors": self.stop_after_errors,
            }
        )
        return self.routing_snapshot()

    def _records(self) -> list[dict[str, Any]]:
        records = self.ledger.read()
        if not records or records[0]["event"].get("kind") != "deployment_initialized":
            raise DeploymentControlError("deployment ledger is not initialized")
        initial = records[0]["event"]
        expected = {
            "kind": "deployment_initialized",
            "target": self.target,
            "target_confirmation": self.target_confirmation,
            "active": asdict(self.active),
            "candidate": asdict(self.candidate),
            "policy": asdict(self.policy),
            "evidence_bundle_digest": self.evidence_bundle_digest,
            "stop_after_errors": self.stop_after_errors,
        }
        if initial != expected:
            raise DeploymentControlError("deployment initialization identity mismatch")
        return records

    def routing_snapshot(self) -> RoutingSnapshot:
        records = self._records()
        phase = desired = "disabled"
        activation = "completed"
        requests = errors = 0
        prepared: dict[str, dict[str, Any]] = {}
        unresolved_transaction: str | None = None
        terminal_transactions: set[str] = set()
        reservations: dict[str, bool] = {}
        for record in records[1:]:
            event = record["event"]
            kind = event.get("kind")
            if kind == "activation_prepared":
                transaction = str(event["transaction_id"])
                if transaction in prepared or unresolved_transaction is not None:
                    raise DeploymentControlError("duplicate activation transaction")
                if (
                    event.get("evidence_bundle_digest")
                    != self.evidence_bundle_digest
                    or event.get("active_artifact_digest")
                    != self.active.release_digest
                    or event.get("candidate_artifact_digest")
                    != self.candidate.release_digest
                    or event.get("routing_policy_digest")
                    != self.policy.policy_digest
                ):
                    raise DeploymentControlError(
                        "prepared activation evidence binding mismatch"
                    )
                prepared[transaction] = event | {"event_hash": record["event_hash"]}
                unresolved_transaction = transaction
                desired = str(event["desired_phase"])
                activation = "prepared"
            elif kind in {"activation_completed", "activation_failed"}:
                transaction = str(event["transaction_id"])
                prior = prepared.get(transaction)
                if (
                    prior is None
                    or unresolved_transaction != transaction
                    or transaction in terminal_transactions
                    or prior["event_hash"] != event.get("prepared_event_hash")
                    or event.get("observed_manifest_digest")
                    != event.get("pointer_active_digest")
                ):
                    raise DeploymentControlError("activation completion is orphaned")
                terminal_transactions.add(transaction)
                unresolved_transaction = None
                if kind == "activation_completed":
                    phase = desired = str(prior["desired_phase"])
                    activation = "completed"
                else:
                    observed = event.get("pointer_active_digest")
                    if observed == self.candidate.release_digest:
                        phase = desired = "recovery_required"
                    else:
                        phase = desired = "stopped" if phase == "canary" else "disabled"
                    activation = "failed"
            elif kind == "operator_stop":
                phase = desired = "stopped"
                activation = "completed"
            elif kind == "candidate_reservation":
                reservation_id = str(event.get("reservation_id", ""))
                if reservation_id in reservations:
                    raise DeploymentControlError("duplicate candidate reservation")
                reservations[reservation_id] = False
                requests += 1
            elif kind == "candidate_result":
                reservation_id = str(event.get("reservation_id", ""))
                if reservation_id not in reservations or reservations[reservation_id]:
                    raise DeploymentControlError("candidate outcome is orphaned or repeated")
                reservations[reservation_id] = True
                errors = 0 if event.get("ok") is True else errors + 1
                if event.get("automatic_stop") is True:
                    if errors < self.stop_after_errors:
                        raise DeploymentControlError("premature automatic stop event")
                    phase = desired = "stopped"
                    activation = "completed"
            else:
                raise DeploymentControlError("unknown deployment ledger event")
        if self.ledger.emergency_stopped(ledger_id=records[0]["ledger_id"]):
            phase = desired = "stopped"
            activation = "completed"
        return RoutingSnapshot(
            ledger_id=records[0]["ledger_id"],
            phase=phase,
            desired_phase=desired,
            activation_status=activation,
            active=self.active,
            candidate=self.candidate,
            policy=self.policy,
            candidate_requests=requests,
            consecutive_errors=errors,
            stop_after_errors=self.stop_after_errors,
            ledger_head=records[-1]["event_hash"],
        )

    def _verify_authorization(
        self, receipt: DeploymentAuthorization, *, action: Action, now: datetime
    ) -> None:
        secret = self.authorization_keys.get(receipt.key_id)
        expected = (
            _sign(secret, b"trustforge.deployment-authorization.v2\x00", receipt.unsigned())
            if secret is not None else ""
        )
        snapshot = self.routing_snapshot()
        if not receipt.signature or not hmac.compare_digest(receipt.signature, expected):
            raise DeploymentControlError("authorization signature is invalid")
        if (
            receipt.receipt_version != "trustforge.deployment-authorization/v2"
            or receipt.action != action
            or receipt.target != self.target
            or receipt.target_confirmation != self.target_confirmation
            or receipt.ledger_id != snapshot.ledger_id
            or receipt.active_artifact_digest != self.active.release_digest
            or receipt.candidate_artifact_digest != self.candidate.release_digest
            or receipt.evidence_bundle_digest != self.evidence_bundle_digest
            or receipt.routing_policy_digest != self.policy.policy_digest
            or receipt.routing_key_id != self.policy.routing_key_id
            or not receipt.actor
            or not receipt.nonce
        ):
            raise DeploymentControlError("authorization binding is invalid")
        issued, expires = _utc(receipt.issued_at), _utc(receipt.expires_at)
        if issued > now or expires <= now or expires <= issued or expires - issued > timedelta(minutes=15):
            raise DeploymentControlError("authorization is stale or future-dated")

    def prepare(
        self, action: Action, receipt: DeploymentAuthorization, *, now: datetime
    ) -> dict[str, Any]:
        self._verify_authorization(receipt, action=action, now=now)
        state = self.routing_snapshot()
        allowed = {
            "start": {"disabled", "stopped"},
            "promote": {"canary"},
            "rollback-a": {"canary", "stopped", "promoted", "recovery_required"},
            "stop": {"canary"},
        }
        if state.phase not in allowed[action] or state.activation_status == "prepared":
            raise DeploymentControlError("action is not allowed from current state")
        if action == "stop":
            return self.ledger.append(
                {
                    "kind": "operator_stop",
                    "nonce": receipt.nonce,
                    "actor": receipt.actor,
                    "at": now.isoformat(),
                },
                expected_head=state.ledger_head,
            )
        desired = {
            "start": "canary",
            "promote": "promoted",
            "rollback-a": "disabled",
        }[action]
        transaction_id = hashlib.sha256(
            b"trustforge.activation-transaction.v1\x00"
            + canonical_json(
                {
                    "ledger_id": state.ledger_id,
                    "action": action,
                    "nonce": receipt.nonce,
                }
            )
        ).hexdigest()
        owner_id = f"deployment-control:{transaction_id}"
        if (
            self.require_distributed_lock
            and os.environ.get("TRUSTFORGE_ACTIVATION_LOCK_BACKEND", "").lower()
            != "dynamodb"
        ):
            raise DeploymentControlError(
                "production activation requires distributed lock backend"
            )
        if not acquire_activation_lock(self.target, owner_id=owner_id, ttl=900):
            raise DeploymentControlError("activation lock is unavailable")
        try:
            return self.ledger.append(
                {
                    "kind": "activation_prepared",
                    "transaction_id": transaction_id,
                    "action": action,
                    "desired_phase": desired,
                    "nonce": receipt.nonce,
                    "actor": receipt.actor,
                    "owner_id": owner_id,
                    "evidence_bundle_digest": self.evidence_bundle_digest,
                    "active_artifact_digest": self.active.release_digest,
                    "candidate_artifact_digest": self.candidate.release_digest,
                    "routing_policy_digest": self.policy.policy_digest,
                    "at": now.isoformat(),
                },
                expected_head=state.ledger_head,
            )
        except BaseException:
            release_activation_lock(self.target, owner_id)
            raise

    def complete(
        self, receipt: ActivationCompletionReceipt, *, now: datetime
    ) -> dict[str, Any]:
        records = self._records()
        terminal_ids = {
            str(record["event"].get("transaction_id"))
            for record in records
            if record["event"].get("kind")
            in {"activation_completed", "activation_failed"}
        }
        unresolved = [
            record for record in records
            if record["event"].get("kind") == "activation_prepared"
            and str(record["event"].get("transaction_id")) not in terminal_ids
        ]
        prepared_record = unresolved[-1] if unresolved else None
        if prepared_record is None:
            raise DeploymentControlError("activation transaction is unknown")
        if prepared_record["event"].get("transaction_id") != receipt.transaction_id:
            raise DeploymentControlError("completion is not for latest unresolved transaction")
        event = prepared_record["event"]
        secret = self.completion_keys.get(receipt.key_id)
        expected = (
            _sign(secret, b"trustforge.activation-completion.v1\x00", receipt.unsigned())
            if secret is not None else ""
        )
        expected_pointer = (
            self.candidate.release_digest if receipt.action == "promote"
            else self.active.release_digest
        )
        pointer_valid = (
            receipt.pointer_active_digest == expected_pointer
            if receipt.status == "completed"
            else receipt.pointer_active_digest
            in {self.active.release_digest, self.candidate.release_digest}
        )
        if (
            not receipt.signature
            or not hmac.compare_digest(receipt.signature, expected)
            or receipt.receipt_version != "trustforge.activation-completion/v1"
            or receipt.action != event["action"]
            or receipt.target != self.target
            or receipt.prepared_event_hash != prepared_record["event_hash"]
            or receipt.active_artifact_digest != self.active.release_digest
            or receipt.candidate_artifact_digest != self.candidate.release_digest
            or not pointer_valid
            or receipt.observed_manifest_digest != receipt.pointer_active_digest
            or receipt.status not in {"completed", "failed"}
            or not receipt.actor
            or not receipt.nonce
        ):
            raise DeploymentControlError("activation completion binding is invalid")
        verified = _utc(receipt.verified_at)
        if verified > now or now - verified > timedelta(minutes=10):
            raise DeploymentControlError("activation completion is stale")
        lock = get_activation_lock(self.target)
        if (
            lock is None
            or lock.owner_id != event["owner_id"]
            or lock.expires_at <= now.timestamp()
        ):
            raise DeploymentControlError("activation lock ownership was lost")
        result = self.ledger.append(
                {
                    "kind": (
                        "activation_completed"
                        if receipt.status == "completed"
                        else "activation_failed"
                    ),
                    "transaction_id": receipt.transaction_id,
                    "prepared_event_hash": receipt.prepared_event_hash,
                    "pointer_active_digest": receipt.pointer_active_digest,
                    "observed_manifest_digest": receipt.observed_manifest_digest,
                    "activation_receipt_digest": "sha256:" + hashlib.sha256(
                        canonical_json(receipt.unsigned() | {"signature": receipt.signature})
                    ).hexdigest(),
                    "nonce": receipt.nonce,
                    "actor": receipt.actor,
                    "at": now.isoformat(),
                },
                expected_head=records[-1]["event_hash"],
            )
        release_activation_lock(self.target, event["owner_id"])
        return result

    def reserve_candidate(
        self,
        *,
        expected_head: str,
        reservation_id: str,
    ) -> RoutingSnapshot:
        if (
            len(reservation_id) != 32
            or any(character not in "0123456789abcdef" for character in reservation_id)
        ):
            raise DeploymentControlError("candidate reservation id is invalid")
        state = self.routing_snapshot()
        if (
            state.ledger_head != expected_head
            or state.phase != "canary"
            or state.desired_phase != "canary"
            or state.activation_status != "completed"
            or state.candidate_requests >= state.policy.request_cap
        ):
            raise LedgerError("candidate reservation state changed")
        self.ledger.append(
            {
                "kind": "candidate_reservation",
                "reservation_id": reservation_id,
                "nonce": f"reservation:{reservation_id}",
            },
            expected_head=expected_head,
        )
        return self.routing_snapshot()

    def record_candidate_result(
        self,
        *,
        expected_head: str,
        reservation_id: str,
        ok: bool,
        status_code: int,
        latency_ms: float,
        error_kind: str,
    ) -> RoutingSnapshot:
        for _attempt in range(4):
            records = self._records()
            existing = next(
                (
                    record for record in records
                    if record["event"].get("kind") == "candidate_result"
                    and record["event"].get("reservation_id") == reservation_id
                ),
                None,
            )
            if existing is not None:
                event = existing["event"]
                if (
                    event.get("ok") is bool(ok)
                    and event.get("status_code") == int(status_code)
                    and event.get("error_kind") == error_kind
                ):
                    return self.routing_snapshot()
                raise LedgerError("candidate reservation has a different terminal outcome")
            reserved = any(
                record["event"].get("kind") == "candidate_reservation"
                and record["event"].get("reservation_id") == reservation_id
                for record in records
            )
            if not reserved:
                raise LedgerError("candidate outcome has no reservation")
            state = self.routing_snapshot()
            next_errors = 0 if ok else state.consecutive_errors + 1
            try:
                self.ledger.append(
                    {
                        "kind": "candidate_result",
                        "reservation_id": reservation_id,
                        "ok": bool(ok),
                        "status_code": int(status_code),
                        "latency_ms": round(float(latency_ms), 3),
                        "error_kind": error_kind,
                        "automatic_stop": next_errors >= self.stop_after_errors,
                        "nonce": f"outcome:{reservation_id}",
                    },
                    expected_head=records[-1]["event_hash"],
                )
                return self.routing_snapshot()
            except LedgerError:
                continue
        raise LedgerError("candidate outcome could not be durably recorded")

    def emergency_stop(self, *, ledger_id: str, reason: str) -> None:
        self.ledger.trip_emergency_stop(ledger_id=ledger_id, reason=reason)
