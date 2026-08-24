# TrustForge Investigation Mode 現況分析報告

> 分析範圍：TrustForge repository、既有 roadmap／architecture／QA 文件、source/archive/dedup/corroboration 測試與既有記憶
>
> 分析日期：2026-08-24
>
> 工作分支：`feature/1224-unified-comparison-report`
>
> 基準 commit：`f13d5b79 fix(#1224): address semantic review findings`

## 1. 執行摘要

TrustForge 已經具備多來源分析的研究核心，包含 canonical source identity、source deduplication、supporting／contradicting evidence、cross-source corroboration、historical source／archive，以及三軌 learning system 的既有設計與部分實作。

目前主要缺口不是再增加一個 scoring 維度，而是把這些能力串成一條使用者可直接使用的完整流程：

```text
輸入 claim／URL
→ 拆解 claims
→ 找來源與歷史快照
→ canonicalize + deduplicate
→ 分辨獨立來源與轉載
→ 建立 supporting／contradicting evidence
→ 輸出 conclusion、unknowns 與可追溯報告
```

因此下一個產品增量建議定義為 **Investigation Mode**，先做窄而完整的 vertical slice，不重寫現有 Market Analysis Mode，也不先擴充大量新 connector。

## 2. 已具備的能力

### 2.1 研究與證據核心

現有 repo／文件已涵蓋下列方向：

- canonical source identity
- source deduplication
- supporting／contradicting stance
- corroboration 與 source scoring
- historical source／archive 的模組與測試
- comparison unified report format
- 三軌 learning system
- external source evidence independence

相關測試檔包含：

- `tests/test_source_archive.py`
- `tests/test_historical_sources.py`
- `tests/test_source_dedup_invariant.py`
- `tests/test_cross_source_divergence_calibration.py`
- `tests/test_issue_1224_unified_report.py`

### 2.2 文件與產品方向

既有文件已經把下列方向視為重要能力：

- 多來源可信度與 evidence map
- 外部來源的獨立性判斷
- historical replay／archive
- report contract 與 unified comparison output
- 三軌 learning／evaluation／feedback loop
- competition-ready 的可解釋報告

## 3. 主要缺口

### 3.1 研究核心尚未收斂成使用者流程

現在較接近「能產生高品質分析報告的研究核心」，尚未形成一個明確的 daily-use workflow。使用者需要一個入口，將 claim、URL 或新聞標題轉成可追溯的調查報告。

### 3.2 Historical source 尚未成為明確的 evidence contract

「原文仍在」、「官方 mirror」、「archive snapshot」、「search index」、「二手引用」與「推導結果」必須被區分。archive 或 snippet 不能被顯示成已找回的原始來源。

建議 evidence level：

| Level | 定義 |
|---|---|
| E0 | 目前可取得的原始來源 |
| E1 | 官方 mirror、官方文件或同一發布者的可驗證副本 |
| E2 | search index、RSS cache、snippet 或 metadata |
| E3 | 有明確引用來源的 secondary report |
| E4 | 多份資料推導出的 inference |

### 3.3 多篇文章不等於多個獨立來源

轉載、syndication、同一 wire report 的改寫，不能被直接累計成獨立 corroboration。需要 source relation 與 independence group，讓報告同時呈現文章數量與獨立來源群數量。

### 3.4 測試基線尚未綠燈

已取得的執行證據：

- `python -m compileall -q src`：通過
- `uv run pytest -q`：曾執行到約 70% 出現至少一個 failure，之後到至少 80% 仍未完成，程序被停止
- source/archive subset：在 180 秒內未完成
- `uv run pytest -x -vv`：長時間沒有產生可讀的失敗 traceback，後續停止

因此目前不能宣稱 pytest 全套通過。下一階段需要先隔離第一個 failure 與慢測試／阻塞來源，再讓 Investigation Mode 的相關測試保持 deterministic。

## 4. 產品判斷

建議新增：

```text
Investigation Mode
```

保留既有：

```text
Market Analysis Mode
```

第一版的核心承諾不是產生「真假」二元答案，而是清楚交代：

- 哪些 claims 被提出
- 每個 claim 的支持與反對證據
- 哪些來源是獨立的
- 原始來源是否仍可取得
- 如果原文消失，目前找到的是哪一級替代證據
- 哪些部分仍然 unresolved

## 5. 第一版必要輸出

最小輸出契約至少要包括：

- `claim`
- `evidence`
- `source`
- `source_relation`
- `evidence_level`
- `conclusion`
- `unknowns`
- `as_of`

每一個 claim 必須有 evidence，或明確標記為 `unverified`／`unresolved`；不能在沒有證據時生成肯定結論。

## 6. 不應立即做的事情

以下項目先不列入第一個 vertical slice：

- 大量新增搜尋／社群／付費資料 connector
- 複雜 graph visualization
- 全自動 self-improvement agent swarm
- 大規模模型訓練
- 重寫 Market Analysis Mode
- 以 search snippet 假裝已取得原始文章

## 7. 結論

TrustForge 下一個高價值增量是把已存在的 source、archive、dedup、corroboration 與 learning 設計，收斂成可使用、可引用、可說明未知事項的 Investigation Mode。

第一優先工程事項是讓測試基線可定位、可重現；第一優先產品事項是完成一條最小完整調查流程。兩者都應以小於 24 小時的 issues 拆解，並明確標註相依性，避免再產生大而無法驗收的 umbrella issue。
