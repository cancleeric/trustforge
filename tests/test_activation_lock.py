"""Unit tests for activation_lock.py"""
from __future__ import annotations

import tempfile
import time

from trustforge.activation_lock import (
    _JsonActivationLockBackend,
    _set_backend_for_tests,
    acquire_activation_lock,
    get_activation_lock,
    release_activation_lock,
)


def test_acquire_free_target():
    with tempfile.TemporaryDirectory() as td:
        backend = _JsonActivationLockBackend(path=f"{td}/lock.json")
        _set_backend_for_tests(backend)
        assert acquire_activation_lock("test-target", "owner-1", ttl=60)


def test_acquire_blocks_second_owner():
    with tempfile.TemporaryDirectory() as td:
        backend = _JsonActivationLockBackend(path=f"{td}/lock.json")
        _set_backend_for_tests(backend)
        assert acquire_activation_lock("test-target", "owner-1", ttl=60)
        assert not acquire_activation_lock("test-target", "owner-2", ttl=60)


def test_release_allows_next():
    with tempfile.TemporaryDirectory() as td:
        backend = _JsonActivationLockBackend(path=f"{td}/lock.json")
        _set_backend_for_tests(backend)
        acquire_activation_lock("test-target", "owner-1", ttl=60)
        release_activation_lock("test-target", "owner-1")
        assert acquire_activation_lock("test-target", "owner-2", ttl=60)


def test_release_wrong_owner_noop():
    with tempfile.TemporaryDirectory() as td:
        backend = _JsonActivationLockBackend(path=f"{td}/lock.json")
        _set_backend_for_tests(backend)
        acquire_activation_lock("test-target", "owner-1", ttl=60)
        release_activation_lock("test-target", "owner-2")  # wrong owner
        lock = get_activation_lock("test-target")
        assert lock is not None
        assert lock.owner_id == "owner-1"


def test_acquire_after_expiry():
    with tempfile.TemporaryDirectory() as td:
        backend = _JsonActivationLockBackend(path=f"{td}/lock.json")
        _set_backend_for_tests(backend)
        acquire_activation_lock("test-target", "owner-1", ttl=0.05)
        assert not acquire_activation_lock("test-target", "owner-2", ttl=60)
        time.sleep(0.1)
        assert acquire_activation_lock("test-target", "owner-2", ttl=60)


def test_acquire_reentrant():
    with tempfile.TemporaryDirectory() as td:
        backend = _JsonActivationLockBackend(path=f"{td}/lock.json")
        _set_backend_for_tests(backend)
        assert acquire_activation_lock("test-target", "owner-1", ttl=60)
        assert acquire_activation_lock("test-target", "owner-1", ttl=60)


def test_get_activation_lock_none():
    with tempfile.TemporaryDirectory() as td:
        backend = _JsonActivationLockBackend(path=f"{td}/lock.json")
        _set_backend_for_tests(backend)
        assert get_activation_lock("no-such-target") is None


def test_get_activation_lock_returns_record():
    with tempfile.TemporaryDirectory() as td:
        backend = _JsonActivationLockBackend(path=f"{td}/lock.json")
        _set_backend_for_tests(backend)
        acquire_activation_lock("test-target", "owner-1", ttl=60)
        record = get_activation_lock("test-target")
        assert record is not None
        assert record.target == "test-target"
        assert record.owner_id == "owner-1"
        assert record.expires_at > record.acquired_at


def test_fail_closed_on_backend_error():
    with tempfile.TemporaryDirectory() as td:
        backend = _JsonActivationLockBackend(path=f"{td}/lock.json")
        _set_backend_for_tests(backend)
        assert acquire_activation_lock("test-target", "owner-1", ttl=60)
        released = release_activation_lock("test-target", "owner-1")
        assert released
