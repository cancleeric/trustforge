# TrustForge 賽後商用化開發計劃（2026-08-04）

## 目的

本計劃將賽後商用化分析轉成可開 issue、可執行、可驗收的工程與產品工作。目標是把 TrustForge 從黑客松展示作品，整理成可導入、可稽核、可維運、可擴充的企業信任校準產品。

本計劃的商用基準文件：

- [POST-COMPETITION-COMMERCIALIZATION-ANALYSIS-2026-08-04.md](../reports/POST-COMPETITION-COMMERCIALIZATION-ANALYSIS-2026-08-04.md)

## 範圍

### In scope

1. 聚焦第一個商用 MVP：鏈上風險情報信任校準。
2. 補齊 TrustForge 商用底座：snapshot、run lineage、evidence hash、scoring version、audit log、credential boundary。
3. 定義 connector contract 與資料源狀態。
4. 將 Dashboard 從工程監控調整為決策支援畫面。
5. 建立 POC 銷售與交付文件：一頁式說明、sample report、POC proposal。
6. 將開發工作拆成每張 issue 不超過 12 小時，並標示相依性。

### Out of scope

1. 立即做完整 SaaS billing、多租戶付費系統、enterprise SSO。
2. 同時支援所有產業與所有資料源。
3. 自動修改 Trust weights、模型、production code 或 deployment。
4. 宣稱 ISO/IEC 42001、EU AI Act conformity、CE marking 或任何未經核准的法規符合性。
5. 用 current-state API 假裝歷史資料。

## 開發原則

1. **Trust-first，不是 demo-first**：每個輸出都要能回溯、驗證、重跑。
2. **少量可靠資料源優先**：先做少而完整，不追求資料源數量。
3. **核心與外掛分離**：Trust Kernel 可升級，connector 可替換。
4. **前端不擁有分析執行**：瀏覽器與按鈕只能查結果或提交請求，不是 pipeline owner。
5. **上一份完整結果保留**：新結果尚未完成時不得 blank，不得顯示半成品。
6. **文件與產品同步**：商務文件、技術文件與 issue 驗收條件要一致。

## 里程碑

| 里程碑 | 目標 | 退出條件 |
|---|---|---|
| M0：商用文件落地 | 分析報告與開發計劃入 repo | 文件可連結、`git diff --check` 通過、branch 已推送 |
| M1：商用 MVP 契約 | 定義鏈上風控 MVP、connector contract、lineage contract | 文件與測試覆蓋 schema/contract |
| M2：可信任工程底座 | snapshot、run lineage、hash、scoring version、credential states | backend tests 可證明不可污染、可回溯、可標示 blocked |
| M3：決策型 Dashboard | UI 呈現問題、結論、Evidence、分數拆解、pipeline 狀態 | frontend test/build 通過，轉場不 blank |
| M4：POC 交付包 | 一頁式、sample report、POC proposal、demo path | 客戶訪談可用的文件與 demo 流程完成 |

## Issue 拆解原則

- 每張 issue 預估工時不可超過 12 小時。
- 每張 issue 必須有 acceptance criteria。
- issue body 必須列出 dependencies 與 blocked-by。
- 先開契約與文件 issue，再開實作 issue。
- 安全、成本、credential、外部 API 授權相關 issue 需標示 security/cost review。

## Issue Backlog

| 序 | 建議標題 | 預估 | 相依性 | 驗收重點 |
|---:|---|---:|---|---|
| 1 | Define commercial MVP scope for on-chain risk trust calibration | 4h | 無 | MVP 輸入、輸出、非目標、demo path 文件化 |
| 2 | Define connector evidence contract and source state taxonomy | 8h | #1 | provider/source_url/published_at/retrieved_at/license/hash/status schema 有文件與測試 |
| 3 | Add evidence contract fixtures for Arkham and Whale Alert sources | 8h | #2 | Arkham/Whale Alert fixture 不含 secrets，可驗證 ready/credential-gated/blocked |
| 4 | Add run lineage and scoring version contract tests | 10h | #2 | report 可回到 run_id、snapshot_id、scoring_version、evidence ids |
| 5 | Harden snapshot isolation for commercial report generation | 12h | #4 | 分析途中資料變動不污染本次報告；測試可重現 |
| 6 | Add evidence content hash and raw payload reference validation | 10h | #2 | 每筆 Evidence 有 content_hash/raw_payload_reference；缺漏會 fail fast |
| 7 | Add credential boundary states to connector/runtime status | 8h | #2、#3 | 缺 key 時顯示 credential-gated，不假裝 ready |
| 8 | Add retry/DLQ observability for commercial analysis packages | 12h | #4 | queue/retry/DLQ/duration/stage 可查，daemon restart 後不遺失 |
| 9 | Build commercial sample report template for on-chain risk | 8h | #1、#4、#6 | sample report 顯示分數、分歧、Evidence lineage、風險理由 |
| 10 | Reshape dashboard IA into decision-support rails | 12h | #1、#9 | 左/中/右/底四區 IA，避免重複分數，保留上一份完整結果 |
| 11 | Add frontend transition tests for complete-result preservation | 8h | #10 | 新分析 pending 時不 blank、不顯示半成品、不閃爍核心 tree |
| 12 | Add commercial POC one-page and proposal template | 6h | #1、#9 | 一頁式、POC proposal、診斷包/POC/導入包邊界完成 |
| 13 | Add live-data licensing and historical archive readiness checklist | 6h | #2、#3 | ready/credential-gated/archive-required/blocked checklist 文件化 |
| 14 | Create commercial MVP release gate checklist | 8h | #4～#13 | local gate、文件、sample report、security/cost review、demo evidence 清單完成 |

> 註：表內 `#N` 代表本計劃中的序號；GitHub issue 建立後，對應編號見下表。

## GitHub issue 對應

| 計劃序 | GitHub issue |
|---:|---|
| 1 | [#1423](https://github.com/cancleeric/trustforge/issues/1423) |
| 2 | [#1424](https://github.com/cancleeric/trustforge/issues/1424) |
| 3 | [#1425](https://github.com/cancleeric/trustforge/issues/1425) |
| 4 | [#1426](https://github.com/cancleeric/trustforge/issues/1426) |
| 5 | [#1427](https://github.com/cancleeric/trustforge/issues/1427) |
| 6 | [#1428](https://github.com/cancleeric/trustforge/issues/1428) |
| 7 | [#1429](https://github.com/cancleeric/trustforge/issues/1429) |
| 8 | [#1430](https://github.com/cancleeric/trustforge/issues/1430) |
| 9 | [#1431](https://github.com/cancleeric/trustforge/issues/1431) |
| 10 | [#1432](https://github.com/cancleeric/trustforge/issues/1432) |
| 11 | [#1433](https://github.com/cancleeric/trustforge/issues/1433) |
| 12 | [#1434](https://github.com/cancleeric/trustforge/issues/1434) |
| 13 | [#1435](https://github.com/cancleeric/trustforge/issues/1435) |
| 14 | [#1436](https://github.com/cancleeric/trustforge/issues/1436) |

## 建議執行順序

```text
1 → 2 → 3
      ↘ 4 → 5
      ↘ 6
      ↘ 7
4 → 8
1 + 4 + 6 → 9 → 10 → 11
1 + 9 → 12
2 + 3 → 13
4～13 → 14
```

## 驗證門檻

### 文件與規劃變更

- `git diff --check` 通過。
- 新增文件被 `docs/README.md` 索引。
- 文件不宣稱未驗證法規符合性、production deployment 或 live data 可用性。

### 後端變更

- targeted pytest。
- lineage / snapshot / evidence contract tests。
- credential-gated 與 blocked 狀態測試。
- `git diff --check`。
- repository-local `.githooks/pre-push` gate，除非本輪明確只推文件 branch 並記錄未跑原因。

### 前端變更

- frontend targeted tests。
- typecheck/build。
- UI eye scan：桌面、手機、overflow、轉場、data truthfulness、錯誤狀態。

### 安全與成本

涉及以下內容時需 security/cost review：

- 外部 API key。
- 客戶資料。
- live provider calls。
- AWS / Bedrock / paid APIs。
- connector 授權與資料保存。

## 第一輪建議

第一輪先開並執行序號 1～4 與 12：

1. 先把商用 MVP scope 與 POC 交付口徑定死。
2. 定義 connector evidence contract，避免後面各自發散。
3. 先用 Arkham / Whale Alert 做 fixture，不碰 live key。
4. 補 lineage/scoring version contract tests，先建立信任產品底座。
5. 同步整理一頁式與 POC proposal，讓商務訪談可以開始。

這樣第一週就能同時推進「能賣」與「能信」，但不冒 live credential 與資料授權風險。
