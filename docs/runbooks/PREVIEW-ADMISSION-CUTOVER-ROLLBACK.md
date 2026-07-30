# Paid-preview admission store cutover and rollback

This runbook provisions infrastructure for #975 only. It does not connect the
public endpoint, policy routing, UI, or question types. `PreviewEnabled`
defaults to `0`; the formal service remains isolated.

## Provision and verify

1. Deploy `deploy/preview-admission-store.yaml` with an exact runtime role,
   exact current and optional previous versioned SecureString parameter ARNs,
   and their exact KMS key ARN.
2. Confirm the dedicated table is `ACTIVE`, `PAY_PER_REQUEST`, has only string
   `pk`/`sk`, KMS encryption, TTL on `ttl`, PITR enabled, and the
   `TrustForgeComponent=preview-admission` tag.
3. Run `python deploy/bootstrap_preview_admission_store.py --allow-aws --table
   trustforge-preview-admission --table-arn TABLE_ARN --table-kms-key-arn
   TABLE_KMS_ARN --initial-shard EPOCH_MINUTE`. It first verifies the exact
   table/schema/TTL/PITR/table-SSE CMK, then conditionally
   creates only the fixed OPEN control and recovery watermark, accepts an
   existing row only when it matches exactly, and never writes secret bytes.
   Attach lifecycle metadata separately with `preview_admission_admin.py
   --allow-aws install-lifecycle`; this is the explicit mutating #993 authority
   path using exact SSM `name:version` references. Never infer missing rows.
4. Run runtime readiness with the feature still off. Missing/malformed
   table/TTL/PITR/KMS/tag/key/lifecycle/clock evidence must remain unavailable.
   `deploy/preview_admission_smoke.py` must first report `off` with the flag
   absent, then report `ready` in dark mode before canary traffic is admitted.
   Enabled deployments require the compiler-aligned non-secret caps:
   `MAX_MINUTE_TOKENS=8000`, `MAX_DAY_TOKENS=51200`,
   `MAX_MINUTE_MICRO_USD=50000`, and `MAX_DAY_MICRO_USD=500000`, each with the
   `TRUSTFORGE_PREVIEW_` environment prefix. Missing or different values fail
   before provider I/O. Both first deploy and update promotion execute
   `preview_admission_release_gate.sh`; unavailable aborts promotion/rolls back.

## Canary

Move `dark → canary` only when `PreviewStoreReadiness.enabled` is true. Verify
one internal canary through admission, terminal reconciliation, restart
recovery, and lifecycle checks. Move `canary → enabled` only with recorded
canary evidence. Enabling means the store runtime is ready; endpoint work
remains owned by #956.

## Rollback

1. Stop new preview traffic first. The `enabled/canary → disabled` kill switch
   is unconditional and must never wait for recovery or cleanup.
2. Keep the table, PITR, TTL, SSM revisions, KMS key, and lifecycle tombstones.
3. Run bounded D2 recovery until the strong recovery watermark is beyond the
   required shard. The durable admission gate must be exact OPEN with no
   pending binding, and lifecycle mode must be SINGLE.
4. Only a positive `preview_admission_admin.py --allow-aws disable-check`
   result permits cleanup/retirement. The required shard/version comes only
   from the durable retirement waterline in the same strong transaction; it is
   never supplied by the operator. An error, ABSENT
   ambiguity, overlap, unavailable reaper, stale/low shard, or lag is not proof.
5. Retain table and key revisions for at least reservation retention plus the
   completed rotation period. This runbook contains no delete command.

Rollback disables preview only. It never enables a legacy budget fallback and
does not change the formal analysis service.
