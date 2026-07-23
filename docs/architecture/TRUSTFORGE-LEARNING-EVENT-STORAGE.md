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

Publication pins both the event directory and its sibling
`.learning_events.staging` directory. The cross-directory safe-fs primitive
writes and fsyncs a random staging file, hard-links it into the event namespace,
unlinks the staging entry, fsyncs the staging directory, and finally fsyncs the
event directory. The event is committed only after both directory fsyncs
succeed. A pre-commit failure rolls back destination and staging entries and
durably syncs the rollback; an incomplete rollback is an explicit error.

The first process atomically publishes the event; concurrent writers of
identical canonical bytes return `idempotent`. A writer that reuses an identity
with different bytes is rejected. Staging files never share the replay
namespace. An unexpected `.tmp` or any other non-event entry in the event
directory is still corruption and fails closed.

All publication and FileExists reconciliation runs under a cross-process
exclusive OS lock. Replay and snapshot hold a shared lock for their complete
scan, validation, and read. The lock is a securely opened, no-follow regular
file in the sibling `.learning_events.control` namespace; it is never reopened
by pathname by the locking library. Before every lock, POSIX validates that the
pinned control fd is a directory owned by the current uid and is not writable by
group or world. After acquisition, the opened lock fd must still match the
single-link regular file in that pinned namespace, preventing a pathname swap
from entering the critical section on a different inode.

`portalocker` supplies shared/exclusive advisory locking. Unsupported shared
locking, non-finite or expired timeout, unsafe control or lock metadata, and
lock identity replacement all fail closed. OS process exit releases the lock,
so there is no PID file or stale-owner deletion.

This implementation is enabled on macOS and Linux. It intentionally fails
closed on Windows because this scope does not include a trustworthy Windows ACL
validator for the control directory. Portalocker's Windows lock backend alone
does not establish safe lock-namespace ownership. Windows enablement therefore
requires a separately reviewed ACL validation implementation.

The lock prevents readers or losing writers from observing a destination
hard-link while the winning writer is still between link and commit/rollback.
An identical FileExists reconciliation fsyncs the event directory before
returning `idempotent`, which safely completes durability if a previous process
crashed after unlinking staging but before the final directory fsync.

An exclusive writer also performs bounded crash cleanup in staging. It accepts
only the exact store-generated temporary-name grammar and regular files. A
single-link temp is removed; a two-link temp is rolled back only when its event
link resolves to the exact same inode. Unexpected entries, excessive entries,
extra hard links, symlinks, or mismatched inodes fail closed rather than being
followed or deleted.

Replay iterates the pinned directory descriptor with bounded `scandir`; it stops
as soon as the configured event-count limit is exceeded and sorts only the
bounded result. Before reading, no-follow metadata checks reject non-regular
entries and enforce the aggregate byte limit. Reads use
`safe_fs.read_regular_file_at` on the same pinned descriptor with a per-event
size bound, and the aggregate limit is checked again against actual bytes.
Replay fails closed for truncated or invalid JSON, non-canonical bytes,
oversized files, digest mismatch, symlinks, non-regular entries, or unexpected
directory contents. Operators must quarantine and investigate corruption rather
than silently skipping it.

`replay` and `snapshot` require a keyword-only `trusted_tenant_id`. They validate
the complete store and return only that tenant's events. There is intentionally
no all-tenant or administrative replay API in this scope.

## Explicit exclusions

- No database schema, migration, transaction, or SQL.
- No `source_archive`, feature-store, or ledger replacement.
- No AnalysisFlow or HTTP runtime connection.
- No delayed labeler, calibration, RAG, ModelHub, training, or activation.
- No cross-host distributed consistency guarantee; this store targets one
  durable local filesystem.
