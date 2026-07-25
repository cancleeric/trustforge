# Wiki: Pre-Push Release Gates

Canonical source: `docs/governance/PRE_PUSH_RELEASE_GATES.md`.

Operational summary:

- GitHub Actions workflows stay disabled and are not required checks.
- `.githooks/pre-push` is the mandatory local push gate.
- The gate pins its runtime: requires **Node ≥20.12** (`.nvmrc`=`22`, `frontend/package.json` `engines.node`), auto-selects it via `nvm use` + `.nvmrc` when available, and **fail-fasts** with an actionable message if Node is missing/too old — *before* the expensive suite. (Issue #660 / PR #661 / codex-review #666 — without this, stale-Node toolchain rot silently blocked all pushes via a cryptic rolldown `util.styleText` SyntaxError.)
- Each PR records commit-bound pre-push evidence.
- UI PRs include actual-branch Eye desktop/mobile verification.
- Every PR requests a named reviewer and posts `/codex-review`.
- Security changes require harper (CISO) and gray (CPO) review; cost changes
  require harper review.
- Production deployment is release-workflow controlled outside GitHub Actions.
