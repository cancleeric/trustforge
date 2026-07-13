# Hermes Agent Architecture

TrustForge is presented to the competition as **Hermes**, a multi-source market
analysis agent. Hermes is not an autonomous trading agent: it creates an
auditable decision-support report and never places orders or gives a buy/sell
instruction.

## Stable workflow

Every analysis has one `run_id` and uses the same five observable nodes:

1. **Source ingestion**: collect price, on-chain, news, social, regulatory, and
   available HOYA BIT sources.
2. **Claim extraction**: derive traceable claims from each retrieved document.
3. **Trust reasoning**: score source reputation, corroboration, freshness, and
   manipulation risk; form the judgment from the pipeline, not a third-party
   analyst conclusion.
4. **Evidence assembly**: bind report claims to official Evidence fields:
   `source`, `fetched_at`, `content_reference`, and `related_claim`.
5. **Report delivery**: produce the Final Report, Evidence List, and Execution
   Log within the 15-minute budget.

## Bounded autonomy

Hermes is not only a request-response workflow. `scripts/hermes_cycle.py` is a
scheduled autonomous research loop for the fixed competition coin pool. It
delegates to the existing hardened fetch scheduler to refresh allowed crawler
sources and then builds cache-only trust snapshots. It has a fixed budget, fixed
coin pool, fixed tool call count, and no unbounded network access.

The formal competition run remains isolated: it selects only source records and
snapshots at or before `run_started_at`. Autonomous research memory may improve
freshness and provide historical replay inputs, but it cannot silently inject a
previous conclusion or post-run information into a formal report. The static
tool/skill declaration lives in `trustforge.hermes.manifest()`.

The supplied five-year OHLCV baseline is not treated as an opaque CSV. Hermes
records the dataset name, safe filename, SHA-256, row count, full UTC coverage,
schema, and exact analysis window on every price Evidence item. The report also
includes a full-history fact (cumulative return and maximum drawdown) alongside
the short-window market observation, so a reviewer can distinguish historical
context from the specific window used for the market judgment.

## Historical replay and confidence calibration

The scheduled `--snapshot` step now captures a per-coin source archive before
building the daily trust snapshot. Each archived record has three separate time
values: document `published_at` (or explicit unknown), source-cache
`fetched_at`, and archive `snapshot_at`. A formal replay may select only an
archive whose `snapshot_at` is at or before its `run_started_at`; a same-day
snapshot captured later is rejected. Missing historical source archives remain
missing and are never reconstructed from current crawler cache.

`scripts/run_historical_replay.py` scores existing point-in-time daily trust
snapshots against later official OHLCV closes at T+1, T+7, and T+14. It reports
eligible sample count, directional hit rate, mean directional return, maximum
drawdown, and a five-bin reliability table. Until an approved small calibrator
is trained, `calibrated_confidence` remains **information completeness**, not a
validated prediction probability; its Brier result is labelled a diagnostic
proxy, never a competition forecast claim.

Source archives begin accumulating after deployment. Older daily trust
snapshots can be outcome-scored, but cannot be promoted to full raw-source
replay evidence unless their matching archived source snapshot exists.

## Bounded self-improvement

Hermes learns operationally, but it is **not** permitted to self-modify in
production. Each autonomous cycle ends with `diagnose_improvement`, which reads
durable scheduler results and can also consume the question-bank and replay JSON
reports. It writes an improvement diagnostic containing evidence, severity,
proposed sandbox experiment, measurable success criterion, and an explicit
human-approval gate.

The diagnostic detects four different deficits rather than treating every issue
as a prompt problem:

1. source acquisition failures → connector reliability experiment;
2. question-bank report/Evidence/log failures → regression fixture and focused
   pipeline repair;
3. high per-source p95 latency → bounded cache/batching/timeout experiment;
4. insufficient or misaligned historical outcomes → continue data collection or
   evaluate an explainable calibrator on a time-separated holdout.

The improvement loop never changes code, source weights, prompts, model choice,
or formal conclusions automatically. A proposal must be reviewed, implemented
in a branch, validated against the question bank and time-sliced replay, then
approved for release. This preserves the competition requirement that every
formal conclusion remains reproducible and attributable to its own run.

## Audit contract

Each Execution Log event keeps the stable top-level schema (`ts`, elapsed time,
tool, parameters, summary). Its `params.hermes` envelope contains `run_id`,
`agent=hermes`, node id, node label, node order, and status.
The web view visualizes these exact events and exports the same JSONL file.
This makes source acquisition, trust reasoning, and final output separately
inspectable by a reviewer.

`ingestion.source` is emitted once for the official OHLCV pack and once for
each crawler/cache-backed connector. It records source name and kind, safe
outcome (`ok`, `empty`, or `failed`), document count, and duration in
milliseconds. Failure events expose only an exception type, never credentials,
URLs with tokens, or local paths. Government announcements, crawler execution,
and source-level latency are therefore auditable in the same run log.

## Deliverables per run

- `report.md`: conclusion / market judgment, key basis with Evidence links,
  confidence, known limits, and conditions that could overturn the conclusion.
- `evidence.json`: the traceable Evidence List.
- `execution_log.jsonl`: node-level execution chronology.

The CLI creates these files directly. The React analysis view exposes the same
three artifacts for the current run as browser downloads.

## Delivery backlog

The authoritative list of remaining Hermes work, dependencies, acceptance
criteria, and forbidden shortcuts is maintained in
`docs/plans/HERMES-AGENT-DELIVERY-BACKLOG-2026-07-13.md`.
