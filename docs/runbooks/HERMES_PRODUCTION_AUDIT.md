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
failure table is SQLite `analysis_dead_letters`, not a DynamoDB table: the same
SSM command opens it with a read-only, belt-and-suspenders connection
(`file:...?mode=ro` plus `PRAGMA query_only=1`, with a `busy_timeout` shorter
than this audit's own read budget so a concurrent WAL writer can never exhaust
it) and reads at most `local-io` budget's `item_limit` (32) most-recent rows,
ordered by `failed_at DESC`. An available, non-truncated table (including an
empty one) is `complete`; a missing file/table, a lock, or an over-budget
payload is fail-closed to `insufficient-evidence`, never a crash. The raw
`error` column text never leaves the SSM script process: it is reduced in
place to an allowlisted exception class name or the fixed sentinel
`unclassified-error` before being returned. `retry_state` is always the fixed
string `dead-lettered` (every row in this table has already exhausted its
retries by construction) and `release_identity` is always `null`, a known
limitation — the source table does not record it.

The three table-name environment flags (`TRUSTFORGE_CACHE_TABLE`,
`TRUSTFORGE_SCHEDULER_RUN_TABLE`, `TRUSTFORGE_COST_LEDGER_TABLE`) now carry
their real remote value (validated against a safe character-set pattern)
instead of a bare `configured`/`absent` tri-state. A remote value is only ever
adopted when it exactly matches that table's own pre-approved name; any other
syntactically valid but unapproved value is fail-closed (that table's binding
is dropped for this audit, reported as insufficient evidence) rather than
silently falling back to reading the previously-approved table. A local shell
environment variable on the operator's machine can never influence this
decision either way; only the SSM snapshot's own validated value can.

## Mandatory dry-run review

Before any production read, use the exact target and output path:

```sh
.venv/bin/python scripts/hermes_production_audit.py \
  --region ap-southeast-2 \
  --instance-id <EC2_INSTANCE_ID> \
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

Do **not** remove `--dry-run` or make a real production read until four
independent Ed25519-signed approval attestations exist, one each from CEO,
CPO, CISO, and the operator. Each attestation is a small signed JSON file
binding: the exact region/instance target, the expected release (if any),
the exact `--output-dir`, and the static SSM command's SHA-256 digest, plus
an `issued_at`/`expires_at` validity window and a one-time nonce. A single
signer can never stand in for another role, and a signature is invalid for
every purpose other than the one role it names — the command refuses to run
if any file is missing, unsigned, forged, expired, not-yet-valid, bound to a
different target/release/output path/command digest than the one actually
being invoked, or if any two of the four attestations share the same actor
or the same signing key (self-approval).

Each signer independently runs (offline, ahead of the actual audit
invocation) the project's approval-signing helper
(`trustforge.hermes_audit_signing.sign_approval_attestation`) with their own
private Ed25519 key, producing one `<role>-approval.json` file. Operators
must also hold a public `--approval-verification-keyring` file (the
`secure_keyring` public-keyring JSON contract) listing all four signers'
verification keys. Invoke the real audit as:

```sh
.venv/bin/python scripts/hermes_production_audit.py \
  --region ap-southeast-2 \
  --instance-id <EC2_INSTANCE_ID> \
  --output-dir out/audits/hermes \
  --expected-release v0.27.37 \
  --ceo-approval ceo-approval.json \
  --cpo-approval cpo-approval.json \
  --ciso-approval ciso-approval.json \
  --operator-approval operator-approval.json \
  --approval-verification-keyring approval-verification-keyring.json \
  --signing-keyring signing-keyring.json
```

Each of the four nonces is consumed exactly once, ledger-wide, at the moment
this command runs (`--approval-nonce-ledger-dir`, default
`out/audit-approval-nonce-ledger`): replaying any single already-used
attestation — even mixed with three otherwise-fresh ones — rejects the
whole bundle and consumes nothing. A new short-lived AWS credential scoped to
the permissions above must also be in place before running without
`--dry-run`. No unit, table, configuration, flag, deployment, or database may
be changed in the same window. The operator records the command's JSON status
and bundle digest, not its raw remote output.

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
