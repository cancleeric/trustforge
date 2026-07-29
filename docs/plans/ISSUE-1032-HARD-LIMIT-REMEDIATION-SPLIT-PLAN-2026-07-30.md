# Issue #1032 — hard-limit remediation split plan

- Owner: gray (CPO)
- Parent: #1019
- Baseline: `origin/develop@06a4c3d0840a6ea2739a47253fe8c4f3c7393a9e`
- Rejected implementation:
  `fc0557794ade987fc4e5ac1a596397ce556a47bb`
- Date: 2026-07-30
- Status: **CEO APPROVED — issue creation authorized; implementation not started**
- CEO approval date: 2026-07-30
- Approval target:
  `2fd77d0224da4232e288b7dbfb52ac6cdfb6fa8d`
- Current disposition: **#1032 OPEN / BLOCK**
- Deliverables: five issues, each no more than 12 hours

## 1. Hard-limit decision

#1032 exceeded its approved 6–8-hour boundary without satisfying its trust
contract. Work stops and the remainder is split. Commit
`fc0557794ade987fc4e5ac1a596397ce556a47bb`, its superseded local revisions,
and the earlier rejected b22 self-attestation/keyring design are void as
completion or release evidence. Code may be reconsidered only as untrusted
implementation input after the new contracts are approved.

| Item | Estimate | Dependency | Outcome |
|---|---:|---|---|
| B1 — signed evidence-action authorization v4 | ≤8h | #997 schema migration | CEO/operator signatures explicitly authorize the evidence action; old schemas fail closed |
| B2 — typed ingress-event ledger contract | 10–12h | signed-ledger primitives; coordinates with #1031 | Required canonical request, route, cap, causal and terminal records |
| B3 — transactional evidence store | 6–8h | none after store contract freeze | No failed or indeterminate transaction leaves an eligible PASS |
| B4 — trusted launcher and process provenance | 6–8h | coordinates with #1033 | Immutable minimal runtime pinned before signer exposure |
| B5 — integrated authority and adversarial gate | 6–8h | B1 + B2 + B3 + B4 | Dual/current PASS plus complete end-to-end fail-closed verification |

B1–B4 may proceed in parallel only after their shared field/domain contracts
are frozen. B5 starts after all four are accepted. Every item repeats a gray
review at hour 6 and stops/splits before exceeding 12 hours.

## 2. Findings mapped to bounded work

| Finding | Severity | Required remediation | Owner |
|---|---|---|---|
| #997 `start-canary`/`start` authorization was reused for evidence publication; evidence action was locally injected | P0-1 | Signed v4 CEO/operator schemas explicitly bind the evidence action and full scope; old/missing action fails closed | B1 |
| Transcript fields could describe route/HTTP/error/caps while signed records contained only kind and subject | P0-2 | Strict typed ingress records bind and recompute all scenario semantics | B2 |
| Publication failure could leave a `.pending` object containing PASS | P0-3 | Separate ineligible staging bytes from eligible commit, tombstone indeterminate state and exhaustively fault-test durability | B3 |
| Publisher opened paths only after Python/import execution; signed provenance did not bind current process/runtime | P0-4 | Minimal immutable pre-exec launcher pins complete runtime before signer FD exposure and verifies current PID/exe | B4 |
| Provenance digest fields were not format-checked or tied to pinned bytes | P1-1 | Typed digests plus byte equality for every process/runtime role | B4 |
| Cleanup could return success with a two-link final/pending inode | P1-2 | Eligible final must have canonical single-name metadata; cleanup uncertainty is tombstoned/noneligible | B3 |
| Producer/verifier contract omitted event fields needed for independent recomputation | P1-3 | Shared versioned producer/verifier schemas with golden failing compatibility tests | B2 |
| S12 accepted a second `operator_stop` as rollback and caller `["A","A"]` as 100% A | Harper P0-2 addendum | Require signed activation rollback-to-A plus actual bucket/cohort observations | B2 |
| Failure proof omitted reconciliation conservation and signed cross-ledger causal ordering | Harper P0-2 addendum | Require reservation → result → reconciliation conservation and signed causal references/order | B2 |
| Python imported mutable dependencies before runtime pin | Harper P0-4 addendum | Package minimal isolated signer/runtime and verify/exec it before signer exposure | B4 |
| Different key IDs could contain the same public key bytes | Harper P1 addendum | Dual authorization requires different actors, IDs **and public key bytes** | B1, B5 |

Any later Harper finding must be appended to this table and assigned to B1–B5
before implementation begins. It may tighten acceptance but cannot silently
expand an issue past 12 hours.

## 3. Shared invariants

1. The harness supplies observations, never signer, verdict or authority.
2. CEO and operator independently sign the exact
   `derive-and-publish-release-ingress-evidence` action and complete bundle
   scope. A canary-start receipt cannot authorize evidence publication.
3. Only strict current schemas are accepted. V1/v2/v3 authorization fallback,
   Work-A transcript v1, unsigned inputs, Darwin and synthetic evidence BLOCK.
4. Every scenario conclusion is recomputed from authenticated typed event
   bytes. Caller summaries are diagnostic only.
5. A PASS becomes eligible only after authorization, current receipt,
   transcript/ledger, provenance, process and transaction checks all pass.
6. No file containing an eligible PASS may remain after a failed or
   indeterminate publication.
7. The executing runtime and all imported code are immutable and verified
   before the signing key becomes accessible.
8. #1031 and #1033 consume the same checked-in schemas and golden vectors; no
   private duplicate contract is allowed.
9. External Linux absence remains `BLOCKED_EXTERNAL_LINUX`, never simulated
   PASS.
10. No production deployment, promotion, merge or #1032 closure is in scope.

## 4. B1 — #997 signed evidence-action authorization v4

### Scope

Add shared CEO and operator authorization v4 schemas whose signed bytes
explicitly authorize the evidence publication action. Migrate producers and
consumers together; older schemas are accepted only by their old operations
and fail closed at the evidence authority.

### Required signed scope

Both authorizations bind:

- schema/domain/version and
  `action=derive-and-publish-release-ingress-evidence`;
- target, candidate, active/candidate release digests and release manifest;
- promotion PASS event hash, git, dataset, policy/ramp and PIT cutoff;
- evidence-bundle digest, routing key, control ledger ID/head/next sequence;
- transcript v2 digest, #1033 provenance digest and intended evidence key ID;
- issued-at, expiry, nonce and nonempty actor/key identity.

### Acceptance

- CEO/operator v4 use distinct signature domains and public-key-only verify.
- Actors, key IDs, nonces **and raw public key bytes** differ.
- Authorization lifetime/current-control/current-PASS checks fail closed.
- V1/v2/v3, `start`, `start-canary`, missing/wrong evidence action, wrong bundle,
  stale/replayed nonce or changed scope cannot publish evidence.
- Migration preserves existing canary-start behavior outside this authority;
  there is no permissive compatibility fallback.
- Shared golden vectors cover valid v4 plus every old/wrong-domain case.

## 5. B2 — signed typed ingress-event ledger and verifier contract

### Scope

Define and implement the shared contract used by #1031 producers and #1032
verifiers. Replace generic event-kind/subject proofs and caller route summaries
with strict authenticated records that contain everything needed to derive the
verdict.

### Required typed fields

Every relevant event binds, as applicable:

- run/scenario/request/identity IDs and domain-separated canonical request;
- endpoint/method/query mode/ordered assets/live-data/LLM flags;
- cohort, routing bucket, ramp, canary epoch, policy, A/B releases and control
  head;
- selected route, terminal HTTP status/body/header digests, candidate status
  and typed error kind;
- reservation/result/reconciliation IDs, request/model/microusd caps, reserved,
  actual, released and cumulative totals;
- signed causal references to predecessor hashes across control and outcome
  ledgers.

### Acceptance

- Exact strict schemas reject unknown/missing/wrong-type fields and legacy
  generic records.
- The verifier reloads authenticated records and recomputes request digest,
  cohort/bucket eligibility, ramp/epoch/releases/control, route and HTTP/error
  outcome; transcript copies cannot decide them.
- Analyze/Compare B cases prove real 2xx B records and ordered assets.
- Request/model/microusd barriers prove exact totals and zero post-barrier B.
- Every attempted B reservation has exactly one terminal result and exactly one
  conservative reconciliation; totals satisfy reservation → result →
  reconciliation conservation.
- Malformed/5xx/timeout bind typed non-200/error records, A fallback and no
  successful B outcome.
- Autostop uses signed causal references and verified cross-ledger ordering:
  failure result → stop barrier → zero subsequent B.
- S12 requires a dedicated signed activation rollback-to-A event. A second
  `operator_stop` cannot substitute.
- Terminal 100% A derives from signed Analyze/Compare observations covering
  every configured cohort/routing bucket. Caller `["A","A"]` is ignored.
- V1 transcript, synthetic/generic ledger records, cross-run splice,
  duplicated/omitted/reordered proof and Darwin status all BLOCK.
- Checked-in schema, example and golden fail/pass vectors are directly usable
  by #1031 and #1033.

## 6. B3 — transactional evidence store and fault matrix

### Scope

Separate staging representation from eligible evidence. Define a state machine
whose public eligibility is unambiguous across write, fsync, link/rename,
cleanup, crash and retry failures.

### Acceptance

- Pending/staging bytes cannot parse or verify as an eligible PASS; eligibility
  requires a separately committed canonical marker/digest.
- File and directory fsync ordering is documented and enforced before
  eligibility changes.
- Any failed or indeterminate commit writes a durable non-PASS tombstone or
  leaves only intrinsically ineligible staging data.
- Permanent unlink failure never produces a failed return with an eligible PASS
  anywhere, including hidden pending names.
- Successful commit ends with one canonical public name, link count one,
  root ownership, mode 0600 and no residual pending name.
- If cleanup cannot establish that invariant, the transaction is
  tombstoned/noneligible rather than reported successful.
- Exact-byte retry is idempotent; different prior evidence is immutable and
  never overwritten.
- Fault matrix covers short write, file fsync, directory fsync before/after
  commit, link/rename, pending/final unlink, tombstone, crash/restart and all
  permanent as well as transient combinations.
- Recovery scan deterministically cleans/tombstones stale transactions without
  making them eligible.

## 7. B4 — trusted pre-exec launcher and process provenance

### Scope

Replace the already-running mutable Python publisher with an immutable packaged
minimal launcher/runtime. It pins and verifies the full executable,
application and dependency set before execution and before a broker exposes
the signing-key descriptor.

### Acceptance

- One immutable package/manifest enumerates launcher, isolated interpreter,
  publisher modules and every imported dependency byte.
- Root-owned no-follow descriptors verify type, owner, mode, link count,
  digest and manifest membership before exec.
- Launch uses an isolated interpreter (`-I` and `-S`, or a smaller equivalent
  runtime) with sanitized environment, fixed module paths and no user/site
  imports.
- Verified descriptors remain pinned through exec; current `/proc/self/exe`,
  PID, executable/runtime/package digests and dependency manifest are checked
  after exec and again post-derive.
- Prefer a separate minimal signer FD broker. It releases the key descriptor
  only after launcher/runtime/config/keyring/process provenance validates and
  never exposes key bytes through path, argv, environment or payload.
- #1033 signed provenance covers current evidence process plus harness, nginx,
  router, release A and release B; all six roles have distinct required
  identities.
- Every executable/artifact digest uses one strict domain/format and equals
  the corresponding pinned bytes. Arbitrary strings BLOCK.
- Claimed PID/UID/executable/artifact data is compared to live Linux process
  state, not accepted solely because the provenance envelope is signed.
- Darwin, container simulation, mutable import path, path swap or unverifiable
  dependency returns `BLOCKED_EXTERNAL_LINUX`/`BLOCK`.

## 8. B5 — integrated dual/current-PASS adversarial authority

### Scope

Integrate only accepted B1–B4 contracts. Derive the final candidate verdict
from fresh #997 promotion state, dual evidence authorization, typed ingress
records, #1033 provenance and transactional eligibility.

### Acceptance

- The latest relevant authenticated promotion event is a current PASS for the
  exact A/B/git/policy/dataset/PIT/manifest scope; a later relevant failure
  blocks.
- Both B1 v4 authorizations verify complete scope, freshness, next control
  sequence, distinct actors/IDs/nonces/public-key bytes and unused intent.
- B2 independently derives all S01–S12 semantics and terminal 100% A from
  records.
- B4 binds the current executing runtime and all six roles before signer access.
- B3 is the sole publication path; a signed verdict is not eligible until its
  durable transaction commits.
- Adversarial matrix includes old authorization schemas, wrong evidence action,
  same public key under two IDs, synthetic v2, generic signed records, false
  rollback, caller terminal routes, cap/reconciliation mismatch, causal splice,
  provenance/PID/runtime mutation and every publication fault.
- No harness/CLI/environment/payload input can supply signer, PASS or verdict.
- Only real Linux with #1031/#1033 contracts satisfied may yield PASS; all
  incomplete/external cases fail closed.

## 9. Dependency and integration flow

```text
             +--> B1 authorization v4 --------+
             +--> B2 typed ingress records ---+
latest develop                                +--> B5 integrated authority
             +--> B3 transactional store -----+
             +--> B4 launcher/provenance -----+
```

B1–B4 may run concurrently after shared domains and field names are frozen.
B2 publishes producer fixtures for #1031; B4 publishes provenance fixtures for
#1033. B5 imports those contracts without weakening them.

## 10. Review and validation

Every implementation PR requires:

- linked child issue and scoped branch;
- focused unit, integration, fault and adversarial tests;
- exact-commit `.githooks/pre-push` PASS;
- gray acceptance review;
- Harper CISO review;
- `/codex-review` adversarial review;
- Eye N/A unless operator-visible UI changes.

Green synthetic tests do not establish real release evidence. External Linux
remains a separate mandatory gate.

## 11. Honest disposition

- `PASS`: B1–B5 pass and the integrated authority derives eligible evidence on
  verified external Linux.
- `FAIL`: any acceptance, security, causal or transaction check fails.
- `BLOCKED_EXTERNAL_LINUX`: exact Linux process/topology/provenance is
  unavailable or unverifiable.

Until then, #1032 remains OPEN/BLOCK. No rejected commit contributes completion
credit.

## 12. CEO approval gate

**CEO APPROVED on 2026-07-30. Issue creation is authorized; implementation has
not started.**

The approval is bound to the preapproval plan commit
`2fd77d0224da4232e288b7dbfb52ac6cdfb6fa8d`. CEO explicitly approved:

1. the B1–B5 split and estimates: B1 ≤8h, B2 10–12h, B3 6–8h, B4 6–8h and
   B5 6–8h;
2. B1–B4 running in parallel only after shared contracts freeze, with B5
   depending on accepted B1–B4;
3. all gray 4 P0/3 P1 mappings and Harper addenda, including signed rollback,
   conservation/causality, immutable pre-exec runtime and distinct public-key
   bytes;
4. `fc0557794ade987fc4e5ac1a596397ce556a47bb`, all superseded local revisions
   and the earlier rejected design as void completion/release evidence;
5. #1032 remaining OPEN/BLOCK until every approved acceptance criterion passes;
6. unavailable or unverifiable external Linux remaining
   `BLOCKED_EXTERNAL_LINUX`, never synthetic PASS.

This approval authorizes creation of B1–B5 GitHub issues only. It does not
authorize implementation, push, merge, production deployment, downstream
unblocking or closure of #1032, and it does not assert that any acceptance
criterion has passed.
