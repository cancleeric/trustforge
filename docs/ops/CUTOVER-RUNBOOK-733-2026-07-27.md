# #733 Production Cutover Runbook — Trust Kernel Canary & Promotion

## 1. Prerequisites (MUST verify before any cutover)

| Step | Check | Evidence |
|------|-------|----------|
| 1.1 | Previous approved A artifact exists and is verifiable | `python -m trustforge.verify_deployed_manifest --verify-active` |
| 1.2 | P0-4 rollback drill is valid and executable | `docs/drills/DRILL-730-2026-07-27.md` |
| 1.3 | Shadow parity rate from #732 >= 0.90 over last 30 runs | Check logs for `shadow_parity_rate` |
| 1.4 | Minimum canary coverage: >= 3 coins, >= 2 question types in window | Check `shadow_diagnostics().coins_seen` |
| 1.5 | No blocking streak (last 3 runs all passing) | Check `shadow_diagnostics()` |
| 1.6 | Production artifact digest matches candidate | `deploy/verify_release.sh` |

**DO NOT PROCEED** if any prerequisite fails.

## 2. Canary Deployment

### 2.1 Set canary ratio

```bash
# 5% canary (deterministic hash bucket)
export KERNEL_CANARY_RATIO=0.05
# Deploy with env var
./deploy/deploy_ec2.sh --env KERNEL_CANARY_RATIO=0.05
```

### 2.2 Verify canary is active

```bash
# Tail logs for kernel_canary diagnostics
journalctl -u trustforge -f | grep "canary_active\|canary_ratio"
# Expected: canary_ratio=0.05, canary_active=true
```

### 2.3 Monitor for minimum duration

Duration: **30 minutes** (default; tunable via `CANARY_MIN_DURATION_MIN`).

Metrics to watch:
- `total_canary_requests` >= 50
- `error_rate` < 0.01
- `consecutive_errors` < 5
- `shadow_parity_rate` maintained above 0.85

## 3. Auto-Stop Conditions

Canary automatically stops (falls back to legacy for all requests) if:
- Consecutive kernel errors >= 5
- Error rate > 1% after 100+ canary requests
- Canary state `should_stop=true` in diagnostics

## 4. Manual Promotion

After canary duration and metrics are healthy:

```bash
# Full kernel cutover
export KERNEL_CANARY_RATIO=1.0
./deploy/deploy_ec2.sh --env KERNEL_CANARY_RATIO=1.0
```

**Promotion is IRREVERSIBLE without a new deployment.** Use canary stop if uncertain.

## 5. Post-Promotion Verification

| Check | Command |
|-------|---------|
| Kernel is active for all requests | `grep "promotion.kernel_active" /var/log/trustforge/app.log` |
| No legacy `score()`/`aggregate()` calls | `grep -c "pipeline.step2" /var/log/trustforge/app.log` (should show count, but not dominate) |
| Report quality maintained | Run question bank: `python scripts/run_question_bank.py --limit 24` |
| Parity rate sustained | Check `shadow_parity_rate >= 0.90` |

## 6. Rollback

```bash
# Emergency: set ratio to 0 (legacy only)
export KERNEL_CANARY_RATIO=0
./deploy/deploy_ec2.sh --env KERNEL_CANARY_RATIO=0
```

**Rollback verifies:**
- Previous A artifact is activated via `activate_release.sh previous`
- Receipt is written to `pointers/receipts/`
- Post-rollback question bank passes

## 7. Contacts

- CISO review required before promotion (cost-sensitive)
- `/codex-review` adversarial audit required before merge
- Eye scan on actual branch required before merge
