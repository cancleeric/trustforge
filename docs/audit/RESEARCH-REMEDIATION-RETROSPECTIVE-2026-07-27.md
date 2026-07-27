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

## Historical scope and findings

| Item | Actual merge / commit | Retrospective finding | Remediation disposition |
|---|---|---|---|
| PR #739 | Merged as `14dc66b0de3448d715034c8c69b9fd84893499b2` from head `f583f6a90c0f4163da54f42617c237da86f2d27c` | GitHub has zero reviews and no commit-bound review comment. The PR body made Eye, Codex-review, and pre-push claims, but those claims are not an independent attestation; the change also included an unrelated AWS usage-assumption document. | The PIT/sample-contract path was revalidated and hardened by #750 / #754. The missing historical approval remains missing. |
| PR #743 | Merged as `aecbf2018a3c52503aa61b25bba0d00473615707` from head `65983cbdf84203ed8bc599fdb419bc60dba0cd34` | GitHub has zero reviews and no commit-bound Eye or `/codex-review` record. | Current remediation gates are recorded below; they do not manufacture evidence for #743. |
| PR #744 | Merged as `1c90762382c9aa28c54dee6c3cc9f7f82eec09b6` from head `24c47a81747e1126c6cff5806d6caa05cf22ef7f` | GitHub has zero reviews. Subsequent real-sample evaluation used seeded random shuffling and reported a non-AUC metric as `auc_proxy`. | #752 / PR #755 replaced this with a chronological, embargoed evaluation and honest metrics; merged as `0309d3ea17ef132f01214c855c306ff7a5c34b65`. |
| PR #745 | Merged as `12594b321d8618c5202d261d657282298c0fb1ea` from head `15686abd2cec00922f3229362c6e32b80cae9d4c` | GitHub has zero reviews. Subsequent source-reliability work reported a non-AUC metric as `auc_proxy` and used a non-ISO training cutoff. | #751 / PR #756 corrected the metric, cutoff, temporal checks, row identity, and artifact disposition; merged as `93d5390e1001c5ead5c56c397e72f9f61c866aeb`. |
| PR #746 | Merged as `ff919753ed36efbd34d236f3c6577f2c412594a3` from head `cda449b977a010476e5cc753493dacce438d2431` | GitHub has zero reviews. Its PR body explicitly records that the full local pre-push gate was skipped, contrary to the repository workflow. | The current remediation PRs run and bind the complete gate to their own heads. That later evidence does not satisfy #746 retroactively. |
| Direct commit `e8817adb` | `docs(contract): historical sample schema` (193 added lines) | No associated GitHub PR or review trail; the documented contract was not matched by the first builder implementation. | #750 / #754 aligned the executable builder and contract and added regression coverage. |
| Direct commit `64845295` | `feat(samples): build_historical_samples.py` | No associated GitHub PR; used `eval`, did not enforce the stated PIT rule, and did not preserve same-day source families. | #750 / PR #754 removed executable parsing, enforced whole-snapshot PIT and canonical identity, preserved heterogeneous families, bounded input, and made output atomic. Merged as `bfb16e9d68214c9a0b38f62da3c01ad780a188c6`. |
| Direct commit `ffb3e441` | `feat(#195): offline source-reliability trainer` | No associated GitHub PR; `auc_proxy` was not ROC AUC and `training_cutoff` was an epoch fragment rather than an ISO timestamp. | #751 / #756 uses honest metrics and aware-UTC temporal ordering and withdraws the unverifiable v1 artifact without fabricating a replacement. |
| Direct commit `9c112f99` | `feat(#197): conformal backtest on real sample JSONL` | No associated GitHub PR; used seeded random shuffling instead of a chronological split. | #752 / #755 implements global unique UTC-date splits, same-date isolation, observation embargo, family/identity validation, and fail-closed promotion checks; merged as `0309d3ea17ef132f01214c855c306ff7a5c34b65`. |

## Remediation evidence verified from GitHub

Evidence below applies only to the exact remediation head named in each row.
The requested GitHub reviewer has not submitted a GitHub review; the repository's
single-developer policy therefore relies on the recorded commit-bound
attestations and does not present an author approval as a reviewer approval.

| Remediation | State and exact disposition | Gate evidence recorded on PR / issue |
|---|---|---|
| #750 / PR #754 | **Merged** as `bfb16e9d68214c9a0b38f62da3c01ad780a188c6`; reviewed head `14f068c7011073fae9338dac3a4b75d4dd04f15e` | Reviewer requested: `@nicholaswang941013` (no submitted GitHub review). Harper first returned REQUEST CHANGES for source-family/provider forgery, unknown-source verification, aggregate fail-closed behavior, and atomic-output coverage. After fixes, the commit-bound Harper CISO and `/codex-review` dispositions were APPROVE. Eye: 0 critical / 0 warning. Full pre-push: PASS — backend 4,763 passed / 11 skipped, frontend 459 passed, competition QA 24/24, plus lint, build, data-contract, source-stub, and diff checks. Harper security regressions: 31 passed. |
| #751 / PR #756 | **Merged** as `93d5390e1001c5ead5c56c397e72f9f61c866aeb`; reviewed head `a8b3307aae25929a678bd0dcee2cc6c01d8fc91d` | Reviewer requested: `@nicholaswang941013` (no submitted GitHub review). Seven adversarial rounds addressed cutoff leakage, date-only joins, dangling v2 claims, timezone/schema/non-finite inputs, and ambiguous row alignment. Commit-bound `/codex-review`: APPROVE. Eye: 0 critical / 0 warning. Full pre-push: PASS — backend 4,785 passed / 11 skipped, frontend 459 passed, competition QA 24/24, plus lint, build, data-contract, source-stub, and diff checks. Post-rebase targeted checks: 70 passed with ruff, mypy, and diff checks green. |
| #752 / PR #755 | **Merged** as `0309d3ea17ef132f01214c855c306ff7a5c34b65`; final reviewed head `a1a9e086559b484c851f3fbc8da0a3996ec004a2` | Reviewer requested: `@nicholaswang941013` (no submitted GitHub review). Initial `/codex-review` returned REQUEST CHANGES for reversed conformal quantile direction, missing outcome embargo, permissive family eligibility, UTC normalization, and partition-family gates. After #751 merged, the conflict was resolved by retaining the chronological implementation and updating the honest-metrics CLI fixture to the strict PIT schema. Commit-bound `/codex-review`: APPROVE. Eye: 0 critical / 0 warning. Push-bound full pre-push: PASS — backend 4,834 passed / 11 skipped, frontend 459 passed, competition QA 24/24, plus lint, build, data-contract, source-stub, and diff checks. Integrated targeted regressions: 67 passed with ruff, mypy, and diff checks green. |

## #753 completion boundary

This document is the repository artifact for #753. Its own final commit-bound
reviewer request, Eye result, `/codex-review`, full pre-push result, PR number,
and merge SHA must be recorded on the #753 PR after the exact head exists.
They cannot truthfully be pre-recorded in the commit they are meant to review.
Obsidian, Wiki, and SkillHub read-back receipts are separate delivery actions
and are likewise not claimed by this repository-only change.

## Non-retroactivity

No remediation PR, issue comment, this retrospective, or later test run
retroactively approves PR #739, #743, #744, #745, or #746. No reviewer identity,
Eye result, `/codex-review` disposition, or pre-push result is inferred where
GitHub does not contain one. Later evidence establishes the disposition of the
exact later commit only.

## Release boundary

No item in this remediation authorizes production promotion of source
reputation or conformal prediction.  A negative experiment is a valid
research result.  Promotion requires the documented source-family, temporal,
PIT, correctness, error, abstention, and review gates to pass with artifacts
bound to the exact release commit.
