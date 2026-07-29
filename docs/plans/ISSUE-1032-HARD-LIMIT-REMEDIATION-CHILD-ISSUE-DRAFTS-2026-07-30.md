# Issue #1032 remediation child issue drafts

These are CEO-approved issue drafts. CEO approved the exact preapproval plan
commit `2fd77d0224da4232e288b7dbfb52ac6cdfb6fa8d` on 2026-07-30 and authorized
issue creation. #1032 remains OPEN/BLOCK;
`fc0557794ade987fc4e5ac1a596397ce556a47bb` and all superseded revisions are
void completion evidence. Implementation has not started.

## Draft B1 — signed evidence-action authorization v4

**Title:** `security(release): #997 evidence-action dual authorization v4`

- Parent: #1032
- Estimate: no more than 8 hours
- Can run in parallel with: B2, B3, B4
- Blocks: B5

### Goal

Add shared CEO/operator v4 signatures that explicitly authorize the evidence
publication action and fail closed for every older or canary-start-only schema.

### Acceptance criteria

- [ ] Both signed domains bind
  `derive-and-publish-release-ingress-evidence`.
- [ ] Complete receipt/release/ramp/dataset/PIT/manifest/bundle/transcript/
  provenance/control/routing/evidence-key scope is signed.
- [ ] Actors, key IDs, nonces and public key bytes are pairwise distinct.
- [ ] Current control/PASS, expiry, lifetime, sequence and replay checks pass.
- [ ] V1/v2/v3, `start`, `start-canary`, wrong action/scope/domain and same key
  under different IDs fail closed.
- [ ] Existing non-evidence #997 operations retain their original strict
  behavior without fallback into v4.
- [ ] Golden migration vectors are shared with B5.
- [ ] Hour-6 review; stop/split before 12 hours.

### Review

Harper + gray + `/codex-review`; exact-commit pre-push. Eye N/A.

## Draft B2 — signed typed ingress-event producer/verifier contract

**Title:** `security(release): typed ingress ledger and S01-S12 causal proofs`

- Parent: #1032
- Estimate: 10–12 hours
- Coordinates with: #1031
- Can run in parallel with: B1, B3, B4
- Blocks: B5

### Goal

Define strict authenticated event records from which #1032 can independently
derive every S01–S12 request, routing, cap, failure, rollback and terminal
claim.

### Acceptance criteria

- [ ] Strict events bind canonical request, identity, cohort/bucket, ramp,
  epoch, releases, control, route, HTTP/error and cap/totals.
- [ ] #1032 recomputes all fields from authenticated bytes; caller summaries
  cannot decide PASS.
- [ ] Reservation → result → reconciliation is exact and conservative for
  every B attempt.
- [ ] Signed causal references prove cross-ledger failure → stop → zero later B.
- [ ] Request/model/microusd barriers use typed exact totals.
- [ ] S12 requires signed activation rollback-to-A; `operator_stop` cannot
  substitute.
- [ ] Terminal 100% A derives from signed Analyze/Compare observations covering
  every configured cohort/routing bucket; caller `["A","A"]` is ignored.
- [ ] Legacy/generic/synthetic records, v1 transcript and cross-run splice
  BLOCK.
- [ ] Checked-in schema and golden vectors are directly consumable by #1031,
  #1033 and B5.
- [ ] Hour-10 review; hard stop/split at 12 hours.

### Review

Security/cost-sensitive: Harper + gray + `/codex-review`; exact-commit
pre-push. Eye N/A.

## Draft B3 — failure-safe transactional evidence store

**Title:** `security(release): tombstoned atomic evidence transaction store`

- Parent: #1032
- Estimate: 6–8 hours
- Can run in parallel with: B1, B2, B4
- Blocks: B5

### Goal

Make staging intrinsically ineligible and ensure no failed/indeterminate
transaction leaves an eligible PASS or ambiguous multi-link artifact.

### Acceptance criteria

- [ ] Pending bytes cannot parse/verify as eligible PASS.
- [ ] Eligibility uses a separately durable canonical marker/digest.
- [ ] Failed/indeterminate transactions leave only ineligible staging or a
  durable non-PASS tombstone.
- [ ] Permanent unlink failure never leaves eligible PASS after a failed return.
- [ ] Success has one canonical name, link count one and no pending residue.
- [ ] Cleanup uncertainty cannot be classified successful while metadata is
  noncanonical.
- [ ] Exact retry is idempotent and different prior evidence is immutable.
- [ ] Fault matrix covers all write/fsync/link/unlink/tombstone/crash/restart
  failures, transient and permanent.
- [ ] Hour-6 review; stop/split before 12 hours.

### Review

Security-sensitive: Harper + gray + `/codex-review`; exact-commit pre-push.

## Draft B4 — immutable pre-exec launcher and live process provenance

**Title:** `security(release): immutable signer launcher and live Linux process provenance`

- Parent: #1032
- Estimate: 6–8 hours
- Coordinates with: #1033
- Can run in parallel with: B1, B2, B3
- Blocks: B5

### Goal

Verify and exec a minimal isolated packaged runtime before exposing the signer,
then bind current PID/executable/runtime and all six process roles to pinned
bytes.

### Acceptance criteria

- [ ] Immutable manifest covers launcher, interpreter, publisher and every
  dependency/import.
- [ ] Root-owned no-follow descriptors stay pinned through verify/exec.
- [ ] Runtime uses `-I`/`-S` or smaller equivalent, sanitized environment and
  fixed module paths.
- [ ] Current `/proc/self/exe`, PID and complete runtime bytes verify before
  signer access and post-derive.
- [ ] Prefer a minimal signer FD broker that releases no key path/bytes.
- [ ] #1033 proves evidence/harness/nginx/router/A/B roles with distinct live
  identities.
- [ ] Executable/artifact digests have strict format/domain and equal pinned
  bytes.
- [ ] Signed claims are compared with live Linux process state.
- [ ] Darwin/simulation/mutable import/path swap returns BLOCK.
- [ ] Hour-6 review; stop/split before 12 hours.

### Review

Security-sensitive: Harper + gray + `/codex-review`; exact-commit pre-push.

## Draft B5 — integrated evidence authority adversarial gate

**Title:** `test(release): integrate #1032 authorization ledger runtime and transaction gate`

- Parent: #1032
- Estimate: 6–8 hours
- Depends on: B1, B2, B3, B4
- Blocks: #1032 acceptance, #1019 closure and downstream release gate

### Goal

Integrate accepted contracts and prove that only fresh dual-authorized,
current-PASS, real-Linux evidence can become an eligible durable verdict.

### Acceptance criteria

- [ ] Latest relevant signed promotion event is current PASS for exact scope.
- [ ] B1 dual v4 authorizations bind evidence action and distinct key bytes.
- [ ] B2 independently derives every S01–S12 and terminal claim.
- [ ] B4 validates current runtime/all roles before signer exposure.
- [ ] B3 is the only eligibility/publication path.
- [ ] Adversarial matrix covers old/wrong auth, synthetic records, false
  rollback/routes, cap/reconcile/causal mismatch, provenance/runtime mutation
  and transaction faults.
- [ ] Harness/CLI/environment/payload cannot supply signer, PASS or verdict.
- [ ] Incomplete/non-Linux evidence BLOCKS; only verified external Linux can
  yield PASS.
- [ ] Hour-6 review; stop/split before 12 hours.

### Review

Security/release/cost-sensitive: Harper + gray + `/codex-review`;
exact-commit pre-push. Eye N/A unless visible UI changes.

## Approval stop

**CEO APPROVED on 2026-07-30 — issue creation authorized.**

Approval is bound to preapproval commit
`2fd77d0224da4232e288b7dbfb52ac6cdfb6fa8d`. It covers the B1–B5 estimates,
B1–B4 parallel/B5-final dependency graph, gray 4 P0/3 P1 and Harper mappings,
void predecessor disposition, #1032 OPEN/BLOCK state and non-overridable
external Linux gate.

This approval authorizes issue creation only. It does not authorize
implementation, push, merge, deployment, downstream unblocking or #1032
closure. Implementation must later follow the approved dependencies and normal
development workflow.
