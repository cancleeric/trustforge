"""Append-only storage compatibility helpers for learning events.

This is not a database adapter.  It is a small contract layer used by migration
and persistence implementations to prove that canonical events stay immutable
and replayable before any storage backend is introduced.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

import portalocker

from .learning_event_contract import (
    LearningEvent,
    LearningEventError,
    assert_append_only,
    deserialize_learning_event,
    serialize_learning_event,
)
from .safe_fs import (
    SafePathError,
    pinned_directory,
    read_regular_file_at,
    write_immutable_cross_directory_at,
)


DEFAULT_MAXIMUM_EVENT_BYTES = 1024 * 1024
DEFAULT_MAXIMUM_EVENT_COUNT = 10_000
DEFAULT_MAXIMUM_TOTAL_BYTES = 256 * 1024 * 1024
DEFAULT_LOCK_TIMEOUT_SECONDS = 10.0
DEFAULT_MAXIMUM_STAGING_ENTRIES = 100
_STAGING_NAME = re.compile(r"^\.[0-9a-f]{64}\.json\.[0-9a-f]{24}\.tmp$")


def default_learning_event_directory() -> Path:
    """Return the portable, local-only learning-event directory."""

    home = Path(os.getenv("TRUSTFORGE_HOME", str(Path(__file__).resolve().parents[2])))
    return home / "out" / "learning_events"


class FileLearningEventStore:
    """Immutable canonical learning events persisted as one file per identity."""

    def __init__(
        self,
        directory: Path | None = None,
        *,
        maximum_event_bytes: int = DEFAULT_MAXIMUM_EVENT_BYTES,
        maximum_event_count: int = DEFAULT_MAXIMUM_EVENT_COUNT,
        maximum_total_bytes: int = DEFAULT_MAXIMUM_TOTAL_BYTES,
        lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
        maximum_staging_entries: int = DEFAULT_MAXIMUM_STAGING_ENTRIES,
    ) -> None:
        for name, value in (
            ("maximum_event_bytes", maximum_event_bytes),
            ("maximum_event_count", maximum_event_count),
            ("maximum_total_bytes", maximum_total_bytes),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if (
            isinstance(lock_timeout_seconds, bool)
            or not isinstance(lock_timeout_seconds, (int, float))
            or lock_timeout_seconds <= 0
        ):
            raise ValueError("lock_timeout_seconds must be positive")
        if (
            isinstance(maximum_staging_entries, bool)
            or not isinstance(maximum_staging_entries, int)
            or maximum_staging_entries < 1
        ):
            raise ValueError("maximum_staging_entries must be a positive integer")
        self.directory = Path(directory) if directory is not None else default_learning_event_directory()
        self.maximum_event_bytes = maximum_event_bytes
        self.maximum_event_count = maximum_event_count
        self.maximum_total_bytes = maximum_total_bytes
        self.lock_timeout_seconds = float(lock_timeout_seconds)
        self.maximum_staging_entries = maximum_staging_entries
        self.staging_directory = self.directory.parent / f".{self.directory.name}.staging"
        self.control_directory = self.directory.parent / f".{self.directory.name}.control"

    def append(self, event: LearningEvent) -> str:
        encoded = serialize_learning_event(event).encode("utf-8")
        if len(encoded) > self.maximum_event_bytes:
            raise LearningEventError("learning event exceeds size limit")
        name = self._path_for_identity(event.identity).name
        with self._store_lock(exclusive=True):
            with pinned_directory(self.directory, create=True) as parent_fd:
                with pinned_directory(self.staging_directory, create=True) as staging_fd:
                    self._cleanup_staging(staging_fd, parent_fd)
                    try:
                        write_immutable_cross_directory_at(staging_fd, parent_fd, name, encoded)
                        return "created"
                    except FileExistsError:
                        current = self._read_event_at(parent_fd, name)
                        if serialize_learning_event(current).encode("utf-8") == encoded:
                            try:
                                os.fsync(parent_fd)
                            except OSError as exc:
                                raise LearningEventError(
                                    "idempotent learning event could not be made durable"
                                ) from exc
                            return "idempotent"
                        assert_append_only(current, event)
                        raise LearningEventError("learning event append failed")

    def replay(self, *, trusted_tenant_id: str) -> list[LearningEvent]:
        self._validate_trusted_tenant_id(trusted_tenant_id)
        with self._store_lock(exclusive=False):
            try:
                return self._replay_existing(trusted_tenant_id)
            except FileNotFoundError:
                return []
            except SafePathError as exc:
                if isinstance(exc.__cause__, FileNotFoundError):
                    return []
                raise LearningEventError("learning event store cannot be opened safely") from exc

    def _replay_existing(self, trusted_tenant_id: str) -> list[LearningEvent]:
        with pinned_directory(self.directory) as parent_fd:
            try:
                os.fsync(parent_fd)
            except OSError as exc:
                raise LearningEventError("learning event store is not durably readable") from exc
            try:
                bounded_entries: list[tuple[str, int]] = []
                total_size = 0
                with os.scandir(parent_fd) as entries:
                    for entry in entries:
                        if len(bounded_entries) >= self.maximum_event_count:
                            raise LearningEventError("learning event store exceeds event count limit")
                        name = entry.name
                        if not name.endswith(".json") or len(name) != 69:
                            raise LearningEventError("learning event store contains an unexpected entry")
                        info = entry.stat(follow_symlinks=False)
                        if not stat.S_ISREG(info.st_mode):
                            raise LearningEventError("learning event file is unsafe or unreadable")
                        total_size += info.st_size
                        if total_size > self.maximum_total_bytes:
                            raise LearningEventError("learning event store exceeds total size limit")
                        bounded_entries.append((name, info.st_size))
            except OSError as exc:
                raise LearningEventError("learning event store cannot be listed safely") from exc
            events: list[LearningEvent] = []
            actual_total = 0
            for name, _ in sorted(bounded_entries):
                event = self._read_event_at(parent_fd, name)
                actual_total += len(serialize_learning_event(event).encode("utf-8"))
                if actual_total > self.maximum_total_bytes:
                    raise LearningEventError("learning event store exceeds total size limit")
                if name != self._path_for_identity(event.identity).name:
                    raise LearningEventError("learning event filename digest does not match identity")
                if event.tenant_id == trusted_tenant_id:
                    events.append(event)
            return events

    def snapshot(self, *, trusted_tenant_id: str) -> tuple[str, ...]:
        return tuple(
            serialize_learning_event(event)
            for event in self.replay(trusted_tenant_id=trusted_tenant_id)
        )

    def _read_event_at(self, parent_fd: int, name: str) -> LearningEvent:
        try:
            encoded, _ = read_regular_file_at(
                parent_fd,
                name,
                maximum_bytes=self.maximum_event_bytes,
            )
        except (OSError, SafePathError) as exc:
            raise LearningEventError("learning event file is unsafe or unreadable") from exc
        return self._decode_event(encoded)

    @staticmethod
    def _decode_event(encoded: bytes) -> LearningEvent:
        try:
            event = deserialize_learning_event(encoded)
        except LearningEventError as exc:
            raise LearningEventError("learning event file is corrupt") from exc
        if serialize_learning_event(event).encode("utf-8") != encoded:
            raise LearningEventError("learning event file is not canonical")
        return event

    def _path_for_identity(self, identity: str) -> Path:
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return self.directory / f"{digest}.json"

    @staticmethod
    def _validate_trusted_tenant_id(trusted_tenant_id: str) -> None:
        if not isinstance(trusted_tenant_id, str) or not trusted_tenant_id.strip():
            raise LearningEventError("trusted_tenant_id is required")

    def _cleanup_staging(self, staging_fd: int, destination_fd: int) -> None:
        entries_to_clean: list[tuple[str, str | None]] = []
        with os.scandir(staging_fd) as entries:
            for entry in entries:
                if len(entries_to_clean) >= self.maximum_staging_entries:
                    raise LearningEventError("learning event staging exceeds cleanup limit")
                if not _STAGING_NAME.fullmatch(entry.name):
                    raise LearningEventError("learning event staging contains an unexpected entry")
                info = entry.stat(follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink not in {1, 2}:
                    raise LearningEventError("learning event staging contains an unsafe entry")
                destination_name: str | None = None
                if info.st_nlink == 2:
                    destination_name = entry.name[1:70]
                    try:
                        destination_info = os.stat(
                            destination_name,
                            dir_fd=destination_fd,
                            follow_symlinks=False,
                        )
                    except OSError as exc:
                        raise LearningEventError(
                            "learning event staging link cannot be reconciled"
                        ) from exc
                    if (
                        not stat.S_ISREG(destination_info.st_mode)
                        or (destination_info.st_dev, destination_info.st_ino)
                        != (info.st_dev, info.st_ino)
                    ):
                        raise LearningEventError(
                            "learning event staging link cannot be reconciled"
                        )
                entries_to_clean.append((entry.name, destination_name))
        if entries_to_clean:
            destination_changed = False
            for name, destination_name in entries_to_clean:
                if destination_name is not None:
                    os.unlink(destination_name, dir_fd=destination_fd)
                    destination_changed = True
                os.unlink(name, dir_fd=staging_fd)
            os.fsync(staging_fd)
            if destination_changed:
                os.fsync(destination_fd)

    @contextmanager
    def _store_lock(self, *, exclusive: bool) -> Iterator[None]:
        context = pinned_directory(self.control_directory, create=True)
        try:
            control_fd = context.__enter__()
        except (OSError, SafePathError) as exc:
            raise LearningEventError("learning event store lock path is unsafe") from exc
        try:
            descriptor = self._open_lock_file(control_fd)
            try:
                os.fsync(control_fd)
            except OSError as exc:
                os.close(descriptor)
                raise LearningEventError(
                    "learning event store lock path is not durable"
                ) from exc
            file_object = os.fdopen(descriptor, "a+b", buffering=0)
            acquired = False
            flags = (
                portalocker.LockFlags.EXCLUSIVE
                if exclusive
                else portalocker.LockFlags.SHARED
            ) | portalocker.LockFlags.NON_BLOCKING
            deadline = time.monotonic() + self.lock_timeout_seconds
            try:
                while True:
                    try:
                        portalocker.lock(file_object, flags)
                        acquired = True
                        break
                    except portalocker.exceptions.LockException as exc:
                        if time.monotonic() >= deadline:
                            raise LearningEventError(
                                "learning event store lock timed out"
                            ) from exc
                        time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
                    except (NotImplementedError, OSError) as exc:
                        raise LearningEventError(
                            "learning event store lock mode is unavailable"
                        ) from exc
                yield
            finally:
                if acquired:
                    try:
                        portalocker.unlock(file_object)
                    finally:
                        file_object.close()
                else:
                    file_object.close()
        finally:
            context.__exit__(None, None, None)

    @staticmethod
    def _open_lock_file(control_fd: int) -> int:
        flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(
                "store.lock",
                flags | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=control_fd,
            )
        except FileExistsError:
            try:
                descriptor = os.open("store.lock", flags, dir_fd=control_fd)
            except OSError as exc:
                raise LearningEventError("learning event store lock file is unsafe") from exc
        except OSError as exc:
            raise LearningEventError("learning event store lock file is unsafe") from exc
        try:
            info = os.fstat(descriptor)
        except OSError as exc:
            os.close(descriptor)
            raise LearningEventError("learning event store lock file is unsafe") from exc
        unsafe_mode = os.name != "nt" and bool(stat.S_IMODE(info.st_mode) & 0o022)
        wrong_owner = os.name != "nt" and info.st_uid != os.getuid()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or unsafe_mode
            or wrong_owner
        ):
            os.close(descriptor)
            raise LearningEventError("learning event store lock file is unsafe")
        return descriptor


@dataclass
class LearningEventAppendLog:
    _events: dict[str, str] = field(default_factory=dict)

    def append(self, event: LearningEvent) -> str:
        encoded = serialize_learning_event(event)
        current = self._events.get(event.identity)
        if current is None:
            self._events[event.identity] = encoded
            return "created"
        if current == encoded:
            return "idempotent"
        assert_append_only(deserialize_learning_event(current), event)
        raise LearningEventError("learning event append failed")

    def replay(self) -> list[LearningEvent]:
        return [deserialize_learning_event(raw) for raw in self._events.values()]

    def snapshot(self) -> tuple[str, ...]:
        return tuple(self._events[key] for key in sorted(self._events))


def plan_learning_event_migration(raw_events: Iterable[str | bytes], *, dry_run: bool = True) -> dict[str, Any]:
    """Validate raw canonical events and return a fail-closed migration plan."""

    append_log = LearningEventAppendLog()
    results: list[dict[str, str]] = []
    for raw in raw_events:
        try:
            event = deserialize_learning_event(raw)
            outcome = append_log.append(event)
        except LearningEventError as exc:
            return {
                "status": "blocked",
                "dry_run": dry_run,
                "reason": str(exc),
                "events_validated": len(results),
                "will_write": False,
            }
        results.append({"identity": event.identity, "kind": event.kind, "result": outcome})
    return {
        "status": "ready" if dry_run else "requires_backend_transaction",
        "dry_run": dry_run,
        "events_validated": len(results),
        "will_write": not dry_run,
        "results": results,
        "snapshot": append_log.snapshot(),
    }
