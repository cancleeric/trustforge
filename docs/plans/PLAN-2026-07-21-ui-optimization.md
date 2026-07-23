# TrustForge UI/UX 優化計劃—現況整理版

- 初版日期：2026-07-21
- 現況稽核：2026-07-23
- 狀態：進行中
- 原則：每張 Issue 不超過 12 小時；先驗證、後修正；不得另建平行資料真相

## 1. 本次更正

本文件已依 2026-07-23 程式碼與 Issue 現況重整，不再沿用初版的靜態畫面判斷。

初版 P0-1 認為「首頁分析流程壞掉，但獨立 `/analyze` 正常」。#360 的實證顯示，首頁與
`/analyze` 都透過 `/api/analysis-job` 建立與輪詢工作；當時看到的完成內容是歷史快照，
不是兩條不同的分析管線。因此該診斷已被取代，不再列為待開發項目。

## 2. 現況總表

| 項目 | 狀態 | Issue／處置 |
|---|---|---|
| 對話內容重複渲染 | 已完成 | #356 CLOSED |
| 四個信任維度被誤解為權重 | 已完成 | #357 CLOSED；已說明為獨立分數 |
| Settings 未完成字樣 | 已完成 | #358 CLOSED |
| 假的 divergence CTA | 已完成 | #359 CLOSED |
| 首頁與 `/analyze` 管線差異 | 診斷已更正 | #360 CLOSED；兩者均使用 analysis-job |
| TrainingStatus 404／timeout 顯示紅色錯誤 | 待處理 | #539，≤4h |
| 首頁 hero 差異化副標 | 待 CEO 文案決策 | #361，≤6h |
| 手機版 overflow／scroll | 先親驗 | #540，≤4h；失敗才執行 #362，≤8h |
| Snapshot 的資料血緣不完整 | 待處理 | #541，≤8h |
| 首頁 Breakdown Drawer 證據能力不足 | 待處理 | #542，≤12h；依賴 #541 |

## 3. Issue 與相依性

| 順序 | Issue | 範圍 | 估時 | 相依性 |
|---|---|---|---:|---|
| A | #539 | TrainingStatus 對 404／timeout 採中性 fail-soft，保留真正服務錯誤辨識 | ≤4h | 無 |
| B1 | #540 | 以 375×667、390×844 真實瀏覽器幾何親驗首頁 | ≤4h | 無 |
| B2 | #362 | 僅修復 #540 可重現的 overflow／scroll 問題 | ≤8h | #540 必須失敗 |
| C1 | #541 | Snapshot Modal 與下載 JSON 補齊 `data_lineage` | ≤8h | 無 |
| C2 | #542 | 首頁 Breakdown Drawer 對齊證據、分歧與 JSON 匯出 | ≤12h | #541 |
| D | #361 | 首頁 hero 差異化副標 | ≤6h | CEO 先拍板文案 variant |

```text
#539 ───────────────────────────────► 可獨立執行

#540 ──通過──► 關閉 #362，不改程式
  └────失敗──► #362 僅修可重現缺陷

#541 ────────► #542

CEO 文案決策 ─► #361
```

## 4. 各項驗收標準

### #539 TrainingStatus fail-soft

- `/api/training/status` 回傳 200 時維持正常狀態與資料。
- 404 或 timeout 時顯示中性「暫無訓練狀態／服務未提供」，不呈現紅色系統故障。
- 其他真實異常仍可辨識，不得把所有錯誤吞掉。
- 補齊 200、404、timeout 的元件測試。

### #540／#362 手機版幾何

- 使用真實瀏覽器檢查 375×667 與 390×844。
- 驗證首頁首屏、分析輸入、進度、完成、錯誤等狀態。
- 記錄 `scrollWidth/clientWidth`、主要容器 bounding box、可捲動區與 CTA 可達性。
- 若全部通過，#362 直接以驗證證據關閉；若失敗，#362 只修重現項並重跑相同矩陣。

### #541 Snapshot 資料血緣

既有 `EvidenceTable`、`SnapshotModal`、JSON 下載入口必須重用。畫面與下載內容需完整表達：

- `file`
- `sha256`
- `rows`
- `coverage`
- `trading_pair`
- `time_basis`
- `columns`

缺值需誠實標示，不得製造資料；畫面與下載 JSON 必須一致。

### #542 首頁 Breakdown Drawer

- 重用既有證據表、Snapshot 與匯出能力，不另建第二套資料解釋。
- Drawer 能由首頁合成結果追到四軸、推理軌跡、證據與分歧。
- 提供與畫面同源的 JSON 匯出。
- 空資料、部分資料、錯誤與窄螢幕狀態均可用。

### #361 Hero 文案

- CEO 先選定文案 variant，再開工。
- 說明 TrustForge 的差異化能力，不宣稱不存在的準確率或模型能力。
- Desktop／mobile 不造成截斷、重疊或首要 CTA 位移。

## 5. 共通交付門檻

每張 Issue 都必須：

1. 從獨立 scoped branch 開發，PR 連回 Issue 並指定 reviewer。
2. 執行相應單元／元件／瀏覽器測試、lint、build 與 `git diff --check`。
3. UI 變更在實際 PR branch 做 desktop、mobile eye scan，檢查資料真實性、overflow、
   狀態轉換與錯誤狀態。
4. Merge 前完成 `/codex-review` 對抗審，修完所有 finding 並留下 commit-bound reviewer
   attestation；不得自我核准或繞過 branch protection。
5. Merge 前確認 `.githooks/pre-push` 與相應 local gate evidence 已綠，且 PR 記錄 commit-bound evidence。
6. Merge 後確認 release workflow 所需的本機／部署驗證；若進入正式發布，另依 release workflow 親驗使用者流程。

## 6. 歷史研究的保留方式

2026-07-21 初版對資訊密度、信任維度可理解性、首頁敘事與 mobile 風險的觀察仍具參考
價值，但只能當日期綁定的研究素材，不能凌駕目前程式碼與真實瀏覽器驗證。尤其 P0-1
已由 #360 推翻，不得再據此建立「修復另一條分析管線」的工作。

## 7. 非本輪範圍

- 不改後端分析演算法、模型訓練、資料集、DB schema 或 migration。
- 不修改 ModelHub、LIDS、外部服務或生產環境。
- 不在本文件整理 PR 中順手實作 #539–#542、#361 或 #362。
- 不為首頁另建與完整分析頁競爭的證據／Snapshot 資料模型。
