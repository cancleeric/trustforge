"""Authenticated release activation control and routing-ledger projection."""

from __future__ import annotations

import hashlib
import http.client
import errno
import grp
import ipaddress
import json
import os
import secrets
import select
import socket
import stat
import urllib.parse
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from trustforge.activation_lock import (
    acquire_activation_lock,
    get_activation_lock,
    release_activation_lock,
)
from trustforge.agent.shadow_contracts import canonical_json
from trustforge.authenticated_ledger import GENESIS_HASH, LedgerError
from trustforge.release_router import (
    ReleaseEndpoint,
    ReleaseRoutingLedger,
    RoutingPolicy,
    RoutingSnapshot,
)
from trustforge.signed_event_ledger import SignedEventLedger

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
    expected_control_head: str
    expected_sequence: int
    actor: str
    issued_at: str
    expires_at: str
    nonce: str
    key_id: str
    signature: str
    receipt_version: str = "trustforge.deployment-authorization/v3"

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
        ledger: SignedEventLedger,
        *,
        outcome_ledger: SignedEventLedger,
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
        clock: Callable[[], datetime] | None = None,
    ):
        expected_confirmation = (
            f"PRODUCTION:{target}:{active.release_digest}:{candidate.release_digest}"
        )
        if not target or target_confirmation != expected_confirmation:
            raise DeploymentControlError(
                "explicit production target confirmation is required"
            )
        if not 1 <= stop_after_errors <= 100:
            raise DeploymentControlError("automatic stop threshold is invalid")
        self.ledger = ledger
        self.outcome_ledger = outcome_ledger
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
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.control_uid = ledger.directory_owner_uid

    def _current_time(self) -> datetime:
        observed = self.clock()
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise DeploymentControlError("control clock must be timezone aware")
        current = observed.astimezone(timezone.utc)
        if current.utcoffset() != timedelta(0):
            raise DeploymentControlError("control clock is not UTC")
        return current

    @property
    def _checkpoint_path(self):
        return self.ledger.directory / "authorization-checkpoint.json"

    def _read_checkpoint(self) -> dict[str, Any] | None:
        """Strictly read the pinned coarse control-plane time checkpoint."""
        with self.ledger.coordination_lock():
            return self._read_checkpoint_unlocked()

    def _read_checkpoint_unlocked(self) -> dict[str, Any] | None:
        """Read after the stable release coordination lock is held."""
        try:
            directory_fd = os.open(
                self.ledger.directory,
                os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
            directory_info = os.fstat(directory_fd)
            if (
                not stat.S_ISDIR(directory_info.st_mode)
                or directory_info.st_uid != self.control_uid
                or stat.S_IMODE(directory_info.st_mode) != self.ledger.directory_mode
            ):
                os.close(directory_fd)
                raise DeploymentControlError(
                    "authorization checkpoint directory is unsafe"
                )
            try:
                descriptor = os.open(
                    "authorization-checkpoint.json",
                    os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                    dir_fd=directory_fd,
                )
            except FileNotFoundError:
                os.close(directory_fd)
                return None
            try:
                info = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != self.control_uid
                    or info.st_gid
                    != (
                        grp.getgrnam(self.ledger.directory_group).gr_gid
                        if self.ledger.directory_group
                        else info.st_gid
                    )
                    or stat.S_IMODE(info.st_mode) != self.ledger.file_mode
                    or info.st_size > 65_536
                ):
                    raise DeploymentControlError(
                        "authorization checkpoint metadata is unsafe"
                    )
                raw = os.read(descriptor, 65_537)
            finally:
                os.close(descriptor)
                os.close(directory_fd)
        except OSError as exc:
            raise DeploymentControlError(
                "authorization checkpoint cannot be safely read"
            ) from exc
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DeploymentControlError("authorization checkpoint is corrupt") from exc
        required = {
            "schema",
            "floor_at",
            "ledger_id",
            "control_sequence",
            "control_head",
        }
        if (
            not isinstance(value, dict)
            or set(value) != required
            or canonical_json(value) + b"\n" != raw
        ):
            raise DeploymentControlError("authorization checkpoint schema is invalid")
        return value

    def _write_checkpoint(
        self,
        *,
        terminal_record: Mapping[str, Any],
        floor: datetime | None = None,
    ) -> None:
        """Publish the derived checkpoint under the external release lock."""
        with self.ledger.coordination_lock():
            self._write_checkpoint_unlocked(
                terminal_record=terminal_record, floor=floor
            )

    def _write_checkpoint_unlocked(
        self,
        *,
        terminal_record: Mapping[str, Any],
        floor: datetime | None = None,
    ) -> None:
        """Project the floor already authenticated by signed terminal history."""
        projected_floor = floor or self._terminal_floor(terminal_record["event"])
        value = {
            "schema": "trustforge.authorization-checkpoint/v2",
            "floor_at": projected_floor.isoformat(),
            "ledger_id": terminal_record["ledger_id"],
            "control_sequence": terminal_record["sequence"],
            "control_head": terminal_record["event_hash"],
        }
        data = canonical_json(value) + b"\n"
        directory_fd = os.open(
            self.ledger.directory,
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        directory_info = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(directory_info.st_mode)
            or directory_info.st_uid != self.control_uid
            or stat.S_IMODE(directory_info.st_mode) != self.ledger.directory_mode
        ):
            os.close(directory_fd)
            raise DeploymentControlError("authorization checkpoint directory is unsafe")
        temporary = (
            f".authorization-checkpoint-{os.getpid()}-{secrets.token_hex(8)}.tmp"
        )
        try:
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                self.ledger.file_mode,
                dir_fd=directory_fd,
            )
            try:
                os.fchmod(fd, self.ledger.file_mode)
                if self.ledger.directory_group:
                    os.fchown(
                        fd,
                        -1,
                        grp.getgrnam(self.ledger.directory_group).gr_gid,
                    )
                view = memoryview(data)
                while view:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise DeploymentControlError("short checkpoint write")
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            os.rename(
                temporary,
                "authorization-checkpoint.json",
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            os.fsync(directory_fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            os.close(directory_fd)

    def _checkpoint_clock_rolled_back(
        self,
        records: list[dict[str, Any]],
        *,
        now: datetime,
        heal: bool = False,
    ) -> bool:
        """Challenge/rebuild the projection from latest signed terminal history."""
        terminals = [
            record
            for record in records
            if record["event"].get("kind")
            in {"operator_stop", "activation_completed", "activation_failed"}
        ]
        if not terminals:
            return False
        terminal = terminals[-1]
        floor: datetime | None = None
        for record in terminals:
            floor = self._terminal_floor(record["event"], floor)
        assert floor is not None
        try:
            checkpoint = self._read_checkpoint()
        except DeploymentControlError:
            checkpoint = None
        expected = {
            "schema": "trustforge.authorization-checkpoint/v2",
            "floor_at": floor.isoformat(),
            "ledger_id": terminal["ledger_id"],
            "control_sequence": terminal["sequence"],
            "control_head": terminal["event_hash"],
        }
        if checkpoint != expected:
            if heal and self.ledger.can_sign:
                try:
                    self._write_checkpoint(terminal_record=terminal, floor=floor)
                except (DeploymentControlError, OSError):
                    return True
            else:
                return True
        return now < floor

    def rebuild_checkpoint(self) -> dict[str, Any]:
        """Rebuild the derived clock floor from authenticated terminal history."""
        if not self.ledger.can_sign:
            raise DeploymentControlError(
                "checkpoint rebuild requires the operator signing identity"
            )
        with self.ledger.coordination_lock():
            records = self._records()
            control_head = records[-1]["event_hash"]
            # Replay authorization/completion/outcome semantics without allowing
            # the normal read path to heal the projection under examination.
            self.routing_snapshot(heal_checkpoint=False)
            replayed = self._records()
            if replayed[-1]["event_hash"] != control_head:
                raise DeploymentControlError(
                    "control ledger changed during checkpoint rebuild"
                )
            terminals = [
                record
                for record in replayed
                if record["event"].get("kind")
                in {"operator_stop", "activation_completed", "activation_failed"}
            ]
            if not terminals:
                raise DeploymentControlError(
                    "checkpoint rebuild requires signed terminal history"
                )
            floor: datetime | None = None
            for record in terminals:
                floor = self._terminal_floor(record["event"], floor)
            assert floor is not None
            terminal = terminals[-1]
            self._write_checkpoint_unlocked(
                terminal_record=terminal,
                floor=floor,
            )
            checkpoint = self._read_checkpoint_unlocked()
            expected = {
                "schema": "trustforge.authorization-checkpoint/v2",
                "floor_at": floor.isoformat(),
                "ledger_id": terminal["ledger_id"],
                "control_sequence": terminal["sequence"],
                "control_head": terminal["event_hash"],
            }
            if checkpoint != expected:
                raise DeploymentControlError(
                    "rebuilt authorization checkpoint failed verification"
                )
            self.routing_snapshot(heal_checkpoint=False)
            if self._records()[-1]["event_hash"] != control_head:
                raise DeploymentControlError(
                    "control ledger changed while publishing checkpoint"
                )
            return expected

    def _publish_checkpoint_after_terminal(
        self,
        *,
        terminal_record: Mapping[str, Any],
    ) -> None:
        """Best-effort projection: signed history remains authoritative.

        Failure leaves B/start/promote blocked, but cannot wedge emergency
        control operations. A later successful terminal transition heals it.
        """
        try:
            self._write_checkpoint(terminal_record=terminal_record)
        except (DeploymentControlError, OSError):
            return

    @staticmethod
    def _verify_ed25519(
        keyring: Mapping[str, bytes],
        key_id: str,
        signature: str,
        domain: bytes,
        unsigned: Mapping[str, Any],
        label: str,
    ) -> None:
        key = keyring.get(key_id)
        try:
            if key is None:
                raise InvalidSignature
            Ed25519PublicKey.from_public_bytes(key).verify(
                bytes.fromhex(signature), domain + canonical_json(unsigned)
            )
        except (InvalidSignature, ValueError) as exc:
            raise DeploymentControlError(f"{label} signature is invalid") from exc

    def _validate_authorization_receipt(
        self,
        receipt: DeploymentAuthorization,
        *,
        action: Action,
        ledger_id: str,
        effective_at: datetime,
        expected_control_head: str,
        expected_sequence: int,
    ) -> None:
        self._verify_ed25519(
            self.authorization_keys,
            receipt.key_id,
            receipt.signature,
            b"trustforge.deployment-authorization.v3\x00",
            receipt.unsigned(),
            "authorization",
        )
        if (
            receipt.receipt_version != "trustforge.deployment-authorization/v3"
            or receipt.action != action
            or receipt.target != self.target
            or receipt.target_confirmation != self.target_confirmation
            or receipt.ledger_id != ledger_id
            or receipt.active_artifact_digest != self.active.release_digest
            or receipt.candidate_artifact_digest != self.candidate.release_digest
            or receipt.evidence_bundle_digest != self.evidence_bundle_digest
            or receipt.routing_policy_digest != self.policy.policy_digest
            or receipt.routing_key_id != self.policy.routing_key_id
            or receipt.expected_control_head != expected_control_head
            or receipt.expected_sequence != expected_sequence
            or not receipt.actor
            or not receipt.nonce
        ):
            raise DeploymentControlError("authorization binding is invalid")
        issued, expires = _utc(receipt.issued_at), _utc(receipt.expires_at)
        if (
            issued > effective_at
            or expires <= effective_at
            or expires <= issued
            or expires - issued > timedelta(minutes=15)
        ):
            raise DeploymentControlError("authorization is stale or future-dated")

    def _validate_completion_receipt(
        self,
        receipt: ActivationCompletionReceipt,
        *,
        prepared_record: Mapping[str, Any],
        effective_at: datetime,
    ) -> None:
        event = prepared_record.get("event", prepared_record)
        expected_pointer = (
            self.candidate.release_digest
            if receipt.action == "promote"
            else self.active.release_digest
        )
        pointer_valid = (
            receipt.pointer_active_digest == expected_pointer
            if receipt.status == "completed"
            else receipt.pointer_active_digest
            in {self.active.release_digest, self.candidate.release_digest}
        )
        if (
            receipt.receipt_version != "trustforge.activation-completion/v1"
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
        self._verify_ed25519(
            self.completion_keys,
            receipt.key_id,
            receipt.signature,
            b"trustforge.activation-completion.v1\x00",
            receipt.unsigned(),
            "activation completion",
        )
        verified = _utc(receipt.verified_at)
        if verified > effective_at or effective_at - verified > timedelta(minutes=10):
            raise DeploymentControlError("activation completion is stale")

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

    def _outcome_records(self, deployment_ledger_id: str) -> list[dict[str, Any]]:
        records = self.outcome_ledger.read()
        for record in records:
            if record["event"].get("deployment_ledger_id") != deployment_ledger_id:
                raise DeploymentControlError("router outcome ledger binding mismatch")
        return records

    @staticmethod
    def _canary_epoch(records: list[dict[str, Any]]) -> str | None:
        desired_by_transaction: dict[str, str] = {}
        epoch: str | None = None
        for record in records:
            event = record["event"]
            if event.get("kind") == "activation_prepared":
                desired_by_transaction[str(event["transaction_id"])] = str(
                    event["desired_phase"]
                )
            elif event.get("kind") == "activation_completed":
                desired = desired_by_transaction.get(str(event["transaction_id"]))
                epoch = record["event_hash"] if desired == "canary" else None
            elif event.get("kind") in {"activation_failed", "operator_stop"}:
                epoch = None
        return epoch

    @staticmethod
    def _terminal_floor(
        event: Mapping[str, Any], prior: datetime | None = None
    ) -> datetime:
        """Project a signed terminal floor, including unshipped pre-field v2."""
        terminal_at = _utc(event["at"])
        expected = max(prior or terminal_at, terminal_at)
        encoded = event.get("checkpoint_floor_at")
        return expected if encoded is None else _utc(encoded)

    @staticmethod
    def _next_checkpoint_floor(
        records: list[dict[str, Any]], terminal_time: datetime
    ) -> datetime:
        floor: datetime | None = None
        for record in records:
            event = record["event"]
            if event.get("kind") in {
                "operator_stop",
                "activation_completed",
                "activation_failed",
            }:
                floor = DeploymentControlLedger._terminal_floor(event, floor)
        return max(terminal_time, floor or terminal_time)

    def routing_snapshot(self, *, heal_checkpoint: bool = True) -> RoutingSnapshot:
        current_time = self._current_time()
        records = self._records()
        terminal_transaction_ids = {
            str(record["event"].get("transaction_id"))
            for record in records
            if record["event"].get("kind")
            in {"activation_completed", "activation_failed"}
        }
        outcome_records = self._outcome_records(records[0]["ledger_id"])
        phase = desired = "disabled"
        activation = "completed"
        requests = errors = 0
        prepared: dict[str, dict[str, Any]] = {}
        unresolved_transaction: str | None = None
        terminal_transactions: set[str] = set()
        reservations: dict[tuple[str, str], bool] = {}
        known_canary_epochs: set[str] = set()
        current_canary_epoch: str | None = None
        projected_floor: datetime | None = None
        for record_index, record in enumerate(records[1:], start=1):
            event = record["event"]
            kind = event.get("kind")
            if kind == "activation_prepared":
                try:
                    authorization = DeploymentAuthorization(
                        **event["authorization_receipt"]
                    )
                except (KeyError, TypeError) as exc:
                    raise DeploymentControlError(
                        "prepared activation authorization is absent"
                    ) from exc
                self._validate_authorization_receipt(
                    authorization,
                    action=event["action"],
                    ledger_id=records[0]["ledger_id"],
                    effective_at=(
                        current_time
                        if str(event["transaction_id"]) not in terminal_transaction_ids
                        else _utc(event["at"])
                    ),
                    expected_control_head=records[record_index - 1]["event_hash"],
                    expected_sequence=record["sequence"],
                )
                if (
                    event.get("nonce") != authorization.nonce
                    or event.get("actor") != authorization.actor
                ):
                    raise DeploymentControlError(
                        "prepared activation authorization identity mismatch"
                    )
                allowed_prior = {
                    "start": {"disabled", "stopped"},
                    "promote": {"canary"},
                    "rollback-a": {
                        "canary",
                        "stopped",
                        "promoted",
                        "recovery_required",
                    },
                }
                expected_desired = {
                    "start": "canary",
                    "promote": "promoted",
                    "rollback-a": "disabled",
                }
                expected_transaction = hashlib.sha256(
                    b"trustforge.activation-transaction.v1\x00"
                    + canonical_json(
                        {
                            "ledger_id": records[0]["ledger_id"],
                            "action": authorization.action,
                            "nonce": authorization.nonce,
                        }
                    )
                ).hexdigest()
                if (
                    authorization.action not in allowed_prior
                    or phase not in allowed_prior[authorization.action]
                    or event.get("desired_phase")
                    != expected_desired[authorization.action]
                    or event.get("transaction_id") != expected_transaction
                    or event.get("owner_id")
                    != f"deployment-control:{expected_transaction}"
                ):
                    raise DeploymentControlError(
                        "prepared activation semantics mismatch authorization"
                    )
                transaction = str(event["transaction_id"])
                if transaction in prepared or unresolved_transaction is not None:
                    raise DeploymentControlError("duplicate activation transaction")
                if (
                    event.get("evidence_bundle_digest") != self.evidence_bundle_digest
                    or event.get("active_artifact_digest") != self.active.release_digest
                    or event.get("candidate_artifact_digest")
                    != self.candidate.release_digest
                    or event.get("routing_policy_digest") != self.policy.policy_digest
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
                try:
                    completion = ActivationCompletionReceipt(
                        **event["completion_receipt"]
                    )
                except (KeyError, TypeError) as exc:
                    raise DeploymentControlError(
                        "activation completion receipt is absent"
                    ) from exc
                self._validate_completion_receipt(
                    completion,
                    prepared_record=prior,
                    effective_at=_utc(event["at"]),
                )
                expected_floor = max(
                    _utc(event["at"]),
                    projected_floor or _utc(event["at"]),
                )
                encoded_floor = event.get("checkpoint_floor_at")
                if encoded_floor is not None and _utc(encoded_floor) != expected_floor:
                    raise DeploymentControlError(
                        "activation checkpoint floor is invalid"
                    )
                projected_floor = expected_floor
                expected_receipt_digest = (
                    "sha256:"
                    + hashlib.sha256(canonical_json(asdict(completion))).hexdigest()
                )
                if (
                    event.get("nonce") != completion.nonce
                    or event.get("actor") != completion.actor
                    or event.get("activation_receipt_digest") != expected_receipt_digest
                ):
                    raise DeploymentControlError(
                        "activation completion receipt identity mismatch"
                    )
                expected_kind = (
                    "activation_completed"
                    if completion.status == "completed"
                    else "activation_failed"
                )
                if (
                    kind != expected_kind
                    or event.get("transaction_id") != completion.transaction_id
                    or event.get("action") != completion.action
                    or event.get("prepared_event_hash")
                    != completion.prepared_event_hash
                    or event.get("pointer_active_digest")
                    != completion.pointer_active_digest
                    or event.get("observed_manifest_digest")
                    != completion.observed_manifest_digest
                ):
                    raise DeploymentControlError(
                        "activation completion semantics mismatch receipt"
                    )
                terminal_transactions.add(transaction)
                unresolved_transaction = None
                if kind == "activation_completed":
                    phase = desired = str(prior["desired_phase"])
                    activation = "completed"
                    if phase == "canary":
                        current_canary_epoch = record["event_hash"]
                        known_canary_epochs.add(current_canary_epoch)
                    else:
                        current_canary_epoch = None
                else:
                    observed = event.get("pointer_active_digest")
                    if observed == self.candidate.release_digest:
                        phase = desired = "recovery_required"
                    else:
                        phase = desired = "stopped" if phase == "canary" else "disabled"
                    activation = "failed"
                    current_canary_epoch = None
            elif kind == "operator_stop":
                try:
                    authorization = DeploymentAuthorization(
                        **event["authorization_receipt"]
                    )
                except (KeyError, TypeError) as exc:
                    raise DeploymentControlError(
                        "operator stop authorization is absent"
                    ) from exc
                self._validate_authorization_receipt(
                    authorization,
                    action="stop",
                    ledger_id=records[0]["ledger_id"],
                    effective_at=_utc(event["at"]),
                    expected_control_head=records[record_index - 1]["event_hash"],
                    expected_sequence=record["sequence"],
                )
                if (
                    event.get("nonce") != authorization.nonce
                    or event.get("actor") != authorization.actor
                ):
                    raise DeploymentControlError(
                        "operator stop authorization identity mismatch"
                    )
                expected_floor = max(
                    _utc(event["at"]),
                    projected_floor or _utc(event["at"]),
                )
                encoded_floor = event.get("checkpoint_floor_at")
                if encoded_floor is not None and _utc(encoded_floor) != expected_floor:
                    raise DeploymentControlError(
                        "operator stop checkpoint floor is invalid"
                    )
                projected_floor = expected_floor
                if phase != "canary":
                    raise DeploymentControlError("operator stop prior phase is invalid")
                phase = desired = "stopped"
                activation = "completed"
                current_canary_epoch = None
            else:
                raise DeploymentControlError("unknown deployment ledger event")
        for record in outcome_records:
            event = record["event"]
            kind = event.get("kind")
            epoch = str(event.get("canary_epoch", ""))
            if epoch not in known_canary_epochs:
                raise DeploymentControlError("router outcome has unknown canary epoch")
            if kind == "candidate_reservation":
                reservation_id = str(event.get("reservation_id", ""))
                key = (epoch, reservation_id)
                if key in reservations:
                    raise DeploymentControlError("duplicate candidate reservation")
                if event.get("control_head") != epoch:
                    raise DeploymentControlError(
                        "candidate reservation control binding mismatch"
                    )
                reservations[key] = False
                if epoch == current_canary_epoch:
                    requests += 1
            elif kind == "candidate_result":
                reservation_id = str(event.get("reservation_id", ""))
                key = (epoch, reservation_id)
                if key not in reservations or reservations[key]:
                    raise DeploymentControlError(
                        "candidate outcome is orphaned or repeated"
                    )
                if event.get("control_head") != epoch:
                    raise DeploymentControlError(
                        "candidate outcome control binding mismatch"
                    )
                reservations[key] = True
                if epoch == current_canary_epoch:
                    errors = 0 if event.get("ok") is True else errors + 1
                if (
                    epoch == current_canary_epoch
                    and event.get("automatic_stop") is True
                ):
                    if errors < self.stop_after_errors:
                        raise DeploymentControlError("premature automatic stop event")
                    phase = desired = "stopped"
                    activation = "completed"
                    current_canary_epoch = None
            elif kind == "router_emergency_stop":
                if event.get("control_head") != epoch:
                    raise DeploymentControlError(
                        "router emergency control binding mismatch"
                    )
                if epoch == current_canary_epoch:
                    phase = desired = "stopped"
                    activation = "completed"
                    current_canary_epoch = None
            else:
                raise DeploymentControlError("unknown router outcome event")
        if current_canary_epoch is not None and self.ledger.epoch_stopped(
            ledger_id=records[0]["ledger_id"],
            canary_epoch=current_canary_epoch,
        ):
            phase = desired = "stopped"
            activation = "completed"
            current_canary_epoch = None
        clock_rolled_back = self._checkpoint_clock_rolled_back(
            records,
            now=current_time,
            heal=heal_checkpoint and self.ledger.can_sign,
        )
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
            ledger_head=(
                outcome_records[-1]["event_hash"] if outcome_records else GENESIS_HASH
            ),
            candidate_blocked=clock_rolled_back,
        )

    def _verify_authorization(
        self, receipt: DeploymentAuthorization, *, action: Action, now: datetime
    ) -> None:
        snapshot = self.routing_snapshot()
        records = self._records()
        self._validate_authorization_receipt(
            receipt,
            action=action,
            ledger_id=snapshot.ledger_id,
            effective_at=now,
            expected_control_head=records[-1]["event_hash"],
            expected_sequence=len(records) + 1,
        )

    def prepare(
        self, action: Action, receipt: DeploymentAuthorization, *, now: datetime
    ) -> dict[str, Any]:
        with self.ledger.coordination_lock():
            return self._prepare_locked(action, receipt, now=now)

    def _prepare_locked(
        self, action: Action, receipt: DeploymentAuthorization, *, now: datetime
    ) -> dict[str, Any]:
        trusted_now = self._current_time()
        if abs((now.astimezone(timezone.utc) - trusted_now).total_seconds()) > 5:
            raise DeploymentControlError("operator time is not current")
        now = trusted_now
        self._verify_authorization(receipt, action=action, now=now)
        state = self.routing_snapshot()
        control_records = self._records()
        control_head = control_records[-1]["event_hash"]
        allowed = {
            "start": {"disabled", "stopped"},
            "promote": {"canary"},
            "rollback-a": {"canary", "stopped", "promoted", "recovery_required"},
            # ``stopped`` covers the crash-safe latch-first state where the
            # signed operator_stop terminal event has not yet been appended.
            "stop": {"canary", "stopped"},
        }
        if (
            state.phase not in allowed[action]
            or state.activation_status == "prepared"
            or (action in {"start", "promote"} and state.candidate_blocked)
        ):
            raise DeploymentControlError("action is not allowed from current state")
        if action == "stop":
            canary_epoch = self._canary_epoch(control_records)
            if canary_epoch is None:
                raise DeploymentControlError("operator stop has no canary epoch")
            # Publish the one-way latch before waiting on any data-plane outcome.
            self.ledger.trip_epoch_stop(
                ledger_id=state.ledger_id, canary_epoch=canary_epoch
            )
            result = self.ledger.append(
                {
                    "kind": "operator_stop",
                    "nonce": receipt.nonce,
                    "actor": receipt.actor,
                    "at": now.isoformat(),
                    "checkpoint_floor_at": self._next_checkpoint_floor(
                        control_records, now
                    ).isoformat(),
                    "authorization_receipt": asdict(receipt),
                },
                expected_head=control_head,
            )
            self._publish_checkpoint_after_terminal(terminal_record=result)
            return result
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
                    "authorization_receipt": asdict(receipt),
                },
                expected_head=control_head,
            )
        except BaseException:
            release_activation_lock(self.target, owner_id)
            raise

    def complete(
        self, receipt: ActivationCompletionReceipt, *, now: datetime
    ) -> dict[str, Any]:
        with self.ledger.coordination_lock():
            return self._complete_locked(receipt, now=now)

    def _complete_locked(
        self, receipt: ActivationCompletionReceipt, *, now: datetime
    ) -> dict[str, Any]:
        trusted_now = self._current_time()
        if abs((now.astimezone(timezone.utc) - trusted_now).total_seconds()) > 5:
            raise DeploymentControlError("operator time is not current")
        now = trusted_now
        records = self._records()
        reconciled_state = self.routing_snapshot()
        terminal_ids = {
            str(record["event"].get("transaction_id"))
            for record in records
            if record["event"].get("kind")
            in {"activation_completed", "activation_failed"}
        }
        unresolved = [
            record
            for record in records
            if record["event"].get("kind") == "activation_prepared"
            and str(record["event"].get("transaction_id")) not in terminal_ids
        ]
        prepared_record = unresolved[-1] if unresolved else None
        if prepared_record is None:
            raise DeploymentControlError("activation transaction is unknown")
        if prepared_record["event"].get("transaction_id") != receipt.transaction_id:
            raise DeploymentControlError(
                "completion is not for latest unresolved transaction"
            )
        event = prepared_record["event"]
        if (
            receipt.status == "completed"
            and event.get("action") in {"start", "promote"}
            and reconciled_state.candidate_blocked
        ):
            raise DeploymentControlError("trusted wall clock rolled back")
        self._validate_completion_receipt(
            receipt, prepared_record=prepared_record, effective_at=now
        )
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
                "action": receipt.action,
                "prepared_event_hash": receipt.prepared_event_hash,
                "pointer_active_digest": receipt.pointer_active_digest,
                "observed_manifest_digest": receipt.observed_manifest_digest,
                "activation_receipt_digest": "sha256:"
                + hashlib.sha256(
                    canonical_json(
                        receipt.unsigned() | {"signature": receipt.signature}
                    )
                ).hexdigest(),
                "nonce": receipt.nonce,
                "actor": receipt.actor,
                "at": now.isoformat(),
                "checkpoint_floor_at": self._next_checkpoint_floor(
                    records, now
                ).isoformat(),
                "completion_receipt": asdict(receipt),
            },
            expected_head=records[-1]["event_hash"],
        )
        release_activation_lock(self.target, event["owner_id"])
        self._publish_checkpoint_after_terminal(terminal_record=result)
        return result

    def reserve_candidate(
        self,
        *,
        expected_head: str,
        reservation_id: str,
    ) -> RoutingSnapshot:
        with self.ledger.coordination_lock():
            return self._reserve_candidate_locked(
                expected_head=expected_head, reservation_id=reservation_id
            )

    def _reserve_candidate_locked(
        self,
        *,
        expected_head: str,
        reservation_id: str,
    ) -> RoutingSnapshot:
        if len(reservation_id) != 32 or any(
            character not in "0123456789abcdef" for character in reservation_id
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
        control_records = self._records()
        deployment_ledger_id = control_records[0]["ledger_id"]
        canary_epoch = self._canary_epoch(control_records)
        if canary_epoch is None:
            raise LedgerError("candidate reservation has no active canary epoch")
        self.outcome_ledger.append(
            {
                "kind": "candidate_reservation",
                "deployment_ledger_id": deployment_ledger_id,
                "canary_epoch": canary_epoch,
                "control_head": control_records[-1]["event_hash"],
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
        # A successful reservation can have its terminal append invalidated by
        # every other in-flight candidate.  Keep this retry budget above the
        # router's documented small canary concurrency so ordinary contention
        # does not manufacture an unrecordable outcome and trip emergency stop.
        for _attempt in range(8):
            control_records = self._records()
            deployment_ledger_id = control_records[0]["ledger_id"]
            records = self._outcome_records(deployment_ledger_id)
            existing = next(
                (
                    record
                    for record in records
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
                raise LedgerError(
                    "candidate reservation has a different terminal outcome"
                )
            reservation = next(
                (
                    record["event"]
                    for record in records
                    if record["event"].get("kind") == "candidate_reservation"
                    and record["event"].get("reservation_id") == reservation_id
                ),
                None,
            )
            if reservation is None:
                raise LedgerError("candidate outcome has no reservation")
            canary_epoch = str(reservation["canary_epoch"])
            state = self.routing_snapshot()
            next_errors = 0 if ok else state.consecutive_errors + 1
            try:
                self.outcome_ledger.append(
                    {
                        "kind": "candidate_result",
                        "deployment_ledger_id": deployment_ledger_id,
                        "canary_epoch": canary_epoch,
                        "control_head": str(reservation["control_head"]),
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

    @contextmanager
    def candidate_connection(
        self,
        *,
        endpoint: ReleaseEndpoint,
        reservation_id: str,
        connect_timeout: float,
    ):
        """Check the epoch latch and establish B under one bounded lock handoff."""
        connection: http.client.HTTPConnection | None = None
        with self.ledger.coordination_lock():
            records = self._records()
            epoch = self._canary_epoch(records)
            state = self.routing_snapshot()
            reservation = next(
                (
                    item["event"]
                    for item in self._outcome_records(records[0]["ledger_id"])
                    if item["event"].get("kind") == "candidate_reservation"
                    and item["event"].get("reservation_id") == reservation_id
                ),
                None,
            )
            if (
                endpoint != self.candidate
                or not 0 < connect_timeout <= 0.25
                or epoch is None
                or self.ledger.epoch_stopped(
                    ledger_id=records[0]["ledger_id"], canary_epoch=epoch
                )
                or reservation is None
                or reservation.get("canary_epoch") != epoch
                or state.phase != "canary"
                or state.activation_status != "completed"
                or state.candidate_blocked
            ):
                raise LedgerError("candidate execution authorization is stale")
            parsed = urllib.parse.urlsplit(endpoint.base_url)
            connection = http.client.HTTPConnection(
                parsed.hostname, parsed.port, timeout=connect_timeout
            )
            # connect() is the atomic classification point. The coordination
            # lock is released immediately afterward, before request/response.
            address = ipaddress.ip_address(str(parsed.hostname))
            family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
            destination = (
                (str(address), int(parsed.port), 0, 0)
                if family == socket.AF_INET6
                else (str(address), int(parsed.port))
            )
            candidate_socket = socket.socket(family, socket.SOCK_STREAM)
            try:
                candidate_socket.setblocking(False)
                result = candidate_socket.connect_ex(destination)
                if result not in {
                    0,
                    errno.EINPROGRESS,
                    errno.EWOULDBLOCK,
                    errno.EALREADY,
                }:
                    raise OSError(result, os.strerror(result))
                if result != 0:
                    _, writable, _ = select.select(
                        [], [candidate_socket], [], connect_timeout
                    )
                    if not writable:
                        raise TimeoutError("candidate connect deadline exceeded")
                    socket_error = candidate_socket.getsockopt(
                        socket.SOL_SOCKET, socket.SO_ERROR
                    )
                    if socket_error:
                        raise OSError(socket_error, os.strerror(socket_error))
                candidate_socket.setblocking(True)
                candidate_socket.settimeout(connect_timeout)
                connection.sock = candidate_socket
            except BaseException:
                candidate_socket.close()
                raise
        try:
            yield connection
        finally:
            connection.close()

    def emergency_stop(self, *, ledger_id: str, reason: str) -> None:
        if reason != "candidate_outcome_unrecordable":
            raise LedgerError("router cannot create operator stop events")
        with self.ledger.coordination_lock():
            for _attempt in range(8):
                state = self.routing_snapshot()
                if state.ledger_id != ledger_id:
                    raise LedgerError("router emergency stop ledger identity mismatch")
                control_records = self._records()
                canary_epoch = self._canary_epoch(control_records)
                if canary_epoch is None:
                    raise LedgerError(
                        "router emergency stop has no active canary epoch"
                    )
                records = self._outcome_records(ledger_id)
                nonce = f"router-emergency:{canary_epoch}:{reason}"
                if any(item["event"].get("nonce") == nonce for item in records):
                    return
                try:
                    self.outcome_ledger.append(
                        {
                            "kind": "router_emergency_stop",
                            "deployment_ledger_id": ledger_id,
                            "canary_epoch": canary_epoch,
                            "control_head": canary_epoch,
                            "reason": reason,
                            "nonce": nonce,
                        },
                        expected_head=(
                            records[-1]["event_hash"] if records else GENESIS_HASH
                        ),
                    )
                    return
                except LedgerError:
                    continue
        raise LedgerError("router emergency stop contention budget exhausted")
