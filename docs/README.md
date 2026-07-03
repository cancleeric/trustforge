# TrustForge docs/

> 本目錄為 TrustForge 的規劃與技術文件索引

| 文件 | 說明 |
|------|------|
| [WORLD-FIRST-MASTER-PLAN.md](WORLD-FIRST-MASTER-PLAN.md) | **三軸 master 世界第一開發計劃（總綱）**：Axis A 呈現層+資料誠實／Axis B 論文級深度／Axis C 大廠廣度，並管排優先序，誠實標「能做/roadmap/合規待確認/大廠護城河做不到」 |
| [COMPETITION.md](COMPETITION.md) | 命題規格（權威）、評分標準、時程、反作弊鐵則 |
| [COMPETITION-OFFICIAL.md](COMPETITION-OFFICIAL.md) | 官方附件全文歸檔 + 官方文件間衝突標記（如 AWS 模型約束，待 7/13 向窗口 Mars Li 確認） |
| [COMPLIANCE-CHECK.md](COMPLIANCE-CHECK.md) | 合規性對照（vs 官方命題文件）：5 能力/交付件/執行限制逐條核對 + 待決策 flag |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 三層管線與信任演算法設計 |
| [AWS-ARCHITECTURE.md](AWS-ARCHITECTURE.md) | AWS 服務架構（決賽簡報用）|
| [SUBMISSION-CHECKLIST.md](SUBMISSION-CHECKLIST.md) | 決賽交付清單 |
| [PROPOSAL.md](PROPOSAL.md) | 競賽企劃書：產品定位、Demo 敘事腳本、評審價值故事 |
| [DEV-PLAN.md](DEV-PLAN.md) | 開發計劃：分階段 Backlog、必做 vs 加分、里程碑 |
| [DEV-PLAN-REWRITE.md](DEV-PLAN-REWRITE.md) | 世界第一開發計劃重寫版（Axis A 全文）：老闆 LIVE 親測「離世界第一差很遠」後，針對產品呈現層 + 資料誠實層的分階段重寫計劃（呼應 `WORLD-FIRST-ANALYSIS.md` 演算法深度軸線之外的並行軸線） |
| [WORLD-FIRST-ANALYSIS.md](WORLD-FIRST-ANALYSIS.md) | 世界第一 gap 分析與策略（Axis B 研究）：四路研究（學術SOTA/crypto大廠/信任UX大廠/issue triage）+ W1-W4 roadmap + 決策日誌 |
| [PLAN-w2-wiring.md](PLAN-w2-wiring.md) | W2（動態來源信譽 TruthFinder/CRH）接線計劃：前提驗證數據 + 接線方案 + CEO 驗收標準 |
| [PLAN-corroboration.md](PLAN-corroboration.md) | `_corroboration` 深化設計方案（Issue #15 + #4）：停用詞過濾 + 方向閘 |
| [PLAN-axisC-snapshots.md](PLAN-axisC-snapshots.md) | Axis C #1 執行細案：多幣信任分快照寫入者（`fetch_scheduler.py --snapshot`）+ 首頁總覽正確讀路徑（單一預渲染 blob + TTL/single-flight，避開 P3 首頁多次讀 DynamoDB 的可用性坑） |
