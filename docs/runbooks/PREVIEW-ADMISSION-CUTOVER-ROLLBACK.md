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
3. Run `python deploy/bootstrap_preview_admission_store.py --table
   trustforge-preview-admission --initial-shard EPOCH_MINUTE`. It conditionally
   creates only the fixed OPEN control and recovery watermark, accepts an
   existing row only when it matches exactly, and never writes secret bytes.
   The sealed runtime conditionally installs lifecycle metadata from exact SSM
   `name:version` references. Never infer missing control rows.
4. Run runtime readiness with the feature still off. Missing/malformed
   table/TTL/PITR/KMS/tag/key/lifecycle/clock evidence must remain unavailable.
   `deploy/preview_admission_smoke.py` must first report `off` with the flag
   absent, then report `ready` in dark mode before canary traffic is admitted.

## Canary

Move `dark → canary` only when `PreviewStoreReadiness.enabled` is true. Verify
one internal canary through admission, terminal reconciliation, restart
recovery, and lifecycle checks. Move `canary → enabled` only with recorded
canary evidence. Enabling means the store runtime is ready; endpoint work
remains owned by #956.

## Rollback

1. Stop new preview traffic first.
2. Keep the table, PITR, TTL, SSM revisions, KMS key, and lifecycle tombstones.
3. Run bounded D2 recovery until the strong recovery watermark is beyond the
   required shard. The durable admission gate must be exact OPEN with no
   pending binding, and lifecycle mode must be SINGLE.
4. Only a positive `evaluate_preview_disable` result permits
   `enabled/canary → disabled`. An error, ABSENT ambiguity, overlap, or lag is
   not proof.
5. Retain table and key revisions for at least reservation retention plus the
   completed rotation period. This runbook contains no delete command.

Rollback disables preview only. It never enables a legacy budget fallback and
does not change the formal analysis service.
