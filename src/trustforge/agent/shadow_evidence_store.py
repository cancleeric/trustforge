"""Standalone, append-only SQLite evidence for shadow release evaluation.

This module deliberately has no imports from, or hooks into, the agent
orchestrator.  A store failure therefore fails evidence collection closed
without changing the active release path.

Security boundary: this POSIX store requires a dedicated owner-only directory.
The owning OS uid is trusted; hostile same-uid processes are out of scope
because they can bypass advisory locks, mutate files, and inspect this process.
Within that boundary, untrusted other users, symlinks, unsafe permissions,
accidental replacements, concurrent trusted workers, and corrupt evidence fail
closed.  We intentionally do not claim that pathname checks can make stdlib
SQLite's WAL VFS safe from a malicious process running as the same uid.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import tempfile
import threading
import time
import weakref
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, TypeVar
from urllib.parse import urlparse

import fcntl

from trustforge.agent.shadow_contracts import (
    CONTRACT_VERSION,
    ShadowAggregate,
    ShadowBlocker,
    ShadowContractError,
    ShadowDecision,
    ShadowDecisionAction,
    ShadowInput,
    ShadowObservation,
    ShadowPolicy,
    ShadowReleaseIdentity,
    canonical_json,
    evaluate_shadow,
    observation_digest,
    policy_digest,
    to_dict,
)

SCHEMA_VERSION = 2
APPLICATION_ID = 0x54534653  # "TSFS"
DEFAULT_MAX_ROWS = 10_000
DEFAULT_MAX_DB_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_QUERY_ROWS = 10_000
_TEMP_WAL_INDEX_ALLOWANCE = 1024 * 1024
_EVENT_DOMAIN = b"trustforge.shadow.store.event.v1\x00"
_ROOT_DOMAIN = b"trustforge.shadow.store.root.v1\x00"
_RECEIPT_DOMAIN = b"trustforge.shadow.store.receipt.v1\x00"
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_T = TypeVar("_T")
_FORK_STORES: weakref.WeakSet[ShadowEvidenceStore] = weakref.WeakSet()


def _after_fork_child() -> None:
    for store in list(_FORK_STORES):
        store._after_fork_child()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork_child)


class ShadowEvidenceStoreError(RuntimeError):
    """Evidence cannot be proven durable and trustworthy."""


@dataclass(frozen=True, slots=True)
class StoredEvaluation:
    aggregate_event_id: str
    decision_event_id: str
    observation_root_digest: str
    decision: ShadowDecision


@dataclass(frozen=True, slots=True)
class ReadOnlyShadowEvaluation:
    """Deterministic evaluation derived without mutating the evidence ledger."""

    aggregate_event_id: str
    decision_event_id: str
    observation_root_digest: str
    ordered_observation_event_ids: tuple[str, ...]
    observations: tuple[ShadowObservation, ...]
    decision: ShadowDecision


@dataclass(frozen=True, slots=True)
class CanonicalShadowObservation:
    """One fully authenticated persisted observation and its stable event id."""

    event_id: str
    recorded_at: str
    completion_recorded_at: str
    observation: ShadowObservation


def _sha256(domain: bytes, value: Any) -> str:
    return "sha256:" + hashlib.sha256(domain + canonical_json(value)).hexdigest()


def _event_id(kind: str, digest: str) -> str:
    return _sha256(_EVENT_DOMAIN, {"kind": kind, "digest": digest})


def _identity_tuple(identity: ShadowReleaseIdentity) -> tuple[str, ...]:
    return (
        identity.active_release,
        identity.candidate_release,
        identity.active_artifact_digest,
        identity.candidate_artifact_digest,
        identity.policy_digest,
        identity.contract_version,
    )


def _identity_from(payload: dict[str, Any]) -> ShadowReleaseIdentity:
    return ShadowReleaseIdentity(**payload)


def _observation_from(payload: dict[str, Any]) -> ShadowObservation:
    value = dict(payload)
    value["release_identity"] = _identity_from(value["release_identity"])
    value["canonical_input"] = ShadowInput(**value["canonical_input"])
    value["claim_ids"] = tuple(value.get("claim_ids", ()))
    return ShadowObservation(**value)


def _aggregate_from(payload: dict[str, Any]) -> ShadowAggregate:
    value = dict(payload)
    value["release_identity"] = _identity_from(value["release_identity"])
    value["blockers"] = tuple(ShadowBlocker(item) for item in value["blockers"])
    return ShadowAggregate(**value)


def _decision_from(payload: dict[str, Any]) -> ShadowDecision:
    value = dict(payload)
    value["release_identity"] = _identity_from(value["release_identity"])
    value["aggregate"] = _aggregate_from(value["aggregate"])
    value["action"] = ShadowDecisionAction(value["action"])
    return ShadowDecision(**value)


def _utc_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ShadowEvidenceStoreError("stored timestamp is malformed") from exc
    if parsed.tzinfo is None:
        raise ShadowEvidenceStoreError("stored timestamp lacks timezone")
    return parsed.astimezone(timezone.utc)


class ShadowEvidenceStore:
    """A bounded v1 evidence ledger.

    Every public operation opens its own connection.  This avoids inherited
    connections across forks and lets SQLite coordinate independent workers.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        max_rows: int = DEFAULT_MAX_ROWS,
        max_db_bytes: int = DEFAULT_MAX_DB_BYTES,
        max_query_rows: int = DEFAULT_MAX_QUERY_ROWS,
        busy_timeout_ms: int = 1_000,
        read_only: bool = False,
    ) -> None:
        # Establish destructor-safe lifecycle state before any validation can
        # raise.  Invalid paths and limits still create a partial Python object
        # whose __del__ may run.
        self._directory_lock_fd = -1
        self._creator_pid = os.getpid()
        self._thread_lock = threading.RLock()
        self._closed = False
        self._parent_identity: tuple[int, int] | None = None
        self._database_identity: tuple[int, int] | None = None
        configured = path if path is not None else os.environ.get("TRUSTFORGE_SHADOW_DB_PATH")
        if not configured:
            raise ShadowEvidenceStoreError("TRUSTFORGE_SHADOW_DB_PATH is required")
        self.path = Path(configured)
        if not self.path.is_absolute():
            raise ShadowEvidenceStoreError("shadow database path must be absolute")
        if min(max_rows, max_db_bytes, max_query_rows, busy_timeout_ms) <= 0:
            raise ShadowEvidenceStoreError("store limits must be positive")
        if max_query_rows > max_rows:
            raise ShadowEvidenceStoreError("query limit cannot exceed row limit")
        self.max_rows = max_rows
        self.max_db_bytes = max_db_bytes
        self.max_query_rows = max_query_rows
        self.busy_timeout_ms = busy_timeout_ms
        self.read_only = read_only
        if os.name != "posix":
            raise ShadowEvidenceStoreError("shadow evidence store requires POSIX file locking")
        self._prepare_path()
        _FORK_STORES.add(self)
        self._initialize()

    def _prepare_path(self) -> None:
        parent = self.path.parent
        try:
            if self.read_only and not parent.exists():
                raise ShadowEvidenceStoreError("shadow database parent does not exist")
            if not self.read_only:
                parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if parent.is_symlink() or self.path.is_symlink():
                raise ShadowEvidenceStoreError("shadow database path cannot use symlinks")
            resolved_parent = parent.resolve(strict=True)
            if resolved_parent != parent:
                raise ShadowEvidenceStoreError("shadow database parent must be canonical")
            parent_mode = stat.S_IMODE(parent.stat().st_mode)
            if parent_mode & 0o077:
                raise ShadowEvidenceStoreError("shadow database parent is accessible by group/other")
            if self.path.exists():
                mode = self.path.lstat().st_mode
                if not stat.S_ISREG(mode) or stat.S_IMODE(mode) & 0o077:
                    raise ShadowEvidenceStoreError("shadow database must be a private regular file")
            elif self.read_only:
                raise ShadowEvidenceStoreError("shadow database does not exist")
            else:
                flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                descriptor = os.open(self.path, flags, 0o600)
                os.close(descriptor)
            for suffix in ("-wal", "-shm"):
                sidecar = Path(f"{self.path}{suffix}")
                if sidecar.is_symlink():
                    raise ShadowEvidenceStoreError("shadow database sidecars cannot be symlinks")
            self._parent_identity = (parent.stat().st_dev, parent.stat().st_ino)
            database = self.path.lstat()
            self._database_identity = (database.st_dev, database.st_ino)
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            if hasattr(os, "O_DIRECTORY"):
                flags |= os.O_DIRECTORY
            descriptor = os.open(parent, flags)
            try:
                bound = os.fstat(descriptor)
                if (
                    not stat.S_ISDIR(bound.st_mode)
                    or (bound.st_dev, bound.st_ino) != self._parent_identity
                ):
                    raise ShadowEvidenceStoreError(
                        "lock fd is not bound to database directory"
                    )
            except Exception:
                os.close(descriptor)
                raise
            self._directory_lock_fd = descriptor
        except OSError as exc:
            raise ShadowEvidenceStoreError("shadow database path is unsafe") from exc

    @staticmethod
    def _open_bound(path: Path) -> tuple[int, os.stat_result]:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            return descriptor, os.fstat(descriptor)
        except Exception:
            os.close(descriptor)
            raise

    def _verify_file_bindings(self) -> tuple[list[int], dict[str, tuple[int, int]]]:
        descriptors: list[int] = []
        sidecar_identities: dict[str, tuple[int, int]] = {}
        try:
            parent_fd, parent_stat = self._open_bound(self.path.parent)
            descriptors.append(parent_fd)
            if (
                not stat.S_ISDIR(parent_stat.st_mode)
                or stat.S_IMODE(parent_stat.st_mode) & 0o077
                or (parent_stat.st_dev, parent_stat.st_ino) != self._parent_identity
            ):
                raise ShadowEvidenceStoreError("shadow database parent identity changed")
            if parent_stat.st_uid != os.geteuid():
                raise ShadowEvidenceStoreError("shadow database parent owner changed")
            database_fd, database_stat = self._open_bound(self.path)
            descriptors.append(database_fd)
            if (
                not stat.S_ISREG(database_stat.st_mode)
                or (database_stat.st_dev, database_stat.st_ino) != self._database_identity
                or database_stat.st_uid != os.geteuid()
            ):
                raise ShadowEvidenceStoreError("shadow database identity changed")
            if not self.read_only:
                os.fchmod(database_fd, 0o600)
            for suffix in ("-wal", "-shm"):
                sidecar = Path(f"{self.path}{suffix}")
                if sidecar.exists() or sidecar.is_symlink():
                    try:
                        sidecar_fd, sidecar_stat = self._open_bound(sidecar)
                    except FileNotFoundError:
                        # SQLite may remove an unused WAL/SHM between lstat and
                        # open.  Absence is safe; any replacement is checked on
                        # the post-connect binding pass.
                        continue
                    descriptors.append(sidecar_fd)
                    if (
                        not stat.S_ISREG(sidecar_stat.st_mode)
                        or stat.S_IMODE(sidecar_stat.st_mode) & 0o077
                    ):
                        raise ShadowEvidenceStoreError("shadow database sidecar is unsafe")
                    if sidecar_stat.st_uid != os.geteuid():
                        raise ShadowEvidenceStoreError("shadow database sidecar owner is unsafe")
                    sidecar_identities[suffix] = (sidecar_stat.st_dev, sidecar_stat.st_ino)
            return descriptors, sidecar_identities
        except (OSError, ShadowEvidenceStoreError) as exc:
            for descriptor in descriptors:
                os.close(descriptor)
            if isinstance(exc, ShadowEvidenceStoreError):
                raise
            raise ShadowEvidenceStoreError("shadow database file binding failed") from exc

    @staticmethod
    def _close_descriptors(descriptors: list[int]) -> None:
        for descriptor in descriptors:
            os.close(descriptor)

    def _connect(self) -> sqlite3.Connection:
        if self.read_only:
            raise ShadowEvidenceStoreError(
                "read-only store must query an isolated filesystem snapshot"
            )
        before, before_sidecars = self._verify_file_bindings()
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                str(self.path),
                timeout=self.busy_timeout_ms / 1000,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA wal_autocheckpoint=100")
            journal = connection.execute("PRAGMA journal_mode").fetchone()[0]
            if str(journal).lower() != "wal":
                journal = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if str(journal).lower() != "wal":
                connection.close()
                raise ShadowEvidenceStoreError("WAL journal mode is unavailable")
            after, after_sidecars = self._verify_file_bindings()
            # WAL and SHM are lifecycle files: SQLite may legitimately replace
            # them while concurrent connections open/close.  We bind and
            # validate both snapshots (nofollow, regular, private owner/mode)
            # rather than requiring an inode to survive across sqlite3.connect.
            # The parent and primary DB identities remain immutable above.
            _ = before_sidecars, after_sidecars
            self._close_descriptors(after)
            return connection
        except Exception as exc:
            if connection is not None:
                connection.close()
            if isinstance(exc, ShadowEvidenceStoreError):
                raise
            raise ShadowEvidenceStoreError("shadow database cannot be opened") from exc
        finally:
            self._close_descriptors(before)

    def _bounded_source_sizes(self) -> dict[str, int]:
        """Validate DB/WAL/SHM and enforce one aggregate source-size cap."""
        sizes: dict[str, int] = {}
        total = 0
        for suffix in ("", "-wal", "-shm"):
            path = Path(f"{self.path}{suffix}")
            if not path.exists():
                if not suffix:
                    raise ShadowEvidenceStoreError(
                        "shadow snapshot database is missing"
                    )
                continue
            try:
                value = path.lstat()
            except OSError as exc:
                raise ShadowEvidenceStoreError(
                    "shadow snapshot source size cannot be verified"
                ) from exc
            if (
                not stat.S_ISREG(value.st_mode)
                or value.st_uid != os.geteuid()
                or stat.S_IMODE(value.st_mode) & 0o077
            ):
                raise ShadowEvidenceStoreError(
                    "shadow snapshot source is unsafe"
                )
            sizes[suffix] = value.st_size
            total += value.st_size
            if total > self.max_db_bytes:
                raise ShadowEvidenceStoreError(
                    "combined shadow DB/WAL/SHM size limit exceeded"
                )
        # A private SQLite reader may checkpoint WAL pages into its disposable
        # DB while retaining the WAL until close.  Reserve one additional WAL
        # length plus a bounded WAL-index allowance up front so temporary
        # storage cannot silently approach twice the configured cap.
        if "-wal" in sizes:
            projected_temporary_bytes = (
                total
                + sizes["-wal"]
                + max(sizes.get("-shm", 0), _TEMP_WAL_INDEX_ALLOWANCE)
            )
            if projected_temporary_bytes > self.max_db_bytes:
                raise ShadowEvidenceStoreError(
                    "private shadow snapshot projection exceeds size limit"
                )
        return sizes

    @staticmethod
    def _copy_stable_file(
        source: Path, destination: Path, *, expected_size: int,
    ) -> None:
        """Stream one verified source into a private file with bounded memory."""
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        source_fd = -1
        destination_fd = -1
        try:
            source_fd = os.open(source, flags)
            before = os.fstat(source_fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or stat.S_IMODE(before.st_mode) & 0o077
                or before.st_size != expected_size
            ):
                raise ShadowEvidenceStoreError(
                    "shadow snapshot source changed before copy"
                )
            destination_flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            if hasattr(os, "O_NOFOLLOW"):
                destination_flags |= os.O_NOFOLLOW
            destination_fd = os.open(destination, destination_flags, 0o600)
            total = 0
            while True:
                chunk = os.read(source_fd, 65_536)
                if not chunk:
                    break
                total += len(chunk)
                if total > expected_size:
                    raise ShadowEvidenceStoreError(
                        "shadow snapshot source grew during copy"
                    )
                view = memoryview(chunk)
                written = 0
                while written < len(view):
                    count = os.write(destination_fd, view[written:])
                    if count <= 0:
                        raise ShadowEvidenceStoreError(
                            "private shadow snapshot write failed"
                        )
                    written += count
            os.fsync(destination_fd)
            after = os.fstat(source_fd)
            stable_fields = (
                "st_dev", "st_ino", "st_mode", "st_uid", "st_size",
                "st_mtime_ns", "st_ctime_ns",
            )
            if (
                tuple(getattr(before, name) for name in stable_fields)
                != tuple(getattr(after, name) for name in stable_fields)
                or total != before.st_size
            ):
                raise ShadowEvidenceStoreError(
                    "shadow snapshot source changed during copy"
                )
        except OSError as exc:
            raise ShadowEvidenceStoreError(
                "shadow snapshot source cannot be copied"
            ) from exc
        finally:
            if destination_fd >= 0:
                os.close(destination_fd)
            if source_fd >= 0:
                os.close(source_fd)

    @contextmanager
    def _read_only_snapshot_connection(self):
        """Query a private DB/WAL copy so SQLite never opens source files.

        The cooperative directory lock held by callers prevents trusted store
        writers from changing DB/WAL while they are copied.  SHM is deliberately
        not copied: SQLite creates any required shared-memory index beside the
        disposable private copy.
        """
        bindings, _ = self._verify_file_bindings()
        self._close_descriptors(bindings)
        sizes = self._bounded_source_sizes()
        connection: sqlite3.Connection | None = None
        with tempfile.TemporaryDirectory(
            prefix="trustforge-shadow-health-",
        ) as directory:
            snapshot = Path(directory) / "evidence.sqlite3"
            self._copy_stable_file(
                self.path, snapshot, expected_size=sizes[""],
            )
            if "-wal" in sizes:
                self._copy_stable_file(
                    Path(f"{self.path}-wal"),
                    Path(f"{snapshot}-wal"),
                    expected_size=sizes["-wal"],
                )
            # Revalidate every source binding and the aggregate cap after the
            # streaming copy.  SHM is counted but never copied or opened.
            bindings, _ = self._verify_file_bindings()
            self._close_descriptors(bindings)
            if self._bounded_source_sizes() != sizes:
                raise ShadowEvidenceStoreError(
                    "shadow snapshot source set changed during copy"
                )
            try:
                connection = sqlite3.connect(
                    snapshot,
                    timeout=self.busy_timeout_ms / 1000,
                    isolation_level=None,
                )
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA query_only=ON")
                connection.execute("PRAGMA foreign_keys=ON")
                yield connection
            except sqlite3.Error as exc:
                raise ShadowEvidenceStoreError(
                    "private shadow snapshot cannot be queried"
                ) from exc
            finally:
                if connection is not None:
                    connection.close()

    def _initialize(self) -> None:
        with self._process_lock():
            self._initialize_locked()

    @contextmanager
    def _process_lock(self):
        with self._thread_lock:
            self._ensure_process_fd()
            descriptor = self._directory_lock_fd
            try:
                lock_stat = os.fstat(descriptor)
                if (
                    lock_stat.st_uid != os.geteuid()
                    or not stat.S_ISDIR(lock_stat.st_mode)
                    or stat.S_IMODE(lock_stat.st_mode) & 0o077
                    or (lock_stat.st_dev, lock_stat.st_ino) != self._parent_identity
                ):
                    raise ShadowEvidenceStoreError("shadow database lock is unsafe")
                deadline = time.monotonic() + self.busy_timeout_ms / 1000
                while True:
                    try:
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError as exc:
                        if time.monotonic() >= deadline:
                            raise ShadowEvidenceStoreError(
                                "shadow database process lock unavailable"
                            ) from exc
                        time.sleep(0.01)
                try:
                    yield
                finally:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError as exc:
                raise ShadowEvidenceStoreError("shadow database process lock failed") from exc

    def _after_fork_child(self) -> None:
        self._thread_lock = threading.RLock()
        if self._directory_lock_fd >= 0:
            os.close(self._directory_lock_fd)
        self._directory_lock_fd = -1
        self._creator_pid = os.getpid()

    def _ensure_process_fd(self) -> None:
        if self._closed:
            raise ShadowEvidenceStoreError("shadow evidence store is closed")
        current_pid = os.getpid()
        if self._creator_pid != current_pid:
            self._after_fork_child()
        if self._directory_lock_fd < 0:
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            if hasattr(os, "O_DIRECTORY"):
                flags |= os.O_DIRECTORY
            descriptor = -1
            try:
                descriptor = os.open(self.path.parent, flags)
                value = os.fstat(descriptor)
            except OSError as exc:
                if descriptor >= 0:
                    os.close(descriptor)
                raise ShadowEvidenceStoreError("shadow database lock fd cannot reopen") from exc
            if (
                not stat.S_ISDIR(value.st_mode)
                or value.st_uid != os.geteuid()
                or stat.S_IMODE(value.st_mode) & 0o077
                or (value.st_dev, value.st_ino) != self._parent_identity
            ):
                os.close(descriptor)
                raise ShadowEvidenceStoreError("reopened database lock fd is unsafe")
            self._directory_lock_fd = descriptor
            self._creator_pid = current_pid

    def close(self) -> None:
        lock = getattr(self, "_thread_lock", None)
        if lock is None:
            descriptor = getattr(self, "_directory_lock_fd", -1)
            if descriptor >= 0:
                os.close(descriptor)
                self._directory_lock_fd = -1
            self._closed = True
            return
        with lock:
            if self._directory_lock_fd >= 0:
                os.close(self._directory_lock_fd)
                self._directory_lock_fd = -1
            self._closed = True

    def __del__(self) -> None:
        try:
            self.close()
        except OSError:
            pass

    def _initialize_locked(self) -> None:
        if self.read_only:
            with self._read_only_snapshot_connection() as connection:
                application_id = connection.execute(
                    "PRAGMA application_id"
                ).fetchone()[0]
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                has_tables = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1"
                ).fetchone()
                if (
                    not has_tables
                    or application_id != APPLICATION_ID
                    or version != SCHEMA_VERSION
                ):
                    raise ShadowEvidenceStoreError(
                        "read-only health requires an existing v2 shadow database"
                    )
                self._verify_schema(connection)
            return
        connection = self._connect()
        try:
            application_id = connection.execute("PRAGMA application_id").fetchone()[0]
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            has_tables = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1"
            ).fetchone()
            if has_tables and application_id == APPLICATION_ID and version == 1:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    if _schema_fingerprint(connection) != _expected_v1_schema_fingerprint():
                        raise ShadowEvidenceStoreError(
                            "legacy v1 schema fingerprint is invalid"
                        )
                    connection.execute(_OBSERVATION_COMPLETION_SCHEMA)
                    for statement in _OBSERVATION_COMPLETION_TRIGGER_STATEMENTS:
                        connection.execute(statement)
                    connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                    connection.execute(
                        "INSERT INTO schema_migrations("
                        "version, contract_version, applied_at) VALUES (?, ?, "
                        "strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
                        (SCHEMA_VERSION, CONTRACT_VERSION),
                    )
                    connection.execute("COMMIT")
                    version = SCHEMA_VERSION
                except Exception:
                    connection.execute("ROLLBACK")
                    raise
            if has_tables and (
                application_id != APPLICATION_ID or version != SCHEMA_VERSION
            ):
                raise ShadowEvidenceStoreError("unknown or legacy shadow database schema")
            if not has_tables:
                connection.executescript(_SCHEMA)
                connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                connection.execute(
                    "INSERT INTO schema_migrations(version, contract_version, applied_at) "
                    "VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
                    (SCHEMA_VERSION, CONTRACT_VERSION),
                )
                connection.executescript(_IMMUTABILITY_TRIGGERS)
            self._verify_schema(connection)
        except (sqlite3.Error, ShadowContractError) as exc:
            if isinstance(exc, ShadowEvidenceStoreError):
                raise
            raise ShadowEvidenceStoreError("shadow database initialization failed") from exc
        finally:
            connection.close()

    @staticmethod
    def _verify_schema(connection: sqlite3.Connection) -> None:
        integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
        if integrity != "ok":
            raise ShadowEvidenceStoreError("shadow database integrity check failed")
        expected = {
            "schema_migrations", "policies", "observations", "aggregates",
            "decisions", "retention_receipts", "observation_completions",
        }
        actual = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if actual != expected:
            raise ShadowEvidenceStoreError("shadow database schema does not exactly match v2")
        expected_triggers = {
            f"immutable_{table}_{operation}"
            for table in expected
            for operation in ("update", "delete")
        }
        actual_triggers = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }
        if actual_triggers != expected_triggers:
            raise ShadowEvidenceStoreError("shadow database immutability guards are missing")
        if _schema_fingerprint(connection) != _expected_schema_fingerprint():
            raise ShadowEvidenceStoreError("shadow database canonical schema differs from v2")

    def _storage_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            path = Path(f"{self.path}{suffix}")
            if path.exists() or path.is_symlink():
                try:
                    value = path.lstat()
                except OSError as exc:
                    raise ShadowEvidenceStoreError("shadow database size cannot be verified") from exc
                if stat.S_ISLNK(value.st_mode) or not stat.S_ISREG(value.st_mode):
                    raise ShadowEvidenceStoreError("shadow database storage is unsafe")
                total += value.st_size
        return total

    def _transaction(
        self,
        operation: Callable[[sqlite3.Connection], _T],
        *,
        commit_guard: Callable[[], bool] | None = None,
    ) -> _T:
        with self._process_lock():
            if self.read_only:
                raise ShadowEvidenceStoreError("read-only shadow store rejects writes")
            if commit_guard is None:
                return self._transaction_locked(operation)
            return self._transaction_locked(operation, commit_guard=commit_guard)

    def _transaction_locked(
        self,
        operation: Callable[[sqlite3.Connection], _T],
        *,
        commit_guard: Callable[[], bool] | None = None,
    ) -> _T:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._verify_schema(connection)
            if self._storage_bytes() > self.max_db_bytes:
                raise ShadowEvidenceStoreError("shadow database size limit exceeded")
            result = operation(connection)
            page_count = connection.execute("PRAGMA page_count").fetchone()[0]
            page_size = connection.execute("PRAGMA page_size").fetchone()[0]
            if page_count * page_size > self.max_db_bytes:
                raise ShadowEvidenceStoreError("shadow database size limit exceeded")
            if self._storage_bytes() > self.max_db_bytes:
                raise ShadowEvidenceStoreError("shadow database WAL/SHM size limit exceeded")
            bindings, _ = self._verify_file_bindings()
            self._close_descriptors(bindings)
            if commit_guard is not None and not commit_guard():
                raise ShadowEvidenceStoreError(
                    "shadow evidence transaction cancelled before commit"
                )
            connection.execute("COMMIT")
            return result
        except (
            sqlite3.Error, OSError, ShadowContractError, ShadowEvidenceStoreError,
            ValueError, TypeError, KeyError,
        ) as exc:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            if isinstance(exc, ShadowEvidenceStoreError):
                raise
            raise ShadowEvidenceStoreError("shadow evidence transaction failed closed") from exc
        finally:
            connection.close()

    def _read_transaction(
        self, operation: Callable[[sqlite3.Connection], _T],
    ) -> _T:
        """Run a bounded ledger read without schema migration or event writes."""
        with self._process_lock():
            with self._read_only_snapshot_connection() as connection:
                try:
                    connection.execute("BEGIN")
                    self._verify_schema(connection)
                    result = operation(connection)
                    connection.execute("ROLLBACK")
                    return result
                except (
                    sqlite3.Error, OSError, ShadowContractError,
                    ShadowEvidenceStoreError, ValueError, TypeError, KeyError,
                ) as exc:
                    try:
                        connection.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                    if isinstance(exc, ShadowEvidenceStoreError):
                        raise
                    raise ShadowEvidenceStoreError(
                        "shadow evidence read failed closed"
                    ) from exc

    def _insert_event(
        self,
        connection: sqlite3.Connection,
        table: str,
        event_id: str,
        payload: dict[str, Any],
        identity: ShadowReleaseIdentity,
        extra: tuple[Any, ...] = (),
    ) -> bool:
        encoded = canonical_json(payload)
        digest = _sha256(_EVENT_DOMAIN, payload)
        existing = connection.execute(
            f"SELECT payload, payload_digest FROM {table} WHERE event_id=?", (event_id,)
        ).fetchone()
        if existing:
            if bytes(existing["payload"]) == encoded and existing["payload_digest"] == digest:
                return False
            raise ShadowEvidenceStoreError("event id collision or conflicting duplicate")
        count = connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        if count >= self.max_rows:
            raise ShadowEvidenceStoreError(f"{table} row limit exceeded")
        connection.execute(
            f"INSERT INTO {table} "
            "(event_id, active_release, candidate_release, active_artifact_digest, "
            "candidate_artifact_digest, policy_digest, contract_version, payload, "
            "payload_digest, recorded_at" + (", observed_at, input_digest" if table == "observations" else "") +
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now')" +
            (", ?, ?" if table == "observations" else "") + ")",
            (event_id, *_identity_tuple(identity), encoded, digest, *extra),
        )
        return True

    @staticmethod
    def _validated_policy_row(row: sqlite3.Row, expected: ShadowPolicy) -> None:
        raw = bytes(row["payload"])
        payload = json.loads(raw)
        digest = policy_digest(expected)
        if (
            canonical_json(payload) != raw
            or payload != to_dict(expected)
            or row["payload_digest"] != digest
            or row["policy_digest"] != digest
            or row["event_id"] != _event_id("policy", digest)
            or row["contract_version"] != CONTRACT_VERSION
        ):
            raise ShadowEvidenceStoreError("stored policy evidence chain is invalid")

    @staticmethod
    def _validated_observation_row(
        row: sqlite3.Row, identity: ShadowReleaseIdentity,
    ) -> ShadowObservation:
        raw = bytes(row["payload"])
        payload = json.loads(raw)
        if canonical_json(payload) != raw:
            raise ShadowEvidenceStoreError("stored observation is not canonical")
        observation = _observation_from(payload)
        digest = _sha256(_EVENT_DOMAIN, payload)
        expected_event = _event_id("observation", observation_digest(payload))
        if (
            row["payload_digest"] != digest
            or row["event_id"] != expected_event
            or observation.input_digest != row["input_digest"]
            or observation.release_identity != identity
            or tuple(row[name] for name in (
                "active_release", "candidate_release", "active_artifact_digest",
                "candidate_artifact_digest", "policy_digest", "contract_version",
            )) != _identity_tuple(identity)
            or observation.observed_at != row["observed_at"]
        ):
            raise ShadowEvidenceStoreError("stored observation evidence chain is invalid")
        return observation

    def record_policy(self, policy: ShadowPolicy) -> str:
        payload = to_dict(policy)
        digest = policy_digest(policy)
        event_id = _event_id("policy", digest)

        def write(connection: sqlite3.Connection) -> str:
            existing = connection.execute(
                "SELECT payload, payload_digest FROM policies WHERE event_id=?", (event_id,)
            ).fetchone()
            encoded = canonical_json(payload)
            if existing:
                if bytes(existing["payload"]) == encoded and existing["payload_digest"] == digest:
                    return event_id
                raise ShadowEvidenceStoreError("conflicting policy event")
            if connection.execute("SELECT count(*) FROM policies").fetchone()[0] >= self.max_rows:
                raise ShadowEvidenceStoreError("policy row limit exceeded")
            connection.execute(
                "INSERT INTO policies(event_id, policy_digest, contract_version, payload, "
                "payload_digest, recorded_at) VALUES (?, ?, ?, ?, ?, "
                "strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
                (event_id, digest, CONTRACT_VERSION, encoded, digest),
            )
            return event_id

        return self._transaction(write)

    def record_observation(self, event_id: str, observation: ShadowObservation) -> str:
        if event_id != _event_id("observation", observation_digest(to_dict(observation))):
            raise ShadowEvidenceStoreError("observation event id does not match payload")
        payload = to_dict(observation)

        def write(connection: sqlite3.Connection) -> str:
            policy = connection.execute(
                "SELECT * FROM policies WHERE policy_digest=?",
                (observation.release_identity.policy_digest,),
            ).fetchone()
            if not policy:
                raise ShadowEvidenceStoreError("observation policy is not durably recorded")
            expected_policy = ShadowPolicy(**json.loads(bytes(policy["payload"])))
            self._validated_policy_row(policy, expected_policy)
            self._insert_event(
                connection, "observations", event_id, payload, observation.release_identity,
                (observation.observed_at, observation.input_digest),
            )
            return event_id

        result = self._transaction(write)
        self.record_observation_completion(
            event_id, observation.elapsed_ms, commit_guard=lambda: True,
        )
        return result

    def record_policy_and_observation(
        self,
        policy: ShadowPolicy,
        event_id: str,
        observation: ShadowObservation,
        *,
        commit_guard: Callable[[], bool],
    ) -> str:
        """Atomically append policy and observation with a final commit guard."""
        if observation.release_identity.policy_digest != policy_digest(policy):
            raise ShadowEvidenceStoreError("observation and policy digest differ")
        if event_id != _event_id("observation", observation_digest(to_dict(observation))):
            raise ShadowEvidenceStoreError("observation event id does not match payload")
        policy_payload = to_dict(policy)
        policy_value_digest = policy_digest(policy)
        policy_event_id = _event_id("policy", policy_value_digest)
        observation_payload = to_dict(observation)

        def write(connection: sqlite3.Connection) -> str:
            existing = connection.execute(
                "SELECT payload, payload_digest FROM policies WHERE event_id=?",
                (policy_event_id,),
            ).fetchone()
            encoded_policy = canonical_json(policy_payload)
            if existing:
                if (
                    bytes(existing["payload"]) != encoded_policy
                    or existing["payload_digest"] != policy_value_digest
                ):
                    raise ShadowEvidenceStoreError("conflicting policy event")
            else:
                if connection.execute(
                    "SELECT count(*) FROM policies"
                ).fetchone()[0] >= self.max_rows:
                    raise ShadowEvidenceStoreError("policy row limit exceeded")
                connection.execute(
                    "INSERT INTO policies(event_id, policy_digest, contract_version, "
                    "payload, payload_digest, recorded_at) VALUES (?, ?, ?, ?, ?, "
                    "strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
                    (
                        policy_event_id, policy_value_digest, CONTRACT_VERSION,
                        encoded_policy, policy_value_digest,
                    ),
                )
            self._insert_event(
                connection,
                "observations",
                event_id,
                observation_payload,
                observation.release_identity,
                (observation.observed_at, observation.input_digest),
            )
            return event_id

        return self._transaction(write, commit_guard=commit_guard)

    def record_observation_completion(
        self,
        observation_event_id: str,
        elapsed_ms: float,
        *,
        commit_guard: Callable[[], bool],
        terminal_status: str = "completed",
    ) -> str:
        """Append operational latency measured after observation durability."""
        if (
            _DIGEST_RE.fullmatch(observation_event_id) is None
            or not isinstance(elapsed_ms, (int, float))
            or isinstance(elapsed_ms, bool)
            or not math.isfinite(elapsed_ms)
            or elapsed_ms < 0
        ):
            raise ShadowEvidenceStoreError("observation completion is invalid")
        if terminal_status not in {"completed", "orphaned_after_commit"}:
            raise ShadowEvidenceStoreError("completion terminal status is invalid")
        payload = {
            "observation_event_id": observation_event_id,
            "elapsed_ms": float(elapsed_ms),
            "terminal_status": terminal_status,
        }
        digest = _sha256(_EVENT_DOMAIN, payload)
        completion_id = _event_id("observation_completion", digest)

        def write(connection: sqlite3.Connection) -> str:
            if not connection.execute(
                "SELECT 1 FROM observations WHERE event_id=?",
                (observation_event_id,),
            ).fetchone():
                raise ShadowEvidenceStoreError("completion observation does not exist")
            existing = connection.execute(
                "SELECT payload, payload_digest FROM observation_completions "
                "WHERE event_id=?",
                (completion_id,),
            ).fetchone()
            encoded = canonical_json(payload)
            if existing:
                if (
                    bytes(existing["payload"]) == encoded
                    and existing["payload_digest"] == digest
                ):
                    return completion_id
                raise ShadowEvidenceStoreError("conflicting completion event")
            if connection.execute(
                "SELECT count(*) FROM observation_completions"
            ).fetchone()[0] >= self.max_rows:
                raise ShadowEvidenceStoreError("completion row limit exceeded")
            connection.execute(
                "INSERT INTO observation_completions("
                "event_id, observation_event_id, elapsed_ms, payload, "
                "payload_digest, recorded_at) VALUES (?, ?, ?, ?, ?, "
                "strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
                (
                    completion_id, observation_event_id, float(elapsed_ms),
                    encoded, digest,
                ),
            )
            return completion_id

        return self._transaction(write, commit_guard=commit_guard)

    @staticmethod
    def observation_event_id(observation: ShadowObservation) -> str:
        return _event_id("observation", observation_digest(to_dict(observation)))

    def evaluate(
        self,
        identity: ShadowReleaseIdentity,
        policy: ShadowPolicy,
        *,
        now: str,
        limit: int = DEFAULT_MAX_QUERY_ROWS,
    ) -> StoredEvaluation:
        if not 0 < limit <= self.max_query_rows:
            raise ShadowEvidenceStoreError("observation query exceeds configured bound")
        if identity.policy_digest != policy_digest(policy):
            raise ShadowEvidenceStoreError("release identity and policy digest differ")

        def evaluate_and_write(connection: sqlite3.Connection) -> StoredEvaluation:
            policy_row = connection.execute(
                "SELECT * FROM policies WHERE policy_digest=?", (identity.policy_digest,)
            ).fetchone()
            if not policy_row:
                raise ShadowEvidenceStoreError("evaluation policy is not durably recorded")
            self._validated_policy_row(policy_row, policy)
            rows = connection.execute(
                "SELECT * FROM observations WHERE "
                "active_release=? AND candidate_release=? AND active_artifact_digest=? AND "
                "candidate_artifact_digest=? AND policy_digest=? AND contract_version=? "
                "ORDER BY observed_at ASC, event_id ASC LIMIT ?",
                (*_identity_tuple(identity), self.max_query_rows + 1),
            ).fetchall()
            if len(rows) > self.max_query_rows:
                raise ShadowEvidenceStoreError("observation result exceeds query bound")
            boundary = _utc_timestamp(now)
            window_start = boundary - timedelta(hours=policy.window_hours)
            observations: list[ShadowObservation] = []
            selected_rows: list[sqlite3.Row] = []
            for row in rows:
                observation = self._validated_observation_row(row, identity)
                observed_at = _utc_timestamp(observation.observed_at)
                if observed_at > boundary:
                    raise ShadowEvidenceStoreError("future observation fails evaluation closed")
                if observed_at < window_start:
                    continue
                completion = connection.execute(
                    "SELECT * FROM observation_completions "
                    "WHERE observation_event_id=?",
                    (row["event_id"],),
                ).fetchone()
                if completion is None:
                    orphan_payload = {
                        "observation_event_id": row["event_id"],
                        "elapsed_ms": policy.latency_each_ms_max,
                        "terminal_status": "orphaned_after_commit",
                    }
                    orphan_digest = _sha256(_EVENT_DOMAIN, orphan_payload)
                    orphan_id = _event_id(
                        "observation_completion", orphan_digest,
                    )
                    connection.execute(
                        "INSERT INTO observation_completions("
                        "event_id, observation_event_id, elapsed_ms, payload, "
                        "payload_digest, recorded_at) VALUES (?, ?, ?, ?, ?, "
                        "strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
                        (
                            orphan_id, row["event_id"],
                            policy.latency_each_ms_max,
                            canonical_json(orphan_payload), orphan_digest,
                        ),
                    )
                    completion = connection.execute(
                        "SELECT * FROM observation_completions WHERE event_id=?",
                        (orphan_id,),
                    ).fetchone()
                completion_payload = json.loads(bytes(completion["payload"]))
                if (
                    canonical_json(completion_payload) != bytes(completion["payload"])
                    or completion_payload.get("observation_event_id") != row["event_id"]
                    or completion_payload.get("elapsed_ms") != completion["elapsed_ms"]
                    or completion_payload.get("terminal_status")
                    not in {"completed", "orphaned_after_commit"}
                    or _sha256(_EVENT_DOMAIN, completion_payload)
                    != completion["payload_digest"]
                    or completion["event_id"]
                    != _event_id(
                        "observation_completion", completion["payload_digest"],
                    )
                ):
                    raise ShadowEvidenceStoreError(
                        "observation completion evidence is invalid"
                    )
                observation = replace(
                    observation,
                    elapsed_ms=float(completion["elapsed_ms"]),
                    status=(
                        "corrupt"
                        if completion_payload["terminal_status"]
                        == "orphaned_after_commit"
                        else observation.status
                    ),
                    parity_passed=(
                        False
                        if completion_payload["terminal_status"]
                        == "orphaned_after_commit"
                        else observation.parity_passed
                    ),
                )
                observations.append(observation)
                selected_rows.append(row)
            if len(observations) > limit:
                raise ShadowEvidenceStoreError("selected observation window exceeds query bound")
            decision = evaluate_shadow(observations, policy, now=now)
            ordered_ids = [row["event_id"] for row in selected_rows]
            root = _sha256(
                _ROOT_DOMAIN,
                {"identity": to_dict(identity), "policy_digest": identity.policy_digest,
                 "now": now, "ordered_observation_event_ids": ordered_ids},
            )
            aggregate_payload = {
                "aggregate": to_dict(decision.aggregate), "observation_root_digest": root,
                "ordered_observation_event_ids": ordered_ids, "evaluated_at": now,
            }
            aggregate_digest = _sha256(_EVENT_DOMAIN, aggregate_payload)
            aggregate_id = _event_id("aggregate", aggregate_digest)
            self._insert_event(
                connection, "aggregates", aggregate_id, aggregate_payload, identity,
            )
            decision_payload = {
                "decision": to_dict(decision), "aggregate_event_id": aggregate_id,
                "observation_root_digest": root, "evaluated_at": now,
            }
            decision_digest = _sha256(_EVENT_DOMAIN, decision_payload)
            decision_id = _event_id("decision", decision_digest)
            self._insert_event(
                connection, "decisions", decision_id, decision_payload, identity,
            )
            return StoredEvaluation(aggregate_id, decision_id, root, decision)

        return self._transaction(evaluate_and_write)

    def read_only_evaluate(
        self,
        identity: ShadowReleaseIdentity,
        policy: ShadowPolicy,
        *,
        now: str,
        limit: int = DEFAULT_MAX_QUERY_ROWS,
    ) -> ReadOnlyShadowEvaluation:
        """Evaluate an exact release window without changing durable state.

        Missing completion evidence is represented as a corrupt terminal
        observation in memory.  Unlike ``evaluate``, this method never repairs
        an orphan or appends aggregate/decision events.
        """
        if not self.read_only:
            raise ShadowEvidenceStoreError(
                "read-only evaluation requires read_only=True"
            )
        if not 0 < limit <= self.max_query_rows:
            raise ShadowEvidenceStoreError(
                "observation query exceeds configured bound"
            )
        if identity.policy_digest != policy_digest(policy):
            raise ShadowEvidenceStoreError(
                "release identity and policy digest differ"
            )

        def read(connection: sqlite3.Connection) -> ReadOnlyShadowEvaluation:
            policy_row = connection.execute(
                "SELECT * FROM policies WHERE policy_digest=?",
                (identity.policy_digest,),
            ).fetchone()
            if not policy_row:
                raise ShadowEvidenceStoreError(
                    "evaluation policy is not durably recorded"
                )
            self._validated_policy_row(policy_row, policy)
            rows = connection.execute(
                "SELECT * FROM observations WHERE "
                "active_release=? AND candidate_release=? AND "
                "active_artifact_digest=? AND candidate_artifact_digest=? AND "
                "policy_digest=? AND contract_version=? "
                "ORDER BY observed_at ASC, event_id ASC LIMIT ?",
                (*_identity_tuple(identity), self.max_query_rows + 1),
            ).fetchall()
            if len(rows) > self.max_query_rows:
                raise ShadowEvidenceStoreError(
                    "observation result exceeds query bound"
                )
            boundary = _utc_timestamp(now)
            window_start = boundary - timedelta(hours=policy.window_hours)
            observations: list[ShadowObservation] = []
            ordered_ids: list[str] = []
            for row in rows:
                observation = self._validated_observation_row(row, identity)
                observed_at = _utc_timestamp(observation.observed_at)
                if observed_at > boundary:
                    raise ShadowEvidenceStoreError(
                        "future observation fails evaluation closed"
                    )
                if observed_at < window_start:
                    continue
                completion = connection.execute(
                    "SELECT * FROM observation_completions "
                    "WHERE observation_event_id=?",
                    (row["event_id"],),
                ).fetchone()
                if completion is None:
                    observation = replace(
                        observation,
                        elapsed_ms=policy.latency_each_ms_max,
                        status="corrupt",
                        parity_passed=False,
                    )
                else:
                    completion_payload = json.loads(bytes(completion["payload"]))
                    if (
                        canonical_json(completion_payload)
                        != bytes(completion["payload"])
                        or completion_payload.get("observation_event_id")
                        != row["event_id"]
                        or completion_payload.get("elapsed_ms")
                        != completion["elapsed_ms"]
                        or completion_payload.get("terminal_status")
                        not in {"completed", "orphaned_after_commit"}
                        or _sha256(_EVENT_DOMAIN, completion_payload)
                        != completion["payload_digest"]
                        or completion["event_id"]
                        != _event_id(
                            "observation_completion",
                            completion["payload_digest"],
                        )
                    ):
                        raise ShadowEvidenceStoreError(
                            "observation completion evidence is invalid"
                        )
                    terminal_status = completion_payload["terminal_status"]
                    observation = replace(
                        observation,
                        elapsed_ms=float(completion["elapsed_ms"]),
                        status=(
                            "corrupt"
                            if terminal_status == "orphaned_after_commit"
                            else observation.status
                        ),
                        parity_passed=(
                            False
                            if terminal_status == "orphaned_after_commit"
                            else observation.parity_passed
                        ),
                    )
                observations.append(observation)
                ordered_ids.append(row["event_id"])
            if len(observations) > limit:
                raise ShadowEvidenceStoreError(
                    "selected observation window exceeds query bound"
                )
            if observations:
                decision = evaluate_shadow(observations, policy, now=now)
            else:
                aggregate = ShadowAggregate(
                    release_identity=identity,
                    observation_count=0,
                    coin_count=0,
                    question_type_count=0,
                    minimum_cell_count=0,
                    parity_rate=0.0,
                    terminal_failure_streak=0,
                    latency_p95_ms=0.0,
                    blockers=(
                        *(
                            (ShadowBlocker.MISSING_STALE_OR_FUTURE,)
                            if rows else ()
                        ),
                        ShadowBlocker.INSUFFICIENT_OBSERVATIONS,
                        ShadowBlocker.INSUFFICIENT_COIN_COVERAGE,
                        ShadowBlocker.INSUFFICIENT_QTYPE_COVERAGE,
                        ShadowBlocker.INCOMPLETE_SCENARIO_MATRIX,
                    ),
                )
                decision = ShadowDecision(
                    release_identity=identity,
                    action=(
                        ShadowDecisionAction.STOP
                        if rows
                        else ShadowDecisionAction.CONTINUE_OBSERVATION
                    ),
                    aggregate=aggregate,
                )
            root = _sha256(
                _ROOT_DOMAIN,
                {
                    "identity": to_dict(identity),
                    "policy_digest": identity.policy_digest,
                    "now": now,
                    "ordered_observation_event_ids": ordered_ids,
                },
            )
            aggregate_payload = {
                "aggregate": to_dict(decision.aggregate),
                "observation_root_digest": root,
                "ordered_observation_event_ids": ordered_ids,
                "evaluated_at": now,
            }
            aggregate_digest = _sha256(_EVENT_DOMAIN, aggregate_payload)
            aggregate_id = _event_id("aggregate", aggregate_digest)
            decision_payload = {
                "decision": to_dict(decision),
                "aggregate_event_id": aggregate_id,
                "observation_root_digest": root,
                "evaluated_at": now,
            }
            decision_digest = _sha256(_EVENT_DOMAIN, decision_payload)
            return ReadOnlyShadowEvaluation(
                aggregate_event_id=aggregate_id,
                decision_event_id=_event_id("decision", decision_digest),
                observation_root_digest=root,
                ordered_observation_event_ids=tuple(ordered_ids),
                observations=tuple(observations),
                decision=decision,
            )

        return self._read_transaction(read)

    def read_canonical_observations(
        self,
        identity: ShadowReleaseIdentity,
        policy: ShadowPolicy,
        *,
        pit_cutoff: str,
        limit: int = DEFAULT_MAX_QUERY_ROWS,
    ) -> tuple[CanonicalShadowObservation, ...]:
        """Read an exact persisted release window without fixture synthesis.

        Every observation and completion row is authenticated against the
        append-only schema. Missing or orphaned completion evidence fails
        closed because promotion datasets may contain only durable terminal
        observations.
        """
        if not self.read_only:
            raise ShadowEvidenceStoreError(
                "canonical observation export requires read_only=True"
            )
        if not 0 < limit <= self.max_query_rows:
            raise ShadowEvidenceStoreError(
                "canonical observation export exceeds configured bound"
            )
        if identity.policy_digest != policy_digest(policy):
            raise ShadowEvidenceStoreError(
                "release identity and policy digest differ"
            )
        cutoff = _utc_timestamp(pit_cutoff).isoformat().replace("+00:00", "Z")

        def read(connection: sqlite3.Connection) -> tuple[CanonicalShadowObservation, ...]:
            policy_row = connection.execute(
                "SELECT * FROM policies WHERE policy_digest=?",
                (identity.policy_digest,),
            ).fetchone()
            if policy_row is None:
                raise ShadowEvidenceStoreError(
                    "export policy is not durably recorded"
                )
            self._validated_policy_row(policy_row, policy)
            rows = connection.execute(
                "SELECT observations.*, "
                "observation_completions.event_id AS completion_event_id, "
                "observation_completions.elapsed_ms AS completion_elapsed_ms, "
                "observation_completions.payload AS completion_payload, "
                "observation_completions.payload_digest AS completion_payload_digest, "
                "observation_completions.recorded_at AS completion_recorded_at "
                "FROM observations JOIN observation_completions ON "
                "observation_completions.observation_event_id=observations.event_id "
                "WHERE "
                "active_release=? AND candidate_release=? AND "
                "active_artifact_digest=? AND candidate_artifact_digest=? AND "
                "policy_digest=? AND contract_version=? AND "
                "observations.recorded_at<=? AND "
                "observation_completions.recorded_at<=? "
                "ORDER BY observed_at ASC, event_id ASC LIMIT ?",
                (*_identity_tuple(identity), cutoff, cutoff, limit + 1),
            ).fetchall()
            if len(rows) > limit:
                raise ShadowEvidenceStoreError(
                    "canonical observation export exceeds query bound"
                )
            exported: list[CanonicalShadowObservation] = []
            for row in rows:
                observation = self._validated_observation_row(row, identity)
                payload = json.loads(bytes(row["completion_payload"]))
                if (
                    canonical_json(payload) != bytes(row["completion_payload"])
                    or payload.get("observation_event_id") != row["event_id"]
                    or payload.get("elapsed_ms") != row["completion_elapsed_ms"]
                    or payload.get("terminal_status") != "completed"
                    or _sha256(_EVENT_DOMAIN, payload)
                    != row["completion_payload_digest"]
                    or row["completion_event_id"]
                    != _event_id(
                        "observation_completion",
                        row["completion_payload_digest"],
                    )
                ):
                    raise ShadowEvidenceStoreError(
                        "promotion observation completion is invalid"
                    )
                exported.append(
                    CanonicalShadowObservation(
                        event_id=row["event_id"],
                        recorded_at=row["recorded_at"],
                        completion_recorded_at=row["completion_recorded_at"],
                        observation=replace(
                            observation,
                            elapsed_ms=float(row["completion_elapsed_ms"]),
                        ),
                    )
                )
            return tuple(exported)

        return self._read_transaction(read)

    def record_retention_receipt(
        self,
        *,
        identity: ShadowReleaseIdentity,
        archive_uri: str,
        before_event_id: str,
        archive_digest: str,
        observation_root_digest: str,
    ) -> str:
        parsed_uri = urlparse(archive_uri)
        if parsed_uri.scheme != "s3" or not parsed_uri.netloc or not parsed_uri.path.strip("/"):
            raise ShadowEvidenceStoreError("archive URI must identify an s3 object")
        for value, name in (
            (before_event_id, "before_event_id"),
            (archive_digest, "archive_digest"),
            (observation_root_digest, "observation_root_digest"),
        ):
            if _DIGEST_RE.fullmatch(value) is None:
                raise ShadowEvidenceStoreError(f"{name} must be a lowercase sha256 digest")
        payload = {
            "operation": "archive_compaction", "archive_uri": archive_uri,
            "before_event_id": before_event_id, "archive_digest": archive_digest,
            "observation_root_digest": observation_root_digest,
            "release_identity": to_dict(identity),
        }
        receipt_id = _sha256(_RECEIPT_DOMAIN, payload)

        def write(connection: sqlite3.Connection) -> str:
            cutoff = connection.execute(
                "SELECT * FROM observations WHERE event_id=?", (before_event_id,)
            ).fetchone()
            if not cutoff:
                raise ShadowEvidenceStoreError("retention cutoff observation does not exist")
            self._validated_observation_row(cutoff, identity)
            aggregate_rows = connection.execute(
                "SELECT * FROM aggregates WHERE active_release=? AND candidate_release=? "
                "AND active_artifact_digest=? AND candidate_artifact_digest=? "
                "AND policy_digest=? AND contract_version=?",
                _identity_tuple(identity),
            ).fetchall()
            bound = False
            for aggregate_row in aggregate_rows:
                aggregate_payload = json.loads(bytes(aggregate_row["payload"]))
                if (
                    canonical_json(aggregate_payload) == bytes(aggregate_row["payload"])
                    and _sha256(_EVENT_DOMAIN, aggregate_payload)
                    == aggregate_row["payload_digest"]
                    and aggregate_row["event_id"]
                    == _event_id("aggregate", aggregate_row["payload_digest"])
                    and tuple(aggregate_row[name] for name in (
                        "active_release", "candidate_release", "active_artifact_digest",
                        "candidate_artifact_digest", "policy_digest", "contract_version",
                    )) == _identity_tuple(identity)
                    and aggregate_payload.get("observation_root_digest") == observation_root_digest
                    and before_event_id
                    in aggregate_payload.get("ordered_observation_event_ids", [])
                ):
                    bound = True
                    break
            if not bound:
                raise ShadowEvidenceStoreError("retention receipt is not bound to an aggregate root")
            encoded = canonical_json(payload)
            existing = connection.execute(
                "SELECT payload, payload_digest FROM retention_receipts WHERE receipt_id=?",
                (receipt_id,),
            ).fetchone()
            if existing:
                if (
                    bytes(existing["payload"]) == encoded
                    and existing["payload_digest"] == receipt_id
                    and _sha256(_RECEIPT_DOMAIN, json.loads(bytes(existing["payload"])))
                    == receipt_id
                ):
                    return receipt_id
                raise ShadowEvidenceStoreError("conflicting retention receipt")
            if connection.execute(
                "SELECT count(*) FROM retention_receipts"
            ).fetchone()[0] >= self.max_rows:
                raise ShadowEvidenceStoreError("retention receipt row limit exceeded")
            connection.execute(
                "INSERT INTO retention_receipts(receipt_id, payload, payload_digest, recorded_at) "
                "VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
                (receipt_id, encoded, receipt_id),
            )
            return receipt_id

        return self._transaction(write)


_IDENTITY_COLUMNS = """
    active_release TEXT NOT NULL,
    candidate_release TEXT NOT NULL,
    active_artifact_digest TEXT NOT NULL,
    candidate_artifact_digest TEXT NOT NULL,
    policy_digest TEXT NOT NULL,
    contract_version TEXT NOT NULL
"""
_OBSERVATION_COMPLETION_SCHEMA = """
CREATE TABLE observation_completions(
    event_id TEXT PRIMARY KEY,
    observation_event_id TEXT NOT NULL UNIQUE,
    elapsed_ms REAL NOT NULL,
    payload BLOB NOT NULL,
    payload_digest TEXT NOT NULL,
    recorded_at TEXT NOT NULL
) STRICT;
"""
_SCHEMA_V1 = f"""
CREATE TABLE schema_migrations(
    version INTEGER PRIMARY KEY, contract_version TEXT NOT NULL, applied_at TEXT NOT NULL
) STRICT;
CREATE TABLE policies(
    event_id TEXT PRIMARY KEY, policy_digest TEXT NOT NULL UNIQUE,
    contract_version TEXT NOT NULL, payload BLOB NOT NULL,
    payload_digest TEXT NOT NULL, recorded_at TEXT NOT NULL
) STRICT;
CREATE TABLE observations(
    event_id TEXT PRIMARY KEY, {_IDENTITY_COLUMNS},
    payload BLOB NOT NULL, payload_digest TEXT NOT NULL, recorded_at TEXT NOT NULL,
    observed_at TEXT NOT NULL, input_digest TEXT NOT NULL
) STRICT;
CREATE INDEX observations_tuple_order ON observations(
    active_release, candidate_release, active_artifact_digest,
    candidate_artifact_digest, policy_digest, contract_version, observed_at, event_id
);
CREATE TABLE aggregates(
    event_id TEXT PRIMARY KEY, {_IDENTITY_COLUMNS},
    payload BLOB NOT NULL, payload_digest TEXT NOT NULL, recorded_at TEXT NOT NULL
) STRICT;
CREATE TABLE decisions(
    event_id TEXT PRIMARY KEY, {_IDENTITY_COLUMNS},
    payload BLOB NOT NULL, payload_digest TEXT NOT NULL, recorded_at TEXT NOT NULL
) STRICT;
CREATE TABLE retention_receipts(
    receipt_id TEXT PRIMARY KEY, payload BLOB NOT NULL,
    payload_digest TEXT NOT NULL, recorded_at TEXT NOT NULL
) STRICT;
"""
_SCHEMA = _SCHEMA_V1 + _OBSERVATION_COMPLETION_SCHEMA
_IMMUTABILITY_TRIGGERS = "\n".join(
    f"""
CREATE TRIGGER immutable_{table}_update BEFORE UPDATE ON {table}
BEGIN SELECT RAISE(ABORT, 'append-only table'); END;
CREATE TRIGGER immutable_{table}_delete BEFORE DELETE ON {table}
BEGIN SELECT RAISE(ABORT, 'append-only table'); END;
"""
    for table in (
        "schema_migrations", "policies", "observations", "aggregates",
        "decisions", "retention_receipts", "observation_completions",
    )
)
_IMMUTABILITY_TRIGGERS_V1 = "\n".join(
    f"""
CREATE TRIGGER immutable_{table}_update BEFORE UPDATE ON {table}
BEGIN SELECT RAISE(ABORT, 'append-only table'); END;
CREATE TRIGGER immutable_{table}_delete BEFORE DELETE ON {table}
BEGIN SELECT RAISE(ABORT, 'append-only table'); END;
"""
    for table in (
        "schema_migrations", "policies", "observations", "aggregates",
        "decisions", "retention_receipts",
    )
)
_OBSERVATION_COMPLETION_TRIGGER_STATEMENTS = (
    """
CREATE TRIGGER immutable_observation_completions_update BEFORE UPDATE ON observation_completions
BEGIN SELECT RAISE(ABORT, 'append-only table'); END
""",
    """
CREATE TRIGGER immutable_observation_completions_delete BEFORE DELETE ON observation_completions
BEGIN SELECT RAISE(ABORT, 'append-only table'); END
""",
)
_OBSERVATION_COMPLETION_TRIGGERS = ";\n".join(
    _OBSERVATION_COMPLETION_TRIGGER_STATEMENTS
) + ";"


def _schema_fingerprint(connection: sqlite3.Connection) -> str:
    objects = [
        tuple(row) for row in connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        )
    ]
    tables: dict[str, Any] = {}
    for table in (
        "schema_migrations", "policies", "observations", "aggregates",
        "decisions", "retention_receipts", "observation_completions",
    ):
        tables[table] = {
            "table_xinfo": [tuple(row) for row in connection.execute(f"PRAGMA table_xinfo({table})")],
            "indexes": [
                {
                    "list": tuple(index),
                    "xinfo": [
                        tuple(row) for row in connection.execute(
                            f"PRAGMA index_xinfo({json.dumps(index[1])})"
                        )
                    ],
                }
                for index in connection.execute(f"PRAGMA index_list({table})")
            ],
        }
    value = {
            "application_id": connection.execute("PRAGMA application_id").fetchone()[0],
            "user_version": connection.execute("PRAGMA user_version").fetchone()[0],
            "objects": objects,
            "tables": tables,
    }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return "sha256:" + hashlib.sha256(_EVENT_DOMAIN + encoded).hexdigest()


@lru_cache(maxsize=1)
def _expected_schema_fingerprint() -> str:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(_SCHEMA)
        connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        connection.executescript(_IMMUTABILITY_TRIGGERS)
        return _schema_fingerprint(connection)
    finally:
        connection.close()


@lru_cache(maxsize=1)
def _expected_v1_schema_fingerprint() -> str:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(_SCHEMA_V1)
        connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
        connection.execute("PRAGMA user_version=1")
        connection.executescript(_IMMUTABILITY_TRIGGERS_V1)
        return _schema_fingerprint(connection)
    finally:
        connection.close()
