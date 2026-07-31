# Tasks：Hermes Production Audit

> 全部任務預設未完成。production read 僅在 CEO/CPO/CISO 批准窗口與短效 AWS session 下執行。

## Task 1：資料契約與安全 primitives

- [x] 定義 audit bundle、evidence/status/limit、release comparison、dead-letter summary schema。
- [x] 定義 unit/table/type allowlist、read budgets、deadline、exit codes。
- [x] 實作 canonical JSON、SHA-256、redaction/secret detector 與安全 error formatter。
- [x] 新增 serialization、redaction、未知欄位、no-raw-payload 測試。

**需求**：R1、R3、R5、R6

## Task 2：CLI、preflight 與靜態 SSM collector

- [x] 建立 `scripts/hermes_production_audit.py`，要求 target/output，提供無副作用 `--dry-run`。
- [x] 實作 boto3 STS identity hash、SSM managed-target preflight 與 static-command invocation。
- [x] 只收集 allowlisted systemd/config/release JSON，驗證 schema/大小/redaction。
- [x] 測試無 credential、target mismatch、offline/timeout、command injection、完整 Environment 泄漏。

**需求**：R1、R2、R3、R6

**相依**：Task 1

## Task 3：控制面與 release 對照

- [x] 實作 runtime guard、Dynamo tri-state、unit env、production default 的 evidence/unknown 規則。
- [x] 收集並遮罩 flags、cache/cost/scheduler config、coin pool、budget、VERSION、Python、manifest/unit/config digest。
- [x] 實作 expected/deployed `match`、`mismatch`、`unknown` 與 regression tests。

**需求**：R2、R3

**相依**：Task 1、Task 2

## Task 4：有界 DynamoDB 與 dead-letter readers

- [x] 建立受控 table resolution、metadata/TTL、projection/limit readers 與 `insufficient-evidence` semantics。
- [x] 聚合 cache freshness、scheduler/cost outcome、source/coin error class，並實作 redacted dead-letter adapter。
- [x] 測試 limits/no-mutation、empty≠healthy、throttle、AccessDenied、schema drift、partial output。

**需求**：R4、R5、R6

**相依**：Task 1、Task 3

## Task 5：本機 evidence bundle、runbook 與 release gate

- [x] 實作 ignored local `evidence.json`、`summary.md`、`SHA256SUMS` writer；確認不會 add/commit/upload。
- [x] 寫 audit runbook，說明權限、參數、exit code、evidence 判讀、blocker 與 incident path。
- [ ] 完成目標測試、`.githooks/pre-push`、`/codex-review`、gray 與 harper review；有 UI 才做 eye scan。
      （目標測試 ✅ 31 passed、`.githooks/pre-push` ✅ 已納入全量 batched pytest；`/codex-review`、gray、harper 未執行）

**需求**：R1–R6

**相依**：Task 2–Task 4

## Task 6：受控 production read-only audit

- [ ] 取得 CEO window、CPO/CISO approval 與短效 AWS credential。
- [ ] 第二人先核對 dry-run 的 region、instance、SSM command digest、limits、output path。
- [ ] 執行一次 audit；同一窗口不得切 flag、改 unit、改 DB 或部署。
- [ ] 驗證 bundle digest/redaction/control/release/cache/scheduler/dead-letter 語義，將 blocker 拆成 Phase 1 issue。

**需求**：R1–R6

**相依**：Task 5 + 明確 production approval + 下方阻斷項結清

### ⛔ Task 6 前置阻斷項：table 名稱未依有效設定解析

`DynamoAuditReader` 目前固定讀 `_APPROVED_TABLE_NAMES` 的靜態名稱，但
R4／design 要求讀「**有效設定所指向的**」table（static map **或已驗證有效設定**）。
靜態 SSM 收集器把 `TRUSTFORGE_CACHE_TABLE`、`TRUSTFORGE_SCHEDULER_RUN_TABLE`、
`TRUSTFORGE_COST_LEDGER_TABLE` 壓成 `configured`／`absent`，實際值沒被帶回來，
所以「已驗證有效設定」這條路徑實作不出來。

production 一旦覆寫其中任一變數，稽核就會讀到非作用中的 table，
產出的證據無效。失敗方向是 fail-closed（回 `insufficient-evidence`，不會假綠），
因此不是安全洞，但**足以讓 Task 6 的 bundle 不可採信**。

結清方式：SSM snapshot 帶回實際 table 名稱 → 對 allowlist 嚴格驗證 →
用於 read；bundle 仍只落 table type 與 digest，不得落 raw 名稱
（維持 `test_contract_coverage_is_exact_and_no_raw_table_name_is_persisted`
與 `test_local_table_environment_cannot_redirect_approved_bindings` 兩條不變式）。
此改動會變更 static SSM command digest，需 harper（CISO）專審。

來源：codex 對抗審 2026-07-31。

## 依賴圖

```text
Task 1 → Task 2 → Task 3 → Task 4 → Task 5 → Task 6
```

## 完成定義

Task 1–5 完成只代表工具可供受控操作。Task 6 必須有真實、批准且已遮罩的 production bundle；fixture、mock、localhost、公開 API 都不能取代。任何 `blocked`、`partial`、redaction failure 或 release mismatch 都結論為 `insufficient-evidence`，不得啟用 Hermes/Three-track/AGOS flag。
