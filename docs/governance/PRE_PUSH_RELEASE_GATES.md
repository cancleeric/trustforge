# Pre-Push Release Gates

TrustForge uses the repository pre-push hook as the mandatory local quality gate.
GitHub Actions workflows under `.github/workflows/` intentionally remain
disabled and are not merge, release, or deployment gates. Do not restore, enable,
rerun, or depend on GitHub Actions as PR acceptance evidence.

## Mandatory Local Gate

Every push must pass `.githooks/pre-push`. Install the hook path once per clone:

```bash
git config core.hooksPath .githooks
```

The hook currently runs these gates in order:

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

The hook may skip `npm --prefix frontend ci` only when
`frontend/node_modules/.bin/vitest` already exists. `TRUSTFORGE_NO_CD=1` may be
used when a push must avoid deployment side effects, but it does not skip any of
the quality gates above.

## PR Evidence

Every PR must record commit-bound local gate evidence in the PR body or in a PR
comment:

- head commit SHA
- UTC timestamp for the gate run
- exact command or hook path used
- local gate result
- targeted tests, lint, build, `git diff --check`, and Eye scan evidence when
  the change requires them
- explicit reason for any gate that was not run

UI changes still require actual-branch Eye desktop and mobile layout
verification. Every PR must request a named reviewer and include a `/codex-review`
comment.

Security-sensitive changes require harper (CISO) and gray (CPO) review before
merge. Cost-sensitive changes require harper review before merge.

## Release Boundary

Production deployment remains controlled by an explicit release workflow outside
GitHub Actions. Release work must verify deployed health and changed user
workflows before closing the release milestone. Issue-development sweeps open PRs
for review only; they do not merge, release, or deploy.
