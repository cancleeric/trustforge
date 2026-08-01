# 比較報告整合輸出格式改善計畫

> 版本: 1.0.0 | 日期: 2026-08-01 | 提案: gray (CPO) | 決策: Eric Wang
> 目標: 將比較分析的最終交付物從「兩份分離格式」統一為「單一完整報告（分析 + 整合總結）」
> 前置依賴: COMPARISON-ANALYSIS-DEVELOPMENT-PLAN-20260728（CA-01～CA-08 已完成）
> Status: **DRAFT — 待 CEO 審核**

---

## 1. 問題陳述

### 1.1 現況

比較分析目前產出**兩種格式**的報告，且主交付物缺少整合總結：

| 輸出 | 格式 | 內容 | 問題 |
|------|------|------|------|
| `report.md`（CLI 主輸出） | `comparison_to_markdown()` | 各幣並排分析 + 相對強弱表 | **無綜合結論、無面向比較、無推翻條件** |
| `comparison_report.md`（次要輸出） | `ComparisonReport.to_markdown()` | 綜合結論 + 四面向 + 信心 | 各幣分析被 `<details>` 摺疊，**不完整展示** |

### 1.2 比賽風險

比賽評分 30% 為「主題切合度」，評審看的是**一份 Final Report**。目前的情況：
- 評審若只看 `report.md`：看到分析但看不到整合結論 → 主題切合度失分
- 評審若看 `comparison_report.md`：看到整合結論但各幣分析被摺疊 → 推理鏈不透明

### 1.3 目標格式

單一 `report.md` 包含：

```
# A vs B 比較分析報告

## 各幣詳細分析（Part I）       ← 完整展開，評審能追溯每幣的推理鏈
  ### A 幣分析（full Report.to_markdown）
  ### B 幣分析（full Report.to_markdown）

## 整合比較總結（Part II）       ← 跨幣綜合判斷
  ### 綜合結論
  ### 比較面向分析（四面向 + 雙邊證據對照）
  ## 已知限制
  ## 可能推翻條件

## 合併證據清單（Part III）      ← 雙幣合併、標明歸屬、可被抽查
```

---

## 2. 設計決策

| 決策 | 選項 | 結論 | 理由 |
|------|------|------|------|
| 各幣分析展示方式 | `<details>` 摺疊 / fully expanded | **fully expanded** | 比賽 Final Report 是靜態文件，摺疊無意義；評審需要追溯推理鏈 |
| 各幣分析 vs 整合總結順序 | 先總結後分析 / 先分析後總結 | **先分析後總結** | 符合「事實→推論→結論」的遞進結構（比賽明確要求「有層次的推理」） |
| 舊版 `comparison_to_markdown()` | 移除 / 保留為 fallback | **保留為 fallback** | `ComparisonReport` 為 None 時（降級場景）仍需可用的輸出 |
| `comparison_report.md` 次要輸出 | 保留 / 移除 | **移除** | 已統一至 `report.md`，不再需要兩份獨立文件 |
| Web UI（`_render_comparison`） | 同步修改 / 不動 | **本次不動** | Web UI 已有 unified section 渲染，且有互動式 expand/collapse，本次只改 CLI/Markdown 交付物 |

---

## 3. 實作範圍

### 3.1 修改檔案

| 檔案 | 改動 | 風險 |
|------|------|------|
| `src/trustforge/comparison_contract.py` | `ComparisonReport.to_markdown()` 重寫為三段式 | 低：純 output formatting，不動資料流 |
| `src/trustforge/cli.py` | comparison 路徑改用 `ComparisonReport.to_markdown()` 作為主輸出 | 低：fallback 保留 |

### 3.2 不動的部分

- `pipeline.run_comparison()`：資料流不變
- `comparison_contract.py` 的 dataclass / `build_comparison_report()`：schema 不變
- `comparison_synthesis.py`：Bedrock 合成不變
- `web.py` / `_render_comparison()`：Web UI 不變
- DB schema：無 migration
- 既有 `comparison_to_markdown()`：保留，不刪除

---

## 4. 驗收條件

| # | 驗收項 | 方法 |
|---|--------|------|
| AC-1 | `ComparisonReport.to_markdown()` 輸出包含 `各幣詳細分析`（fully expanded，無 `<details>`） | unit test assertion |
| AC-2 | 輸出包含 `整合比較總結`（綜合結論 + 四面向 + 已知限制 + 推翻條件） | unit test assertion |
| AC-3 | 輸出包含 `合併證據清單`（雙幣標明歸屬） | unit test assertion |
| AC-4 | CLI `--type comparison` 主輸出 `report.md` 為新格式 | integration test / manual |
| AC-5 | `ComparisonReport` 為 None 時 CLI fallback 回舊版格式（不崩） | edge case test |
| AC-6 | 既有 `test_comparison_markdown.py` 全綠 | CI gate |
| AC-7 | 既有 `test_comparison.py` 全綠 | CI gate |
| AC-8 | Web UI comparison 頁面不受影響（`_render_comparison` 行為不變） | manual smoke test |

---

## 5. 實作步驟

| Step | 內容 | 估時 |
|------|------|------|
| S-1 | 開 issue、建分支 `feature/unified-comparison-report` | 10 min |
| S-2 | 修改 `ComparisonReport.to_markdown()`：移除 `<details>`、改三段式、加 bounds check | 30 min |
| S-3 | 修改 `cli.py`：comparison 改用新 `to_markdown()` 為主輸出、移除 `comparison_report.md` 二次輸出 | 15 min |
| S-4 | 更新 / 新增 tests：AC-1～AC-7 | 30 min |
| S-5 | 本地跑 full test suite 確認全綠 | 10 min |
| S-6 | PR 審查、合併 | — |

**總估時：~1.5 小時**

---

## 6. 風險與降級

| 風險 | 影響 | 降級措施 |
|------|------|----------|
| 新格式太長（兩份完整報告 + 總結） | 評審閱讀疲勞 | Part I 各幣分析加 `---` 分隔線清楚斷章 |
| `Report.to_markdown()` 內含的 `已知限制` 文字與 ComparisonReport 層級重複 | 混淆 | 以 heading level 區分（Report 用 h4，ComparisonReport 用 h2） |
| Bedrock 合成失敗時 `ComparisonReport.conclusion` 為規則層 fallback | 整合結論較機械化 | 可接受：規則層結論仍符合「誠實、不硬給」原則 |

---

## 7. 後續（不在本次範圍）

- Web UI 若需同步調整（如移除 unified section 的重複），另開 issue
- PDF / HTML 匯出同步跟進（目前 Markdown 即為比賽提交格式）
- `comparison_to_markdown()` 可在 CA-12 後正式 deprecate（需確認無其他消費者）
