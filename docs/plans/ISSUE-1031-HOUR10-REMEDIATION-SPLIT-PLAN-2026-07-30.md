# Issue #1031 — hour-10 remediation hard-split plan

- Owner: gray (CPO)
- Parent: #1019
- Baseline: `origin/develop@b9004e86b22bbd31469d13bf618503fc028e8670`
- Rejected implementation: `1c6c8e6d641f7c9e004db5c3d8d5ce7b7a351310`
- Date: 2026-07-30
- Status: **CEO APPROVED — issue creation authorized; implementation not started**
- CEO approval date: 2026-07-30
- Approval target:
  `7042745b93d928d9ef392338b97edf49472353f5`
- Deliverables: four dependent issues, each no more than 12 hours
- Current disposition: **#1031 OPEN / BLOCK**

## 1. Decision

The hour-10 review found that #1031 cannot meet its approved real-ingress
acceptance within the 12-hour limit. Work stops under the hard-split rule.
Commit `1c6c8e6d641f7c9e004db5c3d8d5ce7b7a351310` is rejected and void as
completion evidence. Its ideas may be reimplemented after review, but its
synthetic tests, transcript and status cannot be inherited as PASS.

The remaining work is split as follows:

| Item | Estimate | Depends on | Outcome |
|---|---:|---|---|
| A1 — concrete real-process fault driver | 10–12h | existing #1014/#1020/#1021 contracts | nginx → AF_UNIX → router → two real `web.Handler` processes |
| A2 — transcript v2 actual-record proofs | 8–10h | A1 and #1032 evidence contract | typed signed-ledger records, inclusion, conservation and causal barriers |
| A3 — descriptor-pinned supervisor and redaction | 6–8h | A1 | same-process lifecycle trust and normalized recursive redaction |
| A4 — external Linux S01–S12 execution | 6–10h | A1–A3 and #1033 | real ratio/bucket/terminal coverage and honest external verdict |

No item may absorb another item's unfinished scope. At hour 10 of any item,
gray repeats the acceptance review; unresolved work is split and all work
stops at hour 12.

## 2. Why the rejected implementation is insufficient

The rejected implementation validates a recorder against caller-created
observations. A `ScenarioDriver` can self-report topology, HTTP results, ledger
heads and inclusion identifiers; the test suite uses synthetic observations
and does not execute the ordered runner through real nginx, AF_UNIX, router,
two immutable releases or signed ledgers. A caller can also assert
`real_linux_topology=True` independently of the environment preflight.

Consequently, green focused tests prove only structural serialization. They do
not prove the real command/artifact path, actual ledger records, cap
conservation, drift/autostop causality, routing-bucket coverage or terminal
100% A behavior.

## 3. Non-negotiable shared controls

1. Real-observed status is derived internally from verified topology and
   process state. No caller boolean or transcript field can assert it.
2. A/B are distinct immutable artifacts running the real
   `trustforge.web.Handler`; toy handlers and direct method calls are fixtures.
3. Every S01–S12 request enters exact nginx, crosses its configured AF_UNIX
   upstream, reaches the real router and is observed at the selected release.
4. Transcript claims are recomputed from typed authenticated ledger records,
   not caller-provided counters, digests or inclusion lists.
5. The harness has no evidence-signing key, cannot choose PASS and cannot
   publish release authority. #1032 owns that trust boundary.
6. #1033 owns the authoritative Linux provenance result. Darwin, containers,
   fixtures and unavailable topology remain `BLOCKED_EXTERNAL_LINUX`.
7. Descriptor-pinned process/artifact/config inputs remain pinned from
   verification through launch; path re-resolution and TOCTOU are forbidden.
8. Redaction is normalized, recursive and default-deny for sensitive names and
   values. Raw query, identity, auth, cookie, token, key and secret values never
   enter transcript, argv, environment or logs.
9. Failure, cap, drift and stop barriers fail closed to A and create no
   unauthorized post-barrier B reservation or outcome.
10. No production deployment, B promotion, threshold change or signed release
    PASS is in scope.

## 4. gray review finding mapping

| Finding | Severity | Required correction | Owner |
|---|---|---|---|
| No concrete real ingress driver; synthetic driver can self-attest | P0 | Run and observe exact real process chain; remove caller authority over topology/results | A1 |
| `real_linux_topology=True` can bypass environment truth | P0 | Derive status from A1 lifecycle plus #1033 provenance; external execution only | A1, A4 |
| Ledger inclusion and counters are arbitrary self-reported digests | P0 | Decode, authenticate and recompute typed actual records and conservation | A2 |
| Caps, drift, autostop and terminal 100% A are only shallow assertions | P0 | Prove causal barriers and real ratio/bucket execution | A2, A4 |
| Command/artifact checks are string/path based and TOCTOU-prone | P1 | Descriptor-pin bytes and supervise the verified processes | A3 |
| Redaction is exact-key, case-sensitive and weakly tested | P1 | Normalized recursive default-deny redaction with adversarial tests | A3 |

## 5. A1 — concrete nginx/AF_UNIX/router/two-Handler fault driver

### Scope

Implement one concrete driver that owns the lifecycle of exact nginx, the real
release router and two distinct immutable releases running
`trustforge.web.Handler`. It sends real HTTP requests through nginx, observes
the selected backend and injects bounded malformed, 5xx and timeout faults at
the real candidate boundary.

### Acceptance

- Exact nginx config and AF_UNIX upstream are used; no direct-handler success
  path can satisfy a scenario.
- A/B manifests, git and artifact digests differ and bind the launched real
  `web.Handler` code.
- One supervisor starts, health-checks and cleans up nginx/router/A/B, with
  global deadline, per-request timeout and bounded concurrency.
- Driver observations come from HTTP/process/socket artifacts, never fixture
  callback return values.
- Analyze and Compare exercise real request serialization; ordered comparison
  assets are preserved.
- Fault injection produces real malformed, 5xx and timeout candidate behavior
  while the client receives the observed A fallback.
- `REAL_LINUX_OBSERVED` cannot be supplied by a caller; absent verified Linux
  prerequisites returns `BLOCKED_EXTERNAL_LINUX`.
- Development fixtures remain explicitly typed `SYNTHETIC_NON_RELEASE`.

## 6. A2 — transcript v2 typed actual-record proofs

### Scope

Replace digest-list self-attestation with a transcript derived from actual
authenticated routing, budget, result/reconciliation, control and stop
records. Use the #1032 evidence-input contract, but do not sign or publish a
release verdict.

### Acceptance

- Each transcript event contains a typed record identity plus an inclusion
  proof against recorded before/after signed ledger heads.
- The verifier reloads actual records and independently recomputes record type,
  canonical request, ramp/release/control/epoch, route and scenario linkage.
- Reservation/result/reconciliation conservation is recomputed per scenario
  and cumulatively; orphan, duplicate, cross-run and spliced records BLOCK.
- Request, model-call and microusd caps are distinct typed barriers with exact
  configured limits, before/after totals and zero new B after exhaustion.
- Malformed/5xx/timeout bind non-200 candidate observations, durable failure
  results, A fallback and no successful B outcome.
- Autostop proof orders triggering observation → durable stop barrier → zero
  later B reservations/outcomes.
- Drift proof binds unequal old/new control heads and zero reservation,
  result/outcome and reconciliation delta.
- Transcript cannot choose `PASS`; incomplete or unverifiable records are
  `BLOCK`.

## 7. A3 — descriptor-pinned supervisor and normalized redaction

### Scope

Harden A1 lifecycle inputs and all emitted diagnostics. Verification and
launch occur within one supervisor while trusted files remain descriptor
pinned. Redaction handles normalized keys and sensitive values recursively.

### Acceptance

- Executables, artifacts, manifests and configs are opened no-follow, checked
  as regular immutable files, byte-hashed and kept descriptor pinned through
  process launch.
- Launch cannot replace a verified path between stat/read/exec; symlink,
  hardlink, writable-file and path-swap adversarial cases fail closed.
- The supervisor verifies that launched process identities match the pinned
  artifacts and expected roles.
- Environment and argv are allowlisted; signing/private-key material is
  rejected and never inherited by the harness.
- Redaction normalizes case and separators and recursively protects headers,
  mappings, sequences, exceptions and nested diagnostics.
- At minimum authorization, proxy-authorization, cookie, set-cookie, token,
  api-key, password, secret, private-key, identity and raw-query names and
  values are removed or domain-separated.
- Property/adversarial tests prove secret canaries never appear in transcript,
  logs, exceptions, process metadata or failure output.

## 8. A4 — external Linux S01–S12 execution and terminal bucket coverage

### Scope

On the exact external Linux release topology accepted by #1033, execute the
approved S01–S12 sequence using A1–A3 and independently verify real traffic
ratios, routing buckets, causal barriers and final 100% A state.

### Acceptance

- #1033 provenance is verified and bound before execution; it cannot be
  replaced by a caller flag or a synthetic receipt.
- Exactly S01–S12 execute once and in order through exact real ingress.
- S04/S05 yield real B 2xx Analyze/Compare responses with exact canonical
  cohort, budget, ramp, epoch and ordered assets.
- A bounded distribution sample proves both actual A and B cohort routing;
  observed counts and ratios derive from terminal HTTP/ledger records.
- Request, model-call and microusd barriers each prove zero post-barrier B.
- Malformed/5xx/timeout and autostop satisfy A2 causal evidence.
- S12 samples every configured terminal routing bucket for Analyze and Compare
  after stop/disabled/rollback; all observed routes are A and reservation,
  result/outcome and reconciliation deltas are zero.
- Raw sensitive values are absent from all exported artifacts.
- Missing/unavailable/nonconforming Linux topology, skipped scenario or
  incomplete bucket coverage returns `BLOCKED_EXTERNAL_LINUX`/`BLOCK`, never
  PASS.

## 9. Dependency flow

```text
latest develop
      |
      v
 A1 real process/fault driver --------> A3 descriptor/redaction
      |                                      |
      +-------------> A2 transcript v2 <-----+
                         |   depends #1032 contract
                         v
                A4 external Linux execution
                         |   also depends #1033
                         v
                 #1031 acceptance review
```

A2 may begin only after A1 exposes stable real-observation interfaces and the
#1032 input contract is fixed. A3 depends on A1's concrete lifecycle. A4 waits
for A1–A3 and #1033.

## 10. Required validation and evidence

Every child PR requires a linked issue, exact-commit local pre-push PASS, named
reviewer attestation, gray acceptance review, Harper CISO review and
`/codex-review`. Eye is N/A unless operator-visible UI changes.

Focused/unit tests may use synthetic fixtures only when visibly labeled
non-release. They cannot satisfy real-ingress acceptance. The final A4 evidence
must record exact commit/artifact/config/provenance digests and real command
outputs without secrets.

## 11. Honest disposition

- `PASS`: A1–A3 are complete and A4 passes on the #1033-verified external Linux
  topology with all real records and buckets proven.
- `FAIL`: a required scenario, invariant or adversarial check fails.
- `BLOCKED_EXTERNAL_LINUX`: the exact Linux host/topology/evidence authority is
  missing or unverifiable.

Until A1–A4 pass, #1031 remains OPEN/BLOCK. The rejected commit and its green
synthetic tests cannot reduce this scope or change the disposition.

## 12. CEO approval gate

**CEO APPROVED on 2026-07-30. Issue creation is authorized; implementation has
not started.**

The approval is bound to the preapproval plan commit
`7042745b93d928d9ef392338b97edf49472353f5`. CEO explicitly approved:

1. the hour-10 hard stop and A1–A4 split, with every item at no more than 12
   hours;
2. the dependency graph, including A2 on A1 + #1032, A3 on A1, and A4 on
   A1–A3 + #1033;
3. the gray P0/P1 finding mappings and their acceptance criteria;
4. `1c6c8e6d641f7c9e004db5c3d8d5ce7b7a351310` as rejected and void
   completion evidence;
5. #1031 remaining OPEN/BLOCK until every approved acceptance criterion passes;
6. unavailable or unverifiable external Linux remaining
   `BLOCKED_EXTERNAL_LINUX`, never synthetic PASS.

This approval authorizes creation of the four child issues only. It does not
authorize implementation, push, merge, production deployment or closure of
#1031, and it does not assert that any A1–A4 acceptance criterion has passed.
