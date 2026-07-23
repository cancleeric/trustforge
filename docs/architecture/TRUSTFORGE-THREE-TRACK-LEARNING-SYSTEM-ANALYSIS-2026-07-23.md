# TrustForge 三軌統一學習架構分析

> 日期：2026-07-23  
> 狀態：CEO 審查通過，作為後續開發計劃的架構基準  
> 範圍：文件分析；不代表 ModelHub 線上狀態已驗證，也不授權資料庫或外部服務異動

## 1. 結論

TrustForge 要建立的不是一個包辦所有工作的獨立模型，而是三條目的、資料、標籤、評估與啟用權限彼此隔離的學習軌：

1. **Question RAG 品質軌**：改善問題理解、檢索與回答品質；歷史回答只能作為 `historical_non_evidentiary`，不能升格成新事實。
2. **分析品質軌**：以每次不可變的 point-in-time 分析作為訓練樣本，分別處理異常偵測與信心校準。
3. **外框受控升級軌**：把診斷、候選提案、沙盒驗證、人工審查、人工啟用與回滾串成治理迴圈；不是可自行改寫或自行上線的黑箱模型。

三軌可共用版本登錄、實驗追蹤及可觀測基礎設施，但不得混用 truth label、Evidence、評估指標或 production activation 權限。Trust Kernel、時間邊界與 Evidence binding 是不可由學習系統改寫的硬邊界。

## 2. 已有基礎與缺口

### 2.1 已有基礎

- Question RAG／對話記憶已有 deterministic 路徑，embedding 與 reranker 仍受 model gate 管制。
- Feature Store 已定義 point-in-time、provenance 與特徵版本化方向。
- Hermes 文件已有 replay、校準、持續改善及 sandbox／human gate 概念。
- 五年 OHLCV backfill 可提供市場基準與延遲 outcome 的歷史行情來源。
- ModelHub integration handoff 已描述候選訓練、artifact 與版本登錄的接線邊界。

### 2.2 核心缺口

- 尚無一份三軌共用但責任隔離的權威契約。
- 尚未把每次分析完整封裝為可重放、可追溯的 `analysis-quality.v1` 樣本。
- 即時品質標籤與 T+1／T+7／T+14 延遲 outcome 尚未明確分層。
- 異常偵測、信心校準、RAG 品質各自的 gold set、切分方式及升版門檻未統一。
- 外框改善仍缺候選狀態機、人工啟用證據及一鍵回滾契約。
- ModelHub 現況尚未在本輪唯讀複驗，不能寫成「已正常」或「已完成接線」。

## 3. 第一軌：Question RAG 品質

### 3.1 目的

提升意圖辨識、檢索命中率、引用完整性、拒答正確性與回答一致性，而不是用舊回答替代當下 Evidence。

### 3.2 資料與標籤

- 輸入：使用者問題、正規化 query、question type、檢索候選、排序結果、回答、引用、拒答理由、使用者回饋及人工 gold set。
- 歷史回答一律標記 `historical_non_evidentiary`，只可用於 query reformulation、相似問題與品質評估。
- 可接受標籤：檢索相關性、引用支持度、回答完整性、拒答正確性及人工偏好。
- 禁止把點擊、停留時間或舊答案直接當作事實正確標籤。

### 3.3 評估與啟用

離線評估至少包含 retrieval recall、ranking quality、citation support、abstention precision 及跨時間 replay。Embedding／reranker 候選必須通過既有 model gate；production 啟用仍需人工批准與回滾能力。

## 4. 第二軌：分析異常偵測與信心校準

### 4.1 訓練單位

每一次分析 run 才是一筆不可變訓練樣本。最低資料契約包括：

- `run_id`、分析快照、event time、available time、coin、mode、question type；
- raw confidence、calibrated confidence、direction、decision state；
- supporting／contrarian count、evidence count、average trust、independent source count、source distribution；
- freshness、conflict、missingness、completeness；
- model、prompt、policy、rule、schema version；
- 各 stage latency、failure、retry 與完整 provenance。

所有特徵只能使用該次決策時已可取得的資料，避免 future leakage；原始分析快照不可被後續行情或重算結果覆寫。

### 4.2 兩類標籤必須分開

- **即時品質標籤**：資料缺漏、來源集中、Evidence 衝突、stage failure、信心與證據不一致等，供異常偵測與營運監控。
- **延遲 outcome**：T+1／T+7／T+14 的方向、報酬或風險結果，供校準與回測；必須記錄 label available time，未成熟資料不得進入訓練／評估。

信心校準回答「宣稱 70% 的判斷是否長期接近 70%」，異常偵測回答「本次分析流程或特徵是否偏離正常分布」。兩者可共用分析快照，但不可共用單一模糊標籤。

### 4.3 五年 OHLCV 的正確角色

五年 OHLCV 是市場背景、replay 輸入與延遲 outcome 計算來源；它不是五年的 TrustForge 分析樣本。除非能以當時可得資料、固定版本及完整 provenance 重放歷史分析，否則不能宣稱已擁有五年分析訓練集。

## 5. 第三軌：外框模型受控自我升級

外框升級是治理流程：

`diagnostics → proposal → candidate build → sandbox/replay → reviewer gate → human activation → monitoring → rollback`

- 診斷可提出 prompt、policy、routing、threshold 或模型候選，但不能直接改 production。
- 候選必須綁定資料集、程式、prompt、policy、模型與評估版本。
- 沙盒驗證需比較基準版，包含品質、安全、成本、延遲與退化切片。
- 啟用必須由人決定並留下 commit-bound／artifact-bound 證據。
- Trust Kernel、time boundary、Evidence binding、權限與稽核規則不得由候選覆寫。
- 每次啟用必須保留已知良好版本及明確回滾條件。

只有確實包含可訓練模型的候選，才需要以 `trustforge-wrapper-upgrade` 名義送進 ModelHub；純規則、prompt 或 policy 改動仍走一般版本與 PR 治理。

## 6. ModelHub 的責任邊界

建議邏輯名稱：

- `trustforge-rag-quality`
- `trustforge-analysis-quality`
- `trustforge-wrapper-upgrade`（僅模型型候選）

ModelHub 可負責候選訓練工作、experiment、artifact、metrics 與版本 registry。它不負責產生 truth label、不定義 TrustForge Evidence 邊界，也不授權 production activation。

目前只能記錄為「待執行前唯讀複驗」：確認 health、模型清單、身分／tenant scope、API key 權限及既有 artifact 可讀性。複驗不得建立、修改或刪除資源；任何寫入須另案授權。

## 7. 禁止的跨軌混用

- 不得把歷史 RAG 回答當分析 Evidence 或 truth label。
- 不得把 OHLCV 價格變動直接當作當次分析流程品質標籤。
- 不得把未成熟 outcome 混入 train／validation／test。
- 不得跨 event／available time 做會洩漏未來資訊的 join。
- 不得以 ModelHub registry 狀態取代 TrustForge 的人工啟用批准。
- 不得讓 wrapper 候選修改不可變核心或繞過 reviewer、安全審查與回滾門檻。
- 不得將三軌合成一個無法追溯資料與責任的總分或單一黑箱模型。

## 8. 建議落地順序

1. 定義三軌 canonical schema、版本與 dataset manifest。
2. 建立 `analysis-quality.v1` 不可變事件收集與 provenance。
3. 建立 T+1／T+7／T+14 outcome maturity／labeler。
4. 產出信心校準資料集與基準評估。
5. 建立分析異常偵測 baseline、告警門檻與 replay。
6. 建立 wrapper 候選評估、人工啟用與回滾狀態機。
7. 補齊 RAG feedback、gold set 與 model gate 資料管線。

每一階段都必須先有可驗收的資料契約與離線 replay，再考慮 ModelHub 寫入或 production 啟用。若實作涉及 DB schema／migration，必須停手取得 Eric 當次 purpose token；安全相關項目需 harper（CISO）與 `/codex-review` 雙審。

## 9. 權威參考

- `docs/decisions/RAG-MODEL-GATE-DECISION-2026-07-20.md`
- `docs/architecture/TRUSTFORGE-FEATURE-STORE.md`
- `docs/architecture/HERMES-AGENT.md`
- `docs/architecture/HERMES-CONTINUOUS-INTELLIGENCE-2026-07-16.md`
- `docs/plans/HERMES-AGENT-DELIVERY-BACKLOG-2026-07-13.md`
- `docs/handoff/2026-07-22-modelhub-integration-handoff.md`

後續開發計劃必須以本文件為基準；Wiki 與記憶待文件合併後再同步，避免同時存在多個互相矛盾的權威版本。
