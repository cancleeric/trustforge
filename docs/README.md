# TrustForge docs/

> 本目錄為 TrustForge 的規劃與技術文件索引。文件依生命週期/用途分區：
> `competition/`（命題與交付規範）、`architecture/`（架構決策）、
> `plans/`（進行中的活計劃）、`qa/`（測試與研究發現）、`design/`（既有設計資產）、
> `archive/plans/`（已執行完/被取代的一次性工作單，索引見該目錄 README）。
>
> **規矩**：`PLAN-*` 工作單執行完畢或被取代，當輪移入 `archive/plans/`，並在
> 該目錄的 `README.md` 索引補一行（檔名 + 當初任務 + 結局：已上線版本/被誰取代）。

---

## competition/ — 命題與交付規範

| 文件 | 說明 |
|------|------|
| [competition/COMPETITION.md](competition/COMPETITION.md) | 命題規格（權威）、評分標準、時程、反作弊鐵則 |
| [competition/COMPETITION-OFFICIAL.md](competition/COMPETITION-OFFICIAL.md) | 官方附件全文歸檔 + 官方文件間衝突標記（如 AWS 模型約束，待 7/13 向窗口 Mars Li 確認） |
| [competition/COMPLIANCE-CHECK.md](competition/COMPLIANCE-CHECK.md) | 合規性對照（vs 官方命題文件）：5 能力/交付件/執行限制逐條核對 + 待決策 flag |
| [competition/SUBMISSION-CHECKLIST.md](competition/SUBMISSION-CHECKLIST.md) | 決賽交付清單 |
| [competition/PROPOSAL.md](competition/PROPOSAL.md) | 競賽企劃書：產品定位、Demo 敘事腳本、評審價值故事 |
| [competition/TEAM.md](competition/TEAM.md) | 團隊/角色分工 |

## architecture/ — 架構決策

| 文件 | 說明 |
|------|------|
| [architecture/ARCHITECTURE.md](architecture/ARCHITECTURE.md) | 三層管線與信任演算法設計 |
| [architecture/AWS-ARCHITECTURE.md](architecture/AWS-ARCHITECTURE.md) | AWS 服務架構（決賽簡報用），含前後端分離對外拓樸 |
| [architecture/BACKFILL-SYSTEM.md](architecture/BACKFILL-SYSTEM.md) | 歷史回填系統：5年 OHLCV 逐日 replay，三層啟停控制 |
| [architecture/OBSERVABILITY-API.md](architecture/OBSERVABILITY-API.md) | 觀測層 API 端點文件：budget-governance / improvement / alerts / backfill |
| [architecture/PLAN-frontend-backend-split.md](architecture/PLAN-frontend-backend-split.md) | 前後端分離架構＋遷移計劃：SSR零-JS → React+Vite+TS+Tailwind。**方案 B 已定案（Issue #81，2026-07-06）並上線 v0.6.1**：web.py 降為純 `/api/*` API，React SPA 獨立部署，SSR 凍結新功能僅保留 `cutover_switch.sh legacy` 緊急回滾路徑 |
| [architecture/TRUTH-DISCOVERY-EVALUATION-2026-07-13.md](architecture/TRUTH-DISCOVERY-EVALUATION-2026-07-13.md) | Truth-discovery 統計收斂法補強評估（#179）：CRH/Dawid-Skene/CATD/LTM 四方法對照表，結論 Dawid-Skene EM 最適合當 Bedrock 離線 fallback |
| [architecture/CONFIDENCE-CONVERGENCE-REPORT-2026-07-13.md](architecture/CONFIDENCE-CONVERGENCE-REPORT-2026-07-13.md) | 信心值收斂技術報告：現況 `_dynamic_reputation` 架構＋離線 no-op 問題（#178）＋ Dawid-Skene EM 平行 fallback 解法＋邊界聲明（不涉及 conformal/預測力，#167 範圍） |

## plans/ — 進行中的活計劃

| 文件 | 說明 |
|------|------|
| [plans/WORLD-FIRST-MASTER-PLAN.md](plans/WORLD-FIRST-MASTER-PLAN.md) | **三軸+Axis D master 世界第一開發計劃（總綱，唯一權威，v3 2026-07-03 精簡權威版）**：最終標準宣言（多護城核心疊起來）+ A LIVE 現況表（grep/curl/pytest 逐項實證）+ B 三軸現況＋新 Axis D 多核心擴充 + C 誠實資料卡/gated 清單 + D 下一步連環疊核心序。仍有未執行項（商業級 UI 4 項狀態需覆核、Axis D #3/#4 等），持續更新中 |
| [plans/DEV-PLAN.md](plans/DEV-PLAN.md) | 開發計劃：分階段 Backlog、必做 vs 加分、里程碑。**仍有未執行 backlog**：P0-4 HOYA BIT 企業數據連接器，等 7/13 工作坊取得 API 規格後才能接 |
| [plans/CEO-ISSUE-PR-DEVELOPMENT-SWEEP-2026-07-17.md](plans/CEO-ISSUE-PR-DEVELOPMENT-SWEEP-2026-07-17.md) | CEO issue/PR 處理計劃：#218 release gate、#207/#209/#215 evidence 同步；每小時 sweep 只產生待互動式 CEO 審查的建議，不自行派工、merge 或 deploy |
| [plans/OPTIMIZATION-PLAN-weakness.md](plans/OPTIMIZATION-PLAN-weakness.md) | CEO 兩路批判彙整（核心弱點分析 + UI code-grounded 審查）：Phase1 商業級 UI 快修清單（**注意：清單所列 4 項已在前後端分離 React 重寫中獨立解決，這部分內容已過時，見 `PLAN-next-worldfirst-depth.md` §6 housekeeping 記錄**）+ Phase2 核心戰略抉擇（效度定位/資料密度/niche）——**Phase2 仍待老闆拍板，本文件未關閉** |
| [plans/PLAN-next-worldfirst-depth.md](plans/PLAN-next-worldfirst-depth.md) | 下一步世界第一深度優化計劃（非-gated 專案）：#13 分歧來源去重、#20 主題切換已執行；**仍有未執行項**：#3 跨幣操縱排行、#15 burst 偵測重新設計（僅排資料探索驗證，未排實作）；§6 記錄 `fix/ui-commercial` 分支已過時、UXUI-ROUND-01.md 稽核項目多數已被 React 重寫吸收等 housekeeping 發現 |
| [plans/DAWID-SKENE-CONFIDENCE-PLAN-2026-07-13.md](plans/DAWID-SKENE-CONFIDENCE-PLAN-2026-07-13.md) | Dawid-Skene EM 信心收斂開發擴充計劃：資料結構/介面整合（`stance_fn is None` 分支）/測試策略/分階段工時（約 5 天）；明確排除 conformal/預測力範圍 |
| [plans/PLAN-2026-07-21-ui-optimization.md](plans/PLAN-2026-07-21-ui-optimization.md) | UI/UX 優化現況版：#356–#360 完成／更正、#539–#542 與 #361/#362 剩餘工作、≤12h 相依性與驗收標準 |

## qa/ — 測試與研究發現

| 文件 | 說明 |
|------|------|
| [qa/QA-PLAN.md](qa/QA-PLAN.md) | P-2026 生產 CTA 死互動事故根因分析 + 連結/CTA/表單旅程測試補強計劃（現有測試全綠仍漏抓真實 UX bug 的教訓） |
| [qa/STRESS-TEST.md](qa/STRESS-TEST.md) | P0-5 壓測結果（5 幣 × 3 題型矩陣，`scripts/stress_test.py` 產出） |
| [qa/CONFORMAL-FINDING.md](qa/CONFORMAL-FINDING.md) | W4 Split Conformal Prediction 研究發現：數學實作完成、JOINT coverage 達標，但代理訊號 pseudo-AUC≈0.49（等同隨機）——誠實負結果，不接進 production |
| [qa/modelhub-integration-351.md](qa/modelhub-integration-351.md) | ModelHub client／候選編排 #351 的驗證、審查證據與未執行邊界 |

## reports/ — 事故與調查報告

| 文件 | 說明 |
|------|------|
| [reports/REPORT-2026-07-23-hardcoded-paths-portability.md](reports/REPORT-2026-07-23-hardcoded-paths-portability.md) | 本機排程硬編碼路徑事故：#518／PR #536 根治、審查證據、未執行真機驗收與剩餘 freshness 告警風險 |

## handoff/ — 交接文件

| 文件 | 說明 |
|------|------|
| [handoff/2026-07-22-modelhub-integration-handoff.md](handoff/2026-07-22-modelhub-integration-handoff.md) | ModelHub #351 七段式現況、操作契約與剩餘工作 |

## design/

既有設計資產子目錄，原地不動（見 [design/](design/)）。

## archive/plans/ — 已歸檔工作單

已執行完畢或被取代的一次性 `PLAN-*`/研究文件，索引見 [archive/plans/README.md](archive/plans/README.md)。

## 2026-07-20 additions

- [External Sources And Evidence Independence Plan](plans/EXTERNAL-SOURCES-EVIDENCE-INDEPENDENCE-PLAN-2026-07-20.md)
- [RAG Model Gate Decision](decisions/RAG-MODEL-GATE-DECISION-2026-07-20.md)
