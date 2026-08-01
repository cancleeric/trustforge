# 01 — Workshop 等級導覽

[← 00 Evidence Map 真實佐證矩陣 ](00-evidence-map.md)[文件首頁 ](README.md)[02 系統架構總覽 → ](02-architecture.md)

## 01 — Workshop 等級導覽

Workshop Overview · 學習目標、先備條件、課綱與交付成果

AWS Workshop-grade Delivery

## 從「文件目錄」提升為「可帶客戶上手的 Workshop」

本頁定義 TrustForge 客戶交接 workshop 的學習目標、先備條件、時間配置、實作模組與驗收輸出。客戶照順序走完，應能理解架構、部署環境、跑 smoke、知道如何維運與回滾。

建議時長：3.5–4 小時 對象：客戶工程師／DevOps／PM 形式：講解 + Hands-on Lab 成本保護：Bedrock daily cap 必設

**目錄 **

- [學習目標 ](#goals)

- [先備條件 ](#prereq)

- [Workshop 時程 ](#schedule)

- [模組設計 ](#modules)

- [交付輸出 ](#outputs)

- [清理與成本控管 ](#cleanup)

### 1. 學習目標

- 能用一句話說明 TrustForge 的核心價值：多源資料 → 信任評分 → 溯源生成 → 成本與風險保護。

- 能看懂 AWS EC2 + nginx + Bedrock + DynamoDB 的 production topology。

- 能在客戶 AWS 帳號設定 token、DynamoDB、IAM role、Budget Guard。

- 能執行 production smoke，判斷服務健康、資料新鮮度與成本狀態。

- 能在出問題時依 runbook 回滾、停用 Bedrock、查日誌。

### 2. 先備條件

| 項目 | 需求 | 驗證方式 |
| --- | --- | --- |
| AWS 帳號 Required | 可建立 EC2、IAM、SSM、DynamoDB、CloudWatch，且已申請 Bedrock model access | Console 可看到 Anthropic model 可用 |
| GitHub 權限 Required | 可讀 private repo `cancleeric/trustforge ` | `git ls-remote `成功 |
| Domain / DNS Optional | 正式域名與 TLS 控制權 | 能新增 DNS record |
| 本機工具 | git、Python 3.11+、Node.js、AWS CLI、curl | `--version `檢查 |

### 3. Workshop 時程

| 時間 | 模組 | 成果 |
| --- | --- | --- |
| 00:00–00:20 | 系統導覽 | 理解架構、責任邊界與成本保護 |
| 00:20–01:05 | Lab 1：環境與 IAM | AWS/SSM/DynamoDB/Bedrock 權限就緒 |
| 01:05–02:00 | Lab 2：部署與 TLS | EC2 + nginx + backend health check |
| 02:00–02:45 | Lab 3：API 與前端 smoke | UI/API 可用，錯誤狀態可解讀 |
| 02:45–03:25 | Lab 4：運維、成本、回滾 | 能查 log、調 cap、回滾與停機 |
| 03:25–04:00 | 驗收與簽收 | 完成 Day 0 checklist |

### 4. 模組設計

#### Module 1 — Architecture Walkthrough

理解資料流、信任層與 AWS 邊界。

閱讀：01、05、07

#### Module 2 — Deploy on AWS

建立 production-like EC2/nginx/Bedrock 環境。

閱讀：02、03、09

#### Module 3 — API / Frontend Integration

確認 API envelope、UI 狀態與錯誤處理。

閱讀：04、08、10

#### Module 4 — Operations & Handover

掌握成本、監控、回滾與客戶簽收。

閱讀：06、10、11

### 5. 交付輸出

- 客戶環境 smoke report：health、status、costs、UI screenshot。

- IAM / SSM / DynamoDB 設定截圖或匯出摘要。

- Budget Guard daily cap 與 CloudWatch alarm 設定。

- Day 0 驗收清單與未完成風險清單。

### 6. 清理與成本控管

**Workshop 結束前必做： **若不是正式 production，關閉 EC2、確認 Bedrock daily cap、刪除臨時 token、清掉 demo 資料。不要把 workshop credential 留在開發者環境。

[進入 Hands-on Labs ](13-hands-on-labs.md)[排錯手冊 ](14-troubleshooting-faq.md)[客戶交接總表 ](12-customer-handover.md)

TrustForge by HurricaneSoft（颶風軟體）· AWS Workshop-grade 技術文件
文件版本：v0.18.5 · 最後更新：2026-07-26
