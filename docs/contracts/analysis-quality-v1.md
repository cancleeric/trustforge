# `analysis-quality.v1` emission contract

`analysis-quality.v1` is the immutable observation produced for one analysis.
It is historical, non-evidentiary input for later learning. It is not an
outcome, label, training dataset, model activation, or authorization decision.

## Authority and identity

- The caller-supplied `trusted_tenant_id` is the only tenant authority.
- A snapshot tenant assertion may only match that trusted value.
- The canonical identity is tenant-bound and derived from `analysis_id`.
- Revision is fixed at `1`; an existing identity cannot be rewritten.

## Required canonical data

The event records:

- event, availability, as-of, and source-availability PIT timestamps;
- raw and calibrated confidence plus direction and decision;
- analysis, run, question, answer, and evidence-snapshot references;
- the actual question text (distinct from question type);
- the full canonical Evidence snapshot; each item carries source, canonical
  fetched time, content reference, related claim, schema version, and trust;
- evidence support, contradiction, count, average trust, independent-source
  count, and mutually exclusive source-category distribution;
- freshness, conflict, missingness, and completeness quality;
- contract, kernel, scoring, evidence, prompt, and model versions;
- per-stage latency, status, attempts, and structured failure;
- complete or partial top-level failure disposition;
- source, collector, observation time, and a checksum over the stable
  analysis/PIT/version source-record summary.

Missing, unknown, cross-tenant, internally inconsistent, or future-available
data fails closed. Outcome and gold-label identities are forbidden.
`evidence_snapshot_id` is the `sha256:` canonical content identity of the full
Evidence snapshot and is verified during construction. Evidence timestamps,
source availability, and provenance observation must not follow the trusted
analysis `available_time`.
Evidence items use an exact, closed schema with no extension passthrough:
`source`, `fetched_at`, `content_reference`, `related_claim`, `schema_version`,
and `trust`. Unknown fields—including outcome, label, diagnostic, approval, or
activation surfaces—fail closed even when the caller recomputes the snapshot
hash.

## Delivery semantics

The emission boundary accepts only an append-only sink:

- first append returns `created`;
- byte-identical redelivery returns `idempotent`;
- same identity with different canonical content is a conflict and fails;
- sink errors and exceptions propagate and are never reported as success.

Transport retry metadata is deliberately outside canonical event data. A
delivery retry therefore cannot change the event checksum or bytes. Partial
analysis failure is canonical analysis data and must be represented by the
stage and top-level failure structures. Per-stage `attempts` describes analysis
execution and is canonical; it is not a transport delivery attempt.

Answer content remains in its authoritative record and is referenced by
`answer_id`. The Evidence snapshot is intentionally embedded and deeply frozen;
`evidence_snapshot_id` provides its stable content address.

PIT and provenance values are supplied separately through trusted caller
authority. Same-named snapshot values are assertions only and must match their
trusted canonical values; advancing `as_of_time` or changing collector/source
inside the snapshot cannot change the authority.

Runtime `AnalysisFlow`/HTTP wiring and feature flags are deferred to Issue
#570. Persistence backends, migrations, delayed labeling, datasets, training,
backfill, and ModelHub writes are outside this contract.
