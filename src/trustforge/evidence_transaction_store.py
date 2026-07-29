"""Fail-closed transactional storage for release evidence.

The store deliberately uses two different wire formats:

* staging objects contain only an opaque base64 payload and can never be
  interpreted as an eligible verdict;
* the canonical object is a commit marker which binds the payload digest.

A durable tombstone always wins over a canonical marker.  This makes recovery
and indeterminate cleanup fail closed: a leftover canonical object is not
eligible until the transaction has no tombstone and canonical metadata is
proved safe.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

STAGING_SCHEMA = "trustforge.release-evidence-staging/v1"
COMMIT_SCHEMA = "trustforge.release-evidence-commit/v1"
TOMBSTONE_SCHEMA = "trustforge.release-evidence-tombstone/v1"
CANONICAL_NAME = "release-ingress-evidence.json"
TOMBSTONE_NAME = "release-ingress-evidence.tombstone"
STATE_NAME = ".release-evidence-transaction-state"
STATE_SCHEMA = "trustforge.release-evidence-transaction-state/v1"
MAX_EVIDENCE_BYTES = 8 * 1024 * 1024
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_TRANSACTION_RE = re.compile(r"[0-9a-f]{32}\Z")


class EvidenceTransactionError(RuntimeError):
    """The transaction did not establish an eligible durable result."""


@dataclass(frozen=True)
class Eligibility:
    eligible: bool
    payload: bytes | None
    digest: str | None
    reason: str


FaultHook = Callable[[str], None]


def _canonical_json(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("ascii")
        + b"\n"
    )


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _strict_object(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceTransactionError(
            "transaction object is not canonical JSON"
        ) from exc
    if not isinstance(value, dict) or _canonical_json(value) != raw:
        raise EvidenceTransactionError("transaction object is not canonical JSON")
    return value


def _safe_regular(metadata: os.stat_result, expected_uid: int) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == expected_uid
        and stat.S_IMODE(metadata.st_mode) == 0o600
        and metadata.st_nlink == 1
    )


class EvidenceTransactionStore:
    """Publish immutable evidence with fail-closed recovery semantics."""

    def __init__(
        self,
        directory: Path,
        *,
        expected_uid: int = 0,
        fault_hook: FaultHook | None = None,
    ) -> None:
        self.directory = directory
        self.expected_uid = expected_uid
        self._fault_hook = fault_hook

    def _fault(self, point: str) -> None:
        if self._fault_hook is not None:
            self._fault_hook(point)

    def _open_directory(self) -> int:
        descriptor = os.open(
            self.directory,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != self.expected_uid
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            os.close(descriptor)
            raise EvidenceTransactionError("evidence directory metadata is unsafe")
        return descriptor

    def _open_state(self, directory_fd: int) -> int:
        created = False
        try:
            descriptor = os.open(
                STATE_NAME,
                os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=directory_fd,
            )
        except FileNotFoundError:
            try:
                descriptor = os.open(
                    STATE_NAME,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600,
                    dir_fd=directory_fd,
                )
                created = True
            except FileExistsError:
                descriptor = os.open(
                    STATE_NAME,
                    os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=directory_fd,
                )
        metadata = os.fstat(descriptor)
        if (
            not _safe_regular(metadata, self.expected_uid)
            or metadata.st_dev != os.fstat(directory_fd).st_dev
        ):
            os.close(descriptor)
            raise EvidenceTransactionError("transaction state metadata is unsafe")
        if created:
            os.fsync(descriptor)
            os.fsync(directory_fd)
        return descriptor

    def _assert_coordination_identity(self, directory_fd: int, state_fd: int) -> None:
        self._fault("coordination:verify")
        directory = os.fstat(directory_fd)
        path_directory = os.stat(self.directory, follow_symlinks=False)
        state = os.fstat(state_fd)
        path_state = os.stat(STATE_NAME, dir_fd=directory_fd, follow_symlinks=False)
        if (
            (directory.st_dev, directory.st_ino)
            != (path_directory.st_dev, path_directory.st_ino)
            or not stat.S_ISDIR(path_directory.st_mode)
            or path_directory.st_uid != self.expected_uid
            or stat.S_IMODE(path_directory.st_mode) & 0o022
            or (state.st_dev, state.st_ino) != (path_state.st_dev, path_state.st_ino)
            or state.st_dev != directory.st_dev
            or not _safe_regular(state, self.expected_uid)
            or not _safe_regular(path_state, self.expected_uid)
        ):
            raise EvidenceTransactionError("coordination authority identity changed")

    def _owns_canonical(
        self, directory_fd: int, *, transaction_id: str, digest: str
    ) -> bool:
        try:
            raw, metadata = self._read_name(directory_fd, CANONICAL_NAME)
            marker = _strict_object(raw)
        except (FileNotFoundError, OSError, EvidenceTransactionError):
            return False
        return (
            stat.S_ISREG(metadata.st_mode)
            and metadata.st_uid == self.expected_uid
            and stat.S_IMODE(metadata.st_mode) == 0o600
            and metadata.st_nlink in {1, 2}
            and marker.get("schema") == COMMIT_SCHEMA
            and marker.get("transaction_id") == transaction_id
            and marker.get("evidence_digest") == digest
        )

    def _state_records(self, descriptor: int) -> list[dict[str, Any]]:
        if not _safe_regular(os.fstat(descriptor), self.expected_uid):
            raise EvidenceTransactionError("transaction state metadata changed")
        os.lseek(descriptor, 0, os.SEEK_SET)
        raw = bytearray()
        while len(raw) <= MAX_EVIDENCE_BYTES:
            chunk = os.read(descriptor, min(65536, MAX_EVIDENCE_BYTES + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        if len(raw) > MAX_EVIDENCE_BYTES:
            raise EvidenceTransactionError("transaction state is too large")
        records: list[dict[str, Any]] = []
        previous = "sha256:" + "0" * 64
        for sequence, line in enumerate(bytes(raw).splitlines(keepends=True), start=1):
            record = _strict_object(line)
            if set(record) != {
                "schema",
                "sequence",
                "transaction_id",
                "evidence_digest",
                "owner_pid",
                "state",
                "previous_hash",
                "record_hash",
            }:
                raise EvidenceTransactionError("transaction state fields are invalid")
            unsigned = {
                key: value for key, value in record.items() if key != "record_hash"
            }
            expected_hash = _digest(_canonical_json(unsigned))
            if (
                record["schema"] != STATE_SCHEMA
                or record["sequence"] != sequence
                or record["state"] not in {"BEGIN", "COMMIT", "ABORT"}
                or not isinstance(record["transaction_id"], str)
                or _TRANSACTION_RE.fullmatch(record["transaction_id"]) is None
                or not isinstance(record["evidence_digest"], str)
                or _DIGEST_RE.fullmatch(record["evidence_digest"]) is None
                or not isinstance(record["owner_pid"], int)
                or isinstance(record["owner_pid"], bool)
                or record["owner_pid"] <= 0
                or record["previous_hash"] != previous
                or record["record_hash"] != expected_hash
            ):
                raise EvidenceTransactionError("transaction state chain is invalid")
            records.append(record)
            previous = expected_hash
        open_transaction: tuple[str, str, int] | None = None
        for record in records:
            identity = (
                record["transaction_id"],
                record["evidence_digest"],
                record["owner_pid"],
            )
            if record["state"] == "BEGIN":
                if open_transaction is not None:
                    raise EvidenceTransactionError(
                        "transaction state contains nested ownership"
                    )
                open_transaction = identity
            elif open_transaction != identity:
                raise EvidenceTransactionError(
                    "terminal state does not match transaction owner"
                )
            else:
                open_transaction = None
        return records

    def _append_state(
        self,
        descriptor: int,
        *,
        transaction_id: str,
        digest: str,
        state: str,
        owner_pid: int | None = None,
    ) -> None:
        records = self._state_records(descriptor)
        unsigned = {
            "schema": STATE_SCHEMA,
            "sequence": len(records) + 1,
            "transaction_id": transaction_id,
            "evidence_digest": digest,
            "owner_pid": os.getpid() if owner_pid is None else owner_pid,
            "state": state,
            "previous_hash": (
                records[-1]["record_hash"] if records else "sha256:" + "0" * 64
            ),
        }
        raw = _canonical_json(
            {**unsigned, "record_hash": _digest(_canonical_json(unsigned))}
        )
        self._fault(f"state:{state.lower()}:write")
        os.lseek(descriptor, 0, os.SEEK_END)
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise EvidenceTransactionError(
                    "transaction state write made no progress"
                )
            view = view[written:]
        self._fault(f"state:{state.lower()}:fsync")
        os.fsync(descriptor)

    def _write_exclusive(
        self, directory_fd: int, name: str, raw: bytes, *, prefix: str
    ) -> None:
        self._fault(f"{prefix}:open")
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=directory_fd,
        )
        try:
            view = memoryview(raw)
            while view:
                self._fault(f"{prefix}:write")
                written = os.write(descriptor, view)
                if written <= 0:
                    raise EvidenceTransactionError(f"{prefix} write made no progress")
                view = view[written:]
            self._fault(f"{prefix}:fsync")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _fsync_directory(self, descriptor: int, point: str) -> None:
        self._fault(point)
        os.fsync(descriptor)

    def _unlink(self, descriptor: int, name: str, point: str) -> None:
        self._fault(point)
        os.unlink(name, dir_fd=descriptor)

    def _read_name(self, directory_fd: int, name: str) -> tuple[bytes, os.stat_result]:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=directory_fd,
        )
        try:
            before = os.fstat(descriptor)
            raw = bytearray()
            while len(raw) <= MAX_EVIDENCE_BYTES:
                chunk = os.read(
                    descriptor, min(65536, MAX_EVIDENCE_BYTES + 1 - len(raw))
                )
                if not chunk:
                    break
                raw.extend(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        if before != after or len(raw) > MAX_EVIDENCE_BYTES:
            raise EvidenceTransactionError("transaction object changed while reading")
        return bytes(raw), before

    def _durable_tombstone(
        self, directory_fd: int, *, transaction_id: str, digest: str, reason: str
    ) -> None:
        raw = _canonical_json(
            {
                "schema": TOMBSTONE_SCHEMA,
                "transaction_id": transaction_id,
                "evidence_digest": digest,
                "disposition": "NON_PASS",
                "reason": reason,
            }
        )
        try:
            existing, metadata = self._read_name(directory_fd, TOMBSTONE_NAME)
        except FileNotFoundError:
            self._write_exclusive(directory_fd, TOMBSTONE_NAME, raw, prefix="tombstone")
        else:
            value = _strict_object(existing)
            if (
                not _safe_regular(metadata, self.expected_uid)
                or value.get("schema") != TOMBSTONE_SCHEMA
                or value.get("disposition") != "NON_PASS"
            ):
                raise EvidenceTransactionError("existing tombstone is unsafe")
        self._fsync_directory(directory_fd, "tombstone:dir-fsync")

    def _canonical_eligibility(
        self,
        directory_fd: int,
        *,
        expected_payload: bytes | None = None,
        check_staging: bool = True,
        state_records: list[dict[str, Any]] | None = None,
    ) -> Eligibility:
        try:
            tombstone, tombstone_metadata = self._read_name(
                directory_fd, TOMBSTONE_NAME
            )
        except FileNotFoundError:
            pass
        except OSError:
            return Eligibility(False, None, None, "unsafe tombstone")
        else:
            try:
                value = _strict_object(tombstone)
            except EvidenceTransactionError:
                return Eligibility(False, None, None, "unsafe tombstone")
            if (
                not _safe_regular(tombstone_metadata, self.expected_uid)
                or value.get("schema") != TOMBSTONE_SCHEMA
                or value.get("disposition") != "NON_PASS"
            ):
                return Eligibility(False, None, None, "unsafe tombstone")
            return Eligibility(False, None, value.get("evidence_digest"), "tombstoned")

        if check_staging and any(
            name.startswith(".evidence-")
            and (name.endswith(".stage") or name.endswith(".prepared"))
            for name in os.listdir(directory_fd)
        ):
            return Eligibility(False, None, None, "stale transaction is ineligible")

        if not state_records or state_records[-1]["state"] != "COMMIT":
            return Eligibility(
                False, None, None, "transaction has no committed terminal state"
            )

        try:
            raw, metadata = self._read_name(directory_fd, CANONICAL_NAME)
        except (FileNotFoundError, OSError, EvidenceTransactionError):
            return Eligibility(
                False, None, None, "canonical marker absent or unreadable"
            )
        if not _safe_regular(metadata, self.expected_uid):
            return Eligibility(False, None, None, "canonical metadata is unsafe")
        try:
            marker = _strict_object(raw)
            if set(marker) != {
                "schema",
                "state",
                "transaction_id",
                "evidence_digest",
                "payload_b64",
            }:
                raise EvidenceTransactionError("canonical marker fields are invalid")
            if marker["schema"] != COMMIT_SCHEMA or marker["state"] != "ELIGIBLE":
                raise EvidenceTransactionError("canonical marker is not eligible")
            if (
                not isinstance(marker["transaction_id"], str)
                or _TRANSACTION_RE.fullmatch(marker["transaction_id"]) is None
                or not isinstance(marker["evidence_digest"], str)
                or _DIGEST_RE.fullmatch(marker["evidence_digest"]) is None
                or not isinstance(marker["payload_b64"], str)
            ):
                raise EvidenceTransactionError("canonical marker types are invalid")
            payload = base64.b64decode(marker["payload_b64"], validate=True)
            digest = _digest(payload)
            if marker["evidence_digest"] != digest:
                raise EvidenceTransactionError("canonical payload digest mismatch")
            terminal = state_records[-1]
            if (
                terminal["evidence_digest"] != digest
                or terminal["transaction_id"] != marker["transaction_id"]
            ):
                raise EvidenceTransactionError(
                    "terminal state does not bind canonical marker"
                )
            if expected_payload is not None and payload != expected_payload:
                return Eligibility(
                    False, payload, digest, "different immutable evidence"
                )
        except (EvidenceTransactionError, ValueError, TypeError):
            return Eligibility(False, None, None, "canonical marker is invalid")
        return Eligibility(True, payload, digest, "eligible")

    def eligibility(self) -> Eligibility:
        directory_fd = self._open_directory()
        state_fd = -1
        try:
            fcntl.flock(directory_fd, fcntl.LOCK_EX)
            state_fd = self._open_state(directory_fd)
            fcntl.flock(state_fd, fcntl.LOCK_SH)
            self._assert_coordination_identity(directory_fd, state_fd)
            return self._canonical_eligibility(
                directory_fd, state_records=self._state_records(state_fd)
            )
        except EvidenceTransactionError as exc:
            return Eligibility(False, None, None, str(exc))
        finally:
            if state_fd >= 0:
                os.close(state_fd)
            os.close(directory_fd)

    def _publish(self, payload: bytes) -> Eligibility:
        """Low-level B3 primitive; only the future B5 authority may call it."""
        if (
            not isinstance(payload, bytes)
            or not payload
            or len(payload) > MAX_EVIDENCE_BYTES
        ):
            raise EvidenceTransactionError("evidence payload size is invalid")
        digest = _digest(payload)
        transaction_id = uuid.uuid4().hex
        staging_name = f".evidence-{transaction_id}.stage"
        prepared_name = f".evidence-{transaction_id}.prepared"
        staging = _canonical_json(
            {
                "schema": STAGING_SCHEMA,
                "state": "INELIGIBLE",
                "transaction_id": transaction_id,
                "evidence_digest": digest,
                "opaque_payload_b64": base64.b64encode(payload).decode("ascii"),
            }
        )
        marker = _canonical_json(
            {
                "schema": COMMIT_SCHEMA,
                "state": "ELIGIBLE",
                "transaction_id": transaction_id,
                "evidence_digest": digest,
                "payload_b64": base64.b64encode(payload).decode("ascii"),
            }
        )
        directory_fd = self._open_directory()
        state_fd = -1
        canonical_created = False
        transaction_started = False
        try:
            fcntl.flock(directory_fd, fcntl.LOCK_EX)
            state_fd = self._open_state(directory_fd)
            fcntl.flock(state_fd, fcntl.LOCK_EX)
            self._assert_coordination_identity(directory_fd, state_fd)
            records = self._state_records(state_fd)
            current = self._canonical_eligibility(directory_fd, state_records=records)
            if current.eligible:
                if current.payload == payload:
                    return current
                raise EvidenceTransactionError("different immutable evidence")
            if current.reason in {
                "tombstoned",
                "unsafe tombstone",
                "canonical marker is invalid",
                "canonical metadata is unsafe",
                "stale transaction is ineligible",
            }:
                raise EvidenceTransactionError(current.reason)
            try:
                self._read_name(directory_fd, CANONICAL_NAME)
            except FileNotFoundError:
                pass
            else:
                raise EvidenceTransactionError(
                    "prior noneligible evidence is immutable; recovery required"
                )

            transaction_started = True
            self._append_state(
                state_fd,
                transaction_id=transaction_id,
                digest=digest,
                state="BEGIN",
            )
            self._assert_coordination_identity(directory_fd, state_fd)
            self._write_exclusive(directory_fd, staging_name, staging, prefix="staging")
            self._fsync_directory(directory_fd, "staging:dir-fsync")
            self._assert_coordination_identity(directory_fd, state_fd)
            self._write_exclusive(
                directory_fd, prepared_name, marker, prefix="prepared"
            )
            self._fsync_directory(directory_fd, "prepared:dir-fsync")
            self._assert_coordination_identity(directory_fd, state_fd)
            try:
                self._fault("canonical:link")
                os.link(
                    prepared_name,
                    CANONICAL_NAME,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                    follow_symlinks=False,
                )
                canonical_created = True
            except FileExistsError:
                current = self._canonical_eligibility(
                    directory_fd,
                    expected_payload=payload,
                    check_staging=False,
                    state_records=records,
                )
                if current.payload != payload:
                    raise EvidenceTransactionError(current.reason)
            self._fsync_directory(directory_fd, "canonical:dir-fsync")
            self._assert_coordination_identity(directory_fd, state_fd)
            self._unlink(directory_fd, prepared_name, "prepared:unlink")
            self._fsync_directory(directory_fd, "prepared:cleanup-dir-fsync")
            self._assert_coordination_identity(directory_fd, state_fd)
            self._unlink(directory_fd, staging_name, "staging:unlink")
            self._fsync_directory(directory_fd, "cleanup:dir-fsync")
            self._assert_coordination_identity(directory_fd, state_fd)
            self._append_state(
                state_fd,
                transaction_id=transaction_id,
                digest=digest,
                state="COMMIT",
            )
            self._assert_coordination_identity(directory_fd, state_fd)
            result = self._canonical_eligibility(
                directory_fd,
                expected_payload=payload,
                state_records=self._state_records(state_fd),
            )
            if not result.eligible:
                raise EvidenceTransactionError(result.reason)
            return result
        except Exception as exc:
            if not transaction_started:
                if isinstance(exc, EvidenceTransactionError):
                    raise
                raise EvidenceTransactionError(
                    "evidence publication preflight failed"
                ) from exc
            try:
                self._assert_coordination_identity(directory_fd, state_fd)
            except (FileNotFoundError, OSError, EvidenceTransactionError):
                raise EvidenceTransactionError(
                    "coordination authority changed; transaction abandoned without "
                    "mutating unowned state"
                ) from exc
            reason = f"{type(exc).__name__}: publication indeterminate"
            tombstone_error: Exception | None = None
            owns_canonical = self._owns_canonical(
                directory_fd, transaction_id=transaction_id, digest=digest
            )
            if owns_canonical:
                try:
                    self._assert_coordination_identity(directory_fd, state_fd)
                    self._durable_tombstone(
                        directory_fd,
                        transaction_id=transaction_id,
                        digest=digest,
                        reason=reason,
                    )
                except Exception as failure:
                    tombstone_error = failure
            canonical_rollback_failed = False
            if tombstone_error is not None and canonical_created and owns_canonical:
                # Last-resort rollback.  If it also fails, never disguise the
                # indeterminate state as success.  Preserve the intrinsically
                # ineligible staging guard so eligibility and recovery reject
                # the otherwise complete canonical marker.
                try:
                    self._assert_coordination_identity(directory_fd, state_fd)
                    self._unlink(
                        directory_fd, CANONICAL_NAME, "failure:canonical-unlink"
                    )
                    self._fsync_directory(directory_fd, "failure:canonical-dir-fsync")
                except (FileNotFoundError, OSError, EvidenceTransactionError):
                    canonical_rollback_failed = True
            if not canonical_rollback_failed:
                for name, point in (
                    (prepared_name, "failure:prepared-unlink"),
                    (staging_name, "failure:staging-unlink"),
                ):
                    try:
                        self._assert_coordination_identity(directory_fd, state_fd)
                        self._unlink(directory_fd, name, point)
                    except (FileNotFoundError, OSError, EvidenceTransactionError):
                        pass
                try:
                    self._assert_coordination_identity(directory_fd, state_fd)
                    self._fsync_directory(directory_fd, "failure:cleanup-dir-fsync")
                except (OSError, EvidenceTransactionError):
                    pass
            if state_fd >= 0:
                try:
                    state_records = self._state_records(state_fd)
                    if state_records and state_records[-1]["state"] == "BEGIN":
                        self._assert_coordination_identity(directory_fd, state_fd)
                        self._append_state(
                            state_fd,
                            transaction_id=transaction_id,
                            digest=digest,
                            state="ABORT",
                        )
                except (OSError, EvidenceTransactionError):
                    # BEGIN is already durable and remains a permanent
                    # eligibility veto when ABORT cannot be recorded.
                    pass
            raise EvidenceTransactionError(
                "evidence publication failed or is indeterminate"
            ) from exc
        finally:
            if state_fd >= 0:
                os.close(state_fd)
            os.close(directory_fd)

    def recover(self) -> Eligibility:
        """Fail closed and remove stale intrinsically-ineligible transactions."""

        directory_fd = self._open_directory()
        state_fd = -1
        try:
            fcntl.flock(directory_fd, fcntl.LOCK_EX)
            state_fd = self._open_state(directory_fd)
            fcntl.flock(state_fd, fcntl.LOCK_EX)
            self._assert_coordination_identity(directory_fd, state_fd)
            records = self._state_records(state_fd)
            names = os.listdir(directory_fd)
            stale = sorted(
                name
                for name in names
                if name.startswith(".evidence-")
                and (name.endswith(".stage") or name.endswith(".prepared"))
            )
            if stale:
                canonical = self._canonical_eligibility(
                    directory_fd, state_records=records
                )
                digest = canonical.digest or "sha256:" + "0" * 64
                try:
                    self._durable_tombstone(
                        directory_fd,
                        transaction_id="recovery",
                        digest=digest,
                        reason="stale transaction recovered as NON_PASS",
                    )
                    self._assert_coordination_identity(directory_fd, state_fd)
                except EvidenceTransactionError:
                    # An unsafe pre-existing tombstone is itself a permanent
                    # eligibility veto.  Do not clean the staging guard until
                    # an operator can replace it through a trusted repair.
                    return self._canonical_eligibility(
                        directory_fd, state_records=self._state_records(state_fd)
                    )
                for name in stale:
                    try:
                        raw, metadata = self._read_name(directory_fd, name)
                        value = _strict_object(raw)
                        if not _safe_regular(metadata, self.expected_uid):
                            continue
                        valid_stage = (
                            name.endswith(".stage")
                            and value.get("schema") == STAGING_SCHEMA
                            and value.get("state") == "INELIGIBLE"
                        )
                        valid_prepared = (
                            name.endswith(".prepared")
                            and value.get("schema") == COMMIT_SCHEMA
                            and value.get("state") == "ELIGIBLE"
                        )
                        if not (valid_stage or valid_prepared):
                            continue
                        self._unlink(directory_fd, name, "recovery:staging-unlink")
                    except (FileNotFoundError, OSError, EvidenceTransactionError):
                        continue
                self._fsync_directory(directory_fd, "recovery:dir-fsync")
                self._assert_coordination_identity(directory_fd, state_fd)
            terminal = self._state_records(state_fd)
            if terminal and terminal[-1]["state"] == "BEGIN":
                self._append_state(
                    state_fd,
                    transaction_id=terminal[-1]["transaction_id"],
                    digest=terminal[-1]["evidence_digest"],
                    state="ABORT",
                    owner_pid=terminal[-1]["owner_pid"],
                )
            return self._canonical_eligibility(
                directory_fd, state_records=self._state_records(state_fd)
            )
        finally:
            if state_fd >= 0:
                os.close(state_fd)
            os.close(directory_fd)
