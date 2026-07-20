# External Sources And Evidence Independence Plan

Date: 2026-07-20

## Goal

Improve external source coverage without inflating false corroboration. New connectors must make lineage, source role, and independence explicit before their claims affect confidence.

## Source Priority

1. Primary facts: official chain, regulatory, venue, and issuer records.
2. Computed metrics: allowlisted Dune queries, DefiLlama, CoinGecko/CoinMarketCap comparisons.
3. Analysis context: reputable news, research, and market commentary.
4. Social signals: Reddit/X/community evidence, used as weak signals and contradiction probes.

## Required Metadata

Every new connector should normalize these `Document.meta` fields:

- `origin_family`: upstream data family, such as `ethereum-chain` or `market-aggregator`.
- `evidence_role`: one of `primary_fact`, `computed_metric`, `analysis`, or `social_signal`.
- `independence_group`: de-duplication group for corroboration scoring.
- `upstream_reference`: transaction hash, query ID, dataset version, URL, or official record ID.
- `observation_window`: ISO time window covered by the data.
- `provider_version`: API, query, or dataset version.
- `source_license_note`: short display/cache limitation note.

## Corroboration Rules

- One `independence_group` counts at most once per claim.
- `primary_fact` can anchor a conclusion.
- `computed_metric` can verify scale, direction, and anomaly claims.
- `analysis` provides context but should not independently anchor a conclusion.
- `social_signal` can surface sentiment and contrarian leads but remains weak evidence.
- Aggregators sharing the same upstream market data are one independence group unless provenance proves otherwise.

## Delivery Slices

1. Normalize connector metadata and expose source status cards.
2. Add route/fallback/frequency/timeout observability.
3. Add backpressure and DLQ status for schedulers.
4. Add targeted primary/computed connectors only when credentials and licensing are confirmed.

## Current Implementation

The first observability slice is implemented in `src/trustforge/module_status.py` and surfaced through `upgrade_status()["module_observability"]`.
