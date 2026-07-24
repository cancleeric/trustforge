"""Explicit compatibility adapters for the existing Hermes skill subsystem."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import stat
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from .safe_fs import SafePathError, pinned_directory, read_regular_file_at
from .upgrade_ports import (
    ActivationHandler,
    AuthenticatedPrincipal,
    ModuleCatalog,
    OperationDisplacedError,
    PointerChange,
    RollbackHandler,
    UpgradeCandidate,
    SandboxAttestation,
)


class PrincipalAuthority:
    """Authorize only server-authenticated, unexpired, scoped principals."""

    def require(
        self,
        principal: AuthenticatedPrincipal,
        action: str,
        *,
        tenant_id: str,
    ) -> str:
        if not isinstance(principal, AuthenticatedPrincipal):
            raise PermissionError("trusted authenticated principal is required")
        if principal.is_expired():
            raise PermissionError("authenticated principal has expired")
        if action not in principal.actions:
            raise PermissionError(f"principal lacks {action} action")
        if principal.tenant_id != tenant_id:
            raise PermissionError("cross-tenant upgrade mutation is forbidden")
        if not principal.subject.strip():
            raise PermissionError("authenticated principal subject is required")
        return principal.subject.strip()


class JournalCapacityError(SafePathError):
    pass


class LegacyJournalError(SafePathError):
    pass


class SandboxAttestationAuthority:
    """OS-protected append-only sandbox capability journal.

    Security boundary: the sandbox runner, Web process, and queue workers are
    trusted processes running as the same OS UID.  Mode 0600, no-follow opens,
    directory pinning, and advisory locks exclude other UIDs and path swaps;
    this is not a privilege boundary between mutually hostile same-UID code.
    """

    _MAX_BYTES = 8 * 1024 * 1024
    _MAX_FRAME_BYTES = 1024 * 1024
    _MAX_DISPOSITION_BYTES = 1024
    _RETENTION = timedelta(hours=24, seconds=300)

    def __init__(self, path: Path | None = None, *, clock=None):
        self.path = path or self.default_path()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.key_id = hashlib.sha256(
            str(self.path.resolve(strict=False)).encode()
        ).hexdigest()

    @staticmethod
    def default_path() -> Path:
        configured = os.getenv("TRUSTFORGE_SANDBOX_CAPABILITY_JOURNAL")
        if configured:
            return Path(configured)
        return (
            Path(__file__).resolve().parents[2]
            / "out"
            / "sandbox-capabilities-v4.jsonl"
        )

    @staticmethod
    def _attestation_payload(attestation: SandboxAttestation) -> dict[str, Any]:
        return {
            "db_identity": attestation.db_identity,
            "proposal_id": attestation.proposal_id,
            "candidate_family": attestation.candidate_family,
            "candidate_revision": attestation.candidate_revision,
            "artifact_hash": attestation.artifact_hash,
            "run_id": attestation.run_id,
            "runner_version": attestation.runner_version,
            "details_checksum": attestation.details_checksum,
            "passed": attestation.passed,
            "completed_at": attestation.completed_at.isoformat(),
            "details": attestation.details,
        }

    @contextmanager
    def _locked(self) -> Iterator[int]:
        with pinned_directory(self.path.parent, create=True) as parent_fd:
            name = self.path.name + ".lock"
            fd = os.open(
                name,
                os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=parent_fd,
            )
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
                    raise SafePathError("sandbox journal lock permissions are unsafe")
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield parent_fd
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def _read(self, parent_fd: int) -> dict[str, dict[str, Any]]:
        try:
            raw, info = read_regular_file_at(
                parent_fd, self.path.name, maximum_bytes=self._MAX_BYTES
            )
        except FileNotFoundError:
            return {}
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise SafePathError("sandbox capability journal permissions are unsafe")
        capabilities: dict[str, dict[str, Any]] = {}
        # O_APPEND writes can be cut short by process death.  A non-newline
        # terminated final frame was never durable and is ignored; every
        # complete frame, including its checksum, remains fail-closed.
        complete = raw
        if raw and not raw.endswith(b"\n"):
            complete = raw[: raw.rfind(b"\n") + 1] if b"\n" in raw else b""
        for number, line in enumerate(complete.splitlines(), 1):
            try:
                envelope = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SafePathError(
                    f"corrupt sandbox capability journal at line {number}"
                ) from exc
            if not isinstance(envelope, dict) or set(envelope) != {"record", "checksum"}:
                raise SafePathError("invalid sandbox capability envelope")
            record = envelope["record"]
            if (
                not isinstance(record, dict)
                or envelope["checksum"]
                != hashlib.sha256(_canonical(record)).hexdigest()
            ):
                raise SafePathError("sandbox capability checksum mismatch")
            if record.get("schema") == "trustforge.sandbox-capability/v2":
                raise LegacyJournalError(
                    "legacy sandbox journal v2 is preserved; use the v3 journal"
                )
            capability_id = record.get("capability_id")
            state = record.get("state")
            if not isinstance(capability_id, str) or state not in {
                "issued", "consumed", "rejected"
            }:
                raise SafePathError("invalid sandbox capability record")
            entry = capabilities.setdefault(capability_id, {})
            if state == "issued":
                if entry or set(record) != {
                    "schema", "state", "capability_id", "journal_id",
                    "issued_at", "attestation",
                }:
                    raise SafePathError("duplicate sandbox capability issue")
                if record["schema"] != "trustforge.sandbox-capability/v3":
                    raise SafePathError("unknown sandbox capability schema")
                try:
                    issued_at = datetime.fromisoformat(record["issued_at"])
                except (TypeError, ValueError) as exc:
                    raise SafePathError("invalid sandbox capability issued_at") from exc
                if issued_at.tzinfo is None:
                    raise SafePathError("sandbox capability issued_at must be aware")
                if record["journal_id"] != self.key_id:
                    raise SafePathError("sandbox capability journal identity mismatch")
                attestation = record["attestation"]
                if not isinstance(attestation, dict) or set(attestation) != {
                    "proposal_id", "candidate_family", "candidate_revision",
                    "artifact_hash", "run_id", "runner_version",
                    "details_checksum", "passed", "completed_at", "details",
                    "db_identity",
                }:
                    raise SafePathError("invalid sandbox capability attestation")
                if (
                    not all(isinstance(attestation[key], str) for key in (
                        "proposal_id", "candidate_family", "candidate_revision",
                        "artifact_hash", "run_id", "runner_version",
                        "details_checksum", "completed_at",
                        "db_identity",
                    ))
                    or type(attestation["passed"]) is not bool
                    or not isinstance(attestation["details"], dict)
                ):
                    raise SafePathError("invalid sandbox capability attestation fields")
                entry["issued"] = record
            else:
                if (
                    "issued" not in entry
                    or "consumed" in entry
                    or "rejected" in entry
                ):
                    raise SafePathError("out-of-order sandbox capability disposition")
                if set(record) != {
                    "schema", "state", "capability_id", "journal_id",
                    "issued_checksum", "operation_binding",
                    "db_identity", "db_digest",
                }:
                    raise SafePathError("invalid sandbox capability disposition")
                if (
                    record["schema"] != "trustforge.sandbox-capability/v3"
                    or record["journal_id"] != self.key_id
                    or not isinstance(record["operation_binding"], str)
                    or len(record["operation_binding"]) != 64
                ):
                    raise SafePathError("invalid sandbox capability disposition identity")
                if (
                    not isinstance(record["db_identity"], str)
                    or record["db_digest"]
                    != hashlib.sha256(record["db_identity"].encode()).hexdigest()
                ):
                    raise SafePathError("invalid sandbox disposition DB identity")
                issued_checksum = hashlib.sha256(
                    _canonical(entry["issued"])
                ).hexdigest()
                if record["issued_checksum"] != issued_checksum:
                    raise SafePathError("disposition does not bind issued capability")
                entry[state] = record
        return capabilities

    def _append(
        self,
        parent_fd: int,
        record: dict[str, Any],
        *,
        reservation: int = 0,
    ) -> None:
        envelope = {
            "record": record,
            "checksum": hashlib.sha256(_canonical(record)).hexdigest(),
        }
        encoded = _canonical(envelope) + b"\n"
        if len(encoded) > self._MAX_FRAME_BYTES:
            raise SafePathError("sandbox capability frame exceeds size limit")
        fd = os.open(
            self.path.name,
            os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_fd,
        )
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
                raise SafePathError("sandbox capability journal permissions are unsafe")
            if info.st_size:
                if info.st_size > self._MAX_BYTES:
                    raise SafePathError("sandbox capability journal exceeds size limit")
                journal = os.pread(fd, info.st_size, 0)
                durable_size = info.st_size
                if not journal.endswith(b"\n"):
                    newline = journal.rfind(b"\n")
                    durable_size = newline + 1 if newline >= 0 else 0
                if durable_size + len(encoded) + reservation > self._MAX_BYTES:
                    raise JournalCapacityError(
                        "sandbox capability journal exceeds size limit"
                    )
                if durable_size != info.st_size:
                    os.ftruncate(fd, durable_size)
                    os.fsync(fd)
            elif len(encoded) + reservation > self._MAX_BYTES:
                raise JournalCapacityError(
                    "sandbox capability journal exceeds size limit"
                )
            view = memoryview(encoded)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("short sandbox capability journal write")
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
            os.fsync(parent_fd)

    def _disposition_reservation(self, issued: dict[str, Any]) -> int:
        db_identity = str(issued["attestation"]["db_identity"])
        common = {
            "schema": "trustforge.sandbox-capability/v3",
            "capability_id": str(issued["capability_id"]),
            "journal_id": self.key_id,
            "issued_checksum": hashlib.sha256(_canonical(issued)).hexdigest(),
            "operation_binding": "0" * 64,
            "db_identity": db_identity,
            "db_digest": hashlib.sha256(db_identity.encode()).hexdigest(),
        }
        sizes = []
        for state in ("consumed", "rejected"):
            record = {**common, "state": state}
            envelope = {
                "record": record,
                "checksum": hashlib.sha256(_canonical(record)).hexdigest(),
            }
            sizes.append(len(_canonical(envelope)) + 1)
        return max(sizes)

    def issue(
        self,
        *,
        db_identity: str,
        proposal_id: str,
        candidate_family: str,
        candidate_revision: str,
        artifact_hash: str,
        run_id: str,
        runner_version: str,
        details_checksum: str,
        passed: bool,
        completed_at,
        details: dict[str, Any],
    ) -> SandboxAttestation:
        canonical_db = str(Path(db_identity).resolve(strict=False))
        if (
            not db_identity
            or db_identity != canonical_db
            or len(db_identity.encode("utf-8")) > 4096
        ):
            raise SafePathError("sandbox database identity is unsafe")
        completed_iso = completed_at.isoformat()
        seed = _canonical({
            "proposal_id": proposal_id,
            "run_id": run_id,
            "completed_at": completed_iso,
            "nonce": secrets.token_hex(16),
        })
        capability_id = hashlib.sha256(seed).hexdigest()
        attestation = SandboxAttestation(
            db_identity=db_identity,
            proposal_id=proposal_id,
            candidate_family=candidate_family,
            candidate_revision=candidate_revision,
            run_id=run_id,
            runner_version=runner_version,
            artifact_hash=artifact_hash,
            details_checksum=details_checksum,
            passed=passed,
            completed_at=completed_at,
            details=details,
            key_id=self.key_id,
            proof=capability_id,
        )
        record = {
            "schema": "trustforge.sandbox-capability/v3",
            "state": "issued",
            "capability_id": capability_id,
            "journal_id": self.key_id,
            "issued_at": self.clock().astimezone(timezone.utc).isoformat(),
            "attestation": self._attestation_payload(attestation),
        }
        with self._locked() as parent_fd:
            capabilities = self._read(parent_fd)
            if capability_id in capabilities:
                raise RuntimeError("sandbox capability id collision")
            reservation = sum(
                self._disposition_reservation(entry["issued"])
                for entry in capabilities.values()
                if "consumed" not in entry and "rejected" not in entry
            ) + self._disposition_reservation(record)
            self._append(
                parent_fd,
                record,
                reservation=reservation,
            )
        return attestation

    def resolve(self, capability_id: str) -> SandboxAttestation:
        with self._locked() as parent_fd:
            entry = self._read(parent_fd).get(capability_id)
            if entry is None:
                raise PermissionError("sandbox capability was not issued")
            payload = entry["issued"]["attestation"]
        return SandboxAttestation(
            **{
                **payload,
                "completed_at": datetime.fromisoformat(payload["completed_at"]),
                "key_id": self.key_id,
                "proof": capability_id,
            }
        )

    def compact(
        self,
        *,
        db_identity: str,
        exact_capabilities: dict[str, dict[str, Any]],
    ) -> int:
        """Crash-safely retain only records required by the lifecycle policy."""
        now = self.clock().astimezone(timezone.utc)
        with self._locked() as parent_fd:
            capabilities = self._read(parent_fd)
            retained: list[dict[str, Any]] = []
            for capability_id, entry in capabilities.items():
                issued = entry["issued"]
                issued_at = datetime.fromisoformat(issued["issued_at"]).astimezone(
                    timezone.utc
                )
                if now < issued_at:
                    raise SafePathError("sandbox journal clock moved backwards")
                age = now - issued_at
                disposition = entry.get("consumed") or entry.get("rejected")
                keep = False
                if disposition is None:
                    keep = age <= self._RETENTION
                elif disposition["db_identity"] != db_identity:
                    keep = True
                elif entry.get("rejected") is not None:
                    keep = age <= self._RETENTION
                elif capability_id not in exact_capabilities:
                    keep = True
                else:
                    exact = exact_capabilities[capability_id]
                    expected = {
                        "proposal_id": issued["attestation"]["proposal_id"],
                        "passed": issued["attestation"]["passed"],
                        "artifact_hash": issued["attestation"]["artifact_hash"],
                        "candidate_family": issued["attestation"][
                            "candidate_family"
                        ],
                        "candidate_revision": issued["attestation"][
                            "candidate_revision"
                        ],
                        "run_id": issued["attestation"]["run_id"],
                        "runner_version": issued["attestation"]["runner_version"],
                        "completed_at": issued["attestation"]["completed_at"],
                        "details_checksum": issued["attestation"]["details_checksum"],
                        "operation_binding": disposition["operation_binding"],
                        "journal_id": issued["journal_id"],
                        "db_identity": disposition["db_identity"],
                    }
                    if exact != expected:
                        raise SafePathError(
                            "sandbox DB row conflicts with journal identity"
                        )
                    keep = age <= self._RETENTION
                if keep:
                    retained.append(issued)
                    if disposition is not None:
                        retained.append(disposition)
            encoded = b"".join(
                _canonical({
                    "record": record,
                    "checksum": hashlib.sha256(_canonical(record)).hexdigest(),
                }) + b"\n"
                for record in retained
            )
            if len(encoded) > self._MAX_BYTES:
                raise JournalCapacityError(
                    "compacted sandbox journal exceeds size limit"
                )
            temporary = f".{self.path.name}.{secrets.token_hex(12)}.compact"
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=parent_fd,
            )
            try:
                view = memoryview(encoded)
                while view:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise OSError("short sandbox journal compaction write")
                    view = view[written:]
                os.fsync(fd)
            except BaseException:
                os.close(fd)
                try:
                    os.unlink(temporary, dir_fd=parent_fd)
                except OSError:
                    pass
                raise
            else:
                os.close(fd)
            try:
                os.replace(
                    temporary,
                    self.path.name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                )
                os.fsync(parent_fd)
            except BaseException:
                try:
                    os.unlink(temporary, dir_fd=parent_fd)
                except OSError:
                    pass
                raise
            return len(capabilities) - sum(
                1 for entry in capabilities.values()
                if entry["issued"] in retained
            )

    def _verified_entry(
        self,
        parent_fd: int,
        attestation: SandboxAttestation,
    ) -> tuple[
        dict[str, dict[str, Any]], dict[str, Any], dict[str, Any]
    ]:
        if attestation.key_id != self.key_id:
            raise PermissionError("sandbox attestation authority mismatch")
        capabilities = self._read(parent_fd)
        entry = capabilities.get(attestation.proof)
        if entry is None:
            raise PermissionError("sandbox capability was not issued")
        issued = entry["issued"]
        if issued["journal_id"] != self.key_id or issued["attestation"] != (
            self._attestation_payload(attestation)
        ):
            raise PermissionError("sandbox capability payload is invalid")
        return capabilities, entry, issued

    def _other_undisposed_reservation(
        self,
        capabilities: dict[str, dict[str, Any]],
        capability_id: str,
    ) -> int:
        return sum(
            self._disposition_reservation(entry["issued"])
            for current_id, entry in capabilities.items()
            if current_id != capability_id
            and "consumed" not in entry
            and "rejected" not in entry
        )

    def reject(
        self,
        attestation: SandboxAttestation,
        *,
        operation_binding: str,
        db_identity: str,
    ) -> None:
        """Permanently burn an authentic capability that failed eligibility."""
        if attestation.db_identity != db_identity:
            raise PermissionError("sandbox capability database mismatch")
        if (
            len(operation_binding) != 64
            or any(character not in "0123456789abcdef" for character in operation_binding)
        ):
            raise SafePathError("sandbox operation binding is invalid")
        with self._locked() as parent_fd:
            capabilities, entry, issued = self._verified_entry(
                parent_fd, attestation
            )
            if entry.get("consumed") is not None or entry.get("rejected") is not None:
                raise PermissionError("sandbox capability replay is forbidden")
            record = {
                "schema": "trustforge.sandbox-capability/v3",
                "state": "rejected",
                "capability_id": attestation.proof,
                "journal_id": self.key_id,
                "issued_checksum": hashlib.sha256(_canonical(issued)).hexdigest(),
                "operation_binding": operation_binding,
                "db_identity": db_identity,
                "db_digest": hashlib.sha256(db_identity.encode()).hexdigest(),
            }
            if self._disposition_reservation(issued) < len(
                _canonical({
                    "record": record,
                    "checksum": hashlib.sha256(_canonical(record)).hexdigest(),
                })
            ) + 1:
                raise SafePathError("sandbox disposition exceeds issued reservation")
            self._append(
                parent_fd,
                record,
                reservation=self._other_undisposed_reservation(
                    capabilities, attestation.proof
                ),
            )

    @contextmanager
    def consume(
        self,
        attestation: SandboxAttestation,
        *,
        already_persisted: bool,
        operation_binding: str,
        db_identity: str,
    ) -> Iterator[None]:
        if attestation.db_identity != db_identity:
            raise PermissionError("sandbox capability database mismatch")
        if (
            len(operation_binding) != 64
            or any(character not in "0123456789abcdef" for character in operation_binding)
        ):
            raise SafePathError("sandbox operation binding is invalid")
        with self._locked() as parent_fd:
            capabilities, entry, issued = self._verified_entry(
                parent_fd, attestation
            )
            if entry.get("rejected") is not None:
                raise PermissionError("sandbox capability was rejected")
            if entry.get("consumed") is not None:
                if entry["consumed"]["db_identity"] != db_identity:
                    raise PermissionError("sandbox capability database mismatch")
                if already_persisted:
                    if entry["consumed"]["operation_binding"] != operation_binding:
                        raise PermissionError(
                            "sandbox capability recovery binding mismatch"
                        )
                    yield
                    return
                if entry["consumed"]["operation_binding"] != operation_binding:
                    raise PermissionError("sandbox capability recovery binding mismatch")
                yield
                return
            if already_persisted:
                raise SafePathError("sandbox DB row exists for unconsumed capability")
            record = {
                "schema": "trustforge.sandbox-capability/v3",
                "state": "consumed",
                "capability_id": attestation.proof,
                "journal_id": self.key_id,
                "issued_checksum": hashlib.sha256(
                    _canonical(issued)
                ).hexdigest(),
                "operation_binding": operation_binding,
                "db_identity": db_identity,
                "db_digest": hashlib.sha256(db_identity.encode()).hexdigest(),
            }
            if self._disposition_reservation(issued) < len(
                _canonical({
                    "record": record,
                    "checksum": hashlib.sha256(_canonical(record)).hexdigest(),
                })
            ) + 1:
                raise SafePathError("sandbox disposition exceeds issued reservation")
            self._append(
                parent_fd,
                record,
                reservation=self._other_undisposed_reservation(
                    capabilities, attestation.proof
                ),
            )
            yield


class HermesModuleCatalog(ModuleCatalog):
    def resolve(self, family: str, revision: str, artifact_hash: str) -> UpgradeCandidate:
        from .skills import load_artifact, skill_id_for

        if artifact_hash != f"sha256:{revision}":
            raise ValueError("sandbox candidate identity is incomplete")
        artifact = load_artifact(family, revision)
        return UpgradeCandidate(
            family=family,
            revision=revision,
            artifact_hash=artifact_hash,
            artifact=artifact,
            module_id=skill_id_for(family),
        )


class HermesControlPlaneCatalog:
    def manifest(self):
        from .skills import run_skill_manifest
        return run_skill_manifest()

    def history(self):
        from .skill_changes import change_history
        return change_history()


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


class _HermesReceiptStore:
    _MAX_RECEIPT_BYTES = 4 * 1024 * 1024

    def __init__(
        self,
        log_path: Path | None,
        receipt_path: Path | None,
        after_receipt: Callable[[PointerChange], None] | None,
        after_pointer: Callable[[PointerChange], None] | None,
    ):
        if log_path is None:
            from .skill_changes import default_log_path
            log_path = default_log_path()
        self.log_path = log_path
        self.receipt_path = receipt_path or log_path.with_suffix(
            log_path.suffix + ".upgrade-receipts.jsonl"
        )
        if self.receipt_path.parent.resolve(strict=False) != self.log_path.parent.resolve(
            strict=False
        ):
            raise ValueError("receipt and Hermes change log must share one lock directory")
        self.after_receipt = after_receipt
        self.after_pointer = after_pointer

    @contextmanager
    def _locked(self) -> Iterator[int]:
        with pinned_directory(self.receipt_path.parent, create=True) as parent_fd:
            name = self.log_path.name + ".upgrade-operation.lock"
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            # macOS can transiently surface ENOENT when two dir-fd callers
            # create the same new entry concurrently.  Re-resolve only inside
            # the already pinned directory; other failures remain fatal.
            for attempt in range(3):
                try:
                    fd = os.open(name, flags, 0o600, dir_fd=parent_fd)
                    break
                except FileNotFoundError:
                    if attempt == 2:
                        raise
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise SafePathError("upgrade receipt lock is not a regular file")
                fcntl.flock(fd, fcntl.LOCK_EX)
                yield parent_fd
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def _read(self, parent_fd: int) -> dict[str, dict[str, Any]]:
        try:
            raw, _ = read_regular_file_at(
                parent_fd,
                self.receipt_path.name,
                maximum_bytes=self._MAX_RECEIPT_BYTES,
            )
        except FileNotFoundError:
            return {}
        journals: dict[str, dict[str, Any]] = {}
        for line_number, line in enumerate(raw.splitlines(), 1):
            try:
                envelope = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SafePathError(
                    f"corrupt upgrade receipt at line {line_number}"
                ) from exc
            if not isinstance(envelope, dict) or set(envelope) != {"record", "checksum"}:
                raise SafePathError(f"invalid upgrade receipt at line {line_number}")
            record = envelope["record"]
            if not isinstance(record, dict):
                raise SafePathError(f"invalid upgrade receipt at line {line_number}")
            common = {
                "schema",
                "state",
                "operation_id",
                "request_hash",
                "action",
                "family",
                "target_revision",
                "previous_revision",
                "module_id",
                "handler_log_identity",
                "operation_reason",
            }
            state = record.get("state")
            required = common | (
                {"prepared_checksum", "pointer_event_id", "pointer_entry_checksum"}
                if state == "completed"
                else set()
            )
            if (
                state not in {"prepared", "completed"}
                or set(record) != required
                or record.get("schema")
                != "trustforge.hermes-operation-journal/v2"
            ):
                raise SafePathError(f"invalid upgrade receipt schema at line {line_number}")
            for key in (
                "operation_id",
                "request_hash",
                "action",
                "family",
                "target_revision",
                "module_id",
                "handler_log_identity",
            ):
                if not isinstance(record.get(key), str) or not record[key]:
                    raise SafePathError(f"invalid upgrade receipt field at line {line_number}")
            if record["previous_revision"] is not None and not isinstance(
                record["previous_revision"], str
            ):
                raise SafePathError(f"invalid upgrade receipt field at line {line_number}")
            if record["operation_reason"] is not None and not isinstance(
                record["operation_reason"], str
            ):
                raise SafePathError(f"invalid upgrade receipt field at line {line_number}")
            if len(record["request_hash"]) != 64:
                raise SafePathError(f"invalid upgrade receipt digest at line {line_number}")
            checksum = hashlib.sha256(_canonical(record)).hexdigest()
            if envelope["checksum"] != checksum:
                raise SafePathError(f"upgrade receipt checksum mismatch at line {line_number}")
            operation_id = str(record["operation_id"])
            journal = journals.setdefault(operation_id, {})
            if state == "prepared":
                if journal:
                    raise SafePathError("duplicate or out-of-order prepared receipt")
                journal["prepared"] = record
                journal["prepared_checksum"] = checksum
            else:
                prepared = journal.get("prepared")
                if prepared is None or journal.get("completed") is not None:
                    raise SafePathError("duplicate or out-of-order completed receipt")
                if record["prepared_checksum"] != journal["prepared_checksum"]:
                    raise SafePathError("completed receipt does not bind prepared receipt")
                for key in common - {"state"}:
                    if record[key] != prepared[key]:
                        raise SafePathError("completed receipt changed prepared identity")
                if not isinstance(record["pointer_event_id"], str) or not record[
                    "pointer_event_id"
                ]:
                    raise SafePathError("invalid completed receipt pointer event")
                if (
                    not isinstance(record["pointer_entry_checksum"], str)
                    or len(record["pointer_entry_checksum"]) != 64
                ):
                    raise SafePathError("invalid completed receipt entry checksum")
                journal["completed"] = record
        return journals

    def _append(self, parent_fd: int, record: dict[str, Any]) -> str:
        checksum = hashlib.sha256(_canonical(record)).hexdigest()
        envelope = {
            "record": record,
            "checksum": checksum,
        }
        encoded = _canonical(envelope) + b"\n"
        flags = (
            os.O_WRONLY
            | os.O_APPEND
            | os.O_CREAT
            | getattr(os, "O_NOFOLLOW", 0)
        )
        fd = os.open(self.receipt_path.name, flags, 0o600, dir_fd=parent_fd)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise SafePathError("upgrade receipt is not a regular file")
            view = memoryview(encoded)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("short upgrade receipt write")
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(parent_fd)
        return checksum

    def _read_change_log_strict(self) -> tuple[bytes, list[dict[str, Any]]]:
        with pinned_directory(self.log_path.parent, create=True) as parent_fd:
            try:
                raw, _ = read_regular_file_at(parent_fd, self.log_path.name)
            except FileNotFoundError:
                raw = b""
            else:
                fd = os.open(
                    self.log_path.name,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=parent_fd,
                )
                try:
                    if not stat.S_ISREG(os.fstat(fd).st_mode):
                        raise SafePathError("Hermes change log is not a regular file")
                    os.fsync(fd)
                finally:
                    os.close(fd)
        history: list[dict[str, Any]] = []
        for line_number, line in enumerate(raw.splitlines(), 1):
            try:
                value = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SafePathError(
                    f"corrupt Hermes change log at line {line_number}"
                ) from exc
            if not isinstance(value, dict):
                raise SafePathError(
                    f"invalid Hermes change log at line {line_number}"
                )
            history.append(value)
        return raw, history

    @staticmethod
    def _pointer(record: dict[str, Any]) -> PointerChange:
        return PointerChange(
            family=str(record["family"]),
            revision=str(record["target_revision"]),
            previous_revision=(
                None
                if record.get("previous_revision") is None
                else str(record["previous_revision"])
            ),
            operation_id=str(record["operation_id"]),
        )

    def _prepare(
        self,
        parent_fd: int,
        *,
        operation_id: str,
        request_hash: str,
        action: str,
        family: str,
        target_revision: str,
        previous_revision: str | None,
        module_id: str,
        operation_reason: str | None,
    ) -> tuple[dict[str, Any], str]:
        record = {
            "schema": "trustforge.hermes-operation-journal/v2",
            "state": "prepared",
            "operation_id": operation_id,
            "request_hash": request_hash,
            "action": action,
            "family": family,
            "target_revision": target_revision,
            "previous_revision": previous_revision,
            "module_id": module_id,
            "handler_log_identity": str(self.log_path.resolve(strict=False)),
            "operation_reason": operation_reason,
        }
        return record, self._append(parent_fd, record)

    def _complete(
        self,
        parent_fd: int,
        prepared: dict[str, Any],
        prepared_checksum: str,
        pointer_event: dict[str, Any],
    ) -> PointerChange:
        event_id = pointer_event.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise SafePathError("pointer event has no durable identity")
        record = {
            **prepared,
            "state": "completed",
            "prepared_checksum": prepared_checksum,
            "pointer_event_id": event_id,
            "pointer_entry_checksum": hashlib.sha256(
                _canonical(pointer_event)
            ).hexdigest(),
        }
        self._append(parent_fd, record)
        durable = self._pointer(record)
        if self.after_receipt is not None:
            self.after_receipt(durable)
        return durable

    @staticmethod
    def _rollback_reason(reason: str, operation_id: str) -> str:
        return f"{reason} [trustforge-operation:{operation_id}]"

    def _find_operation_event(
        self,
        prepared: dict[str, Any],
        history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        action = prepared["action"]
        for event in reversed(history):
            if (
                event.get("skill_id") != prepared["module_id"]
                or event.get("skill_hash") != prepared["target_revision"]
            ):
                continue
            if action == "activate" and (
                event.get("action") == "approved"
                and isinstance(event.get("evidence"), dict)
                and event["evidence"].get("operation_id") == prepared["operation_id"]
            ):
                return event
            if action == "rollback" and (
                event.get("action") == "rolled_back"
                and event.get("reason")
                == self._rollback_reason(
                    str(prepared["operation_reason"]), str(prepared["operation_id"])
                )
            ):
                return event
        raise OperationDisplacedError("operation-specific pointer lineage is missing")

    def _validate_completed(
        self,
        completed: dict[str, Any],
        history: list[dict[str, Any]],
    ) -> PointerChange:
        from .skill_changes import active_revision

        if completed["handler_log_identity"] != str(
            self.log_path.resolve(strict=False)
        ):
            raise OperationDisplacedError("operation log identity changed")
        event = next(
            (
                row
                for row in history
                if row.get("event_id") == completed["pointer_event_id"]
            ),
            None,
        )
        if event is None or hashlib.sha256(_canonical(event)).hexdigest() != completed[
            "pointer_entry_checksum"
        ]:
            raise OperationDisplacedError("operation-specific pointer entry changed")
        latest = next(
            (
                row
                for row in reversed(history)
                if row.get("skill_id") == completed["module_id"]
                and row.get("action") in {"approved", "rolled_back"}
            ),
            None,
        )
        if latest is None or latest.get("event_id") != completed["pointer_event_id"]:
            raise OperationDisplacedError("completed operation pointer was displaced")
        current = active_revision(completed["module_id"], history)
        if current != completed["target_revision"]:
            raise OperationDisplacedError("completed operation target is not active")
        return self._pointer(completed)

    def _validate_recovery_event(
        self,
        prepared: dict[str, Any],
        event: dict[str, Any],
        history: list[dict[str, Any]],
    ) -> None:
        from .skill_changes import active_revision

        latest = next(
            (
                row
                for row in reversed(history)
                if row.get("skill_id") == prepared["module_id"]
                and row.get("action") in {"approved", "rolled_back"}
            ),
            None,
        )
        if latest is None or latest.get("event_id") != event.get("event_id"):
            raise OperationDisplacedError(
                "prepared operation pointer was displaced before completion"
            )
        if active_revision(prepared["module_id"], history) != prepared[
            "target_revision"
        ]:
            raise OperationDisplacedError(
                "prepared operation target is not active"
            )


class HermesActivationHandler(_HermesReceiptStore, ActivationHandler):
    def __init__(
        self,
        log_path: Path | None = None,
        *,
        receipt_path: Path | None = None,
        after_receipt: Callable[[PointerChange], None] | None = None,
        after_pointer: Callable[[PointerChange], None] | None = None,
    ):
        super().__init__(log_path, receipt_path, after_receipt, after_pointer)

    def current_revision(self, family: str) -> str | None:
        from .skill_changes import active_revision
        from .skills import skill_id_for

        with self._locked() as parent_fd:
            self._read(parent_fd)
            _, history = self._read_change_log_strict()
            # Reading through active_revision is safe while every adapter
            # pointer mutation is serialized by this same log-derived lock.
            return active_revision(skill_id_for(family), history)

    def activate(
        self,
        candidate: UpgradeCandidate,
        *,
        proposal_id: str,
        operation_id: str,
        expected_revision: str | None,
    ) -> PointerChange:
        from .skill_changes import active_revision, approve, stage
        from .skills import canonical_json

        request = {
            "action": "activate",
            "proposal_id": proposal_id,
            "family": candidate.family,
            "revision": candidate.revision,
            "artifact_hash": candidate.artifact_hash,
            "module_id": candidate.module_id,
            "log_identity": str(self.log_path.resolve(strict=False)),
        }
        request_hash = hashlib.sha256(_canonical(request)).hexdigest()
        with self._locked() as parent_fd:
            journals = self._read(parent_fd)
            journal = journals.get(operation_id)
            if journal is not None and journal["prepared"]["request_hash"] != request_hash:
                raise RuntimeError("operation_id payload conflict")
            _, history = self._read_change_log_strict()
            if journal is not None and journal.get("completed") is not None:
                return self._validate_completed(journal["completed"], history)
            current = active_revision(candidate.module_id, history)
            if journal is None:
                if current != expected_revision:
                    raise RuntimeError("activation compare-and-swap failed")
                prepared, prepared_checksum = self._prepare(
                    parent_fd,
                    operation_id=operation_id,
                    request_hash=request_hash,
                    action="activate",
                    family=candidate.family,
                    target_revision=candidate.revision,
                    previous_revision=current,
                    module_id=candidate.module_id,
                    operation_reason=None,
                )
            else:
                prepared = journal["prepared"]
                prepared_checksum = journal["prepared_checksum"]
                previous = prepared["previous_revision"]
                if current == candidate.revision:
                    event = self._find_operation_event(prepared, history)
                    self._validate_recovery_event(prepared, event, history)
                    return self._complete(
                        parent_fd, prepared, prepared_checksum, event
                    )
                if current != previous:
                    raise RuntimeError("prepared activation pointer is unrecoverable")
            staged = any(
                row.get("action") == "staged"
                and row.get("skill_id") == candidate.module_id
                and row.get("skill_hash") == candidate.revision
                for row in history
            )
            if not staged:
                stage(
                    candidate.module_id,
                    canonical_json(candidate.artifact),
                    f"Hermes proposal {proposal_id}",
                    log_path=self.log_path,
                )
            event = approve(
                candidate.module_id,
                candidate.revision,
                {
                    "proposal_id": proposal_id,
                    "artifact_hash": candidate.artifact_hash,
                    "operation_id": operation_id,
                },
                log_path=self.log_path,
            )
            pointer = PointerChange(
                candidate.family,
                candidate.revision,
                prepared["previous_revision"],
                operation_id,
            )
            if self.after_pointer is not None:
                self.after_pointer(pointer)
            return self._complete(
                parent_fd, prepared, prepared_checksum, event
            )


class HermesRollbackHandler(_HermesReceiptStore, RollbackHandler):
    def __init__(
        self,
        log_path: Path | None = None,
        *,
        receipt_path: Path | None = None,
        after_receipt: Callable[[PointerChange], None] | None = None,
        after_pointer: Callable[[PointerChange], None] | None = None,
    ):
        super().__init__(log_path, receipt_path, after_receipt, after_pointer)

    def rollback(
        self,
        family: str,
        target_revision: str,
        *,
        reason: str,
        operation_id: str,
        expected_revision: str,
    ) -> PointerChange:
        from .skill_changes import active_revision, rollback
        from .skills import skill_id_for

        module_id = skill_id_for(family)
        request = {
            "action": "rollback",
            "family": family,
            "target_revision": target_revision,
            "expected_revision": expected_revision,
            "module_id": module_id,
            "log_identity": str(self.log_path.resolve(strict=False)),
        }
        request_hash = hashlib.sha256(_canonical(request)).hexdigest()
        with self._locked() as parent_fd:
            journals = self._read(parent_fd)
            journal = journals.get(operation_id)
            if journal is not None and journal["prepared"]["request_hash"] != request_hash:
                raise RuntimeError("operation_id payload conflict")
            _, history = self._read_change_log_strict()
            if journal is not None and journal.get("completed") is not None:
                return self._validate_completed(journal["completed"], history)
            current = active_revision(module_id, history)
            if journal is None:
                if current != expected_revision:
                    raise RuntimeError("rollback compare-and-swap failed")
                prepared, prepared_checksum = self._prepare(
                    parent_fd,
                    operation_id=operation_id,
                    request_hash=request_hash,
                    action="rollback",
                    family=family,
                    target_revision=target_revision,
                    previous_revision=current,
                    module_id=module_id,
                    operation_reason=reason,
                )
            else:
                prepared = journal["prepared"]
                prepared_checksum = journal["prepared_checksum"]
                previous = prepared["previous_revision"]
                if current == target_revision:
                    event = self._find_operation_event(prepared, history)
                    self._validate_recovery_event(prepared, event, history)
                    return self._complete(
                        parent_fd, prepared, prepared_checksum, event
                    )
                if current != previous:
                    raise RuntimeError("prepared rollback pointer is unrecoverable")
            event = rollback(
                module_id,
                target_revision,
                self._rollback_reason(
                    str(prepared["operation_reason"]), operation_id
                ),
                log_path=self.log_path,
            )
            pointer = PointerChange(
                family, target_revision, prepared["previous_revision"], operation_id
            )
            if self.after_pointer is not None:
                self.after_pointer(pointer)
            return self._complete(
                parent_fd, prepared, prepared_checksum, event
            )
