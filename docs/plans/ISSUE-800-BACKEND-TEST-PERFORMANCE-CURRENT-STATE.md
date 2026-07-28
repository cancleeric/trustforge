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
| `n=4`, parallel lane, no coverage | 5,243 passed, 12 skipped | 60.0 s |
| `n=8`, parallel lane, no coverage | 5,243 passed, 12 skipped | 96.4 s |
| `n=12`, parallel lane, no coverage | 5,242 passed, 1 shared-lease flake, 12 skipped | 85.4 s |
| `n=16`, parallel lane, no coverage, after lease isolation | 5,246 passed, 12 skipped | 66.0 s |
| `n=4 loadgroup` parallel lane + coverage | 5,246 passed, 12 skipped | 63.7 s |
| Final run 1, `n=4 worksteal`, combined coverage | 5,249 logical passed, 12 logical skipped, 84% | 44.00 s |
| Final run 2, `n=4 worksteal`, combined coverage | 5,249 logical passed, 12 logical skipped, 84% | 43.86 s |
| Final run 3, `n=4 worksteal`, combined coverage | 5,249 logical passed, 12 logical skipped, 84% | 47.74 s |
| Serial fallback, full suite + coverage | 5,249 passed, 12 skipped, 84.17% | 213.5 s |

The `<60 s` acceptance criterion is met in three consecutive complete
backend + combined-coverage runs. The measurements above retain the failed and
slower configurations so the final result cannot hide the isolation work or
the worker-saturation curve.

## Latest develop integration

After the three acceptance runs, `origin/develop` advanced to `5def34f0` and
added 105 backend tests. The branch merged that baseline before PR review.
The first parallel run exposed macOS temporary-path and interpreter-resolution
defects in newly landed tests; the fixes preserve safe-path enforcement and
the active virtual environment rather than weakening either check.

The post-fix complete gate passed with 5,351 parallel tests, 3 serial tests,
12 skips, 1 expected xfail, and 84% combined coverage. Its parallel lane took
72.86 seconds under concurrent host load. This variance is recorded
explicitly: the original three-run acceptance remains reproducible evidence
for the scoped change, while the latest moving-baseline result must not be
misreported as another sub-60-second run.

## Shared-resource and timing inventory

| Resource / timing dependency | Risk under xdist | Treatment |
| --- | --- | --- |
| Module telemetry background queue + SQLite | sleep-based read-after-write guesses | Added an explicit FIFO flush barrier; tests wait for durable processing |
| Analyze single-flight delayed follower | real six-second scheduler sleep | Injected a dedup wall clock and advance logical time after an Event wake |
| Analyze durable lease JSON | repository default caused cross-worker 429 conflicts | Session autouse fixture keeps the real JSON backend but gives every xdist worker its own path |
| SEC fake-network connector tests | production politeness delay ran even though network was mocked | Injectable sleep seam; production still waits 0.1 s between requests while fake-network tests use a no-op |
| Link reachability synthetic client | a direct rate-limiter monkeypatch could hide behavior | Removed the bypass; tests exercise the real limiter with isolated state |
| Shadow runtime forkserver | process startup exceeds the 1 s behavior budget on a saturated host | Non-latency tests use the existing 3 s startup-jitter allowance |
| Shadow timeout/kill tests | must retain real wall-clock process boundary | Explicit `serial` lane; SLO assertions remain unchanged |
| Activation JSON lock expiry | real two-second TTL wait | Backend clock injection; test advances a one-second semantic TTL |
| JSON idempotency lease expiry | real 1.1-second TTL wait | Backend clock injection; test advances the original one-second TTL |
| Subprocess interpreter | hard-coded worktree `.venv` paths can resolve incorrectly | Gate always exports `PYTHONPATH=src`; `.venv/bin/python` is verified and retained |

## Implemented gate design

1. Install `pytest-xdist>=3,<4` as a development dependency.
2. Default to a fixed 4 workers with `--dist worksteal`; it removes the
   long-tail worker imbalance seen with `loadgroup`. The measured 8/12/16
   worker configurations are slower or flaky on this 8-logical-CPU host.
3. Run tests marked `serial` in a separate non-xdist lane.
4. Explicitly erase coverage before the first lane, combine coverage across
   both lanes, and enforce the unchanged 75% threshold.
5. `sys.monitoring` was evaluated but not adopted: Python 3.11 is supported by
   the project, and coverage 7.8 warns that the `core` config is unrecognized.
   The gate therefore retains the portable default coverage engine.
6. Set `TRUSTFORGE_PYTEST_WORKERS=0` for the original full serial fallback.

## Completion state

The performance acceptance criteria are satisfied locally. Before merging,
the branch still requires the repository's complete pre-push gate,
adversarial review, commit-bound evidence, and the normal post-merge
`develop` gate. The serial fallback remains available as the immediate
rollback if a future host exposes an xdist isolation defect.
