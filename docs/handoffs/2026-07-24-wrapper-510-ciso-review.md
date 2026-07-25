# Wrapper Artifact Lifecycle (#510) — CISO Review Handoff

**Branch:** `cto/510-wrapper-sandbox` (off `origin/develop` at `f959c17`)
**Base:** #503 read-only probe evaluator (already merged)
**Issue:** #510 (OPEN)
**Files:**
- `src/trustforge/wrapper_state_machine.py` (new, 117 lines)
- `src/trustforge/wrapper_artifact_control.py` (new, 817 lines)
- `tests/test_wrapper_state_machine.py` (new, 18 tests)
- `tests/test_wrapper_artifact_control.py` (new, 36 tests)

## 1. Background — what the abandoned line got wrong

`origin/feat/510-wrapper-artifact-sandbox` was CISO-FAIL for four reasons.
**None of that line is reused.** The new modules are greenfield. The four
failures and how this implementation closes each:

| CISO finding on old line | How #510 v2 prevents it |
|---|---|
| `is_human_approval_actor(actor)` — string blacklist | No blacklist anywhere. `ReviewerPrincipal` is a typed dataclass; the controller compares principals structurally and forbids self-approval by proposer or sandbox runner. |
| `probe_report.get("status") != "verified"` — caller-supplied dict | `activate()` takes raw `probe_observation` + `ProbeRequirement` and runs `evaluate_modelhub_readonly_probe` inline. A bare `{"status":"verified"}` evaluates to `unverified` and is rejected. |
| Activation can skip sandbox/approval | 8-state FSM with strict edges; `activate()` is reachable only from `review` state, which is reachable only through `request_approval()`, which requires an attached passing sandbox. |
| No checksum/snapshot/rollback_target binding | `compute_binding_checksum()` SHA-256s the canonical encoding of (proposal_id, candidate_artifact_id, candidate_payload_sha256, dataset_manifest_checksum, sandbox_run_id, sandbox_replay_checksum, config_snapshot_artifact_id, rollback_target_artifact_id, rollback_target_config_snapshot_id). The controller re-derives this in `activate()` and rejects drift. |

## 2. State machine

```
diagnostics → proposal → candidate_build → sandbox_replay
           → review → human_activation → monitoring
           → rollback (terminal)
```

- **No skipping:** `transition("proposal", "human_activation")` raises; the full table is in `tests/test_wrapper_state_machine.py::test_negative_table_every_disallowed_pair_raises`.
- **No reverse:** `transition("human_activation", "review")` raises.
- **Rollback** only from `human_activation` or `monitoring`.
- **`rollback` is terminal** — no state may follow it.

The state machine is pure (no I/O, no authorization); all security checks
live in `WrapperArtifactController`.

## 3. Authorization model (the part CISO will scrutinize most)

### Approval record lifecycle

```
request_approval(reviewer, config_snapshot, rollback_target, reason)
   → (validates reviewer not expired)
   → (validates reviewer != proposer && reviewer != sandbox_runner)
   → (validates rollback_target ∈ controller._approved_artifacts)
   → (validates rollback_target's config_snapshot still in registry)
   → (stores config_snapshot as immutable artifact)
   → (computes binding_checksum over 9-tuple)
   → (mints ApprovalRecord with secrets.token_urlsafe id)
   → (records in self._approvals journal; advances state to "review")
   → returns ApprovalRecord
```

The caller **never** constructs an `ApprovalRecord`. They receive one from
`request_approval` and pass it back to `activate`. The controller:

1. Looks up the approval by id in its journal (`self._approvals`).
2. Compares by identity (`recorded is not approval`) — rejects any
   reconstructed object even if the id matches.
3. Checks `approval_id not in self._consumed_approvals` — single-use.
4. Re-derives `compute_binding_checksum()` from current proposal state and
   compares against `approval.binding_checksum` — rejects drift.

### Anti-spoofing properties (adversarially verified)

A throwaway script in the dev worktree exercises all 5 attack vectors and
confirms each is blocked:

- Forged approval with known id → `approval record does not match journal entry`
- Bare `{"status":"verified"}` dict → `ModelHub probe did not verify (status='unverified')`
- Hand-constructed approval id → `approval record is not in the journal`
- Arbitrary rollback target id → `rollback_target_artifact_id is not a previously-approved`
- Self-approval (proposer=reviewer) → `reviewer is the same principal as the proposer`

## 4. ModelHub gate

`activate()` accepts `probe_observation` (raw dict) and `probe_requirement`
(typed `ProbeRequirement`), calls `evaluate_modelhub_readonly_probe` inline,
and only proceeds when the returned `status == "verified"`. The `disabled`
and `unverified` branches both raise `WrapperArtifactError`.

**Rollback deliberately skips the probe.** The rollback target was stored
locally at activation time, so rollback must succeed when ModelHub is
unreachable — fail-closed for forward, fail-safe for known-good rollback.

## 5. Self-assessed risk surface (for CISO review)

These are the points I'd ask harper to challenge:

1. **In-memory state.** `_proposals`, `_approvals`, `_consumed_approvals`,
   `_approved_artifacts` are in-memory dicts. A process restart loses them,
   and an in-flight proposal cannot be resumed. **This matches the no-DB
   design constraint of the issue**, but it means the controller is suitable
   for single-process control planes, not multi-replica HA. If we ever need
   HA, the journal and approval records would need to move to SQLite/append-
   only files like `upgrade_queue.py` uses. **Recommendation:** keep in-memory
   until the third track actually deploys, then plan a durable journal as a
   separate change.

2. **Principal authenticity is caller's responsibility.** The controller
   trusts that whoever instantiates `ReviewerPrincipal(subject, role, expires_at)`
   has actually authenticated `subject`. This module does not perform authn;
   it expects to sit behind an authenticated control-plane (the same model as
   `upgrade_ports.AuthenticatedPrincipal`). **Recommendation:** when wiring
   into production, document that the caller (CLI/API handler) must verify
   sessions before constructing `ReviewerPrincipal`.

3. **`clock` parameter for tests.** The controller accepts an optional
   `clock: datetime` for deterministic time-based tests. In production this
   is `None` and `_now()` returns real UTC. No production path passes a
   clock. **Risk:** minimal — `is_expired()` checks use `_now()` uniformly.

4. **Rollback target liveness.** `request_approval` verifies the rollback
   target's config_snapshot is in the registry at request time. If something
   evicts it from the registry between approval and activation, `activate`
   would currently succeed (it doesn't re-check registry presence for the
   rollback target) but `rollback` would fail in `_approved_artifacts`
   membership + `pointers.rollback()` (which calls `_require_artifact`).
   **Recommendation:** consider an explicit pre-activation registry liveness
   check for the rollback target. Filed as a follow-up risk, not a blocker.

5. **Single approval per proposal.** `request_approval` overwrites the
   proposal's `config_snapshot_artifact_id` and `rollback_target_*` on each
   call. The state-machine prevents this in practice (you can only
   `request_approval` once from `sandbox_replay`), but the journal keeps
   every minted approval. If two approvals were somehow minted for the same
   proposal, only the most recent binding would match `activate`'s
   re-derivation. **Risk:** low (state machine enforces it) but worth noting.

6. **No replay protection across controller instances.** Two controller
   instances with separate `_approvals` journals would each accept their own
   approvals. This is fine in the single-process model; if we ever share
   journals across processes we need a durable store (see #1).

## 6. Test coverage

- 18 FSM tests, 36 controller tests, all green.
- Full repo: 4084 passed, 6 skipped (no regressions).
- Coverage of new modules: `wrapper_state_machine.py` 100%, `wrapper_artifact_control.py` 92%.
- Repo coverage: 86.33% (gate threshold: 75%).

Test cases by CISO requirement:

| Requirement | Test(s) |
|---|---|
| Transition table (legal + illegal) | `test_wrapper_state_machine.py` (18 tests, including `test_negative_table_every_disallowed_pair_raises`) |
| Unauthorized activation | `test_activate_rejects_when_state_not_review`, `test_failed_sandbox_blocks_review_advance` |
| Approval spoofing | `test_activate_rejects_caller_forged_approval_with_known_id`, `test_activate_rejects_approval_from_different_proposal`, `test_activate_rejects_replay_of_consumed_approval`, `test_activate_rejects_caller_fabricated_verified_dict` |
| Sandbox isolation | `test_sandbox_replay_does_not_move_production_pointer`, `test_attach_sandbox_rejects_result_for_different_candidate` |
| Checksum/version mismatch | `test_create_proposal_rejects_checksum_version_mismatch`, `test_create_proposal_rejects_unregistered_candidate`, `test_activate_rejects_binding_drift_after_sandbox_replaced` |
| Provenance missing | `test_create_proposal_rejects_missing_provenance`, `test_create_proposal_rejects_naive_generated_at` |
| Activation failure + rollback drill | `test_rollback_succeeds_directly_from_human_activation`, `test_rollback_offline_restores_previous_artifact`, `test_two_phase_flow_first_activation_becomes_next_rollback_target` |
| ModelHub unverified → disabled | `test_activate_rejects_modelhub_unverified`, `test_activate_rejects_modelhub_disabled` |

## 7. Pre-push gate

All gates green:
- backend tests (4084 pass)
- data contracts (current)
- source stub scan (passed)
- competition QA (24 samples, ok)
- frontend tests (308 pass)
- frontend lint (oxlint, clean)
- frontend build (vite, ok)
- diff check (clean)

## 8. Out of scope (deliberately)

- No DB schema, no migration, no ModelHub writes.
- No production deploy, no push to main.
- No merge (CEO's call after CISO review).
- No reuse of the abandoned `feat/510-wrapper-artifact-sandbox` line.
