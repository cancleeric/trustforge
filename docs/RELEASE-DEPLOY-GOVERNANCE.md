# Release and Deployment Governance

This is the canonical TrustForge release and production deployment policy.

## Local-Only Quality Gate

TrustForge intentionally does not use GitHub Actions. Every workflow file stays
disabled, and no GitHub check run is evidence for push, review, merge, release,
or production deployment.

Each worktree must enable the repository-owned hook once:

```bash
git config core.hooksPath .githooks
```

Every push invokes `.githooks/pre-push`. It runs these fail-closed gates in
order:

1. `PYTHONPATH=src python -m pytest -q`
2. `python scripts/check_data_contracts.py`
3. `python scripts/scan_source_stubs.py --out out/pre-push/stub-scan.json`
4. `TRUSTFORGE_BEDROCK_DAILY_USD_CAP=0 python scripts/run_question_bank.py --limit 24 --out out/pre-push/question-bank-results.json`
5. `npm --prefix frontend ci` when frontend test dependencies are absent
6. `npm --prefix frontend test -- --run`
7. `npm --prefix frontend run lint`
8. `npm --prefix frontend run build`
9. `git diff --check`

The hook selects `.venv/bin/python` when available and otherwise uses
`python3`. Any nonzero result rejects the push. Do not bypass the hook. When a
change makes a gate inapplicable, the PR must explain why; all remaining gates
still run.

## Review and Merge Record

Every PR must:

- link an issue with explicit acceptance criteria;
- name a reviewer;
- record the exact commit SHA and successful pre-push gate evidence;
- receive a commit-bound reviewer attestation;
- pass `/codex-review` with every finding resolved;
- include an actual-branch desktop/mobile eye scan for UI changes; and
- receive harper (CISO) review in addition to `/codex-review` for security- or
  cost-sensitive changes.

The author does not approve their own PR. Merge only the reviewed commit with
no unresolved findings.

## Controlled Local Release and Production Deployment

Production is released by an authorized operator from a clean isolated
worktree, never by GitHub Actions:

1. reconcile reviewed work into `develop`;
2. run the mandatory pre-push gate and record evidence for the exact
   `develop` commit;
3. review and merge `develop` to `main`;
4. rerun release verification on the exact resulting `main` commit;
5. create the release branch and immutable annotated version tag from that
   verified commit;
6. run the repository's local release smoke and controlled deployment scripts
   under the applicable production authorization;
7. verify HTTPS health, projected version, the changed user workflow,
   monitoring, and rollback; and
8. record commands, commit/tag identity, timestamps, results, and final
   disposition in the release record.

Deployment stops on any failed gate or verification. Roll back to the previous
reviewed immutable revision using the documented service rollback mechanism;
never rewrite the release tag or delete append-only production evidence.
