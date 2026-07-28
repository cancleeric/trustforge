# Issue #800 backend test performance — current state

Date: 2026-07-28  
Branch: `fix/800-test-performance-safe`  
Baseline: `origin/develop` at `bf2173a2`

## Scope and non-negotiable constraints

The performance target applies to the complete backend suite with line
coverage for both `trustforge` and `trustforge_core`. Tests, product modules,
coverage, security assertions, latency SLOs, and semantic TTLs must not be
removed or weakened to make the timer green.

The local gate has a fixed-worker parallel path and
`TRUSTFORGE_PYTEST_WORKERS=0` as the serial rollback. A parallel failure must
fail closed; it must never silently retry serial and hide isolation defects.

## Reproducible baseline

Host: Apple macOS, 8 logical CPUs, Python 3.14.6, pytest 9.1.1.

| Configuration | Result | Wall time |
| --- | --- | ---: |
| Original serial full suite + coverage | 5,246 passed, 12 skipped, ~84.2% | 286–321 s |
| `-n 2 --dist loadgroup`, no coverage | 5,244 passed, 2 failed, 12 skipped | 172.7 s |
| `-n 8 --dist loadgroup`, no coverage, before isolation fixes | 5,242 passed, 4 failed, 12 skipped | 89.7 s |
| Fixed parallel lane, no coverage, after isolation fixes | 5,243 passed, 12 skipped | 105.0 s |
| Fixed serial lane, no coverage | 3 passed, 1 collection-time skip | 16.6 s |
| Parallel lane + coverage, before final isolation fixes | 5,235 passed, 2 failed, 12 skipped | 132.0 s |
| Final parallel lane + coverage | 5,243 passed, 12 skipped | 146.8 s |
| Final serial lane + appended coverage | 3 passed, 1 collection-time skip | 14.2 s |
| Final combined coverage | 5,246 logical passed, 12 logical skipped, 84% | 161.0 s |

The `<60 s` acceptance criterion is not yet met. The measurements above are
kept explicitly so a faster but false result cannot replace the actual state.

## Shared-resource and timing inventory

| Resource / timing dependency | Risk under xdist | Treatment |
| --- | --- | --- |
| Module telemetry background queue + SQLite | sleep-based read-after-write guesses | Added an explicit FIFO flush barrier; tests wait for durable processing |
| Analyze single-flight delayed follower | real six-second scheduler sleep | Injected a dedup wall clock and advance logical time after an Event wake |
| Analyze durable lease JSON | fixed repository path creates cross-worker lease collisions | Link reachability tests inject a per-test `tmp_path` lease backend |
| Link reachability synthetic client | unrelated live/real quotas produce ordering-dependent 429 | Reachability fixture isolates the rate-limit concern |
| Shadow runtime forkserver | process startup exceeds the 1 s behavior budget on a saturated host | Non-latency tests use the existing 3 s startup-jitter allowance |
| Shadow timeout/kill tests | must retain real wall-clock process boundary | Explicit `serial` lane; SLO assertions remain unchanged |
| Activation JSON lock expiry | real two-second TTL wait | Backend clock injection; test advances a one-second semantic TTL |
| JSON idempotency lease expiry | real 1.1-second TTL wait | Backend clock injection; test advances the original one-second TTL |
| Subprocess interpreter | hard-coded worktree `.venv` paths can resolve incorrectly | Gate always exports `PYTHONPATH=src`; `.venv/bin/python` is verified and retained |

## Implemented gate design

1. Install `pytest-xdist>=3,<4` as a development dependency.
2. Default to a fixed 8 workers with `--dist loadgroup`; never use `-n auto`.
3. Run tests marked `serial` in a separate non-xdist lane.
4. Combine coverage across both lanes and enforce the unchanged 75% threshold.
5. Use the Python `sys.monitoring` coverage core; it preserves line coverage
   and excludes no source modules.
6. Set `TRUSTFORGE_PYTEST_WORKERS=0` for the original full serial fallback.

## Remaining work before #800 can close

- Obtain three consecutive complete backend + coverage runs with identical
  pass/skip counts and no flakes.
- Reduce each complete run below 60 seconds without reducing assertions,
  source coverage, semantic TTLs, or SLO thresholds.
- Record the final three timings and coverage values in this document.

Until all three conditions are met, #800 remains incomplete and this branch
must not be represented as satisfying the performance acceptance criterion.
