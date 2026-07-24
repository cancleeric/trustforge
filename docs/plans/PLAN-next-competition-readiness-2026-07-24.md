# 下一增量計劃 — 競賽就緒衝刺（2026-07-24，剩約 8 天）

> 作者：gray（CPO）。基準：develop（雙目標「三大新手脈絡模組 + UI/UX」已完成並 merge，AWS Bedrock live 驗證過）。
> 命題依據：`docs/competition/COMPETITION-OFFICIAL.md`、交付流程：`docs/competition/SUBMISSION-CHECKLIST.md`。
> 範圍：本文件只做規劃，不含程式碼改動。PR 一律打回 `develop`；合併 main + AWS 部署為專人事項（見 SUBMISSION-CHECKLIST Part B）。

## 0. 現況查證摘要

- **競賽核心是「單次 ≤15 分鐘 live 執行」**：抽題→分析→產出 4 交付件（Report/Evidence/Execution Log/Source）。評分 30% 主題切合度（多源整合/證據回溯/矛盾訊號/**信心校準**/限制說明）、25% 技術可行性、20% 商業應用性、15% 創意度、10% 完成度。
- 已查證：`peer-metrics`/`eco-link`（模組②③）目前**完全靠 fixture**（`load_peer_metrics_fixture` / `load_ecolink_fixtures`），**未被接進主分析流程**（`analysis_flow`/report 產生），是獨立側邊 dashboard，`illustrative: true` 誠實揭露已到位（`web.py:5154/5237`）。→ 這兩個模組是「加分展示面」而非評分主線的必要輸入。
- 已查證安全問題屬實：`/api/module-telemetry`（`web.py:7086-7107`）用 `dataclasses.asdict(rec)` 原樣序列化 `TelemetryRecord`，把 `evidence_ref`（設計意圖含測試名/CI URL/程式碼位置）與 `metadata` 原樣吐出，**無認證、無 rate limit**（對照同檔其他 `/api/*` 都有 `_check_status_rate_limit`，本端點沒有）。目前生產路徑 `evidence_ref` 恆空，但欄位本身洩漏面已開，且此端點在其餘所有 `/api/*` 中是唯一沒有限流保護的，公開 Live Demo 前必須收斂。對應 Issue #636、PR #635（已開但未合併，harper 裁定 blocked-by #636）。
- Header 導覽已查：主 nav（HERMES/Analyze/Compare/History/Sources/Costs）是 5 個核心頁籤（含底線 active 態），三大新手模組（資產脈絡查詢/EcoLink/Peer 比較）是**額外掛在 header 右側的小字連結**（`frontend/src/components/Header.tsx:82-90`），視覺層級明顯低於主 nav，且彼此、與主分析流程之間**沒有敘事串接**（例如分析完某幣種後不會被導引去看該幣的 peer/eco-link）。30 秒評審 demo 動線下，三模組容易被忽略或被誤認為無關 side feature。
- `#633`（校準模型 vs 20 個測試不同步）、`#634`（backfill 測試污染真實 `data/training/*.jsonl` + 缺 `training_data_dir`）：兩者皆已在 `fix/565-baseline-unblock` 用 `xfail(strict=False)` 解鎖 baseline，**不影響單次 live 執行**（污染只在「跑到那幾個特定 pytest 測試」時發生，不在正式 15 分鐘 agent 執行路徑上），**不阻塞 demo**。屬技術債，建議競賽後處理。
- `#637`（`AgentCoreLLMAdapter` env-gated stub 未實作，已 allowlist）：同樣是 tech-debt，不在生產路徑上啟用，非 demo 阻塞項。
- SUBMISSION-CHECKLIST 已有完整交付/公開化 SOP（Part A 轉 public 清理、Part B AWS 部署+封裝 `finale-submission.zip`），是既有骨架，本輪不重複規劃，只標記待執行時機（臨場前 24-48h）。

## 1. 候選項排序

| # | 項目 | 目標 | 範圍 | 工時 | Reviewer | 需 harper? | 可平行? |
|---|------|------|------|------|----------|------------|---------|
| 1 | **telemetry 安全修（#636）** | 消除公開未認證端點洩漏 `evidence_ref`/`metadata` 風險 | `module_telemetry.py`/`web.py`：白名單序列化（只回 `module_id/state/last_invoked_at/invocation_count/last_result/avg_latency_ms/last_latency_ms`），補 `_check_status_rate_limit`；regression test 驗證 `evidence_ref`/`metadata` 不出現在回應 body | ≤4h（單一 PR，不必拆） | codex + gray | **是（harper 為必要雙審之一）** | 是（與其他項無依賴，可最先/同時開工） |
| 2 | **Demo 敘事整合入口** | 讓三大新手模組在評審 30 秒動線內被看見、有串接故事，而非孤立小連結 | 前端：於 Analyze 結果頁或 Header 主 nav 新增「脈絡總覽」入口（可為單一頁籤，內含三模組摘要卡片 + 導向各自詳頁的 CTA）；不改後端 API | ≤10h（拆 UI 佈局 4h + 串接文案/i18n 3h + E2E 快照/可視化回歸測試 3h） | product-manager 起草 → qa-lead 驗收 E2E | 否 | 是（與項目1、3 無依賴） |
| 3 | **模組③真實資料源（TVL/TPS 連接器）** | 評估：**不建議本輪動工**。跨鏈口徑（不同鏈 TVL/TPS 定義、資料源不一致）風險高、工時不可控，且已查證 eco-link 目前不在評分主線（非接入 report 生成），fixture + `illustrative:true` 誠實揭露已符合「不硬給結論」精神，對 10 天內的評分收益低於風險 | 不排本輪；留待賽後或有餘裕時再議 | — | — | 否 | — |
| 4 | **交付就緒缺口**：README 可重現性驗證 + `finale-submission.zip` 打包演練 + `--offline` demo 可重現性 dry-run | 確保 8/1-8/2 現場不因交付流程手忙腳亂而失分（10% 完成度 + Demo 穩定度） | 依 `SUBMISSION-CHECKLIST.md` Part A/B 走一次 dry-run（非正式提交）：README 從零安裝跑一次、`scripts/package_finale_submission.py` 用既有 `out/artifacts/bedrock-live-run/` 跑一次驗證封裝不報錯 | ≤6h | qa-lead | 否 | 是（與項目1、2 平行） |
| 5 | **#633/#634/#637 技術債** | 不建議本輪處理 | 已 xfail 解鎖、不影響 demo，且 #634 修復需拆解 `_root()` 耦合（非小改），時間投報比低 | — | — | 否 | 延後至賽後 |

## 2. 若只能再做一件事：做哪個

**選項 1（telemetry 安全修 #636）。**

理由：
- 這是唯一有**明確安全裁定**（harper/CISO）且**明確阻塞公開 Live Demo 上線**的項目——命題要求「Live Demo 部署網址」+ repo 可能轉 public（`SUBMISSION-CHECKLIST.md` Part A），一旦公開，此端點對任何人可直接掃描到；即使目前欄位恆空，`evidence_ref` 的設計意圖是塞 CI URL/程式碼位置，一旦後續有人不小心接了真資料就是立即外洩，且無認證+無限流本身就是可被爬蟲放大打的攻擊面，跟評分無關但跟「能不能安全上線」直接掛鉤。
- 工時最小（≤4h）、風險最低、無跨模組依賴，且是唯一被 CEO 標註「安全修改→SOP 需 harper+gray 雙審+codex」的強制項——其餘項目都是「加分/體驗」性質，可以晚幾天做或不做也不影響及格線。

若資源允許做第二件，建議接續做**項目 2（Demo 敘事整合入口）**：完成度與商業應用性評分都吃「Demo 清楚穩定」與「可讀性/可採信性」，且三模組已完成卻未被有效展示是明顯的投報比落差（功能做完了但評審看不到）。

## 3. 需 CEO 裁示點

1. **是否核准項目 1（#636 安全修）立即開工**，並指派 harper 進行雙審（含 codex 複審）？這是本計劃唯一標記「安全」等級的項目。
2. **項目 3（模組③真實資料源）本輪不動工**是否核准？若 CEO 認為評審會實測 eco-link 真實性並扣分，需重新評估工時與風險（跨鏈口徑統一預估需另開規劃，非 ≤12h 可完成）。
3. **項目 4（交付就緒 dry-run）時機**：建議排在 7/30-31（提交前 1-2 天）執行，避免現在做完到 8/1 又有 code 變動導致重跑；請確認排程是否卡在這個時間點。
4. **是否需要決定 repo 轉 public 與否**（`SUBMISSION-CHECKLIST.md` Part A 前提，需先問窗口 Mars Li）——這件事會牽動項目 1 的急迫性（若確定維持 private + 加評審 collaborator，telemetry 端點暴露面縮小，但仍建議修，因為 SOP 已裁定為 must-fix，不因 public/private 而改變裁定）。
5. **#633/#634/#637 技術債延後至賽後**是否核准？目前僅靠 `xfail` 解鎖 baseline，非根治，需要 CEO/CDO 確認這個暫時狀態在賽後仍會被追蹤（避免遺忘）。
