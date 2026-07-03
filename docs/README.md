# TrustForge docs/

> 本目錄為 TrustForge 的規劃與技術文件索引

| 文件 | 說明 |
|------|------|
| [WORLD-FIRST-MASTER-PLAN.md](WORLD-FIRST-MASTER-PLAN.md) | **三軸+Axis D master 世界第一開發計劃（總綱，唯一權威，v3 2026-07-03 精簡權威版）**：最終標準宣言（多護城核心疊起來）+ A LIVE 現況表（grep/curl/pytest 逐項實證，W2 已啟用+21 源資料密度）+ B 三軸現況＋新 Axis D 多核心擴充（信任雷達/跨幣排行/歷史 PIT/信譽榜排序）+ C 誠實資料卡/gated 清單 + D 下一步連環疊核心序 |
| [COMPETITION.md](COMPETITION.md) | 命題規格（權威）、評分標準、時程、反作弊鐵則 |
| [COMPETITION-OFFICIAL.md](COMPETITION-OFFICIAL.md) | 官方附件全文歸檔 + 官方文件間衝突標記（如 AWS 模型約束，待 7/13 向窗口 Mars Li 確認） |
| [COMPLIANCE-CHECK.md](COMPLIANCE-CHECK.md) | 合規性對照（vs 官方命題文件）：5 能力/交付件/執行限制逐條核對 + 待決策 flag |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 三層管線與信任演算法設計 |
| [AWS-ARCHITECTURE.md](AWS-ARCHITECTURE.md) | AWS 服務架構（決賽簡報用）|
| [SUBMISSION-CHECKLIST.md](SUBMISSION-CHECKLIST.md) | 決賽交付清單 |
| [PROPOSAL.md](PROPOSAL.md) | 競賽企劃書：產品定位、Demo 敘事腳本、評審價值故事 |
| [DEV-PLAN.md](DEV-PLAN.md) | 開發計劃：分階段 Backlog、必做 vs 加分、里程碑 |
| [DEV-PLAN-REWRITE.md](DEV-PLAN-REWRITE.md) | 世界第一開發計劃重寫版（Axis A 全文）：老闆 LIVE 親測「離世界第一差很遠」後，針對產品呈現層 + 資料誠實層的分階段重寫計劃（P1-P5，呼應 `WORLD-FIRST-ANALYSIS.md` 演算法深度軸線之外的並行軸線） |
| [WORLD-FIRST-ANALYSIS.md](WORLD-FIRST-ANALYSIS.md) | 世界第一 gap 分析與策略（Axis B 研究）：四路研究（學術SOTA/crypto大廠/信任UX大廠/issue triage）+ W1-W4 roadmap + 決策日誌 |
| [PLAN-w2-wiring.md](PLAN-w2-wiring.md) | W2（動態來源信譽 TruthFinder/CRH）接線計劃：前提驗證數據 + 接線方案 + CEO 驗收標準（舊版接線計劃，已被 PLAN-w2-enable-final.md 取代並執行） |
| [PLAN-w2-enable-final.md](PLAN-w2-enable-final.md) | W2 最終啟用計劃：重新 grep 實證前置硬化已完成、$0/確定性佐證、單一 PR 啟用範圍——**已照此執行並上線（`orchestrator.py:807`）** |
| [PLAN-data-density.md](PLAN-data-density.md) | 資料密度擴充計劃（老闆決策先做這個再做 W2）：現況源盤點 + 免費真源清單（RSS/鏈上/CryptoPanic/Etherscan/Reddit OAuth）+ 三批次排程——**第一/二批已上線（21 源），第三批待老闆申請 key** |
| [PLAN-source-branding.md](PLAN-source-branding.md) | 來源品牌化優化計劃：Evidence List slug → 品牌名+原廠 LOGO——**已上線 v0.5.9/v0.5.10** |
| [PLAN-corroboration.md](PLAN-corroboration.md) | `_corroboration` 深化設計方案（Issue #15 + #4）：停用詞過濾 + 方向閘 |
| [PLAN-axisC-snapshots.md](PLAN-axisC-snapshots.md) | Axis C #1 執行細案：多幣信任分快照寫入者（`fetch_scheduler.py --snapshot`）+ 首頁總覽正確讀路徑（單一預渲染 blob + TTL/single-flight）——**已上線 v0.5.7** |
| [PLAN-W3-coordination-graph.md](PLAN-W3-coordination-graph.md) | W3 抗操縱升級到「協同行為圖」可行性評估：grep 逐檔實證判定帳號級二部圖為**資料卡**（連接器無 author 欄位），改列 #16/#15 小改進 + 帳號級圖列 post-competition roadmap |
| [CONFORMAL-FINDING.md](CONFORMAL-FINDING.md) | W4 Split Conformal Prediction 研究發現：數學實作完成、JOINT coverage 達標，但代理訊號 pseudo-AUC≈0.49（等同隨機）——誠實負結果，不接進 production |
| [OPTIMIZATION-PLAN-weakness.md](OPTIMIZATION-PLAN-weakness.md) | CEO 兩路批判彙整（核心弱點分析 + UI code-grounded 審查）：Phase1 商業級 UI 快修清單 + Phase2 核心戰略抉擇（效度定位/資料密度/niche，待老闆拍板） |
| [QA-PLAN.md](QA-PLAN.md) | P-2026 生產 CTA 死互動事故根因分析 + 連結/CTA/表單旅程測試補強計劃（現有 950 測試全綠仍漏抓真實 UX bug 的教訓） |
| [PLAN-multicore-worldfirst.md](PLAN-multicore-worldfirst.md) | 多核心世界第一擴充計劃（老闆定調：單一信任分撐不起世界第一，要多護城核心疊起來）：grep 實證 Axis C 快照/W2 reputation_trace/21 資料源 kind 分佈現有基礎 → 4 個候選新核心（信任雷達/跨幣排行/歷史 PIT/信譽榜）評估 + 排序 + 建議連環疊前 3 個——**已整合進 WORLD-FIRST-MASTER-PLAN.md v3 Axis D** |
