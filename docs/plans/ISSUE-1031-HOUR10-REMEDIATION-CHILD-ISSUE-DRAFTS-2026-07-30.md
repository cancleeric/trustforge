# Issue #1031 hour-10 remediation child issue drafts

These are CEO-approved issue drafts. CEO approved the exact preapproval plan
commit `7042745b93d928d9ef392338b97edf49472353f5` on 2026-07-30 and authorized
issue creation. Replace draft dependency names with issue numbers after
creation. Implementation has not started.

The rejected commit
`1c6c8e6d641f7c9e004db5c3d8d5ce7b7a351310` is void completion evidence.
#1031 remains OPEN/BLOCK.

## Draft A1 — concrete real-process ingress and fault driver

**Title:** `test(release): concrete nginx AF_UNIX two-Handler fault driver`

- Parent: #1031
- Estimate: 10–12 hours
- Depends on: #1014, #1020, #1021 contracts
- Blocks: Draft A2, Draft A3 and Draft A4

### Goal

Own and observe the real nginx → AF_UNIX → release router → two distinct
immutable real `trustforge.web.Handler` process chain, including bounded
candidate fault injection.

### Acceptance criteria

- [ ] Exact nginx/AF_UNIX/router path fronts two distinct immutable artifacts
  running real `web.Handler`.
- [ ] Direct handler/socket probes cannot satisfy scenario success.
- [ ] One bounded supervisor starts, health-checks and cleans up all processes.
- [ ] Real HTTP Analyze/Compare observations preserve canonical request fields
  and ordered comparison assets.
- [ ] Malformed, 5xx and timeout faults occur at the real B boundary and return
  observed A fallback.
- [ ] Observations derive from real HTTP/process/socket artifacts, not callbacks
  or caller-created digests.
- [ ] Caller cannot assert `REAL_LINUX_OBSERVED`; unavailable verified Linux
  returns `BLOCKED_EXTERNAL_LINUX`.
- [ ] Fixtures are explicitly `SYNTHETIC_NON_RELEASE`.
- [ ] Hour-10 review; hard stop and split at 12 hours.

### Review

gray + Harper + `/codex-review`; exact-commit pre-push. Eye N/A.

## Draft A2 — transcript v2 typed signed-ledger actual-record proofs

**Title:** `security(release): transcript v2 actual-record inclusion and conservation proofs`

- Parent: #1031
- Estimate: 8–10 hours
- Depends on: Draft A1 and #1032 evidence-input contract
- Blocks: Draft A4

### Goal

Derive the transcript from actual authenticated ledger records and
independently recompute inclusion, conservation, cap and causal barrier claims.

### Acceptance criteria

- [ ] Typed routing/budget/result/reconciliation/control/stop records are
  authenticated and inclusion-proven against before/after signed heads.
- [ ] Canonical request, cohort, ramp, release, epoch, control and route are
  recomputed from record bytes.
- [ ] Per-scenario and cumulative reservation/result/reconciliation
  conservation is recomputed; orphan/duplicate/splice evidence BLOCKS.
- [ ] Request, model-call and microusd cap barriers are distinct and each proves
  exact limits, reconciled totals and zero new B.
- [ ] Malformed/5xx/timeout bind non-200 candidate observation, durable failure,
  A fallback and no successful B outcome.
- [ ] Autostop causally orders trigger → durable barrier → zero later B.
- [ ] Drift binds unequal control heads and zero reservation/result/outcome/
  reconciliation delta.
- [ ] The transcript cannot sign, publish or choose PASS.
- [ ] Hour-10 review; hard stop at 12 hours.

### Review

Security/cost-sensitive: Harper + gray + `/codex-review`; exact-commit
pre-push. Eye N/A.

## Draft A3 — descriptor-pinned supervisor and recursive redaction

**Title:** `security(release): descriptor-pinned ingress supervisor and normalized redaction`

- Parent: #1031
- Estimate: 6–8 hours
- Depends on: Draft A1
- Blocks: Draft A4

### Goal

Keep verified commands/artifacts/configs pinned through launch and prevent all
sensitive names or values from escaping through nested harness output.

### Acceptance criteria

- [ ] Executables/artifacts/manifests/configs use no-follow descriptor-pinned
  verification through process launch.
- [ ] Symlink, hardlink, writable input, race and path-swap attacks fail closed.
- [ ] Launched process identity binds the pinned artifact and expected role.
- [ ] Environment/argv are allowlisted and cannot contain evidence signing
  material.
- [ ] Redaction normalizes case/separators and recursively protects headers,
  mappings, sequences, exceptions and diagnostics.
- [ ] Authorization/cookie/token/api-key/password/secret/private-key/identity/
  raw-query names and values are absent or domain-separated.
- [ ] Secret-canary property and adversarial tests cover success and failure
  output.
- [ ] Hour-10 review; hard stop at 12 hours.

### Review

Security-sensitive: Harper + gray + `/codex-review`; exact-commit pre-push.
Eye N/A.

## Draft A4 — external Linux S01–S12 ratio and terminal bucket execution

**Title:** `test(release): external Linux S01-S12 ratio and terminal bucket evidence`

- Parent: #1031
- Estimate: 6–10 hours
- Depends on: Draft A1, Draft A2, Draft A3 and #1033
- Blocks: #1031 acceptance and #1019 closure

### Goal

Execute all approved scenarios on the exact #1033-verified Linux topology and
prove real A/B cohort ratios, cap/stop barriers and complete terminal 100% A
routing-bucket coverage.

### Acceptance criteria

- [ ] #1033 provenance is verified and bound; no caller flag or synthetic
  receipt can substitute.
- [ ] S01–S12 execute exactly once and in order through real ingress.
- [ ] S04/S05 are real B 2xx Analyze/Compare with exact cohort/budget/ramp/
  epoch and ordered assets.
- [ ] Bounded real traffic proves both A and B cohort counts and observed ratio.
- [ ] Request/model-call/microusd barriers each prove zero post-barrier B.
- [ ] Fault and autostop cases satisfy Draft A2 causal proofs.
- [ ] S12 covers every configured terminal routing bucket for Analyze and
  Compare after stop/disabled/rollback; routes are 100% A and every B-related
  ledger delta is zero.
- [ ] Exported evidence passes normalized redaction checks.
- [ ] Missing Linux, skipped scenario or incomplete bucket coverage yields
  `BLOCKED_EXTERNAL_LINUX`/`BLOCK`, never PASS.
- [ ] Hour-10 review; hard stop at 12 hours.

### Review

Release/security/cost-sensitive: Harper + gray + `/codex-review`;
exact-commit pre-push. Eye N/A unless a visible operator surface changes.

## Approval and execution stop

**CEO APPROVED on 2026-07-30 — issue creation authorized.**

Approval is bound to preapproval commit
`7042745b93d928d9ef392338b97edf49472353f5` and covers the A1–A4 split,
dependencies, estimates, P0/P1 mappings, rejected/void `1c6c8e6` disposition,
#1031 OPEN/BLOCK state and non-overridable external Linux gate. It authorizes
issue creation only. No implementation, push, merge, deployment or #1031
closure is authorized by this approval; implementation must later start in
dependency order under the normal development workflow.
