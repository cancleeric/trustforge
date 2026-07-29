# Issue #998 Phase 3 — fresh Harper CISO/cost review

- Reviewer: Harper (CISO)
- Exact commit reviewed:
  `30f2ee8511b32dc51ecb2a326c49f812fe975f0f`
- Date: 2026-07-30
- Scope: security, cost, authorization, key custody, fail-closed behavior,
  rollback and evidence authenticity for tracks A/B/C/E/F/G/H/I/J/K; D only
  for licensing and cost
- Rows reviewed: 67/70
- Feature changes: none

## Disposition

**REMAIN_SHADOW / RELEASE BLOCKED / REMEDIATION REQUIRED**

| Harper result | Count |
|---|---:|
| `HARPER_PASS` | 48 |
| `REMEDIATE` | 4 |
| `DEFER_EXTERNAL` | 15 |
| `NOT_IN_HARPER_SCOPE` | 3 |

All 70 historical gaps remain unchanged. This fresh review is bound to the
current develop commit; it does not backdate Harper approval onto historical
merges and does not replace Eye or `/codex-review`.

## Findings and blockers

### P0 release blockers — 4

K remains externally blocked. Rejected commit `b22fba2` is not evidence and
must never be treated as a partial PASS. The approved child split maps the four
release-evidence boundary failures:

| Issue | P0 boundary |
|---:|---|
| #1031 | No real ordered nginx → AF_UNIX → router → two-Handler transcript with ledger inclusion proofs |
| #1032 | No distinct root-owned, descriptor-pinned evidence authority and authorization-bound signer |
| #1033 | No canonical Linux nginx/auth/socket/process/artifact/ledger provenance preflight |
| #1034 | No adversarial gate that independently recomputes scenarios, caps, causality and redaction |

Until all four are implemented and independently reviewed, #1019, K-1–K-5,
real rollback and production release evidence remain `BLOCKED_EXTERNAL`.
Synthetic, fixture, hermetic, skipped or signer-self-asserted artifacts cannot
lower this severity.

### P1 engineering findings — 4

| Rows | Issue | Security/integrity finding |
|---|---:|---|
| C-1, C-5 | #1035 | Control planes are not typed independently and source-withdrawal PIT replay is absent; provenance prose cannot substitute for fail-closed replay |
| H-1 | #1036 | Candidate application is not behind the sole canonical core composition boundary; duplicate or noncanonical application must remain impossible before promotion |
| J-2 | #1037 | The hermetic drill bypasses the actual release-router request path and therefore cannot prove authorization, ledger CAS, rollback or evidence authenticity |

These are P1 while the feature remains shadow-only and release claims remain
blocked. Any attempt to promote or use the affected evidence as release proof
would escalate the corresponding finding to P0.

## Security and cost conclusions

- **Fail closed:** UNKNOWN/conflict/invalid facts resolve to zero; flag-off and
  BLOCK promotion states do not silently become official output. The four
  engineering gaps above prevent promotion.
- **Authorization and key custody:** programmatic receipts and decision
  signatures are narrow evidence only. Real release authorization and signing
  key custody are not established until #1032/#1034 pass.
- **Budget:** threshold immutability and BLOCK semantics pass programmatically.
  The 200-observation/5-asset/30-day minimum, mature-label calibration, paid
  data availability and real per-ramp release budgets remain external.
- **Licensing:** D-4 honestly discloses licensing, freshness and
  reproducibility limitations. D-5 remains external; no paid-data purchase or
  availability claim is authorized by this review.
- **Rollback:** identical PIT input is only a hermetic property. Real A/B
  artifacts, actual router traversal, rollback SLO, retained A health and
  durable history remain blocked by #1037 and #1019/#1031–#1034.
- **Evidence authenticity:** Phase 2 programmatic evidence is useful for its
  stated scope. It cannot authenticate real topology, elapsed observations,
  production keys or release-host behavior.

## Track disposition

| Track | Harper result |
|---|---|
| A | 6 PASS |
| B | 6 PASS |
| C | 4 PASS, 2 REMEDIATE |
| D | 1 PASS, 1 DEFER_EXTERNAL, 3 NOT_IN_HARPER_SCOPE |
| E | 6 PASS |
| F | 6 PASS, 1 DEFER_EXTERNAL |
| G | 5 PASS, 2 DEFER_EXTERNAL |
| H | 7 PASS, 1 REMEDIATE |
| I | 6 PASS, 1 DEFER_EXTERNAL; Eye not performed |
| J | 1 PASS, 1 REMEDIATE, 5 DEFER_EXTERNAL |
| K | 5 DEFER_EXTERNAL |

## Independent-gate boundary

This record does not perform feature work, production deployment, actual
branch visual verification, `/codex-review`, or Phase 4 external/elapsed
reconciliation. It does not approve main/release/production. #748 and #998
must remain open and the asset-intrinsic candidate must remain shadow-only.
