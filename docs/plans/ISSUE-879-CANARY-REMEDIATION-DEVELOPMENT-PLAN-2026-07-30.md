# #879 Canary Remediation Development Plan

- Owner: gray (CPO)
- Date: 2026-07-30
- Parent: #879
- Audit dependency: #998
- Baseline: `develop` includes #996 and #997
- Status: CEO approved revised split after Harper BLOCK on 2026-07-30

## 1. Decision

The former K1 receipt-to-canary authorization bridge is complete in #997 and
must not be reimplemented. #879 remains open because the earlier canary
controller used hermetic temporary servers and in-memory state. That evidence
does not prove that the real allowlisted Analyze and Compare HTTP workflows can
route through A/B, stop synchronously, remain pinned to A, or produce a
release-grade signed audit.

The remediation is split into seven bounded deliverables. Every issue is
estimated at no more than 12 hours:

| Work item | Estimate | Dependency | Outcome |
| --- | ---: | --- | --- |
| K2a | 10h | #997 merged | Base durable Analyze/Compare routing contract; not total K2 acceptance |
| K2b | 10h | K2a | Atomic allowlist provisioning, nginx authentication topology, rollback/evidence |
| K2c | 12h | K2a | Signed per-ramp model-call/monetary budget and strict cost-bearing query binding |
| K2d | 12h | K2a, K2b, K2c | Actual nginx → AF_UNIX → two real releases E2E and release evidence |
| K3 | 8h | K2d | Synchronous stop, route-to-A rollback, and A health proof |
| K4 | 10h | K2d, K3 | Actual canary/post-promotion monitor; stop never promotes |
| K5 | 12h | K2d, K3, K4, #998, current #875 PASS | Signed audit and release gate |

K2a does not satisfy K2 as a whole and does not unblock K3. K2b and K2c may
proceed after K2a, but K2d requires both. K2–K4 may be implemented and verified in non-production even while the data
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
8. Client-supplied identity headers are cleared by the exact authenticated
   nginx location. The AF_UNIX service accepts identity only from the configured
   nginx worker UID verified through Linux `SO_PEERCRED`; direct socket callers
   are A-only.
9. Unknown or duplicate query fields are never canary-eligible. Every
   cost-affecting field is canonicalized and bound to the allowlist/cohort.
   Live, sample, data and LLM modes default to A-only until a signed monetary
   budget explicitly authorizes them.
10. If local nginx or the exact authentication topology is unavailable, tests
    may be skipped with an explicit reason, but release evidence remains
    `BLOCK`. A fixture, monkeypatch, temporary server or synthetic receipt can
    never replace that evidence.

## 3. K2a–K2d — real allowlisted Analyze/Compare HTTP canary routing

### K2a — base routing and strict response contract (≤10h)

Wire the existing release A/B router and #997 verified canary state into the
Analyze and Compare router service. Establish deployment-bound allowlist
matching, deterministic cohort subjects, control-head TOCTOU protection, strict
response validation, durable reservation/result behavior and failover-to-A.

- K2a is a base contract only. It does **not** satisfy overall K2 acceptance,
  does not prove the external ingress topology, and does not unblock K3.
- Its local direct-Handler and bounded HTTP tests are development evidence,
  never production/release evidence.
- No K2a completion statement may say “real canary complete” or “only waiting
  for data”.

### K2b — secure provision and nginx authentication topology (≤10h)

- Provision the allowlist through a root-only, descriptor-safe, atomic install
  path; derive the nginx worker UID plus authenticated deployment initialization
  and current state, and generate exact identity/endpoint/asset/release/ramp
  bindings.
- Install as `root:root 0600`, reread by descriptor, and include exact content
  digest in prerequisite verification, rollback transaction and signed release
  evidence.
- Before reload, `nginx -T` must prove the exact location is behind the intended
  authentication layer, clears every client identity header, and injects only
  the authenticated principal. Rollback restores prior config and allowlist.
- Linux production uses AF_UNIX `SO_PEERCRED`; real direct-socket spoof and
  unsupported-path tests must prove A-only behavior.

### K2c — signed monetary budget and strict query binding (≤12h)

- Add an authenticated per-ramp ledger contract for model-call count and
  monetary cost reservations, settlement/reconciliation and fail-closed caps;
  request count alone is insufficient.
- Reject unknown or duplicate query parameters. Bind canonical endpoint,
  ordered assets, `q` digest, type, live/sample/data/LLM mode, identity,
  release/ramp and control identity into the allowlist decision and stable
  cohort subject without writing raw identity or raw query to ledgers/logs.
- K2 defaults live, Bedrock/LLM, sample and unknown data modes to A-only.
  Enabling any cost-bearing mode requires an explicit signed monetary budget.
- Tests cover `live=1`, `sample=1`, query changes, duplicates, unknown fields,
  cap exhaustion, crash/restart reconciliation and redaction.

### K2d — actual ingress E2E and release evidence (≤12h)

- Exercise the exact local nginx config through AF_UNIX release-router service
  into two real `web.Handler` releases, using bounded timeouts/concurrency and
  durable ledgers.
- Prove external identity spoof routes A; authenticated Analyze and Compare can
  reach capped B; malformed schema, 5xx and timeout return A with durable
  outcome/stop; control-head drift produces zero B reservation; disabled and
  rollback states are 100% A.
- The harness labels fixtures separately. If exact local nginx/auth topology is
  unavailable it skips development execution explicitly and the release
  evidence verdict is `BLOCK`, never PASS.
- Document cohort semantics: canonical subject inputs, ordered comparison
  assets, stability within one signed ramp, isolation across ramp/release/query
  and the distinction between deterministic assignment and budget admission.

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

Join #997, K2d, K3, and K4 into a release-gate workflow with signed,
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
      K2a
      /  \
    K2b  K2c
      \  /
      K2d
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

#998 may proceed in parallel with K2a–K4, but K5 and #879 production closure are
blocked on its completion. A current #875 `BLOCK` prevents canary start and
release closure but does not excuse missing K2–K5 engineering or tests.
