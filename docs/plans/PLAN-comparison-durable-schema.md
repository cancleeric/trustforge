# PLAN: Comparison Durable Schema Migration

> 狀態：Draft / blocked-external
> 日期：2026-07-29
> 作者：CTO（颶風集團技術長）
> 關聯：ADR-comparison-durable-workflow.md、CA-11
> 決策人：Eric Wang

---

## Schema DDL

### SQLite `comparison_jobs` 表

```sql
-- ============================================================
-- comparison_jobs: 持久化比較分析任務
-- ============================================================

CREATE TABLE IF NOT EXISTS comparison_jobs (
    comparison_id   TEXT PRIMARY KEY NOT NULL,
    -- comparison_id 格式: {COIN_A}_{COIN_B}_{8HEX}
    -- 例: BTC_ETH_a1b2c3d4
    -- 8HEX 用 CSPRNG 生成（secrets.token_hex(4)）

    coin_a          TEXT NOT NULL,
    coin_b          TEXT NOT NULL,
    query           TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    -- 狀態機: pending → running → completed | failed | timeout
    -- 非法值必須由 application layer 拒絕

    result_json     TEXT,
    -- ComparisonRunResult.to_dict() 的 JSON 序列化結果
    -- 僅 status = 'completed' 時非 NULL

    error_message   TEXT,
    -- status = 'failed' 或 'timeout' 時寫入錯誤原因

    llm_mode        TEXT,
    -- 'off' | 'bedrock'

    data_mode       TEXT,
    -- 'sample' | 'live'

    execution_time_ms  INTEGER,
    -- 從 status='running' 到最終狀態的耗時（毫秒）

    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),

    -- 條件約束
    CONSTRAINT chk_coin_not_equal
        CHECK (coin_a != coin_b),
    CONSTRAINT chk_status_valid
        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'timeout')),
    CONSTRAINT chk_result_json_not_null_when_completed
        CHECK (status != 'completed' OR result_json IS NOT NULL)
);
```

### SQLite 條件約束說明

| 約束名 | 目的 |
|--------|------|
| `chk_coin_not_equal` | 防止 coin_a == coin_b（pipeline 層已檢查，DB 加雙保險） |
| `chk_status_valid` | 禁止非法狀態值進入資料庫 |
| `chk_result_json_not_null_when_completed` | completed 必須有 result，failed/timeout 則不強制 |

---

## 索引設計

```sql
-- 索引 1：依幣種對 + 狀態查詢（最常用）
-- 用例：列出 BTC vs ETH 的所有歷史比較，或只看 completed 的
CREATE INDEX IF NOT EXISTS idx_comparison_jobs_coins_status
    ON comparison_jobs (coin_a, coin_b, status);

-- 索引 2：依建立時間降冪掃描（timeline / 最新 N 筆）
-- 用例：dashboard 顯示最近 10 筆 comparison
CREATE INDEX IF NOT EXISTS idx_comparison_jobs_created_desc
    ON comparison_jobs (created_at DESC);

-- 索引 3：狀態 + 更新時間（stale running job 救援掃描）
-- 用例：啟動時掃描 crash 遺留的 running job
CREATE INDEX IF NOT EXISTS idx_comparison_jobs_status_updated
    ON comparison_jobs (status, updated_at)
    WHERE status = 'running';
```

### 索引使用場景對照

| 用例 | SQL 模式 | 使用索引 |
|------|---------|---------|
| 查詢 BTC vs ETH 的最近 completed | `WHERE coin_a='BTC' AND coin_b='ETH' AND status='completed' ORDER BY created_at DESC` | `idx_comparison_jobs_coins_status` + 覆蓋掃描 |
| Dashboard 最新 10 筆 | `ORDER BY created_at DESC LIMIT 10` | `idx_comparison_jobs_created_desc` |
| 啟動時 rescue stale running | `WHERE status='running' AND updated_at < threshold` | `idx_comparison_jobs_status_updated` (partial) |

---

## 遷移步驟

### 前置條件

- ⛔ **DB token**：需 `touch /tmp/eric-auth-$(date +%Y%m%d)-trustforge-comparison-schema.token`
- **備份**：遷移前先 `cp data/trustforge.db data/trustforge.db.bak.$(date +%s)`
- **停機**：DDL 執行期間不需要停機（`CREATE TABLE IF NOT EXISTS` 不影響既有表；
  但若是共享 SQLite，寫入時會短暫持有 lock，單 worker 情況下無感）

### 執行順序

```bash
# Step 1: 備份現有 DB
cp data/trustforge.db data/trustforge.db.bak.$(date +%s)

# Step 2: 執行 DDL（連到現有 trustforge.db）
# 注意：以下 SQL 需在 application 啟動前或 warm-up 時執行
#       若用 SQLAlchemy/Alembic，放在 migration script；此處僅記錄 DDL 規格

sqlite3 data/trustforge.db <<'EOF'
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS comparison_jobs (
    comparison_id      TEXT PRIMARY KEY NOT NULL,
    coin_a             TEXT NOT NULL,
    coin_b             TEXT NOT NULL,
    query              TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'pending',
    result_json        TEXT,
    error_message      TEXT,
    llm_mode           TEXT,
    data_mode          TEXT,
    execution_time_ms  INTEGER,
    created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    CONSTRAINT chk_coin_not_equal
        CHECK (coin_a != coin_b),
    CONSTRAINT chk_status_valid
        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'timeout')),
    CONSTRAINT chk_result_json_not_null_when_completed
        CHECK (status != 'completed' OR result_json IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_comparison_jobs_coins_status
    ON comparison_jobs (coin_a, coin_b, status);

CREATE INDEX IF NOT EXISTS idx_comparison_jobs_created_desc
    ON comparison_jobs (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_comparison_jobs_status_updated
    ON comparison_jobs (status, updated_at)
    WHERE status = 'running';
EOF

# Step 3: 驗證表結構
sqlite3 data/trustforge.db ".schema comparison_jobs"
sqlite3 data/trustforge.db ".indexes comparison_jobs"

# Step 4: 驗證條件約束生效（insert 非法值應回絕）
# 預期：coin_a == coin_b → CHECK constraint failed
sqlite3 data/trustforge.db \
  "INSERT INTO comparison_jobs (comparison_id, coin_a, coin_b, query, status) \
   VALUES ('TEST','BTC','BTC','x','pending');"
# ^ 應回傳 Error: CHECK constraint failed: chk_coin_not_equal

# 預期：非法 status → CHECK constraint failed
sqlite3 data/trustforge.db \
  "INSERT INTO comparison_jobs (comparison_id, coin_a, coin_b, query, status) \
   VALUES ('TEST2','BTC','ETH','x','unknown');"
# ^ 應回傳 Error: CHECK constraint failed: chk_status_valid
```

---

## Rollback Plan

```sql
-- 若需 rollback（例如 application 發現 schema 設計缺陷）
-- DROP TABLE 為破壞性操作，會刪除所有已建立的 comparison job

BEGIN;

DROP INDEX IF EXISTS idx_comparison_jobs_coins_status;
DROP INDEX IF EXISTS idx_comparison_jobs_created_desc;
DROP INDEX IF EXISTS idx_comparison_jobs_status_updated;
DROP TABLE IF EXISTS comparison_jobs;

COMMIT;
```

### 安全 rollback 原則

1. **先備份再 rollback**：`cp data/trustforge.db data/trustforge.db.rollback.$(date +%s)`
2. **若表已經有 production data**，rollback 前用 `SELECT INTO` 或 `sqlite3 .dump` 匯出
3. **rollback 時機**：僅在 discovery phase（dev / staging）允許；production 已經寫入 job 後，
   應改用 `ALTER TABLE` 修正，而非 `DROP TABLE`
4. **與 application 版本耦合**：`comparison_jobs` 表需在 application code（repository layer）
   上線**之前**先建好；若 application 已上線並寫入資料，rollback 表 = 遺失所有 comparison history

---

## DynamoDB 對應規格（Production Phase 2 參考）

本 plan 專注 Phase 1 SQLite；DynamoDB 規格僅供參考，不在本次遷移範圍內。

```
Table: trustforge-comparison-jobs
  PK: comparison_id (S)
  SK: created_at (S)

GSI-1: coin-pair-index
  PK: coin_pair (S)  -- 格式 "BTC#ETH"
  SK: created_at (S)

GSI-2: status-index
  PK: status (S)
  SK: updated_at (S)

TTL: expires_at (N)  -- Unix epoch，90 天後自動刪除

Capacity: on-demand（流量不可預測）
```

---

## blocked-external

```
⛔ blocked-external: Eric DB auth required

原因：
- 本 plan 涉及 SQLite schema 異動（CREATE TABLE + 3 indexes）
- 屬於 DB schema / migration 異動，依鐵律第 1 條需老闆授權
- 本次僅產出 DDL 規格文件，不建立 migration 目錄、不執行 SQL

解除條件：
- Eric 建立 token：/tmp/eric-auth-$(date +%Y%m%d)-trustforge-comparison-schema.token
- 授權後方可執行「遷移步驟」章節中的 DDL

後續待辦（token 解鎖後）：
1. 依本 plan DDL 執行 migration
2. 實作 `ComparisonJobRepository` protocol（SQLite 版）
3. 在 `web.py._do_comparison()` 中接入 `create_job` / `update_job`
4. 補 regression test：非法 status 被拒、coin_a == coin_b 被拒
```
