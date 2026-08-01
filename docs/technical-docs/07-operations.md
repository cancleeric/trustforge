# 07 — 運維手冊

[← 06 資料流 ](06-data-flow.md)[文件首頁 ](README.md)[08 信任演算法詳解 → ](08-trust-algorithm.md)

## 07 — 運維手冊

Operations Guide · 起停控制、成本控管、監控告警、排程器、緊急回滾

**目錄 **

- [服務起停控制 ](#start-stop)

- [成本控管（Budget Guard） ](#budget-guard)

- [執行期配置管理（Admin API） ](#admin-config)

- [監控與告警 ](#monitoring)

- [排程器（Hermes / Fetch） ](#schedulers)

- [緊急回滾 ](#rollback)

- [日誌查詢 ](#logs)

- [部署更新流程 ](#deploy-update)

- [常見問題排錯 ](#troubleshooting)

### 1. 服務起停控制

#### 1.1 本機開發

`# 啟動（前景） ``python src/trustforge/web.py ````# 啟動（背景 daemon） ``./scripts/trustforge_control.sh start ````# 停止 ``./scripts/trustforge_control.sh stop ````# 檢查狀態 ``./scripts/trustforge_control.sh status `

狀態檔案 `TRUSTFORGE_RUNTIME_STATE_PATH `（預設 `out/trustforge-runtime-control.json `），記錄目前是否 running。

#### 1.2 Production（EC2 systemd）

`# 查看狀態 ``systemctl status trustforge.service ````# 重啟 ``sudo systemctl restart trustforge.service ````# 查看日誌 ``journalctl -u trustforge.service -f ````# 停止（不回自動重啟） ``sudo systemctl stop trustforge.service ````# 停用開機自啟 ``sudo systemctl disable trustforge.service `

### 2. 成本控管（Budget Guard）

**Burn-down safety： **預設每日 Bedrock 花費上限為 $3 USD。超過後所有 Bedrock 請求會被拒絕（回 `429 `），直到次日重置。所有預設都是 fail-closed。

#### 2.1 三層 cap 決定順序

- **DynamoDB Admin Config Store **（執行期動態改，不重啟）： `daily_usd_cap `

- **環境變數 fallback **： `TRUSTFORGE_BEDROCK_DAILY_USD_CAP `

- **程式碼預設常數 **： `DEFAULT_DAILY_USD_CAP = 3 `

#### 2.2 DynamoDB Budget Counter

| Backend | 表名 | 安全 |
| --- | --- | --- |
| `dynamodb ` | `trustforge-budget-guard ` | 多實例安全（原子 increment） |
| `local ` | — | process-local 計數，多實例不安全 |

若 DynamoDB 表不存在或 IAM 無權限，app 會自動 fallback 回 process-local，並送 CloudWatch 指標 `BudgetGuardMultiInstanceProtectionDisabled `標記降級。

#### 2.3 每請求預算預留（TOCTOU-safe）

每次 Bedrock 呼叫前，Budget Guard 先從 counter **原子扣除 **預估 token 費用。若餘額不足，拒絕本次請求（不會開始呼叫後才發現超支）。每請求結束後，依實際費用做 delta 調整（多退少補）。

#### 2.4 成本帳本查詢

`# API 查詢（JSON） ``curl https://trustforge.hurricanesoft.com.tw/api/costs ````# 直接讀帳本檔案 ``cat out/cost_ledger.jsonl | jq . `

帳本格式：每行一個 JSON object，記錄 timestamp、model_id、input_tokens、output_tokens、input_cost、output_cost、run_id。

### 3. 執行期配置管理（Admin API）

| 操作 | API | 說明 |
| --- | --- | --- |
| 查看 config | `GET /api/admin/config ` | 回 `{live_token, bedrock_enabled, daily_usd_cap} ` |
| 更新 config | `PUT /api/admin/config ` | 可動態開關 Bedrock、調整 cap |
| 查變更歷史 | `GET /api/admin/audit ` | 誰在何時改了什麼 config |

**認證： **Admin API 需 `TRUSTFORGE_ADMIN_TOKEN `（或 SSM prefix 下的 `admin-token `參數）。未設 = admin API fail-closed。

### 4. 監控與告警

#### 4.1 健康檢查

`# 快速檢查（nginx 使用的） ``curl https://trustforge.hurricanesoft.com.tw/healthz ``# → ok ````# JSON 健康檢查（零 I/O） ``curl https://trustforge.hurricanesoft.com.tw/api/health ``# → {"ok":true,"data":{"status":"ok","version":"v0.16.18","uptime_seconds":...}} ````# 狀態詳細檢查（含 cache 健康） ``curl https://trustforge.hurricanesoft.com.tw/api/status `

#### 4.2 CloudWatch Metrics

自訂指標由 `cloudwatch_metrics.py `上報。若 `TRUSTFORGE_CW_METRICS=0 `則停用。

| Metric 名稱 | 維度 | 說明 |
| --- | --- | --- |
| `DedupFailures ` | — | dedup 操作連續失敗次數 |
| `BudgetGuardMultiInstanceProtectionDisabled ` | — | Budget Guard DynamoDB 降級為 process-local |
| `RequestCount ` | endpoint | 每端點請求數 |
| `RateLimited ` | endpoint | 被限流的請求數 |

#### 4.3 需建立的 CloudWatch Alarms

| Alarm | 條件 | 緊急程度 |
| --- | --- | --- |
| Dedup Degraded | `dedup.degraded = true `持久 > 5 分鐘 | 中 |
| Dedup Low-Freq Non-Zero | `recent_failures > 0 `持續 > 30 分鐘 | 低（but needs attention） |
| Budget Guard Degraded | `BudgetGuardMultiInstanceProtectionDisabled ` | 高 |
| Daily Cap Exhausted | Budget counter 歸零 | 中（Bedrock disabled） |
| Service Down | `/healthz `連續 3 次失敗 | 高 |

### 5. 排程器（Hermes / Fetch）

#### 5.1 Fetch Scheduler

`scripts/fetch_scheduler.py `定時從真實 API 抓取資料，寫入 cache：

`# 本機手動執行 ``python scripts/fetch_scheduler.py ````# EC2 systemd timer 安裝 ``./deploy/install_fetch_scheduler.sh `

#### 5.2 Hermes Cycle

`scripts/hermes_cycle.py `定時自主分析迴圈（固定幣種池、固定預算、固定工具呼叫數）：

`# 本機手動執行 ``python scripts/hermes_cycle.py ````# EC2 systemd timer 安裝 ``./deploy/install_hermes_scheduler.sh `

**Production guard： **在 production（ `TRUSTFORGE_ENV=production `或 `CACHE_BACKEND=dynamodb `），Hermes cycle 會自動跳過所有排程工作，除非同時設這兩個 opt-in：
1. `TRUSTFORGE_RUNTIME_SWITCH=on `
2. `TRUSTFORGE_ALLOW_PRODUCTION_CONTINUOUS=1 `
這是 fail-closed 設計——需要「刻意」啟用才會有自主排程。

### 6. 緊急回滾

#### 6.1 前端回滾（React → SSR Legacy）

`# 切回舊版 SSR 前後端一體（nginx symlink 原子替換） ``./deploy/cutover_switch.sh legacy ````# 回到 React 前端 ``./deploy/cutover_switch.sh react `

#### 6.2 後端回滾

`# 從 S3 拉取舊版 deploy zip ``aws s3 cp s3://trustforge-deploy-{acct}/trustforge_app_v0.17.0.zip /tmp/ ````# SSM Run Command 部署舊版 ``# 或手動 scp + unzip + systemctl restart `

#### 6.3 緊急關閉 Bedrock（不重啟）

`# 透過 Admin API 關閉 Bedrock（立即生效，不重啟服務） ``curl -X PUT https://trustforge.hurricanesoft.com.tw/api/admin/config \ ``-H "Authorization: Bearer ${ADMIN_TOKEN}" \ ``-H "Content-Type: application/json" \ ``-d '{"bedrock_enabled": false}' `

### 7. 日誌查詢

| 日誌來源 | 查詢方式 | 內容 |
| --- | --- | --- |
| TrustForge (systemd) | `journalctl -u trustforge.service -f ` | Python stdout/stderr（HTTP 請求、錯誤、pipeline 進度） |
| TrustForge (CloudWatch) | AWS Console → CloudWatch → Log Groups | EC2 systemd stdout 同步到 CloudWatch |
| nginx access | `tail -f /var/log/nginx/access.log ` | HTTP 請求記錄（IP、路徑、狀態碼、回應時間） |
| nginx error | `tail -f /var/log/nginx/error.log ` | nginx 錯誤（proxy 失敗、TLS 錯誤） |
| Cost Ledger | `cat out/cost_ledger.jsonl ` | 每次 Bedrock 呼叫的 token 消耗與費用 |
| Execution Log | `out/execution_log.jsonl `或 `/api/analysis-journey ` | 每輪分析的 5 階段執行時序 |
| Scheduler Log | `out/scheduler_log.jsonl ` | 排程器執行記錄 |

### 8. 部署更新流程

```text

EC2 更新完整流程（依主 repo docs/governance/PRE_PUSH_RELEASE_GATES.md）：

1. 啟用並跑本機 pre-push gate（GitHub Actions 不是目前 release/deploy gate）：

   git config core.hooksPath .githooks
   .githooks/pre-push

2. pre-push gate 必須涵蓋：
   - backend tests：env PYTHONPATH=src python -m pytest -q
   - data contracts：python scripts/check_data_contracts.py
   - source stub scan：python scripts/scan_source_stubs.py --out out/pre-push/stub-scan.json
   - competition QA：TRUSTFORGE_BEDROCK_DAILY_USD_CAP=0 python scripts/run_question_bank.py --limit 24
   - frontend tests/lint/build：npm test、npm run lint、npm run build
   - git diff --check

3. git push（GitHub + Gitea 雙遠端），PR/交接紀錄需附 head SHA、timestamp、gate result。

4. 執行 controlled local release/deploy 腳本：

   **# 後端更新**
   REGION=ap-southeast-2 ./deploy/deploy_ec2.sh

   **# 前端更新（若 React 有變更）**
   cd frontend && npm run build
   cd .. && ./deploy/deploy_frontend_nginx.sh

5. 驗證：
   curl https://trustforge.hurricanesoft.com.tw/healthz
   curl https://trustforge.hurricanesoft.com.tw/api/health
   curl https://trustforge.hurricanesoft.com.tw/api/status
   curl https://trustforge.hurricanesoft.com.tw/llms.txt
   curl https://trustforge.hurricanesoft.com.tw/api/openapi.yaml

```

### 9. 常見問題排錯

| 問題 | 症狀 | 診斷步驟 | 解決方案 |
| --- | --- | --- | --- |
| 服務無回應 | `curl /healthz `無回應或 timeout | `systemctl status trustforge ` `ss -tlnp | grep 8080 ` | `systemctl restart trustforge ` |
| nginx 502 | 前端頁面顯示 502 | `curl 127.0.0.1:8080/healthz ` | Python service 未監聽 → restart |
| Bedrock 不回應 | 分析永遠回「資料不足」 | 檢查 `BEDROCK_MODEL_ID `是否有設 檢查 `/api/status `的 `bedrock_capable ` | 確認 env 有設 model ID 確認 IAM role 有 `bedrock:InvokeModel ` |
| Cache 為空 | 所有來源 Document 都是空的 | `/api/status `freshness matrix 全 stale | 執行 `fetch_scheduler.py `手動填充 檢查 DynamoDB table 是否存在 |
| Cost ledger 暴增 | 每日費用超過 cap | `/api/costs `查看各模型用量 檢查 `out/cost_ledger.jsonl ` | 調高 cap（Admin API） 關掉排程器 降低 FAQ 輪詢頻率 |
| Dedup degraded | `/api/status `dedup.degraded = true | 檢查 DynamoDB lease table 是否存在 檢查 IAM 權限 | 修復 DynamoDB access 觀察 recent_failures 是否歸零 |

[← 06 資料流 ](06-data-flow.md)[07 信任演算法 → ](08-trust-algorithm.md)
TrustForge 技術文件 · 07 運維手冊 · v0.18.5
