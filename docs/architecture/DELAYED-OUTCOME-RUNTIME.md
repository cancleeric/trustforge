# Delayed outcome runtime boundary

`trustforge.delayed_outcome_labeler` implements Issue #507 as a fixture-only,
append-only domain component. It does not connect a production provider,
database, backfill job, ModelHub, HTTP route, or automatic training flow.

## Time and calendar rules

- The analysis event's trusted `available_time` is the prediction cutoff.
- The fixture calendar is supplied by a trusted fixture registry. Its timezone
  must be a valid IANA zone and its version must already be available when the
  outcome event is labeled.
- A versioned venue calendar selects the first safe start session. The cutoff is
  5 minutes for a 24/7 calendar and 15 minutes for a session venue.
- T+1, T+7, and T+14 advance through calendar-declared eligible sessions.
  Weekends, holidays, and closures are skipped; early closes count. A suspended
  instrument or missing bar never moves the target session.
- Calendar `closed` means the venue itself is closed. An instrument suspension
  remains an open venue session with a missing instrument bar.
- Publication SLA is 1 hour for 24/7 and 4 hours for session venues. Missing
  data stays pending through `matures_at + 72 elapsed UTC hours`; only a later
  instant is unavailable. Even if fixture bars exist early, the outcome cannot
  become labeled before `matures_at`.
- Maturity and late-cutoff state use trusted `labeled_at`, the event's actual
  availability time. A later report `as_of` cannot move a past event into a
  future state.
- A 24/7 calendar uses UTC, contains only contiguous daily open sessions, and
  closes every label precisely at the next UTC midnight.

## Numeric and outcome rules

Prices are decimal strings with at most 18 significant digits and 8 fractional
digits. Computation uses precision 34 and `ROUND_HALF_EVEN`; persisted percentage
values have 8 fractional digits. Bullish and bearish predictions use the
unrounded directional return for hit evaluation. Neutral and abstain outcomes
retain realized move diagnostics but have no directional hit.

The first implementation uses split-adjusted price return and excludes cash
dividends. Both endpoints must carry the same fixture provider, dataset, and
adjustment-methodology lineage.

## Identity, revision, and safety

The canonical outcome SHA-256 covers exactly seven keys: tenant, prediction,
horizon, contract version, market-data variant, market-data revision, and
outcome version. `as_first_known` selects the earliest available revision;
`latest_official` selects the latest revision visible at the trusted as-of.
The market-data revision is a deterministic manifest hash covering only the
exact selected start/target records, calendar, and variant; a caller hash that
does not match is rejected. Unselected corrections are excluded, so a later
rerun cannot change an already reproducible `as_first_known` identity. Price
selection uses a complete-lineage total order when availability timestamps tie,
and is independent of fixture input order. Fixture content hashes must be full
lowercase SHA-256 digests. Calendar and price fixtures are each bounded to
10,000 records.
Late-after-cutoff recovery requires a new immutable version and a same-tenant,
same-logical-key predecessor.

`FixtureOutcomeLedger` is the only public construction path. Under one
in-process lock it allocates a bounded monotonic version per
tenant/prediction/horizon/variant key, validates predecessor continuity,
deduplicates request fingerprints, appends first, and commits in-memory state
only after the append port confirms `created` or `idempotent`; exceptions and
all other statuses leave ledger state untouched. Dry-run plans under the same lock but mutates
nothing. This ledger is deliberately non-durable and fixture-only; it is not a
production allocator or durable audit store.

Outcome events are always `delayed_outcome`, explicitly non-evidentiary, and
never eligible as Evidence. Dry-run returns before invoking the append port, so
it performs zero writes.

## Breaking migration

The old public builder that accepted caller-selected revisions, predecessors,
price dictionaries, and source versions was removed. Consumers must use
`FixtureOutcomeLedger.observe` with trusted tenant/time context and approved
fixture dataclasses. Calibration consumers must explicitly select
`market_data_variant`; canonical rows are isolated by tenant and variant and
read `outcome_version`, `return_pct`, and `market_data_revision` directly.
Joins require the exact source analysis identity—not only a reusable
`analysis_id`—and manifests expose tenant and variant explicitly.
This migration is required for Issue #507 and does not claim the Issue #508
dataset-manifest milestone is complete.
