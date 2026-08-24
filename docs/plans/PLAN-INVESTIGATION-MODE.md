# TrustForge Investigation Mode 開發計劃

> 狀態：Ready to execute
>
> 對應分析：`docs/reports/INVESTIGATION-MODE-ANALYSIS.md`
>
> 分支：`feature/1224-unified-comparison-report`
>
> 原則：每個 issue 預估不得超過 24 小時；先完成相依項，再開始下游功能。

## 1. Goal

將 TrustForge 既有的 source、archive、dedup、corroboration 與 evidence scoring 能力，串成一條可實際使用的 Investigation Mode：

```text
claim／URL
→ claim extraction
→ source retrieval／recovery
→ canonicalization + deduplication
→ independent-source grouping
→ supporting／contradicting evidence
→ conclusion + unknowns
→ Markdown／JSON investigation report
```

第一版不重寫既有 Market Analysis Mode，也不以增加 connector 數量作為完成條件。

## 2. Definition of Done

第一版完成時，使用者可以輸入一段 claim 或 URL，並取得一份報告，其中：

- 每個 claim 都有 evidence，或明確標記 `unverified`／`unresolved`
- 每條 evidence 可追溯到 source
- 每個 source 有 canonical URL、retrieved time、availability 與 evidence level
- 原文、官方 mirror、archive、snippet、二手引用與 inference 不混為同一級
- 轉載文章不會被錯算成獨立來源
- supporting 與 contradicting evidence 同時呈現
- conclusion 帶有 `as_of`
- unknowns 明確列出
- 可輸出 Markdown 與 JSON
- offline fixtures 可 deterministic 重現

## 3. Dependency graph

```text
I0 測試失敗／阻塞定位
  ↓
I1 Investigation contract + evidence levels
  ↓
I2 deterministic source/archive fixtures
  ├──────────────┐
  ↓              ↓
I3 recovery      I4 source relation + independence
  └──────┬───────┘
         ↓
I5 evidence assembly + contradiction report
         ↓
I6 CLI／web 最小入口與 Markdown／JSON export
         ↓
I7 golden-set acceptance + documentation
```

## 4. Issues（每項 ≤ 24 小時）

### I0 — 隔離 pytest 第一個 failure 與阻塞測試

- **預估**：8–16 小時
- **依賴**：無
- **目的**：取得可重現的第一個 failure；分辨 assertion failure、外部網路、fixture、慢測試與 hang。
- **範圍**：測試收集順序、`--maxfail=1 -vv`、分批測試、network／integration 標記、必要的 diagnostic；不順手修 unrelated tests。
- **驗收**：有明確失敗測試名稱與 traceback；相關 source/archive 測試可被單獨執行；所有新增／修改的測試不依賴即時外部網路。

### I1 — 建立 Investigation contract 與 evidence level

- **預估**：12–20 小時
- **依賴**：I0
- **目的**：固定 `InvestigationRequest`、`Claim`、`Evidence`、`SourceSnapshot`、`SourceRelation`、`InvestigationReport` 的資料契約。
- **範圍**：欄位、enum、序列化、`E0–E4` evidence level、`unverified`／`unresolved` 狀態；不接 UI、不新增 provider。
- **驗收**：有 schema／fixture；欄位可 round-trip JSON；沒有 source 的 claim 不可被序列化成已驗證結論。

### I2 — 建立 deterministic source/archive fixtures

- **預估**：8–16 小時
- **依賴**：I1
- **目的**：為 live、官方 mirror、archive、snippet、secondary quote、unresolved 建立固定測試資料。
- **範圍**：fixture factory、snapshot metadata、失敗情況；不呼叫真實網路。
- **驗收**：至少 6 類 fixture；能驗證 URL、hash、retrieved time、availability、evidence level；重跑結果一致。

### I3 — 實作 source recovery ladder

- **預估**：16–24 小時
- **依賴**：I2
- **目的**：將來源回溯順序固定為 live → official mirror → index/cache → archive → secondary quote → inference → unresolved。
- **範圍**：recovery orchestration、retrieval method、失敗原因與 snapshot reference；不新增大量外部 connector。
- **驗收**：6 類 fixture 都能得到正確 level；E2/E3/E4 不得被標為原始來源；找不到時輸出 `unresolved`。

### I4 — 實作 source relation 與 independent-source grouping

- **預估**：16–24 小時
- **依賴**：I2
- **目的**：區分原文、官方 mirror、syndicated copy、quote、update、contradiction 與未知關係。
- **範圍**：relation model、canonical URL grouping、independence group、dedup invariant；不做複雜 ML clustering。
- **驗收**：轉載不重複計分；報告能同時顯示 article count 與 independent group count；關係未知時保持 `unknown`，不可過度推斷。

### I5 — 組裝 evidence、contradiction 與 conclusion report

- **預估**：12–20 小時
- **依賴**：I3、I4
- **目的**：把 claims、evidence、source timeline、supporting／contradicting stance 組成統一 investigation report。
- **範圍**：report builder、unknowns、`as_of`、confidence／evidence summary；不改寫既有 unified comparison contract 以外的流程。
- **驗收**：每個 claim 有 evidence 或 unknown；支持與反證並列；沒有證據時不生成肯定結論；Markdown／JSON 結構穩定。

### I6 — 提供最小 CLI／web Investigation 入口

- **預估**：16–24 小時
- **依賴**：I5
- **目的**：讓使用者輸入 claim 或 URL，取得 investigation report。
- **範圍**：一個最小入口、輸入驗證、同步執行、Markdown／JSON export、source links；不做 graph UI、不做帳號或長任務佇列。
- **驗收**：五個固定案例可完成 end-to-end；錯誤輸入有可讀訊息；來源與 evidence 可點擊／追溯；不洩漏 token、prompt 或 runtime secret。

### I7 — Golden-set acceptance、QA 與操作文件

- **預估**：12–20 小時
- **依賴**：I6
- **目的**：將 Investigation Mode 變成可回歸、可交接的產品能力。
- **範圍**：至少 20 個 golden cases、coverage／latency evidence、使用說明、known limitations、release checklist。
- **驗收**：
  - 5 live source cases
  - 5 deleted／archived cases
  - 3 duplicated coverage cases
  - 3 contradictory cases
  - 2 insufficient evidence cases
  - 2 unresolved cases
  - citation validity ≥ 95%
  - 原始來源誤標 0 次
  - 無證據卻輸出肯定結論 0 次

## 5. 執行規則

1. 一次只啟動一個未滿足 dependency 的 issue。
2. 每個 issue 必須有實際驗收輸出，不以「程式已寫」作為完成。
3. I0 只處理測試基線，不順帶塞入新功能。
4. I1–I5 優先使用現有核心模組，不重複建立 parallel abstraction。
5. 每個 issue 完成後先跑其 scope 內測試，再進下一個 issue。
6. 未完成的 issue 不得被標記為 deployable。
7. 新增 provider、schema migration、長任務 queue 必須另開 issue，不放進本計劃的 24 小時工作項。

## 6. 第一個 Goal

```text
Goal: 取得一個可重現、可診斷的 pytest baseline，隔離第一個 failure 與阻塞測試，並為 Investigation Mode 建立 offline test boundary。
```

第一個實作工作是 **I0**。I0 完成前，不開始 I3、I5 或 UI；避免在測試基線不可信時擴大功能面。

## 7. 不在本輪範圍

- 完整 Market Analysis Mode 重寫
- 大量新資料來源與付費 API
- 自動化 self-improvement agent swarm
- 複雜來源 graph visualization
- 以 snippet／二手引用冒充原始來源
- 將所有 historical source 自動提升為 E0
