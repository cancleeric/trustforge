# Spec：Hermes Production Audit

## 概述

建立一個本機、唯讀、預算有界且輸出遮罩的 Phase 0 audit command，收集 Hermes production 的控制面、資料面與發布身分證據。它只提供後續修復的事實基準；不變更任何 production 設定。

來源：[Hermes 生產修復與漸進啟用計劃](../../../docs/plans/PLAN-HERMES-PRODUCTION-REMEDIATION-AND-ENABLEMENT-2026-07-30.md)。

## 需求

### R1：安全 preflight
- CLI 必須明確要求 AWS region、EC2 instance id 與本機 output directory；不得以 IP、DNS 或 tag 推測 production 目標。
- 必須使用 boto3 default credential chain，先驗證 STS caller identity 與 SSM managed/online state。
- 無有效 session、錯誤 target、權限不足或逾時時，必須輸出結構化 `blocked`、非零結束；不得嘗試 SSH、`aws login` 或任何 fallback。

### R2：控制面收集
- 透過版本化、固定 allowlisted 的 SSM command 收集 `hermes-cycle.timer`、`hermes-cycle.service`、`trustforge-analysis-flow.service`、`fetch-scheduler.timer`、`fetch-scheduler.service` 的 enabled/active/result、timer 時間、unit/drop-in digest 與有限 journal 摘要。
- 遠端 command 不得接受使用者拼接 shell、不得 restart/enable/disable service，且不得輸出完整 Environment、unit 原文或 secret。
- 必須將 runtime guard、DynamoDB `hermes_autonomy_enabled`、unit env 與 production default 依實際優先序記為 evidence；缺資料必須是 `unknown`，不能假設 enabled。

### R3：設定與 release identity
- 收集並遮罩 region、table names、coin pool、budget、Hermes/Three-track/AGOS flags、Python、VERSION、manifest/unit/config digest。
- AWS credential、admin/live token、SSM prefix 和任何 secret-like value 只可輸出 presence 或不可逆 hash。
- 必須對照 expected release、deployed version/manifest、unit/config digest，逐項輸出 `match`、`mismatch` 或 `unknown`。

### R4：有界資料面摘要
- 只允許讀取有效設定所指向的 admin-config、scheduler-run、connector-cache、cost-ledger 和 durable dead-letter store；不得接受任意 table/ARN/projection。
- 每種讀取必須有 request、item、byte、time 上限，並記錄 consumed budget 與截斷狀態。
- 僅輸出 metadata、timestamp、freshness、source/coin 成功或 error class、release identity 與計數；不得保存 market body、prompt 或使用者內容。
- 缺表、schema drift、AccessDenied、throttle、空資料均必須標示 `insufficient-evidence`，不可視為健康。

### R5：dead-letter 與 evidence bundle
- 限量輸出 dead-letter 的 job-id hash、coin、stage、attempt、error class、retry/quarantine state、release identity；不得 requeue/delete/update。
- 每次執行在 ignored local output 建立 `evidence.json`、`summary.md` 與 SHA-256 digest；包含 schema version、target、limits、warnings、blockers、evidence refs。
- 不得自動 commit、upload、呼叫 Bedrock、connector、Analysis Flow、backfill、deployment 或 feature-flag mutation。

### R6：安全與成本
- 僅可使用 stdlib 與既有 boto3；SSM、DynamoDB 和本機 I/O 都需 timeout、retry 上限與總 deadline。
- 必須 redact error 內容，並以 unit、mocked boto3 contract、redaction、limit、no-mutation 與 canonical serialization tests 驗證。

## 非範圍

不修復 scheduler/cache/connector/citation/dead-letter；不切換 Hermes、Three-track 或 AGOS flag；不建立 learning dataset、重訓、AGOS rollout；不取代 CEO/CPO/CISO approval 或 release gate。

## 成功指標

- [ ] 無 credential、錯誤 target、缺權限均 fail closed 且無 production side effect。
- [ ] 有效 audit 能區分控制來源、未知狀態、release mismatch 與資料不足。
- [ ] evidence bundle 不含 secret、prompt、完整 environment、原始市場或使用者資料。
- [ ] audit 證據可供 Phase 1 issue 拆分，但絕不直接授權啟用旗標。
