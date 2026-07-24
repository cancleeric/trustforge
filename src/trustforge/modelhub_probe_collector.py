"""Read-only ModelHub observation collector for the #503 re-verification probe.

This module bridges :class:`ModelHubClient` and
:func:`evaluate_modelhub_readonly_probe`.  It only ever calls read-only
client methods (``health_check`` / ``list_models``) and derives the
capability evidence from the operations the client itself supports; it never
invokes ``trigger_retrain`` or ``poll_training_result``.

Important — current ModelHub read-only contract (see the 2026-07-24 BLOCKED
comment on issue #503):

* ``GET /health`` returns 200.
* ``GET /v1/models`` returns 401 without a credential.
* The candidate ``GET /api/registry/latest`` documents API-key access as an
  admin bypass with **no tenant scope**.
* ``GET /api/external-models/{product}/{name}/path`` returns a path but does
  not expose a tenant-bound checksum or provenance contract.

Because the read-only contract exposes no tenant-scoped metadata endpoint,
no cross-tenant negative probe, and no artifact checksum/provenance binding,
this collector deliberately does **not** fabricate ``identity``,
``negative_read_checks``, ``artifact`` or ``provenance`` evidence.  Under the
current contract the collected observation therefore always evaluates to
``unverified`` — the fail-closed outcome required by #503.  When ModelHub
provides the four read-only capabilities listed in the BLOCKED comment, this
collector is the single place to extend; the evaluator contract is already
complete.
"""
from __future__ import annotations

from typing import Any, Protocol

from .modelhub_client import ModelHubClient, ModelHubError
from .modelhub_readonly_probe import ProbeRequirement, evaluate_modelhub_readonly_probe

# Capabilities the client exposes that a read-only probe is allowed to rely on.
# These are client-side facts (the operations the client implements), not a
# self-reported ModelHub capability advertisement endpoint — ModelHub exposes
# no such endpoint today.
_READONLY_CLIENT_CAPABILITIES = ("health", "list_models", "get_model_path")


class _ReadOnlyClient(Protocol):
    """Structural type for the read-only slice of :class:`ModelHubClient`."""

    def health_check(self) -> bool: ...
    def list_models(self) -> list[dict[str, Any]]: ...


def collect_readonly_observation(client: _ReadOnlyClient) -> dict[str, Any]:
    """Collect fail-closed read-only evidence from a ModelHub client.

    Returns an observation dict ready for
    :func:`evaluate_modelhub_readonly_probe`.  Transport failures are recorded
    as ``unavailable`` rather than raised, so that a transient ModelHub outage
    still produces a deterministic ``disabled`` probe verdict instead of an
    exception escaping to the caller.

    No tenant-scope, negative-access, artifact or provenance evidence is
    collected: the current ModelHub read-only contract does not expose the
    endpoints required to gather it honestly (see module docstring).
    """
    try:
        health_ok = bool(client.health_check())
    except ModelHubError:
        return {"unavailable": True}
    except Exception:  # pragma: no cover - defensive: client contract is ModelHubError
        return {"unavailable": True}

    if not health_ok:
        # ModelHubClient.health_check() returns False only when its read-only
        # catalog probe raised ModelHubError, i.e. the service is unreachable
        # or returned a bad response.  Map that to ``unavailable`` so the
        # evaluator fails closed (disabled) rather than reporting a mere
        # "health not verified" (unverified) state.
        return {"unavailable": True}

    try:
        client.list_models()  # confirm the catalog endpoint is callable read-only
    except ModelHubError:
        return {"unavailable": True}
    except Exception:  # pragma: no cover - defensive
        return {"unavailable": True}

    return {
        "health_ok": True,
        "capabilities": list(_READONLY_CLIENT_CAPABILITIES),
    }


def run_readonly_probe(
    client: _ReadOnlyClient,
    requirement: ProbeRequirement,
) -> dict[str, Any]:
    """Collect read-only evidence and evaluate it against ``requirement``.

    Convenience wrapper combining :func:`collect_readonly_observation` and
    :func:`evaluate_modelhub_readonly_probe`.  Returns the evaluator report;
    under the current ModelHub read-only contract the ``status`` is always
    ``unverified`` (or ``disabled`` when the service is unreachable).
    """
    observation = collect_readonly_observation(client)
    return evaluate_modelhub_readonly_probe(observation, requirement)


__all__ = [
    "ModelHubClient",
    "ProbeRequirement",
    "collect_readonly_observation",
    "evaluate_modelhub_readonly_probe",
    "run_readonly_probe",
]
