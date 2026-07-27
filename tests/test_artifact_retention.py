from __future__ import annotations

from datetime import datetime, timezone, timedelta

from trustforge.artifact_retention import (
    RetentionPolicy,
    apply_retention_policy,
    render_retention_report,
)


_now = datetime.now(timezone.utc)


def _make_entry(digest: str, ts: str, pointers: list[str] | None = None) -> dict:
    entry: dict = {"digest": digest, "timestamp": ts}
    if pointers:
        entry["pointers_referenced"] = pointers
    return entry


def test_protected_active_pointer() -> None:
    policy = RetentionPolicy(observation_window_hours=24, canary_window_minutes=10)
    entries = [
        _make_entry("aaaa", (_now - timedelta(hours=48)).isoformat(), ["pointers/active.json"]),
        _make_entry("bbbb", (_now - timedelta(hours=48)).isoformat()),
    ]
    protected = policy.protected_set(entries, now=_now)
    assert "aaaa" in protected
    assert "bbbb" not in protected


def test_protected_candidate_pointer() -> None:
    policy = RetentionPolicy()
    entries = [
        _make_entry("aaaa", _now.isoformat(), ["pointers/candidate.json"]),
    ]
    protected = policy.protected_set(entries, now=_now)
    assert "aaaa" in protected


def test_protected_previous_pointer() -> None:
    policy = RetentionPolicy()
    entries = [
        _make_entry("aaaa", (_now - timedelta(days=7)).isoformat(), ["pointers/previous.json"]),
    ]
    protected = policy.protected_set(entries, now=_now)
    assert "aaaa" in protected


def test_protected_observation_window() -> None:
    policy = RetentionPolicy(observation_window_hours=24, canary_window_minutes=1)
    entries = [
        _make_entry("recent", (_now - timedelta(hours=12)).isoformat()),
        _make_entry("old", (_now - timedelta(days=7)).isoformat()),
    ]
    protected = policy.protected_set(entries, now=_now)
    assert "recent" in protected
    assert "old" not in protected


def test_protected_canary_window() -> None:
    policy = RetentionPolicy(observation_window_hours=24, canary_window_minutes=10)
    recently_uploaded = _now - timedelta(minutes=5)
    entries = [
        _make_entry("canary", recently_uploaded.isoformat()),
    ]
    protected = policy.protected_set(entries, now=_now)
    assert "canary" in protected


def test_protected_missing_timestamp() -> None:
    policy = RetentionPolicy()
    entries = [
        _make_entry("nots", ""),
    ]
    protected = policy.protected_set(entries, now=_now)
    assert "nots" in protected


def test_apply_retention_policy_eligible() -> None:
    all_digests = ["aaaa", "bbbb", "cccc"]
    entries = [
        _make_entry("aaaa", (_now - timedelta(days=7)).isoformat()),
        _make_entry("bbbb", (_now - timedelta(days=7)).isoformat()),
    ]
    protected, eligible = apply_retention_policy(entries, all_digests, now=_now)
    assert len(protected) == 0
    assert "aaaa" in eligible
    assert "bbbb" in eligible
    assert "cccc" in eligible


def test_apply_retention_policy_pointer_not_in_all() -> None:
    all_digests = ["bbbb"]
    entries = [
        _make_entry("aaaa", (_now - timedelta(days=7)).isoformat(), ["pointers/active.json"]),
        _make_entry("bbbb", (_now - timedelta(days=7)).isoformat()),
    ]
    protected, eligible = apply_retention_policy(entries, all_digests, now=_now)
    assert "aaaa" not in protected  # pointer-referenced but not in all_digests
    assert "bbbb" in eligible  # old, no pointers, in all_digests
    assert len(protected) == 0


def test_apply_retention_policy_empty() -> None:
    protected, eligible = apply_retention_policy([], [], now=_now)
    assert len(protected) == 0
    assert len(eligible) == 0


def test_render_retention_report() -> None:
    protected = {"aaaa"}
    eligible = {"bbbb", "cccc"}
    entries = [
        _make_entry("bbbb", (_now - timedelta(days=30)).isoformat()),
        _make_entry("cccc", (_now - timedelta(days=30)).isoformat()),
    ]
    report = render_retention_report(protected, eligible, entries)
    assert "Protected artifacts: 1" in report
    assert "Eligible for deletion: 2" in report
    assert "bbbb" in report
    assert "cccc" in report


def test_retention_policy_custom_windows() -> None:
    policy = RetentionPolicy(observation_window_hours=1, canary_window_minutes=1)
    assert policy.observation_window_hours == 1
    assert policy.canary_window_minutes == 1
    entries = [
        _make_entry("recent", (_now - timedelta(minutes=55)).isoformat()),
        _make_entry("old", (_now - timedelta(hours=2)).isoformat()),
    ]
    protected = policy.protected_set(entries, now=_now)
    assert "recent" in protected  # 55min < 1h observation window
    assert "old" not in protected  # 2h > 1h obs window, >1min canary, no pointers
