# TrustForge 賽後商用化分析報告（2026-08-04）

## 摘要

TrustForge 賽後不應直接包裝成通用 SaaS 或一般 AI agent 平台。較務實的商用路線，是把 TrustForge 定位為企業 AI 與外部資料決策流程中的「信任校準層」，先以顧問型 POC 與導入案切入，累積案例、資料源契約與可稽核交付，再逐步產品化。

核心訊息：

> TrustForge 不是幫客戶多產生一個答案，而是判斷答案、資料源與模型輸出值不值得信。

## 商用定位

TrustForge 應對外定位為：

> 企業 AI 與外部資料的信任校準層，將不可控的 AI 回答、外部資料源與模型判斷，轉成可評估、可稽核、可追蹤的決策信心。

對外口徑應保持四個重點：

1. 核心可升級。
2. 外掛可替換。
3. 信心校準會進化。
4. 結果可追溯、可稽核、可解釋。

技術名詞如 Dawid-Skene、PIT outcome、semantic direction、ModelHub、outer framework governance 可作為技術背書，但不應放在第一層銷售語言。客戶首先需要理解的是：哪些資料能信、哪些輸出有風險、出事時能否回溯、是否能降低決策風險。

## 建議商用型態

第一階段應避免直接銷售完整 SaaS，改採「顧問＋POC 導入包」。

| 型態 | 內容 | 商業價值 |
|---|---|---|
| 信任診斷包 | 檢查客戶既有 AI／資料決策流程，產出信任風險報告 | 低門檻成交，快速驗證痛點 |
| POC 導入包 | 將一個高風險決策流程接上 TrustForge，產出可運作 demo 與樣本報告 | 建立付費案例與產品需求 |
| 客製 Connector | 針對產業資料源、客戶內部資料或授權 API 開發外掛 | 累積可重用 connector 與資料契約 |
| 稽核 Dashboard | 提供主管可理解的信心分數、來源分歧、風險理由與稽核紀錄 | 從技術 demo 轉成決策產品 |

建議報價級距：

| 方案 | 建議區間 | 邊界 |
|---|---:|---|
| 診斷包 | 新台幣 5～15 萬 | 以報告與訪談為主，不做完整系統整合 |
| POC 包 | 新台幣 20～80 萬 | 限一個流程、一組明確資料源與 demo 報告 |
| 導入包 | 新台幣 100 萬以上 | 含權限、資料源、稽核、客戶環境與維運交接 |

## 優先市場

### 1. 鏈上風控／金融風險情報

這是最貼近現有 TrustForge 的第一個垂直市場。TrustForge 已有 Arkham、Whale Alert 等外部資料源方向，可包裝成「鏈上風險情報信任校準」。

適合客群：

- 加密交易所。
- 鏈上風控公司。
- 金融科技公司。
- 投研團隊。
- 資安與反詐服務商。

可交付輸出：

- token／wallet／transaction／entity 風險摘要。
- 多資料源一致性與分歧判斷。
- 信心分數與拆解理由。
- Evidence lineage。
- 可匯出的稽核報告。

### 2. 企業 AI 治理／AI 稽核

許多企業開始導入 AI，但痛點不是生成能力不足，而是 AI 答錯、資料來源不明、員工過度相信 AI、稽核時無法說明決策依據。

TrustForge 可定位為既有 AI 系統上方或旁路的治理層：

```text
企業資料 / 外部 API / LLM / Agent
        ↓
TrustForge 信任校準層
        ↓
可採信程度 / 風險說明 / 稽核紀錄 / 決策建議
```

### 3. 政府／大型組織資料可信度

適用於多來源資料可信度評估、公共風險、詐騙資訊、災害情報、ESG 或供應鏈資料驗證。這類案源金額可能較高，但銷售週期較長，不建議作為第一個 MVP 主戰場。

## 產品化改進方向

TrustForge 需要從 demo-first 轉為 trust-first。商用化前優先補齊可信任工程，而不是持續加展示功能。

### 產品分層

```text
資料來源 / Connector / Plugin
    ↓
Trust Kernel 信任校準核心
    ↓
Evidence / Claim / Score / Divergence
    ↓
Report API / Dashboard / Audit Log
```

| 層級 | 目標 | 改進重點 |
|---|---|---|
| Trust Kernel | 核心信任評分、分歧判斷、校準邏輯 | 穩定、可測、可解釋、版本化 |
| Connector / Plugin | 外部資料源與產業模組 | 可替換、可授權、可設定、可標示狀態 |
| Application / Dashboard | 給客戶看的畫面與報告 | 清楚、少工程味、主管看得懂 |

### 商用底座能力

| 能力 | 目的 |
|---|---|
| Snapshot isolation | 分析進行中資料變動不污染本次結果 |
| Run lineage | 每份報告知道由哪次 run、哪個 snapshot、哪個 scoring version 產生 |
| Evidence hash | 來源資料可追蹤、可驗證 |
| Retry / DLQ | 失敗任務不消失，可觀測、可重跑 |
| Audit log | 客戶追問判斷理由時可回答 |
| Versioned scoring | 信任分數邏輯改版時可回溯 |
| Credential boundary | demo key、live key、客戶 key、mock data 邊界清楚 |

## Dashboard 改進方向

商用版 Dashboard 應由工程監控畫面轉為決策畫面，建議四區：

| 區塊 | 內容 |
|---|---|
| 左側 | 問題、模式、歷史問答、最近分析 |
| 中間 | 本次完整分析、主要結論、Evidence |
| 右側 | 信心分數拆解、資料源分歧、風險理由 |
| 底部 | 任務進度、queue、stage telemetry、錯誤追蹤 |

注意事項：

- 不要在多處重複顯示同一個分數。
- 分數需有拆解理由，而不是單純漂亮數字。
- 新結果尚未完成前，繼續顯示上一份完整結果。
- 前端不得以 browser navigation 或 submit button 作為分析執行的 owner。

## 資料源策略

第一個商用 MVP 不應追求接很多資料源，而是少量、可靠、契約清楚。

每筆 Evidence 至少需要：

```text
provider
source_url
published_at
retrieved_at
license_or_terms
content_hash
raw_payload_reference
normalization_version
```

資料源狀態需明確標示：

| 狀態 | 意思 |
|---|---|
| ready | 可正常使用 |
| credential-gated | 需要客戶或環境提供 key |
| archive-required | 需要歷史資料授權，不能用 current API 假裝歷史 |
| blocked | 法務、費用或技術原因暫不可用 |

關鍵限制：不得用現在抓到的資料，假裝歷史當天已存在。歷史 replay 需保留 `published_at` 與實際 `retrieved_at`，並要求 provider、URL、terms、hash。

## 建議 MVP

第一個商用 MVP 建議聚焦：

> 鏈上風險情報信任校準。

輸入：

- token。
- wallet。
- transaction。
- entity。
- Arkham / Whale Alert / 公開來源 / 客戶內部清單。

輸出：

- 可信度分數。
- 來源一致與分歧。
- 風險理由。
- Evidence lineage。
- 可匯出的稽核報告。

## 主要風險

| 風險 | 說明 | 緩解方式 |
|---|---|---|
| 定位過大 | 同時想做 AI agent、資料平台、風控、治理，會失焦 | 先鎖鏈上風控 MVP |
| 工程畫面過重 | 客戶主管看不懂，難以轉成預算 | 補商務 demo、sample report、一頁式銷售頁 |
| Evidence 不可回溯 | 信任產品如果自己不可稽核，會失去說服力 | 優先補 snapshot、lineage、hash、audit log |
| 資料授權不清 | 外部 API、歷史資料與客戶資料可能有合規風險 | connector contract 明確記錄 license / terms / state |
| 過度宣稱 | 比賽語言若直接拿去商用，容易變成不實承諾 | 採 evidence-first 與 modest claim |

## 結論

TrustForge 的商用價值不在「比賽作品變 SaaS」，而在成為企業 AI 與外部資料決策流程中的信任校準層。下一步應先把鏈上風控作為第一個垂直 MVP，補齊可稽核工程底座與商務交付文件，透過付費 POC 驗證市場，再將重複部分產品化。
