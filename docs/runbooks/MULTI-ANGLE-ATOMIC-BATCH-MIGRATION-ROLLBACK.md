# Multi-angle atomic batch migration and rollback

## Preconditions

1. Provision a non-production table (`pk`/`sk` string keys) through reviewed
   infrastructure code. Bootstrap the authoritative daily budget item with
   `remaining_usd`, `reserved_total=0`, and a reviewed `config_version`.
2. Grant `dynamodb:TransactWriteItems` plus the transaction's underlying
   `dynamodb:PutItem` and `dynamodb:UpdateItem` actions, consistent
   `dynamodb:GetItem`, and `dynamodb:BatchGetItem` on that table to the
   application role. DynamoDB authorizes every item action inside a transaction;
   `TransactWriteItems` alone is insufficient. The proof runner also needs
   `sts:GetCallerIdentity`, `dynamodb:DescribeTable`,
   `DescribeContinuousBackups`, and `ListTagsOfResource`. Never use root
   credentials.
3. Populate and review the version-controlled
   `deploy/config/multi-angle-batch-sandbox.json`; it is committed disabled and
   must pin SHA-256 digests for one exact account, caller ARN, and table ARN,
   plus the region and budget config version. Never commit the raw identifiers.
   The table must be dedicated to the proof, tagged
   `Environment=sandbox`, encrypted, PITR-enabled, and bootstrapped with exactly
   one batch of remaining capacity. Run
   `scripts/run_multi_angle_batch_sandbox.py --confirm-sandbox` once and retain
   CloudTrail/readback evidence. The CloudTrail trail must be logging the exact
   table's DynamoDB write data events before the one-shot transaction runs;
   data events cannot be reconstructed retroactively. Re-running requires a
   separately reviewed sandbox data reset or a fresh dedicated table; the
   runner never resets counters or deletes records.
4. Complete #884 worker integration and #885 reconciliation gates.

### One-shot sandbox allowlist activation

1. From the protected deployment environment, compute SHA-256 for the reviewed
   values of `TRUSTFORGE_SANDBOX_ACCOUNT_ID`,
   `TRUSTFORGE_SANDBOX_CALLER_ARN`, and `TRUSTFORGE_SANDBOX_TABLE_ARN`. Commit
   only those three digests, set `enabled=true`, and obtain security review.
   The caller must be the `trustforge-896-sandbox-runner` role and the table
   must be exactly `trustforge-issue896-sandbox-3` in the reviewed account and
   region.
2. Supply the three raw values only through the protected runtime environment.
   Run `scripts/run_multi_angle_batch_sandbox.py --confirm-sandbox`. The runner
   verifies each value against the commit-bound digest before contacting
   DynamoDB and then verifies STS identity, exact ARN, encryption, PITR, and
   sandbox tag.
3. Retain the receipt and CloudTrail evidence, immediately unset the three
   runtime variables, and submit a follow-up reviewed commit restoring
   `enabled=false` and blank digests. Never reuse the one-shot capacity without
   a separately authorized reset/fresh table procedure.

## Migration

Deploy the adapter dark, then dual-observe without dual-writing cost state.
Enable atomic admission for an internal canary, verify exactly one batch and
five jobs per admitted request, then expand traffic. SQLite remains local-only.

Production activation additionally requires:

- `TRUSTFORGE_ATOMIC_BATCH_EXCLUSIVE=1`; this disables every legacy live budget
  admission path so atomic and legacy counters cannot spend from split truth.
- `TRUSTFORGE_SHARED_ANALYSIS_DB_PATH` set to the exact durable shared
  projection database path used by every web/daemon instance.

The release gate must additionally verify on every instance that this path is
the same shared mount/device (not a repository checkout, `/tmp`, container
ephemeral layer, or instance-local disk), is writable by the runtime role, and
survives an instance replacement/restart. A matching pathname alone is not
durability proof; retain mount/device and restart evidence with the release.

Without either setting, public atomic submission fails closed with HTTP 503.

## Rollback

Disable new multi-angle submissions first. Allow already claimed jobs to finish
under #885 reconciliation; do not delete reservations or jobs manually. Roll
application traffic back to the prior release. The prior public submission path
must remain disabled until a transaction authority is available—backend
unavailability fails closed.

Before re-enabling the legacy release, unset
`TRUSTFORGE_ATOMIC_BATCH_EXCLUSIVE` only after all atomic submissions are
disabled and #885 has reconciled outstanding reservations. Never run legacy and
atomic live admission concurrently.

This spike does not mutate the existing cost ledger and supplies no cleanup
command. Any data migration or reservation release requires a separate reviewed
runbook and issue.
