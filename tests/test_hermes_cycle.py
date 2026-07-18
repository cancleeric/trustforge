"""Regression coverage for bounded Hermes scheduler degradation behavior."""
from __future__ import annotations

from types import SimpleNamespace

from scripts import hermes_cycle


def _plan():
    return {
        "actions": [
            {"tool": "refresh_sources", "argv": ["refresh.py"]},
            {"tool": "build_snapshots", "argv": ["snapshot.py"]},
        ]
    }


def test_partial_refresh_failure_does_not_stop_bounded_cycle(monkeypatch):
    monkeypatch.setattr(hermes_cycle, "runtime_control", lambda: SimpleNamespace(enabled=True, source="test"))
    monkeypatch.setattr(hermes_cycle, "autonomy_enabled", lambda: (True, "test"))
    monkeypatch.setattr(hermes_cycle, "autonomous_cycle_plan", lambda coins: _plan())
    monkeypatch.setattr(hermes_cycle, "manifest", lambda: {"skill": "test"})
    returns = iter([1, 0])
    monkeypatch.setattr(
        hermes_cycle.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=next(returns)),
    )

    assert hermes_cycle.main(["--max-budget-sec", "10"]) == 0


def test_non_refresh_failure_stops_cycle(monkeypatch):
    monkeypatch.setattr(hermes_cycle, "runtime_control", lambda: SimpleNamespace(enabled=True, source="test"))
    monkeypatch.setattr(hermes_cycle, "autonomy_enabled", lambda: (True, "test"))
    monkeypatch.setattr(hermes_cycle, "autonomous_cycle_plan", lambda coins: _plan())
    monkeypatch.setattr(hermes_cycle, "manifest", lambda: {"skill": "test"})
    monkeypatch.setattr(
        hermes_cycle.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=7),
    )

    # refresh is degraded, then the snapshot failure is terminal.
    assert hermes_cycle.main(["--max-budget-sec", "10"]) == 7


def test_disabled_autonomy_skips_all_cycle_actions(monkeypatch):
    monkeypatch.setattr(hermes_cycle, "runtime_control", lambda: SimpleNamespace(enabled=True, source="test"))
    monkeypatch.setattr(hermes_cycle, "autonomy_enabled", lambda: (False, "config"))
    monkeypatch.setattr(
        hermes_cycle.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run actions")),
    )

    assert hermes_cycle.main(["--max-budget-sec", "10"]) == 0


def test_runtime_stop_skips_before_autonomy_check(monkeypatch):
    monkeypatch.setattr(hermes_cycle, "runtime_control", lambda: SimpleNamespace(enabled=False, source="state_file"))
    monkeypatch.setattr(
        hermes_cycle,
        "autonomy_enabled",
        lambda: (_ for _ in ()).throw(AssertionError("must not read autonomy after runtime stop")),
    )

    assert hermes_cycle.main(["--max-budget-sec", "10"]) == 0
