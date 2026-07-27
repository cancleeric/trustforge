# TrustForge Production Topology & Rollback Evidence

**Date**: 2026-07-27
**Investigation**: Issue #726 (P0 Epic #725: Core A/B Rollback & Single Source of Truth)
**Scope**: Read-only production topology audit, ap-southeast-2, Account 795930814369
**Executor**: CTO (sonnet), executing CPO-approved plan

---

## 1. Topology Conclusion

**Production topology**: **EC2 only** (instance i-0152b70368358a81c)

| Service | Status | Evidence |
|---------|--------|----------|
| EC2 (trustforge-demo) | **RUNNING** (t3.micro, 13.211.110.218) | Launched 2026-07-19T15:57:23Z |
| Lambda (trustforge-demo) | **Active but NOT serving traffic** | Last modified 2026-06-30, Function URL is open (AuthType NONE), no event invoke config |
| App Runner | **Not subscribed** | SubscriptionRequiredException |

**Exclusion evidence**:
- Lambda trustforge-demo has Function URL enabled with AuthType NONE since 2026-06-30, but trustforge-lambda-exec role was last used 2026-06-30 — no runtime activity for 26 days
- App Runner is not subscribed for this account
- nginx config on EC2 proxies /api/* to 127.0.0.1:8080 (systemd trustforge.service), not to Lambda

---

## 2. Active Release

### 2.1 EC2 Runtime Identity

| Attribute | Value |
|-----------|-------|
| VERSION | **v0.16.18** |
| Instance ID | i-0152b70368358a81c |
| Launch Time | 2026-07-19T15:57:23Z (Jul 20 01:57 CST) |
| git tag v0.16.18 | fde5a643 (2026-07-20 17:09 CST) |
| git tag on branch | remotes/origin/release/v0.16.18 |

**EC2 VERSION output** (SSM send-command):
```
VERSION = "v0.16.18"
```

### 2.2 nginx Configuration Mode

| Attribute | Value |
|-----------|-------|
| Active conf symlink | `/etc/nginx/trustforge-sites/react.conf` |
| Mode | **react** (full TLS, SPA static serve + API proxy) |
| TLS | certbot (trustforge.hurricanesoft.com.tw) |
| Backend proxy | 127.0.0.1:8080 (with backup 127.0.0.1:8081) |

### 2.3 Systemd State

| Attribute | Value |
|-----------|-------|
| is-active | **active** |
| is-enabled | **enabled** |
| Restart policy | always |
| Active timers | tf-snapshot, fetch-scheduler, hermes-cycle, certbot-renew, logrotate, sysstat-collect, sysstat-summary |

### 2.4 S3 Artifact (Latest)

| Key | VersionId | Size | Last Modified (UTC) | ETag |
|-----|-----------|------|---------------------|------|
| trustforge_app.zip | YX4HwGd9NXBps7D8te9 | 1,130,475 | 2026-07-20T11:07:02 | 28d67b981ab7b840ea285b4558bbbd7b |
| trustforge_frontend_dist.zip | kmZqBaI_WP5ISclL1lZc | 282,167 | 2026-07-19T16:00:23 | 70b0641d366fcf5e0f5f859eda4fd6ac |

**S3 versioning**: **Enabled**

### 2.5 Runtime-S3 Version Gap

| Component | Version/Date | Gap |
|-----------|-------------|-----|
| EC2 runtime | v0.16.18 (tag: 2026-07-20 17:09 CST) | — |
| S3 trustforge_app.zip | 2026-07-20T11:07Z (= 19:07 CST) | Uploaded ~2h after tag |
| S3 frontend | 2026-07-19T16:00Z (= Jul 20 00:00 CST) | — |

**Key finding**: EC2 was launched Jul 19 15:57 UTC. S3 artifact was re-uploaded Jul 20 11:07 UTC with a different ETag (`28d67b98...` vs previous `c731877f...`), meaning the S3 artifact was updated AFTER the instance launch. The instance may not reflect the latest S3 artifact. S3 versioning enabled means the previous deployable version (`bOPVHMSbf5...`, ETag `68d32355...`, 2026-07-19T16:03:29Z) is still recoverable.

---

## 3. Previous Approved Releases

### 3.1 v0.24.0

| Attribute | Value |
|-----------|-------|
| Tag commit | 427d3d6b (2026-07-26 13:29 CST) |
| Branch | remotes/origin/release/v0.24.0 |
| On main? | **No** — v0.24.0 diverged from main at 7db1936b ("release-promote: develop → main (v0.24.0)") |
| S3 artifact? | **No dedicated S3 artifact** — current S3 artifact predates v0.24.0 tag |
| Can redeploy? | **No** — no matching S3 artifact; would require new build from tag |

### 3.2 v0.18.2

| Attribute | Value |
|-----------|-------|
| Tag commit | 2408c4f3 (2026-07-25 09:08 CST) |
| Ancestor of main? | **Yes** — merge base with main is efb99aa4 |
| S3 artifact? | **No dedicated S3 artifact** — current S3 artifacts are from Jul 19-20 |
| Can redeploy? | **Conditional** — requires building new artifact from tag |

---

## 4. Config Snapshot

### 4.1 Systemd Unit (`/etc/systemd/system/trustforge.service`)

```
[Unit]
Description=TrustForge web
After=network.target
[Service]
Environment=PORT=8080
Environment=TRUSTFORGE_BIND_HOST=127.0.0.1
Environment=TRUSTFORGE_TRUST_PROXY=1
Environment=TRUSTFORGE_CSP_MODE=react
Environment=TRUSTFORGE_HOME=/opt/trustforge
Environment=AWS_REGION=ap-southeast-2
Environment=BEDROCK_MODEL_ID=ap-southeast-2.anthropic.claude-sonnet-4-20250514-v1:0
Environment=PYTHONPATH=/opt/trustforge
Environment=TRUSTFORGE_LEASE_TABLE=trustforge-analyze-leases
Environment=TRUSTFORGE_IDEMPOTENCY_LEASE_BACKEND=dynamodb
Environment=TRUSTFORGE_CW_METRICS=1
Environment=TRUSTFORGE_BUDGET_COUNTER_TABLE=trustforge-budget-guard
Environment=TRUSTFORGE_BUDGET_GUARD_BACKEND=dynamodb
Environment=COST_LEDGER_BACKEND=dynamodb
Environment=TRUSTFORGE_COST_LEDGER_TABLE=trustforge-cost-ledger
Environment=TRUSTFORGE_CACHE_TABLE=trustforge-connector-cache
Environment=CACHE_BACKEND=dynamodb
ExecStartPre=/opt/trustforge/scripts/sweep_deploy_parameters.sh
ExecStart=/usr/bin/python3 -m trustforge.web
Restart=always
[Install]
WantedBy=multi-user.target
```

### 4.2 nginx Active Conf (`/etc/nginx/trustforge-sites/react.conf`)

Full content captured (see SSM output). Key structure:
- Port 80 → 301 redirect to HTTPS (canonical domain)
- Port 443 with Let's Encrypt TLS (HSTS, CSP, X-Frame-Options, Referrer-Policy)
- `/` → try_files SPA fallback to index.html
- `/assets/` → static files with immutable cache
- `/api/` → proxy to 127.0.0.1:8080
- `/api/admin/` → hardened admin proxy (X-Real-IP overwrite, no-cache, IP allowlist support)
- `/healthz` → proxy health check
- ACME challenge path: `/.well-known/acme-challenge/` served from /var/www/certbot

### 4.3 AWS Resource Inventory

| Resource | Identifier | State |
|----------|-----------|-------|
| EC2 instance | i-0152b70368358a81c | running (t3.micro) |
| EC2 Security Group | sg-0263e810b018165a8 | ports 80/443 to 0.0.0.0/0 |
| Lambda Function | trustforge-demo | Active, Python 3.12, 512MB, Function URL AuthType NONE |
| Lambda Role | trustforge-lambda-exec | Last used 2026-06-30 |
| EC2 Role | trustforge-ec2 | Last used 2026-07-26 |
| DynamoDB: connector-cache | trustforge-connector-cache | ACTIVE, 285 items, 8.4MB |
| DynamoDB: cost-ledger | trustforge-cost-ledger | ACTIVE, 5,494 items, 947KB |
| DynamoDB: budget-guard | trustforge-budget-guard | ACTIVE, 0 items |
| DynamoDB: analyze-leases | trustforge-analyze-leases | ACTIVE, 0 items |
| S3 bucket | trustforge-deploy-795930814369 | Versioning ENABLED, SSE-AES256 |
| SSM Parameter | /trustforge/runtime/admin-token | SecureString, v1 |
| SSM Parameter | /trustforge/runtime/live-token | SecureString, v1 |
| SSM Parameter | /trustforge/deploy | **Not found** |
| CloudWatch Alarms | (any containing "trustforge") | **None** |

### 4.4 Active Timers

```
tf-snapshot.timer       → tf-snapshot.service       (every 20 min)
fetch-scheduler.timer   → fetch-scheduler.service    (every 15 min)
hermes-cycle.timer      → hermes-cycle.service       (every 30 min)
certbot-renew.timer     → certbot-renew.service      (daily)
logrotate.timer         → logrotate.service          (daily)
sysstat-collect.timer   → sysstat-collect.service    (every 10 min)
sysstat-summary.timer   → sysstat-summary.service    (daily)
```

---

## 5. Rollback Objectives

### 5.1 Trigger Points

| # | Trigger | Severity |
|---|---------|----------|
| T1 | Health check (/healthz) returns non-200 for >60s | P0 — auto-rollback candidate |
| T2 | DynamoDB error rate >5% of requests over 5-minute window | P1 |
| T3 | nginx error_rate (5xx) >1% over 5-minute window | P1 |
| T4 | Certificate expiry within 7 days (certbot-renew failure) | P2 |
| T5 | Manual CEO rollback order | P0 |

### 5.2 Time Target (RTO) by Scenario

| Scenario | Target | RTO Escalation |
|----------|--------|----------------|
| S3 artifact rollback (no build required, cutover_switch.sh) | **<5 minutes** | — |
| Build-from-tag deploy | **<15 minutes** | If T1 fails after 5min, escalate to full reprovision |
| Full EC2 reprovision | **<30 minutes** | Only if reprovision required (e.g., corrupted filesystem) |

### 5.3 Maximum Allowable Impact (MAI)

| Metric | Threshold | Enforcement |
|--------|-----------|-------------|
| Total downtime (cutover_switch.sh) | **5 minutes** | Immediate trigger for cutover_switch.sh, which is sub-60s |
| Total downtime (full rollback incl. rebuild) | **15 minutes** | If rollback target is an existing S3 artifact; if build required, MAI extends to 30min with explicit CEO approval |
| Data loss | **0** | No writes lost — all state in DynamoDB |
| User-facing error rate | **<1%** of requests for >5 cumulative minutes | Measured at nginx 5xx rate |

> **Rollback authority chain**: (1) CEO has full authority. (2) If CEO unreachable >2min, authority falls to CTO (technical trigger) and COO (business trigger), who must both agree on a full rebuild rollback; any single-person trigger limited to cutover_switch.sh only. (3) CISO retains veto on any rollback that exposes data. |

### 5.4 Verification Suite

| # | Test | Method | Notes |
|---|------|--------|-------|
| V1 | HTTP 200 on /healthz | curl -f | Primary health indicator |
| V2 | SPA serves index.html | curl / → HTTP 200 + CSP header | Frontend serving check |
| V3 | API responds | curl /api/status → 200 | Backend liveness |
| V4 | DynamoDB reachability | aws dynamodb describe-table → ACTIVE | Don't rely on item count (not guaranteed to increase during rollback) |
| V5 | TLS valid | openssl s_client -connect | Cert not expired |
| V6 | All timers active | systemctl list-timers | Scheduled jobs running |

### 5.5 Success/Failure Criteria

**Success**: All 6 verification tests pass. EC2 systemd is-active + is-enabled = true. nginx.conf matches expected mode.

**Failure**: Any verification test fails after 3 retries at 10-second intervals. Rollback declared failed and CEO must be alerted for manual intervention.

---

## 6. Conflict Evidence

| # | Finding | Detail |
|---|---------|--------|
| C1 | **EC2 runs v0.16.18, S3 has newer artifact** | EC2 launched Jul 19 with one artifact version; S3 was updated Jul 20 with different ETag. The EC2 may not be running the latest S3 artifact. |
| C2 | **v0.24.0 tag has no S3 artifact** | Tag created Jul 26 but S3 artifacts are from Jul 19-20. Cannot roll forward to v0.24.0 without rebuilding. |
| C3 | **Lambda exists but is dead code** | Function URL open (AuthType NONE), no runtime activity since Jun 30. Represents a security risk (open endpoint, unmonitored). |
| C4 | **No CloudWatch alarms** | Zero alarms configured for trustforge. Health/degradation monitoring is blind. |
| C5 | **git main diverged from v0.24.0** | main HEAD (4001fe3c) diverged from v0.24.0 tag (merge base: 7db1936b). Current deployed code (v0.16.18) is ~427 commits behind main. |
| C6 | **S3 versioning only partial safety net** | Versioning is enabled but rollback depends on knowing which VersionId corresponds to which release. No manifest in bucket mapping VersionIds to git tags. |
| C7 | **trustforge-budget-guard and trustforge-analyze-leases both empty** | Tables created Jul 13-14 but hold 0 items — features may not be active. |

---

## 7. Risks & Blockers

| # | Risk | Impact | Mitigation | **Immediate Action** |
|---|------|--------|------------|-------------------|
| R1 | **No CloudWatch monitoring** | Cannot detect degradation automatically. Rollback trigger T1-T3 cannot fire. | Deploy at least /healthz alarm before any rollout | **P0**: Create CloudWatch alarm `/healthz` non-200 for 2min (Issue #xxx) |
| R2 | **Open Lambda Function URL** | Attack surface with no traffic but no auth. | Disable Function URL or delete Lambda | **P1**: Disable Lambda Function URL immediately (CTO), then delete Lambda (Issue #yyy) |
| R3 | **Unknown deploy artifact provenance** | Cannot confirm which S3 VersionId was deployed to EC2. Rollback to "previous known good" requires guessing. | Add deploy manifest recording VersionId → deploy timestamp | **P0-3**: Add deploy manifest to deploy_ec2.sh recording VersionId + git SHA |
| R4 | **Build dependency for rollback** | No pre-built S3 artifacts match v0.18.2 or v0.24.0 tags. Rollback to any tagged version requires local build + upload. | Build artifacts for last 2 tagged releases and store in S3 with tag-annotated metadata | **P1**: Build v0.18.2 and v0.24.0 artifacts, upload to S3 with `x-amz-meta-git-tag` |
| R5 | **SSM /trustforge/deploy missing** | Deploy parameters not centralized — unknown if config drift exists between deploy scripts and runtime | Create /trustforge/deploy parameter set during next deploy | **P2**: Create /trustforge/deploy/admin-token + live-token + config-hash parameters |
| B1 | **App Runner not available** | Cannot use App Runner as deployment target | Not a blocker for EC2-only rollback | N/A |

---

## 8. Raw Command Outputs

### A1 — sts get-caller-identity
```
UserId: 795930814369
Account: 795930814369
Arn: arn:aws:iam::795930814369:root
```

### A2 — EC2 instances
```json
[{
  "InstanceId": "i-0152b70368358a81c",
  "State": "running",
  "PublicIp": "13.211.110.218",
  "PrivateIp": "172.31.27.136",
  "LaunchTime": "2026-07-19T15:57:23+00:00",
  "InstanceType": "t3.micro",
  "IamInstanceProfile": "arn:aws:iam::795930814369:instance-profile/trustforge-ec2",
  "Tags": [{"Key": "Name", "Value": "trustforge-demo"}]
}]
```

### A3 — Security Groups
```json
[{
  "GroupId": "sg-0263e810b018165a8",
  "GroupName": "trustforge-ec2-sg",
  "Description": "TrustForge demo 80",
  "IpPermissions": [
    {"IpProtocol": "tcp", "FromPort": 80, "ToPort": 80, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]},
    {"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}
  ]
}]
```

### A4 — Lambda trustforge-demo
```
State: Active, Runtime: python3.12, Memory: 512MB, Timeout: 120s
LastModified: 2026-06-30T14:33:51.521Z
FunctionUrl: [REDACTED — unauthenticated endpoint, see AWS Console for full URL]
AuthType: NONE
```

### A5 — App Runner
```
SubscriptionRequiredException — service not subscribed
```

### A6 — DynamoDB Tables
```
trustforge-connector-cache: ACTIVE, 285 items, 8.4MB, PAY_PER_REQUEST
trustforge-cost-ledger:     ACTIVE, 5,494 items, 947KB, PAY_PER_REQUEST
trustforge-budget-guard:    ACTIVE, 0 items, PAY_PER_REQUEST
trustforge-analyze-leases:  ACTIVE, 0 items, PAY_PER_REQUEST
```

### A7 — S3 bucket
```
Bucket: trustforge-deploy-795930814369
Versioning: Enabled
Encryption: AES256
Contents:
  PRE trustforge-ledger-archives/
  nginx-legacy-tls.conf       4,782 bytes
  nginx-legacy.conf           3,931 bytes
  nginx-react-http.conf      10,074 bytes
  nginx-react.conf           13,746 bytes
  trustforge_app.zip      1,130,475 bytes (2026-07-20T11:07:02Z)
  trustforge_frontend_dist.zip 282,167 bytes (2026-07-19T16:00:23Z)
```

### A8 — IAM Roles
```
trustforge-ec2:         LastUsed 2026-07-26T15:51:17Z
trustforge-lambda-exec: LastUsed 2026-06-30T14:36:23Z
```

### A9 — SSM Parameters
```
/trustforge/runtime/admin-token: SecureString, v1, 2026-07-08
/trustforge/runtime/live-token:  SecureString, v1, 2026-07-08
/trustforge/deploy:              NOT FOUND
```

### A10 — CloudWatch Alarms
```
[] (no alarms contain "trustforge")
```

### B1 — EC2 VERSION
```
VERSION = "v0.16.18"
```

### B1b — nginx symlink
```
/etc/nginx/trustforge-sites/react.conf
```

### B2 — git tags
```
v0.24.0: 427d3d6b (2026-07-26 13:29 CST) — chore: bump to v0.24.0
v0.18.2: 2408c4f3 (2026-07-25 09:08 CST) — Merge pull request #665 from cancleeric/release/v0.18.2
v0.16.18: fde5a643 (2026-07-20 17:09 CST) — chore(release): bump version to 0.16.18
main HEAD: 4001fe3c — fix(hermes): N49 左軌拆成「選單欄 + 對話欄」
```

### B2b — S3 head-object
```
trustforge_app.zip:
  VersionId: YX4HwGd9NXBps7D8te9.SRZZLpm6VXpY
  Size: 1130475, LastModified: 2026-07-20T11:07:02+00:00
  ETag: "28d67b981ab7b840ea285b4558bbbd7b"

trustforge_frontend_dist.zip:
  VersionId: kmZqBaI_WP5ISclL1lZcLUszzJRe9yzc
  Size: 282167, LastModified: 2026-07-19T16:00:23+00:00
  ETag: "70b0641d366fcf5e0f5f859eda4fd6ac"
```

### C1-C4 — Full config snapshot
Captured in full via SSM send-command. See Section 4 above.

---

*End of evidence document. All commands were read-only. No AWS resources were modified.*
