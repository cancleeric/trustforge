# Issue #886 — Multi-angle production-like E2E release gate

## Objective and timebox

Deliver one deterministic, no-AWS release gate that exercises the public
`POST /api/multi-angle` and `GET /api/multi-angle` contract through a real
loopback HTTP server, a distinct daemon `AnalysisFlow`, and one shared durable
SQLite authority/database. Estimated implementation and verification: **10.5
hours**, hard cap **12 hours**.

The gate must prove behavior, accounting, and lineage. It must not replace
production functions with a fake flow, call AWS, use sleeps as correctness
proof, or modify product behavior merely to make the test pass.

## Existing harness and reusable seams

- `tests/test_multi_angle_public_e2e.py` already starts a real
  `ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)`, submits through HTTP,
  starts a separate daemon `AnalysisFlow`, polls the public GET, and verifies
  five stages plus synthesis.
- `tests/test_multi_angle_api.py` covers handler-level validation, response
  mappings, and shared rate-limit behavior, but replaces `AnalysisFlow`.
- `tests/test_multi_angle_atomic_runtime.py` covers admission/projection and
  restart behavior below HTTP, but does not prove the public journey.
- `scripts/run_analysis_flow.py` shows the production daemon loop:
  `reconcile_runtime`, `adopt_pending`, `reap_stale_running`, and
  `adopt_due_retries`.
- External seams that may be deterministic: source collection, Bedrock
  availability/provider response, the clock where explicitly injected, and
  the shared rate-limit backend. Handler routing, admission, durable handoff,
  workers, stages, settlement, synthesis, GET projection, and schema remain
  production code.

## Current gaps found during inventory

1. The existing public E2E proves only one happy submission. It does not assert
   atomic authority rows, reservation release, call receipts, settlement, or
   cost ledger cardinality.
2. Browser behaviors (double-click, refresh, multi-tab) are not distinct server
   protocols; they reduce to concurrent/sequential HTTP requests with the same
   caller, key, and payload. No release test currently proves all variants bind
   to one batch.
3. No HTTP-level test proves different idempotency keys competing for a budget
   that permits only one five-angle batch.
4. Budget rejection, rate limiting, and shared authority failure are covered
   with handler fakes, not a real HTTP server and durable store.
5. Existing restart tests do not combine a public submission, a stopped/crashed
   daemon boundary, a fresh daemon instance, settlement, synthesis, and GET.
6. There is no single bounded script suitable for a local pre-release gate with
   machine-readable failure and an explicit no-AWS invariant.
7. The GET handler opens the same database read-only, while POST and daemon each
   construct independent flow instances. The gate must explicitly pin all three
   to the same path and SQLite authority; otherwise a passing in-memory test
   would not prove the production process boundary.

## Test architecture

Create a serial pytest module plus a thin release script:

- `tests/test_multi_angle_release_gate.py`
  - fixture creates one temporary SQLite file;
  - bootstraps exact daily authority budget;
  - injects the same SQLite atomic store/path into HTTP-created and daemon flow
    instances through existing configuration seams;
  - starts a real loopback `ThreadingHTTPServer`;
  - uses bounded condition polling with diagnostic snapshots, never an
    unbounded sleep;
  - starts/stops fresh daemon instances to model process restart;
  - records the local cost ledger in a temporary durable backend;
  - forbids boto3 client/session construction.
- `scripts/run_multi_angle_release_gate.py`
  - runs only the serial release-gate module;
  - defaults to local temporary storage;
  - emits a concise machine-readable result and non-zero failure code;
  - has no AWS option and rejects AWS/DynamoDB environment configuration.

## Test matrix

| ID | Scenario and stimulus | Required response/state | Exact invariants |
|---|---|---|---|
| RG-01 | One real HTTP POST, distinct daemon runs all five stages, bounded GET polling | POST 200; GET eventually returns one five-angle report | 1 batch, 5 authority jobs/allocations, 10 receipted slots, 5 terminal outcomes, 1 settlement, 1 synthesis; 5 local jobs, 25 completed stage rows, 5 angle results + 1 synthesis result |
| RG-02 | Same caller/key/payload sent as concurrent double-click | Both responses bind to identical snapshot/job IDs (200/202 followed by replay is acceptable per contract) | Still exactly RG-01 cardinalities; one reservation and one ledgered batch |
| RG-03 | Same key sequential refresh, two simulated tabs, and direct HTTP replay after completion | All return the same durable identities/result | No new snapshot, batch, jobs, reservation, call receipts, ledger run, lineage submission, or synthesis |
| RG-04 | Two different keys race while authoritative budget fits exactly one batch | Exactly one admitted; competitor receives budget conflict | 1 batch only, budget debited/reserved once; rejected key leaves no jobs or snapshot side effects |
| RG-05 | Authoritative budget is below the five-angle reservation before POST | HTTP 409 `multi_angle_budget_unavailable` | 0 batch/allocations/jobs/projection/reservation; immutable source snapshot is counted separately and may remain when the current production ordering legitimately created it |
| RG-06 | Shared rate-limit backend reaches its real configured test threshold across HTTP requests/handler instances | Excess request is HTTP 429 `rate_limited` | Rate-limited request creates no batch/jobs/reservation; an admitted replay cannot bypass caller rate limiting |
| RG-07 | Shared authority backend fails before/during admission | HTTP 503 `multi_angle_authority_unavailable` | 0 batch/allocations/jobs/projection/reservation and no fallback authority; any immutable source snapshot is reported separately rather than mislabeled as partial queue state |
| RG-08 | POST commits, daemon stops/crashes at a deterministic checkpoint, new daemon instance starts | Fresh daemon adopts/reconciles and GET eventually returns the one report | Same batch/job IDs; no duplicate charge, slot receipt, terminal, settlement, synthesis, result, or lineage |
| RG-09 | Retryable provider timeout on an atomic live attempt, followed by deterministic retry/restart | Intermediate state is queued/uncertain according to receipt evidence; no early release; final outcome follows durable accounting | Retry attempts do not create a second batch or duplicate ledger receipt; consumed-without-receipt cannot settle |
| RG-10 | Final timeout/dead-letter after retry limit | Failed/timeout batch settles without synthesis | Reservation released exactly once from authoritative outcomes; synthesis claim/result count remains 0 |

## Cardinality and value assertions

For every admitted happy batch, assert values rather than only row existence:

- authority: `atomic_batches=1`, `atomic_allocations=5`,
  `atomic_jobs=5`, `atomic_call_costs=10`,
  `atomic_job_outcomes=5`, `atomic_settlements=1`,
  `atomic_synthesis_claims=1` with `completed`;
- budget: original remaining minus actual cost, `reserved_total=0`, released
  amount equals reservation minus the sum of ten authoritative receipts;
- local runtime: five jobs completed, exactly five stages per job, six results;
- external cost ledger: one immutable record per real `_bedrock_live_attempt`
  durable actual/offline accounting event, unique accounting tokens, and no
  duplicate run IDs. A `cancel_call_slot` `cancelled-before-call` receipt is
  authoritative only in the atomic store and does **not** require a matching
  external cost-ledger row;
- lineage: one `multi_angle_submitted`, 25 `stage_started`, 25
  `stage_completed`, five result publications as currently contracted, and one
  `multi_angle_synthesized`; no duplicate lineage on replay/restart.

Failed/timeout batches assert five terminal outcomes and one settlement but
zero synthesis claim/result. Rejected requests assert all mutation counts are
zero.

## Implementation sequence and estimate

1. Shared HTTP/daemon/authority fixture and no-AWS guard — 2.0h.
2. RG-01 cardinality-rich happy journey — 1.5h.
3. RG-02/RG-03 idempotency browser/direct-HTTP matrix — 1.5h.
4. RG-04/RG-05 budget competition and pre-admission rejection — 1.5h.
5. RG-06/RG-07 shared rate-limit and authority failure — 1.0h.
6. RG-08 restart checkpoint journey — 1.5h.
7. RG-09/RG-10 retry timeout and terminal settlement — 1.0h.
8. Thin release script, diagnostics, focused verification — 0.5h.

Total: **10.5h**.

## Pass/fail policy

The gate passes only when all scenarios finish within bounded deadlines and all
HTTP, authority, budget, ledger, runtime, result, and lineage assertions match.
Timeout diagnostics must print current jobs/stages/retries/dead letters,
authority allocations/receipts, settlement, and budget state. Any skipped
scenario, AWS access attempt, partial authority fallback, duplicate
cardinality, or unbounded wait is a release failure.
