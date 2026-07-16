# Connector Reliability Policy

## Scope

This policy governs every live connector used by the scheduled fetcher. Product
requests are cache-only: they must never retry a provider or bypass a source
failure by fetching live data during a formal analysis.

## Ownership And Credentials

| Source family | Connectors | Owner | Credential rule |
| --- | --- | --- | --- |
| Market | CoinGecko, HOYA BIT | data-acquisition | Store provider credentials only in runtime secret storage; HOYA BIT remains disabled until its official HTTPS contract is configured. |
| News | RSS feeds, CryptoPanic | data-acquisition | Public RSS is unauthenticated; a missing CryptoPanic token is an explicit missing source, never a fixture substitute. |
| Social | Reddit | data-acquisition | Cloud runtime requires OAuth; 403/429 is a recorded degradation and does not trigger anonymous retry storms. |
| On-chain | Blockchain.info, Blockchair, Mempool, FNG | data-acquisition | Keyless quotas use the scheduler interval and per-host limiter. |
| Regulatory | SEC EDGAR | compliance-data | User-Agent and rate limits must be maintained; failures are visible as missing regulatory evidence. |

## Runtime Contract

1. `fetch_scheduler.py` is the sole production path allowed to call providers.
2. A source is the concurrency ownership unit. Parallel workers never split one
   source across workers; snapshots start only after all selected workers join.
3. Every cache write stores `fetched_at`; freshness uses the source refresh
   interval and a longer stale window. DynamoDB TTL is only storage cleanup,
   not the truth of freshness.
4. Each source call records `ok`, `empty`, or `failed`, document count and
   duration in the Hermes execution/scheduler record.

## Failure Budget And Degradation

- A failed source does not abort unrelated sources or fabricate replacement
  facts. The formal report retains the missing/degraded condition.
- Any scheduler run with failures produces a review proposal. The acceptance
  target before changing a connector is seven consecutive scheduled cycles with
  zero failures for that source.
- `429`, `403`, timeout and credential errors are treated as provider failure;
  retries must remain bounded by the cycle's 900-second budget and respect the
  host limiter. No product request retries a provider.
- A source may be disabled only through the existing explicit source switch;
  the disablement and reason must appear in the scheduler/run evidence.

## Operational Response

1. Inspect cache freshness and the recent scheduler run log.
2. Reproduce only the affected source in a sandbox with bounded timeout.
3. Add a regression test before changing retry, interval, batching or fallback.
4. Run the bounded question-bank and replay measurements.
5. Stage any outer-skill policy change; activation requires human approval and
   remains rollbackable.

## Monthly Review

Review source availability, p95 latency, quota failures, freshness gaps and
credential expiry. Update the allowlist/interval only with linked evidence;
never tune Trust Layer weights to conceal a connector outage.
