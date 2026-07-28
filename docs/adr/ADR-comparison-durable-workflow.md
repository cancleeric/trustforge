# ADR: Durable Comparison Workflow

> 狀態：Proposed
> 日期：2026-07-29
> 作者：CTO（颶風集團技術長）
> 關聯：CA-11（COMPARISON-ANALYSIS-DEVELOPMENT-PLAN-20260728.md）

## 背景

目前 `pipeline.run_comparison()` 是**同步請求**：呼叫方（`_do_comparison` in `web.py`）發出 HTTP 請求後，
原地執行兩輪 `pipeline.run()`（coin_a + coin_b），再透過 `build_comparison_report()` 組出
`ComparisonRunResult` 回傳。整個執行結果**不持久化**——請求結束即蒸發。

這造成以下限制：

1. **無法跨請求累積比較**：用戶每次查詢 BTC vs ETH 都是全新計算，無法對比前次結果
2. **無法排程 / 非同步提交**：大型比較（多面向 + Bedrock synthesis）從提交到結果回傳需數十秒，
   請求必須一直保持連線，易觸發 HTTP timeout
3. **無法重跑與稽核**：沒有 execution history，無法追溯某次比較用了哪些資料來源、
   哪個版本 pipeline、產生了什麼結論
4. **無法作為 CA-09 snapshot synthesis 的上游觸發器**：目前 snapshot 是手動/定時產生，
   無法在 comparison job 完成後自動寫入 snapshot

### 現有架構回顧

```
HTTP request → _do_comparison() → run_comparison() → ComparisonRunResult
                                                           ↓
                                                    to_dict() → JSON response
                                                    (然後消失在記憶體中)
```

`ComparisonRunResult` 已有完整 `to_dict()` / `from_dict()` 序列化路徑，
`ComparisonReport` 也具備 `to_markdown()` / `to_dict()`。這表示**持久化層只需要
決定用什麼儲存後端**，序列化格式本身已就緒。

---

## 方案比較

### 方案 A：SQLite `comparison_jobs` 表（本機）

**做法**：在本機 SQLite（現有 `data/trustforge.db` 或獨立 `data/comparison.db`）新增
`comparison_jobs` 表，每個 comparison job 一列，result 欄位存 JSON blob。

**優點**：
- 零外部依賴，本機開發 / 離線可用
- SQLite 已是 TrustForge 既有基礎設施（見 `TRUSTFORGE-STORAGE-ROADMAP.md`）
- `ComparisonRunResult.to_dict()` JSON 可直接存入 `result_json` TEXT 欄位
- 查詢簡單：`SELECT * FROM comparison_jobs WHERE coin_a=? AND coin_b=? ORDER BY created_at DESC`
- Transactional：同一 request 內可原子寫入 job + 其關聯 snapshot

**缺點**：
- 單機限制：多 worker / Cloud Run 多實例無法共享同一 SQLite
- 無內建 TTL / 自動淘汰：需手動實作 cleanup job
- 大 result_json（含完整 evidence/report 嵌套）可能達數 MB，SQLite TEXT 上限 1 GB 綽綽有餘但查詢掃描成本高
- 生產 Cloud Run 的 `/tmp` 為 ephemeral，需外掛 volume 或 Cloud SQL

**適用場景**：dev / offline / 單機部署

---

### 方案 B：DynamoDB（託管，高可用）

**做法**：用 AWS DynamoDB 表 `trustforge-comparison-jobs`，以 `comparison_id` 為 partition key，
`created_at` 為 sort key。result 欄位存 JSON string。

**優點**：
- 全託管、無伺服器，Cloud Run 多實例自動共享
- 內建 TTL（`expires_at` 欄位）自動淘汰舊 job
- Point-in-time recovery / DynamoDB Streams 可串後續 pipeline（自動觸發 snapshot synthesis）
- 讀寫 latency 穩定（single-digit ms）

**缺點**：
- 需要 AWS 帳號 / IAM role / 網路出站 成本
- 離線 dev 無法使用（需 `--offline` fallback 路徑）
- 每個 item 上限 400 KB：大型 comparison result（含完整 evidence/report JSON）需評估是否超限；
  若超限需拆成多 item 或用 S3 pointer
- 查詢模式受限：需預先設計 GSI（如 `(coin_a, coin_b), status` 複合 key）

**適用場景**：production / multi-worker Cloud Run

---

### 方案 C：檔案系統 result cache（`out/comparisons/{run_id}.json`）

**做法**：每次 `run_comparison()` 結束後，將 `ComparisonRunResult.to_dict()` 寫入
`out/comparisons/{comparison_id}.json`。讀取時用檔案 glob 掃描。

**優點**：
- 實作最簡單：`json.dump()` + `json.load()`
- 易於人工除錯（直接 `cat` JSON）
- 已有先例：`out/snapshots/` 目錄（CA-09 snapshot synthesis）
- 適合少量比較結果

**缺點**：
- 無索引：查「最近 N 次 BTC vs ETH 比較」需掃描整個目錄
- 無並行控制：兩個請求同時寫同一檔案可能競爭
- 無狀態追蹤：無法區分 pending / running / completed / failed
- 本機限定，Cloud Run ephemeral 檔案系統無法跨實例
- 無法做跨 job 的聚合查詢（如「本週所有 comparison 的平均 confidence」）

**適用場景**：快速 prototype / 一次性實驗，不建議作為正式方案

---

## 推薦方案

**Phase 1（dev / offline）**：方案 A（SQLite `comparison_jobs` 表）

**Phase 2（production）**：方案 B（DynamoDB）或 Cloud SQL PostgreSQL（若集團既有基礎設施偏好）

**不採用方案 C**：檔案系統快缺少狀態機、索引與並行控制，視為非正式方案。

### Phase 1 → Phase 2 遷移原則

- 兩階段共用相同的 **repository abstraction**（`ComparisonJobRepository` protocol）：
  - `create_job(coin_a, coin_b, query) → comparison_id`
  - `update_job(comparison_id, status, result_json)`
  - `get_job(comparison_id) → ComparisonJob | None`
  - `list_jobs(coin_a, coin_b, limit, offset) → list[ComparisonJob]`
- `comparison_id` 格式統一：`{coin_a}_{coin_b}_{uuid_short}`（8 字節 hex），
  例如 `BTC_ETH_a1b2c3d4`
- Phase 1 SQLite 實作可直接切換 Phase 2 DynamoDB 實作，不需改呼叫端

### 非同步化時程

CA-11 **僅 ADR 設計，不實作**。實際執行（含 DB migration）標記為 `blocked-external`——需 Eric 授權 DB token 後方可動工。

---

## 核心 Schema Proposal

### `comparison_jobs` 表（SQLite 版）

| 欄位 | 型別 | 說明 |
|------|------|------|
| `comparison_id` | TEXT PK | 唯一識別碼，格式 `{COIN_A}_{COIN_B}_{8HEX}`，例如 `BTC_ETH_a1b2c3d4` |
| `coin_a` | TEXT NOT NULL | A 幣種（大寫，須在 COIN_POOL） |
| `coin_b` | TEXT NOT NULL | B 幣種（大寫，須在 COIN_POOL，且 ≠ coin_a） |
| `query` | TEXT NOT NULL | 使用者查詢問題（max 1000 chars，對齊 `_do_comparison` 驗證） |
| `status` | TEXT NOT NULL | `pending` / `running` / `completed` / `failed` / `timeout` |
| `result_json` | TEXT | `ComparisonRunResult.to_dict()` 的 JSON（僅 completed 時非 NULL） |
| `error_message` | TEXT | 失敗原因（僅 failed / timeout 時寫入） |
| `llm_mode` | TEXT | 執行時的 LLM 模式：`off` / `bedrock` |
| `data_mode` | TEXT | 執行時的資料模式：`sample` / `live` |
| `execution_time_ms` | INTEGER | 執行耗時（毫秒），completed/failed 時寫入 |
| `created_at` | TEXT NOT NULL | ISO 8601 UTC 建立時間 |
| `updated_at` | TEXT NOT NULL | ISO 8601 UTC 最後更新時間 |

### DynamoDB 對應

| SQLite 欄位 | DynamoDB 屬性 | 備註 |
|------------|--------------|------|
| `comparison_id` | Partition Key (S) | 同格式 |
| `created_at` | Sort Key (S) | ISO 8601 |
| `coin_a`, `coin_b` | GSI PK/SK | GSI: `coin_pair` = `{coin_a}#{coin_b}` |
| `status` | Attribute + GSI filter | |
| `result_json` | Attribute (S) | 若超 400KB 則拆 S3 pointer |
| `expires_at` | TTL attribute (N) | DynamoDB 用 Unix epoch，非 SQLite 欄位 |

---

## 狀態機

```
                  ┌─────────┐
                  │ pending │  ← job 建立，尚未開始執行
                  └────┬────┘
                       │ dispatch
                  ┌────▼────┐
                  │ running │  ← pipeline.run_comparison() 執行中
                  └────┬────┘
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
    ┌──────────┐ ┌──────────┐ ┌──────────┐
    │completed │ │  failed  │ │ timeout  │
    └──────────┘ └──────────┘ └──────────┘
    result_json   error_msg    error_msg
    有值          有值          有值
                     
不可逆：一旦進入 completed/failed/timeout，不可回到 pending/running
```

**狀態轉換規則**：
- `pending → running`：dispatcher 取走 job，開始執行 `run_comparison()`
- `running → completed`：pipeline 成功完成，`result_json` 寫入 `ComparisonRunResult.to_dict()`
- `running → failed`：pipeline 拋出非 timeout 例外，`error_message` 寫入例外訊息
- `running → timeout`：執行超過上限（預設 120 秒，可配置），強制標記 timeout
- 終端狀態（completed / failed / timeout）**不可逆**

### 同步模式下的狀態簡化

CA-11 Phase 1（同步執行）中，job 建立後立即在同一個 HTTP request 內執行，
狀態為 `pending → running → completed`，不涉及 background worker。
非同步 dispatcher（background worker pool）留待後續 phase。

---

## 與既有模組的整合點

| 既有模組 | 整合方式 |
|---------|---------|
| `pipeline.run_comparison()` | 執行前 update status → `running`；執行後寫入 `result_json` + status → `completed` |
| `comparison_snapshot.py` | completed job 的 `result_json` 可作為 snapshot 來源（CA-09 目前讀 `out/snapshots/`） |
| `comparison_contract.py` | result_json 內容即 `ComparisonRunResult.to_dict()`，無需轉換 |
| `web.py._do_comparison()` | 呼叫 pipeline 前先 `create_job()`，回傳時附加 `comparison_id` |
| Frontend `ComparePage` | 可透過 `comparison_id` 查詢歷史結果、顯示 execution metadata |

---

## 風險與緩解

| 風險 | 緩解 |
|------|------|
| `result_json` 過大（DynamoDB 400KB 上限） | 先量測典型 BTC/ETH comparison 的 JSON 大小；若超限，用 S3 pointer + presigned URL |
| SQLite concurrent write | Phase 1 單 worker 無競爭；若後續加 background worker，用 WAL mode + retry |
| 無 TTL 導致 SQLite 無限增長 | 加入 `cleanup_old_jobs(days=90)` 維護函式，或手動 prune |
| Cloud Run 多實例無法共享 SQLite | 只在 Phase 1 使用；Phase 2 切 DynamoDB / Cloud SQL |
| job 遺留 `running` 狀態（crash） | 啟動時掃描 `status='running' AND updated_at < NOW - 5min`，標記為 `timeout` |

---

## 非目標（Non-Goals）

- **不做 background worker / message queue**：CA-11 不設計非同步 dispatcher（Celery / SQS / Lambda trigger）
- **不做結果比較與 diff**：不實作「兩次 comparison 結果差異」功能
- **不做 multi-tenant 隔離**：comparison_jobs 為全域表，無 tenant 欄位（TrustForge 目前為單一部署）
- **不做 WebSocket 推送**：不實作 job status 的 real-time push

---

## Migration Proposal

詳細 DDL、索引設計、遷移步驟與 rollback plan 見：
→ [`docs/plans/PLAN-comparison-durable-schema.md`](../plans/PLAN-comparison-durable-schema.md)

**狀態**：`blocked-external` — 需 Eric DB auth token 後方可執行 migration。
