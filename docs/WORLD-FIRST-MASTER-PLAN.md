# TrustForge — 世界第一 Master 開發計劃（三軸總綱）

> 作者：CPO（gray）｜建立：2026-07-02
> 依據：老闆指示三軸並管總綱；`docs/DEV-PLAN-REWRITE.md`（Axis A 全文）、
> `docs/WORLD-FIRST-ANALYSIS.md`（Axis B 研究/決策日誌）、`docs/PLAN-w2-wiring.md`
> （W2 接線細節）、`docs/COMPLIANCE-CHECK.md`／`docs/COMPETITION-OFFICIAL.md`
> （合規紅線）、`ROADMAP.md`（黑客松里程碑）、code grounding（`src/trustforge/`
> 逐檔核實，見各節「證據」）。
> 定位：**本文件是總綱、不重寫細節**——Axis A 的逐 Phase 施作細節仍以
> `docs/DEV-PLAN-REWRITE.md` 為準、W2 接線細節仍以 `docs/PLAN-w2-wiring.md` 為準，
> 本文件負責「三軸放在一起看、排優先序、標誠實進度、抓合規風險」。

---

## 0. 對話關鍵決策與現況整理（給後續讀者的快速上下文）

### 0.1 關鍵決策（時間序）
1. **老闆親測 LIVE 版否定「一點都不專」** → 觸發 Axis A 呈現層重寫計劃（`DEV-PLAN-REWRITE.md`），5 階段拆分。
2. **老闆「重新分問」否定「核心引擎已到頂」的自滿結論** → 觸發 Axis B 四路研究（學術 SOTA / crypto 大廠 / 信任UX大廠 / issue triage），拍板軸線＝**演算法深度 × 可解釋性**（`WORLD-FIRST-ANALYSIS.md`）。
3. **官方錄取信硬約束確認**：「僅限使用 AWS 服務提供之基礎模型」——曾一度誤判為「非官方自我約束」，後由老闆轉來官方原文更正（見 `WORLD-FIRST-ANALYSIS.md` 附錄 A）。**`ROADMAP.md` 明文「模型合規：全程 Bedrock 直連，禁用其他供應商」**，`COMPLIANCE-CHECK.md` flag #1 仍標記「命題文件 vs 錄取信兩種解讀不一致，7/13 向窗口 Mars Li 確認定案」——**尚未拍板，仍是待確認風險**。
4. **W1.5 已上線**：因上述合規約束，跨源佐證的語意層改用 **Bedrock Haiku 逐對 stance 分類器**（AWS FM 合規），而非本地開源模型——`src/trustforge/agent/orchestrator.py:519,748` 已接 `stance_client`，`trust/scoring.py::_corroboration`（L693-729）為「token 重疊+停用詞+方向閘」前置閘 **+** stance_fn 二次確認矛盾，**不是純 LLM prompt，也不是純開源 NLI**，是混合式。
5. **W2（動態來源信譽）已完整開發（PR #29，284 測試綠、4 輪 codex 對抗審）但預設關**——`orchestrator.py:363` 呼叫 `score()` 未帶 `dynamic_reputation=True`，`docs/PLAN-w2-wiring.md`（2026-07-01）已完成前提驗證+接線方案+驗收標準，**是現成可執行的計劃，不用重新設計**。
6. **Phase2（Axis A 預設真資料）前置已過**：v0.5.5「真資料管線健康+SSRF加固」已上生產（`release/v0.5.5`），`feat/phase2-real-default` 分支存在，**執行中，尚未完成**（健康 gate 過了，預設切換+日期修正+缺源優雅處理三件事同輪，尚未全上）。
7. **gated-LLM access-token + 預算上限**：`TRUSTFORGE_LIVE_TOKEN`（`web.py:46`）與 `COST_BUDGET_USD`（`web.py:48`）機制**已存在**（token gate 防濫用、預算門檻卡片轉紅告警），但 Phase2 把「真資料·$0」變預設後流量型態改變，**具體門檻數值/是否需要提高保護層級待老闆確認**，非機制不存在。

### 0.2 目前 LIVE 版本
**v0.5.5**（`release/v0.5.5`，2026-07-02）。近期版本序：v0.5.3 成本會計 → v0.5.4 Axis A Phase1（首頁不空白+header清爽）→ v0.5.5 Axis A Phase2 前置（真資料管線健康+SSRF加固）。

### 0.3 三軸現況一頁摘要

| 軸 | 現況（grounded） | 世界第一缺口 | 下一步優先序 |
|---|---|---|---|
| **A 呈現層+資料誠實** | P1 ✅ LIVE(v0.5.4)｜P2 🔨 執行中（健康gate已過、預設切真資料/日期修正/缺源處理尚未全上）｜P3 資訊架構/視覺｜P4 差異化demo case｜P5 持久化+主題 — **P3-P5 皆 ❌ 未做** | 判審 3 秒第一印象仍卡在 P2 收尾 | 收尾 P2 → 立即接 P3 |
| **B 論文級深度** | W1：混合式（token重疊+方向閘為主+Bedrock Haiku二次矛盾偵測），非純heuristic但也非嚴謹NLI｜W2：引擎已建、**預設關**｜W3：已有文字相似度(Jaccard)+單源爆量(burst) heuristic，**非圖算法/社群偵測**｜W4：明確是「簡化版分位數校準」（code 註解自陳非嚴謹 conformal） | W4/W2/W3 皆有免費確定性升級空間；W1 的「開源NLI」提案**觸碰合規紅線待確認** | W4 conformal → W2 開啟 → W3 圖算法 並行；W1 待 7/13 |
| **C 大廠廣度** | 皆 ❌ 未做，但 DynamoDB/JSON 雙 backend 基建已有先例（`cache.py`/`ledger.py`/`scheduler_log.py`）可直接沿用做歷史快照 | 「無歷史」是最大結構缺口 | 歷史快照優先於告警/開放API（告警依賴快照資料） |

---

## 1. 三軸架構總覽

三軸**並管、互不阻擋**——不同人力/分支可平行推進：
- **Axis A**：`src/trustforge/web.py` 呈現層，CSS/HTML/最小前端 JS，不碰演算法。
- **Axis B**：`src/trustforge/trust/scoring.py` + `agent/orchestrator.py` 引擎層，不碰呈現。
- **Axis C**：新增資料持久化 + `web.py` 新頁面/API，跨 A/B 但屬新功能面，不改動既有 A/B 邏輯。

三軸共同紅線（見 §3）：**#24 不造假**、**credit-safe**（不新增付費呼叫熱點）、**GitFlow**（分支+PR+CEO親測）、**誠實不誇大**（明說務實版 vs 論文級、明說大廠護城河短期做不到）。

---

## 2. Axis A — 呈現層 + 資料誠實

> 完整逐 Phase 施作細節（改哪些檔/風險/credit-safe 守則）以 **`docs/DEV-PLAN-REWRITE.md`** 為準，此處只列總綱狀態與驗收，避免內容分裂維護。

| Phase | 內容 | 現況 | 可行性 | CEO 驗收（摘要，全文見 DEV-PLAN-REWRITE.md） |
|---|---|---|---|---|
| **P1** | 拔 dev artifacts、首頁不空白 | ✅ **LIVE（v0.5.4）** | 能做，已完成 | 首頁 3 秒內有 hero/總覽/CTA，header 無 dev 雜訊 |
| **P2** | 預設切真資料 + 健康 gate + 日期修正 + 缺源優雅處理（三件事同輪） | 🔨 **執行中**（健康 gate 已生產親驗過；預設切換/日期修正/缺源處理尚未全上，`feat/phase2-real-default` 分支在動） | 能做，需壓測+風控 | `/status` freshness 多數 fresh 佐證截圖；首頁無參數即真資料檔位；結果頁不再「近兩週」誤配32天前資料；缺源優雅降級不洗版 |
| **P3** | 資訊架構/視覺可信度（多幣總覽導航、loading 態、來源標籤、mobile） | ❌ **未做** | 能做，純 CSS/HTML+最小 JS，風險低 | 官方/公開來源可視覺分辨；loading 態明確；375px mobile 表單/結果表格可讀 |
| **P4** | 差異化 demo case（已知觸發案例 + 誠實「未觸發」文案） | ❌ **未做** | 能做，但**依賴 P2 真資料已預設上線**才能找到真實觸發案例；不得為展示放寬閾值/造資料（`PLAN-w2-wiring.md` 同款紅線） | 至少 1 個真實觸發案例（附時間戳可回溯）；未觸發時明說而非留白 |
| **P5** | 結果持久化 + 主題重開 | ❌ **未做** | roadmap，**先出技術選型文件不動工**；主題重開明確依賴持久化落地（PR #39 教訓：無狀態架構切主題會弄丟報告） | 持久化方案文件（存哪/TTL/成本）經 CEO/老闆核准 |

**Axis A 一句話**：P1 已交付、P2 收尾中，P3-P5 尚未動——**判審第一印象的「表單感」還沒解決，這是老闆點名的缺口，優先序最前**。

---

## 3. Axis B — 論文級深度（引擎）

> W1-W4 對應 `WORLD-FIRST-ANALYSIS.md` gap 表（跨源佐證/來源信譽/操縱偵測/判定信心）。以下依**執行優先序**（非原編號順序）排列：**W4 → W2 → W3 → W1**，因為前三項是免費確定性算法、W1 觸碰合規待確認。

### 3.1 W4 — 信心校準：Split Conformal Prediction（最高 ROI，優先做）

- **現況**：`trust/scoring.py` L1162-1198 code 註解**自陳**「簡化的工程啟發式，不是嚴謹的 conformal prediction——沒有 hold-out calibration set、沒有 exchangeability 假設驗證」，用的是簡化版分位數校準 + 硬門檻 0.5 abstain。
- **世界第一該有**：**Split Conformal Prediction**——用 hold-out calibration set 算 nonconformity score 分位數，提供 **distribution-free coverage 保證**（如 90% 信心區間確實 90% 涵蓋真值），是論文級標準做法（Conformal Language Modeling ICLR2024 等，見 `WORLD-FIRST-ANALYSIS.md` §1A）。
- **可行性**：✅ **能做**。純確定性、免費、不需模型/API 呼叫，幾十行 Python 即可實作（排序 + 分位數計算），**是四項中風險最低、ROI 最高的一項**。PASC（pipeline-aware joint coverage，更嚴謹但更複雜）→ roadmap，本輪先上 split conformal 單點校準。
- **credit 考量**：$0，純 CPU 計算，無新增付費呼叫。
- **可派 CTO 範圍**：`trust/scoring.py` 校準函式重寫（保留現有 `confidence`/`confidence_label` 對外介面，內部演算法替換）+ 需要一組 hold-out calibration 資料（可用既有離線樣本切一部分，**不得為湊資料造假**）+ 對應單元測試（驗證 coverage 統計特性，非只驗證範圍 0-1）。
- **CEO 驗收標準**：
  1. Code 註解/docstring 明確寫「Split Conformal Prediction，distribution-free coverage 保證」，附理論引用。
  2. 有測試驗證「宣稱 90% 信心區間，在測試集上實際涵蓋率接近 90%」（非只測值域）。
  3. 既有 `confidence`/`abstain` 三態行為向下相容，`pytest -q` 全綠。
  4. Bedrock 呼叫次數/成本與升級前相同（純本地計算，無回歸）。

### 3.2 W2 — 動態來源信譽：TruthFinder/CRH 開啟（零成本，已有完整計劃）

- **現況**：引擎**已完整開發**（PR #29，284 測試綠+4輪codex對抗審），但 `orchestrator.py:363` 呼叫 `score()` **未帶** `dynamic_reputation=True`，生產路徑上永遠不執行。`docs/PLAN-w2-wiring.md` 已做完前提驗證（BTC 真樣本接線後可見變化：confidence 0.6125→0.6279）、接線方案、驗收標準、風險防護，**是現成可執行方案，不需要本文件重新設計**。
- **世界第一該有**：TruthFinder/CRH 式來源可靠度×事實真偽互相強化的動態信譽，取代人工固定權重（0.95/mid/0.35）。
- **可行性**：✅ **能做，且已就緒**。開啟本身零額外 Bedrock 呼叫（`stance_fn` 共用同一份 memoized 快取，已實測確認非重複真呼叫）。
- **credit 考量**：$0 增量。
- **可派 CTO 範圍**：直接依 `PLAN-w2-wiring.md` §⑤ 拆兩個 PR：PR-A（merge `feat/w2-dynamic-reputation` 進 main，預設仍關，零行為影響）→ PR-B（接線 `dynamic_reputation=True` + 可解釋性補強 `reputation_trace` 傳到 Report + 新增「操縱來源信譽下降」單元測試）。
- **CEO 驗收標準**：見 `PLAN-w2-wiring.md` §③ 完整清單（BTC 必須有可見變化+可解釋文案、ETH/SOL 允許無變化但需可查證「為何沒變化」、15分鐘/成本無回歸）。
- **來源抄襲/dependency 偵測**（多源互抄虛增信譽的公認陷阱）：目前**未做簡化版**——`scoring.py` 現有文字相似度只用於「排除合法聯播計入獨立佐證」（L1058 附近），不是專門的信譽反制機制。做簡化版（如：偵測到高相似度來源群組時，該群組對信譽提升的貢獻做折算而非各自全額計入）→ **可做，roadmap 次優先**；完整機率式建模（如 Bayesian 來源依賴推斷）→ roadmap 較遠期。

### 3.3 W3 — 抗操縱：協同行為圖（差距最大但可行）

- **現況（grounded 修正）**：`scoring.py` L350-601 **已有**確定性、免 LLM 的協同訊號——文字相似度（Jaccard，排除合法聯播/通稿）+ 單源爆量偵測（時間窗 burst）。**這不是「完全沒做」，但也遠不是圖算法**——沒有帳號-內容二部圖建模、沒有社群偵測（Louvain）、沒有時序 burst 的圖層級分析，仍是單點指標而非結構性偵測。語意換詞可繞過現有相似度偵測（正是 gap 所在）。
- **世界第一該有**：**帳號-內容二部圖 + Louvain 社群偵測 + 時間窗 burst 時序**——找出「一群帳號在短時間內圍繞同批內容異常活躍」的結構性協同，而非單看文字相似度。
- **可行性**：✅ **能做**。二部圖建構、Louvain（`networkx` 有內建或可用純 Python 實作簡化版）、burst 時序統計皆為**免費確定性圖算法**，不需 LLM/外部 API。現有的爆量偵測（`_coordination_signals`）可作為圖模型的時序特徵輸入，**不是從零重寫，是結構性升級**。語意換詞需 embedding（判斷內容是否語意相近而非字面相近）→ 若要做需額外評估模型來源（同 W1 合規考量），**第二階段**，先做圖結構（帳號行為層）不涉及語意內容判斷可先行。
- **credit 考量**：圖算法本身 $0（純 CPU）；若第二階段加 embedding，需比照 W1 走合規評估流程（AWS Bedrock embedding 模型 vs 開源，見 §3.4 合規說明）。
- **可派 CTO 範圍**：新模組（如 `trust/coordination_graph.py`），輸入現有 evidence pool 的 `source/text/ts`（已有欄位，不需新資料源），輸出結構性協同分數 → 併入現有 `info_flags`/`manip_flags` 措辭（沿用既有「中性提醒，非指控」語氣，`scoring.py` L399/L601 已有此原則）。
- **CEO 驗收標準**：
  1. 有單元測試證明「人工構造的協同帳號群組（如 5 個帳號在 10 分鐘內圍繞同 3 則內容互動）」會被結構性標記，而單純「3 家新聞轉載同一通稿」（合法聯播）不會被標記——**用合成測試資料驗證邏輯，不是拿真資料造假觸發**（測試資料 vs demo 展示資料要分清楚，測試合成資料是標準工程實踐、不違反 #24；#24 紅線是不得為了「demo 好看」用真實產品資料造假）。
  2. 措辭延續現有中性原則，不用「協同操縱」等指控字眼作為確定性結論。
  3. 效能：圖建構/社群偵測在既有 15 分鐘預算內完成，附壓測數字。

### 3.4 W1 — 跨源佐證：開源 NLI 模型（⚠️ 合規風險待確認，暫不可直接排程）

- **現況（grounded 修正）**：目前**不是**「通用 LLM prompt」單一機制，是**混合式**——`_corroboration`（`scoring.py` L693-729）以 token 重疊+域內停用詞過濾+方向閘為**前置閘**（免費確定性），通過前置閘的候選才用 **Bedrock Haiku**（W1.5，`orchestrator.py:519,748`）做逐對 stance 分類（entailment/contradiction/neutral）二次確認矛盾。此設計是**為了合規**（見下）刻意選擇。
- **提案（換開源 NLI，如 RoBERTa/BART-MNLI 本地推論）的優點屬實**：免 token 費用、延遲更低、可能更準（專門訓練的 NLI 模型 vs 通用對話模型做 prompt 分類）。
- **⚠️ 但這個提案直接踩到一條本專案已明確記錄、且尚未解除的合規紅線**：
  1. **`ROADMAP.md` 明文**：「完成標準…且只用 AWS Bedrock」「模型合規：全程 Bedrock 直連，禁用其他供應商與內部閘道」。
  2. **`COMPLIANCE-CHECK.md` flag #1**：命題文件與錄取信對「是否僅限 AWS 模型」**兩種解讀不一致，7/13 向窗口 Mars Li 確認定案**——**目前尚未拍板**。
  3. **`WORLD-FIRST-ANALYSIS.md` §5.5 / 附錄 A-1**：本專案**已經評估過幾乎同款方案**（本地 ONNX mDeBERTa 做 NLI），因為當時判定「僅限 AWS 基礎模型」是官方硬約束而**主動 revert 出局**，改走 W1.5 Bedrock Haiku 路線——這正是現在生產路徑在跑的方案。若現在改回開源本地模型，**等於推翻一個已有明確理由的合規決策，且理由（AWS-only 約束是否成立）本身還沒解除**。
  4. 決賽時程是 **8/1-8/2（30小時黑客松，30天後）**，在此之前任何違反命題硬約束的架構改動都有**取消資格風險**，這比任何 ROI 都更需要優先避免。
- **可行性判定**：**❌ 暫不可排入 CTO 執行**，需先滿足前置條件：
  - **7/13（HOYA BIT 企業數據工作坊，11天後）向 Mars Li 確認**「是否僅限 AWS 基礎模型」是否為硬約束。
  - **若確認為硬約束**（維持現狀機率較高，因為是錄取信白紙黑字）：本地開源 NLI **在決賽前不可用**，W1 維持現行 W1.5 Bedrock Haiku 混合式方案；「開源 NLI 換掉 Bedrock」列為**決賽結束後的商業化路線（post-competition roadmap）**，屆時 TrustForge 若脫離黑客松框架獨立營運，才重新評估開源 NLI 的成本優勢。
  - **若確認非硬約束**（命題文件版本較寬鬆）：則可評估，但仍需額外確認「本地推論的運算資源在現有 EC2/Lambda 架構是否可行」（目前架構是 AWS Bedrock API 呼叫模式，無本地 GPU/inference server，跑 RoBERTa/BART-MNLI 需額外運算資源評估，非單純換一行程式碼）。
- **credit 考量**：若最終可行，開源本地推論比 Bedrock Haiku 更省（免 token 費），但需額外運算資源成本（EC2 規格可能要提升，比照 `WORLD-FIRST-ANALYSIS.md` 附錄 A-1 當初 t3.small 分析的邏輯，需重新估算）。
- **可派 CTO 範圍**：**本輪不派**。待 7/13 確認結果回來後，CPO 再出正式接線計劃（比照 `PLAN-w2-wiring.md` 格式）。
- **CEO 驗收標準**：**不適用（尚未進入執行階段）**。7/13 後由 CEO/CPO 依 Mars Li 回覆更新本節。

**Axis B 一句話**：W4/W2/W3 是三項免費確定性升級，能立刻排程；W1 的開源 NLI 提案方向合理但**踩在一條本專案已經踩過一次的合規紅線上，需 7/13 確認才能排程，不可搶跑**。

---

## 4. Axis C — 大廠廣度（差異化，非規模對抗）

### 4.1 歷史信任分快照（最高影響/可行，優先做）

- **現況**：❌ 未做——目前系統無任何歷史信任分持久化，每次分析都是即時計算、無法回溯「這個信任分昨天/上週是多少」。
- **世界第一該有**：類 Glassnode「point-in-time」概念——每日將各幣種/來源的信任分/信譽分寫入 DB，形成可回溯時序。
- **可行性**：✅ **能做**。基建有先例可循——`ingestion/cache.py`（`DynamoDBCache`）、`ledger.py`（`DynamoDBLedger`）、`scheduler_log.py`（`DynamoDBSchedulerRunLog`）皆已是「DynamoDB 線上 + JSON/JSONL 本機雙 backend」模式，新增一張「trust score 每日快照」表可直接沿用同一套模式，**不是從零設計架構**。
- **credit 考量**：DynamoDB 寫入為新增付費資源（雖低量），依既有守則（`DEV-PLAN-REWRITE.md` Phase 5 credit-safe 條款）**新增儲存服務需先估算成本並經 CEO/老闆核准，不得默默啟用計費資源**。可先用 JSON backend（比照現有雙 backend 模式的本機/單容器選項）做 MVP，DynamoDB 留待流量驗證後再升級，降低前期成本風險。
- **可派 CTO 範圍**：新模組（如 `trust/history_store.py`，比照現有 cache/ledger 雙 backend 抽象介面）+ 排程（比照 `scripts/fetch_scheduler.py` 既有排程模式，每日跑一次快照寫入）+ `/status` 或新頁面讀取展示。
- **CEO 驗收標準**：
  1. 跑排程 N 天後，可查詢到「某幣種信任分過去 N 天序列」，非造假回填。
  2. 成本估算文件（每日寫入量 × DynamoDB 定價，或先行 JSON backend 的 $0 方案）附 PR。
  3. 既有即時分析路徑不受影響（快照是旁路寫入，非同步阻塞主流程）。

### 4.2 信任分變動/分歧擴大告警 + watchlist（依賴 4.1）

- **現況**：❌ 未做，且**依賴 4.1 的歷史資料**才能算「變動」。
- **世界第一該有**：比大廠更聚焦——非「什麼都監控」，而是「信任分顯著下滑」或「跨源分歧擴大」時主動告警，使用者可設 watchlist。
- **可行性**：✅ **能做**，但需 4.1 先上線累積至少數天資料才有意義。
- **credit 考量**：告警機制本身 $0（比對既有快照資料的閾值邏輯），若要做 email/webhook 推播則有額外整合成本，MVP 可先做「頁面上顯示變動徽章」不做主動推播。
- **可派 CTO 範圍**：讀取 4.1 快照資料 + 變動量計算（純數學，$0）+ watchlist 使用者狀態（需考慮是否要帳號系統，MVP 可用 URL 參數/cookie 免登入版）。
- **CEO 驗收標準**：有真實累積的歷史資料佐證變動數字（非假造），變動幅度計算邏輯可驗證。

### 4.3 社群訊號操縱透明擴展（Kaito 2026 AI 灌水醜聞論述）

- **現況**：❌ 未做。**工程實作與 Axis B §3.3 W3 協同行為圖高度重疊**——「訊號操縱透明」的技術基礎就是 W3 的協同偵測輸出，本項更多是**產品敘事/UX 呈現層**，把 W3 的技術輸出包裝成「我們主動揭露訊號操縱，大廠不做」的差異化論述。
- **⚠️ 來源誠實提醒**：「Kaito 2026 AI 灌水醜聞」是 CEO 競品分析轉述的結論，本文件**未取得一手引用連結**——對外正式使用前（如簡報/行銷文案）需請 CEO/研究員補一手來源，內部規劃階段先引用但不得對外當作已查證引述（呼應「數據要有來源，不能憑空捏造」守則）。
- **可行性**：技術面依賴 §3.3 W3 完成度；敘事面待來源確認後可用。
- **可派 CTO 範圍**：待 W3 有輸出後，`web.py` 結果頁補「訊號操縱透明度」展示區塊（複用 W3 輸出，非新演算法）。
- **CEO 驗收標準**：展示的訊號皆可回溯到 W3 實際偵測輸出，非文案先行、功能後補。

### 4.4 免費開放 API（DefiLlama 式信任槓桿）

- **現況**：❌ 未做。目前 `/analyze.json` 有 API 形式但是**站內用途**（非公開文件化、無限流分級）。
- **世界第一該有**：DefiLlama 式「免費開放資料當槓桿」——公開文件化的免費 API，讓其他開發者引用 TrustForge 信任分，擴大品牌滲透。
- **可行性**：⏳ **roadmap，非本輪**——需先有穩定的資料源（依賴 Phase2 真資料上線）+ 限流分級設計 + API 文件，屬於**產品化決策**（是否要對外開放、rate limit 策略），涉及成本暴露面擴大，需 CEO/老闆先拍板要不要做，非純技術排程。
- **credit 考量**：公開 API = 公開的成本攻擊面，**必須先有限流/token gate 機制**（現有 `TRUSTFORGE_LIVE_TOKEN`/`_check_live_rate_limit` 可參考但需重新設計公開版分級），本項若倉促上線的風險最高，明確排最後。
- **可派 CTO 範圍**：本輪不派，待 4.1-4.3 穩定後再評估 API 規格。
- **CEO 驗收標準**：不適用（roadmap 階段，先出產品化決策文件而非直接動工）。

### 4.5 大廠護城河短期做不到（誠實標記，不放進本輪計劃）
| 項目 | 為何做不到 |
|---|---|
| 實體錢包標記規模 | 需要 Arkham 等級的長期人工/爬蟲標記資料庫，非演算法可短期補齊 |
| 法院級歸因 | 需要法遵/情資團隊與跨境資料存取權限，超出目前團隊規模與定位 |
| 機構資料基建 | Glassnode/Nansen 等級的多鏈全量索引需要專職資料工程團隊與長期投入 |
| 多鏈海量指標 | 需要大規模鏈上索引基建（如 The Graph 等級），非本專案短期目標 |

**niche 論述（大廠空白、我們的贏面，不需要規模也能贏）**：逐主張可解釋信任（大廠是黑箱分數）、跨源分歧量化（Ground News blindspot 精神）、訊號層操縱透明（比 Arkham Oracle 的黑箱推理更可解釋）——這是 TrustForge 該打的仗，不是規模對抗。

**Axis C 一句話**：歷史快照是最大結構缺口也最可行，優先做；告警/敘事依賴它；開放 API 涉及成本暴露面，明確排最後且需老闆先拍板；大廠規模型護城河誠實標記為短期做不到。

---

## 5. 三軸執行順序建議（合併時間軸）

> 對齊決賽時程：7/13 企業數據工作坊（合規確認 + HOYA BIT 真數據）、8/1-8/2 決賽（30小時）。

**階段 1（現在 → 7/13 前，可立即排程）**：
- Axis A：收尾 P2（預設真資料三件事同輪）→ 接 P3（資訊架構/視覺）
- Axis B：W4（conformal，最高 ROI）+ W2（開啟，已有完整計劃 `PLAN-w2-wiring.md`）+ W3（協同行為圖第一階段：帳號行為結構層，不涉語意）**三項並行**，皆為免費確定性升級，互不阻擋
- Axis C：4.1 歷史快照設計 + MVP（JSON backend 版，先不上 DynamoDB 降低前期成本）

**階段 2（7/13 之後，拿到合規確認 + HOYA BIT 真數據規格）**：
- Axis B：W1 依 Mars Li 回覆決定是否可評估開源 NLI，或維持 W1.5 現狀
- Axis A：P4（差異化 demo case，依賴 P2 真資料+HOYA BIT 真數據已上線）與 P3 收尾可並行
- Axis C：4.1 若 MVP 穩定，評估是否升級 DynamoDB；4.2 告警接續

**階段 3（決賽前最後衝刺 / 決賽後）**：
- Axis A：P5（持久化+主題）留到最後，明確依賴前置架構決策
- Axis C：4.3（依賴 W3 完成）、4.4（roadmap，需老闆拍板才排程）
- 若 W1 開源 NLI 因合規確認為硬約束而暫緩 → 列入**決賽後商業化路線**

---

## 6. 守則重申（三軸共同紅線）

1. **#24 不造假**：demo case 必須真實觸發、有時間戳可回溯；測試合成資料（如 W3 驗證用的人工協同帳號群組）是標準工程實踐、不等同造假，但**不得把合成測試資料當展示資料**，兩者要分清楚標註。
2. **credit-safe**：新增付費資源（DynamoDB 表、公開 API）需先估算成本並經 CEO/老闆核准，優先用免費/低成本 backend 做 MVP。
3. **GitFlow**：分支 + PR + CEO Chrome 親測驗收，不可只信副手測試綠燈（`WORLD-FIRST-ANALYSIS.md` §6 已有慘痛教訓：副手測試綠仍需 CEO 親測抓到真 bug；W1 2b 三輪 revert 的教訓也適用——語言/語意類判斷邏輯尤其需要多層審查）。
4. **誠實不誇大**：本文件已標明「能做」vs「roadmap」vs「合規待確認」vs「大廠護城河短期做不到」四種狀態，不得在對外文案/簡報中把「roadmap」講成「已完成」。

---

## 7. 完成回報

**檔案路徑**：`/Users/apple/HurricaneSoft/trustforge/docs/WORLD-FIRST-MASTER-PLAN.md`（新建）；同步更新 `/Users/apple/HurricaneSoft/trustforge/docs/README.md`（加入索引行）。

**三軸各一句話**：
- **Axis A**：P1 已 LIVE、P2 收尾中，但 P3-P5（老闆點名的「像產品」那 4/5）還沒動，優先序最前。
- **Axis B**：W4 conformal 校準、W2 開啟（已有完整方案）、W3 協同行為圖三項免費確定性升級可立即排程；W1 開源 NLI 提案方向合理但踩中本專案已記錄在案、尚未解除的 AWS-only 合規紅線，需 7/13 確認才能排程，不可搶跑。
- **Axis C**：歷史信任分快照是最大差異化缺口也最可行（有基建先例可沿用），告警與開放 API 依序接續，大廠規模型護城河（錢包標記/法院級歸因/機構基建/多鏈全量）誠實標記為短期做不到。

**建議下一個該動手的 3 件事**：
1. **Axis B — W4 Split Conformal Prediction**：免費、確定性、風險最低、ROI 最高，可立刻派 CTO 開分支動工，不用等任何外部確認。
2. **Axis B — W2 接線 PR-A/PR-B**：`docs/PLAN-w2-wiring.md` 方案已完整，直接照方案派 CTO 執行，零額外成本、零新設計工作。
3. **Axis A — P2 收尾**：把「健康 gate 已過」的既有進度收斂成完整上線（預設切真資料 + 日期修正 + 缺源優雅處理三件事同輪），解除老闆「UI/UX 只做 1/5」的點名壓力，並且是 Axis A P3/P4 與 Axis B demo 展示的共同前置。
