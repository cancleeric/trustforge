# 12 — 客戶交接總表

[← 11 測試、QA ](11-testing-qa.md)[文件首頁 ](README.md)[13 Hands-on Labs 實作手冊 → ](13-hands-on-labs.md)

## 12 — 客戶交接總表

Customer Handover · 交接包、角色分工、Day 0 驗收與簽收

**目錄 **

- [交接包內容 ](#package)

- [接手角色分工 ](#roles)

- [Day 0 驗收流程 ](#day0)

- [前 7 天維運節奏 ](#day7)

- [風險與待辦 ](#risks)

- [簽收清單 ](#signoff)

### 1. 交接包內容

| 項目 | 位置 | 用途 |
| --- | --- | --- |
| 技術文件首頁 | `/docs/ ` | 客戶入口、閱讀路線、文件搜尋 |
| 架構／資料流 | `01 `、 `05 ` | 理解 TrustForge 核心設計 |
| 部署／配置／運維 | `02 `、 `03 `、 `06 ` | 重建環境與維持服務 |
| API／前端 | `04 `、 `08 ` | 串接與 UI 接手 |
| 安全／測試 | `09 `、 `10 ` | 正式交付前檢查 |

### 2. 接手角色分工

#### 產品／PM

- 確認功能範圍與驗收口徑

- 確認資料來源與免責聲明

- 決定 production 啟用哪些外部 connector

#### 後端工程師

- 接手 `src/trustforge `pipeline

- 維護 Bedrock、cache、budget guard

- 新增資料來源與信任規則

#### DevOps

- AWS IAM、SSM、DynamoDB、CloudWatch

- EC2/nginx/TLS/systemd

- 部署、回滾、成本監控

#### 前端工程師

- React 19 + Vite 8 SPA

- API client 與錯誤狀態

- 報告可讀性與手機版

### 3. Day 0 驗收流程

- 客戶可讀取私有 repo，並確認 main/develop 分支策略。

- 客戶 AWS 帳號已開 Bedrock model access。

- 以客戶 credential 建立 SSM Parameter、DynamoDB tables、IAM role。

- 部署 EC2/nginx/TLS，確認 80/443 與 health check。

- 跑 backend tests、frontend build、production smoke。

- 確認 Budget Guard cap 與 CloudWatch alarm。

- 用非機敏測試問題完成一次 analyze，確認引用、反方證據與資訊完整度標示。

### 4. 前 7 天維運節奏

| 時間 | 檢查 | 目標 |
| --- | --- | --- |
| 每日 | `/api/status `、 `/api/costs `、CloudWatch alarms | 確認資料新鮮度與成本 |
| 每次部署前 | git diff、backend tests、frontend build | 避免不可回滾變更 |
| 每次部署後 | healthz、API smoke、UI smoke | 確認線上服務正常 |
| 每週 | 檢查 token rotation、成本 cap、DynamoDB table 成長 | 降低安全與成本風險 |

### 5. 風險與待辦

- **成本風險： **Bedrock live analyze 必須搭配 daily cap 與 rate limit。

- **資料新鮮度： **外部 connector 失敗時要顯示 stale/partial，不輸出假完整報告。

- **權限風險： **交接後應輪替 token，並移除開發者個人權限。

- **競賽／展示邊界： **Function URL 403 gated 不是正式公開入口；production 以 EC2+nginx 為準。

### 6. 簽收清單

- 客戶收到 repo access 與部署文件。

- 客戶環境已獨立持有 AWS/GitHub/Domain/TLS 控制權。

- 客戶知道如何停用 Bedrock 或調低 daily cap。

- 客戶知道如何回滾到上一版前端／後端。

- 客戶確認公開文件未含 secret。

- 雙方確認剩餘限制與後續維護窗口。

[文件首頁 ](README.md)[部署指南 ](03-deployment.md)[運維手冊 ](07-operations.md)[測試驗收 ](11-testing-qa.md)

TrustForge by HurricaneSoft（颶風軟體）· 技術文件區 · 客戶交接版
文件版本：v0.18.5 · 最後更新：2026-07-26
