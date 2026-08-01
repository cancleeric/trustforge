# TrustForge技術文件v0.18.5

[← 開發記錄首頁 ](../README.md)[使用者手冊 ](15-user-manual.md)[競賽交付 ](16-competition-submission.md)[Evidence Map ](00-evidence-map.md)[Workshop 導覽 ](01-workshop-overview.md)[Hands-on Labs ](13-hands-on-labs.md)[客戶交接總表 ](12-customer-handover.md)[參考資料 ](../README.md)

# TrustForge 技術文件 v0.18.5

Evidence-first Workshop-grade：比一般 AWS workshop 更重視真實佐證、線上 smoke、交接邊界與成本保護

134 Python modules 223 backend tests 57 frontend tests Live smoke verified Bedrock currently off

Better than generic workshop docs：真實優先

## 不是把文件寫漂亮，而是每句話都能被 repo 或線上結果驗證

本文件包新增 Evidence Map：把 AWS/Bedrock/DynamoDB/API/部署/測試等主張逐一對到檔案或 live smoke。未驗證的項目標成「支援／待驗證」，不冒充已完成。

Claim → Evidence File / Live Smoke → Status Label → Customer Lab → Sign-off

### 先選你的入口

#### 競賽交付入口

- [競賽交付與 Final Report 模板 ](16-competition-submission.md)

- [Hands-on Labs ](13-hands-on-labs.md)

- [客戶交接總表 ](12-customer-handover.md)

#### 一般使用者入口

- [使用者手冊 ](15-user-manual.md)

- [排錯 FAQ ](14-troubleshooting-faq.md)

- [API 參考（需要時） ](05-api.md)

#### 0. 先看真假邊界

- [Evidence Map ](00-evidence-map.md)

- [Workshop 導覽 ](01-workshop-overview.md)

- [架構總覽 ](02-architecture.md)

#### 1. 客戶工程師實作

- [Hands-on Labs ](13-hands-on-labs.md)

- [API 參考 ](05-api.md)

- [前端架構 ](09-frontend.md)

#### 2. DevOps 上線

- [部署指南 ](03-deployment.md)

- [環境變數 ](04-configuration.md)

- [運維手冊 ](07-operations.md)

#### 3. 正式交付

- [安全交接 ](10-security-handover.md)

- [測試驗收 ](11-testing-qa.md)

- [排錯 FAQ ](14-troubleshooting-faq.md)

- [簽收清單 ](12-customer-handover.md#signoff)

### 文件導航

[

### 00 — Evidence Map 真實佐證矩陣

每個技術主張對應 repo 檔案、線上 smoke 或明確邊界；避免文件誇大。

Evidence Must read
](00-evidence-map.md)[

### 15 — 使用者手冊

一般使用者如何查看 Dashboard、提交分析、比較幣種、查歷史、看狀態與回報問題。

使用者 Start here
](15-user-manual.md)[

### 16 — 競賽交付與 Final Report 模板

把提案簡報、Final Report、Evidence List、Execution Log 與 Source/Config 分清楚，對齊 HOYA BIT 決賽交付。

比賽交付 Start here
](16-competition-submission.md)[

### 01 — Workshop 等級導覽

學習目標、先備條件、3.5–4 小時課綱、模組時程、交付輸出與清理成本控管。

Workshop Start here
](01-workshop-overview.md)[

### 02 — 系統架構總覽

三層管線、AWS 拓樸、核心元件與服務狀態，已改為 evidence snapshot。

架構 15 min
](02-architecture.md)[

### 03 — 部署指南

EC2+nginx production、App Runner、Lambda Function URL，含 IAM、TLS、Blue/Green。

部署 20 min
](03-deployment.md)[

### 04 — 環境變數參考

環境變數、預設值、生產建議、安全注意事項、fail-closed 行為。

配置 10 min
](04-configuration.md)[

### 05 — API 參考

JSON REST API、{ok,data,error} 信封、rate limit、OpenAPI 對照。

API 15 min
](05-api.md)[

### 06 — 資料流與連接器

7 大來源到最終報告：Ingestion → Cache → Trust Scoring → Agent Narrative。

資料流 15 min
](06-data-flow.md)[

### 07 — 運維手冊

起停控制、成本控管、監控告警、排程器、緊急回滾、日誌查詢。

運維 15 min
](07-operations.md)[

### 08 — 信任演算法詳解

TrustScore 公式、權重矩陣、Stance、Dawid-Skene EM、Conformal Calibration。

演算法 20 min
](08-trust-algorithm.md)[

### 09 — 前端架構

React 19 SPA、API Client、型別系統、建置流程、CSP 安全策略。

前端 10 min
](09-frontend.md)[

### 10 — 安全與交接邊界

機敏資料、IAM、網路、LLM 安全、token rotation 與客戶接手檢查。

安全 12 min
](10-security-handover.md)[

### 11 — 測試、QA 與驗收

後端測試、前端 build、production smoke、客戶驗收準則與已知邊界。

測試 12 min
](11-testing-qa.md)[

### 12 — 客戶交接總表

交接包內容、角色分工、Day 0 驗收、前 7 天維運節奏、簽收清單。

交接 10 min
](12-customer-handover.md)[

### 13 — Hands-on Labs 實作手冊

Lab 0–4：環境檢查、AWS/SSM、部署、API smoke、成本與回滾。

Lab 60–90 min
](13-hands-on-labs.md)[

### 14 — Troubleshooting FAQ 與術語表

AWS、部署、API、UI、成本、rate limit、回滾的決策樹與術語表。

排錯 15 min
](14-troubleshooting-faq.md)

#### 本輪真實性修正

- 把舊的「已部署」統一改成 evidence status：已線上驗證／程式支援／待客戶設定。

- 明確標示目前 production `bedrock_capable=false `，不能宣稱 live Bedrock 已開。

- 用主 repo `origin/main `實際計數更新專案規模：134 個 `src/trustforge `Python 模組、223 個 `tests/test_*.py `後端測試檔、57 個前端測試檔；完整 git 追蹤檔為 984 個。

- 新增 `00 — Evidence Map `作為客戶交接時的防誇大索引。

TrustForge by HurricaneSoft（颶風軟體）· Evidence-first Workshop-grade 技術文件
文件版本：v0.18.5 · 最後更新：2026-07-26
