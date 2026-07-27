# Research remediation retrospective — 2026-07-27

## Disposition

The retrospective result is **REMEDIATION REQUIRED**.  Merge status and a
passing test count are not, by themselves, evidence that a research claim or
release gate is valid.  Existing source-reputation and conformal artifacts
must remain research-only until the remediation issues linked below have
passed their own review and validation gates.

This document records evidence observed after the original merges.  It does
not retroactively create an approval, reviewer identity, Eye result, or
`/codex-review` result that did not exist at merge time.

## Scope

| Item | Original disposition | Retrospective finding | Remediation |
|---|---|---|---|
| PR #739 / merge `14dc66b0de3448d715034c8c69b9fd84893499b2` | Merged | No GitHub review record. PR body reports Eye, Codex review, and pre-push, but no separate commit-bound review comment exists. The PR also included an unrelated AWS usage-assumption document. | Governance evidence recorded by #753; PIT/sample-contract behavior is revalidated by #750. |
| PR #743 / merge `aecbf2018a3c52503aa61b25bba0d00473615707` | Merged | No GitHub review record or commit-bound Eye/`/codex-review` evidence. | Full remediation gate and retrospective disposition under #753. |
| PR #744 / merge `1c90762382c9aa28c54dee6c3cc9f7f82eec09b6` | Merged | No GitHub review record. Later real-sample work introduced a random temporal split and a non-AUC metric named `auc_proxy`. | Chronological and research-integrity remediation under #752. |
| PR #745 / merge `12594b321d8618c5202d261d657282298c0fb1ea` | Merged | No GitHub review record. Later source-reliability work introduced a non-AUC metric named `auc_proxy` and a non-ISO cutoff. | Honest-metrics and artifact remediation under #751. |
| PR #746 / merge `ff919753ed36efbd34d236f3c6577f2c412594a3` | Merged | No GitHub review record. PR body explicitly says the full local pre-push gate was skipped, contrary to the current repository workflow. | This remediation PR runs the complete gate and records the missing historical evidence without fabricating it. |
| Commit `e8817adb` | Direct commit | No associated GitHub PR. | Re-reviewed and corrected by #750/#751/#752 as applicable. |
| Commit `64845295` | Direct commit | No associated GitHub PR; sample builder used `eval`, did not enforce its stated PIT rule, and did not preserve same-day source families. | #750. |
| Commit `ffb3e441` | Direct commit | No associated GitHub PR; `auc_proxy` was not ROC AUC and `training_cutoff` was an epoch fragment rather than an ISO date. | #751. |
| Commit `9c112f99` | Direct commit | No associated GitHub PR; historical evaluation used seeded random shuffling rather than a chronological split. | #752. |

## Required remediation evidence

The final version of this record must contain:

- #750 merge SHA, named reviewer attestation, harper security disposition,
  Eye result, `/codex-review` result, targeted tests, and full pre-push result.
- #751 merge SHA, named reviewer attestation, Eye result, `/codex-review`
  result, artifact schema/version and honest metric disposition.
- #752 merge SHA, named reviewer attestation, Eye result, `/codex-review`
  result, chronological split boundaries, targeted tests, and full pre-push
  result.
- #753 exact commit, named reviewer attestation, Eye result,
  `/codex-review` result, and full pre-push result.
- Read-back receipts for the Obsidian project note, TrustForge Wiki update,
  and both SkillHub skills.

## Release boundary

No item in this remediation authorizes production promotion of source
reputation or conformal prediction.  A negative experiment is a valid
research result.  Promotion requires the documented source-family, temporal,
PIT, correctness, error, abstention, and review gates to pass with artifacts
bound to the exact release commit.
