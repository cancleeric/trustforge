# Cost Ledger Durability

The cost ledger is append-only. The UI page is only a paginated view; it is not
the retention mechanism.

## Archive and Integrity Drill

Run on the production host with its normal ledger backend configured:

```bash
python3 scripts/cost_ledger_archive.py export --format jsonl --out out/ledger-archive.jsonl
python3 scripts/cost_ledger_archive.py export --format csv --out out/ledger-archive.csv
python3 scripts/cost_ledger_archive.py verify --archive out/ledger-archive.jsonl
python3 scripts/cost_ledger_archive.py restore-drill --archive out/ledger-archive.jsonl --out /tmp/ledger-restore-drill.jsonl
```

Each archive has a sidecar manifest containing the byte hash, canonical JSONL
hash, record count, and accumulated USD cost. Restore drills only write a new
local JSONL target and refuse to overwrite an existing ledger. They never write
back into DynamoDB.

## Production Retention Gate

Before treating the ledger as durable in production, enable DynamoDB point-in-
time recovery for `trustforge-cost-ledger`, retain an off-table archive copy,
and record a successful restore drill. Verify first, then enable only with a
privileged AWS session:

```bash
deploy/verify_cost_ledger_pitr.sh --verify
deploy/verify_cost_ledger_pitr.sh --enable
deploy/verify_cost_ledger_pitr.sh --verify
```

PITR is the primary recovery path. The JSONL archive is the portable,
off-table audit copy; upload its archive and manifest together to the approved
retention bucket after a successful verification. Record the archive hash,
PITR status, and restore-drill output in the release evidence.
