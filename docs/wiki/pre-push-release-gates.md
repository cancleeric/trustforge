# Wiki: Pre-Push Release Gates

Canonical source: `docs/governance/PRE_PUSH_RELEASE_GATES.md`.

Operational summary:

- GitHub Actions workflows stay disabled and are not required checks.
- `.githooks/pre-push` is the mandatory local push gate.
- Each PR records commit-bound pre-push evidence.
- UI PRs include actual-branch Eye desktop/mobile verification.
- Every PR requests a named reviewer and posts `/codex-review`.
- Security changes require harper (CISO) and gray (CPO) review; cost changes
  require harper review.
- Production deployment is release-workflow controlled outside GitHub Actions.
