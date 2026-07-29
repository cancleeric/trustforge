# Issue #998 Phase 3 — fresh gray CPO review

- Reviewer: gray (CPO)
- Exact commit reviewed:
  `30f2ee8511b32dc51ecb2a326c49f812fe975f0f`
- Date: 2026-07-30
- Scope: methodology and product truthfulness
- Rows reviewed: 70/70
- Feature changes: none

## Disposition

**REMAIN_SHADOW / REMEDIATION REQUIRED**

The five-dimension methodology remains asset-identity-blind and does not
hard-code BTC above BNB. Current shadow labeling is truthful. That does not
support “11/11 complete”:

| Gray result | Count |
|---|---:|
| `GRAY_PASS` | 50 |
| `REMEDIATE` | 4 |
| `DEFER_EXTERNAL` | 16 |

All 70 historical gaps remain recorded. This fresh review is bound to the
current exact commit and does not backdate a gray approval onto the eleven
historical merges.

## Remediation findings

| Rows | Issue | Finding |
|---|---:|---|
| C-1, C-5 | [#1035](https://github.com/cancleeric/trustforge/issues/1035) | Control planes lack typed independent replay; source-withdrawal PIT proof is absent |
| H-1 | [#1036](https://github.com/cancleeric/trustforge/issues/1036) | Candidate application is not behind a sole canonical core composition boundary |
| J-2 | [#1037](https://github.com/cancleeric/trustforge/issues/1037) | Hermetic drill does not traverse the actual release-router request path |

The issue estimates are 10h, 12h and 10h respectively and remain within the
approved per-issue limit.

## Track review

| Track | Gray disposition | Product-truthfulness note |
|---|---|---|
| A | 6 PASS | Neutral, reproducible methodology; no conclusion-first ranking |
| B | 6 PASS | Portable fixtures prove programmatic method, not real-world rank |
| C | 4 PASS, 2 REMEDIATE | UNKNOWN/conflict behavior is honest; plane separation and withdrawal replay remain incomplete |
| D | 3 PASS, 2 DEFER | Holder concentration stays unknown without entity-resolved external history |
| E | 6 PASS | Shadow is isolated from official output; visual truth remains Eye scope |
| F | 6 PASS, 1 DEFER | Benchmark is symbol-blind; real coverage still requires observation reconciliation |
| G | 5 PASS, 2 DEFER | Policy correctly remains BLOCK without 200/5/30 and mature labels |
| H | 7 PASS, 1 REMEDIATE | Shadow-only behavior is honest, but canonical composition is incomplete |
| I | 6 PASS, 1 DEFER | Copy/contract are truthful; actual-branch Eye remains outstanding |
| J | 1 PASS, 1 REMEDIATE, 5 DEFER | Hermetic input parity is narrow evidence, not a real rollback drill |
| K | 5 DEFER | Real nginx/Linux/two-release/restart evidence remains external and #1019-dependent |

## Required independent gates

This record deliberately does not perform or replace:

- Harper CISO/cost review for the required security/cost tracks;
- actual-branch Eye for E/I and any applicable K UI;
- `/codex-review` adversarial review across all tracks;
- Phase 4 external/elapsed-observation reconciliation.

Those gates must produce their own exact-commit records. Missing evidence
cannot be inferred from this gray disposition.

## Closure statement

#748 and #998 remain open. The only truthful current summary is:

> The symbol-blind five-dimension shadow framework has substantial
> programmatic coverage, but four implementation criteria require remediation
> and sixteen criteria still require external or elapsed evidence. Promotion
> and real release claims remain blocked.
