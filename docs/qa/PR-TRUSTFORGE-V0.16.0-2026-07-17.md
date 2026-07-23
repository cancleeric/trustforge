# PR: TrustForge v0.16.0 Data Foundation And Runtime Stability

Target: `develop -> main`

## Summary

- Promote Hermes' durable SQLite data foundation and continuous-analysis
  pipeline without changing the release-locked Trust Kernel boundary.
- Add immutable provider source events, versioned data contracts, quarantine,
  end-to-end analysis lineage and a point-in-time Trust Feature Store.
- Complete five-year historical ingestion/replay foundations with explicit
  event-time, availability-time, provider and content-hash provenance.
- Stop hidden workspace rendering/polling to remove page flicker and the
  history-workspace unexpected-error path.
- Restore monotonic release numbering as `v0.16.0` after published `v0.15.0`.

## Branch Audit

- [x] Fetch and prune reachable remotes.
- [x] Compare active local and remote branches against `develop`.
- [x] Confirm no active feature branch contains commits absent from `develop`.
- [x] Keep obsolete `release/v0.6.x` and superseded `release/v0.15.0` topology
  out of develop.
- [x] Confirm worktree is clean before publication.

## Verification Evidence

- [x] Backend: 2,148 passed, 6 skipped; 90.59% coverage.
- [x] Loopback health-monitor tests: 3 passed in an unrestricted local process.
- [x] Frontend: 27 files, 252 tests passed.
- [x] Frontend lint passed.
- [x] Frontend production build passed.
- [x] `scripts/release_version.py --verify --ref v0.16.0` passed.
- [x] Local HTTP release smoke passed.
- [x] Deterministic release question bank: 24/24 passed.
- [x] `/api/analysis-flow` and `/api/hermes-upgrades` returned HTTP 200.

## Release And Production Checklist

- [ ] Push reviewed `develop` to GitHub and Gitea independently.
- [ ] Run the mandatory pre-push gate and record commit-bound evidence.
- [ ] Open the `develop -> main` PR with a named reviewer.
- [ ] Merge through branch protection; do not push directly to `main`.
- [ ] Re-run release verification on the resulting main commit.
- [ ] Create `release/v0.16.0` from that exact verified commit.
- [ ] Create and push immutable annotated tag `v0.16.0`.
- [ ] Execute the controlled local production release/deploy runbook.
- [ ] Verify service health, version projection, analysis flow and rollback
  marker after deployment.

## Rollback Boundary

Production rollback selects the previous reviewed release artifact and active
pointer. It must not delete append-only source events, quarantine, lineage,
historical snapshots or completed analysis records.
