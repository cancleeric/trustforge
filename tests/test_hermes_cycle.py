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
    monkeypatch.setattr(hermes_cycle, "autonomous_cycle_plan", lambda coins: _plan())
    monkeypatch.setattr(hermes_cycle, "manifest", lambda: {"skill": "test"})
    monkeypatch.setattr(
        hermes_cycle.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=7),
    )

    # refresh is degraded, then the snapshot failure is terminal.
    assert hermes_cycle.main(["--max-budget-sec", "10"]) == 7
