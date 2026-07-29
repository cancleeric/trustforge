# Issue #1037 — authenticated router receipt hard-limit split plan

- Owner: gray (CPO)
- Parent audit: #998
- Parent feature: #877
- Date: 2026-07-30
- Baseline: latest `origin/develop` at implementation start
- Status: **CEO REVIEW REQUIRED — implementation is not authorized**
- Current disposition: **#1037 OPEN / BLOCK**

## 1. Hard-limit decision

#1037 exceeded its approved ten-hour boundary. The apparently narrow rollback
drill exposed three separate trust boundaries:

1. proof that the production transport actually handled the request and
   response bytes;
2. concurrency-safe durable publication of the resulting disposition;
3. non-production rollback orchestration and exact reconciliation.

They cannot be collapsed into one issue without recreating a caller-signed
self-consistency proof.

The following local, unpushed revisions are void as completion, review or
release evidence:

- `d05b486c5aaaa6fee84260ac6995100ec4547d77`
- `942f2b55759ef0d13d4a70963ead7728dda4a079`
- `c3e4a3a1de2e44e39472e164ebb35fbb00418b7b`

Code from those revisions may be reconsidered only as untrusted design input
after the relevant child plan is approved.

| Item | Estimate | Dependencies | Outcome |
|---|---:|---|---|
| RR1 — transport-produced authenticated request receipt | 10–12h | production router/manifest contract; coordinate with #1057/#1058 native authority | The component that owns the authenticated transport produces an unforgeable, privacy-safe receipt over actual request/response execution |
| RR2 — concurrency-safe signed disposition ledger | 10–12h | accepted RR1 schema/capability; signed-ledger migration infrastructure | Exactly one terminal disposition is durably appended for every accepted request under concurrency, failure and restart |
| RR3 — actual-router rollback drill and reconciliation | 8–10h | accepted RR1 + RR2 + #877 | A→B→regression→A through the production path with exact receipt/ledger reconciliation, explicitly non-release |

RR2 may prepare schema migration fixtures after RR1 freezes its domains, but
it cannot claim acceptance with a Python-only caller seal. RR3 starts only
after RR1 and RR2 merge to `develop`.

Each issue receives a Gray hour-6 scope review and stops before 12 hours. Any
remainder is split again.

## 2. Round 3 P0 finding

Reservation and terminal result events prove that the outcome signer accepted
two caller-supplied facts. They do not prove that:

- the production router performed manifest verification;
- the authenticated candidate socket was used;
- the recorded path/subject selected the observed route;
- response status/body/error came from that transport;
- fallback bytes came from immutable A;
- the request returned through the canonical production method.

A monkeypatched `ReleaseABRouter.route` can call canonical
`reserve_candidate()` and `record_candidate_result()`, creating authentic
signed ledger records, then return a hard-coded `RoutedResponse`. A verifier
that accepts those two records and response labels still produces false
evidence.

Appending another event through a generally callable Python ledger method does
not solve the finding: the same monkeypatch can call that method too. Callable
identity or bytecode checks can be defense in depth, but cannot replace an
authority/capability boundary owned by the actual transport execution.

## 3. Shared invariants

1. Callers, drill code and response objects cannot sign or manufacture an RR1
   transport receipt.
2. Receipt authority becomes available only inside the verified production
   transport execution boundary after endpoint manifest and connection checks.
3. Raw stable identity, query, authorization headers, cookies, response body
   and credentials never enter receipts or ledgers.
4. Path, subject and body use distinct keyed/domain-separated privacy digests.
   Unkeyed low-entropy path or subject hashes are insufficient.
5. Every receipt binds exact control head, routing policy, A/B artifact
   digests, route, status, failover and typed error.
6. B success binds its reservation and successful terminal result. Candidate
   regression binds its reservation, failed result and immutable-A fallback.
   Normal A binds absence of a candidate reservation.
7. A request has exactly one terminal disposition. Duplicate, missing,
   conflicting, orphaned, indeterminate or cross-run records BLOCK.
8. Concurrent requests may interleave ledger records but cannot steal or
   overwrite another request's receipt or references.
9. Ledger and provisioning schema changes are versioned migrations; old
   permission receipts fail closed.
10. Hermetic drill success remains `NON_RELEASE_HERMETIC` /
    `BLOCKED_NON_PRODUCTION`, never production PASS.

## 4. RR1 — transport-produced authenticated request receipt

### Goal

Introduce a narrow capability held by the actual production transport path,
not by the drill or general control/outcome API. It authenticates what was
really requested, received and returned.

### Required receipt fields

- versioned schema, domain and receipt ID;
- run/service/process identity and transport generation;
- keyed path digest and keyed stable-subject digest;
- HTTP method and request-binding digest;
- control ledger ID/head and routing outcome head observed at admission;
- routing policy/ramp and active/candidate artifact digests;
- selected release, failover flag and typed route reason;
- endpoint manifest digest and authenticated connection identity for B;
- keyed response-body digest, terminal HTTP status and typed error;
- reservation ID plus reservation/result event hashes when B was attempted;
- issued sequence/time and receipt-authority key/capability identity.

### Acceptance

- Receipt production occurs inside the canonical manifest-verified HTTP
  execution boundary after response bytes are read and before they are
  returned.
- A distinct least-privilege signer/broker or native capability releases a
  signing operation only to that verified boundary. General Python callers
  cannot access key bytes, path, FD or a generic `sign(payload)` operation.
- Receipt fields are derived from local execution state. Caller-supplied
  release, status, body digest, failover, error or event hash is rejected.
- Path/subject/body digests are keyed with separate domains and bind the
  current service generation. Dictionary attacks cannot recover low-entropy
  identity/path values from public evidence.
- For B, receipt creation requires the exact endpoint manifest, authenticated
  socket generation, reservation and terminal result.
- For A fallback after B failure, both failed B transport and served A bytes
  are bound. A hard-coded fallback label cannot satisfy it.
- For normal A, receipt proves the production A request path and explicitly
  binds no candidate reservation.
- Private keys/capabilities are absent from argv, environment, request,
  response, drill report and ledger.
- A monkeypatched route that writes authentic reservation/result events and
  returns matching fake response bytes cannot obtain a valid RR1 receipt.
- Darwin, fixture signer, in-process self-key, monkeypatch or unavailable
  authority returns BLOCK and is not release evidence.

### Tests

- real production transport composition with authenticated local release
  services;
- candidate manifest/socket swap, response/body/status mutation and fallback
  substitution;
- generic signer exposure, replay, cross-generation and cross-process use;
- exact stronger monkeypatch attack from Round 3;
- keyed privacy-domain separation and secret-leak scan.

## 5. RR2 — concurrency-safe signed disposition ledger

### Goal

Consume accepted RR1 receipts and publish one durable, authenticated terminal
disposition without making the outcome ledger a second receipt authority.

### Acceptance

- Introduce a versioned outcome event and provisioning receipt migration
  (v3 or later); all readers, writers, provisioners, migration scripts and
  release verifiers migrate atomically.
- The ledger accepts only an authentic, current RR1 receipt and stores its
  digest/ID plus exact referenced reservation/result hashes.
- Exactly one terminal disposition exists per request/receipt and per
  reservation. Exact retry is idempotent; conflicting retry BLOCKS.
- Normal A, B success, B failure/A fallback, timeout, manifest failure,
  response-validation failure and terminal transport failure have explicit
  signed outcomes. If signing/publication is unavailable, the request is
  explicitly fail-closed and cannot become evidence.
- Atomic append/retry uses the current authenticated head. Interleaved
  reservation/result/disposition events preserve sequence, previous hash and
  per-request causality without assuming a three-record contiguous slice.
- Concurrent requests cannot attach another request's reservation, result,
  RR1 receipt, path/body digest or control head.
- Crash/restart recovery detects missing and indeterminate dispositions; it
  never manufactures one from response logs or caller summaries.
- Projection validates receipt authenticity, generation, scope, artifacts,
  policy, route/status/error semantics and all event references.
- Final head/count reconciliation includes every event exactly once; deletion,
  duplication, reordering and cross-run splice BLOCK.

### Tests

- deterministic concurrency with interleaved A/B success/failure;
- head conflict/retry, duplicate exact retry and conflicting retry;
- crash after RR1 receipt, before append, during append and after fsync;
- old permission receipt/schema, unknown event and partial migration;
- forged receipt, wrong generation/control/policy/artifact and crossed
  reservation/result;
- full provision/migrate/readiness/verified-gate compatibility suite.

## 6. RR3 — actual-router rollback drill and reconciliation

### Goal

Use the merged production composition, RR1 transport receipt and RR2 ledger to
perform the non-production rollback drill without introducing another
authority.

### Acceptance

- Production `build_runtime_router` and the drill consume the same public
  composition seam; the drill supplies one existing authenticated non-
  production `DeploymentControlLedger`.
- All acceptance requests enter the actual router/transport path. Direct A/B
  probes are diagnostic only and never cited as routed evidence.
- Read-only immutable A/B artifacts and exact endpoint manifests are
  digest-bound.
- One identical PIT request set binds keyed path/subject/request digests,
  policy/ramp and A/B artifacts.
- Ordered evidence is A → B → injected B regression with A fallback →
  rollback-to-A → subsequent A-only requests.
- Every request has an authentic RR1 receipt and exactly one reconciled RR2
  disposition. B success has reservation/result/receipt/disposition; failure
  has failed result plus authenticated fallback; A has no candidate events.
- Before/after control and outcome heads, all event hashes/record digests,
  receipt IDs/digests, reservation IDs and final head/count reconcile without
  assuming concurrency-free adjacency.
- Rollback SLO ends only after the complete post-rollback PIT set traverses
  the router and returns authenticated A receipts.
- History and receipts remain readable after rollback; no irreversible
  migration or production mutation occurs.
- Report and any gate receipt explicitly bind
  `release_eligible=false`, `NON_RELEASE_HERMETIC` and
  `BLOCKED_NON_PRODUCTION`.
- Mock router, direct probes, caller labels, authentic reservation/result with
  fake response, missing RR1, duplicate disposition and a second control
  authority all BLOCK.

## 7. Dependency graph

```text
#1057/#1058 transport authority coordination
                    |
                    v
              RR1 transport receipt
                    |
                    v
          RR2 signed disposition ledger
                    |
                    v
       RR3 actual-router rollback drill
                    |
                    v
          #1037 / #998 J-2 disposition
```

RR1 must state explicitly whether #1057/#1058 is a hard dependency. If the
receipt capability cannot be protected before those issues merge, RR1 stays
`BLOCKED_BY_NATIVE_AUTHORITY`; it must not substitute a Python private seal.

## 8. Review and validation

Every child issue requires:

- scoped branch/worktree and explicit dependencies/acceptance;
- Gray truthfulness review at hour 6 and before push;
- Harper CISO review;
- independent `/codex-review` adversarial review;
- focused unit/integration/concurrency/adversarial tests;
- exact-commit repository-local `.githooks/pre-push` PASS;
- commit-bound reviewer attestation and `Eye N/A` rationale;
- normal merge to `develop` and fresh post-merge full gate.

RR1 and RR2 are security-sensitive. No author self-approval, admin merge,
protection override, caller-signed fixture, synthetic production receipt or
backdated evidence is allowed.

## 9. Honest disposition

- `PASS`: RR1–RR3 are accepted and RR3 reconciles every authentic
  non-production request receipt/disposition.
- `FAIL`: any functional, signature, privacy, concurrency, causal or
  reconciliation check fails.
- `BLOCKED_BY_NATIVE_AUTHORITY`: transport-only signing capability is not
  available without pending native authority work.
- `BLOCKED_NON_PRODUCTION`: RR3 succeeds hermetically but cannot establish a
  production release gate.

#1037 and #998 J-2 remain OPEN/BLOCK until RR3 is accepted. RR3 does not close
external production evidence gaps.

## 10. CEO approval gate

CEO must explicitly approve:

1. RR1/RR2/RR3 scopes, estimates and dependency order;
2. the Round 3 P0 finding and caller-self-signing prohibition;
3. void status of `d05b486`, `942f2b5` and `c3e4a3a`;
4. keyed privacy digests and native-capability dependency decision;
5. versioned permission migration and concurrency requirements;
6. RR3 remaining non-release evidence;
7. all Gray/Harper/Codex/full-gate requirements.

This document does not authorize implementation, push, merge, issue closure,
production mutation or release activity.
