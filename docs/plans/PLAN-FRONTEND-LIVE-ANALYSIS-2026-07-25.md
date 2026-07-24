# 開發計劃：前端 HERMES UI 發起 Live Bedrock 分析

- 狀態：**待 CEO 審查，尚未執行**（本文件不含任何 code 變更）
- 撰寫：CPO（gray）
- Repo：`/Users/yinghaowang/HurricaneSoft/trustforge`，branch `develop`
- 前置文件：`docs/plans/PLAN-ENABLE-LOCAL-LIVE-BEDROCK-2026-07-24.md`（本機 env 開 live）

---

## 0. ⚠️ 重大發現：任務描述與現況不符（審查前必看）

Eric 派工描述「目前前端硬編碼離線（`analysis_flow.py` 寫死 `offline=True`）」——**此描述已過時**。逐行驗證後，後端早已改完，前端按鈕走的路徑與 Eric 認知的 token 路徑是**兩條完全不同的路**。本計劃的核心價值在於釐清這個分歧，避免 CTO 做白工。

### 0.1 `analysis_flow.py` 已不再寫死離線（file:line 證據）

| 位置 | 現況 | 證據 |
|---|---|---|
| `analysis_flow.py:856-864` `_stage_claim_extraction` | `with _bedrock_live_attempt(log) as live: client = BedrockClient(offline=not live)` | **動態判定**，非寫死 |
| `analysis_flow.py:113-220` `_bedrock_live_attempt` | context manager，讀 `web._bedrock_allowed()`（env `BEDROCK_MODEL_ID`）+ `budget_guard.daily_cap_exceeded()` + `narrative_model_priced()` + `try_reserve_request_budget()` 原子預留 | 完整護欄，與 `pipeline.run()` 同一套 |
| `analysis_flow.py:899-900` `_stage_evidence_assembly` | Step3 narrative 獨立再走一次 `_bedrock_live_attempt`（反映佇列等待後最新 cap 狀態） | 兩次真呼叫各自獨立閘 |
| `analysis_flow.py:192` 記帳 | `"offline": not live` | 花費如實進帳本 |

> 昨天的 `PLAN-ENABLE-LOCAL-LIVE-BEDROCK-2026-07-24.md`「發現一」講的 `analysis_flow.py:745` 寫死 `offline=True` **已不存在**——該份計劃描述的是改動前的舊狀態。那份計劃的「選項 B（後端改 analysis_flow）」**已經被做完了**。

### 0.2 前端按鈕走的是 register→flow，不是 on-demand `/api/analyze`

Eric 認知「live 模式需要 `X-Live-Token` header + `?live=1` query param」——這是 **on-demand `/api/analyze`** 路徑的需求（`web.py:309-315` `_compute_live_from_cfg`）。但前端 HERMES「送分析」按鈕**根本不走那條**：

| 前端動作 | 實際路徑 | 證據 |
|---|---|---|
| 按「送出分析」（預設） | `registerAnalysisQuestion(coin,mode,q)` → `POST /api/analysis-question` body `{coin,mode,question}` | `AnalyzePage.tsx:179`、`endpoints.ts:77-86` |
| `/api/analysis-question` handler | 只接受 `{coin,mode,question}`，**多欄位直接 400**；不吃 token、不吃 live | `web.py:6290` `if set(payload) - {"coin","mode","question"}: return 400` |
| `submit_manual(coin, mode, question)` | 三參數，**不吃 live/token** | `analysis_flow.py:447` |
| worker 執行 stages | `_bedrock_live_attempt` 動態讀 env + budget_guard 判定 live | `analysis_flow.py:858` |

→ **register→flow 路徑的 live 完全由後端 env 決定，與 request 帶不帶 token 無關。**

### 0.3 結論：前端零改動即可 live（前提：後端 env 設好）

只要 `PLAN-ENABLE-LOCAL-LIVE-BEDROCK-2026-07-24.md` 第 1.4 節的 env 清單套到 web.py process（`BEDROCK_MODEL_ID` / `AWS_REGION=us-east-1` / `BEDROCK_HAIKU_MODEL_ID` / `TRUSTFORGE_BEDROCK_DAILY_USD_CAP=10` 等），**前端 HERMES 按鈕點下去就會自動走真 Bedrock**，不需要改任何前端程式碼、不需要 token 進瀏覽器。

**CISO 正在審的「token 進瀏覽器」方案完全不影響這條路徑**——因為 register→flow 路徑從頭到尾不經過 `_compute_live_from_cfg` / `X-Live-Token` 那套閘。CISO 方案只影響 on-demand `/api/analyze`（目前只有 `sample=1` fixture 模式在用，見 `AnalyzePage.tsx:134`）。

---

## 1. 兩條路徑對照（決策依據）

| 維度 | 路徑 A：register→flow（前端現況） | 路徑 B：on-demand `/api/analyze` |
|---|---|---|
| 前端觸發 | `POST /api/analysis-question`（`AnalyzePage.tsx:179`） | `GET /api/analyze`（目前僅 `sample=1` 用，line 134） |
| live 判定 | 後端 `_bedrock_live_attempt` 讀 env（`analysis_flow.py:858`） | request 帶 `?live=1` + `X-Live-Token`（`web.py:309-315`） |
| 前端要改嗎 | **不用**（env 開了就 live） | 要（`AnalyzeParams` 加 live/token、UI 加開關、改觸發邏輯） |
| CISO token 方案影響 | **無**（路徑不吃 token） | 完全依賴（方案 D/A 決定前端怎麼帶 token） |
| token 進瀏覽器風險 | 無 | 有（除非 CISO 採方案 D vite proxy 注入） |
| 5 階段視覺化 | 有（durable 佇列、stage drilldown、可重啟 recover） | 無（同步一次性結果） |
| 既有人手投資 | 已上線、已測試、前端已串好 | 需新建觸發鏈 |
| 成本護欄 | `budget_guard`（daily cap + 原子預留 + unpriced model 保護）✅ | `budget_guard` ✅（同一套） |

### 建議：採路徑 A

理由：
1. 前端按鈕**已經走這條**，後端 live **已完工**，零改動即可驗收。
2. CISO token 方案的不確定性**完全不阻塞**——不需要等 CISO 結論就能上。
3. register→flow 是 durable 設計（snapshot 隔離、佇列、可重啟 recover、5 階段視覺化），是 HERMES 工作區的核心價值；硬切到 on-demand 會降級體驗。
4. token 進瀏覽器的資安風險在路徑 A **不存在**。

以下第 2-5 節按**路徑 A**撰寫（最小改動）。第 6 節附帶「若 CEO 堅持路徑 B」的改動範圍供裁示。

---

## 2. 路徑 A 改造範圍（建議方案）

### 2.1 後端：無需改 code

`analysis_flow.py` 的 `_bedrock_live_attempt`（line 113-220）已完整接好 `budget_guard`，行為與 `pipeline.run()` 放行真 Bedrock 前的檢查一致。**唯一動作是設 env**（屬 `PLAN-ENABLE-LOCAL-LIVE-BEDROCK-2026-07-24.md` 範圍，非本計劃）。

### 2.2 前端：改 0 個檔案即可 live（env 開了就生效）

若 CEO 只要「能 live」，前端**完全不用改**。以下為可選的 UX 增強（非必要）：

| 可選增強 | 動機 | 改動檔案 | 預估工時 |
|---|---|---|---|
| 顯示 LIVE 徽章 | 讓使用者知道這次是真 Bedrock（非離線罐頭） | `AnalysisReportView.tsx` 讀 `execution_log` 的 `bedrock.complete` event `llm_active`（`analysis_flow.py:863`）；或讀 `report` 既有 `total_cost_usd > 0`（`types.ts:472-473`） | 0.5 人日 |
| 活讀 `/api/status` 的 `bedrock_capable`/`live_token_set` | TopBar 顯示「LIVE UPLINK」是否真上線（目前 `HermesTopBar.tsx:65` 是靜態文案） | `HermesTopBar.tsx` + `getStatus()`（`endpoints.ts:270`，回傳值已有 `bedrock_model_id_set`，`types.ts:535`） | 0.5 人日 |

> 這兩項是「錦上添花」，不阻塞 live 功能本身。

### 2.3 工時總估

| 項目 | 工時 |
|---|---|
| 後端 code 改動 | 0（已完工） |
| 前端 code 改動（最小：零改動） | 0 |
| 前端 UX 增強（可選：LIVE 徽章 + TopBar 活讀） | 1 人日 |
| env 設定 + web.py 重啟（屬前置 PLAN 範圍） | 0.5 人日 |
| 驗收（第 4 節） | 0.5 人日 |

---

## 3. CISO 安全方案相容性（路徑 A 視角）

| CISO 可能方案 | 對路徑 A 的影響 |
|---|---|
| 方案 D（vite proxy 注入 token） | **無影響**——路徑 A 不吃 token，proxy 注入的 header 會被 `/api/analysis-question` handler 忽略（它不讀 `X-Live-Token`） |
| 方案 A（後端 loopback 信任） | **無影響**——路徑 A 的 live 判定在 `_bedrock_live_attempt` 讀 env，與 loopback 信任機制無關 |
| 任何其他方案 | **無影響**——只要不動 `analysis_flow.py` 的 `_bedrock_live_attempt` / `budget_guard`，路徑 A 行為不變 |

→ **路徑 A 不需要等 CISO 結論。** CISO 方案只決定「on-demand `/api/analyze` 要不要開放給前端」，與 HERMES 按鈕的 register→flow 路徑正交。

---

## 4. 測試計劃（CEO 親測）

### 4.1 前置：env 設定 + web.py 重啟

沿用 `PLAN-ENABLE-LOCAL-LIVE-BEDROCK-2026-07-24.md` 第 1.4 節 env 清單 + 第 2 節重啟步驟。重啟後先 curl 確認總閘：

```bash
curl -s http://localhost:8799/api/status | jq '{bedrock_capable: .data.bedrock_capable, live_token_set: .data.live_token_set, bedrock_model_id_set: .data.bedrock_model_id_set}'
# 預期：三者皆 true（bedrock_capable 反映 env BEDROCK_MODEL_ID 已設）
```

### 4.2 真實前端 live 分析（路徑 A 主驗收）

1. 開 `http://localhost:4174/analyze`（vite dev，proxy `/api` → 8799）
2. 選 BTC、multi_source、送出「分析 BTC 近期市場狀況」
3. 觀察 5 階段跑完（source_ingestion → claim_extraction → trust_reasoning → evidence_assembly → report_delivery）

**確認是真 Bedrock（非 offline fallback）的三個獨立訊號**：

| 訊號 | 怎麼驗 | 預期（live） | 預期（offline） |
|---|---|---|---|
| A. 帳本花費 | `curl -s http://localhost:8799/api/budget-governance \| jq '.data.spent_today_usd'` | > 0 且遞增 | 0 |
| B. execution_log `llm_active` | 前端 devtools Network → `/api/analysis-job` 回應 → `result.execution_log` 找 `tool:"bedrock.complete"` 的 `params.llm_active` | `true` | `false` |
| C. 敘事文字品質 | 報告 `report.facts`/`inferences` 是否為模型生成的自然語句（非罐頭「本次未執行線上模型生成」） | 自然語句 | 含「本次未執行線上模型生成；結論由結構化規則與可追溯證據產生」（見 fixture `live-analyze.json` 的 offline inferences） |

> 訊號 B 是最硬的證據：`analysis_flow.py:863` `log.record("bedrock.complete", params={...,"llm_active": llm_active})` 如實記錄。

### 4.3 成本護欄驗證（daily cap）

| 驗證 | 做法 | 預期 |
|---|---|---|
| cap 生效 | 跑數次 live 分析累積接近 $10，再送一次 | 最後一次 `_bedrock_live_attempt` 的 `daily_cap_exceeded()` 回 True → `live=False` → 該次自動降離線（`analysis_flow.py:154`），不報錯、不超支 |
| 帳本記帳 | 每次真呼叫後 `/api/budget-governance` 的 `spent_today_usd` 遞增 | `analysis_flow.py:186-203` `append_run` 記帳；失敗落 `record_unledgered_spend` |
| 並行 race | （選做）同時開兩個 tab 送分析 | `try_reserve_request_budget` 原子預留防 TOCTOU（`analysis_flow.py:157`） |

### 4.4 回歸：sample 模式不受影響

```bash
# sample=1 走 on-demand /api/analyze 讀 fixture，$0，不受 env 影響
curl -s "http://localhost:8799/api/analyze?type=multi_source&coin=BTC&q=test&sample=1" | jq '.ok'
# 預期：true，total_cost_usd: 0
```

---

## 5. 回滾方案

### 5.1 快速回到全離線（不需改 code）

```bash
# 移除 live env 重啟 web.py 即可
scripts/trustforge_control.sh stop
unset BEDROCK_MODEL_ID  # 或整組 env 清空
scripts/trustforge_control.sh start
```

移除 `BEDROCK_MODEL_ID` 後：
- `_bedrock_allowed()`（`web.py:376`）短路回 `False`
- `_bedrock_live_attempt`（`analysis_flow.py:152-153`）判定 `live=False`
- 所有 stage 降離線，$0

### 5.2 緊急止血（不重啟）

若 live 進行中發現異常花費，且不想中斷服務：
- `budget_guard` 的 daily cap（`TRUSTFORGE_BEDROCK_DAILY_USD_CAP=10`）會自動在達上限後把後續請求降離線（fail-closed，`analysis_flow.py:154`）
- 可臨時調降 cap env（但需重啟生效）；或不動——cap 本身就是回滾機制

### 5.3 前端零改動的回滾保證

因為路徑 A 前端沒改任何 code，「回到 offline」純粹是後端 env 操作，**沒有前端版控回滾問題**。

---

## 6. 附錄：若 CEO 堅持路徑 B（on-demand `/api/analyze`）

> ⚠️ 不建議。列出來僅供裁示對照。會破壞既有 register→flow 架構，且完全依賴 CISO 方案。

### 6.1 後端改動（無）

`/api/analyze` 的 live 路徑早已存在（`web.py:6484` `_handle_api_analyze` → `_do_analyze` → `pipeline.run()`，live 閘在 `_compute_live_from_cfg` line 309-315）。

### 6.2 前端改動（依 CISO 方案分兩支）

| CISO 方案 | 前端改動 | 檔案 |
|---|---|---|
| **D（vite proxy 注入 token）** | `AnalyzeParams` 加 `live?: '1'`；`AnalyzePage` 觸發改呼叫 `getAnalyze({...,live:'1'})`（token 由 vite proxy 自動加 header，前端**不碰 token**） | `endpoints.ts:49-56`、`AnalyzePage.tsx:179`、`vite.config.ts`（proxy header） |
| **A（後端 loopback 信任）** | 同上 + 前端不用帶 token（後端對 127.0.0.1 來源放行 live） | `endpoints.ts`、`AnalyzePage.tsx`、`web.py`（加 loopback trust 分支——**屬 CISO 範圍**） |

### 6.3 工時（路徑 B）

| 項目 | 工時 |
|---|---|
| 等 CISO 方案定案 | 未知（阻塞） |
| 前端改動（方案 D） | 1.5 人日 |
| 前端改動（方案 A，含後端 loopback） | 2.5 人日 |
| 破壞既有 register→flow 體驗的風險 | 高（失去 5 階段視覺化、durable 重啟） |

---

## 待 CEO 決定事項

1. **是否接受第 0 節的事實澄清**（後端已改完、前端按鈕走 register→flow 不吃 token）？
2. **是否採路徑 A**（env 開了就 live，前端零改動，不等 CISO）？
3. 若採路徑 A，是否要做第 2.2 節的可選 UX 增強（LIVE 徽章 + TopBar 活讀）？
4. 若堅持路徑 B，是否接受「等 CISO 方案 + 破壞既有 flow 體驗」的代價？
