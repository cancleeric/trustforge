# BUILD-PLAN — 招牌武器落地排程（CTO 產出）

> 依據：`CREATIVE-STRATEGY.md`（招牌四武器①~④ + 砍除項）、`COMPETITION-OFFICIAL.md`（官方評分/交付）、
> 實際 codebase `src/trustforge/`（2026-07-18 盤點）。
> 方法論：先讀既有程式碼，能改既有的絕不從零建。所有檔案/函式路徑均為實地 grep/read 驗證，非憑空推測。

---

## 0. 全局結論（先講重點）

**這個 codebase 比創意文件想像的成熟得多**。招牌四武器裡，③矛盾帳本幾乎是**現貨**（後端+前端都已存在、只差命名/包裝），
拉盤指紋（次要加分項）也已是**現貨**（W3 協同操縱偵測，模板相似 active、爆量偵測 D1.2 已重啟並過 insights 層）。
真正的淨新增集中在：①反事實 A/B 的「naive baseline 對照組」與離線比較腳本、②來源獨立性的**視覺化**（後端聚類算法已存在，
只是沒暴露成圖）、④負空間情報（全新偵測函式，但沿用既有 `Insight` pattern）。

次要加分裡風險最高的是「魔鬼代言人 agent」——這是**唯一一個要在既有三步驟推理鏈裡插入第 4 個真 LLM 呼叫**的項目，
會直接吃 15 分鐘官方執行時間與 Bedrock 每日成本上限（`budget_guard.py`），必須謹慎排。

**招牌四武器總工時：約 27 人時（區間 24–30h）。加上次要加分四項 + CEO 加碼「來源分離視覺」共約 20 人時，全部九項合計約 47 人時（44–52h）。**
**47h > 30hr 決賽時間盒** → 這些都不是現場才寫的東西，**全部必須是賽前（8/1 前）工作**，30hr 決賽時間盒只留給「抽題整合測試 + 部署 + 正式執行 + 交付件打包」。這點在下面第 3、4 節具體排出。

---

## 1. 逐項現況盤點 + 工時 + 難度 + 風險

### ① 反事實 A/B（離線對照組）— 8h・難度中

**現況**：`agent.orchestrator.run_agent_pipeline(query, coin, qtype, docs, ...)`（`src/trustforge/agent/orchestrator.py:1163`）
直接吃 `docs: list[Document]`，不綁定固定連接器；`ingestion.base.collect(query, coin, sources=..., ...)`
（`src/trustforge/ingestion/base.py:256`）的 `sources` 參數可傳子集連接器。也就是說「拿掉某類來源重跑一次」這件事
**現有架構零阻力**，這正是創意文件說「既有信任層開關即可，近零工程」的依據，已驗證屬實。

**淨新增缺口**：
1. 目前**沒有** naive baseline（無信任層／無交叉比對／無操縱懲罰的陽春版）可對照——需要寫一個最小 baseline 路徑，
   讓「TrustForge 有信任層」vs「naive RAG 無信任層」的差異看得出來（例如 2 則同溫層/機器人洗版新聞被 naive 版直接採信、
   TrustForge 版被 `_manipulation_penalty`/`_coordination_signals` 抓到降權或標旗）。~2h。
2. 一支離線比較腳本（暫定 `scripts/run_counterfactual_ab.py`），對同一批 docs 跑兩次（naive vs trust-layer），
   輸出 before/after 的 `market_judgment`/`confidence`/`limits` diff，供簡報引用。~2h。
3. 賽前準備 1–2 組「已知會翻盤」的對照組資料（機器人洗版/單源放大的合成樣本，掛進 `demo/sample_data/` 同款格式）。
   這需要人工設計腳本能製造出「naive 判斷 A，trust 層判斷 B」的可信劇本，非純工程，抓 2h 但含撰稿。
4. UI/簡報包裝（靜態頁或表格截圖，不需要即時互動）。~2h。

**風險**：對照資料要「真實可信」又要「保證會翻盤」，容易流於做作；文案上要老實承認這是預先設計的示範情境，
不能包裝成「真發生過的攻擊事件」（品牌自殺風險同 B2 教訓）。

---

### ② 來源獨立性圖譜 — 9h・難度中低

**現況（比想像中完整）**：
- `trust.scoring._corroboration()`（`scoring.py:887` 起）已經是「N 篇文章→K 個獨立來源」的核心運算：canonical
  source 正規化去重、停用詞過濾、方向閘、`stance_fn` 語意矛盾閘。
- `trust.scoring._coordination_template_flags()`（`scoring.py:502`）用 **union-find** 對高 Jaccard 相似文字做聚類
  （門檻 0.8，≥3 個獨立來源觸發）——這套聚類引擎可以直接**重用**當「獨立性圖譜」的後端算法，不必重寫。
- `web.py:_independence_tier()`（`web.py:2637`）已經有「高·官方／高·第三方／中·社群／一般·輔助」二維標籤
  （獨立性層級 × 權威性），區分 CoinGecko/onchain 這種「客觀但非一手」的來源，避免誤標「官方」。
- 前端已有 `TrustRadarChart.tsx`、`TrustBreakdown.tsx`、`PriceProvenancePanel.tsx` 可參考複用樣式。

**淨新增缺口**：
1. 沒有把 `_corroboration`/union-find 的聚類結果**在 report 層級聚合暴露**（目前只在單一 claim 的
   `info_flags`/佐證計數裡，沒有「這個議題 12 篇文章→3 個獨立集群」這種彙總數字）。~3h，低風險（複用已測試邏輯）。
2. 沒有真正的「圖」視覺化元件——目前沒有任何 `*Graph*.tsx`。建議**不做**力導向圖（過度工程、對 15% 創意度沒有加分，
   反而吃掉工程時間），改用「分組氣泡/徽章牆」（N 篇卡片依集群分組上色）——比照 `EvidenceTable.tsx` 現有渲染模式擴充。
   新元件 `SourceIndependenceGraph.tsx`（或索性做成 `EvidenceTable` 的分組模式）~5h。
3. 稀疏來源（BNB/XRP 常見）的降級敘述——`insights.py` 已有 `COVERAGE_INSUFFICIENT` 誠實合約可直接套用，~1h 接線。

**風險**：低——後端算法是現成且已通過測試的（`_coordination_template_flags` 有 codex 對抗審記錄），主要工作量在前端呈現，不涉及信任分數計算本身，不會動到既有回歸測試。

---

### ③ 矛盾帳本 — 3h・難度低（現貨，只差包裝）

**現況（幾乎全建好）**：
- `agent.orchestrator.detect_cross_source_signal()`（`orchestrator.py:533`）對**所有題型**（非僅假設驗證）計算
  `stance_pairs`（跨源語意矛盾配對）+ `distinct_sources`（bullish/bearish 各自去重來源清單），寫入
  `Report.cross_source_signal`。
- 前端 `CrossSourceSignalPanel.tsx` 已經完整渲染：矛盾時左右兩欄（▲BULLISH / ▼BEARISH）列出來源+原文，
  單一來源主導時有 `SingleSourceBadge` 警示，且附 `supporting_claim_ids` 可回溯。
- 另有 `HypothesisLedgerPanel.tsx`（正反方對照）服務 `HYPOTHESIS` 題型，是姊妹功能，非重複建設。

**淨新增缺口**：這個武器**本質上已交付**。剩下純粹是比賽包裝：
1. Pitch 文案把「跨源分歧面板」正式定名「矛盾帳本」，簡報截圖直接引用現有 UI。~1h。
2. 補一段「為何這樣加權」說明——`Evidence.trust_components` 已有分項數值，`EvidenceTable` 展開已顯示，
   只需在 `CrossSourceSignalPanel` 旁加一句連結文字指向 trust breakdown。~2h。

**風險**：幾乎零。這是招牌四武器裡 CP 值最高的一項，創意文件的判斷得到 code 驗證。

---

### ④ 負空間情報 — 7h・難度中低

**現況**：`trust/insights.py` 已建立可驗證洞察層模式（D1.1 聰明錢背離、D1.2 操縱爆量、D1.4 來源自我矛盾、
D1.5 假設驗證正反方），每條 `Insight` 都有「≥2 貢獻來源 + 方向 + 強度 + 覆蓋閘」規格（`COVERAGE_INSUFFICIENT`
誠實合約，見 `insights.py:34`）。**但目前沒有任何一條洞察是「刻意檢查『沒發生什麼』本身當情報」**——
D1.1/D1.2/D1.4 都是「有訊號才報」，跟「監管 0 事件/鯨魚異常安靜」這種「缺席即情報」語意不同。

**淨新增缺口**：
1. 新函式 `detect_negative_space_signal()`（比照既有 D1.x 命名為 D1.6），檢查特定 kind（`regulatory`/`onchain`）
   在分析視窗內的 claim 數是否為 0（或低於預期基準），輸出「觀察到沉默」洞察，措辭必須明講
   「未觀察到 ≠ 不存在，可能是資料源覆蓋不足」（避免 negative evidence 謬誤，這點需要跟 D1.1 的
   `_SMART_MONEY_PROXY_NOTE` 誠實聲明同款嚴謹度）。~4h（含單元測試，比照 `tests/test_insights_d11.py` 等既有模式）。
2. 前端 `InsightExplainabilityPanel.tsx` 目前是**依 `insight_type` 硬編渲染**（第 50 行對 `source_self_contradiction`
   特判），新增一種 type 需要跟著加 case，不是全自動泛用。~2h。
3. 接線進 `insights.detect_insights()` 彙總入口 + `Report.insights`。~1h。

**風險**：中低。主要風險是「缺席即情報」容易被裁判抽查時問「你怎麼知道不是你自己的資料源沒接到，而不是真的沒事件」——
措辭必須把「資料源覆蓋範圍」清楚揭露（哪些 regulatory feed、哪些 onchain 指標），跟①的品牌風險同一類，需要法務/CISO
等級的用字謹慎度，不能簡化。

**招牌四武器小計：8 + 9 + 3 + 7 = 27h**

---

## 2. 次要加分 + CEO 加碼 現況盤點

### 雙軸信心卡 — 3h・難度低
**現況**：`ConfidenceGauge.tsx` 已經是雙軸資料（`calibratedConfidence` 資訊完整度 vs `rawConfidence` 信任分），
只是目前 UI 呈現成「一個大字 hero + 一個附屬數字」，不是視覺上對等的雙軸卡片。
**缺口**：改版面（雙半圓或雙軸雷達），純前端樣式工作，數據來源不用動。低風險。

### 魔鬼代言人 agent — 8h・難度中高（風險最高項）
**現況**：`Report.contrarian`/`could_flip` 已由**機械式規則**產生（`orchestrator.py:218-220`：反方訊號數量→固定句式），
Step4「限制複審」（`orchestrator.py` run_agent_pipeline 內，~1163 行後段）已經是一次額外 Bedrock 呼叫，但只做「限制條款補充」，
不是主動建構反方論證。
**缺口**：要做到真正「魔鬼代言人」（LLM 主動用 contrarian 證據組一段最強反方論證），需要**新增第 5 個 Bedrock 呼叫**或
把 Step4 prompt 改造成雙重任務。
**風險（要老實標出來）**：
- 這是全部九項裡**唯一要在官方 15 分鐘正式執行內插入新 LLM 呼叫**的項目，直接跟 `budget_guard.py` 的
  `daily_cap_exceeded()`/`try_reserve_request_budget()` 每日成本上限、`log.remaining()` 時間預算搶資源。
- 若 Bedrock 延遲/降級（`llm_offline` fallback），這個 agent 步驟必須跟既有 Step1-4 一樣有 fail-safe 離線退化，
  不能讓它變成單點故障拖垮整條 15 分鐘執行鏈。
- 建議：**降低野心**，不開新 Bedrock 呼叫，而是把 Step4 prompt 擴充成「複審限制 + 順便產出一段反方論證」一次呼叫兩用
  （省一次 API call/延遲），這樣風險降到跟現有 Step4 一樣的等級。若照此改法，工時可壓到 5-6h。

### 鏈上真相錨 — 2h（維持現況即可）・難度低
**現況比預期完整**：`ingestion/onchain.py` 已有 Alternative.me Fear&Greed（**幣種無關，五幣皆可用**）、
Blockchain.info/mempool.space/Blockchair（**僅 BTC**）。官方幣池 BTC/ETH/SOL/BNB/XRP 五選一/多，抽到非 BTC 時
現有連接器只剩 FNG 這個幣種無關指標可當「鏈上/總經錨」。
**缺口**：若要對 ETH/SOL/BNB/XRP 也有一手鏈上錨（Etherscan/Solscan），需要新連接器 + API key（見第 5 節依賴）。
**建議**：**不擴大範圍**——FNG 已經是官方交付規格允許的「鏈上/總經」代理，且是唯一保證五幣通用、免 key 的來源；
真正要做的只是 2h 的**文案工作**：把 FNG 在報告裡明確定位成「市場總體恐慌貪婪錨」，不誇大成「鏈上一手證據」，
誠實標注 kind=onchain 的真實覆蓋範圍。若時間允許再加 ETH/SOL 連接器算加碼，不算基本交付。

### 拉盤指紋降級版 — 2h（現貨，只需驗證+命名）・難度低
**現況**：已經是**兩層現貨**——`_coordination_template_flags`（模板文字相似度，informational-only，active）
+ `_coordination_burst_flags`（同源爆量偵測，`insights.py` D1.2 `detect_manipulation_burst` 已把它「重啟」接進
洞察層並過覆蓋閘）。這正是創意文件講的「降級版」精神：不下操縱結論，只做透明化提示（`info_flags`，不扣信任分）。
**缺口**：幾乎沒有，只需要：確認 `tests/test_insights_d12.py` 綠燈（賽前跑一次驗證，NOT OBSERVED，本輪未執行）、
簡報文案定名「拉盤指紋」。~2h。

**次要加分小計：3 + 8(或6) + 2 + 2 = 15–17h**

### CEO 加碼：判斷來源分離視覺 — 5h・難度中
**現況**：`FactsInferenceLadder.tsx` 已有**段落級**分離（事實／推論／結論三步驟卡片），這是現貨基礎。
**理解 CEO 的加碼要求**＝更細顆粒度：每一句 fact/inference 要能一眼看出「這句話是客觀連接器數據、還是 AI 推論、
還是引用第三方來源」，目前 `Report.facts`/`inferences` 是 `list[str]`（純字串），沒有逐句掛 `kind`/`evidence_idx`。
**缺口與做法**：**不動既有 `facts`/`inferences` 欄位**（`test_json_api.py` 3413 行對 `Report` JSON 合約有大量鎖定測試，
直接改型別炸裂半個回歸測試面，風險過高）——改用**加法**：新增可選的並行陣列（如 `fact_sources: list[dict] | None`），
`None` 時完全不影響既有序列化（沿用專案「缺鍵=未知，向後相容」慣例，見 `Evidence.author` 的先例）。
前端逐句掛小色標（客觀/推論/引用）。5h（後端 2h 資料標註 + 前端 3h 呈現）。
**風險**：中——只要守住「加法、不改既有欄位型別」這條線，風險可控；若貪快直接改 `facts: list[str]` 為
`list[dict]`，會動到 JSON API 合約，判定為高風險，**不建議**。

**九項合計：27 + 15~17 + 5 = 47~49h（取中 47h）**

---

## 3. 賽前 vs 現場 — 具體工作項拆分

### 必須 8/1 前做完（全部功能開發，共 ~47h）
①②③④招牌四武器全部、次要加分四項全部、CEO 加碼「來源分離視覺」——**一項都不留到現場**。理由：
- 現場只有一次正式執行機會（15 分鐘），不能拿來做功能除錯。
- 官方抽題前不知道幣種/題型，但**功能本身（信任層/圖譜/矛盾帳本/負空間偵測）跟抽到哪個幣種無關**，
  抽題只影響「餵進去的 docs 內容」，不影響程式碼要不要存在——沒有理由等抽題才寫。

### 反事實 A/B 的「離線先跑、不佔計分執行」紀律 → 具體落地成這些工作項：
1. **賽前**準備好 1-2 組固定的 naive-vs-trust 對照資料與跑過的結果截圖/表格，存成靜態簡報素材
   （不是即時 demo 頁面），存放於 `docs/pitch/` 下新增一節或獨立截圖資產。
2. **程式碼層防呆**：naive baseline 路徑（`scripts/run_counterfactual_ab.py`）**不掛進** `web.py` 的
   `/api/analyze` 公開端點，也不在 CLI `main()` 的預設參數路徑上——官方 15 分鐘執行只能觸發正式 `pipeline.run`，
   物理上不可能誤觸發 A/B 對照跑兩次吃掉時間。
3. **簡報流程**：現場報告時，A/B 對照當成「投影片裡的一張圖」講解方法論，明確口頭聲明「此對照組為賽前離線驗證，
   非本次正式執行內容」——避免評審誤以為 15 分鐘裡跑了兩次分析（若誤會，可能被質疑「超時/多次執行」違規）。
4. `SUBMISSION-CHECKLIST.md` 目前的 30 小時建議流程完全沒提到這條紀律，**需要補一行**提醒現場報告人不要在
   demo 環節誤觸發或誤講成即時對照（建議由 CTO 在賽前補一版 checklist）。

### 現場 15 分鐘正式執行內只做的事
唯一一次：抽題 → 鎖定幣種/題型 → 打 `pipeline.run`（真連接器 + 真 Bedrock）→ 產出 4 交付件。
**不含**任何 A/B 對照、任何新功能除錯。

---

## 4. 30 小時決賽時間盒排程（8/1–8/2）

> 前提：功能開發已在賽前完成（第 3 節），30hr 只處理「抽題整合測試＋部署＋正式執行＋交付」。
> 標 **[並行]** 的項目可若有第二人手（嵋婕/子彤/Nicholas 支援簡報美化、資料檢查）同時做，不卡在關鍵路徑上。

| 時段 | 工作項 | 人時 | 關鍵路徑 |
|------|--------|------|----------|
| H0–H1 | 抽題、鎖定幣種/題型；確認官方 OHLCV 資料已就位 | 1h | ✅ |
| H1–H3 | 跑 `--offline` 全流程 smoke test，確認四招牌武器在**這次抽到的幣種/題型**上都有觸發（尤其負空間/矛盾帳本這類需要特定資料形狀才會出現的洞察，抽到冷門幣可能觸發不了——這是排程裡最大的不確定性，見下方風險） | 2h | ✅ |
| H3–H6 | 切換真連接器（Reddit/Bluesky/news/onchain）+ 真 Bedrock，跑一輪非正式驗證，檢查信任權重/報告行文合理 | 3h | ✅ |
| H3–H6 | [並行] AWS 架構圖 + 提案簡報收尾（引用①-④截圖與現有 UI） | 3h | — |
| H6–H9 | 部署 App Runner（`apprunner.yaml` 已存在，README 已有步驟）出 Live Demo URL，`?live=1` 驗證真 Bedrock 通 | 3h | ✅ |
| H9–H10 | 成本護欄檢查（AWS Budgets、`TRUSTFORGE_BEDROCK_DAILY_USD_CAP`）、Secrets Manager 金鑰就位 | 1h | ✅ |
| H10–H12 | **緩衝**：處理部署/連接器意外（歷史經驗 Cloud Run/App Runner cutover 常有 stale worker、region 差異等坑） | 2h | 緩衝 |
| H12–H12.5 | **正式 15 分鐘執行**（官方僅一次機會，全程錄影） | 0.5h | ✅ |
| H12.5–H13.5 | 若因主辦環境問題需重跑（規則允許不可歸責重跑一次），保留紀錄 | 1h | 緩衝 |
| H13.5–H16 | 4 交付件整理：Final Report / Evidence List / Execution Log / Source-Config 打包驗證 | 2.5h | ✅ |
| H13.5–H16 | [並行] Live Demo 錄影後製、字幕、簡報最終定稿 | 2.5h | — |
| H16–H18 | 反事實 A/B 素材 + 招牌武器截圖整理進簡報（第 3 節說的靜態素材，非即時展示） | 2h | — |
| H18–H20 | 全隊彩排一次完整 pitch + demo 播放，計時 | 2h | ✅ |
| H20–H30 | **總緩衝**（10h）：AWS 區域/模型可用性、team 生理時鐘、投稿平台上傳問題、Part A 若需轉 public 的清理 SOP | 10h | 緩衝 |

**關鍵路徑**：H0→H1→H3→H6→H9→H10→H12.5（正式執行）→H16→H18→H20，約需 20h 硬工，其餘 10h 是緩衝——
**30hr 對「賽前已做完功能開發」這個前提是夠的**，且緩衝比例（1/3）合理。

---

## 5. 依賴 / 阻塞

| 項目 | 狀態 | 影響 |
|------|------|------|
| 7/13 HOYA BIT 企業數據工作坊規格 | **NOT OBSERVED**——`docs/competition/COMPETITION.md` 裡這條 checklist（`- [ ] 7/13 拿 HOYA BIT 數據規格 → 回填連接器`）仍是未勾選狀態，本輪讀 code 沒看到任何回填痕跡（ingestion 連接器清單跟 `COMPETITION-OFFICIAL.md` 歸檔日 7/1 時列的一致）。**今天已 7/18，工作坊理論上已開完**，需要跟老闆確認實際結果，若拿到新規格要回填連接器，會排擠上述 47h 預算。 | 若有新規格，需重新估工時，可能排擠次要加分項（優先砍：魔鬼代言人／來源分離視覺，見第 6 節）。 |
| AWS 帳號 / Bedrock 存取 / 「僅限 AWS 模型」爭議 | **NOT OBSERVED**——`CREATIVE-STRATEGY.md` 明寫「CEO 7/13 向 Mars Li 確認…後定案模型入口」，`COMPLIANCE-CHECK.md`/`COMPETITION.md` 對應 checkbox 仍未勾。需要老闆確認是否已定案。 | 我方已用 Bedrock（兩種解讀皆合規），**不影響架構方向**，但若確認「僅限 AWS」，可以放心把④負空間情報/魔鬼代言人這類需要 LLM 判斷的文案措辭寫得更篤定（不用留退路給非 AWS 模型）；若還沒問，建議賽前務必問到，避免決賽當天卡在合規爭議。 |
| Etherscan/Solscan API key（鏈上真相錨擴大範圍用） | 需老闆/團隊申請 | 第 2 節已建議**不擴大範圍**、用 FNG 頂著即可，此依賴可**降為非必要**。若團隊仍想做，需先申請 key 才能動工，且要排進 47h 之外。 |
| AWS App Runner 實際部署驗證 | `apprunner.yaml` 存在、README 有步驟，但 **NOT OBSERVED**——本輪未實際跑過部署，`SUBMISSION-CHECKLIST.md` 上「Live Demo 部署網址」這條也未打勾 | 建議排進賽前（非等 8/1 才第一次嘗試部署），否則第 4 節 H6–H9 的 3h 可能不夠——部署踩坑歷史經驗（Cloud Run cutover stale worker 等）顯示「第一次部署」風險高，應該 8/1 前先跑過一次 dry-run。 |
| 魔鬼代言人 agent 的 Bedrock 呼叫預算 | 需人親自決策：是否接受在 Step4 疊加任務（省一次呼叫，風險較低）vs 開新 Step5（風險較高但邏輯乾淨） | CTO 建議前者，但這是設計取捨，需 CEO/CTO 對齊一次再動工，非純技術問題。 |

---

## 6. 若時間不夠：砍除優先序建議

47h 若賽前時間不足，建議砍除順序（越前面越先砍，對評分傷害越小）：
1. **CEO 加碼「判斷來源分離視覺」**（5h）— 已有 `FactsInferenceLadder` 段落級分離頂著，逐句細顆粒是錦上添花。
2. **魔鬼代言人 agent**（6-8h）— 風險最高、`contrarian`/`could_flip` 已有機械式版本頂著基本分。
3. **鏈上真相錨擴大範圍**（若團隊真想做 ETH/SOL 連接器）— 本來就建議不做，優先度最低。
4. **雙軸信心卡**（3h）— 純視覺加分，`ConfidenceGauge` 現況已可用。

**招牌四武器（27h）+ 拉盤指紋（2h）+ 矛盾帳本（3h，其實已含在 27h 內）務必保留**——這些命中官方 30% 主題切合度 +
25% 技術可行性評分項，且拉盤指紋/矛盾帳本幾乎零成本（現貨），沒有理由砍。
