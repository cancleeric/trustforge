# Issue #896 production-adapter sandbox proof

Date: 2026-07-29  
Region: `us-east-1`  
Account: `795930814369`

## Safety boundary

- Caller: `arn:aws:iam::795930814369:user/trustforge-896-sandbox-runner`
- Table: `arn:aws:dynamodb:us-east-1:795930814369:table/trustforge-issue896-sandbox-2`
- Table tags: `Environment=sandbox`, `Purpose=issue-896-proof`
- Billing: on-demand
- Encryption: enabled
- Point-in-time recovery: enabled
- Root credentials were not used by the proof runner.
- The runner policy was scoped to the single proof table.

## Result

The one-shot production-adapter runner completed successfully:

```text
proof=passed batch_id=spike-505c813d-54c6-496c-94cc-47836322b38d jobs=5 replay=verified competitor=denied
```

Consistent post-run inspection showed:

- exactly 13 table items;
- `remaining_usd=0`;
- `reserved_total=0.00001`;
- `config_version=issue-896-v2`;
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
- Latest delivery: `2026-07-29T03:08:47.922000+00:00`, no delivery error

CloudTrail delivered the following successful, state-preserving
`TransactWriteItems` audit confirmation after the one-shot proof. The
transaction atomically added zero to the existing reservation counter; a
consistent read immediately afterward confirmed that the 13-item proof state,
`remaining_usd=0`, and `reserved_total=0.00001` were unchanged.

- Object:
  `AWSLogs/795930814369/CloudTrail/us-east-1/2026/07/29/795930814369_CloudTrail_us-east-1_20260729T0305Z_uwVyA5UJqr9WRTqP.json.gz`
- Event time: `2026-07-29T03:03:46Z`
- Event: `TransactWriteItems`
- Error code: `null`
- Request ID: `TEC8RDJ210HECN38KHKLEHRF2BVV4KQNSO5AEMVJF66Q9ASUAAJG`
- Caller:
  `arn:aws:iam::795930814369:user/trustforge-896-sandbox-runner`
- Resource:
  `arn:aws:dynamodb:us-east-1:795930814369:table/trustforge-issue896-sandbox-2`

CloudTrail also captured the preceding deliberately rejected conditional
transaction (`TransactionCanceledException`, request ID
`NS0S68JTRO796IMGFQUI8O4L07VV4KQNSO5AEMVJF66Q9ASUAAJG`), confirming the
fail-closed path. No access key or secret is recorded in this document.
