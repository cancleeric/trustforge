# 歸檔工作單索引 — docs/archive/plans/

已執行完畢或被取代的一次性規劃/研究文件。移入本目錄前的判定依據見對應 PR 說明。
每筆：檔名 — 當初任務 — 結局。

| 檔案 | 當初任務 | 結局 |
|------|---------|------|
| [PLAN-w2-wiring.md](PLAN-w2-wiring.md) | W2（動態來源信譽 TruthFinder/CRH）舊接線計劃：前提驗證數據 + 接線方案 | **已被 `PLAN-w2-enable-final.md` 取代**（README 原文自述） |
| [PLAN-w2-enable-final.md](PLAN-w2-enable-final.md) | W2 最終啟用計劃：重新 grep 實證前置硬化、$0/確定性佐證、單一 PR 啟用範圍 | **已執行並上線**（`orchestrator.py:807 dynamic_reputation=True`） |
| [PLAN-data-density.md](PLAN-data-density.md) | 資料密度擴充三批次計劃：免費真源清單（RSS/鏈上/CryptoPanic/Etherscan/Reddit OAuth） | **第一/二批已上線**（21 源）；第三批需老闆申請 API key，已轉列 `WORLD-FIRST-MASTER-PLAN.md` §C 誠實資料卡/gated 清單持續追蹤 |
| [PLAN-source-branding.md](PLAN-source-branding.md) | 來源品牌化優化計劃：Evidence List slug → 品牌名 + 原廠 LOGO | **已上線 v0.5.9/v0.5.10** |
| [PLAN-corroboration.md](PLAN-corroboration.md) | `_corroboration` 深化設計方案（Issue #15 + #4）：停用詞過濾 + 方向閘 | **已實作上線**（commit `217e0f6`，Issue #16） |
| [PLAN-axisC-snapshots.md](PLAN-axisC-snapshots.md) | Axis C #1 執行細案：多幣信任分快照寫入者 + 首頁總覽正確讀路徑 | **已上線 v0.5.7** |
| [PLAN-W3-coordination-graph.md](PLAN-W3-coordination-graph.md) | W3 抗操縱升級到「協同行為圖」可行性評估 | 帳號級二部圖判定**資料卡**（連接器無 author 欄位），列 post-competition roadmap；建議的小改進（#16）已於 `PLAN-corroboration.md` 執行；結論已收錄 `WORLD-FIRST-MASTER-PLAN.md` §C |
| [PLAN-multicore-worldfirst.md](PLAN-multicore-worldfirst.md) | 多核心世界第一擴充計劃：信任雷達/跨幣排行/歷史 PIT/信譽榜 4 候選評估與排序 | **已整合進 `WORLD-FIRST-MASTER-PLAN.md` v3 Axis D**（README 原文自述）；候選 #1 歷史 PIT 持久化（PR #59）、#2 多維度信任雷達（PR #60）已上線；#3/#4 後續執行進度改由 master plan D 段追蹤 |
| [WORLD-FIRST-ANALYSIS.md](WORLD-FIRST-ANALYSIS.md) | 世界第一 gap 分析與策略（Axis B 研究）：四路研究 + W1-W4 roadmap + 決策日誌 | roadmap 已執行完畢（W1.5/W2/W3/W4 於 `WORLD-FIRST-MASTER-PLAN.md` §A 皆列 LIVE），現況已由 master plan 吸收追蹤；本文件轉為歷史研究依據保留 |
| [DEV-PLAN-REWRITE.md](DEV-PLAN-REWRITE.md) | 產品呈現層重寫計畫（Phase1-5）：拔 Dev Chrome、首頁不空白、預設真資料、視覺可信度、差異化 demo case、結果持久化/主題 | Phase1-3 已上線（PR #42/44/46，v0.5.4 起）；Phase4-5 因前後端分離 cutover（Issue #81，2026-07-06 方案 B 定案，SSR 凍結新功能）失去適用前提，整體歸檔 |
| [UXUI-ROUND-01.md](UXUI-ROUND-01.md) | UX/UI 批判稽核第 1 輪（12 輪計劃之一）：6 項弱點 + 建議優先修 3 項 | 稽核標的為舊 SSR（`web.py`）；前後端分離 React 重寫後多數項目已吸收（`PLAN-next-worldfirst-depth.md` §6 逐項 grep 核實：a11y/badge 圓角/mobile flex-wrap/時間人性化皆已在 React 版到位），僅 P1 版面重排（分析頁單欄 vs 並排）未核實，列作後續稽核起點但非現行待辦 |
