# Calibration Dataset Temporal Manifest

Status: Issue #508 fixture/runtime contract. This component builds an immutable
dataset artifact; it does not train, score, write ModelHub, allocate production
traffic, persist a registry, or enable a feature flag.

## Exact build policy

Every build requires a frozen `CalibrationDatasetPolicy`. Its content is
normalized to UTC with one canonical `...000000Z` representation and is part of
the manifest checksum. Offset timestamps denoting the same instant therefore
produce the same policy; timezone-naive timestamps fail closed.

- `dataset_as_of`: the inclusive point-in-time visibility cutoff;
- `train_end` and `validation_end`: fixed UTC prediction-time boundaries;
- `embargo_seconds`: subtracted from each split's label cutoff;
- `eligibility_version`, `split_version`, and `producer_version`;
- one trusted `tenant_id` and one explicit market-data variant.

The required ordering is:

`train_end < validation_end <= dataset_as_of`.

Split assignment depends only on the canonical analysis availability time,
never event time, row position, or input order. This prevents an analysis whose
event happened during train but became available during validation from
flowing backward into train. An analysis available at `train_end` is
validation; an analysis available at `validation_end` is test. The group key is
`(tenant_id, analysis_identity)`, so every horizon belonging to one analysis is
always in the same split.

Label availability must be after analysis availability and no later than:

- train: `train_end - embargo`;
- validation: `validation_end - embargo`;
- test: `dataset_as_of - embargo`.

This prevents a label learned beyond a split boundary from leaking backward.

## Eligibility and trusted joins

The analysis side accepts only canonical `learning-event.v1`,
`analysis-quality.v1`, complete events for the selected tenant. It rejects
missing IDs, partial/failed records, unsupported directions, invalid or
non-finite confidence, oversized text, and any attempt to expand five-year
OHLCV into analysis samples.

The outcome side reuses #507
`validate_canonical_delayed_outcome`, the trusted fixture authority registry,
source-analysis identity binding, supersession lineage, and selected
market-data variant. Only mature directional labels enter rows. The join uses
an identity index keyed by exact source analysis and horizon, not
`analysis_id`, and is O(A + O); duplicate identities or revisions fail closed.
For each `(source analysis identity, horizon)`, the builder first derives the
analysis group's split and label cutoff, discards and counts canonical
revisions beyond that cutoff, and only then chooses the highest eligible
revision. A late v2 can therefore never shadow an eligible v1.

## Resource and point-in-time bounds

Inputs are consumed once. After safe event-type and tenant metadata checks,
foreign-tenant events are discarded before all quotas, exclusion counts,
roots, or checksums. Scoped event count, recursive string-field UTF-8,
container-node, and nesting-depth bounds are checked directly on the event
tree. Scalar canonical bytes are counted incrementally with
`JSONEncoder.iterencode`, aborting as soon as the aggregate limit is crossed.
Only a successfully preflighted event is converted to a canonical anchor;
sorting and hashing occur after the bounded scan.
Events whose `available_time` is after `dataset_as_of` are invisible: adding or
reordering such late inputs does not change rows, roots, counts, or checksum.
The implementation then canonical-sorts visible scoped anchors and output rows.

## Manifest and reproducibility

`confidence-calibration-dataset.v2` contains:

- the full exact policy and producer/eligibility/split/schema versions;
- scoped canonical analysis and outcome input roots;
- all exclusion categories and counts;
- exact split ranges;
- row and analysis-group counts by split;
- canonical rows plus `rows_sha256`;
- `manifest_sha256` over every preceding manifest field.

Canonical JSON uses UTF-8, sorted object keys, compact separators, and stable
row ordering. Thus identical visible inputs and policy yield the same artifact
independent of iterable order.

The manifest checksum is the content address. Rollback means selecting a
previously retained manifest by its exact checksum and verifying that checksum
before use. This issue intentionally adds no mutable “current” pointer, DB
table, filesystem registry, deployment switch, or rollback side effect.

## Consumer boundary

Downstream calibration or anomaly work may consume only a manifest whose
policy, tenant, market-data variant, input roots, versions, and checksum it
explicitly verifies. A raw row list is not an authorized dataset. Production
training, ModelHub submission, HTTP wiring, durable artifact storage, and
feature enablement remain separate issues and review gates.
