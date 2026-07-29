"""Fail-closed bridge from signed promotion evidence to canary start only."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from trustforge.agent.shadow_contracts import canonical_json
from trustforge.asset_intrinsic_promotion_receipt import (
    EVENT_KIND,
    FAILURE_EVENT_KIND,
    validate_receipt_event,
)
from trustforge.deployment_control import (
    CanaryStartContext,
    DeploymentAuthorization,
    DeploymentControlError,
    DeploymentControlLedger,
)
from trustforge.signed_event_ledger import SignedEventLedger

CEO_AUTHORIZATION_VERSION = "trustforge.verified-receipt-ceo-authorization/v2"
CEO_AUTHORIZATION_DOMAIN = b"trustforge.verified-receipt-ceo-authorization.v2\x00"
AUDIT_INTENT_KIND = "verified_receipt_canary_intent"
AUDIT_OUTCOME_KIND = "verified_receipt_canary_outcome"
MAX_RECEIPT_AGE = timedelta(minutes=90)
MAX_FUTURE_SKEW = timedelta(seconds=30)
MAX_PIT_LAG = timedelta(minutes=90)
MAX_AUTHORIZATION_LIFETIME = timedelta(minutes=15)


def _utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise DeploymentControlError("verified gate timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DeploymentControlError("verified gate timestamp lacks timezone")
    return parsed.astimezone(timezone.utc)


def validate_gate_audit_event(event: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(event)
    common = {
        "transaction_id",
        "receipt_event_hash",
        "control_head",
        "active_artifact_digest",
        "candidate_artifact_digest",
        "rollback_artifact_digest",
        "ceo_actor",
        "operator_actor",
        "ceo_nonce",
        "operator_nonce",
    }
    kind = value.get("kind")
    expected = (
        common
        | {
            "kind",
            "reason",
            "git_sha",
            "policy_digest",
            "dataset_digest",
            "release_manifest_digest",
            "pit_cutoff",
            "expected_sequence",
            "from_phase",
            "to_phase",
        }
        if kind == AUDIT_INTENT_KIND
        else common
        | {
            "kind",
            "status",
            "control_event_hash",
            "control_event_kind",
        }
    )
    if set(value) != expected or kind not in {
        AUDIT_INTENT_KIND,
        AUDIT_OUTCOME_KIND,
    }:
        raise DeploymentControlError("release gate audit schema is invalid")
    if (
        not all(
            isinstance(value[name], str) and value[name]
            for name in common - {"rollback_artifact_digest"}
        )
        or value["rollback_artifact_digest"] != value["active_artifact_digest"]
        or value["active_artifact_digest"] == value["candidate_artifact_digest"]
    ):
        raise DeploymentControlError("release gate audit binding is invalid")
    if kind == AUDIT_INTENT_KIND and (
        value["from_phase"] not in {"disabled", "stopped"}
        or value["to_phase"] != "canary"
        or not isinstance(value["release_manifest_digest"], str)
        or not value["release_manifest_digest"]
        or not isinstance(value["expected_sequence"], int)
        or value["expected_sequence"] < 1
    ):
        raise DeploymentControlError("release gate intent is invalid")
    if kind == AUDIT_OUTCOME_KIND and (
        value["status"] not in {"prepared", "completed", "failed", "not_prepared"}
        or (
            value["status"] in {"prepared", "completed", "failed"}
            and (
                not value["control_event_hash"]
                or not value["control_event_kind"]
            )
        )
        or (
            value["status"] == "not_prepared"
            and (
                value["control_event_hash"] is not None
                or value["control_event_kind"] is not None
            )
        )
    ):
        raise DeploymentControlError("release gate outcome is invalid")
    return value


@dataclass(frozen=True, slots=True)
class VerifiedReceiptCEOAuthorization:
    action: str
    receipt_event_hash: str
    git_sha: str
    artifact_digest: str
    release_manifest_digest: str
    policy_digest: str
    dataset_digest: str
    pit_cutoff: str
    expected_control_head: str
    expected_sequence: int
    active_artifact_digest: str
    candidate_artifact_digest: str
    actor: str
    issued_at: str
    expires_at: str
    nonce: str
    key_id: str
    signature: str
    authorization_version: str = CEO_AUTHORIZATION_VERSION

    def unsigned(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("signature")
        return value


class VerifiedReceiptReleaseGate:
    """Authorize only disabled/stopped -> canary preparation."""

    def __init__(
        self,
        *,
        receipt_ledger: SignedEventLedger,
        audit_ledger: SignedEventLedger,
        deployment_control: DeploymentControlLedger,
        ceo_keys: Mapping[str, bytes],
        expected_git_sha: str,
        expected_policy_digest: str,
        expected_dataset_digest: str,
        expected_release_manifest_digest: str,
    ) -> None:
        if not ceo_keys:
            raise DeploymentControlError("CEO authorization keyring is empty")
        locks = {
            receipt_ledger.coordination_lock_path.resolve(),
            audit_ledger.coordination_lock_path.resolve(),
            deployment_control.ledger.coordination_lock_path.resolve(),
        }
        if len(locks) != 1:
            raise DeploymentControlError(
                "receipt, audit, and control must share coordination lock"
            )
        roots = {
            receipt_ledger.coordination_root.resolve(),
            audit_ledger.coordination_root.resolve(),
            deployment_control.ledger.coordination_root.resolve(),
        }
        if len(roots) != 1:
            raise DeploymentControlError(
                "receipt, audit, and control must share coordination root"
            )
        self.receipt_ledger = receipt_ledger
        self.audit_ledger = audit_ledger
        self.control = deployment_control
        self.ceo_keys = dict(ceo_keys)
        self.expected_git_sha = expected_git_sha
        self.expected_policy_digest = expected_policy_digest
        self.expected_dataset_digest = expected_dataset_digest
        self.expected_release_manifest_digest = expected_release_manifest_digest

    def _latest_pass(self, now: datetime) -> tuple[dict[str, Any], str]:
        records = self.receipt_ledger.read()
        relevant = []
        for record in records:
            candidate = record["event"]
            kind = candidate.get("kind")
            if isinstance(kind, str) and kind.startswith(
                "intrinsic_promotion_"
            ) and kind not in {EVENT_KIND, FAILURE_EVENT_KIND}:
                raise DeploymentControlError(
                    "latest promotion stream contains unknown event"
                )
            if kind not in {EVENT_KIND, FAILURE_EVENT_KIND}:
                continue
            release_scoped = (
                candidate.get("active_artifact_digest")
                == self.control.active.release_digest
                and candidate.get("shadow_candidate_artifact_digest")
                == self.control.candidate.release_digest
            )
            unscoped_failure = (
                kind == FAILURE_EVENT_KIND
                and candidate.get("active_artifact_digest") is None
            )
            if release_scoped or unscoped_failure:
                relevant.append(record)
        if not relevant:
            raise DeploymentControlError("signed promotion receipt is missing")
        record = relevant[-1]
        try:
            event = validate_receipt_event(record["event"])
        except Exception as exc:
            raise DeploymentControlError("latest promotion receipt is invalid") from exc
        generated = _utc(event["generated_at"])
        pit = _utc(event["pit_cutoff"])
        if (
            event["kind"] != EVENT_KIND
            or event["decision"] != "pass"
            or generated > now + MAX_FUTURE_SKEW
            or now - generated > MAX_RECEIPT_AGE
            or generated - pit > MAX_PIT_LAG
            or event["git_sha"] != self.expected_git_sha
            or event["artifact_digest"]
            != self.control.candidate.release_digest
            or event["shadow_candidate_artifact_digest"]
            != self.control.candidate.release_digest
            or event["active_artifact_digest"] != self.control.active.release_digest
            or event["policy_digest"] != self.expected_policy_digest
            or event["dataset_digest"] != self.expected_dataset_digest
        ):
            raise DeploymentControlError("latest promotion receipt is not eligible")
        return event, str(record["event_hash"])

    def _audit_records(self) -> list[dict[str, Any]]:
        records = self.audit_ledger.read()
        intents: dict[str, dict[str, Any]] = {}
        outcomes: dict[str, str] = {}
        for record in records:
            event = validate_gate_audit_event(record["event"])
            transaction_id = event["transaction_id"]
            if event["kind"] == AUDIT_INTENT_KIND:
                if transaction_id in intents:
                    raise DeploymentControlError(
                        "duplicate release gate audit intent"
                    )
                intents[transaction_id] = event
            else:
                if transaction_id not in intents:
                    raise DeploymentControlError(
                        "orphaned release gate audit outcome"
                    )
                intent = intents[transaction_id]
                for field in (
                    "receipt_event_hash",
                    "control_head",
                    "active_artifact_digest",
                    "candidate_artifact_digest",
                    "rollback_artifact_digest",
                    "ceo_actor",
                    "operator_actor",
                    "ceo_nonce",
                    "operator_nonce",
                ):
                    if event[field] != intent[field]:
                        raise DeploymentControlError(
                            "release gate audit outcome binding mismatch"
                        )
                prior = outcomes.get(transaction_id)
                if prior in {"completed", "failed", "not_prepared"} or (
                    prior == "prepared" and event["status"] == "prepared"
                ) or (
                    prior is not None and event["status"] != prior
                    and prior != "prepared"
                ):
                    raise DeploymentControlError(
                        "release gate audit outcome transition is invalid"
                    )
                outcomes[transaction_id] = event["status"]
        return records

    def _append_outcome(
        self,
        intent: Mapping[str, Any],
        *,
        status: str,
        control_record: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        event = {
            "kind": AUDIT_OUTCOME_KIND,
            "transaction_id": intent["transaction_id"],
            "receipt_event_hash": intent["receipt_event_hash"],
            "control_head": intent["control_head"],
            "active_artifact_digest": intent["active_artifact_digest"],
            "candidate_artifact_digest": intent["candidate_artifact_digest"],
            "rollback_artifact_digest": intent["rollback_artifact_digest"],
            "ceo_actor": intent["ceo_actor"],
            "operator_actor": intent["operator_actor"],
            "ceo_nonce": intent["ceo_nonce"],
            "operator_nonce": intent["operator_nonce"],
            "status": status,
            "control_event_hash": (
                control_record["event_hash"] if control_record else None
            ),
            "control_event_kind": (
                control_record["event"]["kind"] if control_record else None
            ),
        }
        validate_gate_audit_event(event)
        return self.audit_ledger.append(event)

    def reconcile_audit(self) -> tuple[dict[str, Any], ...]:
        with self.control.ledger.coordination_lock():
            records = self._audit_records()
            outcomes = {
                record["event"]["transaction_id"]: record["event"]["status"]
                for record in records
                if record["event"]["kind"] == AUDIT_OUTCOME_KIND
            }
            appended = []
            for record in records:
                intent = record["event"]
                transaction_id = intent.get("transaction_id")
                if (
                    intent["kind"] != AUDIT_INTENT_KIND
                ):
                    continue
                control_records = self.control.activation_transaction(
                    transaction_id
                )
                control_record = control_records[-1] if control_records else None
                kind = control_record["event"]["kind"] if control_record else None
                status = {
                    "activation_prepared": "prepared",
                    "activation_completed": "completed",
                    "activation_failed": "failed",
                }.get(kind, "not_prepared")
                if outcomes.get(transaction_id) != status:
                    appended.append(
                        self._append_outcome(
                            intent, status=status, control_record=control_record
                        )
                    )
            return tuple(appended)

    def verify_audit(self) -> tuple[dict[str, Any], ...]:
        with self.control.ledger.coordination_lock():
            return tuple(self._audit_records())

    def _verify_ceo(
        self,
        authorization: VerifiedReceiptCEOAuthorization,
        *,
        event: Mapping[str, Any],
        event_hash: str,
        control_head: str,
        expected_sequence: int,
        operator: DeploymentAuthorization,
        now: datetime,
    ) -> None:
        key = self.ceo_keys.get(authorization.key_id)
        try:
            if key is None:
                raise InvalidSignature
            Ed25519PublicKey.from_public_bytes(key).verify(
                bytes.fromhex(authorization.signature),
                CEO_AUTHORIZATION_DOMAIN + canonical_json(authorization.unsigned()),
            )
        except (InvalidSignature, ValueError) as exc:
            raise DeploymentControlError("CEO authorization signature is invalid") from exc
        issued, expires = _utc(authorization.issued_at), _utc(authorization.expires_at)
        if (
            authorization.authorization_version != CEO_AUTHORIZATION_VERSION
            or authorization.action != "start-canary"
            or authorization.receipt_event_hash != event_hash
            or authorization.git_sha != event["git_sha"]
            or authorization.artifact_digest != event["artifact_digest"]
            or authorization.release_manifest_digest
            != self.expected_release_manifest_digest
            or authorization.policy_digest != event["policy_digest"]
            or authorization.dataset_digest != event["dataset_digest"]
            or authorization.pit_cutoff != event["pit_cutoff"]
            or authorization.expected_control_head != control_head
            or authorization.expected_sequence != expected_sequence
            or authorization.active_artifact_digest
            != self.control.active.release_digest
            or authorization.candidate_artifact_digest
            != self.control.candidate.release_digest
            or not authorization.actor
            or not authorization.nonce
            or authorization.actor == operator.actor
            or authorization.key_id == operator.key_id
            or issued > now + MAX_FUTURE_SKEW
            or expires <= now
            or expires <= issued
            or expires - issued > MAX_AUTHORIZATION_LIFETIME
        ):
            raise DeploymentControlError("CEO authorization binding is invalid")

    def start_canary(
        self,
        *,
        ceo_authorization: VerifiedReceiptCEOAuthorization,
        operator_authorization: DeploymentAuthorization,
        now: datetime,
    ) -> dict[str, Any]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise DeploymentControlError("verified gate clock lacks timezone")
        current = now.astimezone(timezone.utc)

        def guard(context: CanaryStartContext) -> None:
            self.reconcile_audit()
            event, event_hash = self._latest_pass(current)
            self._verify_ceo(
                ceo_authorization,
                event=event,
                event_hash=event_hash,
                control_head=context.control_head,
                expected_sequence=context.expected_sequence,
                operator=operator_authorization,
                now=current,
            )
            intent = {
                "kind": AUDIT_INTENT_KIND,
                "reason": "verified-current-pass-and-dual-authorization",
                "transaction_id": context.transaction_id,
                "receipt_event_hash": event_hash,
                "git_sha": event["git_sha"],
                "policy_digest": event["policy_digest"],
                "dataset_digest": event["dataset_digest"],
                "release_manifest_digest": (
                    ceo_authorization.release_manifest_digest
                ),
                "pit_cutoff": event["pit_cutoff"],
                "control_head": context.control_head,
                "expected_sequence": context.expected_sequence,
                "from_phase": context.phase,
                "to_phase": "canary",
                "active_artifact_digest": context.active_artifact_digest,
                "candidate_artifact_digest": context.candidate_artifact_digest,
                "rollback_artifact_digest": context.active_artifact_digest,
                "ceo_actor": ceo_authorization.actor,
                "operator_actor": operator_authorization.actor,
                "ceo_nonce": ceo_authorization.nonce,
                "operator_nonce": operator_authorization.nonce,
            }
            validate_gate_audit_event(intent)
            prior = [
                record
                for record in self._audit_records()
                if record["event"].get("kind") == AUDIT_INTENT_KIND
                and record["event"].get("transaction_id")
                == context.transaction_id
            ]
            consumed = [
                record["event"]
                for record in self._audit_records()
                if record["event"].get("kind") == AUDIT_INTENT_KIND
            ]
            if any(
                item["ceo_nonce"] == ceo_authorization.nonce
                for item in consumed
            ):
                raise DeploymentControlError(
                    "CEO authorization identity was already consumed"
                )
            if any(
                item["operator_nonce"] == operator_authorization.nonce
                for item in consumed
            ):
                raise DeploymentControlError(
                    "operator authorization identity was already consumed"
                )
            if prior:
                raise DeploymentControlError(
                    "activation transaction authorization was already consumed"
                )
            self.audit_ledger.append(intent)
            _, current_receipt_hash = self._latest_pass(current)
            if current_receipt_hash != event_hash:
                raise DeploymentControlError(
                    "promotion receipt head changed before canary commit"
                )

        try:
            prepared = self.control.guarded_prepare_canary_start(
                operator_authorization, now=current, guard=guard
            )
        except BaseException:
            self.reconcile_audit()
            raise
        self.reconcile_audit()
        return prepared
