# 2026-07-23 Pre-Push Release Gate Memory

Decision: GitHub Actions workflows remain disabled and are not TrustForge PR,
release, or deployment gates.

Operational memory:

- `.githooks/pre-push` is the mandatory local push gate.
- PRs must include commit-bound pre-push evidence.
- UI PRs still need actual-branch Eye desktop/mobile verification.
- Every PR needs a named reviewer and `/codex-review`.
- Security changes require harper (CISO) and gray (CPO) review; cost changes
  require harper review.
- Production deployment remains release-workflow controlled outside GitHub
  Actions.

Canonical reference: `docs/governance/PRE_PUSH_RELEASE_GATES.md`.
