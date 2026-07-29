# Issue #998 — #748 merged PR review / acceptance evidence remediation plan

- Owner: gray (CPO)
- Parent: #748
- Plan issue: #998
- Baseline: `origin/develop@46fa9836eef314a8825589d2ff3cb0bda1df8e89`
- Estimate: 10 hours (hard limit: 12 hours)
- Output type: audit evidence and follow-up issue decisions; this plan does not authorize feature implementation
- Status: **CEO APPROVED — execution authorized; Phase 1 not started**
- CEO approval date: 2026-07-30
- Approval target: plan commit
  `cc9888d1f9ee868def4af895b5a7bb96c8b215ff`

## 1. Decision to correct

The statement “11/11 complete; only natural data accumulation remains” is not
supported by the available evidence and must not be repeated.

The eleven historical PRs are merged and their merge commits are ancestors of
the baseline `develop`. That proves code lineage only. It does not prove that
every original acceptance criterion was met, that required reviews happened at
merge time, that current behavior still satisfies the criteria, or that
observation-dependent release gates passed.

Historical missing review evidence must be recorded as a **historical gap**. A
fresh review may establish the state of the current exact commit, but it must
not be dated or described as a review of the historical merge event.

## 2. Scope and non-goals

This audit will:

1. bind each original PR to its actual merge SHA;
2. map every original issue acceptance criterion to reproducible evidence,
   a current finding, or an explicit external-observation blocker;
3. perform fresh gray, Harper, Eye, and `/codex-review` reviews where required;
4. produce exact-commit local gate evidence and a CEO disposition;
5. reopen or create focused remediation issues for every unmet criterion.

This audit will not:

- rewrite Git or backfill approvals;
- claim historical review occurred when no contemporaneous evidence exists;
- turn fixtures, synthetic receipts, temporary handlers, or unit tests into
  production/release evidence;
- hard-code an expected BTC/BNB ordering;
- close #748, #998, or any remediation item while a required external gate is
  BLOCK;
- modify the #1020 worktree or its branch.

## 3. Verified merge matrix and audit targets

All SHAs below were independently verified as ancestors of the stated baseline.
“Review route” is the required fresh review against the final audit commit, not
a claim about historical approval.

| Track | Issue | PR | Verified merge SHA | Primary acceptance evidence to re-check | Review route |
|---|---:|---:|---|---|---|
| A | #869 | #891 | `46cd3f04e1626785fa59fc4beb02e9a4b73bb790` | Five normalized dimensions, PIT/unknown/conflict rules, identity-blind metamorphic tests, v1 compatibility | gray + Harper + `/codex-review` |
| B | #873 | #900 | `67ecc12efaf67e89c18df37267ead319ef5cfc12` | Pinned source revision/hash/coordinates, two protocol types, byte-stable offline rebuild, stale/future/conflict fail-closed | gray + Harper + `/codex-review` |
| C | #870 | #904 | `a8e54461a566df918e8cc0fb54566c9ff965716a` | Separate control planes, two source families, source withdrawal/freshness replay, no documentation-as-control proof | gray + Harper + `/codex-review` |
| D | #872 | #902 | `31a69be645bc49c9473b585cec508379adf6ddc2` | Entity-resolution requirements, address/entity distinction, licensing/freshness/reproducibility, honest unknown disposition | gray + `/codex-review`; Harper if cost follow-up exists |
| E | #871 | #903 | `f92810c30319cc4f89cdeb1834dca71b34ea7511` | Idempotent shadow event, official-output isolation, provenance sanitization, dashboard coverage/missing/stale/conflict/delta | gray + Harper + Eye + `/codex-review` |
| F | #874 | #910 | `71bfaebee9cc4f9b79becf16f0567fd9a23e8850` | Five assets/profiles, every state per dimension, PIT cutoff/seed/data version, manipulation and coverage-bias metrics | gray + Harper + `/codex-review` |
| G | #875 | #961 | `bf1b41dcd1c8500eda078b87eea48377fc4cad01` | Versioned thresholds, signed decision receipt, BLOCK immutability, 200 observations/5 assets/30 days, optional mature-label calibration | gray + Harper + `/codex-review` |
| H | #876 | #976 | `472183430c7227df6afdbe3ccf4b20aaafb43cd0` | Canonical-only integration, eligible PIT facts, flag-off byte parity, exact-zero invalid facts, shadow-only before promotion | gray + Harper + `/codex-review` |
| I | #878 | #981 | `2ffe69fd239b5fd7dd27a2056f20663842aea3fb` | Capability/receipt-driven state, plain-language five dimensions, malformed fail-closed, desktop/mobile/zoom/locale/error UI | gray + Harper + Eye + `/codex-review` |
| J | #877 | #980 | `fbd01340d99c8f9d51e80addf3d9621207779e72` | Immutable A/B digests, identical PIT replay, real non-production A→B→regression→A, SLO receipt, A health/history retention | gray + Harper + `/codex-review` |
| K | #879 | #984 | `ff8db7bb521c89e32da5725b7aea82fd66ce509e` | Authenticated nginx→AF_UNIX topology, signed budgets, real two-release ingress, stop/rollback/A health, signed release gate | gray + Harper + Eye where UI changes + `/codex-review` |

## 4. Evidence classification

Every acceptance-criterion row in the audit ledger must receive exactly one
classification:

### 4.1 Programmatically provable now

Examples include:

- merge SHA ancestry and changed-file lineage;
- schemas, immutable contracts, deterministic fixtures and byte parity;
- fail-closed behavior for malformed, stale, future, conflicted, non-finite,
  duplicated, or unauthorized inputs;
- identity-rename and input-permutation metamorphic properties;
- source/redaction rules and signed-receipt verification;
- local tests, lint, build, data checks, `git diff --check`;
- static proof that the official path remains unchanged while a flag is off.

These require exact command, exact commit, exit status, and artifact/log digest.
A passing unit test is evidence only for the behavior it actually exercises.

### 4.2 Requires external or elapsed observation

These cannot be completed by adding assertions or synthetic fixtures:

- #875: at least 200 eligible PIT observations, at least 5 assets, and at least
  30 elapsed days;
- #875: Brier/ECE non-inferiority only if mature outcome labels exist;
- #872: real entity-resolved holder history, licensing permission, freshness,
  cross-chain/custodian/bridge/burn/locked/lost-key deduplication;
- #870: actual two-family eligible governance/control evidence where required;
- #877: a real non-production release drill using immutable A and B artifacts,
  observed rollback SLO, surviving history, and post-rollback A health;
- #879: actual authenticated nginx ingress, Linux AF_UNIX peer identity, signed
  per-ramp request/model/monetary budget reconciliation, two real releases,
  stop/rollback behavior, and signed release evidence;
- Eye checks that require a running actual branch UI at the named viewport,
  locale, zoom, overflow, transition, and error states.

If infrastructure or sufficient observations are unavailable, the result is
`BLOCKED-EXTERNAL` or `REMAIN-SHADOW`, never PASS. “Natural accumulation” is a
dependency, not completed work.

### 4.3 Historical gap

For each PR, inspect the PR body, comments, review records, linked artifacts,
and commit-bound evidence for the originally required reviewer, Harper, Eye,
and `/codex-review` gates.

- Existing contemporaneous evidence is cited verbatim by URL and commit.
- Missing evidence is recorded as `HISTORICAL-GAP`.
- A fresh review is labeled `FRESH-REVIEW@<sha>`.
- Fresh evidence may support current acceptance, but never retroactively
  converts `HISTORICAL-GAP` into a historical approval.

## 5. Review routing and independence

| Gate | Scope | Required output |
|---|---|---|
| gray (CPO) | All 11 tracks; methodology, product truthfulness, acceptance mapping, no conclusion-first ranking | Criterion ledger with PASS/FAIL/BLOCKED-EXTERNAL/HISTORICAL-GAP and cited evidence |
| Harper (CISO) | A, B, C, E, F, G, H, I, J, K; plus D only if cost/licensing action is proposed | Security/cost findings, threat cases, disposition bound to commit |
| Eye CLI | E and I; K only for user/admin UI touched by the audited state | Actual-branch desktop/mobile, zh-TW/en, 200% zoom, overflow, state transition and error evidence |
| `/codex-review` | Every track, run adversarially against the current exact commit and criterion ledger | Findings with severity, file/line or evidence target, fix/block disposition |
| CEO | Plan approval before work; final acceptance after all fresh reviews and local gates | Explicit `APPROVE`, `REMEDIATE`, `REMAIN-SHADOW`, or `BLOCKED-EXTERNAL` |

The PR author cannot self-approve on GitHub. The final record uses a
commit-bound reviewer attestation and does not fabricate an approval.

## 6. Execution plan (10 hours)

### Phase 0 — CEO plan gate (0.25 h)

- CEO reviews this plan, its scope, evidence taxonomy, routing, and timebox.
- No audit execution begins without an explicit `CEO APPROVED` record.
- Any requested scope exceeding 12 hours becomes a separate issue.

### Phase 1 — Build immutable audit ledger (1.5 h)

- Pin the audit commit from the latest `origin/develop`.
- Export the 11 PR merge SHAs, merge timestamps, issue criteria, changed files,
  PR review/comment history, and existing evidence links.
- Verify every merge SHA is an ancestor of the audit commit.
- Create one ledger row per acceptance criterion; do not summarize multiple
  criteria into one ambiguous checkbox.
- Mark missing contemporaneous gates as `HISTORICAL-GAP`.

Deliverable: machine-readable ledger plus human-readable matrix, both bound to
the audit commit.

### Phase 2 — Programmatic acceptance replay (2.5 h)

- Map each programmatically provable criterion to existing tests/contracts.
- Run focused tests first, then the repository-local `.githooks/pre-push`.
- Add no feature code in #998. If an acceptance criterion lacks adequate proof,
  record FAIL and open a scoped remediation issue rather than weakening the
  criterion.
- Record exact commands, exit codes, test counts, skipped-test reasons, and
  artifact/log digests.
- Reject synthetic, fixture-only, monkeypatched, or platform-skipped evidence
  wherever the criterion calls for real topology or release behavior.

Deliverable: exact-commit programmatic evidence bundle.

### Phase 3 — Fresh routed reviews (3.0 h)

- gray reviews all ledger rows for methodology and truthful product claims.
- Harper reviews the security/cost tracks and explicitly checks fail-closed
  boundaries, key custody, authorization, budget accounting, rollback, and
  evidence authenticity.
- Eye scans E/I and any applicable K UI against the actual audited branch.
- `/codex-review` attacks each track and the cross-track dependency chain.
- Resolve documentation/evidence defects within #998; feature defects become
  separate ≤12-hour issues with dependencies and acceptance criteria.
- Rerun every affected gate after an evidence-document correction.

Deliverable: four clearly separated review records with no backdating.

### Phase 4 — External evidence reconciliation (1.25 h)

- Query the canonical observation/gate artifacts without mutating them.
- Report actual eligible observation count, distinct asset count, elapsed date
  range, known-dimension/source-family coverage, and current #875 disposition.
- Check whether mature outcome labels exist before reporting Brier/ECE.
- Locate real #877/#879 topology/drill/reconciliation receipts and validate
  signature, release/artifact digests, timestamps, environment, and health.
- Where evidence is unavailable or insufficient, record
  `BLOCKED-EXTERNAL`/`REMAIN-SHADOW` and the exact future evidence needed.

Deliverable: current-state evidence snapshot with collection timestamp and
source identity.

### Phase 5 — Findings, remediation graph, final gates (1.5 h)

- Open or update one focused issue per independently deliverable finding;
  every issue is ≤12 hours and declares dependencies.
- Keep #748 and #998 open while required remediation issues or external gates
  remain unresolved.
- Run the final local pre-push gate against the exact evidence commit.
- Publish a final table for all 11 tracks; no aggregate “11/11” unless every
  criterion is PASS and no required blocker remains.
- CEO reviews the ledger, fresh reviews, external evidence, open dependencies,
  and exact-commit gate evidence.

Deliverable: CEO disposition. Only explicit CEO approval permits #998 closure;
#748 closure additionally requires its product outcome and all dependent
release criteria to be satisfied.

## 7. Required audit ledger schema

Each row must contain:

```text
track
issue
pr
merge_sha
acceptance_criterion
evidence_class = PROGRAMMATIC | EXTERNAL | HISTORICAL_REVIEW
evidence_uri_or_command
evidence_commit
observed_at
result = PASS | FAIL | BLOCKED_EXTERNAL | REMAIN_SHADOW | HISTORICAL_GAP
review_route
finding_or_blocker
remediation_issue
```

No blank result, inferred approval, or unbound screenshot is acceptable.

## 8. Dependency-sensitive disposition rules

- A merged PR is not equivalent to accepted behavior.
- H cannot be release-promoted while G is BLOCK.
- I may truthfully display shadow state while G is BLOCK, but cannot display
  official state without a valid promotion capability/receipt.
- J’s synthetic or mocked drill cannot satisfy its real non-production drill
  criterion.
- K remains incomplete until its current remediation dependency graph and real
  topology/release evidence pass; K2a alone is not K2 completion.
- Holder concentration may remain unknown without blocking other shadow
  dimensions, but it must not receive a fabricated numeric value.
- BTC ranking above BNB is not an acceptance criterion; the methodology remains
  asset-identity-blind.

## 9. Exit criteria

#998 may be proposed for closure only when:

1. every original criterion has a populated ledger row;
2. all 11 merge SHAs and current ancestry are verified;
3. historical gaps are explicitly retained;
4. all fresh routed reviews are recorded against an exact commit;
5. all programmatically provable criteria are PASS or linked to open remediation;
6. every external criterion is supported by authentic evidence or explicitly
   marked BLOCKED/REMAIN-SHADOW;
7. the local pre-push gate is green against the final evidence commit;
8. no unresolved finding is hidden by an aggregate completion percentage;
9. CEO records the final disposition.

The valid honest outcome may be “audit complete, product remains shadow and
external gates remain blocked.” It may not be “11/11 product complete” unless
all acceptance and release evidence actually passes.

## 10. CEO approval checkpoint

**Current state: CEO APPROVED on 2026-07-30. Phase 1 is authorized but has not
started.**

The approval is bound to the pre-approval plan commit
`cc9888d1f9ee868def4af895b5a7bb96c8b215ff`. CEO explicitly approved all five
required controls:

1. the 10-hour scope and no-feature-code boundary;
2. the historical-gap policy, including the prohibition on backdated approval;
3. the programmatic versus external/elapsed-observation evidence split;
4. the gray/Harper/Eye/`/codex-review` routing;
5. the rule that #748 remains open while remediation issues or external
   blockers remain.

This approval authorizes the plan as written. It does not assert that Phase 1
has begun, that any audit criterion has passed, or that #748/#998 may close.
