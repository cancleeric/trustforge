# TrustForge技術文件v0.18.5

[← 使用者手冊 ](15-user-manual.md)[文件首頁 ](README.md)[Evidence Map ](00-evidence-map.md)

# TrustForge 技術文件 v0.18.5

Competition Submission · 依 HOYA BIT 官方命題文件整理的決賽交付與 Final Report 模板

## 16 — 競賽交付與 Final Report 模板

本頁把「提案簡報」、「Final Report／分析報告」、「Evidence List」、「Execution Log」與「Source / Config」分清楚。Final Report 是主要評分對象；Execution Log 是執行紀錄，不是主報告。

OFFICIAL REQUIREMENT MAPPING

## 先分清楚三種文件

| 文件 | 用途 | 是否主要評分對象 |
| --- | --- | --- |
| 提案簡報 | 說明解題方向、AI 技術應用、數據資料應用與 AWS 架構圖。 | 決賽展示用 |
| Final Report／分析報告 | 現場抽題後，由 TrustForge 產生的市場分析結果。 | **是 ** |
| Execution Log | 時間戳、工具呼叫、資料取得與流程摘要。 | 佐證用，不是主報告 |

**目錄 **

- [官方 4 件提交項目 ](#deliverables)

- [Final Report 必備結構 ](#final-report)

- [Evidence List 欄位 ](#evidence)

- [提案簡報大綱 ](#deck)

- [Kiro 加分誠實說法 ](#kiro)

- [送件前檢查 ](#checklist)

### 1. 官方 4 件提交項目

| # | 交付物 | TrustForge 對應 | 注意事項 |
| --- | --- | --- | --- |
| 1 | Final Report | `report.md `／HTML／Dashboard | 主要評分對象，至少含結論、關鍵依據、信心說明。 |
| 2 | Evidence List | `evidence.json `或表格 | 支撐報告結論，主辦可能抽查。 |
| 3 | Execution Log | `execution_log.jsonl ` | 時戳、工具呼叫、資料取得、流程摘要。 |
| 4 | Source / Config | GitHub repo、README、設定與執行說明 | 不可包含 secrets；資料與權限邊界要清楚。 |

### 2. Final Report 必備結構

現場抽到「幣種 + 題型」後，Final Report 應使用這個結構：

```text
# TrustForge Final Report

## 0. 報告基本資料
Team / Project / Coin / Question Type / Question / Run time / Live Demo URL / Source Commit

## 1. 結論與市場判斷
Market Judgment / Direction / Decision State / TrustScore / Confidence / Information Completeness

## 2. 關鍵依據
Evidence ID / Source Type / Fetched At / Content Reference / Related Claim / How It Supports or Challenges

## 3. 多源整合覆蓋
Price / On-chain / News / Official / Social / Macro / Regulatory coverage

## 4. 推理鏈
Facts → Inferences → Final Judgment

## 5. 正反方證據
Supporting / Contrarian / Low-trust or flagged evidence

## 6. 信心說明
Known limitations / What could flip this judgment / Abstain rule

## 7. Execution Summary
只摘要執行狀態；完整 log 另存 execution_log.jsonl

## 8. 合規與反作弊聲明
不把第三方完整市場判斷當主要結果；不提供投資建議。
```

### 3. Evidence List 每筆必備欄位

| 欄位 | 用途 |
| --- | --- |
| `source ` | 來源名稱或網址。 |
| `fetched_at ` | 取得時間。 |
| `content_reference ` | 引用片段、資料區間、查詢條件、指標數值或摘要。 |
| `related_claim ` | 對應 Final Report 裡的哪個判斷。 |

網頁類來源應補 `source_url `與引用片段；API／CSV／鏈上類應補 endpoint、query 參數、時間範圍、交易對、鏈上地址或檔名。

### 4. 提案簡報大綱

| Slide | 內容 |
| --- | --- |
| 1 | TrustForge：多源加密資訊的信任提煉 Agent。 |
| 2 | 命題理解：不是摘要、不是回測，是多源整合與可回溯推理。 |
| 3 | 痛點：LLM 來源幻覺、加密資訊雜訊、單一分數不可審計。 |
| 4 | 解法：資料來源 → Claim → TrustScore → Evidence → Final Report。 |
| 5 | Trust Layer：來源信譽、交叉佐證、時效、操縱風險、Evidence Binding。 |
| 6 | AWS 架構圖：Frontend / API / Pipeline / Bedrock / Logs / IAM。 |
| 7 | Demo 流程：抽題、輸入、報告、Evidence、Execution Log。 |
| 8 | 交付物對應與反作弊聲明。 |

### 5. Kiro 加分誠實說法

避免主張「整個開發流程都由 Kiro 完成」。依目前證據，應使用這個說法：
TrustForge 部分核心模組採用 AWS Kiro 的 spec-driven workflow，並保留 `.kiro/steering/ `、 `.kiro/specs/ `、 `.kiro/hooks/ `作為開發過程證據。

| Kiro 元件 | 證據用途 |
| --- | --- |
| steering | 專案規範、競賽限制、Trust Layer 約束。 |
| specs | 功能需求、設計、任務拆解。 |
| hooks | lint／test／review gate 輔助。 |

### 6. 送件前檢查

- Final Report 有結論、關鍵依據、信心說明。

- 每個關鍵判斷都有 Evidence ID。

- Evidence List 每筆都有 `source `、 `fetched_at `、 `content_reference `、 `related_claim `。

- Execution Log 是 `execution_log.jsonl `，不是主報告。

- Source / Config 不包含 token、API key、password 或私人金鑰。

- 提案簡報含解題方向、AI 技術應用、數據資料應用、AWS 架構圖。

- Kiro 只主張部分採用，並附證據矩陣與截圖。

- Live Demo URL 現場可開；錄影包含流程與輸出，但不暴露 secrets。

[← 使用者手冊 ](15-user-manual.md)[看 Evidence Map ](00-evidence-map.md)[排錯 FAQ ](14-troubleshooting-faq.md)

TrustForge by HurricaneSoft（颶風軟體）· Competition-ready submission guide
