# Historical Replay And Confidence Calibration

## Purpose

Turn the existing daily TrustForge snapshots into a measurable research record
without future leakage. This is an offline diagnostic, not a trading system and
not evidence that current information completeness predicts price direction.

## Data contract

The scheduler captures `__source_snapshot_history__:{COIN}:{YYYY-MM-DD}` before
the cache-only trust snapshot. Every archive carries:

- `published_at`: normalized timestamp from the source document, or `null` when
  the source did not provide one.
- `fetched_at`: the moment this source cache entry was acquired.
- `snapshot_at`: the common UTC boundary for the scheduled archive.

Formal replay supplies an `at_or_before` boundary. The reader rejects any
archive captured after it, including one from the same UTC date. A missing date
is returned as missing; current cache data must never be used to backfill it.

Historical imports use `scripts/historical_backfill.py`. Every imported
document must include its provider, license/contract, `published_at`, actual
`retrieved_at`, and content. The importer records a deterministic
`content_sha256`; the archive writer rejects missing or mismatched provenance.
The import's retrieval date remains distinct from the historical analysis
boundary and must never be presented as a contemporaneous fetch.

## Runbook

Run the bounded autonomous collection cycle in production scheduling:

```bash
python3 scripts/hermes_cycle.py
```

Generate an outcome report from available daily snapshots:

```bash
python3 scripts/run_historical_replay.py --coin BTC --days 365 --out out/replay-btc.json
```

The report evaluates `偏多` and `偏空` snapshots only. It joins each snapshot
date to supplied official OHLCV closes at T+1, T+7, and T+14. Neutral and
abstain snapshots have no directional prediction and are reported outside the
eligible denominator rather than being scored as fabricated wins or losses.

## Promotion gate

Do not train or publish a probability calibrator until the archive has enough
point-in-time samples across market regimes and a held-out period. The first
candidate is an explainable logistic or isotonic model using source reputation,
corroboration, freshness, conflicts, missingness, and volatility. LLMs remain
bounded to planning, claim extraction, contradiction organization, and report
writing; timestamps, evidence binding, and calibration stay deterministic.
