# Issue #1019 — real ingress evidence remediation split plan

- Owner: gray (CPO)
- Parent: #879
- Replaces: rejected/void b22 implementation plan
- Baseline: `origin/develop@958d9a0aa1374139da8e558007c86201861ed548`
- Date: 2026-07-30
- Status: **CEO APPROVED — issue creation authorized; implementation not started**
- CEO approval date: 2026-07-30
- Approval target:
  `806a7e301ea387798b7d187f4c3f7677a20d267a`
- Implementation estimate: 27–35 hours plus access to an external Linux release
  host
- Deliverables: four issues, each no more than 12 hours

## 1. Decision

The b22 plan is rejected and void. #1019 cannot honestly fit in one 12-hour
issue. The current estimate is 27–35 engineering hours, within the reviewed
27–37-hour range, plus externally scheduled Linux execution.

The work is split into four independently reviewable issues:

| Work item | Estimate | Depends on | Outcome |
|---|---:|---|---|
| A — real ingress harness | 10–12h | #1014, #1021, #1020 | Real nginx → AF_UNIX → two real `web.Handler` releases; ordered transcript and ledgers |
| B — evidence trust anchor | 6–8h | A contract may be stubbed; implementation may run in parallel with A | Root-owned descriptor-pinned evidence role and authorization |
| C — Linux provenance preflight | 6–8h | B, plus #1021 topology contract | Fail-closed proof of exact nginx/socket/process/artifact/ledger provenance |
| D — adversarial release-host gate | 5–7h | A + B + C | Attack matrix and release-host verdict |

If A exceeds 12 hours, stop at the bounded harness contract and open another
issue for remaining ordered scenarios. Do not silently enlarge A.

## 2. Why the previous shape failed

The former issue mixed four different trust boundaries:

1. functional HTTP behavior through two real application releases;
2. authority to create release evidence;
3. proof that the executing Linux/nginx/socket/process topology is the intended
   topology;
4. adversarial proof that synthetic or attacker-controlled inputs cannot mint
   PASS evidence.

A green test suite inside one developer process cannot prove all four. A
temporary server, fake nginx, fixture signer, monkeypatch transport, direct
handler call, or synthetic receipt may support development tests, but none is
a release-host trust anchor.

## 3. Shared invariants

1. A and B are real immutable release artifacts and run the real
   `trustforge.web.Handler`; a toy handler cannot satisfy evidence.
2. Requests enter through the exact nginx binary/config/auth location and then
   the AF_UNIX router socket. Direct handler/socket probes are diagnostic only.
3. The harness may collect raw observations but cannot self-authorize or mint
   release PASS.
4. Release evidence is produced only by the root-owned descriptor-pinned role
   after verified authorization and provenance preflight.
5. All paths fail closed to A. Unknown, duplicate, unauthorized, over-budget,
   mismatched, unverifiable, stopped, disabled, or rollback state cannot reach
   B.
6. Transcript order, request digest, routing/control heads, reservations,
   outcomes, stop causality and final disposition are immutable and
   inclusion-proven.
7. No raw identity, raw query, private key, credential, authorization payload,
   or secret appears in transcript, argv, environment, logs, or evidence.
8. Fixtures, synthetic signers, fake `PATH`, direct service probes and
   non-Linux runs are explicitly labeled non-release evidence.
9. A Linux release-host run is mandatory for PASS. Without it, the result is
   `BLOCKED_EXTERNAL_LINUX`, never PASS or “only waiting for data”.
10. This work does not promote B, modify #875 thresholds, deploy production, or
    replace #997 authorization.

## 4. Harper BLOCK P0 mapping

The four P0 findings from the b22 review are normalized below into executable
acceptance. Nothing is considered resolved merely because a unit test exists.

| Harper finding | Required correction | Owning work |
|---|---|---|
| P0-1 — functional evidence was not bound to a real end-to-end application path | Use exact nginx → AF_UNIX → two immutable releases running real `web.Handler`; execute the ordered scenarios and bind transcript/ledgers | A |
| P0-2 — the harness could act as its own evidence authority | Separate collection from attestation; root-owned descriptor-pinned role verifies explicit authorization before signing/publishing | B |
| P0-3 — environment/topology provenance was not fail-closed | Verify nginx binary/config/source and effective auth scope, peer credentials, socket/process/artifact/ledger identity before execution | C |
| P0-4 — release PASS could be confused with synthetic or attacker-controlled evidence | Attack fake `PATH`, synthetic signer, tampering, omission/reordering and canonical mismatch; prove 100% A/failure/autostop causality | D |

All P0 rows require Harper re-review and `/codex-review` after implementation.

## 5. Work A — real nginx/AF_UNIX/two-release Handler harness

### Scope

Build a bounded non-production harness that starts two immutable release
artifacts, each serving the real `trustforge.web.Handler`, places the actual
release router behind the exact nginx AF_UNIX location, and records one
append-only ordered transcript plus canonical routing/control/budget ledgers.

The harness is an observer and fault injector. It has no evidence-signing key
and no authority to publish PASS.

### Twelve ordered scenarios

The transcript must contain exactly these ordered scenario IDs. Each row binds
canonical request digest, ramp/release/control identity, before/after ledger
heads, reservation/result IDs, route disposition and redacted HTTP result.

1. `S01_DISABLED_ANALYZE_A` — disabled baseline Analyze is A.
2. `S02_DISABLED_COMPARE_A` — disabled baseline Compare is A and preserves
   ordered comparison assets in the canonical digest.
3. `S03_EXTERNAL_SPOOF_A` — direct/external identity spoof is cleared or
   rejected and routes A.
4. `S04_AUTH_ANALYZE_B` — authenticated, allowlisted, in-cohort Analyze reaches
   real B within all signed caps.
5. `S05_AUTH_COMPARE_B` — authenticated, allowlisted, in-cohort Compare reaches
   real B with ordered assets bound.
6. `S06_COHORT_OR_SCOPE_A` — valid authentication but out-of-cohort,
   non-allowlisted endpoint/asset/identity, unknown or duplicate query is A.
7. `S07_REQUEST_CAP_A` — request cap exhaustion makes no new B execution and
   returns A.
8. `S08_MODEL_OR_MONEY_CAP_A` — model-call or monetary cap exhaustion makes no
   new B execution and returns A with reconciled reservation.
9. `S09_B_MALFORMED_FALLBACK_A` — B malformed schema records failure, returns
   A, and creates no successful B outcome.
10. `S10_B_5XX_FALLBACK_A` — B 5xx records failure, returns A, and contributes
    to stop causality.
11. `S11_B_TIMEOUT_AUTOSTOP_A` — bounded B timeout returns A, records the
    triggering observation and causally precedes stop.
12. `S12_DRIFT_ROLLBACK_100A` — control-head drift admits zero B reservations;
    after stop/disabled/rollback, a bounded Analyze+Compare sample is 100% A.

If model and monetary cap exhaustion cannot both be proven within S08, split
the second cap case into a follow-up issue rather than adding an unbounded
thirteenth scenario to A.

### Acceptance criteria

- Both backends are distinct immutable artifacts running the real
  `trustforge.web.Handler`; artifact and git digests are recorded.
- All 12 scenarios enter through exact nginx and AF_UNIX and complete in order
  under an overall deadline, per-request timeout and concurrency cap.
- Transcript and durable ledgers agree on canonical request digest, cohort,
  reservation, route, outcome, stop and final A state.
- Analyze/Compare bodies are schema-validated; B malformed/5xx/timeout never
  leaks a partial B result to the client.
- Control-head drift creates zero B reservations.
- Scenario 12 proves 100% A after stop/disabled/rollback.
- Harness has no private evidence key, cannot publish PASS, and labels local
  fixture/synthetic modes as non-release.
- Secrets and raw identity/query values are absent from all artifacts.
- Linux unavailable: development tests may run, but release result remains
  `BLOCKED_EXTERNAL_LINUX`.

### Stop/split rule

At hour 10, gray reviews completion against all 12 scenarios. If a real
Handler, ordered transcript, or durable ledger binding remains incomplete,
stop at 12 hours and draft a new dependent issue. Do not report A complete.

## 6. Work B — root-owned descriptor-pinned evidence trust anchor

### Scope

Separate the unprivileged test harness from the evidence publisher. Implement
a minimal release-host role that opens trusted inputs by descriptor, verifies
authorization and provenance, then signs/publishes a verdict. It never accepts
a path, signer, decision or PASS supplied by the harness.

### Acceptance criteria

- Publisher executable, config, public-key ring, authorization receipt,
  transcript, ledgers and artifacts are opened with no-follow descriptor-safe
  semantics; exact bytes and metadata are verified after open.
- Publisher and trusted configuration are root-owned, not group/world
  writable, regular, single-link files; descriptors remain pinned through
  validation and publish.
- A dedicated evidence role is distinct from nginx worker, router, A/B release
  process and harness identities.
- #997 authorization is verified public-key-only and binds target, candidate,
  ramp, release, control head, deadline and permitted evidence action.
- Private signing key is unavailable to the harness and never accepted from
  CLI path, environment, transcript or payload.
- Evidence decision is locally derived from verified transcript/ledger and
  provenance results; caller-supplied PASS is rejected.
- Publication is root-owned, atomic, durable, idempotent and rollback-safe;
  prior evidence cannot be silently overwritten.
- Failure produces nonzero status and no PASS artifact.

## 7. Work C — Linux provenance preflight

### Scope

Create a fail-closed Linux-only preflight that yields a typed provenance result
consumed by B and D. A non-Linux host or unavailable exact topology returns
BLOCK and cannot be overridden.

### Acceptance criteria

- Resolve the real nginx executable without trusting caller `PATH`; open it by
  descriptor and verify root ownership, mode, type, link count and executable
  bytes before running exact descriptor-pinned `nginx -T`.
- Parse `nginx -T` source boundaries and bind the actual canary location’s
  effective auth, `auth_basic/auth_request off` behavior, identity-header
  clearing/injection, AF_UNIX upstream and included allowlist bytes.
- Descriptor-verify every config/source/include used for the conclusion;
  sibling/nested auth cannot satisfy the canary location.
- Verify router AF_UNIX socket inode/type/owner/mode and peer identity using
  Linux `SO_PEERCRED`; direct or wrong-UID socket callers remain A-only.
- Bind running nginx/router/A/B process identity to executable descriptor,
  immutable release manifest, git/artifact digest and expected service role.
- Verify routing, control, budget and outcome ledger roots/heads, ownership,
  modes, signatures and cross-ledger identity.
- Emit canonical provenance bytes plus digest; unknown/missing/mutable/raced
  input is BLOCK.
- On Darwin, container-only simulation, absent nginx or mismatched topology,
  tests may document development coverage but release verdict is
  `BLOCKED_EXTERNAL_LINUX`.

## 8. Work D — adversarial and release-host gate

### Scope

Run A only after B/C establish authority and provenance. Attack every trust
boundary and generate the only candidate release-host verdict for #1019.

### Acceptance criteria

- Fake `PATH`, alias/wrapper nginx, replaced executable/config/include, symlink,
  hardlink, owner/mode mutation and descriptor/path swap all BLOCK.
- Synthetic/private-key-shaped signer input, harness-owned signer, forged or
  stale #997 authorization and caller-supplied PASS all BLOCK.
- Transcript/ledger/artifact/provenance deletion, truncation, duplication,
  reordering, mutation and cross-run splice all BLOCK through inclusion and
  canonical digest verification.
- Canonical Analyze/Compare request mismatch, reordered assets, changed query
  mode, ramp/release/control mismatch and raw identity/query leakage all BLOCK.
- The gate independently recomputes scenario inclusion and requires all 12
  ordered scenario IDs exactly once.
- The gate proves disabled/stop/rollback terminal traffic is 100% A and that
  no B reservation occurs after the barrier or on control drift.
- Malformed/5xx/timeout failure status is durable, fallback is A, and autostop
  causality orders observation → stop barrier → zero new B → final 100% A.
- Any skipped, synthetic, fixture, non-Linux or incomplete run returns
  `BLOCKED_EXTERNAL_LINUX`/`BLOCK`, never PASS.
- Only a real release-host run with B/C verified and all adversarial cases
  passing may emit signed PASS evidence.

## 9. Dependency and evidence flow

```text
#1014 + #1021 + #1020
          |
          +--------> A: real Handler ingress harness --------+
          |                                                  |
          +--------> B: evidence role / authorization -------+
                              |                               |
                              +--> C: Linux provenance -------+
                                                              |
                                                              v
                                                D: adversarial release gate
                                                              |
                                                              v
                                                #1019 disposition / K3
```

A and B may start in parallel after their contracts are agreed. C depends on
B’s trusted-input/result contract. D depends on completed A, B and C. #1019
does not unblock #1015/#1016/#1017 while any child is open or external Linux
evidence is BLOCK.

## 10. Validation and review

Each implementation PR requires:

- scoped branch and linked child issue;
- focused regression/adversarial tests;
- exact-commit `.githooks/pre-push` PASS;
- named reviewer attestation;
- gray acceptance/truthfulness review;
- Harper CISO/cost review;
- `/codex-review` adversarial review;
- Eye only if admin/operator/user-visible behavior changes; otherwise explicit
  `Eye N/A` rationale.

No author self-approval, admin merge, protection override, synthetic release
receipt, or backdated review is permitted.

## 11. Honest external Linux gate

Implementation may be complete on a development host while release evidence is
still BLOCK. The only valid dispositions are:

- `PASS`: real Linux release host, exact nginx/auth/AF_UNIX topology, two real
  releases, all 12 scenarios, B/C trust checks and D attacks pass;
- `FAIL`: an acceptance or adversarial test fails;
- `BLOCKED_EXTERNAL_LINUX`: exact Linux host/topology/authority is unavailable
  or unverifiable.

`BLOCKED_EXTERNAL_LINUX` keeps #1019 and #879 open. It cannot be converted to
PASS by Docker simulation, Darwin skips, fixtures, synthetic signers,
temporary toy handlers, or a manually asserted receipt.

## 12. CEO approval gate

**CEO APPROVED on 2026-07-30. Issue creation is authorized, but no issue has
been opened and implementation has not started.**

The approval is bound to the pre-approval plan commit
`806a7e301ea387798b7d187f4c3f7677a20d267a`. CEO explicitly approved all six
controls:

1. b22 is void and no completion evidence is inherited from it;
2. the A/B/C/D split and dependency graph;
3. the 12 ordered scenarios and A’s hard split rule;
4. all Harper P0-1..4 mappings;
5. external Linux as an honest non-overridable BLOCK condition;
6. no issue exceeds 12 hours and no production deployment/promotion is in
   scope.

This approval does not assert that any child acceptance criterion has passed,
that external Linux is available, or that #1019/#879 may close.
