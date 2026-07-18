# TrustForge Development Workflow

This repository is currently maintained by one developer. Single-developer work
must still complete every review and release gate; do not silently skip a gate.

## Required Flow

1. Start from an issue with explicit acceptance criteria and dependencies.
2. Create a scoped branch. Do not develop directly on `main`.
3. Implement focused changes with regression tests.
4. Run local tests, lint, build, and `git diff --check` as applicable.
5. Open a PR linked to the issue and wait for all required CI checks.
6. Perform an adversarial `/codex-review`. Fix every finding and rerun checks.
7. For UI changes, perform an eye scan against the actual branch. Check desktop
   and mobile layout, data truthfulness, overflow, state transitions, and errors.
8. Record reviewer findings, fixes, eye-scan evidence, and final disposition in
   the PR. GitHub forbids authors from approving their own PR; in this one-person
   repository use a commit-bound reviewer attestation instead of fabricating an
   approval. Never use admin or override merge to bypass protection.
9. Merge only with no unresolved findings and green CI. Verify post-merge CI.
10. Deploy production only through the release workflow, then verify health and
    the changed user workflow before closing the milestone.

Security and cost-sensitive changes require an explicit security/adversarial
review section in addition to the normal review record.

## CEO Development Cycle

The local CEO sweep runs hourly and produces a recommendation report only. It
does not invoke agents, approve plans, edit code, merge, or deploy. When an
interactive CEO agent consumes that report, each unfinished-issue round follows
this order:

1. gray (CPO) writes a scoped development or optimization plan.
2. CEO reviews the plan and must approve it before implementation starts.
3. Background deputies analyze and implement while the CEO thread stays interactive.
4. Coding assistance should prefer `http://yingdemacbook-pro.local:11434/` when
   reachable; it is coding-only and receives no secrets or deployment authority.
5. Every PR names a reviewer and completes the reviewer attestation, eye scan,
   and `/codex-review` adversarial gate before merge.
6. Security changes additionally require harper (CISO) review.
7. Report after every milestone or after more than three PRs.
8. Only personally verified behavior may be reported complete.
