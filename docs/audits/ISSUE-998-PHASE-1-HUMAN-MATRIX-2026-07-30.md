# Issue #998 Phase 1 — #748 historical acceptance evidence matrix

- Baseline: `develop@003ad3568366471945b80dff16f34ccb0a2d5f12`
- Collection date: 2026-07-30
- Scope: inventory only; no fresh acceptance review and no feature changes
- Machine ledger: `docs/audits/issue-998-criterion-ledger.json`
- Status: Phase 1 complete; Phase 2 has not started

## Honest interpretation

All 11 merge SHAs are ancestors of the baseline. GitHub returned zero formal
review records for all 11 PRs. Each PR has one author-posted, merge-time
attestation comment. Those comments are contemporaneous evidence of what the
author claimed and tested; they are not independent GitHub approvals and do not
fill a missing gray, Harper, Eye, or `/codex-review` artifact.

All 70 criterion rows therefore retain `HISTORICAL_GAP`. This is an evidence
status, not a finding that all 70 implementations fail. Programmatic acceptance
is deliberately deferred to Phase 2, fresh routed review to Phase 3, and
external/elapsed observation reconciliation to Phase 4.

## Merge and historical evidence matrix

| Track | Issue / PR | Merge SHA (ancestor) | Contemporaneous URL | Evidence found | Historical gaps |
|---|---|---|---|---|---|
| A | #869 / #891 | `46cd3f04e1626785fa59fc4beb02e9a4b73bb790` yes | [attestation](https://github.com/cancleeric/trustforge/pull/891#issuecomment-5111414731) | Author claims CEO, Harper and `/codex-review`; no formal review | gray |
| B | #873 / #900 | `67ecc12efaf67e89c18df37267ead319ef5cfc12` yes | [attestation](https://github.com/cancleeric/trustforge/pull/900#issuecomment-5112016220) | CEO author attestation; PR body says Harper and `/codex-review` pending | gray, Harper, `/codex-review` |
| C | #870 / #904 | `a8e54461a566df918e8cc0fb54566c9ff965716a` yes | [attestation](https://github.com/cancleeric/trustforge/pull/904#issuecomment-5112203991) | CEO author attestation; PR body says Harper and `/codex-review` pending | gray, Harper, `/codex-review` |
| D | #872 / #902 | `31a69be645bc49c9473b585cec508379adf6ddc2` yes | [attestation](https://github.com/cancleeric/trustforge/pull/902#issuecomment-5112016451) | CEO author attestation only | gray, `/codex-review` |
| E | #871 / #903 | `f92810c30319cc4f89cdeb1834dca71b34ea7511` yes | [attestation](https://github.com/cancleeric/trustforge/pull/903#issuecomment-5112016707) | CEO author attestation; PR body says Harper and `/codex-review` pending | gray, Harper, Eye, `/codex-review` |
| F | #874 / #910 | `71bfaebee9cc4f9b79becf16f0567fd9a23e8850` yes | [attestation](https://github.com/cancleeric/trustforge/pull/910#issuecomment-5112460930) | CEO author attestation; PR body says Harper and `/codex-review` pending | gray, Harper, `/codex-review` |
| G | #875 / #961 | `bf1b41dcd1c8500eda078b87eea48377fc4cad01` yes | [attestation](https://github.com/cancleeric/trustforge/pull/961#issuecomment-5116459207) | CEO author attestation; receipt explicitly BLOCK; Harper and `/codex-review` pending | gray, Harper, `/codex-review` |
| H | #876 / #976 | `472183430c7227df6afdbe3ccf4b20aaafb43cd0` yes | [attestation](https://github.com/cancleeric/trustforge/pull/976#issuecomment-5117206892) | Author claims CEO and Harper; no distinct `/codex-review` artifact | gray, `/codex-review` |
| I | #878 / #981 | `2ffe69fd239b5fd7dd27a2056f20663842aea3fb` yes | [attestation](https://github.com/cancleeric/trustforge/pull/981#issuecomment-5117651057) | CEO author attestation; Eye explicitly deferred until after merge | named reviewer, gray, Harper, Eye, `/codex-review` |
| J | #877 / #980 | `fbd01340d99c8f9d51e80addf3d9621207779e72` yes | [attestation](https://github.com/cancleeric/trustforge/pull/980#issuecomment-5117644663) | CEO author attestation; hermetic drill; Harper advice described as “internalized” | gray, Harper, `/codex-review` |
| K | #879 / #984 | `ff8db7bb521c89e32da5725b7aea82fd66ce509e` yes | [attestation](https://github.com/cancleeric/trustforge/pull/984#issuecomment-5118088414) | Author claims CEO and Harper; hermetic/synthetic remain-shadow result | gray, `/codex-review`; current real-topology criteria not proven |

## Counts

| Measure | Count |
|---|---:|
| Tracks | 11 |
| Acceptance-criterion rows | 70 |
| Merge SHAs verified as baseline ancestors | 11 |
| GitHub formal review records | 0 |
| Author attestation comments | 11 |
| Rows retaining historical gap | 70 |
| Programmatically verifiable rows | 54 |
| External or elapsed-observation rows | 16 |

Criterion count by track:

| A | B | C | D | E | F | G | H | I | J | K |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 6 | 6 | 6 | 5 | 6 | 7 | 7 | 8 | 7 | 7 | 5 |

## Material gaps already visible in Phase 1

1. #874’s PR body admits that `CONFLICTED` is unreachable through its PIT path
   and uses a direct-view probe. Phase 2 must determine whether F-2 actually
   covers every state for every dimension.
2. #875’s merge-time receipt was BLOCK: 3 observations, 3 assets and 2 days,
   below 200/5/30. No calibration claim was permitted without mature labels.
3. #876 declared a dependency on #875 with PASS, but its PR records #875 as
   BLOCK and retains shadow-only behavior. This is honest shadow behavior, not
   release promotion completion.
4. #878 explicitly merged without the required actual-branch Eye matrix.
5. #877’s evidence is described as hermetic; it cannot by itself prove the real
   non-production release drill, rollback health, or persistence criteria.
6. #879/#984 supplied a synthetic controller skeleton. The issue now correctly
   remains open with real nginx, AF_UNIX, signed-budget, two-release ingress,
   stop/rollback and signed release-gate dependencies.

## Phase boundary

No criterion is upgraded to PASS by this inventory. No historical approval is
backdated. #748 remains open. Phase 2 requires separate CEO authorization under
the approved #998 plan.
