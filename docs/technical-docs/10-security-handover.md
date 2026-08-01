# 10 — 安全與交接邊界

[← 09 前端架構 ](09-frontend.md)[文件首頁 ](README.md)[11 測試、QA → ](11-testing-qa.md)

## 10 — 安全與交接邊界

Security Handover · secret、IAM、LLM、網路與客戶接手檢查

**目錄 **

- [安全邊界 ](#boundaries)

- [機敏資料處理 ](#secrets)

- [認證與管理面 ](#auth)

- [網路與 AWS 權限 ](#network)

- [LLM 安全與輸出邊界 ](#llm)

- [客戶接手檢查 ](#handover)

### 1. 安全邊界

**交接原則： **公開文件只描述安全設計與操作位置，不放 token、AWS 帳號、私鑰、實際 Parameter 值或內部憑證。

| 邊界 | 設計 | 客戶接手重點 |
| --- | --- | --- |
| Public Web | nginx TLS 終端；React 靜態資源與 /api/* 同域 | 確認 HSTS、CSP、憑證續期 |
| Backend | Python server 僅聽 127.0.0.1:8080，由 nginx proxy | 不直接開 8080 到 Internet |
| AWS Bedrock | 唯一 LLM 入口；IAM 最小權限 | 模型 ID 與 inference profile 需在目標 region 可用 |
| DynamoDB | cache、成本帳本、rate limit、idempotency lease | 確認 table ARN 與 IAM policy scope |

### 2. 機敏資料處理

- `TRUSTFORGE_LIVE_TOKEN `不寫入 repo，不經 deployment command line 傳遞。

- production token 放 SSM Parameter Store；app 啟動時依 `TRUSTFORGE_TOKEN_SSM_PREFIX `讀取。

- GitHub token、AWS access key、Webhook secret 不出現在公開 GitHub Pages、日誌與 HTML。

- 交接時用客戶自己的 AWS/GitHub credential 重新設定，不沿用開發者個人 credential。

### 3. 認證與管理面

| 入口 | 保護方式 | 失敗行為 |
| --- | --- | --- |
| `/api/analyze ` | Live token / rate limit / budget guard | 無 token 或超額回 401/429 |
| `/api/admin/* ` | Admin token + fail-closed | 未設定即關閉管理操作 |
| Lambda Function URL | 403 gated，不作公開入口 | 拒絕匿名流量 |

### 4. 網路與 AWS 權限

- Security Group 公開只需 80/443；SSH 走 SSM Session Manager，不開 22。

- EC2 instance role 限縮 Bedrock InvokeModel、SSM GetParameter、S3 GetObject、必要 DynamoDB 表。

- CloudWatch 只上報指標與服務日誌，不輸出 token。

### 5. LLM 安全與輸出邊界

- 所有 Bedrock 呼叫集中在 `bedrock.py `，方便審計。

- 報告輸出要求引用 claim_id，避免無來源結論。

- 資訊完整度不足時標示為 low/insufficient，不假裝確定。

- Budget Guard 在呼叫前預留成本，避免高併發 TOCTOU 超支。

### 6. 客戶接手檢查

- 客戶 AWS 帳號已建立 Bedrock model access。

- SSM Parameter Store 已寫入 production token。

- Security Group 只公開 80/443。

- DynamoDB 表與 IAM policy 已套用最小權限。

- 日花費上限已符合客戶預算。

- 交接包未包含任何明文 secret。

[部署指南 ](03-deployment.md)[環境變數 ](04-configuration.md)[運維手冊 ](07-operations.md)[交接總表 ](12-customer-handover.md)

TrustForge by HurricaneSoft（颶風軟體）· 技術文件區 · 客戶交接版
文件版本：v0.18.5 · 最後更新：2026-07-26
