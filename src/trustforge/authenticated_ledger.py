"""Small, fail-closed authenticated append-only security ledger.

The production location is deliberately fixed.  Callers may select another
directory only by spelling ``test_directory_override=...``; this keeps a
convenient test seam from accidentally becoming a production configuration
surface.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import hmac
import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any, Mapping

PROTECTED_LEDGER_DIRECTORY = Path("/var/lib/trustforge/security-ledger")
LEDGER_FILENAME = "events.jsonl"
HEAD_FILENAME = "head.json"
EMERGENCY_STOP_FILENAME = "emergency-stop.json"
SCHEMA = "trustforge.authenticated-ledger/v1"
GENESIS_HASH = "0" * 64


class LedgerError(RuntimeError):
    """The ledger cannot be safely accessed or authenticated."""


class LedgerLimitError(LedgerError):
    """A configured storage bound would be exceeded."""


class NonceAlreadyConsumed(LedgerError):
    """A nonce already exists anywhere in this ledger."""


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LedgerError("event is not canonical JSON") from exc


class AuthenticatedLedger:
    """A bounded JSONL hash chain authenticated by a rotating HMAC keyring."""

    def __init__(
        self,
        *,
        keyring: Mapping[str, bytes],
        active_key_id: str,
        test_directory_override: str | os.PathLike[str] | None = None,
        max_file_bytes: int = 8 * 1024 * 1024,
        max_events: int = 10_000,
        max_event_bytes: int = 32 * 1024,
    ) -> None:
        if not keyring or active_key_id not in keyring:
            raise LedgerError("active key id is absent from keyring")
        if any(not isinstance(k, str) or not k or not isinstance(v, bytes) or len(v) < 32
               for k, v in keyring.items()):
            raise LedgerError("keyring requires named keys of at least 32 bytes")
        if min(max_file_bytes, max_events, max_event_bytes) <= 0:
            raise LedgerError("ledger bounds must be positive")
        self.directory = (
            Path(test_directory_override)
            if test_directory_override is not None
            else PROTECTED_LEDGER_DIRECTORY
        )
        self._keyring = dict(keyring)
        self._active_key_id = active_key_id
        self._max_file_bytes = max_file_bytes
        self._max_events = max_events
        self._max_event_bytes = max_event_bytes
        self._ensure_directory()

    def _ensure_directory(self) -> None:
        try:
            existed = self.directory.exists()
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = os.lstat(self.directory)
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise LedgerError("ledger directory is not a real directory")
            if info.st_uid != os.geteuid():
                raise LedgerError("ledger directory has the wrong owner")
            if existed and stat.S_IMODE(info.st_mode) != 0o700:
                raise LedgerError("existing ledger directory permissions are unsafe")
            if not existed:
                os.chmod(self.directory, 0o700)
        except OSError as exc:
            raise LedgerError("cannot secure ledger directory") from exc

    def _open_directory(self) -> int:
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            fd = os.open(self.directory, flags)
            info = os.fstat(fd)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise LedgerError("ledger directory permissions changed")
            return fd
        except OSError as exc:
            raise LedgerError("cannot safely open ledger directory") from exc

    def _open_file(self, directory_fd: int) -> int:
        existing_flags = os.O_RDWR | os.O_APPEND | os.O_CLOEXEC | os.O_NOFOLLOW
        create_flags = (
            os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        )
        try:
            try:
                fd = os.open(LEDGER_FILENAME, existing_flags, dir_fd=directory_fd)
            except FileNotFoundError:
                # On macOS O_NOFOLLOW|O_CREAT may return ENOENT for a missing
                # path. O_EXCL gives equivalent symlink-race protection while
                # creating; if another process wins, reopen with O_NOFOLLOW.
                try:
                    fd = os.open(
                        LEDGER_FILENAME,
                        create_flags,
                        0o600,
                        dir_fd=directory_fd,
                    )
                    os.fsync(directory_fd)
                except FileExistsError:
                    fd = os.open(
                        LEDGER_FILENAME,
                        existing_flags,
                        dir_fd=directory_fd,
                    )
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise LedgerError("ledger file has unsafe ownership or permissions")
            return fd
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.EMLINK):
                raise LedgerError("ledger file cannot be a symlink") from exc
            raise LedgerError("cannot safely open ledger file") from exc

    def _decode_and_verify(self, fd: int) -> list[dict[str, Any]]:
        size = os.fstat(fd).st_size
        if size > self._max_file_bytes:
            raise LedgerLimitError("ledger file exceeds size bound")
        os.lseek(fd, 0, os.SEEK_SET)
        raw = bytearray()
        while len(raw) <= self._max_file_bytes:
            chunk = os.read(fd, min(64 * 1024, self._max_file_bytes + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        if len(raw) != size or (raw and not raw.endswith(b"\n")):
            raise LedgerError("ledger is truncated or changed during read")
        records: list[dict[str, Any]] = []
        previous = GENESIS_HASH
        ledger_id: str | None = None
        seen_nonces: set[str] = set()
        for expected_sequence, line in enumerate(raw.splitlines(), start=1):
            if expected_sequence > self._max_events:
                raise LedgerLimitError("ledger event count exceeds bound")
            if len(line) > self._max_event_bytes:
                raise LedgerLimitError("ledger event exceeds size bound")
            try:
                record = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise LedgerError("ledger contains corrupt JSON") from exc
            if not isinstance(record, dict) or _canonical(record) != line:
                raise LedgerError("ledger contains non-canonical JSON")
            required = {
                "schema", "sequence", "previous_hash", "event_hash",
                "key_id", "hmac", "ledger_id", "event",
            }
            if set(record) != required or record["schema"] != SCHEMA:
                raise LedgerError("ledger record schema is invalid")
            if record["sequence"] != expected_sequence or record["previous_hash"] != previous:
                raise LedgerError("ledger sequence or hash chain is invalid")
            if (
                not isinstance(record["ledger_id"], str)
                or len(record["ledger_id"]) != 32
                or (ledger_id is not None and record["ledger_id"] != ledger_id)
            ):
                raise LedgerError("ledger identity is invalid")
            ledger_id = record["ledger_id"]
            event = record["event"]
            if not isinstance(event, dict):
                raise LedgerError("ledger event is invalid")
            key = self._keyring.get(record["key_id"])
            if key is None:
                raise LedgerError("ledger references an unknown key id")
            core = {
                "schema": SCHEMA,
                "sequence": expected_sequence,
                "previous_hash": previous,
                "key_id": record["key_id"],
                "ledger_id": ledger_id,
                "event": event,
            }
            event_hash = hashlib.sha256(_canonical(core)).hexdigest()
            if not hmac.compare_digest(str(record["event_hash"]), event_hash):
                raise LedgerError("ledger event hash is invalid")
            signature = hmac.new(
                key, _canonical({**core, "event_hash": event_hash}), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(str(record["hmac"]), signature):
                raise LedgerError("ledger authentication failed")
            nonce = event.get("nonce")
            if nonce is not None:
                if not isinstance(nonce, str) or not nonce or nonce in seen_nonces:
                    raise LedgerError("ledger contains an invalid or repeated nonce")
                seen_nonces.add(nonce)
            previous = event_hash
            records.append(record)
        return records

    def _verify_head(
        self, directory_fd: int, records: list[dict[str, Any]]
    ) -> str | None:
        """Bind the current tail so deleting complete final lines is detectable."""
        try:
            fd = os.open(
                HEAD_FILENAME, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
        except FileNotFoundError:
            if records:
                raise LedgerError("ledger head is missing")
            return None
        except OSError as exc:
            raise LedgerError("cannot safely open ledger head") from exc
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_size > self._max_event_bytes
            ):
                raise LedgerError("ledger head has unsafe ownership, permissions, or size")
            raw = os.read(fd, self._max_event_bytes + 1)
        finally:
            os.close(fd)
        try:
            head = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LedgerError("ledger head is corrupt") from exc
        if not isinstance(head, dict) or _canonical(head) + b"\n" != raw:
            raise LedgerError("ledger head is non-canonical")
        if set(head) != {"count", "event_hash", "key_id", "ledger_id", "hmac"}:
            raise LedgerError("ledger head schema is invalid")
        key = self._keyring.get(head["key_id"])
        unsigned = {
            name: head[name] for name in ("count", "event_hash", "key_id", "ledger_id")
        }
        expected_hash = records[-1]["event_hash"] if records else GENESIS_HASH
        if (
            key is None
            or not isinstance(head["ledger_id"], str)
            or len(head["ledger_id"]) != 32
            or head["count"] != len(records)
            or head["event_hash"] != expected_hash
            or (records and head["ledger_id"] != records[0]["ledger_id"])
            or not hmac.compare_digest(
                str(head["hmac"]),
                hmac.new(key, _canonical(unsigned), hashlib.sha256).hexdigest(),
            )
        ):
            raise LedgerError("ledger head does not match authenticated tail")
        return head["ledger_id"]

    def _write_head(
        self, directory_fd: int, count: int, event_hash: str, ledger_id: str
    ) -> None:
        unsigned = {
            "count": count,
            "event_hash": event_hash,
            "key_id": self._active_key_id,
            "ledger_id": ledger_id,
        }
        head = {
            **unsigned,
            "hmac": hmac.new(
                self._keyring[self._active_key_id], _canonical(unsigned), hashlib.sha256
            ).hexdigest(),
        }
        temporary = f".head-{os.getpid()}-{id(self)}.tmp"
        try:
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_fd,
            )
            try:
                data = _canonical(head) + b"\n"
                view = memoryview(data)
                while view:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise LedgerError("short ledger head write")
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            os.rename(
                temporary, HEAD_FILENAME,
                src_dir_fd=directory_fd, dst_dir_fd=directory_fd,
            )
        finally:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass

    def trip_emergency_stop(self, *, ledger_id: str, reason: str) -> None:
        """Durably create a one-way authenticated stop latch.

        The latch is separate from the event stream so a failed/corrupt stream
        append can still prevent any later B route.
        """
        if len(ledger_id) != 32 or reason not in {
            "candidate_outcome_unrecordable",
            "routing_state_conflict",
            "operator_emergency_stop",
        }:
            raise LedgerError("emergency stop identity or reason is invalid")
        unsigned = {
            "schema": "trustforge.emergency-stop/v1",
            "ledger_id": ledger_id,
            "reason": reason,
            "key_id": self._active_key_id,
        }
        payload = {
            **unsigned,
            "hmac": hmac.new(
                self._keyring[self._active_key_id],
                b"trustforge.emergency-stop.v1\x00" + _canonical(unsigned),
                hashlib.sha256,
            ).hexdigest(),
        }
        directory_fd = self._open_directory()
        try:
            try:
                fd = os.open(
                    EMERGENCY_STOP_FILENAME,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
                    0o600,
                    dir_fd=directory_fd,
                )
            except FileExistsError:
                if not self.emergency_stopped(ledger_id=ledger_id):
                    raise LedgerError("existing emergency latch is invalid")
                return
            try:
                encoded = _canonical(payload) + b"\n"
                view = memoryview(encoded)
                while view:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise LedgerError("short emergency latch write")
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def emergency_stopped(self, *, ledger_id: str) -> bool:
        directory_fd = self._open_directory()
        try:
            try:
                fd = os.open(
                    EMERGENCY_STOP_FILENAME,
                    os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                    dir_fd=directory_fd,
                )
            except FileNotFoundError:
                return False
            try:
                info = os.fstat(fd)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_size > self._max_event_bytes
                ):
                    raise LedgerError("emergency latch metadata is unsafe")
                raw = os.read(fd, self._max_event_bytes + 1)
            finally:
                os.close(fd)
        finally:
            os.close(directory_fd)
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LedgerError("emergency latch is corrupt") from exc
        if not isinstance(payload, dict) or _canonical(payload) + b"\n" != raw:
            raise LedgerError("emergency latch is non-canonical")
        if set(payload) != {"schema", "ledger_id", "reason", "key_id", "hmac"}:
            raise LedgerError("emergency latch schema is invalid")
        key = self._keyring.get(payload["key_id"])
        unsigned = {name: payload[name] for name in ("schema", "ledger_id", "reason", "key_id")}
        if (
            key is None
            or payload["schema"] != "trustforge.emergency-stop/v1"
            or payload["ledger_id"] != ledger_id
            or not hmac.compare_digest(
                str(payload["hmac"]),
                hmac.new(
                    key,
                    b"trustforge.emergency-stop.v1\x00" + _canonical(unsigned),
                    hashlib.sha256,
                ).hexdigest(),
            )
        ):
            raise LedgerError("emergency latch authentication failed")
        return True

    def read(self) -> list[dict[str, Any]]:
        """Return verified records.  No partial result is ever returned."""
        directory_fd = self._open_directory()
        fd = self._open_file(directory_fd)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
            records = self._decode_and_verify(fd)
            self._verify_head(directory_fd, records)
            return records
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            os.close(directory_fd)

    def append(
        self,
        event: Mapping[str, Any],
        *,
        expected_head: str | None = None,
    ) -> dict[str, Any]:
        """Authenticate and durably append one event under an exclusive lock."""
        event_copy = dict(event)
        event_bytes = _canonical(event_copy)
        if len(event_bytes) > self._max_event_bytes:
            raise LedgerLimitError("event exceeds size bound")
        nonce = event_copy.get("nonce")
        if nonce is not None and (not isinstance(nonce, str) or not nonce):
            raise LedgerError("nonce must be a non-empty string")

        directory_fd = self._open_directory()
        fd = self._open_file(directory_fd)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            records = self._decode_and_verify(fd)
            actual_head = records[-1]["event_hash"] if records else GENESIS_HASH
            if expected_head is not None and not hmac.compare_digest(
                expected_head, actual_head
            ):
                raise LedgerError("ledger head changed before conditional append")
            ledger_id = self._verify_head(directory_fd, records) or secrets.token_hex(16)
            if len(records) >= self._max_events:
                raise LedgerLimitError("ledger event count bound reached")
            if nonce is not None and any(r["event"].get("nonce") == nonce for r in records):
                raise NonceAlreadyConsumed("nonce was already consumed")
            sequence = len(records) + 1
            previous = records[-1]["event_hash"] if records else GENESIS_HASH
            core = {
                "schema": SCHEMA,
                "sequence": sequence,
                "previous_hash": previous,
                "key_id": self._active_key_id,
                "ledger_id": ledger_id,
                "event": event_copy,
            }
            event_hash = hashlib.sha256(_canonical(core)).hexdigest()
            record = {**core, "event_hash": event_hash}
            record["hmac"] = hmac.new(
                self._keyring[self._active_key_id], _canonical(record), hashlib.sha256
            ).hexdigest()
            line = _canonical(record) + b"\n"
            current_size = os.fstat(fd).st_size
            if current_size + len(line) > self._max_file_bytes:
                raise LedgerLimitError("ledger file size bound reached")
            view = memoryview(line)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise LedgerError("short ledger append")
                view = view[written:]
            os.fsync(fd)
            self._write_head(directory_fd, sequence, event_hash, ledger_id)
            os.fsync(directory_fd)
            return record
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            os.close(directory_fd)
