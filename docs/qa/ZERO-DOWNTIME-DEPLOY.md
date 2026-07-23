# Production Zero-Downtime Deployment Evidence

> Historical evidence only. Current releases follow
> [Release and Deployment Governance](../RELEASE-DEPLOY-GOVERNANCE.md).
> GitHub Actions are intentionally disabled and are not a current deployment
> mechanism.

## Scope

TrustForge keeps the primary backend on port 8080 and starts a health-checked
deployment candidate on port 8081. Nginx uses 8081 as a backup upstream while
the primary service is replaced. The release workflow probes the public health
endpoint every 250 ms before, during and after the backend deployment; any curl
failure or non-2xx response fails the release.

## Production Evidence (2026-07-14)

| Release | GitHub run | Artifact | Samples | During deploy | Non-2xx / connection failures | Maximum latency |
|---|---:|---:|---:|---:|---:|---:|
| `v0.14.8` | `29323981609` | `8307182431` | 37 | 35 | 0 | 1.346 s |
| `v0.14.9` | `29324508069` | `8307403335` | 33 | 31 | 0 | 1.624 s |
| `v0.14.10` | `29325019645` | `8307605322` | 26 | 24 | 0 | 1.551 s |

All three artifacts contain `deploy-health-canary.jsonl`. Across 96 public
probes, every sample returned HTTP 200 with curl exit code 0. Each run contains
at least one `before` and `after` sample, plus continuous `during` samples.

## Historical Verification

- `deploy/prepare_backend_deploy_backup.sh` creates and health-checks the
  temporary candidate before the primary restart.
- All production nginx configurations route through the 8080 primary and 8081
  backup upstream.
- `scripts/monitor_deploy_health.sh` was used as a fail-closed wrapper around
  the backend deployment.
- The historical GitHub-run artifacts included the health canary and
  question-bank result. They do not authorize or describe the current release
  path.

This evidence validates uninterrupted public health availability for these
three production releases. It does not claim that every external upstream data
provider was available during the deployments.
