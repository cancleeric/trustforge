# Atomic multi-angle batch reconciliation

This runbook covers Issue #885 reconciliation only. The durable batch store,
allocations, call receipts, terminal outcomes, settlement, and synthesis lease
are the authority. Process memory and local queue state are not accounting
evidence.

## Safety contract

- The command is dry-run unless `--apply` is present.
- No backend is inferred. Select exactly one existing SQLite database or one
  DynamoDB table.
- DynamoDB access additionally requires both `--allow-aws` and an explicit
  `--region`; omission cannot create an AWS client.
- A consumed slot without a durable ledger receipt is `uncertain`. Never
  release it, replace its owner, fabricate a zero-cost receipt, or manually
  mark it terminal.
- Only five authoritative terminal jobs can settle a batch. Settlement is
  idempotent and releases unused reservation once.
- Synthesis uses a durable lease. A crashed claim may be taken over only after
  the stale cutoff; a completed claim is immutable.

## Inspect

For local parity:

```bash
python scripts/reconcile_multi_angle_batches.py \
  --sqlite /absolute/path/to/trustforge.sqlite3
```

For DynamoDB, first verify the active account, role, region, table, and change
ticket. Then run a read-only inspection:

```bash
python scripts/reconcile_multi_angle_batches.py \
  --dynamodb-table trustforge-atomic-batches \
  --region us-east-1 \
  --allow-aws
```

Archive the single JSON output. Review `ready`, `pending`, and `uncertain`.
Backend or integrity errors produce JSON on stderr and a non-zero exit code.

## Apply

After comparing each `ready` batch against its five job outcomes and ten call
slots, repeat the identical command with `--apply`. Do not widen the stale
window between review and apply.

```bash
python scripts/reconcile_multi_angle_batches.py \
  --sqlite /absolute/path/to/trustforge.sqlite3 \
  --apply
```

Rerun without `--apply`. Successfully settled batches must no longer appear as
ready, and budget `reserved_total` must fall by exactly the original batch
reservation while `remaining_usd` rises only by the recorded unused amount.

## Crash boundaries

- Ledger append succeeded, receipt write failed: retain as uncertain and repair
  the receipt from the exact immutable ledger record; never append a duplicate.
- Local report committed, authority terminal missing: restart reconciliation
  may replay terminal only when the durable local owner, batch, mode, job, and
  result identities all match.
- Settlement committed, synthesis did not start: wait for the synthesis lease
  cutoff, then allow one takeover. Do not rerun settlement or create another
  batch.
- Provider timeout with no usage receipt: classify as uncertain. Obtain
  provider/ledger evidence before any manual terminal decision.

## Escalation and evidence

Stop without `--apply` when a manifest is incomplete, costs exceed reservation,
the budget counter would underflow, pagination cannot complete, or AWS identity
is unexpected. Preserve the JSON output, relevant immutable ledger receipt IDs,
batch ID, job IDs, configured stale cutoff, and pre/post budget values for the
cost/security review.
