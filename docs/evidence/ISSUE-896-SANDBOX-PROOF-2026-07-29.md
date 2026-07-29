# Issue #896 production-adapter sandbox proof

Date: 2026-07-29  
Region: `us-east-1`  
Account: `795930814369`

## Safety boundary

- Caller: `arn:aws:iam::795930814369:user/trustforge-896-sandbox-runner`
- Table: `arn:aws:dynamodb:us-east-1:795930814369:table/trustforge-issue896-sandbox-3`
- Table tags: `Environment=sandbox`, `Purpose=issue-896-proof`
- Billing: on-demand
- Encryption: enabled
- Point-in-time recovery: enabled
- Root credentials were not used by the proof runner.
- The runner policy was scoped to the single proof table.

## Result

The one-shot production-adapter runner completed successfully:

```text
proof=passed batch_id=spike-d0c3c694-682b-449b-8316-da8617390ac8 jobs=5 replay=verified competitor=denied
```

Consistent post-run inspection showed:

- exactly 13 table items;
- `remaining_usd=0`;
- `reserved_total=0.00001`;
- `config_version=issue-896-v3`;
- one admitted batch with five jobs;
- same-key replay returned the original batch and job manifest;
- the competing batch was denied without partial writes.

## IAM finding from the first attempt

The first real adapter attempt failed closed with zero writes because the
least-privilege policy granted `dynamodb:TransactWriteItems` but omitted the
transaction's underlying `dynamodb:PutItem` and `dynamodb:UpdateItem` actions.
DynamoDB authorizes each item operation inside a transaction. The policy,
runbook, and regression test were corrected before the successful proof.

## CloudTrail evidence

- Trail: `trustforge-issue896-proof`
- Destination: dedicated encrypted, public-blocked S3 audit bucket
- Selector: write-only DynamoDB data events for the exact proof table
- Trail status before execution: logging, no delivery error

The final proof used a fresh table only after the exact table selector reported
active and the trail reported `IsLogging=true` with no delivery error. AWS
delivered the actual adapter admission and the runner's competitor rejection in
the same immutable object:

- Object:
  `AWSLogs/795930814369/CloudTrail/us-east-1/2026/07/29/795930814369_CloudTrail_us-east-1_20260729T0335Z_6IyXViCMmgFycgjf.json.gz`
- Object SHA-256:
  `5ede5c4a3a204f69fa98a9ce02b2e1a64da6ffd58ec03f19428af31c388f4bf3`
- Admission: `2026-07-29T03:32:36Z`, `TransactWriteItems`,
  `errorCode=null`, request ID
  `MJQFG0R0L7ELMT2NIS62EEI11JVV4KQNSO5AEMVJF66Q9ASUAAJG`
- Competitor rejection: `2026-07-29T03:32:37Z`,
  `TransactionCanceledException`, request ID
  `O0I9Q91OKPOKCM7UBSSKCSD0HJVV4KQNSO5AEMVJF66Q9ASUAAJG`
- Both events identify the exact non-root caller and exact sandbox table ARN
  listed in the safety boundary.

CloudTrail does not serialize DynamoDB transaction item bodies in these data
events. The successful admission event is cross-checked against the runner
output and the immediately following consistent read: one budget counter, one
request, one batch metadata item, five allocations, and five jobs (13 items).

## Auditable artifacts

- Sanitized runner output and complete consistent-read key/type inventory:
  `docs/evidence/issue-896-sandbox-3-readback.json`
- Exact deployed IAM policy:
  `deploy/iam/issue-896-sandbox-runner-policy.json`
- Canonical (`jq -S -c`) IAM policy SHA-256, matching AWS and the committed
  artifact:
  `76367059efc0ecef16f4717ff9ebc01ae28d2a8fc8544e3ea1acc02267b2b693`
- The committed allowlist is returned to `enabled=false` after the one-shot
  run.

No access key or secret is recorded in these artifacts.
