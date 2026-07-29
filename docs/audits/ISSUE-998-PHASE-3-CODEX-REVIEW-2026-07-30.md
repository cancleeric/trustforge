# Issue #998 Phase 3 — fresh `/codex-review` adversarial review

- Reviewer route: `/codex-review`
- Exact commit reviewed:
  `b9004e86b22bbd31469d13bf618503fc028e8670`
- Date: 2026-07-30
- Scope: all 70 A–K criteria plus cross-track dependency integrity
- Feature changes: none
- Historical rows rewritten: none

## Disposition

**REMAIN_SHADOW / RELEASE BLOCKED / REMEDIATION REQUIRED**

| Result | Count |
|---|---:|
| `CODEX_PASS` | 49 |
| `REMEDIATE` | 5 |
| `DEFER_EXTERNAL` | 16 |
| Historical `HISTORICAL_GAP` rows retained | 70 |

This is a fresh review of the exact current commit. It does not backdate an
approval onto any of the eleven historical merges. A green repository gate
does not convert missing architecture, authentic release evidence, elapsed
observations, or an actual visual inspection into PASS.

## Findings

### P0 — four release-evidence boundaries remain open

The Harper P0 split is valid and complete for the currently known K release
boundary:

| Issue | Adversarial conclusion |
|---:|---|
| #1031 | Required real ordered nginx → AF_UNIX → router → two real Handler transcript is absent |
| #1032 | Required independent root-owned, descriptor-pinned evidence authority is absent |
| #1033 | Required canonical Linux topology/process/artifact/ledger provenance preflight is absent |
| #1034 | Required independent recomputation and tamper/splice/redaction adversarial gate is absent |

All four issues are OPEN. Their dependency order is coherent: #1032 can run in
parallel with #1031; #1033 depends on #1032 and #1021; #1034 depends on
#1031–#1033. #1019 and K-1–K-5 therefore remain blocked. Synthetic, hermetic,
fixture, skipped, or signer-self-asserted output is not release evidence.

### P1 — confirmed programmatic failures

1. **C-1 and C-5 — #1035.** `control_dispersion` still lacks typed,
   independently replayable validator/miner/node/governance planes and there
   is no canonical before/at/after-cutoff source-withdrawal replay. Provenance
   prose and multiple hosts cannot satisfy either criterion.
2. **H-1 — #1036.**
   `src/trustforge/agent/shadow_runtime.py:267-282` imports and applies the
   candidate in the application/agent layer. `trustforge_core` has no sole
   candidate composition boundary, so duplicate/noncanonical application has
   not been made impossible.
3. **J-2 — #1037.** `scripts/run_rollback_drill.py:2-15` explicitly describes
   a hermetic loopback drill. It imports routing policy but probes temporary A/B
   handlers directly; it does not traverse the actual release-router request
   path.

The three remediation issues are OPEN, scoped at 10h, 12h and 10h, and contain
explicit acceptance criteria and review routes.

### P1 — additional finding: I-3 is not programmatically proved

Phase 2 and the two routed reviews treated I-3 as PASS, but the exact source
does not support the criterion:

- `frontend/src/components/AssetIntrinsicShadowPanel.tsx:54-59` says the
  backend does not emit the official struct and its internal receipt schemas
  remain pending.
- `frontend/src/components/AssetIntrinsicShadowPanel.tsx:377-398` accepts
  `mode=official` from a structurally valid client payload containing arbitrary
  nonblank `receipt_id`/`policy_digest`, `decision=pass`, and any object as
  `calibration_claim`.
- No signature, policy digest, observation root, release identity, capability
  authority, or current BLOCK receipt is verified on this path.

Therefore the visible official state is not yet demonstrably driven by a
verified release capability and promotion receipt. I-3 is `REMEDIATE`, not
PASS. A separate ≤12-hour issue is required to bind the backend-emitted
official payload to a verified signed promotion receipt/capability and to make
forged, stale, wrong-policy, wrong-release and current-BLOCK payloads fail
closed. This audit does not open or implement that feature issue.

## Cross-track dependency attack

| Dependency | Result |
|---|---|
| C → F/G/H | C-1/C-5 remain defects; benchmark/gate PASS rows cannot erase missing typed-plane and withdrawal proofs |
| D → product display | Holder concentration remains UNKNOWN without entity-resolved external history; no fabricated numeric value is authorized |
| E → G | Shadow observations are narrow programmatic evidence; G still needs authentic elapsed observations |
| F → G | Symbol-blind benchmark passes narrowly; real coverage remains external |
| G → H | Current promotion is BLOCK; H must remain shadow-only |
| G/H → I | I may display shadow, but official display is additionally blocked by G and the new I-3 receipt-authenticity finding |
| J → K | Hermetic J evidence cannot satisfy real K topology or rollback evidence |
| #1020/#1021 → #1019 | Signed budgets/provisioning are prerequisites only; they do not prove real ingress |
| #1031–#1034 → #1019/K | All four release-evidence boundaries must pass before K or real rollback can be accepted |

No dependency inversion or aggregate green test result permits promotion.

## Track disposition

| Track | `/codex-review` result |
|---|---|
| A | 6 PASS |
| B | 6 PASS |
| C | 4 PASS, 2 REMEDIATE |
| D | 3 PASS, 2 DEFER_EXTERNAL |
| E | 6 PASS |
| F | 6 PASS, 1 DEFER_EXTERNAL |
| G | 5 PASS, 2 DEFER_EXTERNAL |
| H | 7 PASS, 1 REMEDIATE |
| I | 5 PASS, 1 REMEDIATE, 1 DEFER_EXTERNAL |
| J | 1 PASS, 1 REMEDIATE, 5 DEFER_EXTERNAL |
| K | 5 DEFER_EXTERNAL; four P0 release blockers |

## Independent-gate boundary

This record does not perform or replace:

- actual-branch Eye for E/I;
- Phase 4 external and elapsed-observation reconciliation;
- feature remediation for #1031–#1037 or the new I-3 finding;
- release, `main`, or production work.

#748 and #998 remain OPEN. The only honest current disposition is that the
five-dimensional framework has substantial shadow-only programmatic coverage,
five criteria require engineering remediation, sixteen require external or
elapsed evidence, and release evidence remains blocked.
