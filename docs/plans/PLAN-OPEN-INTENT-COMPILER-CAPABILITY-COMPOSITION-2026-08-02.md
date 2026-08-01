# Open-intent compiler、能力組合與對題覆蓋閘開發計畫

日期：2026-08-02  
基線：`origin/develop` 6802b8ff  
關聯：#965、#966、#948、#953

## 1. 問題與實跑證據

題目：

> 請分析 BTC：比對新聞與社群情緒是否一致，並指出來源時效與可能的操弄風險。

目前會被送入 `multi_source` 通用流程。2026-08-02 以原題、BTC、sample data、
LLM off 實跑，得到一般性的「BTC 偏空」市場判斷；`cross_source_signal=null`，
操弄洞察指向 `ohlcv-csv` 單源爆量，沒有逐項回答新聞情緒、社群情緒、兩者是否
一致、兩側資料時效與社群操弄風險。

根因不是單一 renderer 漏欄位：

1. `QuestionType`/官方三個範例被當成過粗的執行選擇，無法表達組合型意圖。
2. 現有跨源訊號比較「客觀資料 vs news+social 合併情緒」，不是 `news vs social`。
3. 原始 query 沒有編譯成 typed operations/deliverables，固定 pipeline 不知道哪些答案必交。
4. 報告沒有 answer-coverage contract；未回答原題仍可顯示完成。
5. 操弄與時效能力讀得到部分底層資料，卻沒有依 query 指定的 source scope 組合。

## 2. 決策

官方題型是相容 fixture／可選提示，不是輸入 whitelist，也不是開放題目的能力上限。
系統先把自然語言編譯成結構化 `AnalysisIntent`，再由 capability registry 驗證並組合
deterministic analyzers；LLM 只負責語意解析與最終行文，不擁有分數、證據、執行權限
或「已完成」判定。

```text
raw question
  -> Intent Compiler (LLM + deterministic fallback)
  -> schema / safety / capability validation
  -> dependency-aware operation plan
  -> deterministic analyzers
  -> structured deliverables
  -> Answer Coverage Gate
  -> evidence-bound narrative
```

## 3. 核心契約

### 3.1 AnalysisIntent

最小欄位：

- `assets`: 一至多個 canonical asset symbols。
- `operations`: ordered typed operations；第一階段支援 `sentiment_analysis`、
  `compare`、`freshness_assessment`、`manipulation_risk`。
- `targets`: operation 的來源類別或前序 operation output reference。
- `deliverables`: 原題要求必須回答的 typed output keys。
- `time_window`: 明示時間窗；未提供時記錄 deterministic default，不得默默猜。
- `matched_official_template`: 選填、可為 null；只供相容與分析，不影響 supported 判定。
- `parse_confidence`、`parse_mode`、`unsupported_reasons`。

LLM 回傳必須通過封閉 JSON schema。asset/source/operation/deliverable 只能引用 registry
已知值；未知值不得直接驅動 tool 或 connector。

### 3.2 Capability Registry

每個 capability 宣告：

- input/output types；
- 支援的 source kinds；
- dependencies；
- 是否需要 LLM、live data 或額外成本；
- coverage requirements；
- insufficient-data 的 typed outcome。

Planner 只做拓撲排序與相依驗證，不允許 LLM 直接指定 Python function、URL、secret、
connector 或任意參數。

### 3.3 Answer Coverage Gate

每個 requested deliverable 必須落在以下狀態之一：

- `answered`：有結構化結果與 evidence binding；
- `insufficient_data`：清楚列出缺少的來源／時間窗／樣本；
- `unsupported`：registry 無此能力；
- `failed`：執行失敗並保留 typed reason。

只要存在非 `answered` 項目，整體狀態不得宣稱完整完成；UI/Markdown/API 必須顯示
partial/insufficient/unsupported，而不是用一般市場判斷掩蓋漏答。

## 4. 第一個縱向切片

以本次 BTC 題目作 golden case：

1. deterministic parser 先支援中英文關鍵結構，LLM compiler 介面可注入但測試預設離線；
2. 產出 news sentiment、social sentiment、alignment、freshness、manipulation risk 五項；
3. news/social 分開聚合，不再拿 objective vs combined sentiment 代替；
4. freshness 同時呈現兩側最新時間、資料年齡、時間窗落差與是否可比較；
5. manipulation scope 限定題目要求的 news/social，保留 flagged claims，不讓通用
   `ohlcv-csv` burst 取代社群操弄回答；
6. API report 增加選填 `intent`、`deliverables`、`answer_coverage`，舊 payload 仍可讀；
7. UI 新增對題回答區塊；通用市場總結可保留，但不得取代 requested deliverables。

## 5. 分階段實作

### Phase A — 純契約與 compiler（本輪起點）

- 新增 DB-free dataclass/schema、registry、validation errors。
- deterministic fallback parser 與 injectable LLM parser port。
- 官方三題、任意題、combined、unknown、mixed-language fixtures。
- 不接 production Bedrock，不改 DB，不改 migration。

### Phase B — capability composition

- 實作 news/social sentiment、cross-source alignment、freshness、scoped manipulation adapters。
- 以既有 scored claims/evidence 為唯一資料來源，不重新抓取、不重算權威 trust。
- operation dependency graph 與 insufficient-data semantics。

### Phase C — report/API/UI

- 將 deliverables 與 coverage 放進 public payload。
- evidence/claim id 綁定與 validator。
- 對題回答 UI、partial/unsupported 狀態、mobile/desktop eye scan。

### Phase D — LLM 與正式路由

- 依 #965/#966 核准契約接 Bedrock structured output。
- 套用全域 1 RPS、daily budget、timeout、prompt-injection 與 deterministic fallback。
- formal job/idempotency/receipt 依既有 #957/#958 契約，不在本計畫另造權威。

## 6. 驗收條件

- 原 BTC 題目產出五個 requested deliverables，逐項 answered 或明確 insufficient。
- `alignment` 明確比較 news 與 social，不得以 objective vs sentiment 代替。
- 操弄分析只回答 query scope；被 manipulation flags 標記的社群證據不得因 top-N 截斷消失。
- 時效輸出可驗證兩側 latest timestamp、age、window skew 與 comparability。
- 官方三個範例持續通過，但任意／組合題不被強迫三選一。
- LLM malformed/timeout/unsupported operation 均 fail closed 到 typed fallback，不退回看似完成的通用報告。
- legacy API、排程、comparison 與舊 Report payload 回歸全綠。
- security/cost review、`/codex-review`、完整 pre-push 與 UI eye scan 完成後才可合併。

## 7. 非目標與安全界線

- 不讓 LLM 直接決定 market direction、trust score 或 evidence eligibility。
- 不把官方三範例擴寫成新的封閉 enum 清單。
- 不做 DB schema/migration；若後續 formal persistence 需要異動，另案且須 Eric token 授權。
- 本輪不部署、不動 secret、不放寬 Bedrock 成本與 RPS 防線。

## 8. 測試矩陣

- Unit：intent schema、parser fallback、registry、dependency graph、coverage state machine。
- Golden：本 BTC news/social 題、官方三題、跨資產、假設、unknown、mixed intent。
- Adversarial：prompt injection、虛構 source、循環 dependency、空 operations、LLM malformed JSON。
- Integration：pipeline 以既有 evidence 產出 scoped deliverables；top-N 不得吞 manipulation evidence。
- API/UI：validator parity、partial/unsupported 顯示、390px/1440px、zh-Hant/en。
- Regression：既有 cross-source、comparison、analysis-flow、report、budget/security gates。

## 9. 交付切分

本輪先完成 Phase A 與 BTC golden case 所需的最小 Phase B；Phase C/D 依變更量拆 PR，
避免把 open-intent routing、正式成本控制與整個 UI 一次塞進不可審查的大 PR。
