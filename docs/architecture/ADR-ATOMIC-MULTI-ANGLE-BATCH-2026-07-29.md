# ADR: Multi-angle batch uses one DynamoDB transaction authority

Status: spike accepted pending sandbox proof (#896); production integration is #884.

## Decision

Production admission will use one DynamoDB table and one `TransactWriteItems`
request. The transaction conditionally decrements an authoritative,
pre-bootstrapped UTC-day `remaining_usd`, increments `reserved_total`, and
creates one durable caller/idempotency request, one immutable batch, five
allocations, and five pending jobs. It is all or nothing. SQLite implements the same `create_batch()` contract for local
development only and is never a production fallback.

The key layout is:

- `BUDGET#<UTC day>` / `COUNTER`
- `REQUEST#<caller hash>` / `IDEMPOTENCY#<key hash>`
- `BATCH#<batch id>` / `META`
- `BATCH#<batch id>` / `ALLOCATION#<mode>` (five)
- `JOB#<job id>` / `META` (five)

The budget item is created only by the reviewed configuration/bootstrap plane.
Admission requires that it exists, its `config_version` matches, and
`remaining_usd >= batch_cost`; no caller-supplied spent/cap is authoritative.
Every created item has an existence condition. A deterministic
`ClientRequestToken` protects immediate SDK retries; the durable request item
makes later retries replay-safe. Same caller/key with a different fingerprint is
a conflict. Replay succeeds only after a consistent read verifies the batch and
all five allocations/jobs.

Shared-backend errors raise `BatchStoreBackendError`; callers must fail closed.
There is no process-local fallback.

## Worker claim boundary

#884 will add a conditional job transition `pending -> claimed`, bound to the
batch allocation. This spike intentionally does not wire workers or consume
allocations. #885 owns reconcile/release.

## Consequences

The transaction has 13 actions, below DynamoDB's transaction limit. Active
request, batch, allocation, and job records have no TTL. #885 may apply
retention only after terminal reconciliation. Production
must provision a table with string partition and sort keys named `pk` and `sk`,
TTL on `expires_at`, encryption, PITR, alarms, and least-privilege permissions.
Those infrastructure changes are outside this spike.
