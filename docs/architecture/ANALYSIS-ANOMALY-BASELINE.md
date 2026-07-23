# Analysis Anomaly Baseline

Status: Issue #509 deterministic fixture/runtime contract. This component
produces explainable `candidate_diagnostic` events only. It is not an ML model,
Evidence source, approval system, activation switch, registry, database, or
ModelHub integration.

## Frozen authority

Every run requires an immutable `AnalysisAnomalyPolicy`: one tenant, exact UTC
reference/current half-open windows, query cutoff, sample minimums, thresholds,
and baseline/query/producer versions. The calibration input must be a complete
`confidence-calibration-dataset.v2` manifest. The consumer recomputes its
manifest and row checksums and verifies its exact nested schema, policy tenant,
`dataset_as_of`, market-data variant, schema versions, input roots, rows, and
row/group counts. Split ranges derive from the frozen cutoffs; row availability
must bind to its split and duplicate `(analysis_identity, horizon)` rows fail
closed. `dataset_as_of` covers `current_end` without exceeding `query_as_of`.
A raw row list is never authorized.

The analysis input root is recomputed from the exact #508 canonical event
anchors and must equal `input_roots.analysis_sha256`; a subset, extra event, or
plausible-looking fake digest fails closed. Manifest rows select the labeled
distribution cohort, while root-bound partial events remain visible to pipeline
diagnostics. Inputs must be canonical `analysis-quality.v1` events for the
trusted tenant and visible by `dataset_as_of`; the query cutoff is applied
separately. Foreign tenants and post-dataset events cannot affect quotas,
findings, roots, or hashes. Scoped inputs have finite
event-count, streaming canonical UTF-8 byte, per-field, nesting, node,
JSON-type, and finite numeric bounds
before order-dependent work.
Manifest and event streams have independent byte budgets, so a deliberately
small event budget cannot be consumed by manifest validation. Oversize,
non-finite, excessive-depth/node/count, and unknown-schema events fail before
event-anchor materialization; foreign-tenant and post-cutoff events are
discarded before scoped quota accounting.

## Explainable rules

The versioned rules compare fixed reference and current windows:

- absolute mean calibrated-confidence drift;
- zero-Evidence rate;
- mean maximum source share;
- per-analysis calibrated-confidence outliers using reference median/MAD;
- failure/partial rate (`PIPELINE_FAILURE_OR_PARTIAL`), where either the
  top-level analysis is not complete or any stage is failed;
- retry-spike rate (`PIPELINE_RETRY_SPIKE`), based only on stage attempts
  greater than one and never inferred from failure;
- missing-stage and robust latency anomalies, each with its own reason code.

If either window misses its frozen sample minimum, the only result is
`INSUFFICIENT_DATA`. Missing eligible quality metrics yield a deterministic
`DEGRADED_INPUT` finding, never silent substitution or approval.
A zero reference MAD is explicitly degraded rather than silently treated as a
healthy scale.

## Candidate-only output

Each finding contains a stable reason code, human-readable reason, exact
measurements and threshold. Its `candidate_diagnostic` binds the full query,
query hash, manifest checksum, rows checksum, analysis input root, manifest
versions, baseline version, and query version. Identity and provenance bind the
tenant, baseline specification checksum, manifest checksum, query checksum,
deterministic finding ordinal, reason code, and reason. Canonical sorting,
hashing, and cohort identity de-duplication
make replay independent of input order.
The provenance `source_record` has an exact schema and repeats the trusted
tenant, baseline specification checksum, manifest/rows/input-root identities,
manifest versions, query checksum and specification, ordinal, reason code, and
finding checksum. Its integrity checksum is computed over that complete record.

The exact payload states `classification=non_evidentiary_candidate`,
`eligible_as_evidence=false`, and `candidate_only=true`, and contains frozen
thresholds plus reference count, confidence mean/median/MAD, Evidence-missing
rate, source concentration coverage/mean share, pipeline anomaly rate, and
latency mean/median/MAD; it also contains a manifest summary, reproducible query, and
input identity root/count/exclusion/degradation summary. The learning-event contract forbids
`approval_action`, `activation`, `proposal`, `active_version`, authority aliases,
and evidentiary discriminator fields. No result
can approve a model, change traffic, become Evidence, or mutate runtime state.

Rollback means calling the pure function with a previously retained, explicitly
selected manifest and `baseline_version`, then verifying the reproduced report
checksum. There is intentionally no mutable current pointer, DB/filesystem
write, implicit fallback, or side effect.
