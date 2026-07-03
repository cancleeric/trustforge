# TrustForge docs/

> 本目錄為 TrustForge 的規劃與技術文件索引

| 文件 | 說明 |
|------|------|
| [WORLD-FIRST-MASTER-PLAN.md](WORLD-FIRST-MASTER-PLAN.md) | **三軸 master 世界第一開發計劃（總綱，唯一權威，2026-07-03 全面稽核版）**：開頭「最終標準宣言」+ 對話關鍵決策整理 + A 逐項完成度表（grep+curl+pytest 實證）+ B 修正缺弱（含信任分效度驗證方法論、W2 未接線、商業級 UI 卡分支未上線、W3 判定翻案）+ C 擴充強化（資料密度/niche）+ D 三軸整合優先序 + 下一步 5 件事 |
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
| [PLAN-w2-wiring.md](PLAN-w2-wiring.md) | W2（動態來源信譽 TruthFinder/CRH）接線計劃：前提驗證數據 + 接線方案 + CEO 驗收標準（方案已審完，尚待執行） |
| [PLAN-corroboration.md](PLAN-corroboration.md) | `_corroboration` 深化設計方案（Issue #15 + #4）：停用詞過濾 + 方向閘 |
| [PLAN-axisC-snapshots.md](PLAN-axisC-snapshots.md) | Axis C #1 執行細案：多幣信任分快照寫入者（`fetch_scheduler.py --snapshot`）+ 首頁總覽正確讀路徑（單一預渲染 blob + TTL/single-flight）——**已上線 v0.5.7** |
| [PLAN-W3-coordination-graph.md](PLAN-W3-coordination-graph.md) | W3 抗操縱升級到「協同行為圖」可行性評估：grep 逐檔實證判定帳號級二部圖為**資料卡**（連接器無 author 欄位），改列 #16/#15 小改進 + 帳號級圖列 post-competition roadmap |
| [CONFORMAL-FINDING.md](CONFORMAL-FINDING.md) | W4 Split Conformal Prediction 研究發現：數學實作完成、JOINT coverage 達標，但代理訊號 pseudo-AUC≈0.49（等同隨機）——誠實負結果，不接進 production |
| [OPTIMIZATION-PLAN-weakness.md](OPTIMIZATION-PLAN-weakness.md) | CEO 兩路批判彙整（核心弱點分析 + UI code-grounded 審查）：Phase1 商業級 UI 快修清單 + Phase2 核心戰略抉擇（效度定位/資料密度/niche，待老闆拍板） |
| [QA-PLAN.md](QA-PLAN.md) | P-2026 生產 CTA 死互動事故根因分析 + 連結/CTA/表單旅程測試補強計劃（現有 950 測試全綠仍漏抓真實 UX bug 的教訓） |
