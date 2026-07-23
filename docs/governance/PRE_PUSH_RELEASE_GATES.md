# Pre-Push Release Gates

TrustForge does not use GitHub Actions as a merge, release, or deployment gate.
All workflows under `.github/workflows/` intentionally remain disabled. Do not
restore, enable, rerun, or depend on them for PR acceptance.

## Mandatory Local Gate

Every push must pass the repository pre-push hook at `.githooks/pre-push`.
Install it once per clone:

```bash
git config core.hooksPath .githooks
```

The hook currently runs these exact gates, in order:

```bash
env PYTHONPATH=src "$PYTHON" -m pytest -q
"$PYTHON" scripts/check_data_contracts.py
"$PYTHON" scripts/scan_source_stubs.py --out out/pre-push/stub-scan.json
env TRUSTFORGE_BEDROCK_DAILY_USD_CAP=0 "$PYTHON" scripts/run_question_bank.py --limit 24 --out out/pre-push/question-bank-results.json
npm --prefix frontend ci
npm --prefix frontend test -- --run
npm --prefix frontend run lint
npm --prefix frontend run build
git diff --check
```

The hook skips `npm --prefix frontend ci` only when
`frontend/node_modules/.bin/vitest` already exists. `TRUSTFORGE_NO_CD=1` may be
used when a push must skip deployment side effects, but it does not skip the
quality gates above.

## PR Evidence

Each PR must record commit-bound pre-push evidence in the PR body or a PR
comment:

- head commit SHA
- UTC timestamp for the gate run
- exact command or hook path used
- local gate result
- targeted tests, lint, build, `git diff --check`, and Eye scan evidence when
  the change requires them
- explicit reason for any gate that was not run

UI changes still require actual-branch Eye desktop/mobile layout verification.
Every PR requires a named reviewer and a `/codex-review` comment.
Security-sensitive changes require harper (CISO) and gray (CPO) review before
merge. Cost-sensitive changes require harper review before merge.

## Release Boundary

Production deployment remains controlled by an explicit release workflow outside
GitHub Actions. Release work must verify deployed health and changed user
workflows before closing a release milestone. Issue-development sweeps open PRs
for review only; they do not merge, release, or deploy.
