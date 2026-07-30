# PLAN — 比賽官方題型 UI 對齊

> **已取代（2026-07-30）**：本計畫源自已關閉的 #935，所提「主要 UI
> 改成官方三題型／三選一」不再是現行產品契約。權威現況改見
> `docs/plans/ISSUE-937-UI-CONTRACT-TRUTH-2026-07-29.md`：官方「多源整合、
> 假設驗證、比較分析」只作 release/E2E 驗證案例，不是 Hermes UI、client
> validation 或 API input whitelist。使用者入口必須接受任意自然語言，
> mixed/unknown intent 亦納入驗證。下文僅保留為歷史決策紀錄，不應據此實作。

> 日期：2026-07-26  
> 目標分支：`develop`  
> 範圍：TrustForge 前端分析入口與少量後端 mode mapping；不變更核心 Trust Layer 演算法。  
> 依據：`docs/competition/COMPETITION-OFFICIAL.md`。  
> 狀態：**CEO 已裁示採方案 B**（主要 UI 改成官方三題型，4–8h，後端已支援三題型，
> 主要成本在前端與測試）；C／D 視時間再補。2026-07-27 依實作面回填四處修正
> （§5.1 左軌、§7 雙語系、§8 五道 gate、§10.2 顯示層過濾），待排入實作。

---

## 1. 問題背景

目前 TrustForge 首頁／Hermes Dashboard 的「分析模式」顯示五個產品化分析目的：

- 風險評估
- 市場情緒
- 基本面
- 新聞驗證
- 價格催化因子

這五個選項不是 HOYA BIT 官方命題的題型。官方文件列出的題型是三種：

1. 多源整合
2. 假設驗證
3. 比較分析

目前程式邏輯其實已把五個 mode 映射到官方題型：

| 現有 mode | 後端映射 |
|---|---|
| `risk` | `multi_source` |
| `sentiment` | `multi_source` |
| `fundamentals` | `hypothesis` |
| `news` | `multi_source` |
| `catalyst` | `hypothesis` |
| `comparison` | `comparison` |

但評審或現場操作人看到 UI 時，可能誤以為比賽要求五種題型，造成交付格式不對題。

---

## 2. 官方要求摘要

官方題型：

| 題型 | 官方描述 |
|---|---|
| 多源整合 | 分析指定幣種過去兩週表現，整合價格／鏈上／新聞／社群，給整體判斷並說明各類資料一致程度。 |
| 假設驗證 | 對指定假設蒐集正反證據，給最終判斷與理由。 |
| 比較分析 | 比較兩個幣種的市場位置與風險特徵，例如流動性／關注度／風險敞口。 |

官方幣種池：

```text
BTC / ETH / SOL / BNB / XRP
```

官方 4 交付件：

```text
report.md
 evidence.json
 execution_log.jsonl
 Source / Config
```

---

## 3. 目標

把 UI 改成「比賽方看得懂」的格式：

1. 首要選項是官方三題型。
2. 五個產品化分析目的降級成「分析角度／焦點」，不可再顯示為官方題型。
3. 比賽模式下只顯示官方幣種池，不顯示額外幣種。
4. 產出仍對應現有後端 `QuestionType`：`multi_source` / `hypothesis` / `comparison`。
5. 不破壞既有一般使用者入口；若時間不足，先保留一般模式、優先補比賽模式。

---

## 4. 建議方案

### 方案 B — 先改官方三題型 UI（建議優先做）

把目前單一「分析模式」下拉改成官方題型：

```text
多源整合
假設驗證
比較分析
```

原本五個選項改為：

```text
分析角度（選填）
- 風險
- 情緒
- 基本面
- 新聞
- 催化因子
```

若工期更短，第一版可以先不做第二層分析角度，只用官方三題型。

### 方案 D — 比賽專用頁（後續可做）

新增 `/competition` 或 Dashboard 的「🏆 比賽模式」入口，畫面只包含：

- 幣種：BTC / ETH / SOL / BNB / XRP
- 題型：多源整合 / 假設驗證 / 比較分析
- 主辦現場題目輸入框
- 執行按鈕
- 產出區：Final Report / Evidence List / Execution Log

這是最乾淨的決賽操作介面，但工時較高。

---

## 5. 變更範圍

### 5.1 前端主要檔案

| 檔案 | 變更 |
|---|---|
| `frontend/src/components/QueryConsole.tsx` | 把 `ANALYSIS_MODES` 改為官方題型；必要時新增 optional focus selector。 |
| `frontend/src/pages/HermesDashboard.tsx` | 調整 `qtypes`、`activeQuestionMode`、`onSubmit()` 的 `type` / `mode` 產生邏輯。 |
| `frontend/src/components/HermesLeftRail.tsx` | **本次補上**：四欄改版後，題型／模式選單實際渲染在左軌，不是只在 `QueryConsole` 內。漏掉這支會出現「主入口改成三題型、左軌仍列五種」的雙軌不一致。 |
| `frontend/src/lib/beginnerExperience.ts` | 調整 beginner intents，不再把五種模式當官方題型；維持作為分析目的。 |
| `frontend/src/hermes/hermesI18n.tsx` | 新增／修改文案：官方題型、分析角度、比賽模式提示。 |
| `frontend/src/pages/HermesDashboard.test.tsx` | 更新目前寫死五種 mode 的測試。 |
| `frontend/src/pages/AnalyzePage.test.tsx` | 更新 URL param 與 submit 行為測試。 |

### 5.2 後端檔案

| 檔案 | 變更 |
|---|---|
| `src/trustforge/schema.py` | 不一定要改；官方三題型已存在。 |
| `src/trustforge/analysis_flow.py` | 若前端仍送 `mode`，保留舊 mode mapping；若改送 official mode，新增相容 mapping。 |
| `src/trustforge/web.py` | 若 API contract 需要接受新的 `focus` 或 official mode，才需調整。 |

### 5.3 文件

| 檔案 | 變更 |
|---|---|
| `docs/competition/COMPETITION-OFFICIAL.md` | 不改官方原文。 |
| `docs/competition/SUBMISSION-CHECKLIST.md` | 如新增比賽模式，補入口與操作說明。 |
| `docs/competition/FINAL-REPORT-TEMPLATE.md` | 若最後模板留在主 repo，需跟 UI 題型保持一致；目前公開文件版在 TrustForge-devlog。 |

---

## 6. 實作步驟

### Phase 0 — 盤點與安全分支

1. 從最新 `develop` 建立分支：

   ```bash
   git checkout develop
   git pull --ff-only origin develop
   git checkout -b feat/competition-question-format-ui
   ```

2. 確認目前測試基線：

   ```bash
   git status --short
   git diff --check
   ```

3. 依 repo `AGENTS.md`，本機 `.githooks/pre-push` 是自動化 gate。

### Phase 1 — 最小可用版

1. `QueryConsole.tsx`：官方題型 selector。
2. `HermesDashboard.tsx`：
   - `type=multi_source` / `hypothesis` / `comparison` 直接對應官方題型。
   - `comparison` 題型導向 `/compare` 或在同頁明確提示「比較分析請至比較頁」。
3. 預設題目改為官方題型文案：

   ```text
   多源整合：分析 BTC 過去兩週表現，整合價格、鏈上、新聞與社群資料，給整體判斷並說明各類資料一致程度。
   假設驗證：檢驗「BTC 短期將盤整」這個假設，蒐集正反證據並說明最終判斷與理由。
   比較分析：比較 BTC 與 ETH 當前市場位置與風險特徵。
   ```

4. i18n 更新中文與英文文案。
5. 更新測試。

### Phase 2 — 分析角度保留（可選）

若產品還需要原本五種入口，把它改成 second-level focus：

```text
type=multi_source&focus=risk
```

或維持：

```text
type=multi_source&mode=risk
```

但 UI 必須標明：

```text
官方題型：多源整合
分析角度：風險
```

### Phase 3 — 比賽專用模式（可選）

新增 `/competition` 或首頁區塊：

- 只顯示官方幣種池。
- 題型 selector 只有三項。
- 一鍵產出與下載 `report.md`、`evidence.json`、`execution_log.jsonl`。
- 顯示 15 分鐘預算與目前執行狀態。

---

## 7. 驗收標準

### UI 驗收

- [ ] 主要分析入口不再把「風險評估 / 市場情緒 / 基本面 / 新聞驗證 / 催化因子」顯示成官方題型。
- [ ] 使用者／評審可明確看到官方三題型：多源整合、假設驗證、比較分析。
- [ ] 比較分析不會被錯送成單幣分析。
- [ ] 比賽模式若存在，只顯示 BTC / ETH / SOL / BNB / XRP。
- [ ] 手機版下拉與按鈕不 overflow，且 **zh-TW 與 en 兩個語系都要各驗一次**：
      英文字串普遍比中文長（例：「多源整合」4 字 vs `Multi-source integration`
      25 字元），實測上 en 才是換行／溢出的觸發語系，只驗中文會漏掉。

### API / 行為驗收

- [ ] 多源整合送出 `type=multi_source`。
- [ ] 假設驗證送出 `type=hypothesis`。
- [ ] 比較分析導向 `/compare` 或送出 `type=comparison`，且輸出比較報告。
- [ ] 既有 `mode=risk/sentiment/fundamentals/news/catalyst` 連結仍向後相容或有清楚 redirect / fallback。

### 文件／競賽驗收

- [ ] UI 文案與 `docs/competition/COMPETITION-OFFICIAL.md` 一致。
- [ ] Final Report、Evidence List、Execution Log 的定位不混淆。
- [ ] 不宣稱投資建議、勝率、價格保證。

---

## 8. 測試建議

### 前端

本 repo 的實際 gate 是五道，缺一不可（`npm test -- --run` 與 `npm run lint`
兩個指令在此 repo 並不存在，照抄會直接失敗）：

```bash
cd frontend
npx vitest run                     # 1. 單元／契約測試
npx tsc -b                         # 2. 型別
npx oxlint                         # 3. lint
npm run build                      # 4. production build
npm run test:mobile-geometry       # 5. 行動裝置幾何／命中測試矩陣
```

第 5 道會用 Playwright 在 13 種視窗尺寸實跑命中測試，題型 selector 若在窄
視窗被遮住或縮到 24px 以下會在這裡被擋下——這正是本 PLAN §7「手機版下拉與
按鈕不 overflow」的自動化對應，不要只靠肉眼。

### 後端

```bash
python3 -m pytest tests/test_analysis_flow.py tests/test_web.py -q
python3 -m pytest tests/test_package_finale_submission.py -q
```

### 全 repo gate

依 `AGENTS.md`：

```bash
.githooks/pre-push
```

GitHub Actions 不作 release gate。

---

## 9. 工時估算

| 方案 | 工時 | 風險 | 說明 |
|---|---:|---|---|
| A：只改文案，把五種模式改名為「快速分析目的」 | 1–2 小時 | 低 | 最快，但比賽現場仍不夠乾淨。 |
| B：主要 UI 改成官方三題型 | 4–8 小時 | 中低 | 建議優先做。後端已支援三題型，主要是前端與測試。 |
| C：官方題型 + 分析角度雙層 selector | 1–1.5 天 | 中 | 比 B 更完整，保留產品化 UX。 |
| D：新增比賽專用頁 /competition | 2–3 天 | 中 | 最乾淨，適合決賽現場。 |

建議先做 B，再視時間補 C 或 D。**2026-07-27 CEO 裁示：採 B。**

---

## 10. 風險與注意事項

1. **不要刪掉後端五 mode mapping**：現有排程、舊連結、測試或歷史 job 可能還會用。
2. **不要移除 ARB 產品支援**：只在比賽模式限制官方幣種池；一般產品可保留 ARB。
   實作上這條要講死：**限制發生在顯示層的 filter，不是換掉資料來源**。
   亦即比賽模式從既有幣種清單過濾出 BTC/ETH/SOL/BNB/XRP 來 render，
   不得另外維護一份「比賽幣種常數」去取代原清單——後者會讓一般產品的
   ARB 跟著消失，且日後兩份清單必然漂移。
3. **Comparison 是特殊路徑**：目前已有 `/compare` 頁，改 UI 時要避免把比較分析硬塞進單幣 `/analyze`。
4. **i18n 與測試會連動**：`hermesI18n.tsx` 的 mode label、`modeLabel()`、測試中的 role/文字查詢都會受影響。
5. **Release gate 以本機流程為準**：本 repo 指定本機 `.githooks/pre-push`。
6. **文件要 evidence-first**：若 UI 文案宣稱「官方題型」，必須對齊 `COMPETITION-OFFICIAL.md`。

---

## 11. 建議分工

| 角色 | 任務 |
|---|---|
| CPO / gray | 確認 UI copy、比賽模式資訊架構與評審話術。 |
| CTO | 實作 QueryConsole / HermesDashboard / tests。 |
| QA | 桌機與手機 eye scan；驗證三題型送出的 URL / API 行為。 |
| CISO / harper | 若涉及 live token、成本或管理路徑，審查是否暴露 secrets 或造成 Bedrock 濫用。 |
| Eric / CEO | ~~決定採 B、C 或 D~~ → 2026-07-27 已裁示採 **B**；核准進入實作。 |

---

## 12. Definition of Done

- [ ] 最新 `develop` 上有 PR 或 commit-bound implementation。
- [ ] 三題型 UI 可操作。
- [ ] 比賽模式或主要分析入口不再誤導為五題型。
- [ ] 本機測試與 build 通過。
- [ ] `.githooks/pre-push` 通過。
- [ ] 桌機與手機人工 eye scan 通過。
- [ ] 文件更新並標明官方三題型。
- [ ] Eric 確認比賽現場流程可用。
