# Issue #1019 remediation child issue drafts

These drafts are not GitHub issues. CEO approval of the parent split plan is
required before creation. Replace `<A>`, `<B>`, and `<C>` with the actual child
issue numbers after creation.

## Draft A — real nginx/AF_UNIX/two-release Handler harness

**Title:** `test(release): real nginx AF_UNIX two-Handler ingress transcript`

- Parent: #1019
- Estimate: 10–12 hours
- Depends on: #1014, #1021, #1020
- Blocks: Draft D and #1019 closure
- Can run in parallel with: Draft B

### Goal

Run exact nginx → AF_UNIX → release router → two immutable real
`trustforge.web.Handler` releases and produce a durable ordered development
transcript without evidence-signing authority.

### Acceptance criteria

- [ ] A/B are distinct immutable release artifacts running real `web.Handler`.
- [ ] All 12 scenario IDs S01–S12 from the approved plan execute exactly once
  and in order through exact nginx/AF_UNIX.
- [ ] Transcript binds canonical request digest, ramp/release/control identity,
  ledger heads, reservation/outcome, route and redacted HTTP result.
- [ ] Malformed/5xx/timeout falls back to A and records failure/stop causality.
- [ ] Drift admits zero B; stop/disabled/rollback terminal sample is 100% A.
- [ ] Harness has no private evidence key and cannot publish PASS.
- [ ] Timeouts, deadline, concurrency, cleanup and secret-redaction tests pass.
- [ ] Linux unavailable returns `BLOCKED_EXTERNAL_LINUX`.
- [ ] At hour 10, unfinished scope is split; work stops at 12 hours.

### Review

gray + Harper + `/codex-review`; full pre-push; Eye N/A unless visible behavior
changes.

## Draft B — root-owned descriptor-pinned evidence trust anchor

**Title:** `security(release): root descriptor-pinned #1019 evidence authority`

- Parent: #1019
- Estimate: 6–8 hours
- Depends on: #997; contract-compatible with Draft A
- Blocks: Draft C and Draft D
- Can run in parallel with: Draft A

### Goal

Create a dedicated root-owned evidence role that verifies authorization and
trusted descriptors before deriving and atomically publishing a signed verdict.

### Acceptance criteria

- [ ] Every trusted executable/config/keyring/auth/transcript/ledger/artifact
  input is no-follow descriptor-pinned and metadata/byte verified.
- [ ] Evidence role is distinct from harness/nginx/router/A/B identities.
- [ ] #997 authorization is public-key verified and fully target/ramp/release/
  control/deadline/action bound.
- [ ] Harness cannot access signing key or supply signer/PASS decision.
- [ ] Verdict is derived from verified bytes, not caller fields.
- [ ] Publish is root-owned, atomic, durable, idempotent and preserves prior
  evidence on failure.
- [ ] Any trust/authorization failure is nonzero and emits no PASS.

### Review

Security-sensitive: Harper + gray + `/codex-review`; full pre-push.

## Draft C — Linux provenance preflight

**Title:** `security(release): Linux nginx AF_UNIX process and ledger provenance preflight`

- Parent: #1019
- Estimate: 6–8 hours
- Depends on: Draft B, #1021
- Blocks: Draft D

### Goal

Produce a canonical fail-closed Linux provenance result for the exact nginx,
auth, AF_UNIX, process, artifact and ledger topology.

### Acceptance criteria

- [ ] Descriptor-pinned real nginx binary executes exact `-T`; fake `PATH`
  cannot influence selection.
- [ ] Effective canary location auth/header clearing/injection/socket topology
  is verified across exact source boundaries.
- [ ] All nginx source/include bytes are descriptor-verified.
- [ ] AF_UNIX socket and Linux `SO_PEERCRED` peer UID/process role are bound.
- [ ] nginx/router/A/B running processes bind to expected immutable artifacts,
  manifests and git digests.
- [ ] Routing/control/budget/outcome ledger identities, heads, signatures,
  ownership and modes verify.
- [ ] Missing, raced, mutable, non-Linux or simulated inputs return
  `BLOCKED_EXTERNAL_LINUX`, never PASS.

### Review

Security-sensitive: Harper + gray + `/codex-review`; full pre-push.

## Draft D — adversarial release-host gate

**Title:** `test(release): adversarial #1019 release-host evidence gate`

- Parent: #1019
- Estimate: 5–7 hours
- Depends on: Draft A, Draft B, Draft C
- Blocks: #1019 closure and #1015

### Goal

Attack all evidence/provenance boundaries and derive the only eligible
release-host verdict from verified A/B/C artifacts.

### Acceptance criteria

- [ ] Fake `PATH`, wrapper binary, config/include substitution, symlink,
  hardlink, owner/mode mutation and descriptor/path swaps BLOCK.
- [ ] Synthetic/harness/private-key-shaped signer and forged/stale/wrong-scope
  authorization BLOCK.
- [ ] Transcript/ledger/provenance/artifact omission, reorder, duplicate,
  truncate, tamper and cross-run splice BLOCK.
- [ ] Canonical request/ramp/release/control mismatch and raw secret/identity/
  query leakage BLOCK.
- [ ] All S01–S12 are inclusion-proven exactly once and in order.
- [ ] Drift and post-barrier reservations are zero; terminal traffic is 100% A.
- [ ] Failure status and autostop causality are durable and correctly ordered.
- [ ] Skip, fixture, synthetic, non-Linux or incomplete evidence returns BLOCK.
- [ ] Only a real Linux release-host run may emit signed PASS.

### Review

Security/release/cost-sensitive: Harper + gray + `/codex-review`; exact-commit
pre-push. Eye N/A unless an evidence/operator UI is added.
