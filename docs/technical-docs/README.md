# TrustForge 技術文件

來源：`TrustForge-devlog/docs/`。本目錄以 **Markdown** 作為主 repo 內的技術文件主格式，方便在 GitHub、code review、diff 與 `trustforge/README.md` 文件導覽中閱讀。

另保留一份 HTML 靜態版於 [`html/`](html/)；需要展示原本 devlog 視覺版或離線開瀏覽器時，請看 [`html/index.html`](html/index.html)。

## Markdown 主要閱讀順序

| 順序 | Markdown 文件 | HTML 版 | 說明 |
|---:|---|---|---|
| 00 | [00-evidence-map.md](00-evidence-map.md) | [html/00-evidence-map.html](html/00-evidence-map.html) | 00 — Evidence Map 真實佐證矩陣 |
| 01 | [01-workshop-overview.md](01-workshop-overview.md) | [html/01-workshop-overview.html](html/01-workshop-overview.html) | 01 — Workshop 等級導覽 |
| 02 | [02-architecture.md](02-architecture.md) | [html/02-architecture.html](html/02-architecture.html) | 02 — 系統架構總覽 |
| 03 | [03-deployment.md](03-deployment.md) | [html/03-deployment.html](html/03-deployment.html) | 03 — 部署指南 |
| 04 | [04-configuration.md](04-configuration.md) | [html/04-configuration.html](html/04-configuration.html) | 04 — 環境變數參考 |
| 05 | [05-api.md](05-api.md) | [html/05-api.html](html/05-api.html) | 05 — API 參考 |
| 06 | [06-data-flow.md](06-data-flow.md) | [html/06-data-flow.html](html/06-data-flow.html) | 06 — 資料流與連接器 |
| 07 | [07-operations.md](07-operations.md) | [html/07-operations.html](html/07-operations.html) | 07 — 運維手冊 |
| 08 | [08-trust-algorithm.md](08-trust-algorithm.md) | [html/08-trust-algorithm.html](html/08-trust-algorithm.html) | 08 — 信任演算法詳解 |
| 09 | [09-frontend.md](09-frontend.md) | [html/09-frontend.html](html/09-frontend.html) | 09 — 前端架構 |
| 10 | [10-security-handover.md](10-security-handover.md) | [html/10-security-handover.html](html/10-security-handover.html) | 10 — 安全與交接邊界 |
| 11 | [11-testing-qa.md](11-testing-qa.md) | [html/11-testing-qa.html](html/11-testing-qa.html) | 11 — 測試、QA 與驗收 |
| 12 | [12-customer-handover.md](12-customer-handover.md) | [html/12-customer-handover.html](html/12-customer-handover.html) | 12 — 客戶交接總表 |
| 13 | [13-hands-on-labs.md](13-hands-on-labs.md) | [html/13-hands-on-labs.html](html/13-hands-on-labs.html) | 13 — Hands-on Labs 實作手冊 |
| 14 | [14-troubleshooting-faq.md](14-troubleshooting-faq.md) | [html/14-troubleshooting-faq.html](html/14-troubleshooting-faq.html) | 14 — Troubleshooting FAQ 與術語表 |
| 15 | [15-user-manual.md](15-user-manual.md) | [html/15-user-manual.html](html/15-user-manual.html) | 15 — 使用者手冊 |
| 16 | [16-competition-submission.md](16-competition-submission.md) | [html/16-competition-submission.html](html/16-competition-submission.html) | TrustForge技術文件v0.18.5 |
| 首頁 | [index.md](index.md) | [html/index.html](html/index.html) | TrustForge技術文件v0.18.5 |

## 快速入口

- Markdown 首頁：[index.md](index.md)
- Evidence Map：[00-evidence-map.md](00-evidence-map.md)（HTML：[html/00-evidence-map.html](html/00-evidence-map.html)）
- 競賽交付：[16-competition-submission.md](16-competition-submission.md)（HTML：[html/16-competition-submission.html](html/16-competition-submission.html)）
- HTML 靜態版入口：[html/index.html](html/index.html)

## HTML 版與舊連結

`html/` 保存原始 HTML 技術文件與舊編號 redirect，避免既有 devlog 連結或展示入口失效。主 repo 內的新增連結請優先指向 Markdown 檔。

### 舊編號 redirect（HTML only）

- [html/00-workshop-overview.html](html/00-workshop-overview.html) — Redirecting to 01 — Workshop 等級導覽
- [html/01-architecture.html](html/01-architecture.html) — Redirecting to 02 — 系統架構總覽
- [html/02-deployment.html](html/02-deployment.html) — Redirecting to 03 — 部署指南
- [html/03-configuration.html](html/03-configuration.html) — Redirecting to 04 — 環境變數參考
- [html/04-api.html](html/04-api.html) — Redirecting to 05 — API 參考
- [html/05-data-flow.html](html/05-data-flow.html) — Redirecting to 06 — 資料流與連接器
- [html/06-operations.html](html/06-operations.html) — Redirecting to 07 — 運維手冊
- [html/07-trust-algorithm.html](html/07-trust-algorithm.html) — Redirecting to 08 — 信任演算法詳解
- [html/08-frontend.html](html/08-frontend.html) — Redirecting to 09 — 前端架構
- [html/09-security-handover.html](html/09-security-handover.html) — Redirecting to 10 — 安全與交接邊界
- [html/10-testing-qa.html](html/10-testing-qa.html) — Redirecting to 11 — 測試、QA 與驗收
- [html/11-customer-handover.html](html/11-customer-handover.html) — Redirecting to 12 — 客戶交接總表
- [html/12-hands-on-labs.html](html/12-hands-on-labs.html) — Redirecting to 13 — Hands-on Labs 實作手冊
- [html/13-troubleshooting-faq.html](html/13-troubleshooting-faq.html) — Redirecting to 14 — Troubleshooting FAQ 與術語表
- [html/14-evidence-map.html](html/14-evidence-map.html) — Redirecting to 00 — Evidence Map 真實佐證矩陣
