"""Distributed activation lock for A/B release promotion.

Uses a DynamoDB table ``trustforge-activation-locks`` (keyed by
``activation_target``) to ensure at-most-one concurrent activation on a
given EC2 deployment target.  The lock is deadline-aware: an ``expires_at``
TTL defends against a crashed activator permanently blocking the target.

Usage::

    from trustforge.activation_lock import acquire_activation_lock, release_activation_lock

    if acquire_activation_lock("trustforge-demo", "deployer-pid-42", ttl=300):
        # ... do the activation transaction ...
        release_activation_lock("trustforge-demo", "deployer-pid-42")

The acquire is an atomic conditional PutItem:

    * attribute_not_exists(activation_target)  -- no lock held
    * expires_at < now                          -- previous lock expired
    * owner_id == my_owner                      -- re-entrant refresh

All backends are fail-closed: a DynamoDB error during acquire returns False
(blocking progress) so we never allow two activators to run concurrently.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO


logger = logging.getLogger(__name__)


class LockBackendError(RuntimeError):
    """Non-recoverable backend error that prevents lock acquisition."""


@dataclass(frozen=True)
class ActivationLockRecord:
    target: str
    owner_id: str
    acquired_at: float
    expires_at: float
    metadata: dict[str, Any] = field(default_factory=dict)


def _default_ttl_seconds() -> int:
    raw = os.getenv("TRUSTFORGE_ACTIVATION_LOCK_TTL_SECONDS", "300")
    try:
        val = int(raw)
        if val > 0:
            return val
    except ValueError:
        pass
    return 300


def _new_owner_id() -> str:
    return f"{os.getpid()}:{uuid.uuid4().hex}"


# ---------------------------------------------------------------------------
# DynamoDB backend
# ---------------------------------------------------------------------------

class _DynamoDBActivationLockBackend:
    """Conditional PutItem / DeleteItem against ``trustforge-activation-locks``."""

    def __init__(self, table_name: str | None = None, region: str | None = None):
        self.table_name = table_name or os.getenv(
            "TRUSTFORGE_ACTIVATION_LOCK_TABLE", "trustforge-activation-locks"
        )
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        self._table: Any = None

    def _get_table(self):
        if self._table is None:
            import boto3
            self._table = boto3.resource("dynamodb", region_name=self.region).Table(self.table_name)
        return self._table

    def acquire(self, target: str, owner_id: str, ttl_seconds: int) -> bool:
        from boto3.dynamodb.conditions import Attr
        from botocore.exceptions import ClientError
        from decimal import Decimal

        now = time.time()
        expires = int(now + ttl_seconds)
        try:
            self._get_table().put_item(
                Item={
                    "activation_target": target,
                    "owner_id": owner_id,
                    "acquired_at": str(now),
                    "expires_at": expires,
                },
                ConditionExpression=(
                    Attr("activation_target").not_exists()
                    | Attr("expires_at").lt(int(now))
                    | Attr("owner_id").eq(owner_id)
                ),
            )
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            logger.warning("Activation lock acquire: DynamoDB error (fail-closed)", exc_info=True)
            return False
        except Exception:
            logger.warning("Activation lock acquire: unexpected error (fail-closed)", exc_info=True)
            return False

    def release(self, target: str, owner_id: str) -> bool:
        from boto3.dynamodb.conditions import Attr
        from botocore.exceptions import ClientError

        try:
            self._get_table().delete_item(
                Key={"activation_target": target},
                ConditionExpression=Attr("owner_id").eq(owner_id),
            )
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            logger.warning("Activation lock release: DynamoDB error", exc_info=True)
            return True  # best-effort: don't block caller on cleanup
        except Exception:
            logger.warning("Activation lock release: unexpected error", exc_info=True)
            return True

    def get(self, target: str) -> ActivationLockRecord | None:
        try:
            item = self._get_table().get_item(Key={"activation_target": target}).get("Item")
        except Exception:
            logger.warning("Activation lock get: DynamoDB error", exc_info=True)
            return None
        if not item:
            return None
        try:
            return ActivationLockRecord(
                target=item.get("activation_target", target),
                owner_id=item.get("owner_id", ""),
                acquired_at=float(item.get("acquired_at", "0")),
                expires_at=int(item.get("expires_at", 0)),
                metadata=json.loads(item.get("metadata", "{}")),
            )
        except (ValueError, TypeError, json.JSONDecodeError):
            return None


# ---------------------------------------------------------------------------
# Fallback backend: JSON file + fcntl.flock (single-machine durably-atomic)
# ---------------------------------------------------------------------------

class _JsonActivationLockBackend:
    """Local-host lock backend used when DynamoDB is unavailable (dev/CI)."""

    def __init__(self, path: str | Path | None = None):
        if path is None:
            home = os.getenv("TRUSTFORGE_HOME", str(Path(__file__).resolve().parents[2]))
            path = Path(home) / "out" / "activation_locks.json"
        self.path = Path(path)

    @property
    def _lock_path(self) -> Path:
        return self.path.with_name(self.path.name + ".lock")

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=f".{self.path.name}.", suffix=".tmp"
        )
        try:
            os.fdopen(fd, "w", encoding="utf-8").close()
            with open(tmp_name, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def _lock_fd(self) -> tuple[int, TextIO]:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        fcntl = __import__("fcntl")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        return lock_fd, os.fdopen(os.dup(lock_fd), "w+")

    def _unlock_fd(self, lock_fd: int, fh: TextIO) -> None:
        fcntl = __import__("fcntl")
        fh.close()
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)

    def acquire(self, target: str, owner_id: str, ttl_seconds: int) -> bool:
        try:
            lock_fd, lock_fh = self._lock_fd()
        except OSError:
            logger.warning("Activation lock acquire: cannot acquire file lock (fail-closed)", exc_info=True)
            return False
        try:
            now = time.time()
            data = self._load()
            existing = data.get(target)
            if existing is not None and now < existing.get("expires_at", 0.0):
                if existing.get("owner_id") != owner_id:
                    return False
            data[target] = {
                "owner_id": owner_id,
                "acquired_at": now,
                "expires_at": now + ttl_seconds,
            }
            self._write(data)
            return True
        finally:
            self._unlock_fd(lock_fd, lock_fh)

    def release(self, target: str, owner_id: str) -> bool:
        try:
            lock_fd, lock_fh = self._lock_fd()
        except OSError:
            return True  # best-effort cleanup
        try:
            data = self._load()
            existing = data.get(target)
            if existing is not None and existing.get("owner_id") == owner_id:
                data.pop(target, None)
                self._write(data)
            return True
        finally:
            self._unlock_fd(lock_fd, lock_fh)

    def get(self, target: str) -> ActivationLockRecord | None:
        existing = self._load().get(target)
        if existing is None:
            return None
        try:
            return ActivationLockRecord(
                target=target,
                owner_id=existing.get("owner_id", ""),
                acquired_at=float(existing.get("acquired_at", 0)),
                expires_at=int(existing.get("expires_at", 0)),
            )
        except (ValueError, TypeError):
            return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def acquire_activation_lock(
    target: str,
    owner_id: str | None = None,
    ttl: int = 0,
) -> bool:
    """Atomically acquire activation lock for *target*.

    Returns True if the caller now holds the lock.  Returns False if another
    activator holds an unexpired lock or if the backend is unreachable
    (fail-closed).

    ``target`` identifies the deployment target (e.g. ``"trustforge-demo"``).

    ``owner_id`` defaults to ``"<pid>:<uuid>"`` when not supplied.

    ``ttl`` is in seconds; defaults to the value of env
    ``TRUSTFORGE_ACTIVATION_LOCK_TTL_SECONDS`` or 300.
    """
    if ttl <= 0:
        ttl = _default_ttl_seconds()
    if owner_id is None:
        owner_id = _new_owner_id()
    backend = _get_backend()
    try:
        return backend.acquire(target, owner_id, ttl)
    except Exception:
        logger.warning("Activation lock acquire: backend unreachable (fail-closed)", exc_info=True)
        return False


def release_activation_lock(target: str, owner_id: str) -> bool:
    """Release the activation lock for *target* if it is held by *owner_id*.

    Returns True if the release was acknowledged by the backend.  Returns
    False only when the inbound owner_id is not the current lock holder
    (harmless no-op).  Backend errors during release are treated as
    best-effort and return True (they do not block the caller).
    """
    backend = _get_backend()
    try:
        return backend.release(target, owner_id)
    except Exception:
        logger.warning("Activation lock release: backend unreachable", exc_info=True)
        return True


def get_activation_lock(target: str) -> ActivationLockRecord | None:
    """Return the current lock record for *target*, or None if no lock held."""
    backend = _get_backend()
    try:
        return backend.get(target)
    except Exception:
        logger.warning("Activation lock get: backend unreachable", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------

_BACKEND: _DynamoDBActivationLockBackend | _JsonActivationLockBackend | None = None


def _get_backend():
    global _BACKEND
    if _BACKEND is None:
        backend_name = os.getenv("TRUSTFORGE_ACTIVATION_LOCK_BACKEND", "json").strip().lower()
        if backend_name == "dynamodb":
            _BACKEND = _DynamoDBActivationLockBackend()
        else:
            _BACKEND = _JsonActivationLockBackend()
    return _BACKEND


def _set_backend_for_tests(backend):
    global _BACKEND
    _BACKEND = backend
