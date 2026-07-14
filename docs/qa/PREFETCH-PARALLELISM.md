# Production Prefetch Parallelism Evidence

## 2026-07-14 v0.14.3 cycle

- Runtime: EC2 `i-0152b70368358a81c`, region `ap-southeast-2`.
- Service: `hermes-cycle.service`, result `success`, exit status `0`.
- Scheduler: 4 source-owner workers, 900 second total budget.
- Fetch elapsed time: 25.40 seconds.
- Successful writes: 32 source/coin targets, 131 documents.
- Snapshot result: BTC, ETH, SOL, BNB and XRP all written (5/5).
- Whole Hermes cycle elapsed time: 49.83 seconds.

The deployed scheduler log proves that real connectors use the bounded parallel
path and that snapshot generation happens after the fetch workers finish. The
regression test `test_parallel_prefetch_runs_source_workers_concurrently`
provides the deterministic sequential-time comparison without repeating live
provider calls merely for benchmarking.

## Observed reliability gap

Eight refresh targets returned HTTP 429 in this cycle:

- `cryptoslate:XRP`
- `coingecko-dev:SOL`
- `reddit-bitcoin:BNB`
- `reddit-bitcoin:XRP`
- `reddit-cryptocurrency:ETH`
- `reddit-cryptocurrency:SOL`
- `reddit-cryptocurrency:BNB`
- `reddit-cryptocurrency:XRP`

The cycle degraded safely and retained existing cache entries, but H-20 remains
open until per-provider quota/backoff behavior and repeated-run failure rates
are verified. A forced sequential live comparison is intentionally excluded:
it would consume provider quota after a recorded 429 event and would not be a
responsible production benchmark.

The first remediation is implemented after this observation: a coin-scoped
source that receives an explicit HTTP 429 stops calling that source for the
remaining coins in the current cycle. Every deferred stale target is still
reported as failed, so cooldown reduces provider pressure without hiding
freshness gaps. Production verification remains pending the next release.

## 2026-07-14 v0.14.4 verification

- Production health reported `v0.14.4`; timer remained enabled and active.
- The full Hermes cycle completed successfully in 51.03 seconds and wrote all
  five trust snapshots.
- `reddit-cryptocurrency:BTC` succeeded with 24 documents.
- `reddit-cryptocurrency:ETH` returned HTTP 429. The scheduler immediately
  deferred SOL, BNB and XRP, recorded all four targets as failed, and emitted
  the `HTTP 429 cooldown` audit message. No remaining Reddit call was made.
- `reddit-bitcoin` remained fresh and generated no live provider traffic.

This verifies the source-scoped cooldown in production. CoinGecko showed that
separate source workers sharing one provider can still encounter independent
429 responses; provider-wide cooldown coordination remains a follow-up and is
not claimed complete by this release.
