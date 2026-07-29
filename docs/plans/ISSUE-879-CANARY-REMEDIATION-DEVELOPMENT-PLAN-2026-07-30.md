# #879 Canary Remediation Development Plan

- Owner: gray (CPO)
- Date: 2026-07-30
- Parent: #879
- Audit dependency: #998
- Baseline: `develop` includes #996 and #997
- Status: CEO approved for implementation on 2026-07-30

## 1. Decision

The former K1 receipt-to-canary authorization bridge is complete in #997 and
must not be reimplemented. #879 remains open because the earlier canary
controller used hermetic temporary servers and in-memory state. That evidence
does not prove that the real allowlisted Analyze and Compare HTTP workflows can
route through A/B, stop synchronously, remain pinned to A, or produce a
release-grade signed audit.

The remediation is split into four sequential deliverables. Every issue is
estimated at no more than 12 hours:

| Work item | Estimate | Dependency | Outcome |
| --- | ---: | --- | --- |
| K2 | 10h | #997 merged | Real allowlisted Analyze/Compare HTTP canary routing |
| K3 | 8h | K2 | Synchronous stop, route-to-A rollback, and A health proof |
| K4 | 10h | K2, K3 | Actual canary/post-promotion monitor; stop never promotes |
| K5 | 12h | K2, K3, K4, #998, current #875 PASS | Signed audit, real HTTP E2E, release gate |

K2–K4 may be implemented and verified in non-production even while the data
promotion gate remains `BLOCK`. K5 cannot produce a release-ready disposition
until #998 is complete and a current authenticated #875 PASS receipt is
eligible through #997.

## 2. Shared invariants

1. Only explicitly allowlisted Analyze and Compare requests may reach B.
   All other paths, assets, tenants, callers, and missing/invalid identities
   route to A.
2. A remains the immutable fallback. B transport errors, timeout, invalid
   response, health failure, stop condition, or unverifiable state must fail
   closed to A.
3. A stop may stop expansion and roll traffic back to A. It must never
   auto-promote B.
4. Promotion remains a separate, explicit, dual-authorized release action.
   These issues do not create an automatic promotion path.
5. Production claims require actual signed ledgers, immutable release
   manifests, real HTTP ingress, real Analyze/Compare responses, and exact
   commit evidence. Fixtures, monkeypatched transports, temporary servers,
   in-memory ledgers, and synthetic receipts are test evidence only.
6. HTTP tests must use bounded timeouts, request budgets, concurrency limits,
   deterministic cleanup, and redacted logs. Secrets, authorization payloads,
   or private keys must not appear in URLs, argv, environment dumps, logs, or
   test artifacts.
7. The current #875 decision is authoritative. If it is `BLOCK`, stale,
   missing, future-dated, or fails PIT/binding checks, the system stays on A
   and the milestone closes honestly as `remain-shadow/BLOCK`, not
   “production complete”.

## 3. K2 — real allowlisted Analyze/Compare HTTP canary routing

### Scope

Wire the existing release A/B router and #997 verified canary state into the
real HTTP entry points for Analyze and Compare. The allowlist must bind the
request identity, endpoint, asset scope, release/ramp identity, and authenticated
control snapshot. Routing must occur before the actual application handler is
invoked and must preserve current API contracts.

### Acceptance criteria

- Real Analyze and Compare HTTP entry points consume the authenticated durable
  routing snapshot; no parallel in-memory controller is authoritative.
- Only configured allowlisted request identities and asset scopes are eligible
  for B. Missing, malformed, replayed, non-allowlisted, or unsupported requests
  route to A.
- Stable routing is deterministic for the same subject and signed ramp policy.
- B selection is capped by the signed ratio/request budget and records the
  existing durable reservation/result outcome.
- B timeout, connection error, HTTP 5xx, invalid schema, or outcome-recording
  failure returns or retries through A according to the existing public
  contract, without leaking B-only state.
- Analyze and Compare both have real non-network integration coverage and
  bounded real-HTTP tests against the actual application entry points.
- Byte-/schema-compatible A behavior is proven when the canary is disabled.
- Test fixtures are labeled `fixture` or `test`; none are accepted as
  production evidence.

### Non-goals

- No promotion action.
- No change to intrinsic scoring or #875 thresholds.
- No broad routing of unrelated endpoints.
- No production deployment.

## 4. K3 — synchronous stop, rollback to A, and A health

### Scope

Connect stop conditions and operator stop to a synchronous control-plane
transition that prevents new B reservations, completes `rollback-a`, verifies
the A pointer, and proves Analyze/Compare health on A.

### Acceptance criteria

- A stop first blocks new B reservations and drains or bounds in-flight B
  requests; no request admitted after the stop barrier may reach B.
- The signed `rollback-a` preparation/completion path is used. No local flag or
  best-effort callback substitutes for durable control state.
- After completion, routing is 100% A for allowlisted and non-allowlisted
  Analyze/Compare requests, including after process restart.
- A is pinned to the approved manifest/artifact and both `/health` plus actual
  Analyze/Compare smoke workflows pass.
- Failure to verify rollback completion or A health remains stopped/fail-closed
  and emits an actionable error; it never resumes B or promotes.
- Concurrent stop/request tests prove the barrier ordering and bounded
  completion time.

### Non-goals

- No automatic promotion or automatic rollback to a different release.
- No replacement for deployment-control authorization.
- No claim that a hermetic HTTP server proves production rollback.

## 5. K4 — actual canary and post-promotion monitor

### Scope

Implement the real monitor that reads production-shaped routing outcomes,
health, Analyze/Compare workflow results, and intrinsic quality telemetry. It
may request/execute stop-to-A through K3; it cannot promote.

### Acceptance criteria

- Monitor inputs are authenticated, release-bound, PIT-aware, and partitioned
  by active/candidate/ramp identity.
- It evaluates availability, latency, HTTP/schema errors, reservation/result
  reconciliation, score spread, direction/decision flips, coverage,
  missingness, and source concentration using documented windows and minimum
  sample requirements.
- Missing, stale, sparse, mismatched, or unverifiable telemetry produces
  `insufficient-evidence` or `stop`, never PASS.
- A breached stop threshold synchronously invokes K3 and records the observed
  threshold, data window, control head, rollback transaction, and final A
  health disposition.
- The monitor exposes `continue`, `stop`, and `insufficient-evidence`; it has no
  `promote` output and no callable promotion capability.
- Restart/replay is deterministic and does not duplicate stop actions.
- Cost bounds cover polling frequency, retention, cardinality, and HTTP smoke
  volume.

### Non-goals

- No model/scorer tuning.
- No automatic promotion.
- No substitution of synthetic telemetry for actual release evidence.

## 6. K5 — signed audit, real HTTP E2E, and release gate

### Scope

Join #997, K2, K3, and K4 into a release-gate workflow with signed,
commit-bound evidence. Exercise actual Analyze/Compare HTTP ingress for canary,
B failure, stop, rollback, restart, and A health.

### Acceptance criteria

- Depends on completed #998 audit and an eligible current #875 PASS receipt.
  K5 fails closed if either is missing or blocked.
- The E2E starts from A, uses #997 dual authorization to enter canary, proves
  both A and B through real allowlisted Analyze/Compare HTTP requests, injects
  a bounded B failure/stop, completes K3 rollback, restarts the relevant
  process, and proves 100% A plus A health.
- Signed audit links receipt hash, CEO/operator authorization identities,
  control and routing ledger heads, manifest/git/artifact digests, allowlist and
  ramp policy, HTTP evidence digests, monitor decision, rollback transaction,
  and final A health.
- Audit verification is public-key-only and rejects missing, duplicate,
  reordered, mismatched, truncated, or private-key-shaped verification input.
- Release gate enforces bounded deadline, concurrency and cost budgets, leaves
  no test traffic or reservations behind, and produces no secrets in evidence.
- Evidence distinguishes fixture/non-production runs from production-shaped or
  production runs. Only the latter may satisfy the production release gate.
- Reviewer attestation, Harper security/cost review, `/codex-review`, and
  commit-bound pre-push evidence are recorded. Eye is required only if an
  operator/admin UI or user-visible Analyze/Compare state changes.

### Honest closure when the data gate is BLOCK

If #875 remains `BLOCK`, K5 must verify that B cannot start, attach the
authenticated blocking evidence, and leave #879 open or close it explicitly as
`remain-shadow/BLOCK` according to CEO disposition. It must not fabricate a
PASS receipt, lower thresholds, reuse stale evidence, call fixtures
production, or claim that data accumulation is the only remaining engineering
work unless K2–K5 acceptance evidence is independently complete.

### Non-goals

- No production deploy, promotion, or threshold override within this issue.
- No GitHub Actions requirement; repository-local gates remain authoritative.
- No recreation of K1/#997.

## 7. Review and validation

- **All PRs:** named reviewer attestation, local pre-push evidence,
  `/codex-review`, no unresolved findings.
- **Harper (required for K2–K5):** authorization boundaries, SSRF/allowlist,
  fail-closed routing, ledger/key handling, audit integrity, timeouts,
  concurrency, cleanup, and cost caps.
- **gray/CPO:** acceptance truthfulness, scope, metrics semantics, and honest
  closure language.
- **Eye CLI:** required for changes to admin/operator controls, routing status,
  user-visible Analyze/Compare behavior, errors, or release evidence views;
  test desktop/mobile and state transitions. Backend-only changes record
  `Eye N/A` with rationale.
- **Final CEO check:** personally exercise the changed real workflow before
  declaring a milestone complete.

## 8. Dependency and closure graph

```text
#997 (K1 complete)
        |
       K2
        |
       K3
        |
       K4
        |
#998 --+-- current #875 PASS
        |
       K5
        |
      #879 disposition
```

#998 may proceed in parallel with K2–K4, but K5 and #879 production closure are
blocked on its completion. A current #875 `BLOCK` prevents canary start and
release closure but does not excuse missing K2–K5 engineering or tests.
