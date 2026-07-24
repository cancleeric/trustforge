"""Tests for the read-only ModelHub probe collector (#503).

These tests use fake clients only; no real ModelHub is contacted.  They pin
the fail-closed contract: under the current ModelHub read-only API (no
tenant-scoped metadata endpoint, no cross-tenant negative probe, no artifact
checksum/provenance binding), the probe must never reach ``verified``.
"""
from __future__ import annotations

from typing import Any

import pytest

from trustforge.modelhub_client import ModelHubError, ModelHubTransportError
from trustforge.modelhub_probe_collector import (
    collect_readonly_observation,
    run_readonly_probe,
)
from trustforge.modelhub_readonly_probe import ProbeRequirement

REQUIREMENT = ProbeRequirement(
    tenant_id="tenant-a",
    product="trustforge",
    model_name="calibrator",
    artifact_id="artifact-123",
    artifact_sha256="a" * 64,
    provenance_id="prov-123",
)


class FakeClient:
    """Minimal read-only client double."""

    def __init__(self, *, health: bool = True, models: Any = None, list_error: Exception | None = None):
        self._health = health
        self._models = models if models is not None else [{"slug": "calibrator"}]
        self._list_error = list_error
        self.list_calls = 0

    def health_check(self) -> bool:
        return self._health

    def list_models(self):
        self.list_calls += 1
        if self._list_error is not None:
            raise self._list_error
        return self._models


# --- collect_readonly_observation --------------------------------------------


def test_collect_returns_health_and_client_capabilities_when_reachable():
    client = FakeClient(health=True)

    observation = collect_readonly_observation(client)

    assert observation == {
        "health_ok": True,
        "capabilities": ["health", "list_models", "get_model_path"],
    }
    assert client.list_calls == 1


def test_collect_reports_unavailable_when_health_check_false():
    """ModelHubClient.health_check() returns False only on ModelHubError, i.e. unreachable."""
    client = FakeClient(health=False)

    observation = collect_readonly_observation(client)

    assert observation == {"unavailable": True}
    assert client.list_calls == 0


@pytest.mark.parametrize(
    "error",
    [ModelHubTransportError("down"), ModelHubError("boom")],
)
def test_collect_traps_modelhub_error_as_unavailable(error):
    class FailingHealth(FakeClient):
        def health_check(self):
            raise error

    observation = collect_readonly_observation(FailingHealth())

    assert observation == {"unavailable": True}


def test_collect_reports_unavailable_when_catalog_read_fails_after_health_ok():
    client = FakeClient(health=True, list_error=ModelHubTransportError("catalog down"))

    observation = collect_readonly_observation(client)

    assert observation == {"unavailable": True}
    assert client.list_calls == 1


# --- run_readonly_probe: fail-closed contract under current ModelHub API -------


def test_probe_is_unverified_under_current_modelhub_contract():
    """Health ok + client capabilities alone cannot reach verified (#503)."""
    client = FakeClient(health=True)

    report = run_readonly_probe(client, REQUIREMENT)

    assert report["status"] == "unverified"
    assert report["read_only"] is True
    assert report["write_operations"] == []
    # The four evidence families ModelHub does not yet expose are all missing.
    components = report["components"]
    for missing in ("identity", "read_access", "artifact", "provenance"):
        assert components[missing]["status"] == "unverified", missing
    assert components["health"]["status"] == "verified"
    assert components["capability"]["status"] == "verified"


def test_probe_disables_when_modelhub_unreachable():
    client = FakeClient(health=False)

    report = run_readonly_probe(client, REQUIREMENT)

    assert report["status"] == "disabled"
    assert report["components"]["health"]["status"] == "disabled"
    assert report["components"]["health"]["reason"] == "modelhub_unavailable"


def test_probe_disables_when_transport_error_raises():
    class FailingHealth(FakeClient):
        def health_check(self):
            raise ModelHubTransportError("down")

    report = run_readonly_probe(FailingHealth(), REQUIREMENT)

    assert report["status"] == "disabled"
    assert report["components"]["health"]["reason"] == "modelhub_unavailable"


def test_probe_never_verifies_even_with_models_returned():
    """Returning a model catalog does not manufacture tenant/provenance evidence."""
    client = FakeClient(health=True, models=[{"slug": "btc-calibrator"}, {"slug": "eth-calibrator"}])

    report = run_readonly_probe(client, REQUIREMENT)

    assert report["status"] == "unverified"


# --- Read-only invariant: the collector must never touch state-changing ops ----


def test_collector_never_calls_state_changing_client_methods():
    """Pin the #503 contract: collecting evidence never mutates ModelHub."""

    class WriteSpy(FakeClient):
        def __init__(self):
            super().__init__(health=True)
            self.write_calls: list[str] = []

        def trigger_retrain(self, *_args, **_kwargs):
            self.write_calls.append("trigger_retrain")
            raise AssertionError("collector must not trigger_retrain")

        def poll_training_result(self, *_args, **_kwargs):
            self.write_calls.append("poll_training_result")
            raise AssertionError("collector must not poll_training_result")

    spy = WriteSpy()
    collect_readonly_observation(spy)

    assert spy.write_calls == []
    # Read-only methods were exercised.
    assert spy.list_calls == 1
