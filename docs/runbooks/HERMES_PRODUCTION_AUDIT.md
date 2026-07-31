# Hermes production audit runbook

## Purpose and safety boundary

`hermes_production_audit.py` is a bounded Phase 0 evidence collector. It is not
an enablement, deployment, remediation, backfill, flag-change, connector, or
LLM command. Its only remote operations are STS caller identity, SSM managed
instance discovery and one fixed read-only command, plus allowlisted DynamoDB
metadata/TTL and bounded reads. It never uses SSH, an arbitrary shell command,
`aws login`, Bedrock, or a DynamoDB/SSM write operation.

A `complete` result means only that the bounded evidence was collected. It is
not approval to enable Hermes, Three-track, AGOS, or any production feature.
If no expected release/digest baselines are supplied, release comparisons remain
`unknown` and the result is deliberately `partial`; unknown evidence is never
promoted to a healthy conclusion. `blocked`, `partial`, `insufficient-evidence`,
integrity failure, an unknown control state, a truncated table, or a release
mismatch are all insufficient evidence and require a Phase 1 issue; do not
repair or toggle anything from this command.

## Required access and local prerequisites

Use a dedicated, short-lived AWS session with no permissions beyond:

- `sts:GetCallerIdentity`
- `ssm:DescribeInstanceInformation`, `ssm:SendCommand`, and
  `ssm:GetCommandInvocation` for the explicit audited instance
- `dynamodb:DescribeTable`, `dynamodb:DescribeTimeToLive`, `dynamodb:GetItem`,
  and `dynamodb:Scan` for the resolved allowlisted tables only

The session must not grant SSM session/port-forwarding, SSM parameter writes,
EC2 mutation, DynamoDB write, CloudWatch write, deployment, or feature-flag
permissions. Run from the repository root using its existing virtual
environment; do not install packages or use a global credential helper.

Local output must be under ignored `out/`; the tool rejects another path. It
creates a new `0700` audit directory only after a non-dry run and never stages,
commits, uploads, comments on an issue, or deletes evidence.

The static SSM reducer evaluates the allowlisted environment of
`hermes-cycle.service` (not the SSM process environment), and reads the actual
release manifest at `/opt/trustforge/manifest.json`. The durable analysis
failure table is SQLite `analysis_dead_letters`; this audit intentionally does
not guess or read a DynamoDB table for it, so the durable-dead-letter row is
reported as insufficient evidence until a separately approved read-only SQLite
adapter exists.

## Mandatory dry-run review

Before any production read, use the exact target and output path:

```sh
.venv/bin/python scripts/hermes_production_audit.py \
  --region ap-southeast-2 \
  --instance-id i-0152b70368358a81c \
  --output-dir out/audits/hermes \
  --expected-release v0.27.37 \
  --dry-run
```

The dry run makes no AWS client and no remote request. It prints the target,
static SSM-command SHA-256, immutable API allowlist, no-mutation assertion, and
fixed lower-only read limits. A second person must record that these values,
the short-lived credential identity, and the output path match the approved
window. Reject the run if any value differs, the command digest is unexpected,
or the output path is outside `out/`.

## Production-read authorization gate (Task 6)

Do **not** remove `--dry-run` or make a real production read until all of the
following are recorded in the review ticket/window:

1. An explicit CEO change window and exact region/instance target.
2. CPO approval for the evidence scope and CISO approval for the privilege,
   secret-handling, and retention boundary.
3. A new short-lived AWS credential scoped to the permissions above.
4. A second-person signed dry-run review of target, release, command digest,
   limits, and output path.

The approved operator may then run the same command without `--dry-run`. No
unit, table, configuration, flag, deployment, or database may be changed in
the same window. The operator records the command's JSON status and bundle
digest, not its raw remote output.

## Interpreting results

| Exit | Status | Required disposition |
|---:|---|---|
| 0 | `complete` | Preserve and review evidence; it does not authorize rollout. |
| 2 | `blocked` | Treat identity, target, or session evidence as unavailable. Do not use a fallback. |
| 3 | `partial` / `insufficient-evidence` | Create a bounded Phase 1 evidence/remediation issue. Do not infer health from an empty table. |
| 4 | `integrity-failure` | Treat as a potential redaction/schema incident; do not rerun with a broader command. |
| 5 | `internal-failure` | Preserve local diagnostics without secrets and investigate the local tool/credential boundary. |

A successful run writes `out/audits/hermes/<audit-id>/` with:

- `evidence.json`: canonical, secret-safe evidence and its embedded digest.
- `summary.md`: status, blockers, and the no-rollout disposition.
- `SHA256SUMS`: SHA-256 of the two local files.

Verify the checksum file locally before review. The evidence deliberately
contains only hashes and reduced allowlisted summaries: it does not contain
SSM stdout, full service environments, raw logs, DynamoDB items, market
bodies, prompts, user content, AWS credentials, API keys, or error text.

## Incident and retention path

If evidence appears to contain a secret, unexpected raw payload, or an
integrity failure, stop and preserve the bundle location/digest for CISO
review. Do not paste evidence into chat, tickets, or a commit, and do not
silently delete a bundle already referenced by a review. Follow the security
incident retention process to quarantine or dispose of it. For all other
partial/blocked outcomes, retain the local bundle for the review window and
open a Phase 1 issue that states the missing evidence, scoped owner, and
non-mutating next action.
