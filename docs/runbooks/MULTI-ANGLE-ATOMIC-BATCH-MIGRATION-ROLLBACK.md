# Multi-angle atomic batch migration and rollback

## Preconditions

1. Provision a non-production table (`pk`/`sk` string keys) through reviewed
   infrastructure code. Bootstrap the authoritative daily budget item with
   `remaining_usd`, `reserved_total=0`, and a reviewed `config_version`.
2. Grant only `dynamodb:TransactWriteItems`, consistent `GetItem`, and
   `BatchGetItem` on that table to the application role. The proof runner also
   needs `sts:GetCallerIdentity`, `dynamodb:DescribeTable`,
   `DescribeContinuousBackups`, and `ListTagsOfResource`. Never use root
   credentials.
3. Populate and review the version-controlled
   `deploy/config/multi-angle-batch-sandbox.json`; it is committed disabled and
   must name one exact account, caller ARN, table ARN, region, and budget config
   version. The table must be dedicated to the proof, tagged
   `Environment=sandbox`, encrypted, PITR-enabled, and bootstrapped with exactly
   one batch of remaining capacity. Run
   `scripts/run_multi_angle_batch_sandbox.py --confirm-sandbox` once and retain
   CloudTrail/readback evidence. Re-running requires a separately reviewed
   sandbox data reset or a fresh dedicated table; the runner never resets
   counters or deletes records.
4. Complete #884 worker integration and #885 reconciliation gates.

## Migration

Deploy the adapter dark, then dual-observe without dual-writing cost state.
Enable atomic admission for an internal canary, verify exactly one batch and
five jobs per admitted request, then expand traffic. SQLite remains local-only.

## Rollback

Disable new multi-angle submissions first. Allow already claimed jobs to finish
under #885 reconciliation; do not delete reservations or jobs manually. Roll
application traffic back to the prior release. The prior public submission path
must remain disabled until a transaction authority is available—backend
unavailability fails closed.

This spike does not mutate the existing cost ledger and supplies no cleanup
command. Any data migration or reservation release requires a separate reviewed
runbook and issue.
