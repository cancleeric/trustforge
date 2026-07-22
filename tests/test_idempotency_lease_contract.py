"""Generic idempotency lease provider contract tests (#417)."""
from __future__ import annotations

import threading

from trustforge.idempotency_lease import (
    IdempotencyLeaseProvider,
    InMemoryIdempotencyLeaseProvider,
    JsonLeaseBackend,
    LeaseBackendIdempotencyAdapter,
)


def test_memory_provider_is_runtime_checkable():
    provider = InMemoryIdempotencyLeaseProvider()

    assert isinstance(provider, IdempotencyLeaseProvider)


def test_acquire_follow_complete_allows_next_leader():
    provider = InMemoryIdempotencyLeaseProvider()

    first = provider.acquire("expensive-analysis", "owner-a", 30, now=100.0)
    follower = provider.follow("expensive-analysis", now=101.0)

    assert first.role == "leader"
    assert first.handle is not None
    assert follower.role == "follower"
    assert follower.handle == first.handle
    assert follower.retry_after_seconds == 29.0

    provider.complete(first.handle)
    second = provider.acquire("expensive-analysis", "owner-b", 30, now=102.0)

    assert second.role == "leader"
    assert second.handle is not None
    assert second.handle.owner_id == "owner-b"
    assert provider.terminal_events == [
        {"key": "expensive-analysis", "owner_id": "owner-a", "status": "completed"}
    ]


def test_fail_releases_without_allowing_stale_owner_to_clear_new_leader():
    provider = InMemoryIdempotencyLeaseProvider()
    first = provider.acquire("k", "owner-a", 1, now=100.0)
    assert first.handle is not None
    assert provider.expire("k", now=102.0)
    second = provider.acquire("k", "owner-b", 30, now=102.0)
    assert second.handle is not None

    provider.fail(first.handle, "late failure")
    follower = provider.follow("k", now=103.0)

    assert follower.handle == second.handle
    assert provider.terminal_events == [{"key": "k", "status": "expired"}]


def test_race_has_one_leader_and_bounded_followers():
    provider = InMemoryIdempotencyLeaseProvider()
    results = []
    lock = threading.Lock()

    def acquire(owner: str) -> None:
        decision = provider.acquire("shared", owner, 60, now=10.0)
        with lock:
            results.append(decision)

    threads = [threading.Thread(target=acquire, args=(f"owner-{idx}",)) for idx in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    leaders = [result for result in results if result.role == "leader"]
    followers = [result for result in results if result.role == "follower"]

    assert len(leaders) == 1
    assert len(followers) == 11
    assert all(result.reason == "lease_held" for result in followers)


def test_backend_adapter_fails_closed_when_backend_is_uncertain():
    class UncertainBackend:
        def try_acquire(self, key: str, owner_id: str, ttl_seconds: int) -> bool:
            raise OSError("backend unavailable")

        def release(self, key: str, owner_id: str) -> None:
            raise AssertionError("release should not be called")

        def is_held(self, key: str) -> bool:
            return True

    adapter = LeaseBackendIdempotencyAdapter(UncertainBackend())  # type: ignore[arg-type]

    decision = adapter.acquire("k", "owner", 30, now=1.0)

    assert decision.role == "follower"
    assert decision.handle is None
    assert decision.reason == "lease_held_or_backend_uncertain"


def test_backend_adapter_wraps_json_local_backend(tmp_path):
    backend = JsonLeaseBackend(tmp_path / "leases.json")
    adapter = LeaseBackendIdempotencyAdapter(backend)

    first = adapter.acquire("k", "owner-a", 30, now=1.0)
    second = adapter.acquire("k", "owner-b", 30, now=2.0)

    assert first.role == "leader"
    assert first.handle is not None
    assert second.role == "follower"
    assert backend.is_held("k")

    adapter.complete(first.handle)
    assert adapter.acquire("k", "owner-b", 30, now=3.0).role == "leader"
