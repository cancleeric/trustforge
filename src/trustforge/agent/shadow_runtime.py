"""Bounded, non-authoritative shadow observation runtime.

This module may execute the candidate kernel only when both explicit feature
flags and the complete durable-store release identity are configured.  It
never returns a value that can replace the active legacy result and exposes no
activation or promotion operation.
"""
from __future__ import annotations

import multiprocessing
import os
import threading
import time
from functools import partial
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable, Sequence

from trustforge_core import KernelOutput, run_kernel

from .kernel_mapper import to_kernel_input
from .shadow import ShadowParityResult, compare_outputs
from .shadow_contracts import (
    ShadowInput,
    ShadowObservation,
    input_digest,
    load_policy,
)
from .shadow_identity import measured_release_identity, verify_reviewed_loaded_candidate

_ENABLED_VALUES = frozenset({"1", "true", "yes", "on"})
_RUNTIME_FLAG = "TRUSTFORGE_SHADOW_RUNTIME_ENABLED"
_OBSERVE_FLAG = "KERNEL_SHADOW_OBSERVE"
_HARD_TIMEOUT_MS = 1_000.0
_SINGLE_FLIGHT = threading.BoundedSemaphore(value=1)
_POISON_LOCK = threading.Lock()
_SHADOW_RUNTIME_POISONED = False
_CHILD_ENV_NAMES = (
    "TRUSTFORGE_SHADOW_DEDICATED_RUNTIME",
    "TRUSTFORGE_SHADOW_RUNTIME_ATTESTATION_PATH",
    "TRUSTFORGE_SHADOW_DB_PATH",
    "TRUSTFORGE_SHADOW_ACTIVE_RELEASE",
    "TRUSTFORGE_SHADOW_CANDIDATE_RELEASE",
    "TRUSTFORGE_SHADOW_ACTIVE_ARTIFACT_DIGEST",
    "TRUSTFORGE_SHADOW_CANDIDATE_ARTIFACT_DIGEST",
)


@dataclass(frozen=True, slots=True)
class ShadowRuntimeResult:
    """Diagnostics only; callers must not consume this as an active result."""

    status: str
    elapsed_ms: float = 0.0
    kernel_output: KernelOutput | None = None
    parity: ShadowParityResult | None = None
    observation_event_id: str | None = None
    provider_calls: int = 0
    cost_usd: float = 0.0
    diagnostic: str | None = None


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _ENABLED_VALUES


def _reject_unresolvable_spawn_callable(value: object) -> None:
    """Reject callables known to be unresolvable by a fresh interpreter."""
    if isinstance(value, partial):
        _reject_unresolvable_spawn_callable(value.func)
        return
    qualname = getattr(value, "__qualname__", "")
    if "<locals>" in qualname or getattr(value, "__name__", "") == "<lambda>":
        raise TypeError("spawn callable must be module-resolvable")


def _configured_identity():
    policy = load_policy()
    measured = measured_release_identity(policy)
    return (
        measured.identity,
        policy,
        measured.candidate_contract_version,
    )


def _cleanup_process(process) -> bool:
    """Best-effort cleanup that can never interfere with lease release."""
    if process is None:
        return True
    try:
        if process.is_alive():
            process.kill()
            process.join(timeout=0.1)
    except Exception:
        return False
    try:
        reaped = not process.is_alive()
    except Exception:
        return False
    if not reaped:
        return False
    try:
        process.close()
    except Exception:
        pass
    return True


def _observation_worker(
    send_connection,
    deadline,
    started,
    hard_timeout_ms,
    claims,
    scored,
    legacy_confidence,
    legacy_trust_raw,
    coin,
    question_type,
    query,
    request_id,
    pit_epoch,
    observed_epoch,
    monotonic_fn,
    kernel_fn,
    mapper_fn,
    store_factory,
    child_environment,
    use_measured_candidate,
) -> None:
    """Spawn-safe candidate boundary; all effects remain inside this process."""
    store = None
    try:
        os.environ.clear()
        for name, value in child_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        try:
            identity, policy, candidate_contract_version = _configured_identity()
            if use_measured_candidate:
                verify_reviewed_loaded_candidate(
                    kernel_fn, mapper_fn, candidate_contract_version,
                )
            if store_factory is None:
                from .shadow_evidence_store import ShadowEvidenceStore

                store_factory = ShadowEvidenceStore
            remaining_ms = max(1, int((deadline - monotonic_fn()) * 1000.0))
            store = store_factory(
                busy_timeout_ms=min(int(hard_timeout_ms), remaining_ms),
            )
        except Exception:
            send_connection.send(ShadowRuntimeResult(
                status="not_observed",
                elapsed_ms=min(
                    max(0.0, (monotonic_fn() - started) * 1000.0),
                    hard_timeout_ms,
                ),
            ))
            return
        if monotonic_fn() > deadline:
            return
        canonical_input = ShadowInput(
            request_id=request_id, coin=coin, question_type=question_type,
            pit_epoch=pit_epoch, query=query,
        )
        kernel_input = mapper_fn(
            claims, pit_epoch=pit_epoch, coin=coin, query=query,
        )
        output = kernel_fn(kernel_input)
        parity = compare_outputs(
            output,
            legacy_confidence=legacy_confidence,
            legacy_trust_raw=legacy_trust_raw,
            legacy_scored=scored,
            coin=coin,
            qtype_value=question_type,
        )
        elapsed_ms = max(0.0, (monotonic_fn() - started) * 1000.0)
        if monotonic_fn() > deadline:
            return
        observed_at = datetime.fromtimestamp(
            observed_epoch, tz=timezone.utc,
        ).isoformat().replace("+00:00", "Z")
        observation = ShadowObservation(
            release_identity=identity,
            canonical_input=canonical_input,
            input_digest=input_digest({
                "request_id": canonical_input.request_id,
                "coin": canonical_input.coin,
                "question_type": canonical_input.question_type,
                "pit_epoch": canonical_input.pit_epoch,
                "query": canonical_input.query,
            }),
            observed_at=observed_at,
            status="success",
            parity_passed=parity.parity_passed,
            confidence_delta=parity.delta_confidence,
            trust_delta=parity.delta_trust,
            supporting_jaccard=parity.supporting_jaccard,
            elapsed_ms=elapsed_ms,
            provider_calls=0,
            cost_usd=0.0,
            claim_ids=tuple(claim.id for claim in claims),
        )
        event_id = store.observation_event_id(observation)
        store.record_policy_and_observation(
            policy,
            event_id,
            observation,
            commit_guard=lambda: monotonic_fn() <= deadline,
        )
        operational_elapsed_ms = max(
            0.0, (monotonic_fn() - started) * 1000.0,
        )
        store.record_observation_completion(
            event_id,
            operational_elapsed_ms,
            commit_guard=lambda: monotonic_fn() <= deadline,
        )
        if monotonic_fn() > deadline:
            return
        store.close()
        store = None
        send_connection.send(ShadowRuntimeResult(
            status="success", elapsed_ms=operational_elapsed_ms, kernel_output=output,
            parity=parity, observation_event_id=event_id,
        ))
    except Exception:
        elapsed_ms = max(0.0, (monotonic_fn() - started) * 1000.0)
        try:
            send_connection.send(ShadowRuntimeResult(
                status="error", elapsed_ms=min(elapsed_ms, hard_timeout_ms),
            ))
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        if store is not None:
            try:
                store.close()
            except Exception:
                pass
        send_connection.close()


def observe_candidate(
    *,
    claims: Sequence,
    scored: list,
    legacy_confidence: float,
    legacy_trust_raw: float,
    coin: str,
    question_type: str,
    query: str,
    request_id: str,
    pit_epoch: float,
    observed_epoch: float,
    monotonic_fn: Callable[[], float] = time.monotonic,
    kernel_fn: Callable = run_kernel,
    mapper_fn: Callable = to_kernel_input,
    store_factory: Callable[..., object] | None = None,
) -> ShadowRuntimeResult:
    """Attempt one bounded observation without affecting the active pipeline.

    Configuration, mapper, kernel, schema, timeout, and store failures are
    deliberately collapsed to non-authoritative diagnostic states.
    """
    global _SHADOW_RUNTIME_POISONED
    if not (_enabled(_RUNTIME_FLAG) and _enabled(_OBSERVE_FLAG)):
        return ShadowRuntimeResult(status="not_observed")
    with _POISON_LOCK:
        if _SHADOW_RUNTIME_POISONED:
            return ShadowRuntimeResult(
                status="not_observed", diagnostic="runtime_poisoned_unreaped_child",
            )
    if not _SINGLE_FLIGHT.acquire(blocking=False):
        return ShadowRuntimeResult(status="not_observed")

    started = monotonic_fn()
    deadline = started + (_HARD_TIMEOUT_MS / 1000.0)
    # forkserver never forks the multi-threaded web process itself.  It keeps
    # process startup substantially below a fresh spawn while retaining a
    # terminable isolation boundary.
    context = multiprocessing.get_context("forkserver")
    receive = send = None
    process = None

    try:
        receive, send = context.Pipe(duplex=False)
        worker_args = (
            send, deadline, started, _HARD_TIMEOUT_MS, claims, scored,
            legacy_confidence, legacy_trust_raw, coin, question_type,
            query, request_id, pit_epoch, observed_epoch, monotonic_fn,
            kernel_fn, mapper_fn, store_factory,
            {name: os.environ.get(name) for name in _CHILD_ENV_NAMES},
            kernel_fn is run_kernel and mapper_fn is to_kernel_input,
        )
        # Fail predictable local-function errors before a child can exist.
        # Do not pickle the complete arguments here: valid multiprocessing
        # synchronization primitives may only be serialized during spawn.
        for spawn_callable in (monotonic_fn, kernel_fn, mapper_fn, store_factory):
            if spawn_callable is not None:
                _reject_unresolvable_spawn_callable(spawn_callable)
        process = context.Process(
            target=_observation_worker,
            args=worker_args,
            name="trustforge-shadow-observation",
            daemon=True,
        )
        process.start()
        send.close()
        send = None
        remaining = max(0.0, deadline - monotonic_fn())
        if receive.poll(remaining):
            result = receive.recv()
            if process.is_alive():
                process.terminate()
                process.join(timeout=0.1)
            total_elapsed_ms = max(0.0, (monotonic_fn() - started) * 1000.0)
            return replace(
                result,
                elapsed_ms=min(total_elapsed_ms, _HARD_TIMEOUT_MS),
            )
        process.terminate()
        process.join(timeout=0.1)
        if process.is_alive():
            process.kill()
            process.join(timeout=0.1)
        return ShadowRuntimeResult(status="timeout", elapsed_ms=_HARD_TIMEOUT_MS)
    except Exception:
        return ShadowRuntimeResult(
            status="error",
            elapsed_ms=min(
                max(0.0, (monotonic_fn() - started) * 1000.0),
                _HARD_TIMEOUT_MS,
            ),
        )
    finally:
        confirmed_reaped = False
        try:
            try:
                if send is not None:
                    send.close()
            except Exception:
                pass
            try:
                if receive is not None:
                    receive.close()
            except Exception:
                pass
            confirmed_reaped = _cleanup_process(process)
        finally:
            if confirmed_reaped:
                _SINGLE_FLIGHT.release()
            else:
                # An unproved-dead child may still write.  Retain the lease and
                # poison observation for this process lifetime.
                with _POISON_LOCK:
                    _SHADOW_RUNTIME_POISONED = True
