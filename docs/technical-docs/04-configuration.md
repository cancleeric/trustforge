# 04 — 環境變數參考

[← 03 部署指南 ](03-deployment.md)[文件首頁 ](README.md)[05 API 參考 → ](05-api.md)

## 04 — 環境變數參考

Configuration Reference · 全部 25+ 環境變數、預設值、生產建議值、fail-closed 行為

**目錄 **

- [設定原則 ](#principles)

- [Bedrock / LLM ](#bedrock)

- [HTTP Server ](#server)

- [Cache / 儲存後端 ](#cache)

- [成本控管 ](#cost)

- [安全 / Token / 管理 ](#security)

- [Hermes 自主排程 ](#hermes)

- [前端建置 ](#frontend)

- [設定優先級（Config Store → Env） ](#priority)

### 1. 設定原則

- **Fail-closed **：未設定 = 功能關閉。不存在「漏設就意外開啟」的情境。

- **機敏值不入版控 **：token 類變數一律經 SSM Parameter Store 或手動設 env，不寫入部署腳本、不存 GitHub。

- **三層 cap 順序 **：DynamoDB Admin Config Store → 環境變數 fallback → 程式碼預設常數。

- **Production guards **： `TRUSTFORGE_ENV=production `時，Hermes 連續排程 fail-closed，除非同時設兩個 opt-in 旗標。

### 2. Bedrock / LLM

| 變數 | 預設值 | 必要 | 說明 |
| --- | --- | --- | --- |
| BEDROCK_MODEL_ID | `"" ` | — | AWS Bedrock 模型 ID。設為空 = 離線模式（不呼叫 Bedrock，不燒 credit）。 範例： `au.anthropic.claude-sonnet-4-6 ` |
| AWS_REGION | `ap-southeast-2 ` | — | AWS 區域。Bedrock 呼叫、DynamoDB 連線、SSM 讀取共用。 |
| BEDROCK_HAIKU_MODEL_ID | `au.anthropic.claude-haiku-4-5-20251001-v1:0 ` | — | Haiku 模型 ID，用於 Stance 分類（比 Sonnet 便宜）。 |
| BEDROCK_MAX_TOKENS | `1024 ` | — | Bedrock 敘事生成最大 token 數。 |
| TRUSTFORGE_ONLINE_STANCE | 未設 | — | 即使在 `real-off `模式也啟用線上 stance 分類。未設 = stance 離線。 |

### 3. HTTP Server

| 變數 | 預設值 | 必要 | 說明 |
| --- | --- | --- | --- |
| PORT | `8080 ` | — | HTTP server 監聽埠。App Runner 預設 PORT=8080。 |
| TRUSTFORGE_CSP_MODE | `legacy ` | — | CSP 模式： `legacy `（零 JS SSR）／ `react `（SPA）。 |
| TRUSTFORGE_TRUST_PROXY | 未設 | — | 信任 X-Real-IP / X-Forwarded-For headers（nginx 反向代理時需設）。 |
| TRUSTFORGE_CORS_ALLOW_ORIGINS | `"" ` | — | CORS 允許來源（逗號分隔）。空白 = same-origin only。 |
| TRUSTFORGE_WEB_MAX_ACTIVE_REQUESTS | `32 ` | — | 請求並行上限。超過回 `503 `。 |
| TRUSTFORGE_SWAGGER | `0 ` | — | 設 `1 `啟用 Swagger UI @ `/docs `。 |
| TRUSTFORGE_ENV | 未設 | — | `prod `或 `production `觸發 production guard。 |

### 4. Cache / 儲存後端

| 變數 | 預設值 | 必要 | 說明 |
| --- | --- | --- | --- |
| CACHE_BACKEND | `dynamodb ` | — | Connector cache 後端： `dynamodb `/ `json `/ `sqlite `。 測試預設強制 `json `（conftest.py）。 |
| TRUSTFORGE_SQLITE_PATH | `out/trustforge.sqlite3 ` | — | SQLite 資料庫路徑（shared cache 模式）。 |
| TRUSTFORGE_HOME | src/ 父目錄 | — | 根目錄覆寫。 |
| TRUSTFORGE_CACHE_DIR | 自動 | — | JSON cache 目錄（CACHE_BACKEND=json 時使用）。 |
| TRUSTFORGE_COST_LEDGER_PATH | `out/cost_ledger.jsonl ` | — | 成本帳本檔案路徑。 |
| TRUSTFORGE_SOURCE_ARCHIVE_PATH | SQLite out/ | — | 來源事件歸檔位置（SQLite）。 |
| TRUSTFORGE_RUNTIME_STATE_PATH | `out/trustforge-runtime-control.json ` | — | Runtime control 狀態檔案路徑。 |

### 5. 成本控管

| 變數 | 預設值 | 必要 | 說明 |
| --- | --- | --- | --- |
| TRUSTFORGE_BEDROCK_DAILY_USD_CAP | `3 ` | — | 每日 Bedrock 花費上限（USD）。DynamoDB config store 有值時優先。 設 `0 `= 完全禁用 Bedrock。 |
| COST_BUDGET_USD | 未設 | — | Budget 告警門檻，供 `/api/costs `前端用量條顯示。 |
| TRUSTFORGE_BUDGET_GUARD_BACKEND | `dynamodb ` | — | Budget counter 後端： `dynamodb `/ `local `。 local = process-local 計數，多實例不安全。 |
| TRUSTFORGE_BUDGET_COUNTER_TABLE | `trustforge-budget-guard ` | — | DynamoDB budget counter 表名。 |

### 6. 安全 / Token / 管理

| 變數 | 預設值 | 必要 | 說明 |
| --- | --- | --- | --- |
| TRUSTFORGE_LIVE_TOKEN | `"" ` | ★ | Live Bedrock 模式的 token（前端 `X-Live-Token `header 需匹配）。空白 = 禁用 live 模式。 |
| TRUSTFORGE_ADMIN_TOKEN | 未設 | — | Admin API token。未設 = admin API fail-closed。 |
| TRUSTFORGE_TOKEN_SSM_PREFIX | 未設 | — | SSM 參數名前綴（非機敏）。設如 `/trustforge/runtime `，app 啟動期自行從 SSM 讀取 token 值。 |
| TRUSTFORGE_IDEMPOTENCY_LEASE_BACKEND | `dynamodb ` | — | Analyze 去重後端： `dynamodb `/ `json `。 |
| TRUSTFORGE_LEASE_TABLE | `trustforge-analyze-leases ` | — | DynamoDB lease 表名。 |
| TRUSTFORGE_CW_METRICS | `1 ` | — | 設 `1 `啟用 CloudWatch 指標上報。設 `0 `關閉。 |

### 7. Hermes 自主排程

| 變數 | 預設值 | 必要 | 說明 |
| --- | --- | --- | --- |
| TRUSTFORGE_RUNTIME_SWITCH | 未設 | — | Runtime state 覆寫： `on `/ `off `。local dev 預設 on；production 預設 off。 |
| TRUSTFORGE_ALLOW_PRODUCTION_CONTINUOUS | 未設 | ★ | Production 環境啟用 Hermes 連續排程的 opt-in 旗標。 在 production 中，即使 `TRUSTFORGE_RUNTIME_SWITCH=on `，沒設此旗標也 fail-closed。 |
| TRUSTFORGE_AGENTCORE | 未設 | — | 啟用 AgentCore/Strands LLM 橋接器（實驗性）。 |

### 8. 前端建置

| 變數 | 預設值 | 必要 | 說明 |
| --- | --- | --- | --- |
| VITE_API_PROXY_TARGET | `http://13.211.110.218 ` | — | 前端 dev server（ `npm run dev `）的 API proxy 目標。 本機開發設 `http://127.0.0.1:8080 `。 |

### 9. 設定優先級（Config Store → Env）

部分設定存在 DynamoDB Admin Config Store（ `admin_config.py `）中，可在執行期透過 Admin API 動態變更，不必重啟服務：

| Config Key | 環境變數 fallback | 說明 |
| --- | --- | --- |
| `live_token ` | `TRUSTFORGE_LIVE_TOKEN ` | DynamoDB 有值 → 用 DynamoDB；否則用 env |
| `bedrock_enabled ` | — | 布林值。DynamoDB 設定優先 |
| `daily_usd_cap ` | `TRUSTFORGE_BEDROCK_DAILY_USD_CAP ` | DynamoDB 有值 → 用 DynamoDB；否則用 env 預設常數 |

**安全提醒： **`TRUSTFORGE_LIVE_TOKEN `和 `TRUSTFORGE_ADMIN_TOKEN `為機敏值。來源碼一律不存真實 token 值（即使測試也不存）。deploy_ec2.sh 不含 token 傳遞——改由 app 啟動期從 SSM Parameter Store 讀取。

[← 03 部署指南 ](03-deployment.md)[05 API 參考 → ](05-api.md)
TrustForge 技術文件 · 04 環境變數參考 · v0.18.5
