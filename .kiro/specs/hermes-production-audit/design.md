# Design：Hermes Production Audit

## Architecture

```text
local operator + short-lived AWS credentials
   │
   ▼
scripts/hermes_production_audit.py
   ├─ STS / SSM preflight
   ├─ Static SSM collector ──────────────> trustforge-demo
   ├─ Bounded DynamoDB summary readers ─> approved tables
   ├─ Control/release reconciliation
   └─ Local evidence writer ─────────────> out/audits/hermes/<audit-id>/
       ├─ evidence.json
       ├─ summary.md
       ├─ ATTESTATION.json
       └─ SHA256SUMS
```

入口以 stdlib + boto3 實作。純 schema、redaction、limit、digest、AWS adapter 可置於 `src/trustforge/`，CLI 放 `scripts/hermes_production_audit.py`。client wrapper 只暴露 read-only API；不提供 mutation method。

## CLI

```text
python scripts/hermes_production_audit.py \
  --region ap-southeast-2 \
  --instance-id i-0152b70368358a81c \
  --output-dir out/audits/hermes \
  [--expected-release <identity>] [--dry-run]
```

region、instance id、output directory 必填。`--dry-run` 只列出 static SSM command digest、AWS read APIs、限額與輸出路徑，不做 remote/table read。所有 timeout、journal window、SSM wait、Dynamo request/item/byte budget 為程式常數，可下調、不可由 CLI 無界擴大。

Exit codes：`0=complete`、`2=blocked`、`3=partial/insufficient-evidence`、`4=integrity/redaction failure`、`5=internal failure`。partial 仍輸出已遮罩 bundle，且明示不能據此批准 rollout。

## Evidence model

```text
AuditBundle
  schema_version, audit_id, captured_at, target, invoker_identity_hash
  limits, overall_status, warnings, blockers
  control_plane: units, effective_control, redacted_config, release_comparison
  data_plane: table_audits, scheduler_summary, cache_summary, cost_summary
  dead_letters: redacted summaries
  evidence_refs, canonical_payload_sha256
```

SSM stdout 與 DynamoDB items 只在記憶體解析為 allowlisted 聚合欄位，從不寫入 bundle。每個 evidence ref 有取得方式、時間、資料縮減規則、截斷旗標和 hash。

## SSM collection

1. 以 STS `GetCallerIdentity` 產生不可逆 invoker hash，並以 `DescribeInstanceInformation` 驗證目標在指定 region、managed 且 online。
2. 以 `SendCommand` + 固定版本化 `AWS-RunShellScript` 收集固定 unit 的 enabled/active/result、timer timestamps、unit/drop-in digest、allowlisted config、VERSION/Python/manifest digest 和有限 journal error count。
3. command 不接受 CLI 插值，不執行 `systemctl cat`、不印完整 `Environment`、不 restart/enable/disable service。
4. 本機 schema 驗證、大小限制、未知欄位拒絕和 secret-like scan 失敗時不寫 raw payload、不 fallback SSH。

## DynamoDB collection and reconciliation

- table name 只可取自 static table-type map 或已驗證有效設定，拒絕任意 table/ARN/projection。
- 先讀 metadata/TTL；再以最小 projection + fixed `Limit` 收集 key、timestamp、status、error class、release ref。無安全 query key 時才使用固定小上限 eventual-consistent scan。
- `admin_config` 只讀 Hermes tri-state/version/timestamp；cache 只做 freshness/key-shape 聚合；scheduler/cost 只做 count/status；dead-letter 用 durable-store adapter 產生 hash summary。
- table/schema/permission/limit 問題為 `insufficient-evidence`，空資料不是健康。
- 控制面按 runtime guard → Dynamo config → unit env → production default 表達。任一較高層未知時，effective state 是 unknown；Three-track/AGOS flag 僅列 evidence。
- 對照 remote VERSION、manifest/release identity、unit/config digest 與 expected release；mismatch 至少降級為 partial，不做修復。

## Output and failure safety

`out/audits/` 必須在實作時確認為 ignored runtime output。canonical JSON 使用排序 key 與 UTC；canonical JSON + SHA-256 提供內容自洽性摘要（操作者自己就能重算，不足以防第三方竄改）——`ATTESTATION.json` 內的 Ed25519 簽章才是防竄改依據，驗證需要對應的 verification key。`complete`/`blocked`/`partial`/`insufficient-evidence`/`integrity-failure` 全狀態一律簽署，不只對「好消息」簽章。summary 只列狀態、hash、摘要與人工下一步。不得 upload、commit、issue comment、CloudWatch write 或 DynamoDB write。

AWS session/SSM timeout 產生 blocked/partial，不嘗試 SSH。redaction/schema 違反是 integrity failure，未遮罩 payload 不落盤。工具沒有 production mutation；停止工具或回到前一 script release 即可回退。本機 evidence 若疑似含敏感資料，走 security incident/retention，不得逕自刪除已被引用的證據。

## Test strategy

- Unit：CLI bounds、static command digest、redaction、canonical JSON/digest、limits、control precedence、exit codes。
- Mocked boto3：STS/SSM preflight、timeout、permission denial、Dynamo projection/limit、throttle、schema drift、empty result。
- Fixtures：safe remote JSON、release mismatch、dead-letter redaction、partial bundle。
- Negative：arbitrary table、command injection、full environment、secret-like output、mutating API 和 SSH fallback 必須被拒絕或不存在。
- Fixture success 不是 production audit；受控真實 audit 仍需 CEO/CPO/CISO window 與 approval。
