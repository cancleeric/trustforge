# TrustForge learning-event storage

## Scope

`FileLearningEventStore` is the first durable, non-database persistence boundary
for canonical three-track learning events. It stores only events that already
pass `learning_event_contract`; it does not authorize collection, labeling,
training, ModelHub submission, model activation, or runtime wiring.

The default local path is:

```text
${TRUSTFORGE_HOME:-<repository-root>}/out/learning_events
```

Callers may inject a different directory for tests or an explicitly managed
local deployment.

## On-disk contract

Each event is canonical UTF-8 JSON with no newline. The filename is:

```text
sha256(canonical identity UTF-8 bytes).json
```

The digest-only filename avoids exposing tenant or entity identifiers in
directory listings. The event envelope remains tenant-bound and is validated on
every read. Replay sorts filenames for deterministic ordering and revalidates
the identity digest, canonical serialization, schema, provenance checksum,
temporal constraints, and kind discriminator.

## Durability and failure behavior

Publication pins the store directory once and uses
`safe_fs.write_atomic_at(..., immutable=True)`. The first process
atomically publishes the event; concurrent writers of identical canonical bytes
return `idempotent`. A writer that reuses an identity with different bytes is
rejected. File and directory fsync are required before `created` is returned.
If publication fsync fails, the destination is rolled back and no event is
reported as durable.

Replay lists the pinned directory descriptor and uses
`safe_fs.read_regular_file_at` on that same descriptor with a size bound and
no-follow semantics.
Replay fails closed for truncated or invalid JSON, non-canonical bytes,
oversized files, digest mismatch, symlinks, non-regular entries, or unexpected
directory contents. Operators must quarantine and investigate corruption rather
than silently skipping it.

## Explicit exclusions

- No database schema, migration, transaction, or SQL.
- No `source_archive`, feature-store, or ledger replacement.
- No AnalysisFlow or HTTP runtime connection.
- No delayed labeler, calibration, RAG, ModelHub, training, or activation.
- No cross-host distributed consistency guarantee; this store targets one
  durable local filesystem.
